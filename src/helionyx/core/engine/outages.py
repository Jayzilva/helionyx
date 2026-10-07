"""Grid availability masks (FR-SCN-007)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from helionyx.core.engine.timeaxis import HOURS, window_mask

Int8Array = npt.NDArray[np.int8]


def always() -> Int8Array:
    return np.ones(HOURS, dtype=np.int8)


def scheduled(windows: Sequence[tuple[str, str, str]], weekend_days: Sequence[int]) -> Int8Array:
    """Grid unavailable inside each (start, end, days) window, every matching day."""
    mask = np.ones(HOURS, dtype=np.int8)
    for start, end, days in windows:
        mask[window_mask(start, end, days, weekend_days)] = 0
    return mask


def stochastic(events_per_year: float, mean_duration_h: float, seed: int) -> Int8Array:
    """Random outages: Poisson event count, uniform start hour, exponential duration (≥ 1 h)."""
    rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed)))
    mask = np.ones(HOURS, dtype=np.int8)
    n = int(rng.poisson(events_per_year))
    starts = rng.integers(0, HOURS, size=n)
    durations = np.maximum(1, np.ceil(rng.exponential(mean_duration_h, size=n))).astype(np.int64)
    for s, d in zip(starts, durations, strict=True):
        mask[s:min(HOURS, s + d)] = 0
    return mask
