"""Seeded multi-start pattern search over the discrete size grid (FR-OPT-005).

The search works on index coordinates of each size axis. It minimises the score
``(total normalised constraint violation, NPC)`` lexicographically, so any
feasible design beats any infeasible one. From several starting points it
evaluates the pattern neighbours (± step on each axis) in one batch, moves to the
best improvement, and halves the step when nothing improves. Every design is
evaluated at most once; the budget caps the number of evaluations.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

Index = tuple[int, ...]
Score = tuple[float, float]
EvalFn = Callable[[list[Index]], list[Score]]


@dataclass
class HeuristicResult:
    scores: dict[Index, Score] = field(default_factory=dict)
    starts: int = 0
    iterations: int = 0
    budget_exhausted: bool = False

    @property
    def evaluations(self) -> int:
        return len(self.scores)

    def best(self) -> Index:
        return min(self.scores, key=lambda k: (self.scores[k], k))


def pattern_search(axis_lengths: Sequence[int], evaluate: EvalFn, seed: int, max_evaluations: int = 4000,
                   n_starts: int = 6) -> HeuristicResult:
    rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence([seed, 5])))
    dims = len(axis_lengths)
    res = HeuristicResult()

    def run(points: list[Index]) -> None:
        new = [p for p in dict.fromkeys(points) if p not in res.scores]
        room = max_evaluations - len(res.scores)
        if len(new) > room:
            new = new[:room]
            res.budget_exhausted = True
        if new:
            for p, s in zip(new, evaluate(new), strict=True):
                res.scores[p] = s

    # Starting points: grid centre, the "all largest" corner, then random points.
    starts: list[Index] = [tuple(n // 2 for n in axis_lengths), tuple(n - 1 for n in axis_lengths)]
    while len(starts) < n_starts:
        starts.append(tuple(int(rng.integers(0, n)) for n in axis_lengths))
    starts = list(dict.fromkeys(starts))
    run(starts)
    res.starts = len(starts)

    for start in starts:
        if start not in res.scores:
            break
        cur = start
        step = max(1, max(axis_lengths) // 4)
        while not res.budget_exhausted:
            res.iterations += 1
            neigh: list[Index] = []
            for d in range(dims):
                for sign in (-1, 1):
                    k = min(max(cur[d] + sign * step, 0), axis_lengths[d] - 1)
                    if k != cur[d]:
                        p = list(cur)
                        p[d] = k
                        neigh.append(tuple(p))
            run(neigh)
            scored = [p for p in neigh if p in res.scores]
            best = min(scored, key=lambda p: (res.scores[p], p)) if scored else cur
            if scored and res.scores[best] < res.scores[cur]:
                cur = best
            elif step > 1:
                step = max(1, step // 2)
            else:
                break
    return res
