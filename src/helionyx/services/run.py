"""Run service: build engine inputs, simulate, cost, rank and persist (FR-SIM, FR-OPT, FR-ECO, FR-JOB)."""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from typing import Any

import numpy as np
import pandas as pd

from helionyx import DISCLAIMER
from helionyx.core import economics as eco
from helionyx.core.billing import compute_bill
from helionyx.core.engine import dispatch as dsp
from helionyx.core.engine import outages
from helionyx.core.engine.pv import pv_ac_per_kwp
from helionyx.core.engine.timeaxis import HOURS, MONTH
from helionyx.core.engine.wind import turbine_output
from helionyx.core.models.scenario import ResolvedScenario
from helionyx.errors import ErrorCode, HelionyxError, validation
from helionyx.infra.db import now_iso
from helionyx.infra.ids import new_id
from helionyx.infra.packs import get_pack
from helionyx.services.context import Helionyx, engine_version
from helionyx.services.resource import dataset_frame
from helionyx.services.scenario import enumerate_candidates, get_scenario, lock_scenario, resolved_of, scenario_hash
from helionyx.services.tariff import tariff_period_index

SORT_KEYS = {"npc": "npc", "lcoe": "lcoe", "initial_capital": "initial_capital"}
SIZE_KEYS = ("pv_kwp", "wind_count", "bess_kwh", "genset_kw")

_INPUT_CACHE: OrderedDict[str, dsp.SystemInputs] = OrderedDict()
_CACHE_LOCK = threading.Lock()


# --------------------------------------------------------------------------- inputs


