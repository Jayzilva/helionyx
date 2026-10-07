"""Country data-pack loader and validator (SRS §6.3, FR-TAR-001, AT-12)."""

from __future__ import annotations

import datetime as dt
import gzip
import io
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pydantic import ValidationError

from helionyx.core.models.packs import Archetype, Component, Emissions, PackDefaults, PackManifest, Tariff
from helionyx.errors import not_found, validation

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass
class Pack:
    root: Path
    manifest: PackManifest
    tariffs: dict[str, Tariff]
    components: dict[str, Component]
    archetypes: dict[str, Archetype]
    emissions: Emissions
    defaults: PackDefaults
    samples: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.manifest.id}@{self.manifest.version}"

    # -------------------------------------------------------------- tariffs
    def tariff(self, tariff_id: str) -> Tariff:
        if tariff_id not in self.tariffs:
            raise not_found("tariff", tariff_id)
        return self.tariffs[tariff_id]

    def tariff_in_effect(self, utility: str, category: str, on: dt.date) -> Tariff | None:
        cands = [t for t in self.tariffs.values()
                 if t.utility.lower() == utility.lower() and t.category.lower() == category.lower()
                 and t.effective_from <= on and (t.effective_to is None or on <= t.effective_to)]
        return max(cands, key=lambda t: t.effective_from) if cands else None

    def newer_revision(self, tariff: Tariff) -> Tariff | None:
        newer = [t for t in self.tariffs.values()
                 if t.utility == tariff.utility and t.category == tariff.category
                 and t.effective_from > tariff.effective_from]
        return min(newer, key=lambda t: t.effective_from) if newer else None

    # -------------------------------------------------------------- components / archetypes
    def component(self, spec: str) -> Component:
        if spec not in self.components:
            raise not_found("component", spec)
        return self.components[spec]

    def archetype(self, archetype_id: str) -> Archetype:
        if archetype_id not in self.archetypes:
            raise not_found("archetype", archetype_id)
        return self.archetypes[archetype_id]

    def sample_resource(self, sample_id: str) -> pd.DataFrame:
        info = self.samples.get(sample_id)
        if info is None:
            raise not_found("sample resource", sample_id)
        with gzip.open(self.root / "samples" / info["file"], "rb") as fh:
            return pd.read_csv(io.BytesIO(fh.read()))


def _collect(root: Path, sub: str, model: type[Any], errors: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path in sorted((root / sub).rglob("*.yaml")):
        try:
            obj = model.model_validate(_load_yaml(path))
        except ValidationError as exc:
            errors.append(f"{path.relative_to(root)}: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}")
            continue
        if obj.id in out:
            errors.append(f"{path.relative_to(root)}: duplicate id {obj.id}")
        out[obj.id] = obj
    return out


def load_pack_from(root: Path) -> tuple[Pack | None, list[str]]:
    """Load and validate a pack directory. Returns (pack or None, errors)."""
    errors: list[str] = []
    try:
        manifest = PackManifest.model_validate(_load_yaml(root / "pack.yaml"))
        emissions = Emissions.model_validate(_load_yaml(root / "emissions.yaml"))
        defaults = PackDefaults.model_validate(_load_yaml(root / "defaults.yaml"))
    except (OSError, ValidationError) as exc:
        return None, [f"manifest/emissions/defaults: {exc}"]
    tariffs = _collect(root, "tariffs", Tariff, errors)
    components = _collect(root, "components", Component, errors)
    archetypes = _collect(root, "archetypes", Archetype, errors)
    for t in tariffs.values():
        expected = f"{t.effective_from.isoformat()}"
        if not t.id.endswith(expected):
            errors.append(f"tariff {t.id}: id must end with @{expected}")
        if t.currency != manifest.currency:
            errors.append(f"tariff {t.id}: currency {t.currency} differs from pack currency {manifest.currency}")
    for a in archetypes.values():
        if not a.basis:
            errors.append(f"archetype {a.id}: missing basis")
    for kind, spec in defaults.default_components.items():
        if spec not in components:
            errors.append(f"defaults: default {kind} component {spec} not found")
    samples_path = root / "samples" / "samples.yaml"
    samples = _load_yaml(samples_path) if samples_path.exists() else {}
    pack = Pack(root, manifest, tariffs, components, archetypes, emissions, defaults, samples or {})
    return (pack if not errors else None), errors


@lru_cache(maxsize=8)
def get_pack(country: str) -> Pack:
    """The bundled pack, or a newer valid one installed by `helionyx pack update` in the workspace."""
    root = PACKS_ROOT / country
    if not root.is_dir():
        raise not_found("country pack", country)
    pack, errors = load_pack_from(root)
    if pack is None:
        raise validation(f"Country pack '{country}' failed validation.",
                         "Run `helionyx pack validate` and fix the listed files.", errors=errors[:10])
    from helionyx.infra.settings import Settings

    installed = Settings().workspace / "packs" / country
    if installed.is_dir():
        newer, _ = load_pack_from(installed)
        if newer is not None and _calver(newer.manifest.version) > _calver(pack.manifest.version):
            return newer
    return pack


def _calver(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))
