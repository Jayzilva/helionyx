"""Exports: HOMER-importable series, Markdown reports, time series and scenario YAML (FR-EXP, FR-RPT-006)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jinja2
import numpy as np
import pandas as pd

from helionyx import DISCLAIMER
from helionyx.core.engine import dispatch as dsp
from helionyx.errors import ErrorCode, HelionyxError, validation
from helionyx.infra.db import now_iso
from helionyx.services.context import Helionyx
from helionyx.services.results import explain_run, get_monthly_summary, get_results
from helionyx.services.run import build_inputs, get_run
from helionyx.services.scenario import get_scenario, resolved_of, scenario_to_yaml

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=False, trim_blocks=True,
                          lstrip_blocks=True, undefined=jinja2.StrictUndefined)


def _fmt(v: Any, digits: int = 0) -> str:
    if v is None:
        return "—"
    if isinstance(v, float | int):
        return f"{v:,.{digits}f}"
    return str(v)


_env.filters["n0"] = lambda v: _fmt(v, 0)
_env.filters["n1"] = lambda v: _fmt(v, 1)
_env.filters["n2"] = lambda v: _fmt(v, 2)


def _out_dir(app: Helionyx, *parts: str) -> Path:
    d = app.settings.export_dir.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


def export_homer_csv(app: Helionyx, scenario_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    if (scenario_id is None) == (run_id is None):
        raise validation("Give exactly one of scenario_id or run_id.", "Use the scenario you want to cross-check.")
    if run_id:
        scenario_id = get_run(app, run_id)["scenario_id"]
    assert scenario_id is not None
    doc = get_scenario(app, scenario_id)
    s = resolved_of(doc)
    inp = build_inputs(app, s)
    from helionyx.services.resource import dataset_frame

    res = dataset_frame(app, s.resource.dataset_id)
    out = _out_dir(app, scenario_id, "homer")
    files = {
        "load_kw.txt": inp.load,
        "ghi_kw_m2.txt": res["ghi_w_m2"].to_numpy() / 1000.0,
        "temp_c.txt": res["temp_c"].to_numpy(),
    }
    wind_height = None
    if "wind50_m_s" in res and s.components.wind is not None \
            and abs(s.components.wind.technical["hub_height_m"] - 50) < abs(s.components.wind.technical["hub_height_m"]
                                                                            - 10):
        files["wind_m_s.txt"] = res["wind50_m_s"].to_numpy()
        wind_height = 50
    else:
        files["wind_m_s.txt"] = res["wind10_m_s"].to_numpy()
        wind_height = 10
    for name, arr in files.items():
        np.savetxt(out / name, np.asarray(arr, dtype=np.float64), fmt="%.4f")
    params = _env.get_template("homer_parameters.md.j2").render(
        s=s.model_dump(mode="json"), scenario_id=scenario_id, scenario_hash=doc["scenario_hash"],
        wind_height=wind_height, generated_at=now_iso(), disclaimer=DISCLAIMER, assumptions=doc["assumptions"])
    (out / "homer_parameters.md").write_text(params, encoding="utf-8")
    totals = {"load_kwh_per_yr": float(inp.load.sum()), "ghi_kwh_m2_per_yr": float(files["ghi_kw_m2.txt"].sum()),
              "mean_temp_c": float(np.mean(files["temp_c.txt"])),
              "mean_wind_m_s": float(np.mean(files["wind_m_s.txt"]))}
    return {"scenario_id": scenario_id, "directory": str(out),
            "files": [str(out / n) for n in [*files, "homer_parameters.md"]],
            "anemometer_height_m": wind_height, "annual_totals": totals,
            "notes": ["Each series file has 8,760 rows starting 1 January 00:00 local time.",
                      "Import the series in HOMER Pro and enter the values from homer_parameters.md by hand.",
                      "HOMER import of these files has not yet been verified against the current HOMER version "
                      "(SRS Appendix A, V6)."]}


def export_timeseries(app: Helionyx, run_id: str, rank: int = 1, fmt: str = "csv") -> dict[str, Any]:
    from helionyx.services.results import candidate_series

    c, series, _ = candidate_series(app, run_id, rank)
    df = pd.DataFrame(series, columns=list(dsp.SERIES_FIELDS))
    out = _out_dir(app, run_id)
    if fmt == "csv":
        path = out / f"rank{rank}_timeseries.csv"
        df.to_csv(path, index_label="hour")
    elif fmt == "parquet":
        path = out / f"rank{rank}_timeseries.parquet"
        df.to_parquet(path)
    else:
        raise validation("format must be csv or parquet.", "Use csv or parquet.")
    return {"run_id": run_id, "rank": rank, "path": str(path), "rows": len(df), "columns": list(df.columns)}


def render_report(app: Helionyx, run_id: str) -> str:
    run = get_run(app, run_id)
    scn = get_scenario(app, run["scenario_id"])
    results = get_results(app, run_id, top_n=5)
    explanation = explain_run(app, run_id, rank=1, compare_to="base" if run.get("base_case") else "next")
    monthly = get_monthly_summary(app, run_id, rank=1)
    batches = [app.db.get("batches", r["id"], "batch") for r in
               app.db.query("SELECT id FROM batches WHERE scenario_id = ? ORDER BY created_at", (run["scenario_id"],))]
    sensitivities = [b["result"] for b in batches if b.get("status") == "completed"]
    from helionyx.services.results import _uncertainties

    methodology_notes = (TEMPLATES / "differences_from_homer.md").read_text(encoding="utf-8")
    return _env.get_template("report.md.j2").render(
        run=run, scn=scn, s=scn["resolved"], results=results, ex=explanation, monthly=monthly,
        sensitivities=sensitivities, uncertainties=_uncertainties(app, scn), generated_at=now_iso(),
        disclaimer=DISCLAIMER, homer_differences=methodology_notes)


def export_report(app: Helionyx, run_id: str, fmt: str = "md") -> dict[str, Any]:
    if fmt == "xlsx":
        raise HelionyxError(ErrorCode.UNSUPPORTED_COMBINATION, "Excel reports arrive in v0.2.",
                            "Use format='md'; the Markdown report contains every section.")
    if fmt != "md":
        raise validation("format must be 'md'.", "Use format='md'.")
    text = render_report(app, run_id)
    path = _out_dir(app, run_id) / "report.md"
    path.write_text(text, encoding="utf-8")
    return {"run_id": run_id, "format": "md", "path": str(path), "resource_uri": f"hnx://runs/{run_id}/report.md",
            "bytes": len(text.encode("utf-8"))}


def export_scenario_yaml(app: Helionyx, scenario_id: str) -> str:
    return scenario_to_yaml(get_scenario(app, scenario_id))


def results_csv(app: Helionyx, run_id: str) -> str:
    get_run(app, run_id)
    rows = []
    for c in app.db.get_candidates(run_id, order="index"):
        row = {"candidate_index": c["candidate_index"], "rank": c.get("rank"), "feasible": c["feasible"],
               **c["sizes"], **c["metrics"],
               "violations": ";".join(v["constraint"] for v in c["violations"])}
        rows.append(row)
    return str(pd.DataFrame(rows).to_csv(index=False))
