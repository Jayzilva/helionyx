"""Time-series preparation: UTC→local alignment, leap days, gaps, resampling (FR-RES-005…007, FR-LOAD-006)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
import pandas as pd

from helionyx.core.engine.timeaxis import HOURS

FloatArray = npt.NDArray[np.float64]


@dataclass
class PrepReport:
    filled_short: int = 0
    filled_long: int = 0
    dropped_leap_day: bool = False
    alignment: str = ""
    notes: list[str] = field(default_factory=list)


def drop_leap_day(index: pd.DatetimeIndex, values: np.ndarray) -> tuple[pd.DatetimeIndex, np.ndarray, bool]:
    mask = (index.month == 2) & (index.day == 29)
    if mask.any():
        return index[~mask], values[~mask], True
    return index, values, False


def utc_offset_hours(timezone: str, year: int) -> float:
    """Standard UTC offset of a zone on 1 January of ``year`` (Asia/Colombo: +5.5)."""
    ts = pd.Timestamp(f"{year}-01-01", tz=timezone)
    off = ts.utcoffset()
    return off.total_seconds() / 3600.0 if off is not None else 0.0


def align_utc_to_local(utc_values: FloatArray, offset_hours: float) -> tuple[FloatArray, str]:
    """Shift an 8,760-step UTC series (each value = mean over [h, h+1) UTC) to local civil hours.

    For a fractional offset (for example +5:30), each local hour overlaps two UTC
    hours by half; its value is the mean of the two (FR-RES-005). The series is
    treated as circular at the year boundary (implementation decision D6).
    """
    if len(utc_values) != HOURS:
        raise ValueError("expected 8760 values")
    whole = math.floor(offset_hours)
    frac = offset_hours - whole
    # local hour k covers UTC [k - offset, k - offset + 1)
    a = np.roll(utc_values, whole)  # a[k] = utc[k - whole]
    if abs(frac) < 1e-9:
        return a.astype(np.float64), f"shift {whole:+d} h (whole-hour offset)"
    b = np.roll(utc_values, whole + 1)  # b[k] = utc[k - whole - 1]
    out = (1.0 - frac) * a + frac * b
    method = (f"UTC{offset_hours:+.2f} h: each local hour is the overlap-weighted mean of two UTC hours "
              f"({frac:.2f}/{1 - frac:.2f}); circular wrap at the year boundary")
    return out.astype(np.float64), method


def fill_gaps(values: FloatArray, fill_long_gaps: bool, max_short: int = 3) -> tuple[FloatArray, int, int, list[int]]:
    """Fill NaN gaps: ≤ 3 h linearly; longer only if allowed, with the same-hour mean of ±3 days.

    Returns (filled values, short filled count, long filled count, lengths of unfilled long gaps).
    """
    v = values.astype(np.float64).copy()
    isnan = np.isnan(v)
    if not isnan.any():
        return v, 0, 0, []
    n = len(v)
    short = long = 0
    unfilled: list[int] = []
    i = 0
    original = v.copy()
    while i < n:
        if not isnan[i]:
            i += 1
            continue
        j = i
        while j < n and isnan[j]:
            j += 1
        length = j - i
        if length <= max_short and i > 0 and j < n:
            v[i:j] = np.interp(np.arange(i, j), [i - 1, j], [v[i - 1], v[j]])
            short += length
        elif fill_long_gaps:
            for k in range(i, j):
                neigh = [original[k + 24 * d] for d in (-3, -2, -1, 1, 2, 3)
                         if 0 <= k + 24 * d < n and not np.isnan(original[k + 24 * d])]
                v[k] = float(np.mean(neigh)) if neigh else 0.0
            long += length
        elif length <= max_short:  # edge gap: hold nearest value
            edge = v[j] if i == 0 and j < n else v[i - 1]
            v[i:j] = edge
            short += length
        else:
            unfilled.append(length)
        i = j
    return v, short, long, unfilled


def to_hourly_mean(values: FloatArray, resolution_min: int) -> FloatArray:
    """Convert a 15/30/60-minute power series (kW, mean over each interval) to hourly means."""
    per_hour = 60 // resolution_min
    if len(values) % per_hour:
        raise ValueError("series length is not a whole number of hours")
    return np.asarray(values.reshape(-1, per_hour).mean(axis=1), dtype=np.float64)
