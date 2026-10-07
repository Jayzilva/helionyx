"""Application context: one object wiring settings, stores, packs and jobs."""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from helionyx import __version__
from helionyx.infra.artefacts import ArtefactStore
from helionyx.infra.db import Database
from helionyx.infra.http_cache import HttpCache
from helionyx.infra.jobs import JobManager
from helionyx.infra.settings import Settings


@lru_cache(maxsize=1)
def git_commit() -> str:
    root = Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True,
                             text=True, timeout=5, check=False)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def engine_version() -> str:
    return f"helionyx {__version__} (git {git_commit()})"


class Helionyx:
    """Holds the long-lived services of one Helionyx process."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = (settings or Settings()).ensure()
        self.db = Database(self.settings.db_path)
        self.artefacts = ArtefactStore(self.settings.artefact_dir)
        self.http = HttpCache(self.settings.cache_dir, self.settings.offline, self.settings.http_timeout_s)
        self.jobs = JobManager(self.db, self.settings.max_concurrent_jobs, self.settings.job_timeout_s)

    def provenance(self, **extra: Any) -> dict[str, Any]:
        base: dict[str, Any] = {"engine": engine_version()}
        base.update({k: v for k, v in extra.items() if v is not None})
        return base

    def close(self) -> None:
        self.jobs.shutdown()
        self.db.close()
