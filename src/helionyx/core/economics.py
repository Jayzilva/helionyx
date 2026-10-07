"""Life-cycle economics (SRS §7.7) and emissions (§7.9).

All cash flows are in constant (real) currency. Costs escalate at a real rate
``e`` as ``C1 * (1 + e) ** (y - 1)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq


def real_discount_rate(nominal: float, inflation: float) -> float:
    return (nominal - inflation) / (1.0 + inflation)


def crf(i: float, n: int) -> float:
    if abs(i) < 1e-12:
        return 1.0 / n
    a = (1.0 + i) ** n
    return i * a / (a - 1.0)


def pv_annuity(i: float, n: int, escalation: float = 0.0) -> float:
    """Present value of 1 per year for years 1..n, escalating at ``escalation``."""
    return float(sum((1.0 + escalation) ** (y - 1) / (1.0 + i) ** y for y in range(1, n + 1)))


def replacement_years(lifetime: float, n: int) -> list[float]:
    years: list[float] = []
    if lifetime <= 0 or not math.isfinite(lifetime):
        return years
    k = 1
    while k * lifetime < n - 1e-9:
        years.append(k * lifetime)
        k += 1
    return years


def salvage_value(capital: float, replacement: float, lifetime: float, n: int) -> float:
    reps = replacement_years(lifetime, n)
    y_last = reps[-1] if reps else 0.0
    c_last = replacement if reps else capital
    r_rem = lifetime - (n - y_last)
    return max(0.0, c_last * r_rem / lifetime) if lifetime > 0 else 0.0


@dataclass
class CostItem:
    """One cost line: a component, fuel, grid or system fixed costs."""

    name: str
    capital: float = 0.0
    replacement_cost: float = 0.0
    lifetime_years: float = math.inf
    om_per_year: float = 0.0
    fuel_per_year: float = 0.0
    grid_purchases_per_year: float = 0.0
    grid_sales_per_year: float = 0.0
    escalation_fuel: float = 0.0
    escalation_grid: float = 0.0


@dataclass
class EconomicsResult:
    npc: float
    annualised_cost: float
    initial_capital: float
    operating_cost_per_year: float
    breakdown: dict[str, dict[str, float]]          # item -> {capital, replacement, om, fuel, grid_purchases, ...}
    by_type: dict[str, float]
    cash_flows: list[float] = field(default_factory=list)  # undiscounted, year 0..N


def evaluate(items: list[CostItem], i: float, n: int) -> EconomicsResult:
    """Compute NPC with its breakdown and the undiscounted yearly cash flows."""
    breakdown: dict[str, dict[str, float]] = {}
    cash = np.zeros(n + 1)
    for it in items:
        reps = replacement_years(it.lifetime_years, n) if it.capital > 0 or it.replacement_cost > 0 else []
        rep_pv = sum(it.replacement_cost / (1.0 + i) ** y for y in reps)
        salv = salvage_value(it.capital, it.replacement_cost, it.lifetime_years, n) \
            if math.isfinite(it.lifetime_years) else 0.0
        salv_pv = salv / (1.0 + i) ** n
        om_pv = it.om_per_year * pv_annuity(i, n)
        fuel_pv = it.fuel_per_year * pv_annuity(i, n, it.escalation_fuel)
        buy_pv = it.grid_purchases_per_year * pv_annuity(i, n, it.escalation_grid)
        sell_pv = it.grid_sales_per_year * pv_annuity(i, n, it.escalation_grid)
        parts = {
            "capital": it.capital,
            "replacement": rep_pv,
            "om": om_pv,
            "fuel": fuel_pv,
            "grid_purchases": buy_pv,
            "grid_sales": -sell_pv,
            "salvage": -salv_pv,
        }
        parts["total"] = sum(parts.values())
        breakdown[it.name] = parts

        cash[0] += it.capital
        for y in range(1, n + 1):
            cash[y] += it.om_per_year
            cash[y] += it.fuel_per_year * (1.0 + it.escalation_fuel) ** (y - 1)
            cash[y] += (it.grid_purchases_per_year - it.grid_sales_per_year) * (1.0 + it.escalation_grid) ** (y - 1)
        for ry in reps:
            cash[min(n, max(1, math.ceil(ry - 1e-9)))] += it.replacement_cost
        cash[n] -= salv

    by_type = {k: sum(b[k] for b in breakdown.values())
               for k in ("capital", "replacement", "om", "fuel", "grid_purchases", "grid_sales", "salvage")}
    npc = sum(b["total"] for b in breakdown.values())
    initial = sum(it.capital for it in items)
    operating = (npc - initial) * crf(i, n)
    return EconomicsResult(
        npc=npc,
        annualised_cost=crf(i, n) * npc,
        initial_capital=initial,
        operating_cost_per_year=operating,
        breakdown=breakdown,
        by_type=by_type,
        cash_flows=[float(x) for x in cash],
    )


def lcoe(annualised_cost: float, e_served_kwh: float, e_export_kwh: float) -> float | None:
    denom = e_served_kwh + e_export_kwh
    return annualised_cost / denom if denom > 0 else None


@dataclass
class PaybackResult:
    simple_payback_yr: float | None
    discounted_payback_yr: float | None
    irr_pct: float | None


def _payback(diff: np.ndarray) -> float | None:
    """First (fractional) year in which cumulative savings cover the extra capital."""
    if diff[0] >= 0:
        return 0.0
    cum = diff[0]
    for y in range(1, len(diff)):
        prev = cum
        cum += diff[y]
        if cum >= 0:
            return (y - 1) + (-prev / diff[y] if diff[y] > 0 else 1.0)
    return None


def payback(candidate_cash: list[float], base_cash: list[float], i: float) -> PaybackResult:
    """Payback and IRR from the year-by-year difference with the base case (§7.7)."""
    diff = np.array(base_cash) - np.array(candidate_cash)  # savings; year 0 is minus the extra capital
    n = len(diff) - 1
    disc = diff / (1.0 + i) ** np.arange(n + 1)
    irr: float | None = None

    def npv(r: float) -> float:
        return float(np.sum(diff / (1.0 + r) ** np.arange(n + 1)))

    lo, hi = -0.99, 10.0
    try:
        if np.sign(npv(lo)) != np.sign(npv(hi)) and diff[0] < 0:
            irr = brentq(npv, lo, hi, xtol=1e-10, maxiter=200) * 100.0
    except (ValueError, OverflowError):
        irr = None
    return PaybackResult(_payback(diff), _payback(disc), irr)


def emissions_kg_per_year(fuel_l: float, grid_import_kwh: float, ef_diesel: float, ef_grid: float) -> float:
    return fuel_l * ef_diesel + grid_import_kwh * ef_grid
