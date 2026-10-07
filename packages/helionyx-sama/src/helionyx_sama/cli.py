"""``helionyx-sama run <input.json> <output.json>``: size a Helionyx scenario with SAMAPy.

SAMAPy reads its inputs from a module-level ``InData`` object that other modules
copy at import time, and it writes files to the current directory. So each case
runs in a fresh process, in a temporary working directory, and ``InData`` is
overwritten before the optimiser module is imported.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time
from importlib.metadata import version
from math import ceil
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA = "helionyx-adapter-io/1"
NT = 8760
# SAMA's objective is 1e-5 x NPC plus penalty terms whose weights assume USD-sized money.
# Money is divided by this scale on the way in and multiplied back on the way out; the
# sizing is unchanged by a uniform currency scale apart from that penalty balance.
DEFAULT_SCALE = 300.0


def _grab(log: str, label: str) -> float | None:
    m = re.search(r"^" + re.escape(label) + r"\s*=\s*\$?\s*([-\d.eE+]+)", log, re.M)
    return float(m.group(1)) if m else None


def _money(v: float | None, scale: float) -> float | None:
    return None if v is None else v * scale


def configure(d: Any, inp: dict[str, Any], k: float) -> dict[str, float]:
    ts, comps, eco, cons = inp["time_series"], inp["components"], inp["economics"], inp["constraints"]
    pv, wind, bess, conv, gen = (comps.get(k) for k in ("pv", "wind", "bess", "converter", "genset"))
    if wind is not None:
        raise ValueError("wind is not mapped to SAMA in this adapter version")
    load = np.asarray(ts["load_kw"], dtype=float)
    d.Eload_eh = load.copy()
    d.Eload = load.copy()
    d.Eload_Previous = load.copy()
    d.EloadPrevious = load.copy()
    d.Eload_hp = np.zeros(NT)
    n_i = float(conv["technical"]["efficiency"]) if conv else 0.96
    d.n_I = n_i
    d.fpv, d.Tcof = 1.0, 0.0
    d.G = np.asarray(ts["pv_kw_per_kwp"], dtype=float) * 1000.0 / n_i
    d.T = np.full(NT, 25.0)
    d.Vw = np.zeros(NT)
    grid = inp["grid_mode"] == "grid_connected"
    d.PV, d.WT, d.Bat, d.DG, d.Grid = int(pv is not None), 0, int(bess is not None), int(gen is not None), int(grid)
    d.Li_ion, d.Lead_acid, d.HP, d.EV, d.NEM, d.NG_Grid, d.EM = 1, 0, 0, 0, 0, 0, 0
    d.cap_option = 4
    d.Ppv_r, d.Pwt_r, d.Cdg_r = 1.0, 1.0, 1.0
    d.Cbt_r = d.Vnom_Li_ion * d.Cnom_Li / 1000.0
    n = int(eco["project_life_years"])
    d.n, d.n_ir, d.e_ir = n, float(eco["real_discount_rate"]), 0.0
    d.ir = d.n_ir
    d.RE_incentives = d.System_Tax = d.Engineering_Costs = 0.0
    d.Budget = 1e15
    if pv is not None:
        d.C_PV, d.R_PV, d.MO_PV, d.L_PV = pv["capital_per_unit"] * k, pv["replacement_per_unit"] * k, \
            pv["om_per_unit_year"] * k, pv["lifetime_years"]
    if conv is not None:
        d.C_I, d.R_I, d.MO_I, d.L_I = conv["capital_per_unit"] * k, conv["replacement_per_unit"] * k, \
            conv["om_per_unit_year"] * k, conv["lifetime_years"]
    if bess is not None:
        t = bess["technical"]
        d.C_B, d.R_B, d.MO_B, d.L_B = bess["capital_per_unit"] * k, bess["replacement_per_unit"] * k, \
            bess["om_per_unit_year"] * k, float(t["float_life_years"])
        d.ef_bat_Li = float(t["round_trip_efficiency"])
        d.SOC_min, d.SOC_max, d.SOC_initial = float(t["soc_min"]), 1.0, float(t["soc_initial"])
        d.Q_lifetime_Li = float(t["lifetime_throughput_kwh_per_kwh"]) * d.Cbt_r
    if gen is not None:
        t = gen["technical"]
        d.C_DG, d.R_DG = gen["capital_per_unit"] * k, gen["replacement_per_unit"] * k
        d.MO_DG = gen["om_per_unit_year"] * k / 8760.0
        d.a, d.b = float(t["fuel_curve_slope_l_per_kwh"]), float(t["fuel_curve_intercept_l_per_h_per_kw"])
        d.TL_DG, d.LR_DG = float(t["lifetime_hours"]), float(t["min_load_ratio"])
        d.C_fuel, d.C_fuel_adj = float(eco["fuel_price_per_l"]) * k, 0.0
    d.C_CH = d.R_CH = d.MO_CH = 0
    for c in ("PV", "WT", "B", "I", "CH", "HP", "EV"):
        life = getattr(d, f"L_{c}", 0) or 0
        setattr(d, f"RT_{c}", ceil(n / life) - 1 if life else 0)
    d.Grid_escalation = np.zeros(n)
    d.Grid_escalation_NG = np.zeros(n)
    d.Service_charge = np.zeros(12)
    d.Grid_credit = d.Annual_expenses = 0
    d.Grid_Tax = 0.0
    if grid:
        d.Cbuy = np.asarray(ts["grid_import_price_per_kwh"], dtype=float) * k
        d.Csell = np.asarray(ts["grid_export_price_per_kwh"] or [0.0] * NT, dtype=float) * k
        d.Pbuy_max, d.Psell_max = float(eco["grid_max_import_kw"]), float(eco["grid_max_export_kw"])
    d.LPSP_max = float(cons["max_capacity_shortage"])
    d.RE_min = float(cons.get("min_renewable_fraction") or 0.0)
    peak = float(load.max())
    pv_max = float(pv["max_size"]) if pv else 0.0
    if pv and cons.get("max_pv_kwp") is not None:
        pv_max = min(pv_max, float(cons["max_pv_kwp"]))
    d.VarMin = np.zeros(5)
    d.VarMax = np.array([pv_max, 0.0, (float(bess["max_size"]) / d.Cbt_r) if bess else 0.0,
                         float(gen["max_size"]) if gen else 0.0, max(peak * 1.5, pv_max)])
    opts = inp.get("options", {})
    d.MaxIt, d.nPop, d.Run_Time = int(opts.get("max_iterations", 150)), int(opts.get("population", 50)), 1
    return {"cbt_r": float(d.Cbt_r)}


def _resync(d: Any) -> None:
    """Push the configured inputs into every loaded samapy module.

    ``samapy.core`` imports ``Fitness`` together with ``Input_Data``, and Fitness copies the
    input values into its own globals at import time, before ``configure`` runs. Without this
    step the optimiser would size SAMA's built-in default case.
    """
    attrs = vars(d)
    for name, mod in list(sys.modules.items()):
        if not name.startswith("samapy.") or mod is None or name == "samapy.core.Input_Data":
            continue
        for g in list(vars(mod)):
            if g in attrs and not callable(attrs[g]):
                setattr(mod, g, attrs[g])


def _crf(i: float, n: int) -> float:
    return 1.0 / n if abs(i) < 1e-12 else i * (1 + i) ** n / ((1 + i) ** n - 1)


def solve(inp: dict[str, Any], workdir: Path) -> dict[str, Any]:
    os.chdir(workdir)
    os.environ["MPLBACKEND"] = "Agg"
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.savefig = lambda *a, **k: None  # charts are not needed; saves most of the reporting time
    np.random.seed(int(inp.get("seed", 42)))
    buf = io.StringIO()
    t0 = time.perf_counter()
    with contextlib.redirect_stdout(buf):
        from samapy.core.Input_Data import InData

        scale = float(inp.get("options", {}).get("currency_scale", DEFAULT_SCALE))
        configure(InData, inp, 1.0 / scale)
        _resync(InData)
        from samapy.optimizers.swarm import Swarm

        opt = Swarm()
        opt.optimize()
    log = buf.getvalue()
    lpsp = _grab(log, "LPSP Total ")
    re_pct = _grab(log, "RE ")
    cons = inp["constraints"]
    re_min = cons.get("min_renewable_fraction")
    feasible = (lpsp is None or lpsp / 100.0 <= float(cons["max_capacity_shortage"]) + 1e-6) and \
        (re_min is None or re_pct is None or re_pct / 100.0 >= float(re_min) - 1e-6)
    pv, bat, dg, inv = _grab(log, "Cpv  (kW)"), _grab(log, "Cbat (kWh)"), _grab(log, "Cdg  (kW)"), \
        _grab(log, "Cinverter (kW)")
    npc = _money(_grab(log, "NPC "), scale)
    eco = inp["economics"]
    served = float(np.sum(inp["time_series"]["load_kw"])) * (1.0 - (lpsp or 0.0) / 100.0)
    # SAMA prints LCOE rounded to 2 decimals; recompute it from NPC for full precision.
    lcoe = npc * _crf(float(eco["real_discount_rate"]), int(eco["project_life_years"])) / served \
        if npc is not None and served > 0 else None
    return {
        "schema": SCHEMA, "status": "feasible" if feasible else "infeasible",
        "solver": {"name": "sama", "version": version("samapy"),
                   "label": "particle swarm (SAMA), rule-based dispatch"},
        "sizes": {"pv_kwp": pv or 0.0, "wind_count": 0.0, "bess_kwh": bat or 0.0, "bess_kw": inv or 0.0,
                  "genset_kw": dg or 0.0},
        "metrics": {"npc": npc, "lcoe_per_kwh": lcoe,
                    "initial_capital": _money(_grab(log, "Initial Cost "), scale),
                    "operating_cost_per_yr": _money(_grab(log, "Operating Cost "), scale),
                    "renewable_fraction_pct": re_pct, "capacity_shortage_pct": lpsp,
                    "fuel_l_per_yr": _grab(log, "Annual fuel consumed by Generator ") or 0.0},
        "messages": [f"optimisation {time.perf_counter() - t0:.0f} s", "bess_kw reports SAMA's DC inverter size",
                     f"money passed to SAMA divided by {scale:g} and scaled back",
                     "LCOE = NPC x CRF / served energy (grid sales not included)"],
    }


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] != "run":
        print("usage: helionyx-sama run <input.json> <output.json>", file=sys.stderr)
        sys.exit(2)
    src, dst = Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
    inp = json.loads(src.read_text(encoding="utf-8"))
    if inp.get("schema") != SCHEMA:
        print(f"unsupported schema {inp.get('schema')!r}", file=sys.stderr)
        sys.exit(2)
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        try:
            out = solve(inp, Path(tmp))
        finally:
            os.chdir(cwd)
    dst.write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
