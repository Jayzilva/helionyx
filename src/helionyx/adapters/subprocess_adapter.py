"""Out-of-process solver adapters (FR-ADP-001, FR-ADP-003, FR-ADP-004).

Copyleft solvers stay outside the Apache-2.0 core (constraint C5, NFR-LIC-01):
MicroGridsPy is EUPL-1.2 and SAMAPy is AGPL-3. Each ships as a separate package
with its own command-line program. The core talks to it only through JSON files
(schema ``helionyx-adapter-io/1``, documented in docs/adapters.md):

    <command> run <input.json> <output.json>

The core never imports the solver package.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from helionyx.errors import ErrorCode, HelionyxError
from helionyx.infra.db import now_iso
from helionyx.services.context import Helionyx

SCHEMA = "helionyx-adapter-io/1"

SOLVERS: dict[str, dict[str, str]] = {
    "microgridspy": {"env": "HNX_MICROGRIDSPY_CMD", "default": "helionyx-microgridspy",
                     "package": "helionyx-microgridspy", "licence": "EUPL-1.2",
                     "label": "LP (linopy + HiGHS), perfect foresight, annualised costs"},
    "sama": {"env": "HNX_SAMA_CMD", "default": "helionyx-sama", "package": "helionyx-sama", "licence": "AGPL-3.0",
             "label": "metaheuristic (SAMA), rule-based dispatch"},
}
DIESEL_LHV_KWH_PER_L = 9.9


def build_input(app: Helionyx, scenario_doc: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Solver-neutral description of a scenario: hourly series, components, economics, constraints."""
    from helionyx.services.run import build_inputs
    from helionyx.services.scenario import resolved_of

    s = resolved_of(scenario_doc)
    inp = build_inputs(app, s)
    c = s.components

    def comp(x: Any, sizes: list[float] | None) -> dict[str, Any] | None:
        if x is None:
            return None
        return {"spec": x.spec, "capital_per_unit": x.capital_per_unit, "replacement_per_unit": x.replacement_per_unit,
                "om_per_unit_year": x.om_per_unit_year, "lifetime_years": x.lifetime_years,
                "technical": x.technical,
                "min_size": min(sizes) if sizes else None, "max_size": max(sizes) if sizes else None,
                "sizes": sizes}

    import_price = export_price = None
    t = s.grid.tariff
    if s.grid.mode == "grid_connected" and t is not None:
        names = t.period_names
        rates = t.charges.energy.rates_per_kwh or {"all": t.charges.energy.rate_per_kwh or 0.0}
        if t.charges.energy.type == "block":  # use the top slab as the marginal price
            rates = {"all": (t.charges.energy.blocks or [])[-1].rate_per_kwh}
            names = ["all"]
        import_price = [float(rates[names[p]]) if len(names) > 1 else float(rates[names[0]]) for p in inp.period]
        scheme = getattr(t.export_schemes, s.grid.export_scheme, None) if s.grid.export_scheme != "none" else None
        rate = getattr(scheme, "export_rate_per_kwh", 0.0) if scheme is not None else 0.0
        export_price = [float(rate)] * len(import_price)
    genset = comp(c.genset, c.genset.sizes_kw if c.genset else None)
    if genset is not None:
        g = c.genset.technical if c.genset else {}
        full_load_l_per_kwh = g["fuel_curve_intercept_l_per_h_per_kw"] + g["fuel_curve_slope_l_per_kwh"]
        genset["full_load_efficiency"] = 1.0 / (full_load_l_per_kwh * DIESEL_LHV_KWH_PER_L)
        genset["fuel_lhv_kwh_per_l"] = DIESEL_LHV_KWH_PER_L
    return {
        "schema": SCHEMA,
        "scenario_id": scenario_doc["scenario_id"], "scenario_hash": scenario_doc["scenario_hash"],
        "currency": s.economics.currency, "grid_mode": s.grid.mode, "seed": s.seed,
        "site": {"latitude": s.site.latitude, "longitude": s.site.longitude, "timezone": s.site.timezone},
        "time_series": {
            "load_kw": np.round(inp.load, 6).tolist(),
            "pv_kw_per_kwp": np.round(inp.pv_unit, 6).tolist(),
            "wind_kw_per_turbine": np.round(inp.wind_unit, 6).tolist(),
            "grid_available": inp.available.astype(int).tolist(),
            "grid_import_price_per_kwh": import_price, "grid_export_price_per_kwh": export_price,
        },
        "components": {
            "pv": comp(c.pv, c.pv.sizes_kwp if c.pv else None) | ({"dc_ac_ratio": c.pv.dc_ac_ratio} if c.pv else {})
            if c.pv else None,
            "wind": comp(c.wind, c.wind.counts if c.wind else None),
            "bess": comp(c.bess, c.bess.sizes_kwh if c.bess else None)
            | ({"power_kw_per_kwh": c.bess.power_kw_per_kwh} if c.bess else {}) if c.bess else None,
            "converter": comp(c.converter, None),
            "genset": genset,
        },
        "economics": {"real_discount_rate": s.economics.real_discount_rate,
                      "project_life_years": s.economics.project_life_years,
                      "fuel_price_per_l": s.economics.fuel_price_per_l,
                      "grid_max_import_kw": s.grid.max_import_kw, "grid_max_export_kw": s.grid.max_export_kw},
        "constraints": {"max_capacity_shortage": s.constraints.max_capacity_shortage,
                        "min_renewable_fraction": s.constraints.min_renewable_fraction,
                        "max_pv_kwp": s.constraints.max_pv_kwp},
        "options": {"time_limit_s": 900, **(options or {})},
    }


