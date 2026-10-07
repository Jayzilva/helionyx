"""Load service: synthetic, composite and calibrated load profiles (FR-LOAD-001…005, 008)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from helionyx.core.load import (
    add_variability,
    archetype_shape,
    calibrate_monthly,
    load_stats,
    scale_to_energy,
    scale_to_peak,
)
from helionyx.errors import WarningCode, validation, warning
from helionyx.infra.db import now_iso
from helionyx.infra.packs import get_pack
from helionyx.services.context import Helionyx
from helionyx.services.resource import _store_dataset, get_site


class LoadComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archetype: str
    count: float | None = Field(None, gt=0, le=100_000)
    annual_kwh: float | None = Field(None, gt=0)
    peak_kw: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _one_scale(self) -> LoadComponent:
        given = [x is not None for x in (self.count, self.annual_kwh, self.peak_kw)]
        if sum(given) > 1:
            raise ValueError("give at most one of count, annual_kwh or peak_kw")
        return self


class Variability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day_sigma: float | None = Field(None, ge=0, le=0.5)
    hour_sigma: float | None = Field(None, ge=0, le=0.5)


def synthesize_load(app: Helionyx, site_id: str, components: list[LoadComponent],
                    monthly_kwh: list[float] | None = None, variability: Variability | None = None,
                    seed: int = 42) -> dict[str, Any]:
    site = get_site(app, site_id)
    pack = get_pack(site["country_pack"])
    if not components:
        raise validation("At least one load component is required.", "Add an archetype such as 'hotel'.")
    if monthly_kwh is not None and (len(monthly_kwh) != 12 or any(v < 0 for v in monthly_kwh)):
        raise validation("monthly_kwh must have 12 non-negative values (January to December).",
                         "Give one value per month from the electricity bills.")
    weekend = pack.manifest.weekend_days
    streams = np.random.SeedSequence(seed).spawn(len(components))
    total = np.zeros(8760)
    parts: list[dict[str, Any]] = []
    for comp, ss in zip(components, streams, strict=True):
        arch = pack.archetype(comp.archetype)
        rng = np.random.Generator(np.random.PCG64(ss))
        day_sigma = variability.day_sigma if variability and variability.day_sigma is not None else arch.day_sigma
        hour_sigma = variability.hour_sigma if variability and variability.hour_sigma is not None else arch.hour_sigma
        series = add_variability(archetype_shape(arch, weekend), day_sigma, hour_sigma, rng)
        if comp.peak_kw is not None:
            series = scale_to_peak(series, comp.peak_kw)
            scale = {"peak_kw": comp.peak_kw}
        else:
            target = comp.annual_kwh if comp.annual_kwh is not None else (comp.count or 1.0) * arch.typical_annual_kwh
            series = scale_to_energy(series, target)
            scale = {"annual_kwh": target} if comp.annual_kwh is not None else \
                {"count": comp.count or 1.0, "typical_annual_kwh_per_unit": arch.typical_annual_kwh}
        total += series
        parts.append({"archetype": arch.id, "basis": arch.basis[:160], "scale": scale,
                      "day_sigma": day_sigma, "hour_sigma": hour_sigma,
                      "annual_kwh": float(series.sum())})
    if monthly_kwh is not None:
        total = calibrate_monthly(total, monthly_kwh)
    frame = pd.DataFrame({"load_kw": total})
    flags = [warning(WarningCode.SYNTHETIC_INPUT, "Synthetic load built from archetypes"
                     + (" and calibrated to monthly energy." if monthly_kwh else "; not calibrated to bills."),
                     path="load").model_dump()]
    prov = {"source": "synthetic", "pack": pack.ref, "components": parts, "seed": seed,
            "calibrated_to_monthly_kwh": monthly_kwh is not None, "retrieved_at": now_iso(),
            "license": "CC-BY-4.0 (pack archetypes)"}
    stats = load_stats(total)
    if monthly_kwh is not None:
        stats["calibration_targets_monthly_kwh"] = monthly_kwh
    return _store_dataset(app, site_id, "load", frame, "synthetic", True, prov, stats, flags, "load")