def build_inputs(app: Helionyx, s: ResolvedScenario) -> dsp.SystemInputs:
    key = scenario_hash(s)
    with _CACHE_LOCK:
        if key in _INPUT_CACHE:
            _INPUT_CACHE.move_to_end(key)
            return _INPUT_CACHE[key]
    pack = get_pack(s.site.country_pack)
    load = np.zeros(HOURS)
    for ref in s.loads:
        load += dataset_frame(app, ref.dataset_id)["load_kw"].to_numpy(dtype=np.float64)
    res = dataset_frame(app, s.resource.dataset_id)
    ghi = res["ghi_w_m2"].to_numpy(dtype=np.float64)
    temp = res["temp_c"].to_numpy(dtype=np.float64) if "temp_c" in res else np.full(HOURS, 25.0)
    w10 = res["wind10_m_s"].to_numpy(dtype=np.float64) if "wind10_m_s" in res else np.zeros(HOURS)
    w50 = res["wind50_m_s"].to_numpy(dtype=np.float64) if "wind50_m_s" in res else None
    c = s.components
    pv_unit = np.zeros(HOURS)
    if c.pv is not None:
        t = c.pv.technical
        pv_unit = pv_ac_per_kwp(ghi, temp, w10, latitude=s.site.latitude, longitude=s.site.longitude,
                                elevation_m=s.site.elevation_m, timezone=s.site.timezone, tilt_deg=c.pv.tilt_deg,
                                azimuth_deg=c.pv.azimuth_deg, dc_ac_ratio=c.pv.dc_ac_ratio,
                                derating_factor=t["derating_factor"], temp_coeff_pct_per_c=t["temp_coeff_pct_per_c"],
                                inverter_efficiency=t["inverter_efficiency"], faiman_u0=t["faiman_u0"],
                                faiman_u1=t["faiman_u1"], albedo=t["albedo"])
    wind_unit = np.zeros(HOURS)
    if c.wind is not None:
        t = c.wind.technical
        wind_unit = turbine_output(w10, w50, hub_height_m=t["hub_height_m"], power_curve=t["power_curve"],
                                   availability=t["availability"], roughness_length_m=t["roughness_length_m"],
                                   elevation_m=s.site.elevation_m, air_density_correction=t["air_density_correction"])
    weekend = pack.manifest.weekend_days
    tariff = s.grid.tariff
    if s.grid.mode == "grid_connected" and tariff is not None:
        period = tariff_period_index(tariff, weekend)
        names = tariff.period_names
    else:
        period = np.zeros(HOURS, dtype=np.int64)
        names = ["all"]
    av = s.grid.availability
    if av.type == "scheduled":
        avail = outages.scheduled([(w.start, w.end, w.days) for w in av.windows], weekend)
    elif av.type == "stochastic":
        avail = outages.stochastic(av.events_per_year, av.mean_duration_h, av.seed)
    else:
        avail = outages.always()
    d = s.dispatch
    dis_mask = np.array([n in d.battery_discharge_periods for n in names], dtype=np.bool_)
    ch_mask = np.array([n in d.charge_periods for n in names], dtype=np.bool_)
    eta_conv = float(c.converter.technical["efficiency"]) if c.converter is not None else 1.0
    if c.bess is not None:
        b = c.bess.technical
        eta = eta_conv * math.sqrt(float(b["round_trip_efficiency"]))
        soc_min, soc0 = float(b["soc_min"]), float(b["soc_initial"])
    else:
        eta, soc_min, soc0 = 1.0, 0.0, 0.0
    g = c.genset.technical if c.genset is not None else {"min_load_ratio": 0.0,
                                                         "fuel_curve_intercept_l_per_h_per_kw": 0.0,
                                                         "fuel_curve_slope_l_per_kwh": 0.0}
    params = np.zeros(dsp.N_PRM)
    params[dsp.P_ETA_C] = eta
    params[dsp.P_ETA_D] = eta
    params[dsp.P_SOC_MIN] = soc_min
    params[dsp.P_SOC0] = max(soc0, soc_min)
    params[dsp.P_SOC_RES] = d.soc_reserve_for_outage
    params[dsp.P_SOC_SP] = d.cc_setpoint_soc
    params[dsp.P_RMIN] = float(g["min_load_ratio"])
    params[dsp.P_F0] = float(g["fuel_curve_intercept_l_per_h_per_kw"])
    params[dsp.P_F1] = float(g["fuel_curve_slope_l_per_kwh"])
    params[dsp.P_IMP_MAX] = s.grid.max_import_kw
    params[dsp.P_EXP_MAX] = s.grid.max_export_kw
    flags = np.zeros(dsp.N_FLG, dtype=np.int64)
    flags[dsp.F_GRID] = 1 if s.grid.mode == "grid_connected" else 0
    flags[dsp.F_EXPORT] = 1 if s.grid.export_scheme != "none" else 0
    flags[dsp.F_GRID_CHARGING] = 1 if d.grid_charging else 0
    flags[dsp.F_STRATEGY_CC] = 1 if d.strategy == "cycle_charging" else 0
    flags[dsp.F_CC_HOLD] = 1 if d.cc_hold_until_setpoint else 0
    flags[dsp.F_NET_PLUS] = 1 if s.grid.export_scheme == "net_plus" else 0
    inp = dsp.SystemInputs(load=load, pv_unit=pv_unit, wind_unit=wind_unit, available=avail, period=period,
                           month=MONTH, discharge_mask=dis_mask, charge_mask=ch_mask, params=params, flags=flags,
                           n_periods=len(names))
    with _CACHE_LOCK:
        _INPUT_CACHE[key] = inp
        while len(_INPUT_CACHE) > 8:
            _INPUT_CACHE.popitem(last=False)
    return inp


def size_arrays(s: ResolvedScenario, sizes: list[tuple[float, float, float, float]]) -> tuple[np.ndarray, ...]:
    arr = np.array(sizes, dtype=np.float64).reshape(-1, 4)
    ppk = s.components.bess.power_kw_per_kwh if s.components.bess is not None else 0.0
    return arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 2] * ppk, arr[:, 3]


# --------------------------------------------------------------------------- economics per candidate


