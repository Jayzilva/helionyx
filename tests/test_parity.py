"""HOMER parity comparison tooling (PRD F15, SRS §9.4)."""

from __future__ import annotations

from helionyx.services import parity
from tests.conftest import ROOT


def test_parity_compare_flags_differences(shared_app, tmp_path):
    csv = tmp_path / "homer.csv"
    # Hypothetical HOMER numbers: NPC 5 % higher (inside ±10 %), LCOE 20 % higher (outside), RF 3 points lower.
    from helionyx.services import results, run
    from helionyx.services.study import create_from_study

    scn = create_from_study(shared_app, ROOT / "reference_cases" / "rc3_estate_village.yaml")
    j = run.start_run(shared_app, scn["scenario_id"])
    shared_app.jobs.wait(j["job_id"], 300)
    top = results.candidate_by_rank(shared_app, j["run_id"], 1)
    m, s = top["metrics"], top["sizes"]
    csv.write_text("case,pv_kwp,bess_kwh,genset_kw,npc,lcoe_per_kwh,renewable_fraction_pct\n"
                   f"RC-3,{s['pv_kwp']},{s['bess_kwh']},{s['genset_kw']},{m['npc'] * 1.05},"
                   f"{m['lcoe_per_kwh'] * 1.2},{m['renewable_fraction_pct'] - 3}\n", encoding="utf-8")
    rep = parity.compare(shared_app, csv, ROOT / "reference_cases")
    case = rep["cases"][0]
    assert case["architecture_match"]
    checks = {c["metric"]: c for c in case["checks"]}
    assert checks["npc"]["within_target"] is True
    assert checks["lcoe_per_kwh"]["within_target"] is False
    assert checks["renewable_fraction_pct"]["within_target"] is True
    md = parity.to_markdown(rep)
    assert "| npc |" in md and "**no**" in md
