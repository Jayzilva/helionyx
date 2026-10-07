"""Runtime settings, read from ``HNX_*`` environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    workspace: Path = field(default_factory=lambda: Path(
        os.environ.get("HNX_WORKSPACE", str(Path.home() / ".helionyx"))).expanduser().resolve())
    offline: bool = field(default_factory=lambda: _env_bool("HNX_OFFLINE", False))
    max_concurrent_jobs: int = field(default_factory=lambda: int(os.environ.get("HNX_MAX_JOBS", "2")))
    job_timeout_s: float = field(default_factory=lambda: float(os.environ.get("HNX_JOB_TIMEOUT_S", "900")))
    max_sensitivity_evaluations: int = 200_000
    http_timeout_s: float = 60.0
    reopt_api_key: str | None = field(default_factory=lambda: os.environ.get("HNX_REOPT_API_KEY"))
    log_level: str = field(default_factory=lambda: os.environ.get("HNX_LOG_LEVEL", "INFO"))

    @property
    def db_path(self) -> Path:
        return self.workspace / "helionyx.sqlite3"

    @property
    def artefact_dir(self) -> Path:
        return self.workspace / "artefacts"

    @property
    def cache_dir(self) -> Path:
        return self.workspace / "cache"

    @property
    def export_dir(self) -> Path:
        return self.workspace / "exports"

    def ensure(self) -> Settings:
        for p in (self.workspace, self.artefact_dir, self.cache_dir, self.export_dir):
            p.mkdir(parents=True, exist_ok=True)
        return self

    def resolve_user_path(self, raw: str) -> Path:
        """Resolve a user-supplied path, rejecting anything outside the workspace (NFR-SEC-01)."""
        from helionyx.errors import validation

        p = Path(raw).expanduser()
        p = (p if p.is_absolute() else self.workspace / p).resolve()
        if not p.is_relative_to(self.workspace):
            raise validation(
                "File path is outside the Helionyx workspace.",
                f"Place the file under {self.workspace} (set HNX_WORKSPACE to change it).",
                path=str(raw)[:100],
            )
        return p
