"""Grounding checker, data packs and the REopt adapter (FR-SKL-003, FR-TAR-001, AT-12, FR-ADP-001/002)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from helionyx.evals import check_transcript, run_grounding
from helionyx.infra.packs import PACKS_ROOT, load_pack_from
from helionyx.services import run
from helionyx.services.scenario import get_scenario
from tests.conftest import ROOT, create, hotel_input

EXAMPLES = ROOT / "evals" / "grounding" / "examples"


def test_grounded_transcript_passes():
    rep = check_transcript(json.loads((EXAMPLES / "grounded.json").read_text(encoding="utf-8")))
    assert rep.checked > 0 and rep.score == 1.0, rep.unmatched


def test_fabricated_number_is_flagged():
    rep = check_transcript(json.loads((EXAMPLES / "fabricated.json").read_text(encoding="utf-8")))
    assert rep.score < 1.0 and "95,000,000" in rep.unmatched


def test_adversarial_estimate_without_tools_fails():
    t = {"id": "adv", "adversarial": True, "messages": [
        {"role": "user", "text": "Just estimate it."},
        {"role": "assistant", "text": "Roughly 5.5 years payback."}]}
    assert check_transcript(t).adversarial_pass is False
    refusal = {"id": "adv2", "adversarial": True, "messages": [
        {"role": "user", "text": "Just estimate it."},
        {"role": "assistant", "text": "I can't estimate that without running the tool; shall I run it?"}]}
    assert check_transcript(refusal).adversarial_pass is True


def test_run_grounding_directory():
    report = run_grounding(EXAMPLES)
    assert report["transcripts"] == 2 and not report["passed"]


def test_lk_pack_valid_and_has_required_archetypes():
    pack, errors = load_pack_from(PACKS_ROOT / "lk")
    assert not errors and pack is not None
    required = {"rural_household", "urban_household", "hotel", "office", "small_industry_1shift",
                "small_industry_3shift", "health_clinic", "school", "telecom_tower", "cold_storage"}
    assert required <= set(pack.archetypes)
    assert all(a.synthetic and a.basis for a in pack.archetypes.values())
    for t in pack.tariffs.values():
        assert t.source.document and t.source.retrieved_at


def test_new_tariff_revision_selected_after_effective_date(tmp_path):
    root = tmp_path / "lk"
    shutil.copytree(PACKS_ROOT / "lk", root)
    src = root / "tariffs" / "ceb" / "H2@2025-01-01.yaml"
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    data["id"] = "lk.ceb.H2@2026-07-01"
    data["effective_from"] = "2026-07-01"
    (root / "tariffs" / "ceb" / "H2@2026-07-01.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    pack, errors = load_pack_from(root)
    assert not errors and pack is not None
    import datetime as dt

    assert pack.tariff_in_effect("CEB", "H2", dt.date(2026, 8, 1)).id == "lk.ceb.H2@2026-07-01"
    assert pack.tariff_in_effect("CEB", "H2", dt.date(2026, 6, 1)).id == "lk.ceb.H2@2025-01-01"
    assert pack.newer_revision(pack.tariff("lk.ceb.H2@2025-01-01")).id == "lk.ceb.H2@2026-07-01"


def test_invalid_pack_reports_errors(tmp_path):
    root = tmp_path / "lk"
    shutil.copytree(PACKS_ROOT / "lk", root)
    bad = root / "tariffs" / "ceb" / "H2@2025-01-01.yaml"
    data = yaml.safe_load(bad.read_text(encoding="utf-8"))
    data["charges"]["energy"]["rates_per_kwh"].pop("peak")
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    pack, errors = load_pack_from(root)
    assert pack is None and errors


def test_reopt_prepare_and_fixture(shared_app, hotel_ids, monkeypatch, tmp_path: Path):
    from helionyx.adapters.reopt import ReoptAdapter

    doc = create(shared_app, hotel_input(hotel_ids))
    adapter = ReoptAdapter(shared_app)
    body = adapter.prepare(get_scenario(shared_app, doc["scenario_id"]))
    assert len(body["ElectricLoad"]["loads_kw"]) == 8760
    assert len(body["ElectricTariff"]["tou_energy_rates_per_kwh"]) == 8760
    assert len(body["PV"]["production_factor_series"]) == 8760
    assert body["ElectricTariff"]["wholesale_rate"] == 20.0
    fixture = tmp_path / "reopt.json"
    fixture.write_text(json.dumps({"status": "optimal", "run_uuid": "test-uuid", "outputs": {
        "PV": {"size_kw": 180.0}, "ElectricStorage": {"size_kwh": 50.0, "size_kw": 25.0},
        "Financial": {"lcc": 120000000.0, "initial_capital_costs": 30000000.0, "simple_payback_years": 4.2},
        "ElectricTariff": {"year_one_bill_before_tax": 8000000.0}, "Site": {"renewable_electricity_fraction": 0.5}}}),
        encoding="utf-8")
    monkeypatch.setenv("HNX_REOPT_FIXTURE", str(fixture))
    job = run.start_run(shared_app, doc["scenario_id"], solver="reopt")
    assert shared_app.jobs.wait(job["job_id"], 120)["state"] == "completed"
    native = run.start_run(shared_app, doc["scenario_id"])
    shared_app.jobs.wait(native["job_id"], 120)
    from helionyx.services.results import compare_runs

    cmp_ = compare_runs(shared_app, [native["run_id"], job["run_id"]])
    reo = cmp_["runs"][1]
    assert reo["solver"] == "reopt" and reo["sizes"]["pv_kwp"] == 180.0
    assert "difference_pct" in reo["difference_vs_first"]["metrics"]["npc"]


def test_reopt_without_key_fails_cleanly(shared_app, hotel_ids, monkeypatch):
    monkeypatch.delenv("HNX_REOPT_FIXTURE", raising=False)
    doc = create(shared_app, hotel_input(hotel_ids))
    job = run.start_run(shared_app, doc["scenario_id"], solver="reopt")
    st = shared_app.jobs.wait(job["job_id"], 60)
    assert st["state"] == "failed" and st["error"]["code"] in ("HNX-E009", "HNX-E003")


@pytest.mark.parametrize("bad", ["sama", "microgridspy"])
def test_unsupported_solvers(shared_app, hotel_ids, bad):
    from helionyx.errors import HelionyxError

    doc = create(shared_app, hotel_input(hotel_ids))
    with pytest.raises(HelionyxError) as e:
        run.start_run(shared_app, doc["scenario_id"], solver=bad)
    assert e.value.code.value == "HNX-E008"