def cost_items(s: ResolvedScenario, sz: dict[str, float], agg: np.ndarray, bill_purchases: float,
               bill_sales: float) -> list[eco.CostItem]:
    c = s.components
    e = s.economics
    items: list[eco.CostItem] = []

    def add(name: str, comp: Any, qty: float, lifetime: float, **kw: float) -> None:
        if comp is None or qty <= 0:
            return
        items.append(eco.CostItem(name=name, capital=comp.capital_per_unit * qty,
                                  replacement_cost=comp.replacement_per_unit * qty, lifetime_years=lifetime,
                                  om_per_year=comp.om_per_unit_year * qty, **kw))

    add("pv", c.pv, sz["pv_kwp"], c.pv.lifetime_years if c.pv else 0)
    add("wind", c.wind, sz["wind_count"], c.wind.lifetime_years if c.wind else 0)
    if c.bess is not None and sz["bess_kwh"] > 0:
        b = c.bess.technical
        thr = agg[dsp.A_THR]
        life = float(b["float_life_years"])
        if thr > 0:
            life = min(life, float(b["lifetime_throughput_kwh_per_kwh"]) * sz["bess_kwh"] / thr)
        add("bess", c.bess, sz["bess_kwh"], life)
        add("converter", c.converter, sz["bess_kw"], c.converter.lifetime_years if c.converter else 0)
    if c.genset is not None and sz["genset_kw"] > 0:
        hours = agg[dsp.A_GEN_HOURS]
        life = c.genset.lifetime_years
        if hours > 0:
            life = min(float(c.genset.technical["lifetime_hours"]) / hours, life)
        add("genset", c.genset, sz["genset_kw"], life,
            fuel_per_year=agg[dsp.A_FUEL] * e.fuel_price_per_l, escalation_fuel=e.fuel_price_escalation_real)
    if s.grid.mode == "grid_connected":
        items.append(eco.CostItem(name="grid", grid_purchases_per_year=bill_purchases,
                                  grid_sales_per_year=bill_sales, escalation_grid=e.grid_price_escalation_real))
    if e.system_fixed_capital > 0 or e.system_fixed_om_per_year > 0:
        items.append(eco.CostItem(name="system_fixed", capital=e.system_fixed_capital,
                                  om_per_year=e.system_fixed_om_per_year))
    return items


