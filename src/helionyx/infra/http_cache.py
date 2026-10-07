"""On-disk cache for external HTTP GET requests (FR-RES-008)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from helionyx.errors import ErrorCode, HelionyxError


class HttpCache:
    def __init__(self, root: Path, offline: bool, timeout_s: float = 60.0) -> None:
        self.root = root / "http"
        self.root.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.timeout_s = timeout_s
        self.network_calls = 0

    def _key(self, url: str, params: dict[str, Any]) -> Path:
        raw = json.dumps({"url": url, "params": params}, sort_keys=True)
        return self.root / (hashlib.sha256(raw.encode()).hexdigest() + ".json")

    def get_json(self, url: str, params: dict[str, Any], source_name: str) -> tuple[Any, bool]:
        """Return (json body, from_cache)."""
        path = self._key(url, params)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")), True
        if self.offline:
            raise HelionyxError(
                ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                f"Offline mode is on and {source_name} data for this request is not cached.",
                "Disable offline mode (HNX_OFFLINE=0), use a bundled sample site, or import a CSV.",
                {"source": source_name},
            )
        try:
            self.network_calls += 1
            resp = httpx.get(url, params=params, timeout=self.timeout_s, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise HelionyxError(
                ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                f"{source_name} could not be reached ({type(exc).__name__}).",
                "Retry later, use another source, or import the series as CSV.",
                {"source": source_name},
            ) from exc
        if resp.status_code >= 400:
            raise HelionyxError(
                ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                f"{source_name} returned HTTP {resp.status_code}.",
                "Check the coordinates and year, try another source, or import a CSV.",
                {"source": source_name, "status": resp.status_code, "body": resp.text[:300]},
            )
        body = resp.json()
        path.write_text(json.dumps(body), encoding="utf-8")
        return body, False
