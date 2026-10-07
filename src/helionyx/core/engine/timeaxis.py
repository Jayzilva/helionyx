"""The representative-year time axis shared by loads, tariffs and dispatch.

The model uses 8,760 hourly steps of one non-leap representative year in local
civil time (constraint C6). Day types (weekday or weekend) follow the calendar
of ``REFERENCE_YEAR`` so that load shapes and TOU periods line up.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import numpy.typing as npt
import pandas as pd

HOURS = 8760
REFERENCE_YEAR = 2023  # non-leap; 1 January is a Sunday

IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]


def local_index(year: int = REFERENCE_YEAR) -> pd.DatetimeIndex:
    """Naive local timestamps at the start of each hour, with 29 February removed."""
    idx = pd.date_range(f"{year}-01-01", periods=HOURS + 24, freq="h")
    idx = idx[~((idx.month == 2) & (idx.day == 29))]
    return idx[:HOURS]


_IDX = local_index()
MONTH: IntArray = (_IDX.month.to_numpy() - 1).astype(np.int64)
HOUR_OF_DAY: IntArray = _IDX.hour.to_numpy().astype(np.int64)
ISO_WEEKDAY: IntArray = (_IDX.dayofweek.to_numpy() + 1).astype(np.int64)
DAY_OF_YEAR: IntArray = (np.arange(HOURS) // 24).astype(np.int64)
DAYS_IN_MONTH = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], dtype=np.int64)


def is_weekend(weekend_days: Iterable[int]) -> BoolArray:
    return np.isin(ISO_WEEKDAY, list(weekend_days))


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def window_mask(start: str, end: str, days: str, weekend_days: Sequence[int]) -> BoolArray:
    """Hours whose midpoint falls inside a daily window [start, end) in local time.

    Windows may cross midnight (for example 22:30–05:30). ``days`` is ``all``,
    ``weekday`` or ``weekend``.
    """
    mid = HOUR_OF_DAY * 60 + 30
    s, e = _minutes(start), _minutes(end)
    inside = (mid >= s) & (mid < e) if s <= e else (mid >= s) | (mid < e)
    if days == "all":
        return np.asarray(inside, dtype=np.bool_)
    weekend = is_weekend(weekend_days)
    return np.asarray(inside & (weekend if days == "weekend" else ~weekend), dtype=np.bool_)


def period_index(periods: Sequence[tuple[str, str, str, str]], names: Sequence[str],
                 weekend_days: Sequence[int]) -> IntArray:
    """Map every hour to a TOU period index. ``periods`` rows are (name, start, end, days).

    Hours not covered by any window are assigned to the last named period. Later
    windows win where windows overlap.
    """
    out = np.full(HOURS, len(names) - 1, dtype=np.int64)
    for name, start, end, days in periods:
        out[window_mask(start, end, days, weekend_days)] = names.index(name)
    return out