def candidate_record(s: ResolvedScenario, idx: int, sz_tuple: tuple[float, ...], agg: np.ndarray,
                     imp_mp: np.ndarray, exp_mp: np.ndarray, peak: np.ndarray) -> dict[str, Any]:
    pv_kwp, wind_count, bess_kwh, gen_kw = sz_tuple
    ppk = s.components.bess.power_kw_per_kwh if s.components.bess is not None else 0.0
    sz = {"pv_kwp": pv_kwp, "wind_count": wind_count, "bess_kwh": bess_kwh, "bess_kw": bess_kwh * ppk,
          "genset_kw": gen_kw}
    purchases = sales = annual_bill = 0.0
    if s.grid.mode == "grid_connected" and s.grid.tariff is not None:
        bill = compute_bill(s.grid.tariff, s.grid.export_scheme, imp_mp, exp_mp, peak, s.options.demand_peak_factor)
        purchases, sales, annual_bill = bill.purchases, bill.sales, bill.total
    i = s.economics.real_discount_rate
    n = s.economics.project_life_years
    items = cost_items(s, sz, agg, purchases, sales)
    ev = eco.evaluate(items, i, n)
    load = agg[dsp.A_LOAD]
    served = load - agg[dsp.A_UNS]
    gen_total = agg[dsp.A_PV] + agg[dsp.A_WT] + agg[dsp.A_GEN]
    denom = served + agg[dsp.A_EXP]
    rf = 1.0 - (agg[dsp.A_GEN] + agg[dsp.A_IMP] * (1.0 - s.economics.grid_renewable_fraction)) / denom \
        if denom > 0 else 0.0
    usable = bess_kwh * (1.0 - float(s.components.bess.technical["soc_min"])) if s.components.bess else 0.0
    eta_d = 1.0
    if s.components.bess is not None:
        conv = float(s.components.converter.technical["efficiency"]) if s.components.converter else 1.0
        eta_d = conv * math.sqrt(float(s.components.bess.technical["round_trip_efficiency"]))
    lc = eco.lcoe(ev.annualised_cost, served, agg[dsp.A_EXP])
    metrics = {
        "npc": ev.npc,
        "lcoe_per_kwh": lc,
        "initial_capital": ev.initial_capital,
        "operating_cost_per_yr": ev.operating_cost_per_year,
        "annualised_cost_per_yr": ev.annualised_cost,
        "renewable_fraction_pct": 100.0 * rf,
        "capacity_shortage_pct": 100.0 * agg[dsp.A_UNS] / load if load > 0 else 0.0,
        "excess_electricity_pct": 100.0 * agg[dsp.A_XS] / gen_total if gen_total > 0 else 0.0,
        "load_kwh_per_yr": load,
        "served_kwh_per_yr": served,
        "unserved_kwh_per_yr": agg[dsp.A_UNS],
        "pv_kwh_per_yr": agg[dsp.A_PV],
        "wind_kwh_per_yr": agg[dsp.A_WT],
        "genset_kwh_per_yr": agg[dsp.A_GEN],
        "grid_import_kwh_per_yr": agg[dsp.A_IMP],
        "grid_export_kwh_per_yr": agg[dsp.A_EXP],
        "excess_kwh_per_yr": agg[dsp.A_XS],
        "bess_throughput_kwh_per_yr": agg[dsp.A_THR],
        "bess_equivalent_full_cycles_per_yr": agg[dsp.A_THR] / usable if usable > 0 else 0.0,
        "bess_autonomy_h": usable * eta_d / (load / HOURS) if load > 0 and usable > 0 else 0.0,
        "fuel_l_per_yr": agg[dsp.A_FUEL],
        "genset_hours_per_yr": agg[dsp.A_GEN_HOURS],
        "genset_starts_per_yr": agg[dsp.A_GEN_STARTS],
        "genset_mean_load_ratio_pct": 100.0 * agg[dsp.A_GEN] / (agg[dsp.A_GEN_HOURS] * gen_kw)
        if agg[dsp.A_GEN_HOURS] > 0 and gen_kw > 0 else 0.0,
        "peak_export_kw": agg[dsp.A_PEAK_EXP],
        "annual_bill": annual_bill,
        "co2_kg_per_yr": eco.emissions_kg_per_year(agg[dsp.A_FUEL], agg[dsp.A_IMP], s.economics.diesel_kg_co2_per_l,
                                                   s.economics.grid_kg_co2_per_kwh),
        "outage_hours_per_yr": agg[dsp.A_OUTAGE_HOURS],
        "outage_unserved_kwh_per_yr": agg[dsp.A_OUTAGE_UNS],
        "max_energy_balance_error_kwh": agg[dsp.A_BAL_ERR],
    }
    lifetimes = {it.name: it.lifetime_years for it in items if math.isfinite(it.lifetime_years)}
    violations = _violations(s, sz, metrics)
    return {"candidate_index": idx, "sizes": sz, "metrics": metrics, "feasible": not violations,
            "violations": violations, "cost_breakdown": ev.breakdown, "cost_by_type": ev.by_type,
            "component_lifetimes_yr": lifetimes, "cash_flows": ev.cash_flows}


def _violations(s: ResolvedScenario, sz: dict[str, float], m: dict[str, Any]) -> list[dict[str, Any]]:
    k = s.constraints
    out: list[dict[str, Any]] = []

    def v(name: str, value: float, limit: float, kind: str) -> None:
        amount = (value - limit) if kind == "max" else (limit - value)
        if amount > 1e-9:
            out.append({"constraint": name, "value": value, "limit": limit,
                        "normalised_violation": amount / max(abs(limit), 1e-3)})

    v("max_capacity_shortage", m["capacity_shortage_pct"] / 100.0, k.max_capacity_shortage, "max")
    if k.min_renewable_fraction is not None:
        v("min_renewable_fraction", m["renewable_fraction_pct"] / 100.0, k.min_renewable_fraction, "min")
    if k.max_genset_hours is not None:
        v("max_genset_hours", m["genset_hours_per_yr"], k.max_genset_hours, "max")
    if k.max_export_kw is not None:
        v("max_export_kw", m["peak_export_kw"], k.max_export_kw, "max")
    if k.max_pv_kwp is not None:
        v("max_pv_kwp", sz["pv_kwp"], k.max_pv_kwp, "max")
    if k.max_initial_capital is not None:
        v("max_initial_capital", m["initial_capital"], k.max_initial_capital, "max")
    return out


