"""Load profile synthesis, calibration and statistics (FR-LOAD-001…006, 008)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import numpy.typing as npt

from helionyx.core.engine.timeaxis import DAY_OF_YEAR, HOUR_OF_DAY, HOURS, MONTH, is_weekend
from helionyx.core.models.packs import Archetype

FloatArray = npt.NDArray[np.float64]


def archetype_shape(arch: Archetype, weekend_days: Sequence[int]) -> FloatArray:
    """Unscaled 8,760-hour shape from the day-type profiles and monthly multipliers."""
    wd = np.asarray(arch.weekday, dtype=np.float64)
    we = np.asarray(arch.weekend, dtype=np.float64)
    weekend = is_weekend(weekend_days)
    daily = np.where(weekend, we[HOUR_OF_DAY], wd[HOUR_OF_DAY])
    monthly = np.asarray(arch.monthly, dtype=np.float64)
    return np.asarray(daily * monthly[MONTH], dtype=np.float64)


def add_variability(shape: FloatArray, day_sigma: float, hour_sigma: float, rng: np.random.Generator) -> FloatArray:
    """Multiply by day-to-day and hour-to-hour noise factors (FR-LOAD-003)."""
    day_f = rng.normal(1.0, day_sigma, size=HOURS // 24) if day_sigma > 0 else np.ones(HOURS // 24)
    hour_f = rng.normal(1.0, hour_sigma, size=HOURS) if hour_sigma > 0 else np.ones(HOURS)
    out = shape * day_f[DAY_OF_YEAR] * hour_f
    return np.asarray(np.clip(out, 0.0, None), dtype=np.float64)


def scale_to_energy(series: FloatArray, annual_kwh: float) -> FloatArray:
    total = series.sum()
    return np.asarray(series * (annual_kwh / total) if total > 0 else series, dtype=np.float64)


def scale_to_peak(series: FloatArray, peak_kw: float) -> FloatArray:
    m = np.max(series)
    return np.asarray(series * (peak_kw / m) if m > 0 else series, dtype=np.float64)


def calibrate_monthly(series: FloatArray, monthly_kwh: Sequence[float]) -> FloatArray:
    """Scale each month so its energy equals the target (FR-LOAD-004)."""
    out = series.copy()
    for m in range(12):
        mask = MONTH == m
        cur = out[mask].sum()
        if cur > 0:
            out[mask] *= monthly_kwh[m] / cur
        elif monthly_kwh[m] > 0:
            out[mask] = monthly_kwh[m] / mask.sum()
    return out


def load_stats(series: FloatArray) -> dict[str, Any]:
    annual = float(series.sum())
    peak = float(np.max(series))
    monthly = [float(series[MONTH == m].sum()) for m in range(12)]
    avg_daily = [float(series[HOUR_OF_DAY == h].mean()) for h in range(24)]
    return {
        "annual_kwh": annual,
        "peak_kw": peak,
        "average_kw": annual / HOURS,
        "load_factor": annual / (peak * HOURS) if peak > 0 else 0.0,
        "monthly_kwh": monthly,
        "average_daily_profile_kw": avg_daily,
    }
