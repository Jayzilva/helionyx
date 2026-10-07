"""``helionyx-microgridspy run <input.json> <output.json>``: solve a Helionyx scenario with MicroGridsPy."""

from __future__ import annotations

import contextlib
import io
import json
import math
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

SCHEMA = "helionyx-adapter-io/1"
NAME = "helionyx_case"


def crf(i: float, n: int) -> float:
    return 1.0 / n if abs(i) < 1e-12 else i * (1 + i) ** n / ((1 + i) ** n - 1)


def write_series(path: Path, columns: list[str], data: list[list[float]]) -> None:
    """Write a MicroGridsPy typical-year CSV: scenario/year header rows, optional label row, 8760 rows."""
    n = len(data)
    rows: list[list[Any]] = [["meta", *["scenario_1"] * n], ["hour", *["typical_year"] * n]]
    if columns:
        rows.append(["", *columns])
    for h in range(len(data[0])):
        rows.append([h, *[float(col[h]) for col in data]])
    pd.DataFrame(rows).to_csv(path, index=False, header=False)


def edit_yaml(path: Path, fn: Any) -> None:
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(d)
    path.write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")


def om_share(c: dict[str, Any]) -> float:
    return c["om_per_unit_year"] / c["capital_per_unit"] if c["capital_per_unit"] > 0 else 0.0


