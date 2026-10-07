"""Results, structured explanation, monthly summary and run comparison (FR-RPT-001…005, FR-ADP-005)."""

from __future__ import annotations

from typing import Any

import numpy as np

from helionyx import DISCLAIMER
from helionyx.core.billing import compute_bill
from helionyx.core.engine import dispatch as dsp
from helionyx.errors import ErrorCode, HelionyxError, validation
from helionyx.services.context import Helionyx
from helionyx.services.run import SIZE_KEYS, _sort_value, build_inputs, get_run
from helionyx.services.scenario import get_scenario, resolved_of

SUMMARY_METRICS = ("npc", "lcoe_per_kwh", "initial_capital", "operating_cost_per_yr", "renewable_fraction_pct",
                   "capacity_shortage_pct", "excess_electricity_pct", "annual_bill", "simple_payback_yr",
                   "discounted_payback_yr", "irr_pct", "fuel_l_per_yr", "co2_kg_per_yr")
COST_TYPES = ("capital", "replacement", "om", "fuel", "grid_purchases", "grid_sales", "salvage")


def _units(currency: str) -> dict[str, str]:
    return {"npc": currency, "lcoe_per_kwh": f"{currency}/kWh", "initial_capital": currency,
            "operating_cost_per_yr": f"{currency}/yr", "annualised_cost_per_yr": f"{currency}/yr",
            "annual_bill": f"{currency}/yr", "cost_breakdown": currency, "sizes.bess_kw": "kW",
            "sizes.wind_count": "turbines"}


def provenance(app: Helionyx, run: dict[str, Any], scn: dict[str, Any]) -> dict[str, Any]:
    s = scn["resolved"]
    tariff = s["grid"]["tariff"]
    sources = [f"resource:{s['resource']['dataset_id']}"] + [f"load:{x['dataset_id']}" for x in s["loads"]]
    if tariff:
        sources.append(f"tariff:{tariff['id']}")
    return {"engine": run["engine"], "solver": f"{run['solver']}-enumerative" if run["solver"] == "native"
            else run["solver"], "packs": run["packs"], "scenario_hash": run["scenario_hash"], "sources": sources,
            "seed": run["seed"], "wall_time_s": run.get("wall_time_s")}


def _row(c: dict[str, Any], full: bool = False) -> dict[str, Any]:
    m = c["metrics"]
    return {"rank": c.get("rank"), "candidate_index": c["candidate_index"], "sizes": c["sizes"],
            "metrics": m if full else {k: m.get(k) for k in SUMMARY_METRICS}}