def crf(i: float, n: int) -> float:
    return 1.0 / n if abs(i) < 1e-12 else i * (1 + i) ** n / ((1 + i) ** n - 1)


class SubprocessAdapter:
    def __init__(self, app: Helionyx, name: str) -> None:
        if name not in SOLVERS:
            raise HelionyxError(ErrorCode.UNSUPPORTED_COMBINATION, f"Unknown adapter '{name[:30]}'.",
                                f"Use one of {', '.join(SOLVERS)}.")
        self.app = app
        self.name = name
        self.meta = SOLVERS[name]

    def command(self) -> list[str]:
        raw = os.environ.get(self.meta["env"], self.meta["default"])
        parts = [x.strip('"') for x in shlex.split(raw, posix=os.name != "nt")]
        if not parts or (shutil.which(parts[0]) is None and not Path(parts[0]).exists()):
            raise HelionyxError(
                ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                f"The {self.name} adapter is not installed.",
                f"Install the separately licensed package `{self.meta['package']}` ({self.meta['licence']}) "
                f"or set {self.meta['env']} to its command.", {"command": raw[:200]})
        return parts

    def prepare(self, scenario_doc: dict[str, Any]) -> dict[str, Any]:
        return build_input(self.app, scenario_doc)

    def run(self, prepared: dict[str, Any], progress: Any) -> dict[str, Any]:
        cmd = self.command()
        timeout = float(prepared["options"]["time_limit_s"]) + 300
        with tempfile.TemporaryDirectory(dir=self.app.settings.workspace) as tmp:
            src, dst = Path(tmp) / "input.json", Path(tmp) / "output.json"
            src.write_text(json.dumps(prepared), encoding="utf-8")
            progress(5.0, f"running {self.name}")
            try:
                proc = subprocess.run([*cmd, "run", str(src), str(dst)], capture_output=True, text=True,
                                      timeout=timeout, check=False)
            except subprocess.TimeoutExpired as exc:
                raise HelionyxError(ErrorCode.JOB_TIMEOUT, f"{self.name} did not finish in {timeout:.0f} s.",
                                    "Reduce the problem size or raise options.time_limit_s.") from exc
            if proc.returncode != 0 or not dst.exists():
                raise HelionyxError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                                    f"{self.name} exited with code {proc.returncode}.",
                                    "See details for the last lines of its error output.",
                                    {"stderr_tail": proc.stderr[-800:]})
            out: dict[str, Any] = json.loads(dst.read_text(encoding="utf-8"))
        if out.get("schema") != SCHEMA:
            raise HelionyxError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, f"{self.name} returned an unknown schema.",
                                f"Update {self.meta['package']} to a version that writes {SCHEMA}.")
        return out

    def normalise(self, raw: dict[str, Any], scenario_doc: dict[str, Any]) -> dict[str, Any]:
        if raw.get("status") not in ("optimal", "feasible", "completed"):
            raise HelionyxError(ErrorCode.NO_FEASIBLE_CANDIDATE,
                                f"{self.name} finished with status '{str(raw.get('status'))[:40]}'.",
                                "Relax the constraints or widen the size bounds.", {"messages": raw.get("messages")})
        sizes = {k: float(raw["sizes"].get(k, 0.0) or 0.0)
                 for k in ("pv_kwp", "wind_count", "bess_kwh", "bess_kw", "genset_kw")}
        m = raw.get("metrics", {})
        metrics = {k: m.get(k) for k in ("npc", "lcoe_per_kwh", "initial_capital", "operating_cost_per_yr",
                                          "annual_bill", "renewable_fraction_pct", "capacity_shortage_pct",
                                          "excess_electricity_pct", "simple_payback_yr", "discounted_payback_yr",
                                          "irr_pct", "fuel_l_per_yr", "co2_kg_per_yr")}
        return {"candidate_index": 0, "rank": 1, "feasible": True, "violations": [], "sizes": sizes,
                "metrics": metrics, "cost_breakdown": {}, "cost_by_type": {},
                "solver": {"name": self.name, "version": raw.get("solver", {}).get("version"),
                           "label": raw.get("solver", {}).get("label", self.meta["label"]),
                           "licence": self.meta["licence"], "notes": raw.get("messages", [])}}

    def execute(self, run_id: str, scenario_doc: dict[str, Any], progress: Any) -> dict[str, Any]:
        progress(1.0, f"mapping scenario to {self.name}")
        raw = self.run(self.prepare(scenario_doc), progress)
        cand = self.normalise(raw, scenario_doc)
        self.app.db.put_candidates(run_id, [cand])
        run = self.app.db.get("runs", run_id, "run")
        run.update(status="completed", feasible_count=1, infeasible_count=0, base_case=None,
                   solver_detail=cand["solver"], finished_at=now_iso())
        self.app.db.put("runs", run_id, run, scenario_id=run["scenario_id"], status="completed")
        return {"run_id": run_id, "solver": self.name}


