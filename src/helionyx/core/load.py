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


def extend_partial_year(day_of_year: npt.NDArray[np.int64], hour: npt.NDArray[np.int64], values: FloatArray,
                        weekend_days: Sequence[int], monthly_kwh: Sequence[float] | None = None,
                        ) -> tuple[FloatArray, npt.NDArray[np.bool_]]:
    """Extend measured hourly load (at least 4 weeks) to a full year (FR-LOAD-007).

    Measured hours are kept exactly at their position in the representative year.
    Missing hours take the mean measured profile of their day type (weekday or
    weekend) and hour; when ``monthly_kwh`` is given, the synthetic hours of each
    month are scaled so the month total matches it. Returns (series, measured mask).
    """
    from helionyx.core.engine.timeaxis import is_weekend

    pos = day_of_year * 24 + hour
    measured = np.zeros(HOURS, dtype=np.bool_)
    series = np.zeros(HOURS)
    series[pos] = values
    measured[pos] = True
    weekend = is_weekend(weekend_days)
    profile = np.zeros((2, 24))
    for wk in (0, 1):
        for h in range(24):
            sel = measured & (weekend == bool(wk)) & (HOUR_OF_DAY == h)
            if not sel.any():  # no measured day of this type: fall back to all days
                sel = measured & (HOUR_OF_DAY == h)
            profile[wk, h] = series[sel].mean()
    fill = ~measured
    series[fill] = profile[weekend[fill].astype(int), HOUR_OF_DAY[fill]]
    if monthly_kwh is not None:
        for m in range(12):
            month = MONTH == m
            synth = month & fill
            target = monthly_kwh[m] - series[month & measured].sum()
            cur = series[synth].sum()
            if synth.any() and cur > 0 and target > 0:
                series[synth] *= target / cur
    return np.asarray(series, dtype=np.float64), measured