def _apply_filters(cands: list[dict[str, Any]], filters: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not filters:
        return cands
    out = []
    for c in cands:
        ok = True
        for key, bounds in filters.items():
            val = c["sizes"].get(key, c["metrics"].get(key))
            if val is None or not isinstance(bounds, dict):
                raise validation(f"Unknown filter '{str(key)[:40]}'.",
                                 "Filter on a size (pv_kwp, bess_kwh…) or metric name with {min, max}.")
            if "min" in bounds and val < bounds["min"]:
                ok = False
            if "max" in bounds and val > bounds["max"]:
                ok = False
        if ok:
            out.append(c)
    return out


def nearest_infeasible(app: Helionyx, run_id: str) -> dict[str, Any]:
    cands = app.db.get_candidates(run_id, order="index")
    best = min(cands, key=lambda c: (sum(v["normalised_violation"] for v in c["violations"]), c["candidate_index"]))
    return {"sizes": best["sizes"], "violations": best["violations"],
            "metrics": {k: best["metrics"].get(k) for k in SUMMARY_METRICS}}


def get_results(app: Helionyx, run_id: str, top_n: int = 5, sort_by: str | None = None,
                filters: dict[str, Any] | None = None) -> dict[str, Any]:
    if not 1 <= top_n <= 50:
        raise validation("top_n must be 1–50.", "Use the results.csv resource for the full table.")
    run = get_run(app, run_id)
    scn = get_scenario(app, run["scenario_id"])
    if run["feasible_count"] == 0:
        raise HelionyxError(ErrorCode.NO_FEASIBLE_CANDIDATE, "No candidate meets the constraints.",
                            "Relax the violated constraints or widen the search space; the nearest candidate is "
                            "in details.", {"run_id": run_id, "nearest_candidate": nearest_infeasible(app, run_id)})
    sort_key = sort_by or run["sort_by"]
    if sort_key != run["sort_by"] or filters:
        cands = _apply_filters(app.db.get_candidates(run_id, feasible_only=True), filters)
        cands.sort(key=lambda c: (_sort_value(c, sort_key), c["candidate_index"]))
        cands = cands[:top_n]
        for i, c in enumerate(cands, 1):
            c["rank_in_view"] = i
    else:
        cands = app.db.get_candidates(run_id, feasible_only=True, limit=top_n)
    base = run.get("base_case")
    return {
        "run_id": run_id, "scenario_id": run["scenario_id"], "scenario_hash": run["scenario_hash"],
        "currency": run["currency"], "sorted_by": sort_key, "filters": filters,
        "feasible_count": run["feasible_count"], "infeasible_count": run["infeasible_count"],
        "candidates": [_row(c) | ({"rank_in_view": c["rank_in_view"]} if "rank_in_view" in c else {})
                       for c in cands],
        "base_case": ({"label": base["label"], "sizes": base["sizes"],
                       "metrics": {k: base["metrics"].get(k) for k in SUMMARY_METRICS}} if base else None),
        "units": _units(run["currency"]),
        "warnings": scn["warnings"],
        "provenance": provenance(app, run, scn),
        "disclaimer": DISCLAIMER,
    }


def candidate_by_rank(app: Helionyx, run_id: str, rank: int) -> dict[str, Any]:
    run = get_run(app, run_id)
    if rank == 0:
        if not run.get("base_case"):
            raise validation("This run has no base case.", "Base cases exist for grid-connected scenarios and "
                             "off-grid scenarios with a genset.")
        return run["base_case"]
    rows = app.db.query("SELECT doc FROM candidates WHERE run_id = ? AND rank = ?", (run_id, rank))
    if not rows:
        raise HelionyxError(ErrorCode.NOT_FOUND, f"Run {run_id} has no feasible candidate at rank {rank}.",
                            f"Ranks run from 1 to {run['feasible_count']}; use 0 for the base case.",
                            {"feasible_count": run["feasible_count"]})
    import json
    doc: dict[str, Any] = json.loads(rows[0]["doc"])
    return doc


def _delta(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"sizes": {}, "metrics": {}}
    for k in (*SIZE_KEYS, "bess_kw"):
        av, bv = a["sizes"][k], b["sizes"][k]
        if av != bv:
            out["sizes"][k] = {"this": av, "other": bv, "difference": av - bv}
    for k in SUMMARY_METRICS:
        av, bv = a["metrics"].get(k), b["metrics"].get(k)
        if av is None or bv is None:
            continue
        out["metrics"][k] = {"this": av, "other": bv, "difference": av - bv,
                             "difference_pct": 100.0 * (av - bv) / abs(bv) if bv else None}
    return out


def _uncertainties(app: Helionyx, scn: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for w in scn["warnings"]:
        if w["code"] in ("HNX-W001", "HNX-W002", "HNX-W003"):
            out.append({"code": w["code"], "input": w.get("path"), "reason": w["message"]})
    comps = scn["resolved"]["components"]
    from helionyx.infra.packs import get_pack

    pack = get_pack(scn["resolved"]["site"]["country_pack"])
    for kind, comp in comps.items():
        if comp and comp["spec"] in pack.components and pack.components[comp["spec"]].status != "verified":
            out.append({"code": "HNX-W004", "input": f"components.{kind}",
                        "reason": f"{comp['spec']} costs are indicative and unverified."})
    return out


def _sensitivity_candidates(uncert: list[dict[str, Any]], breakdown: dict[str, dict[str, float]],
                            grid: bool) -> list[dict[str, str]]:
    """Suggest the two most uncertain inputs as sensitivity paths (FR-SKL-001 e)."""
    paths: list[dict[str, str]] = []
    shares = sorted(((k, v["total"]) for k, v in breakdown.items()), key=lambda kv: -abs(kv[1]))
    for name, _ in shares:
        if name in ("pv", "bess", "wind", "genset", "converter"):
            paths.append({"path": f"components.{name}.overrides.capital_per_unit",
                          "why": f"{name} capital cost is a large, unverified share of NPC"})
        elif name == "grid" and grid:
            paths.append({"path": "economics.grid_price_escalation_real",
                          "why": "grid purchases are a large share of NPC and tariffs change"})
        if name == "genset":
            paths.append({"path": "economics.fuel_price_per_l", "why": "fuel is a large share of NPC"})
    if any(u["code"] == "HNX-W002" for u in uncert):
        paths.append({"path": "load (re-synthesise with monthly_kwh)", "why": "synthetic load"})
    seen, out = set(), []
    for p in paths:
        if p["path"] not in seen:
            seen.add(p["path"])
            out.append(p)
    return out[:2]


def explain_run(app: Helionyx, run_id: str, rank: int = 1, compare_to: str = "next") -> dict[str, Any]:
    run = get_run(app, run_id)
    scn = get_scenario(app, run["scenario_id"])
    cur = run["currency"]
    c = candidate_by_rank(app, run_id, rank)
    m = c["metrics"]
    npc = m["npc"]
    by_type = c["cost_by_type"]
    by_comp = {k: v["total"] for k, v in c["cost_breakdown"].items()}
    gross = sum(abs(v) for v in by_type.values()) or 1.0
    shares_type = {k: {"value": v, "share_pct": 100.0 * abs(v) / gross} for k, v in by_type.items() if v}
    drivers = sorted(((f"{comp}.{kind}", val) for comp, parts in c["cost_breakdown"].items()
                      for kind, val in parts.items() if kind in COST_TYPES and val > 0), key=lambda kv: -kv[1])[:3]
    k = scn["resolved"]["constraints"]
    binding = []
    for name, value, limit in (
        ("max_capacity_shortage", m["capacity_shortage_pct"] / 100.0, k["max_capacity_shortage"]),
        ("min_renewable_fraction", m["renewable_fraction_pct"] / 100.0, k.get("min_renewable_fraction")),
        ("max_genset_hours", m["genset_hours_per_yr"], k.get("max_genset_hours")),
        ("max_export_kw", m["peak_export_kw"], k.get("max_export_kw")),
        ("max_pv_kwp", c["sizes"]["pv_kwp"], k.get("max_pv_kwp")),
        ("max_initial_capital", m["initial_capital"], k.get("max_initial_capital")),
    ):
        if limit is None or (limit == 0 and value == 0):
            continue
        near = abs(value - limit) <= max(0.05 * abs(limit), 1e-6)
        if near:
            binding.append({"constraint": name, "value": value, "limit": limit})
    comps = scn["resolved"]["components"]
    if comps.get("pv") and c["sizes"]["pv_kwp"] == max(comps["pv"]["sizes_kwp"]) and c["sizes"]["pv_kwp"] > 0:
        binding.append({"constraint": "search_space_upper_bound", "value": c["sizes"]["pv_kwp"],
                        "limit": max(comps["pv"]["sizes_kwp"]), "note": "pv_kwp is at the largest size searched"})
    if comps.get("bess") and c["sizes"]["bess_kwh"] == max(comps["bess"]["sizes_kwh"]) and c["sizes"]["bess_kwh"] > 0:
        binding.append({"constraint": "search_space_upper_bound", "value": c["sizes"]["bess_kwh"],
                        "limit": max(comps["bess"]["sizes_kwh"]), "note": "bess_kwh is at the largest size searched"})
    other = None
    other_label = None
    if compare_to == "base" and run.get("base_case"):
        other, other_label = run["base_case"], run["base_case"]["label"]
    elif compare_to == "next":
        try:
            other, other_label = candidate_by_rank(app, run_id, rank + 1), f"rank {rank + 1}"
        except HelionyxError:
            other = None
    elif compare_to not in ("next", "base"):
        raise validation("compare_to must be 'next' or 'base'.", "Use 'next' or 'base'.")
    uncert = _uncertainties(app, scn)
    dispatch_stats = {key: m[key] for key in (
        "genset_hours_per_yr", "genset_starts_per_yr", "fuel_l_per_yr", "bess_equivalent_full_cycles_per_yr",
        "bess_autonomy_h", "excess_electricity_pct", "unserved_kwh_per_yr", "grid_import_kwh_per_yr",
        "grid_export_kwh_per_yr", "outage_hours_per_yr", "outage_unserved_kwh_per_yr")}
    sentences = [
        f"Rank {rank} has a net present cost of {npc:,.0f} {cur} over "
        f"{scn['resolved']['economics']['project_life_years']} years (run {run_id}).",
    ]
    if drivers:
        top = drivers[0]
        comp, kind = top[0].split(".", 1)
        label = kind.replace("_", " ") if comp == "grid" else f"{comp} {kind.replace('_', ' ')}"
        sentences.append(f"The largest cost driver is {label} at "
                         f"{100.0 * top[1] / gross:.1f}% of gross life-cycle cost.")
    if binding:
        sentences.append("Binding or near-binding limits: " + ", ".join(b["constraint"] for b in binding) + ".")
    if other is not None and other_label:
        d = other["metrics"]["npc"]
        sentences.append(f"Compared with {other_label}, NPC differs by {npc - d:,.0f} {cur}.")
    return {
        "run_id": run_id, "rank": rank, "currency": cur, "sizes": c["sizes"],
        "npc": npc, "lcoe_per_kwh": m["lcoe_per_kwh"],
        "project_life_years": scn["resolved"]["economics"]["project_life_years"],
        "npc_by_cost_type": shares_type,
        "npc_by_component": by_comp,
        "top_cost_drivers": [{"item": name, "value": val, "share_pct": 100.0 * val / gross} for name, val in drivers],
        "binding_constraints": binding,
        "dispatch_statistics": dispatch_stats,
        "component_lifetimes_yr": c.get("component_lifetimes_yr", {}),
        "comparison": {"against": other_label, **_delta(c, other)} if other is not None else None,
        "uncertain_inputs": uncert,
        "suggested_sensitivities": _sensitivity_candidates(uncert, c["cost_breakdown"],
                                                           scn["resolved"]["grid"]["mode"] == "grid_connected"),
        "method_notes": ["Battery discharges before the genset starts (HOMER compares marginal costs).",
                         "No operating reserve; capacity shortage equals unserved energy.",
                         "Hourly time step; one year represents the project life."],
        "sentences": sentences,
        "units": _units(cur),
        "provenance": provenance(app, run, scn),
        "disclaimer": DISCLAIMER,
    }


def candidate_series(app: Helionyx, run_id: str, rank: int) -> tuple[dict[str, Any], np.ndarray, Any]:
    run = get_run(app, run_id)
    scn = get_scenario(app, run["scenario_id"])
    s = resolved_of(scn)
    c = candidate_by_rank(app, run_id, rank)
    inp = build_inputs(app, s)
    sz = c["sizes"]
    agg, series = dsp.simulate_series(inp, sz["pv_kwp"], sz["wind_count"], sz["bess_kwh"], sz["bess_kw"],
                                      sz["genset_kw"])
    return c, series, s


def get_monthly_summary(app: Helionyx, run_id: str, rank: int = 1) -> dict[str, Any]:
    c, series, s = candidate_series(app, run_id, rank)
    inp = build_inputs(app, s)
    month = inp.month
    flows = []
    from helionyx.core.billing import MONTHS

    for m in range(12):
        mask = month == m
        flows.append({"month": MONTHS[m], **{f"{name}_kwh": float(series[mask, i].sum())
                                             for i, name in enumerate(dsp.M_FIELDS)}})
    bills_before = bills_after = None
    if s.grid.mode == "grid_connected" and s.grid.tariff is not None:
        P = inp.n_periods
        imp = np.zeros((12, P))
        exp = np.zeros((12, P))
        base_imp = np.zeros((12, P))
        np.add.at(imp, (month, inp.period), series[:, 4])
        np.add.at(exp, (month, inp.period), series[:, 5])
        np.add.at(base_imp, (month, inp.period), inp.load)
        peak = np.array([series[month == m, 4].max() for m in range(12)])
        base_peak = np.array([inp.load[month == m].max() for m in range(12)])
        after = compute_bill(s.grid.tariff, s.grid.export_scheme, imp, exp, peak, s.options.demand_peak_factor)
        before = compute_bill(s.grid.tariff, "none", base_imp, np.zeros((12, P)), base_peak,
                              s.options.demand_peak_factor)
        bills_after = [b.as_dict() for b in after.months]
        bills_before = [b.as_dict() for b in before.months]
        for i in range(12):
            flows[i]["bill_before"] = bills_before[i]["total"]
            flows[i]["bill_after"] = bills_after[i]["total"]
            flows[i]["bill_saving"] = bills_before[i]["total"] - bills_after[i]["total"]
    totals = {k: sum(f[k] for f in flows) for k in flows[0] if k != "month"}
    run = get_run(app, run_id)
    return {"run_id": run_id, "rank": rank, "currency": s.economics.currency, "sizes": c["sizes"],
            "months": flows, "annual_totals": totals, "bill_detail_after": bills_after,
            "provenance": provenance(app, run, get_scenario(app, run["scenario_id"])),
            "disclaimer": DISCLAIMER}


def compare_runs(app: Helionyx, run_ids: list[str], rank: int = 1) -> dict[str, Any]:
    if not 2 <= len(run_ids) <= 6:
        raise validation("Give 2–6 run IDs.", "Compare runs of the same or related scenarios.")
    rows = []
    for rid in run_ids:
        run = get_run(app, rid)
        if run["feasible_count"] == 0:
            rows.append({"run_id": rid, "solver": run["solver"], "feasible": False})
            continue
        c = candidate_by_rank(app, rid, rank)
        rows.append({"run_id": rid, "solver": run["solver"], "scenario_hash": run["scenario_hash"], "feasible": True,
                     "sizes": c["sizes"], "metrics": {k: c["metrics"].get(k) for k in SUMMARY_METRICS}})
    ref = rows[0]
    for r in rows[1:]:
        if r.get("feasible") and ref.get("feasible"):
            r["difference_vs_first"] = _delta(r, ref)
    return {"rank": rank, "runs": rows, "reference_run_id": ref["run_id"],
            "same_scenario": len({r.get("scenario_hash") for r in rows}) == 1, "disclaimer": DISCLAIMER}
