"""Data-pack releases: build, verify and install (SRS §6.2, `helionyx pack update`).

A pack release is a zip archive plus a JSON manifest::

    {"country": "lk", "version": "2026.11.0", "archive": "pack-lk-2026.11.0.zip",
     "sha256": "<hex digest of the archive>"}

``update`` downloads the manifest and archive (HTTPS URL or local path), verifies
the SHA-256 checksum, validates the pack against its schemas and installs it under
``<workspace>/packs/<country>``. Installed packs override the bundled pack only when
their CalVer version is newer.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import httpx

from helionyx.errors import ErrorCode, HelionyxError, validation
from helionyx.infra.packs import load_pack_from

DEFAULT_MANIFEST_URL = "https://github.com/Jayzilva/helionyx/releases/latest/download/pack-{country}.json"


def calver_key(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


def _fetch(location: str, base: str | None = None) -> bytes:
    if base is not None and "://" not in location and not Path(location).is_absolute():
        location = base.rsplit("/", 1)[0] + "/" + location if "://" in base else str(Path(base).parent / location)
    if location.startswith(("http://", "https://")):
        if location.startswith("http://"):
            raise validation("Pack downloads must use HTTPS.", "Use an https:// URL or a local path.")
        try:
            r = httpx.get(location, timeout=60, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise HelionyxError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, "The pack release could not be downloaded.",
                                "Check the URL and your connection.") from exc
        if r.status_code >= 400:
            raise HelionyxError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, f"Pack download failed (HTTP {r.status_code}).",
                                "Check that the release exists.", {"url": location[:200]})
        return r.content
    path = Path(location)
    if not path.exists():
        raise validation(f"Pack file not found: {location[:200]}", "Check the path.")
    return path.read_bytes()


def build(pack_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Validate a pack directory and write ``pack-<country>-<version>.zip`` plus ``pack-<country>.json``."""
    pack, errors = load_pack_from(pack_dir)
    if pack is None:
        raise validation("The pack does not validate.", "Run `helionyx pack validate` and fix the errors.",
                         errors=errors[:10])
    m = pack.manifest
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"pack-{m.id}-{m.version}.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(pack_dir.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                info = zipfile.ZipInfo(f.relative_to(pack_dir).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, f.read_bytes())  # fixed timestamps: reproducible archive
    archive.write_bytes(buf.getvalue())
    manifest = {"country": m.id, "version": m.version, "archive": archive.name,
                "sha256": hashlib.sha256(buf.getvalue()).hexdigest()}
    (out_dir / f"pack-{m.id}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest | {"archive_path": str(archive)}


def update(workspace: Path, country: str = "lk", manifest_location: str | None = None) -> dict[str, Any]:
    from helionyx.infra.packs import PACKS_ROOT, get_pack

    loc = manifest_location or DEFAULT_MANIFEST_URL.format(country=country)
    try:
        manifest = json.loads(_fetch(loc))
    except json.JSONDecodeError as exc:
        raise validation("The pack manifest is not valid JSON.", "Check the manifest URL.") from exc
    for key in ("country", "version", "archive", "sha256"):
        if key not in manifest:
            raise validation(f"The pack manifest has no '{key}'.", "Rebuild it with `helionyx pack build`.")
    if manifest["country"] != country:
        raise validation(f"The manifest is for '{manifest['country']}', not '{country}'.", "Use the right manifest.")
    data = _fetch(manifest["archive"], base=loc)
    digest = hashlib.sha256(data).hexdigest()
    if digest != manifest["sha256"]:
        raise HelionyxError(ErrorCode.VALIDATION_FAILED, "Pack checksum mismatch: the archive was not installed.",
                            "Download again; if it persists, report it to the pack maintainers.",
                            {"expected": manifest["sha256"], "actual": digest})
    current = get_pack(country).manifest.version
    if calver_key(manifest["version"]) <= calver_key(current):
        return {"country": country, "installed": False, "current_version": current,
                "available_version": manifest["version"], "message": "Already up to date."}
    target = workspace / "packs" / country
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / country
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.startswith("/") or ".." in Path(name).parts:
                    raise validation("The pack archive contains unsafe paths.", "Rebuild the archive.")
            zf.extractall(stage)
        pack, errors = load_pack_from(stage)
        if pack is None or pack.manifest.version != manifest["version"]:
            raise validation("The downloaded pack does not validate.", "Report it to the pack maintainers.",
                             errors=errors[:10])
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(stage, target)
    get_pack.cache_clear()
    _ = PACKS_ROOT
    return {"country": country, "installed": True, "previous_version": current, "version": manifest["version"],
            "path": str(target), "sha256": digest}