def solve(inp: dict[str, Any], workdir: Path) -> dict[str, Any]:
    import microgridspy as mgp

    ts, comps, eco, cons = inp["time_series"], inp["components"], inp["economics"], inp["constraints"]
    pv, wind, bess, conv, gen = (comps.get(k) for k in ("pv", "wind", "bess", "converter", "genset"))
    if pv is None and wind is None:
        raise ValueError("MicroGridsPy needs at least one renewable (PV or wind) in this adapter version")
    wacc = float(eco["real_discount_rate"])
    on_grid = inp["grid_mode"] == "grid_connected"
    export = on_grid and bool(ts.get("grid_export_price_per_kwh")) and max(ts["grid_export_price_per_kwh"]) > 0
    resources, conversions, cfs = [], [], []
    if pv is not None:
        resources.append("Solar")
        conversions.append("PV")
        cfs.append(np.clip(np.asarray(ts["pv_kw_per_kwp"], dtype=float), 0.0, 1.0))
    if wind is not None:
        rated = float(wind["technical"]["rated_kw"])
        resources.append("Wind")
        conversions.append("WT")
        cfs.append(np.clip(np.asarray(ts["wind_kw_per_turbine"], dtype=float) / rated, 0.0, 1.0))

    mgp.set_workspace(str(workdir))
    paths = mgp.create_project(NAME, formulation="typical_year", system_type="on_grid" if on_grid else "off_grid",
                               allow_export=export, resources=resources, conversions=conversions,
                               csv_delimiter=",", overwrite=True)
    d = Path(paths.inputs_dir)
    write_series(d / "load_demand.csv", [], [ts["load_kw"]])
    write_series(d / "resource_availability.csv", resources, cfs)
    if on_grid:
        write_series(d / "grid_import_price.csv", [], [ts["grid_import_price_per_kwh"]])
        if export:
            write_series(d / "grid_export_price.csv", [], [ts["grid_export_price_per_kwh"]])

    def ren(y: dict[str, Any]) -> None:
        for r in y["renewables"]:
            c = pv if r["conversion_technology"] == "PV" else wind
            assert c is not None
            unit = 1.0 if r["conversion_technology"] == "PV" else float(c["technical"]["rated_kw"])
            per_kw = c["capital_per_unit"] / unit
            r["investment"]["by_step"]["base"].update(
                nominal_capacity_kw=1.0, specific_investment_cost_per_kw=per_kw, wacc=wacc,
                lifetime_years=int(round(c["lifetime_years"])), fixed_om_share_per_year=om_share(c),
                inverter_specific_investment_cost_per_kw_ac=0.0, inverter_fixed_om_share_per_year=0.0)
            cap = c.get("max_size")
            if r["conversion_technology"] == "PV" and cons.get("max_pv_kwp") is not None:
                cap = min(cap, cons["max_pv_kwp"]) if cap is not None else cons["max_pv_kwp"]
            r["technical"].update(dc_ac_ratio=1.0, inverter_efficiency=1.0,
                                  max_installable_capacity_kw=None if cap is None else float(cap) * unit)

    edit_yaml(d / "renewables.yaml", ren)

    def bat(y: dict[str, Any]) -> None:
        b = y["battery"]
        if bess is None:
            b["technical"]["max_installable_capacity_kwh"] = 0.0
            return
        t = bess["technical"]
        eff = math.sqrt(float(t["round_trip_efficiency"])) * (float(conv["technical"]["efficiency"]) if conv else 1.0)
        b["investment"]["by_step"]["base"].update(
            specific_investment_cost_per_kwh=bess["capital_per_unit"], wacc=wacc,
            calendar_lifetime_years=int(round(float(t["float_life_years"]))), fixed_om_share_per_year=om_share(bess),
            inverter_specific_investment_cost_per_kw=conv["capital_per_unit"] if conv else 0.0,
            inverter_lifetime_years=int(round(conv["lifetime_years"])) if conv else 15)
        b["technical"].update(charge_efficiency=eff, discharge_efficiency=eff, initial_soc=float(t["soc_initial"]),
                              depth_of_discharge=1.0 - float(t["soc_min"]),
                              max_charge_c_rate=float(bess["power_kw_per_kwh"]),
                              max_discharge_c_rate=float(bess["power_kw_per_kwh"]),
                              max_installable_capacity_kwh=float(bess["max_size"]))

    edit_yaml(d / "battery.yaml", bat)

    def generator(y: dict[str, Any]) -> None:
        g = y["generator"]
        if gen is None:
            g["technical"]["max_installable_capacity_kw"] = 0.0
            return
        g["investment"]["by_step"]["base"].update(
            specific_investment_cost_per_kw=gen["capital_per_unit"], wacc=wacc,
            lifetime_years=int(round(gen["lifetime_years"])), fixed_om_share_per_year=om_share(gen))
        g["technical"].update(nominal_efficiency_full_load=float(gen["full_load_efficiency"]),
                              efficiency_curve_csv=None, max_installable_capacity_kw=float(gen["max_size"]))
        y["fuel"]["by_scenario"]["scenario_1"].update(
            lhv_kwh_per_unit_fuel=float(gen["fuel_lhv_kwh_per_l"]),
            fuel_cost_per_unit_fuel=float(eco["fuel_price_per_l"]))

    edit_yaml(d / "generator.yaml", generator)
    if on_grid:
        def grid(y: dict[str, Any]) -> None:
            y["grid"]["by_scenario"]["scenario_1"]["line"].update(capacity_kw=float(eco["grid_max_import_kw"]))
        edit_yaml(d / "grid.yaml", grid)
    fj_path = Path(paths.formulation_json)
    fj = json.loads(fj_path.read_text(encoding="utf-8"))
    fj["optimization_constraints"].update(
        enforcement="scenario_wise", max_lost_load_fraction=float(cons["max_capacity_shortage"]),
        lost_load_cost_per_kwh=0.0, min_renewable_penetration=float(cons.get("min_renewable_fraction") or 0.0))
    fj["integer_sizing"] = False
    fj_path.write_text(json.dumps(fj, indent=2), encoding="utf-8")

    mgp.validate_project(NAME)
    model = mgp.TypicalYearModel(NAME)
    t0 = time.perf_counter()
    out = model.solve_single_objective(solver="highs",
                                       solver_params={"time_limit": float(inp["options"].get("time_limit_s", 900))})
    status = str(out.attrs.get("status"))
    res = model.results()
    k = res.kpis[res.kpis.scenario == "expected"].iloc[0]
    ds = res.design_summary
    row = ds.iloc[0] if hasattr(ds, "iloc") and len(ds.shape) == 2 else ds

    def pick(prefix: str, contains: str = "") -> float:
        for col, val in row.items():
            if str(col).startswith(prefix) and contains.lower() in str(col).lower():
                return float(val)
        return 0.0

    pv_kw = pick("res_installed_kw", "PV") or pick("res_installed_kw", "Solar")
    wt_kw = pick("res_installed_kw", "WT") or pick("res_installed_kw", "Wind")
    bess_kwh, bess_kw, gen_kw = pick("battery_installed_kwh"), pick("battery_inverter_power_kw"), \
        pick("generator_installed_kw")
    n = int(eco["project_life_years"])
    annual = float(k["reported_total_annual_cost"])
    served = float(k["served_energy_kwh"])
    rp = float(k["renewable_penetration"])
    capital = (pv_kw * (pv["capital_per_unit"] if pv else 0.0)
               + ((wt_kw / float(wind["technical"]["rated_kw"])) * wind["capital_per_unit"] if wind else 0.0)
               + bess_kwh * (bess["capital_per_unit"] if bess else 0.0)
               + bess_kw * (conv["capital_per_unit"] if conv else 0.0)
               + gen_kw * (gen["capital_per_unit"] if gen else 0.0))
    return {
        "schema": SCHEMA, "status": "optimal" if "optimal" in status else status,
        "solver": {"name": "microgridspy", "version": version("microgridspy"),
                   "label": "LP (linopy + HiGHS), perfect foresight, annualised costs"},
        "sizes": {"pv_kwp": pv_kw, "wind_count": wt_kw / float(wind["technical"]["rated_kw"]) if wind else 0.0,
                  "bess_kwh": bess_kwh, "bess_kw": bess_kw, "genset_kw": gen_kw},
        "metrics": {"npc": annual / crf(float(eco["real_discount_rate"]), n),
                    "annualised_cost_per_yr": annual,
                    "lcoe_per_kwh": annual / served if served > 0 else None,
                    "initial_capital": capital,
                    "renewable_fraction_pct": 100.0 * rp if rp <= 1.0 else rp,
                    "capacity_shortage_pct": 100.0 * float(k["lost_load_fraction"]),
                    "fuel_l_per_yr": float(k.get("fuel_consumption", 0.0) or 0.0)},
        "messages": [f"solve time {time.perf_counter() - t0:.1f} s",
                     "NPC = total annual cost / CRF(real rate, project life); no salvage or distinct replacement "
                     "costs in MicroGridsPy typical-year mode"],
    }


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] != "run":
        print("usage: helionyx-microgridspy run <input.json> <output.json>", file=sys.stderr)
        sys.exit(2)
    inp = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if inp.get("schema") != SCHEMA:
        print(f"unsupported schema {inp.get('schema')!r}", file=sys.stderr)
        sys.exit(2)
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
        out = solve(inp, Path(tmp))
    Path(sys.argv[3]).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
