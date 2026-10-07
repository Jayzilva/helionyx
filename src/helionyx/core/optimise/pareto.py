"""Non-dominated (Pareto) set of candidates (FR-OPT-006)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def non_dominated(points: Sequence[Sequence[float]]) -> list[int]:
    """Indices of points not dominated by any other point (all objectives minimised).

    A point dominates another when it is no worse on every objective and strictly
    better on at least one. Duplicates are kept once (lowest index).
    """
    arr = np.asarray(points, dtype=np.float64)
    if arr.size == 0:
        return []
    order = np.lexsort(arr.T[::-1])  # sort by first objective, then the next ones
    keep: list[int] = []
    for i in order:
        p = arr[i]
        dominated = False
        for j in keep:
            q = arr[j]
            if np.all(q <= p) and (np.any(q < p) or j < i):
                dominated = True
                break
        if not dominated:
            keep = [j for j in keep if not (np.all(p <= arr[j]) and np.any(p < arr[j]))]
            keep.append(int(i))
    return sorted(keep)
