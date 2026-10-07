"""Content-addressed artefact store for time series (Parquet) and exports."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
import pandas as pd


def hash_array(arr: np.ndarray) -> str:
    a = np.ascontiguousarray(arr, dtype=np.float64)
    return "sha256:" + hashlib.sha256(a.tobytes()).hexdigest()


def hash_frame(df: pd.DataFrame) -> str:
    h = hashlib.sha256()
    for col in df.columns:
        h.update(str(col).encode())
        h.update(np.ascontiguousarray(df[col].to_numpy(dtype=np.float64)).tobytes())
    return "sha256:" + h.hexdigest()


class ArtefactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str, suffix: str) -> Path:
        hexd = digest.split(":", 1)[-1]
        return self.root / hexd[:2] / f"{hexd}{suffix}"

    def put_frame(self, df: pd.DataFrame) -> tuple[str, str]:
        """Store a frame as Parquet. Returns (content_hash, uri)."""
        digest = hash_frame(df)
        path = self._path(digest, ".parquet")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            buf = io.BytesIO()
            df.to_parquet(buf, index=False)
            path.write_bytes(buf.getvalue())
        return digest, path.as_uri()

    def get_frame(self, digest: str) -> pd.DataFrame:
        return pd.read_parquet(self._path(digest, ".parquet"))
