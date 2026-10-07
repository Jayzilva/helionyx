"""Asynchronous job execution with persisted state (FR-JOB-001…006).

Jobs run in worker threads. The heavy numerical work releases the GIL inside
Numba, so threads give real parallelism without pickling scenario data.
Cancellation and the timeout are cooperative: the job calls ``progress`` between
chunks, which raises if the job must stop.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from helionyx.errors import ErrorCode, HelionyxError, not_found
from helionyx.infra.db import Database, now_iso
from helionyx.infra.ids import new_id

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class JobCancelled(Exception):
    pass


class JobTimedOut(Exception):
    pass


ProgressFn = Callable[[float, str], None]
JobFn = Callable[[ProgressFn], dict[str, Any]]


@dataclass
class _Live:
    doc: dict[str, Any]
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    started: float = 0.0
    last_write: float = 0.0


class JobManager:
    def __init__(self, db: Database, max_concurrent: int, timeout_s: float) -> None:
        self.db = db
        self.max_concurrent = max_concurrent
        self.timeout_s = timeout_s
        self._pool = ThreadPoolExecutor(max_workers=max(2, max_concurrent * 2), thread_name_prefix="hnx-job")
        self._live: dict[str, _Live] = {}
        self._lock = threading.Lock()
        self._mark_interrupted()

    def _mark_interrupted(self) -> None:
        for row in self.db.query("SELECT id, doc FROM jobs WHERE state IN ('queued', 'running')"):
            doc = self.db.get("jobs", row["id"], "job")
            doc.update(state="interrupted", finished_at=now_iso(),
                       error={"code": "HNX-E006", "name": "JOB_TIMEOUT",
                              "message": "The server restarted while the job was running.",
                              "hint": "Start the run again."})
            self._persist(doc)

    def _persist(self, doc: dict[str, Any]) -> None:
        self.db.put("jobs", doc["job_id"], doc, kind=doc["kind"], state=doc["state"], owner=doc["owner"])

    def active_count(self, owner: str) -> int:
        with self._lock:
            return sum(1 for j in self._live.values()
                       if j.doc["owner"] == owner and j.doc["state"] in ("queued", "running"))

    def submit(self, kind: str, owner: str, fn: JobFn, refs: dict[str, Any]) -> dict[str, Any]:
        if self.active_count(owner) >= self.max_concurrent:
            raise HelionyxError(
                ErrorCode.RATE_LIMITED,
                f"You already have {self.max_concurrent} jobs running.",
                "Wait for a running job to finish (get_job_status) or cancel one (cancel_job).",
                {"limit": self.max_concurrent},
            )
        job_id = new_id("job")
        doc: dict[str, Any] = {"job_id": job_id, "kind": kind, "owner": owner, "state": "queued",
                               "progress_pct": 0.0, "message": "queued", "created_at": now_iso(),
                               "started_at": None, "finished_at": None, "eta_s": None, "error": None,
                               "result": None, **refs}
        live = _Live(doc)
        with self._lock:
            self._live[job_id] = live
        self._persist(doc)
        self._pool.submit(self._run, live, fn)
        return dict(doc)

    def _progress_fn(self, live: _Live) -> ProgressFn:
        def progress(pct: float, message: str) -> None:
            if live.cancel.is_set():
                raise JobCancelled()
            elapsed = time.monotonic() - live.started
            if elapsed > self.timeout_s:
                raise JobTimedOut()
            pct = max(0.0, min(100.0, pct))
            live.doc.update(progress_pct=round(pct, 1), message=message,
                            eta_s=round(elapsed * (100.0 - pct) / pct, 1) if pct > 0 else None)
            now = time.monotonic()
            if now - live.last_write > 0.5:
                live.last_write = now
                self._persist(live.doc)
        return progress

    def _run(self, live: _Live, fn: JobFn) -> None:
        doc = live.doc
        live.started = time.monotonic()
        doc.update(state="running", started_at=now_iso(), message="running")
        self._persist(doc)
        try:
            result = fn(self._progress_fn(live))
            doc.update(state="completed", progress_pct=100.0, message="completed", eta_s=0.0, result=result)
        except JobCancelled:
            doc.update(state="cancelled", message="cancelled by user")
        except JobTimedOut:
            doc.update(state="failed", message="timed out", error={
                "code": ErrorCode.JOB_TIMEOUT.value, "name": "JOB_TIMEOUT",
                "message": f"The job exceeded its {self.timeout_s:.0f} s limit.",
                "hint": "Reduce the search space or the number of sensitivity values."})
        except HelionyxError as exc:
            doc.update(state="failed", message=exc.message, error=exc.to_body().model_dump())
        except Exception as exc:  # noqa: BLE001 - a job must never crash the server
            doc.update(state="failed", message="internal error", error={
                "code": "HNX-E001", "name": "INTERNAL_ERROR",
                "message": f"{type(exc).__name__}: {str(exc)[:200]}",
                "hint": "Report this with the scenario YAML at the Helionyx issue tracker."})
        doc["finished_at"] = now_iso()
        self._persist(doc)
        live.done.set()

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            live = self._live.get(job_id)
        if live is not None:
            return dict(live.doc)
        if not self.db.exists("jobs", job_id):
            raise not_found("job", job_id)
        return self.db.get("jobs", job_id, "job")

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            live = self._live.get(job_id)
        if live is None:
            return self.status(job_id)
        if live.doc["state"] not in TERMINAL:
            live.cancel.set()
            if live.doc["state"] == "queued":
                live.doc.update(state="cancelled", message="cancelled before start", finished_at=now_iso())
                self._persist(live.doc)
        return dict(live.doc)

    def wait(self, job_id: str, timeout_s: float | None = None) -> dict[str, Any]:
        with self._lock:
            live = self._live.get(job_id)
        if live is not None:
            live.done.wait(timeout_s)
        return self.status(job_id)

    def shutdown(self) -> None:
        for live in list(self._live.values()):
            live.cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
