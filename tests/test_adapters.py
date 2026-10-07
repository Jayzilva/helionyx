"""Out-of-process adapters: JSON protocol, missing-package hint, licence isolation (FR-ADP-001/003/004)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from helionyx.adapters.subprocess_adapter import SCHEMA, build_input
from helionyx.errors import HelionyxError
from helionyx.services import results, run
from helionyx.services.scenario import get_scenario
from tests.conftest import ROOT, create, hotel_input

FAKE = Path(__file__).parent / "fixtures" / "fake_adapter.py"


def test_build_input_schema(shared_app, hotel_ids):
    doc = create(shared_app, hotel_input(hotel_ids))
    inp = build_input(shared_app, get_scenario(shared_app, doc["scenario_id"]))
    assert inp["schema"] == SCHEMA
    ts = inp["time_series"]
    assert all(len(ts[k]) == 8760 for k in ("load_kw", "pv_kw_per_kwp", "grid_import_price_per_kwh"))
    assert inp["components"]["pv"]["max_size"] == 200 and inp["components"]["bess"]["power_kw_per_kwh"] == 0.5


def test_subprocess_round_trip(shared_app, hotel_ids, monkeypatch):
    monkeypatch.setenv("HNX_MICROGRIDSPY_CMD", f'"{sys.executable}" "{FAKE}"')
    doc = create(shared_app, hotel_input(hotel_ids))
    j = run.start_run(shared_app, doc["scenario_id"], solver="microgridspy")
    assert shared_app.jobs.wait(j["job_id"], 120)["state"] == "completed"
    top = results.candidate_by_rank(shared_app, j["run_id"], 1)
    assert top["sizes"]["pv_kwp"] == 42.0 and top["metrics"]["npc"] == 1234.5
    assert top["solver"]["licence"] == "EUPL-1.2"


def test_missing_adapter_gives_install_hint(shared_app, hotel_ids, monkeypatch):
    monkeypatch.setenv("HNX_SAMA_CMD", "helionyx-sama-not-installed")
    doc = create(shared_app, hotel_input(hotel_ids))
    with pytest.raises(HelionyxError) as e:
        run.start_run(shared_app, doc["scenario_id"], solver="sama")
    assert e.value.code.value == "HNX-E003" and "helionyx-sama" in e.value.hint


def test_core_never_imports_copyleft_solvers():
    src = (ROOT / "src" / "helionyx").rglob("*.py")
    for f in src:
        text = f.read_text(encoding="utf-8")
        assert "import samapy" not in text and "from samapy" not in text, f
        assert "import microgridspy" not in text and "from microgridspy" not in text, f