def evaluate_sizes(app: Helionyx, s: ResolvedScenario, sizes: list[tuple[float, float, float, float]],
                   progress: Any = None, base_index: int = 0, p_from: float = 0.0, p_to: float = 100.0,
                   ) -> list[dict[str, Any]]:
    inp = build_inputs(app, s)
    n = len(sizes)
    chunk = max(64, math.ceil(n / 20)) if n else 1
    out: list[dict[str, Any]] = []
    for start in range(0, n, chunk):
        part = sizes[start:start + chunk]
        pv, wt, kwh, kw, gen = size_arrays(s, part)
        r = dsp.simulate_batch(inp, pv, wt, kwh, kw, gen)
        for j, sz in enumerate(part):
            out.append(candidate_record(s, base_index + start + j, sz, r.agg[j], r.imp_mp[j], r.exp_mp[j], r.peak[j]))
        if progress is not None:
            progress(p_from + (p_to - p_from) * min(n, start + chunk) / n, f"simulated {min(n, start + chunk)}/{n}")
    return out


def base_case_sizes(s: ResolvedScenario, peak_load_kw: float) -> tuple[str, tuple[float, float, float, float]] | None:
    """Grid-only for grid-connected; diesel-only for off-grid with a genset (FR-OPT-004)."""
    if s.grid.mode == "grid_connected":
        return "grid_only", (0.0, 0.0, 0.0, 0.0)
    if s.components.genset is not None:
        sizes = sorted(x for x in s.components.genset.sizes_kw if x > 0)
        if sizes:
            fit = [x for x in sizes if x >= peak_load_kw]
            return "diesel_only", (0.0, 0.0, 0.0, fit[0] if fit else sizes[-1])
    return None


def _sort_value(c: dict[str, Any], key: str) -> float:
    m = c["metrics"]
    val = {"npc": m["npc"], "lcoe": m["lcoe_per_kwh"], "initial_capital": m["initial_capital"]}[key]
    return float("inf") if val is None else float(val)


def rank(cands: list[dict[str, Any]], sort_by: str) -> None:
    feas = sorted((c for c in cands if c["feasible"]), key=lambda c: (_sort_value(c, sort_by), c["candidate_index"]))
    for c in cands:
        c["rank"] = None
    for r, c in enumerate(feas, start=1):
        c["rank"] = r


def attach_payback(cands: list[dict[str, Any]], base: dict[str, Any] | None, i: float) -> None:
    for c in cands:
        if base is None:
            c["metrics"].update(simple_payback_yr=None, discounted_payback_yr=None, irr_pct=None)
            continue
        pb = eco.payback(c["cash_flows"], base["cash_flows"], i)
        c["metrics"].update(simple_payback_yr=pb.simple_payback_yr, discounted_payback_yr=pb.discounted_payback_yr,
                            irr_pct=pb.irr_pct)


