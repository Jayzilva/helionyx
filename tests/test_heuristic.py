"""Heuristic optimiser (FR-OPT-005): finds the enumerated optimum or a design within 1 % of its NPC."""

from __future__ import annotations

import pytest

from helionyx.errors import HelionyxError
from helionyx.services import results, run
from helionyx.services.study import create_from_study
from tests.conftest import ROOT, create, hotel_input


@pytest.mark.parametrize("case, budget", [("rc1_island_village", 700), ("rc3_estate_village", 400)])
def test_heuristic_matches_enumeration(shared_app, case, budget):
    scn = create_from_study(shared_app, ROOT / "reference_cases" / f"{case}.yaml")
    jobs = {s: run.start_run(shared_app, scn["scenario_id"], solver=s, max_evaluations=budget)
            for s in ("native", "heuristic")}
    for j in jobs.values():
        assert shared_app.jobs.wait(j["job_id"], 600)["state"] == "completed"
    enum = results.candidate_by_rank(shared_app, jobs["native"]["run_id"], 1)
    heur = results.candidate_by_rank(shared_app, jobs["heuristic"]["run_id"], 1)
    assert heur["metrics"]["npc"] <= enum["metrics"]["npc"] * 1.01
    res = results.get_results(shared_app, jobs["heuristic"]["run_id"])
    assert res["search"]["method"].startswith("heuristic") and res["search"]["evaluations"] <= budget
    assert res["search"]["evaluations"] < scn["candidate_count"]


def test_large_space_needs_heuristic(shared_app, hotel_ids):
    big = hotel_input(hotel_ids, components={"pv": {"sizes_kwp": {"min": 0, "max": 300, "step": 1}},
                                             "bess": {"sizes_kwh": {"min": 0, "max": 400, "step": 2}}})
    doc = create(shared_app, big)
    assert doc["candidate_count"] == 301 * 201 and not doc["errors"]
    assert any("heuristic" in (w.get("hint") or "") for w in doc["warnings"])
    with pytest.raises(HelionyxError) as e:
        run.start_run(shared_app, doc["scenario_id"])
    assert e.value.code.value == "HNX-E004"
    j = run.start_run(shared_app, doc["scenario_id"], solver="heuristic", max_evaluations=300)
    assert shared_app.jobs.wait(j["job_id"], 600)["state"] == "completed"


def test_pareto_front_is_non_dominated(shared_app, village_ids):
    import itertools

    from tests.conftest import offgrid_input

    doc = create(shared_app, offgrid_input(village_ids))
    j = run.start_run(shared_app, doc["scenario_id"])
    shared_app.jobs.wait(j["job_id"], 300)
    front = results.get_pareto(shared_app, j["run_id"], max_points=50)
    keys = results.PARETO_OBJECTIVES
    pts = [[p["metrics"][k] for k in keys] for p in front["points"]]
    for a, b in itertools.permutations(pts, 2):
        assert not (all(x <= y for x, y in zip(a, b, strict=True)) and any(x < y for x, y in zip(a, b, strict=True)))
    allc = shared_app.db.get_candidates(j["run_id"], order="index")
    cheapest = min(c["metrics"]["npc"] for c in allc)
    assert pts[0][0] == cheapest  # the cheapest design is always on the front
