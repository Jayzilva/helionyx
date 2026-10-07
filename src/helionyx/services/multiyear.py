"""Multi-year analysis: load growth (FR-LOAD-009) and capacity expansion (FR-OPT-007).

The load of project year ``y`` is the year-1 load scaled by ``(1 + g) ** (y - 1)``. Capacity
added by an expansion stage is in place from the start of its year. The engine simulates
sample years (the first and last year of every period of constant capacity, plus every
``sample_every_years``-th year) and interpolates the annual flows linearly in between.
Economics then run year by year (:func:`helionyx.core.economics.evaluate_years`).
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np

from helionyx.core import economics as eco
from helionyx.core.billing import compute_bill
from helionyx.core.engine import dispatch as dsp
from helionyx.core.models.scenario import ResolvedScenario
from helionyx.services.context import Helionyx

BASE_KEYS = ("pv_kwp", "wind_count", "bess_kwh", "genset_kw")
FLOWS = ("load", "served", "unserved", "export", "import", "fuel", "throughput", "genset_hours", "genset", "pv",
         "wind", "purchases", "sales", "bill", "peak_export", "co2")


def growth_factor(g: float, year: int) -> float:
    return float((1.0 + g) ** (year - 1))


def segments(n: int, stage_years: list[int]) -> list[tuple[int, int]]:
    """Inclusive year ranges with constant installed capacity."""
    starts = [1, *sorted(y for y in stage_years if 1 < y <= n)]
    return [(a, (starts[k + 1] - 1) if k + 1 < len(starts) else n) for k, a in enumerate(starts)]


def sample_years(n: int, stage_years: list[int], step: int) -> list[int]:
    ys: set[int] = set()
    for a, b in segments(n, stage_years):
        ys.update(range(a, b + 1, step))
        ys.update((a, b))
    return sorted(ys)


def installed(s: ResolvedScenario, sz: tuple[float, ...], year: int) -> tuple[float, float, float, float]:
    """Capacity in place during ``year``: the initial sizes plus every stage added by then."""
    cap = list(sz[:4])
    assert s.multi_year is not None
    for k, st in enumerate(s.multi_year.expansion):
        if st.year <= year:
            for j in range(4):
                cap[j] += sz[4 + 4 * k + j]
    return cap[0], cap[1], cap[2], cap[3]


def _interpolate(n: int, stage_years: list[int], samples: dict[int, dict[str, float]]) -> dict[str, np.ndarray]:
    out = {f: np.zeros(n) for f in FLOWS}
    for a, b in segments(n, stage_years):
        xs = [y for y in sorted(samples) if a <= y <= b]
        years = np.arange(a, b + 1)
        for f in FLOWS:
            out[f][a - 1:b] = np.interp(years, xs, [samples[y][f] for y in xs])
    return out


def _flows(s: ResolvedScenario, agg: np.ndarray, imp: np.ndarray, exp: np.ndarray, peak: np.ndarray,
           ) -> dict[str, float]:
    purchases = sales = bill = 0.0
    if s.grid.mode == "grid_connected" and s.grid.tariff is not None:
        b = compute_bill(s.grid.tariff, s.grid.export_scheme, imp, exp, peak, s.options.demand_peak_factor)
        purchases, sales, bill = b.purchases, b.sales, b.total
    e = s.economics
    return {"load": agg[dsp.A_LOAD], "served": agg[dsp.A_LOAD] - agg[dsp.A_UNS], "unserved": agg[dsp.A_UNS],
            "export": agg[dsp.A_EXP], "import": agg[dsp.A_IMP], "fuel": agg[dsp.A_FUEL],
            "throughput": agg[dsp.A_THR], "genset_hours": agg[dsp.A_GEN_HOURS], "genset": agg[dsp.A_GEN],
            "pv": agg[dsp.A_PV], "wind": agg[dsp.A_WT], "purchases": purchases, "sales": sales, "bill": bill,
            "peak_export": agg[dsp.A_PEAK_EXP],
            "co2": eco.emissions_kg_per_year(agg[dsp.A_FUEL], agg[dsp.A_IMP], e.diesel_kg_co2_per_l,
                                             e.grid_kg_co2_per_kwh)}


def _renewable_fraction(s: ResolvedScenario, f: dict[str, Any]) -> float:
    denom = f["served"] + f["export"]
    if denom <= 0:
        return 0.0
    return 100.0 * (1.0 - (f["genset"] + f["import"] * (1.0 - s.economics.grid_renewable_fraction)) / denom)


def _cost_items(s: ResolvedScenario, sz: tuple[float, ...], y: dict[str, np.ndarray]) -> list[eco.CostItem]:
    """One cost item per component bank (initial system and each stage) plus fuel, grid and fixed costs."""
    assert s.multi_year is not None
    c, e, n = s.components, s.economics, s.economics.project_life_years
    ppk = c.bess.power_kw_per_kwh if c.bess is not None else 0.0
    years = np.arange(1, n + 1)
    banks: list[tuple[str, int, tuple[float, ...]]] = [("", 0, sz[:4])]
    banks += [(f"_y{st.year}", st.year - 1, sz[4 + 4 * k:8 + 4 * k]) for k, st in enumerate(s.multi_year.expansion)]
    total_kwh = np.array([installed(s, sz, int(yr))[2] for yr in years])
    items: list[eco.CostItem] = []

    def add(name: str, comp: Any, qty: float, life: float, t0: int) -> None:
        if comp is not None and qty > 0:
            items.append(eco.CostItem(name=name, capital=comp.capital_per_unit * qty,
                                      replacement_cost=comp.replacement_per_unit * qty, lifetime_years=life,
                                      om_per_year=comp.om_per_unit_year * qty, install_year=t0))

    for suffix, t0, (pv, wt, kwh, gen) in banks:
        active = years > t0
        add("pv" + suffix, c.pv, pv, c.pv.lifetime_years if c.pv else 0, t0)
        add("wind" + suffix, c.wind, wt, c.wind.lifetime_years if c.wind else 0, t0)
        if c.bess is not None and kwh > 0:
            b = c.bess.technical
            life = float(b["float_life_years"])
            per_kwh = float(np.mean(y["throughput"][active] / np.maximum(total_kwh[active], 1e-9)))
            if per_kwh > 0:
                life = min(life, float(b["lifetime_throughput_kwh_per_kwh"]) / per_kwh)
            add("bess" + suffix, c.bess, kwh, life, t0)
            add("converter" + suffix, c.converter, kwh * ppk, c.converter.lifetime_years if c.converter else 0, t0)
        if c.genset is not None and gen > 0:
            life = c.genset.lifetime_years
            hours = float(np.mean(y["genset_hours"][active]))
            if hours > 0:
                life = min(float(c.genset.technical["lifetime_hours"]) / hours, life)
            add("genset" + suffix, c.genset, gen, life, t0)
    if c.genset is not None and np.any(y["fuel"] > 0):
        holder = next((it for it in items if it.name.startswith("genset")), None)
        if holder is None:
            holder = eco.CostItem(name="genset")
            items.append(holder)
        holder.fuel_by_year = list(y["fuel"] * e.fuel_price_per_l)
        holder.escalation_fuel = e.fuel_price_escalation_real
    if s.grid.mode == "grid_connected":
        items.append(eco.CostItem(name="grid", grid_purchases_by_year=list(y["purchases"]),
                                  grid_sales_by_year=list(y["sales"]), escalation_grid=e.grid_price_escalation_real))
    if e.system_fixed_capital > 0 or e.system_fixed_om_per_year > 0:
        items.append(eco.CostItem(name="system_fixed", capital=e.system_fixed_capital,
                                  om_per_year=e.system_fixed_om_per_year))
    return items


def evaluate_sizes(app: Helionyx, s: ResolvedScenario, sizes: list[tuple[float, ...]], progress: Any = None,
                   base_index: int = 0, p_from: float = 0.0, p_to: float = 100.0) -> list[dict[str, Any]]:
    """Multi-year counterpart of :func:`helionyx.services.run.evaluate_sizes`."""
    from helionyx.services.run import _violations, build_inputs, candidate_record, size_arrays
    from helionyx.services.scenario import size_axes

    my = s.multi_year
    assert my is not None
    n = s.economics.project_life_years
    i = s.economics.real_discount_rate
    stage_years = [st.year for st in my.expansion]
    ys = sample_years(n, stage_years, my.sample_every_years)
    inp = build_inputs(app, s)
    samples: list[dict[int, dict[str, float]]] = [{} for _ in sizes]
    year1: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = [None] * len(sizes)  # type: ignore[list-item]
    for k, yr in enumerate(ys):
        inp_y = replace(inp, load=inp.load * growth_factor(my.load_growth_rate, yr))
        caps = [installed(s, sz, yr) for sz in sizes]
        if caps:
            pv, wt, kwh, kw, gen = size_arrays(s, caps)
            r = dsp.simulate_batch(inp_y, pv, wt, kwh, kw, gen)
            for j in range(len(sizes)):
                samples[j][yr] = _flows(s, r.agg[j], r.imp_mp[j], r.exp_mp[j], r.peak[j])
                if yr == 1:
                    year1[j] = (r.agg[j].copy(), r.imp_mp[j].copy(), r.exp_mp[j].copy(), r.peak[j].copy())
        if progress is not None:
            progress(p_from + (p_to - p_from) * (k + 1) / len(ys), f"simulated year {yr} ({k + 1}/{len(ys)})")

    names = list(size_axes(s))
    ppk = s.components.bess.power_kw_per_kwh if s.components.bess is not None else 0.0
    out: list[dict[str, Any]] = []
    for j, sz in enumerate(sizes):
        agg, imp, exp, peak = year1[j]
        rec = candidate_record(s, base_index + j, installed(s, sz, 1), agg, imp, exp, peak)
        y = _interpolate(n, stage_years, samples[j])
        items = _cost_items(s, sz, y)
        ev = eco.evaluate_years(items, i, n)
        level = eco.levelised_energy(list(y["served"] + y["export"]), i)
        per_year = [{"year": yr, "load_kwh": f["load"],
                     "capacity_shortage_pct": 100.0 * f["unserved"] / f["load"] if f["load"] > 0 else 0.0,
                     "renewable_fraction_pct": _renewable_fraction(s, f), "fuel_l": f["fuel"],
                     "grid_import_kwh": f["import"], "annual_bill": f["bill"], "genset_hours": f["genset_hours"],
                     "peak_export_kw": f["peak_export"], "co2_kg": f["co2"]}
                    for yr, f in sorted(samples[j].items())]
        m = rec["metrics"]
        m.update(npc=ev.npc, lcoe_per_kwh=ev.annualised_cost / level if level > 0 else None,
                 initial_capital=ev.initial_capital, operating_cost_per_yr=ev.operating_cost_per_year,
                 annualised_cost_per_yr=ev.annualised_cost,
                 final_year_load_kwh_per_yr=float(y["load"][-1]),
                 expansion_capital=sum(it.capital for it in items if it.install_year > 0),
                 worst_year_capacity_shortage_pct=max(p["capacity_shortage_pct"] for p in per_year),
                 min_year_renewable_fraction_pct=min(p["renewable_fraction_pct"] for p in per_year),
                 worst_year_genset_hours=max(p["genset_hours"] for p in per_year),
                 max_year_peak_export_kw=max(p["peak_export_kw"] for p in per_year),
                 mean_co2_kg_per_yr=float(np.mean(y["co2"])))
        sizes_doc = dict(zip(names, (float(v) for v in sz), strict=True))
        sizes_doc["bess_kw"] = sizes_doc["bess_kwh"] * ppk
        for st in my.expansion:
            sizes_doc[f"bess_kw_add_y{st.year}"] = sizes_doc[f"bess_kwh_add_y{st.year}"] * ppk
        # Constraints hold in every simulated year; the PV limit applies to the final installed capacity.
        worst = {**m, "capacity_shortage_pct": m["worst_year_capacity_shortage_pct"],
                 "renewable_fraction_pct": m["min_year_renewable_fraction_pct"],
                 "genset_hours_per_yr": m["worst_year_genset_hours"],
                 "peak_export_kw": m["max_year_peak_export_kw"]}
        violations = _violations(s, {**sizes_doc, "pv_kwp": installed(s, sz, n)[0]}, worst)
        rec.update(sizes=sizes_doc, feasible=not violations, violations=violations, cost_breakdown=ev.breakdown,
                   cost_by_type=ev.by_type, cash_flows=ev.cash_flows,
                   component_lifetimes_yr={it.name: it.lifetime_years for it in items
                                           if math.isfinite(it.lifetime_years)},
                   multi_year={"load_growth_rate": my.load_growth_rate, "sample_years": ys,
                               "stage_years": stage_years, "years": per_year})
        out.append(rec)
    return out
