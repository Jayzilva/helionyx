"""REopt API v3 adapter (FR-ADP-002).

REopt solves a MILP with perfect foresight. Helionyx supplies the custom hourly
load, a PV production-factor series from its own PV model and an hourly energy
rate series from the tariff, so no US-specific datasets are needed. Values are
passed in the scenario currency; REopt's arithmetic is currency-agnostic.

Set ``HNX_REOPT_API_KEY``. For offline tests set ``HNX_REOPT_FIXTURE`` to a
recorded JSON results file; the adapter then replays it instead of calling the API.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from helionyx.errors import ErrorCode, HelionyxError
from helionyx.infra.db import now_iso
from helionyx.services.context import Helionyx

BASE_URL = os.environ.get("HNX_REOPT_URL", "https://developer.nlr.gov/api/reopt/stable")
RATE_LIMIT_PER_HOUR = 60
DEFAULT_SCALE = 300.0
# REopt defaults to US tax incentives; none apply outside the US.
_NO_US_INCENTIVES: dict[str, Any] = {"federal_itc_fraction": 0.0, "macrs_option_years": 0, "macrs_bonus_fraction": 0.0}
_calls: deque[float] = deque()
_lock = threading.Lock()


def _acquire_slot() -> None:
    with _lock:
        now = time.monotonic()
        while _calls and now - _calls[0] > 3600:
            _calls.popleft()
        if len(_calls) >= RATE_LIMIT_PER_HOUR:
            raise HelionyxError(ErrorCode.RATE_LIMITED, "The REopt limit of 60 runs per hour has been reached.",
                                "Wait and retry, or use the native solver.",
                                {"retry_after_s": int(3600 - (now - _calls[0]))})
        _calls.append(now)


def currency_scale(s: Any) -> float:
    """Local currency units per REopt money unit: the user FX rate if given, else the pack placeholder.

    REopt's optimisation is linear in money, so any positive scale gives the same sizes;
    outputs are multiplied back by the same factor.
    """
    return float(s.economics.fx_rate_per_usd or DEFAULT_SCALE)


class ReoptAdapter:
    name = "reopt"
    version = "v3"

    def __init__(self, app: Helionyx) -> None:
        self.app = app

    # ------------------------------------------------------------------ prepare
    def prepare(self, scenario_doc: dict[str, Any]) -> dict[str, Any]:
        from helionyx.services.run import build_inputs
        from helionyx.services.scenario import resolved_of

        s = resolved_of(scenario_doc)
        if s.grid.mode != "grid_connected" or s.grid.tariff is None:
            raise HelionyxError(ErrorCode.UNSUPPORTED_COMBINATION,
                                "The v0.1 REopt adapter supports grid-connected scenarios only.",
                                "Use the native solver for off-grid scenarios.")
        if s.components.wind is not None or s.components.genset is not None:
            raise HelionyxError(ErrorCode.UNSUPPORTED_COMBINATION,
                                "The v0.1 REopt adapter maps PV and battery only.",
                                "Remove wind and genset, or use the native solver.")
        t = s.grid.tariff
        if t.charges.energy.type == "block":
            raise HelionyxError(ErrorCode.UNSUPPORTED_COMBINATION, "Block tariffs are not mapped to REopt in v0.1.",
                                "Use a TOU or flat tariff, or the native solver.")
        inp = build_inputs(self.app, s)
        names = t.period_names
        rates = t.charges.energy.rates_per_kwh or {"all": t.charges.energy.rate_per_kwh or 0.0}
        e = s.economics
        k = 1.0 / currency_scale(s)  # REopt range-checks money in USD-like magnitudes; scale back in normalise()
        hourly_rate = (np.array([rates[names[p]] for p in inp.period], dtype=float) * k).round(8).tolist()
        body: dict[str, Any] = {
            "Site": {"latitude": s.site.latitude, "longitude": s.site.longitude},
            "ElectricLoad": {"loads_kw": inp.load.round(4).tolist(), "year": 2023},
            "ElectricTariff": {"tou_energy_rates_per_kwh": hourly_rate},
            # Real terms and no taxes: Helionyx economics are in constant currency without tax effects.
            "Financial": {"analysis_years": e.project_life_years,
                          "offtaker_discount_rate_fraction": e.real_discount_rate,
                          "owner_discount_rate_fraction": e.real_discount_rate,
                          "elec_cost_escalation_rate_fraction": e.grid_price_escalation_real,
                          "om_cost_escalation_rate_fraction": 0.0,
                          "offtaker_tax_rate_fraction": 0.0, "owner_tax_rate_fraction": 0.0},
        }
        if t.charges.demand is not None and t.charges.demand.per_kva_month > 0:
            per_kw = (t.charges.demand.per_kva_month * s.options.demand_peak_factor
                      / t.charges.demand.power_factor_assumption)
            body["ElectricTariff"]["monthly_demand_rates"] = [per_kw * k] * 12
        scheme = s.grid.export_scheme
        if scheme in ("net_accounting", "net_plus"):
            rate = getattr(t.export_schemes, scheme)
            body["ElectricTariff"]["wholesale_rate"] = rate.export_rate_per_kwh * k if rate else 0.0
        if s.components.pv is not None:
            pv = s.components.pv
            body["PV"] = {"production_factor_series": inp.pv_unit.round(6).tolist(),
                          "min_kw": min(pv.sizes_kwp), "max_kw": s.constraints.max_pv_kwp or max(pv.sizes_kwp),
                          "installed_cost_per_kw": pv.capital_per_unit * k, "om_cost_per_kw": pv.om_per_unit_year * k,
                          "dc_ac_ratio": pv.dc_ac_ratio, "can_export_beyond_nem_limit": scheme != "none",
                          "can_net_meter": False, "can_wholesale": scheme != "none", "degradation_fraction": 0.0,
                          **_NO_US_INCENTIVES}
        if s.components.bess is not None:
            b = s.components.bess
            conv = s.components.converter
            body["ElectricStorage"] = {"min_kwh": min(b.sizes_kwh), "max_kwh": max(b.sizes_kwh),
                                       "max_kw": max(b.sizes_kwh) * b.power_kw_per_kwh,
                                       "installed_cost_per_kwh": b.capital_per_unit * k,
                                       "installed_cost_per_kw": conv.capital_per_unit * k if conv else 0.0,
                                       "replace_cost_per_kwh": b.replacement_per_unit * k,
                                       "internal_efficiency_fraction": math.sqrt(b.technical["round_trip_efficiency"]),
                                       "rectifier_efficiency_fraction": conv.technical["efficiency"] if conv else 1.0,
                                       "inverter_efficiency_fraction": conv.technical["efficiency"] if conv else 1.0,
                                       "battery_replacement_year": int(b.technical["float_life_years"]),
                                       "soc_min_fraction": b.technical["soc_min"],
                                       "soc_init_fraction": b.technical["soc_initial"],
                                       "can_grid_charge": s.dispatch.grid_charging,
                                       "replace_cost_per_kw": conv.replacement_per_unit * k if conv else 0.0,
                                       "inverter_replacement_year": int(conv.lifetime_years) if conv else 10,
                                       "installed_cost_constant": 0.0, "replace_cost_constant": 0.0,
                                       "total_itc_fraction": 0.0, "macrs_option_years": 0,
                                       "macrs_bonus_fraction": 0.0}
        return body

    # ------------------------------------------------------------------ run
    def run(self, prepared: dict[str, Any], progress: Any) -> dict[str, Any]:
        fixture = os.environ.get("HNX_REOPT_FIXTURE")
        if fixture:
            progress(50.0, "replaying recorded REopt fixture")
            data: dict[str, Any] = json.loads(Path(fixture).read_text(encoding="utf-8"))
            return data
        key = self.app.settings.reopt_api_key
        if not key:
            raise HelionyxError(ErrorCode.UNAUTHORIZED, "No REopt API key is configured.",
                                "Register at developer.nlr.gov and set HNX_REOPT_API_KEY.")
        if self.app.settings.offline:
            raise HelionyxError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, "Offline mode is on.",
                                "Disable HNX_OFFLINE to use REopt.")
        _acquire_slot()
        try:
            r = httpx.post(f"{BASE_URL}/job/", params={"api_key": key}, json=prepared, timeout=60)
            if r.status_code >= 400:
                raise HelionyxError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                                    f"REopt rejected the job (HTTP {r.status_code}).",
                                    "Check the inputs; see details.", {"body": r.text[:400]})
            run_uuid = r.json()["run_uuid"]
            start = time.monotonic()
            while True:
                progress(min(90.0, 5.0 + (time.monotonic() - start) / 6.0), "waiting for REopt")
                res = httpx.get(f"{BASE_URL}/job/{run_uuid}/results/", params={"api_key": key}, timeout=60)
                body: dict[str, Any] = res.json()
                api_error = body.get("error")
                if isinstance(api_error, dict) and api_error.get("code") == "OVER_RATE_LIMIT":
                    raise HelionyxError(ErrorCode.RATE_LIMITED, "The REopt API key has exceeded its rate limit.",
                                        "Wait and retry, or register a personal key at developer.nlr.gov "
                                        "(DEMO_KEY is shared and heavily limited).", {"run_uuid": run_uuid})
                status = str(body.get("status", "")).lower()
                if "optimal" in status or status == "completed":
                    return body
                if "error" in status or "infeasible" in status:
                    raise HelionyxError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                                        f"REopt finished with status '{status[:60]}'.",
                                        "Check the REopt messages in details.",
                                        {"messages": body.get("messages", {})})
                time.sleep(5)
        except httpx.HTTPError as exc:
            raise HelionyxError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, "REopt could not be reached.",
                                "Retry later or use the native solver.") from exc

    # ------------------------------------------------------------------ normalise
    def normalise(self, raw: dict[str, Any], scenario_doc: dict[str, Any]) -> dict[str, Any]:
        out = raw.get("outputs", {})
        pv = out.get("PV", {}) or {}
        st = out.get("ElectricStorage", {}) or {}
        fin = out.get("Financial", {}) or {}
        tar = out.get("ElectricTariff", {}) or {}
        site = out.get("Site", {}) or {}
        rf = site.get("renewable_electricity_fraction")
        irr = fin.get("internal_rate_of_return")
        from helionyx.services.scenario import resolved_of

        scale = currency_scale(resolved_of(scenario_doc))

        def money(v: Any) -> float | None:
            return None if v is None else float(v) * scale

        return {
            "candidate_index": 0, "rank": 1, "feasible": True, "violations": [],
            "sizes": {"pv_kwp": float(pv.get("size_kw", 0.0)), "wind_count": 0.0,
                      "bess_kwh": float(st.get("size_kwh", 0.0)), "bess_kw": float(st.get("size_kw", 0.0)),
                      "genset_kw": 0.0},
            "metrics": {"npc": money(fin.get("lcc")), "lcoe_per_kwh": None,
                        "initial_capital": money(fin.get("initial_capital_costs")), "operating_cost_per_yr": None,
                        "annual_bill": money(tar.get("year_one_bill_before_tax")),
                        "renewable_fraction_pct": 100.0 * rf if rf is not None else None,
                        "capacity_shortage_pct": 0.0, "excess_electricity_pct": None,
                        "simple_payback_yr": fin.get("simple_payback_years"), "discounted_payback_yr": None,
                        "irr_pct": 100.0 * irr if irr is not None else None,
                        "fuel_l_per_yr": 0.0, "co2_kg_per_yr": None},
            "solver": {"name": "reopt", "version": raw.get("reopt_version", self.version),
                       "label": "MILP, perfect foresight", "run_uuid": raw.get("run_uuid"),
                       "currency_scale": scale},
            "cost_breakdown": {}, "cost_by_type": {},
        }

    # ------------------------------------------------------------------ job entry point
    def execute(self, run_id: str, scenario_doc: dict[str, Any], progress: Any) -> dict[str, Any]:
        progress(1.0, "mapping scenario to REopt")
        prepared = self.prepare(scenario_doc)
        raw = self.run(prepared, progress)
        cand = self.normalise(raw, scenario_doc)
        self.app.db.put_candidates(run_id, [cand])
        run = self.app.db.get("runs", run_id, "run")
        run.update(status="completed", feasible_count=1, infeasible_count=0, base_case=None,
                   solver_detail=cand["solver"], finished_at=now_iso(),
                   privacy_notice="Load data was sent to the REopt API (developer.nlr.gov).")
        self.app.db.put("runs", run_id, run, scenario_id=run["scenario_id"], status="completed")
        return {"run_id": run_id, "solver": "reopt"}