def optimise(app: Helionyx, s: ResolvedScenario, sort_by: str, progress: Any = None
             ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    sizes = enumerate_candidates(s)
    cands = evaluate_sizes(app, s, sizes, progress, 0, 2.0, 90.0)
    inp = build_inputs(app, s)
    base = None
    bc = base_case_sizes(s, float(inp.load.max()))
    if bc is not None:
        label, bsz = bc
        base = evaluate_sizes(app, s, [bsz], base_index=-1)[0]
        base["label"] = label
        base["feasible"] = True
        base["metrics"].update(simple_payback_yr=None, discounted_payback_yr=None, irr_pct=None)
    attach_payback(cands, base, s.economics.real_discount_rate)
    rank(cands, sort_by)
    return cands, base


# --------------------------------------------------------------------------- run lifecycle


def _persist_series(app: Helionyx, s: ResolvedScenario, cand: dict[str, Any]) -> str:
    inp = build_inputs(app, s)
    sz = cand["sizes"]
    _, series = dsp.simulate_series(inp, sz["pv_kwp"], sz["wind_count"], sz["bess_kwh"], sz["bess_kw"],
                                    sz["genset_kw"])
    digest, _ = app.artefacts.put_frame(pd.DataFrame(series, columns=list(dsp.SERIES_FIELDS)))
    return digest


def execute_run(app: Helionyx, run_id: str, scenario_doc: dict[str, Any], sort_by: str, keep_top_n: int,
                progress: Any) -> dict[str, Any]:
    t0 = time.perf_counter()
    s = resolved_of(scenario_doc)
    progress(1.0, "preparing inputs")
    cands, base = optimise(app, s, sort_by, progress)
    progress(92.0, "persisting results")
    top = sorted((c for c in cands if c["rank"] is not None), key=lambda c: c["rank"])[:keep_top_n]
    for c in top:
        c["timeseries_digest"] = _persist_series(app, s, c)
    if base is not None:
        base["timeseries_digest"] = _persist_series(app, s, base)
    rows = [{k: v for k, v in c.items() if k != "cash_flows"} for c in cands]
    app.db.put_candidates(run_id, rows)
    feasible = sum(1 for c in cands if c["feasible"])
    run = app.db.get("runs", run_id, "run")
    run.update(status="completed", feasible_count=feasible, infeasible_count=len(cands) - feasible,
               base_case={k: v for k, v in base.items() if k != "cash_flows"} if base else None,
               wall_time_s=round(time.perf_counter() - t0, 3), finished_at=now_iso(),
               max_energy_balance_error_kwh=max((c["metrics"]["max_energy_balance_error_kwh"] for c in cands),
                                                default=0.0))
    app.db.put("runs", run_id, run, scenario_id=run["scenario_id"], status="completed")
    return {"run_id": run_id, "feasible_count": feasible, "candidate_count": len(cands)}


def start_run(app: Helionyx, scenario_id: str, solver: str = "native", sort_by: str = "npc",
              keep_timeseries_top_n: int | None = None, owner: str = "local") -> dict[str, Any]:
    doc = get_scenario(app, scenario_id)
    if doc["errors"]:
        raise validation("The scenario has validation errors and cannot run.",
                         "Call validate_scenario, fix the errors and create a new scenario version.",
                         errors=doc["errors"][:5])
    if sort_by not in SORT_KEYS:
        raise validation(f"Unknown sort_by '{sort_by[:30]}'.", "Use npc, lcoe or initial_capital.")
    if solver not in ("native", "reopt"):
        raise HelionyxError(ErrorCode.UNSUPPORTED_COMBINATION, f"Solver '{solver[:30]}' is not available in v0.1.",
                            "Use solver='native' (or 'reopt' with HNX_REOPT_API_KEY set).")
    s = resolved_of(doc)
    keep = keep_timeseries_top_n if keep_timeseries_top_n is not None else s.options.keep_timeseries_top_n
    run_id = new_id("run")
    lock_scenario(app, doc)
    run = {"run_id": run_id, "scenario_id": scenario_id, "scenario_hash": doc["scenario_hash"], "solver": solver,
           "sort_by": sort_by, "status": "queued", "engine": engine_version(), "packs": s.pack, "seed": s.seed,
           "currency": s.economics.currency, "created_at": now_iso(), "disclaimer": DISCLAIMER}
    app.db.put("runs", run_id, run, scenario_id=scenario_id, status="queued")
    if solver == "reopt":
        from helionyx.adapters.reopt import ReoptAdapter

        adapter = ReoptAdapter(app)

        def job_fn(progress: Any) -> dict[str, Any]:
            return adapter.execute(run_id, doc, progress)
    else:
        def job_fn(progress: Any) -> dict[str, Any]:
            return execute_run(app, run_id, doc, sort_by, keep, progress)

    job = app.jobs.submit("optimization", owner, job_fn, {"run_id": run_id, "scenario_id": scenario_id})
    run["job_id"] = job["job_id"]
    app.db.put("runs", run_id, run, scenario_id=scenario_id, status="queued")
    return {"job_id": job["job_id"], "run_id": run_id, "state": job["state"], "scenario_id": scenario_id,
            "candidate_count": doc["candidate_count"]}


def get_run(app: Helionyx, run_id: str) -> dict[str, Any]:
    run = app.db.get("runs", run_id, "run")
    if run["status"] != "completed":
        job = app.jobs.status(run["job_id"]) if run.get("job_id") else None
        state = job["state"] if job else run["status"]
        if state != "completed":
            raise HelionyxError(ErrorCode.VALIDATION_FAILED, f"Run {run_id} is not complete (state: {state}).",
                                "Poll get_job_status until the state is 'completed'.",
                                {"state": state, "job_id": run.get("job_id")})
        run = app.db.get("runs", run_id, "run")
    return run
