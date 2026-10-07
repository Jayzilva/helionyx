"""HOMER Pro parity comparison (PRD F15, SRS §9.4).

Reads HOMER Pro results for the reference cases from a CSV filled in by hand,
runs the same reference cases with the native engine, and compares them against
the PRD targets: NPC and LCOE within ±10 %, renewable fraction within ±5 points.
The targets are a hypothesis to test, not a release gate (PRD §9).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from helionyx import DISCLAIMER
from helionyx.errors import validation
from helionyx.services import results, run
from helionyx.services.context import Helionyx
from helionyx.services.study import create_from_study

CASES = {"RC-1": "rc1_island_village.yaml", "RC-2": "rc2_hotel_negombo.yaml", "RC-3": "rc3_estate_village.yaml"}
METRICS = {  # HOMER column -> (Helionyx metric, kind, tolerance)
    "npc": ("npc", "relative", 0.10),
    "lcoe_per_kwh": ("lcoe_per_kwh", "relative", 0.10),
    "renewable_fraction_pct": ("renewable_fraction_pct", "points", 5.0),
    "fuel_l_per_yr": ("fuel_l_per_yr", "report", None),
    "excess_electricity_pct": ("excess_electricity_pct", "report", None),
    "capacity_shortage_pct": ("capacity_shortage_pct", "report", None),
}
SIZES = ("pv_kwp", "wind_count", "bess_kwh", "genset_kw")


def _num(v: str | None) -> float | None:
    if v is None or not str(v).strip():
        return None
    return float(str(v).replace(",", ""))


def read_homer_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows or "case" not in rows[0]:
        raise validation("The HOMER results CSV needs a 'case' column.",
                         "Start from reference_cases/homer_parity/homer_results_template.csv.")
    for r in rows:
        if r["case"] not in CASES:
            raise validation(f"Unknown case '{r['case'][:20]}'.", f"Use one of {', '.join(CASES)}.")
    return rows


def compare(app: Helionyx, homer_csv: Path, cases_dir: Path) -> dict[str, Any]:
    out = []
    for row in read_homer_csv(homer_csv):
        scn = create_from_study(app, cases_dir / CASES[row["case"]])
        job = run.start_run(app, scn["scenario_id"])
        state = app.jobs.wait(job["job_id"], 1800)["state"]
        if state != "completed":
            out.append({"case": row["case"], "error": f"Helionyx run {state}"})
            continue
        top = results.candidate_by_rank(app, job["run_id"], 1)
        checks = []
        for col, (key, kind, tol) in METRICS.items():
            h, x = _num(row.get(col)), top["metrics"].get(key)
            if h is None or x is None:
                continue
            if kind == "relative":
                diff = (x - h) / abs(h) if h else None
                ok = None if diff is None else abs(diff) <= tol
                checks.append({"metric": col, "homer": h, "helionyx": x, "difference_pct": 100 * diff
                               if diff is not None else None, "target": f"±{tol:.0%}", "within_target": ok})
            elif kind == "points":
                checks.append({"metric": col, "homer": h, "helionyx": x, "difference_points": x - h,
                               "target": f"±{tol:g} points", "within_target": abs(x - h) <= tol})
            else:
                checks.append({"metric": col, "homer": h, "helionyx": x, "difference": x - h, "target": "report only",
                               "within_target": None})
        sizes = {k: {"homer": _num(row.get(k)), "helionyx": top["sizes"][k]} for k in SIZES}
        out.append({"case": row["case"], "run_id": job["run_id"], "scenario_hash": scn["scenario_hash"],
                    "architecture_match": all((v["homer"] or 0) == v["helionyx"] for v in sizes.values()
                                              if v["homer"] is not None),
                    "sizes": sizes, "checks": checks})
    return {"cases": out, "disclaimer": DISCLAIMER,
            "note": "Explain every difference beyond the targets with the documented differences (methodology §11)."}


def to_markdown(rep: dict[str, Any]) -> str:
    lines = ["# HOMER Pro parity comparison", "", rep["note"], ""]
    for c in rep["cases"]:
        lines += [f"## {c['case']}", ""]
        if "error" in c:
            lines += [f"Error: {c['error']}", ""]
            continue
        lines += [f"Helionyx run `{c['run_id']}`; same optimal architecture: {c['architecture_match']}.", "",
                  "| Size | HOMER | Helionyx |", "|---|---|---|"]
        lines += [f"| {k} | {v['homer'] if v['homer'] is not None else '—'} | {v['helionyx']:g} |"
                  for k, v in c["sizes"].items()]
        lines += ["", "| Metric | HOMER | Helionyx | Difference | Target | Within target |",
                  "|---|---|---|---|---|---|"]
        for ch in c["checks"]:
            if ch.get("difference_pct") is not None:
                dtxt = f"{ch['difference_pct']:+.1f} %"
            elif "difference_points" in ch:
                dtxt = f"{ch['difference_points']:+.1f} pts"
            else:
                dtxt = f"{ch.get('difference', 0):+,.1f}"
            ok = {True: "yes", False: "**no**", None: "—"}[ch["within_target"]]
            lines.append(f"| {ch['metric']} | {ch['homer']:,.2f} | {ch['helionyx']:,.2f} | {dtxt} | "
                         f"{ch['target']} | {ok} |")
        lines.append("")
    lines += [f"> {rep['disclaimer']}", ""]
    return "\n".join(lines)
