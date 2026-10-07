"""Regression: reference cases are reproducible and match stored goldens (FR-PRV-003, AT-08, §9.2)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from helionyx.services import run
from helionyx.services.study import create_from_study
from tests.conftest import ROOT, make_app

CASES = ["rc1_island_village", "rc2_hotel_negombo", "rc3_estate_village"]
GOLDEN = ROOT / "reference_cases" / "expected"
VOLATILE = {"run_id", "scenario_id", "job_id", "created_at", "finished_at", "wall_time_s", "timeseries_digest",
            "engine"}


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def results_document(app, path: Path) -> str:
    scn = create_from_study(app, path)
    job = run.start_run(app, scn["scenario_id"])
    assert app.jobs.wait(job["job_id"], 600)["state"] == "completed"
    run_doc = app.db.get("runs", job["run_id"], "run")
    doc = {"scenario_hash": scn["scenario_hash"], "candidate_count": scn["candidate_count"],
           "feasible_count": run_doc["feasible_count"], "base_case": run_doc["base_case"],
           "candidates": app.db.get_candidates(job["run_id"], order="index")}
    return json.dumps(_strip(doc), sort_keys=True)


@pytest.mark.parametrize("case", CASES)
def test_reference_case_is_reproducible(case, tmp_path):
    path = ROOT / "reference_cases" / f"{case}.yaml"
    app1 = make_app(tmp_path / "a")
    app2 = make_app(tmp_path / "b")
    try:
        first = results_document(app1, path)
        second = results_document(app2, path)
    finally:
        app1.close()
        app2.close()
    assert first == second  # byte-identical apart from IDs and timestamps
    summary = json.loads(first)
    top = min((c for c in summary["candidates"] if c["rank"] is not None), key=lambda c: c["rank"])
    golden = GOLDEN / f"{case}.json"
    record = {"scenario_hash": summary["scenario_hash"], "candidate_count": summary["candidate_count"],
              "feasible_count": summary["feasible_count"], "rank1_sizes": top["sizes"],
              "rank1_npc": round(top["metrics"]["npc"], 2)}
    if os.environ.get("HNX_UPDATE_GOLDEN") or not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    expected = json.loads(golden.read_text(encoding="utf-8"))
    assert record == expected
