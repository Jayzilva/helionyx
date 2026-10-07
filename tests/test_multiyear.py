"""Multi-year analysis: load growth (FR-LOAD-009) and capacity expansion (FR-OPT-007)."""

from __future__ import annotations

import pytest

from helionyx.core import economics as eco
from helionyx.errors import HelionyxError
from helionyx.services import multiyear, results, run, scenario
from tests.conftest import create, hotel_input, offgrid_input
from tests.test_scenario_runs import run_to_end


def _ranked(app, run_id):
    return [results.candidate_by_rank(app, run_id, r) for r in range(1, 4)]


def test_sample_years_cover_stage_boundaries():
    assert multiyear.segments(20, [8]) == [(1, 7), (8, 20)]
    assert multiyear.sample_years(20, [8], 5) == [1, 6, 7, 8, 13, 18, 20]
    assert multiyear.sample_years(25, [], 5) == [1, 6, 11, 16, 21, 25]


def test_evaluate_years_matches_single_year_economics():
    items = [eco.CostItem("pv", capital=1000, replacement_cost=800, lifetime_years=7.3, om_per_year=10),
             eco.CostItem("genset", capital=500, replacement_cost=500, lifetime_years=4.4, fuel_per_year=300,
                          escalation_fuel=0.02),
             eco.CostItem("grid", grid_purchases_per_year=200, grid_sales_per_year=50, escalation_grid=0.03)]
    a, b = eco.evaluate(items, 0.06, 25), eco.evaluate_years(items, 0.06, 25)
    assert b.npc == pytest.approx(a.npc, rel=1e-12)
    assert b.cash_flows == pytest.approx(a.cash_flows, abs=1e-9)


def test_staged_capital_is_discounted_from_its_install_year():
    item = eco.CostItem("pv_y10", capital=1000, om_per_year=10, install_year=9)
    r = eco.evaluate_years([item], 0.05, 20)
    assert r.breakdown["pv_y10"]["capital"] == pytest.approx(1000 / 1.05 ** 9)
    assert r.breakdown["pv_y10"]["om"] == pytest.approx(10 * eco.pv_annuity(0.05, 11) / 1.05 ** 9)
    assert r.initial_capital == 0.0
    assert r.cash_flows[9] == pytest.approx(1000)


def test_load_growth_scales_each_year(shared_app, hotel_ids):
    inp = hotel_input(hotel_ids, components={"pv": {"sizes_kwp": [100]}, "bess": {"sizes_kwh": [0]}},
                      multi_year={"load_growth_rate": 0.03, "sample_every_years": 4})
    rid = run_to_end(shared_app, create(shared_app, inp)["scenario_id"])
    c = results.candidate_by_rank(shared_app, rid, 1)
    years = c["multi_year"]["years"]
    y1 = years[0]["load_kwh"]
    for p in years:  # FR-LOAD-009 acceptance: E_n = E_1 × (1 + g)^(n − 1)
        assert p["load_kwh"] == pytest.approx(y1 * 1.03 ** (p["year"] - 1), rel=1e-9)
    last = years[-1]
    assert c["metrics"]["final_year_load_kwh_per_yr"] == pytest.approx(y1 * 1.03 ** (last["year"] - 1), rel=1e-9)


def test_zero_growth_matches_single_year_run(shared_app, hotel_ids):
    comps = {"pv": {"sizes_kwp": [0, 100]}, "bess": {"sizes_kwh": [0, 100]}}
    single = run_to_end(shared_app, create(shared_app, hotel_input(hotel_ids, components=comps))["scenario_id"])
    multi = run_to_end(shared_app, create(shared_app, hotel_input(
        hotel_ids, components=comps, multi_year={"load_growth_rate": 0.0}))["scenario_id"])
    for a, b in zip(_ranked(shared_app, single), _ranked(shared_app, multi), strict=True):
        assert b["sizes"]["pv_kwp"] == a["sizes"]["pv_kwp"] and b["sizes"]["bess_kwh"] == a["sizes"]["bess_kwh"]
        assert b["metrics"]["npc"] == pytest.approx(a["metrics"]["npc"], rel=1e-9)
        assert b["metrics"]["lcoe_per_kwh"] == pytest.approx(a["metrics"]["lcoe_per_kwh"], rel=1e-9)


@pytest.fixture(scope="module")
def expansion_run(shared_app, village_ids):
    inp = offgrid_input(village_ids, components={"pv": {"sizes_kwp": [50, 100]}, "bess": {"sizes_kwh": [150, 300]},
                                                 "genset": {"sizes_kw": [60, 80]}},
                        multi_year={"load_growth_rate": 0.03,
                                    "expansion": [{"year": 10, "pv_add_kwp": [0, 50], "bess_add_kwh": [0, 150]}]})
    doc = create(shared_app, inp)
    return doc, run_to_end(shared_app, doc["scenario_id"])


def test_expansion_multiplies_the_search_space(shared_app, expansion_run):
    doc, rid = expansion_run
    assert doc["candidate_count"] == 2 * 2 * 2 * 2 * 2
    c = results.candidate_by_rank(shared_app, rid, 1)
    assert {"pv_kwp_add_y10", "bess_kwh_add_y10", "bess_kw_add_y10"} <= set(c["sizes"])
    assert 10 in c["multi_year"]["sample_years"] and 9 in c["multi_year"]["sample_years"]


def test_reliability_holds_in_every_year(shared_app, expansion_run):
    _, rid = expansion_run
    for c in _ranked(shared_app, rid):
        assert all(p["capacity_shortage_pct"] <= 1.0 + 1e-9 for p in c["multi_year"]["years"])
        assert c["metrics"]["worst_year_capacity_shortage_pct"] <= 1.0 + 1e-9


def test_expansion_capital_is_reported_separately(shared_app, expansion_run):
    doc, rid = expansion_run
    s = scenario.resolved_of(scenario.get_scenario(shared_app, doc["scenario_id"]))
    pv = s.components.pv
    for c in _ranked(shared_app, rid):
        add = c["sizes"]["pv_kwp_add_y10"]
        if add > 0:
            i = s.economics.real_discount_rate
            assert c["cost_breakdown"]["pv_y10"]["capital"] == pytest.approx(pv.capital_per_unit * add / (1 + i) ** 9)
            assert c["metrics"]["expansion_capital"] >= pv.capital_per_unit * add


def test_heuristic_handles_multi_year(shared_app, expansion_run):
    doc, _ = expansion_run
    rid = run_to_end(shared_app, doc["scenario_id"], solver="heuristic", max_evaluations=50)
    assert results.candidate_by_rank(shared_app, rid, 1)["multi_year"]["years"]


def test_external_solvers_refuse_multi_year(shared_app, expansion_run):
    doc, _ = expansion_run
    with pytest.raises(HelionyxError) as e:
        run.start_run(shared_app, doc["scenario_id"], solver="reopt")
    assert "multi-year" in e.value.message


def test_invalid_expansion_is_reported(shared_app, village_ids):
    inp = offgrid_input(village_ids, components={"pv": {"sizes_kwp": [50]}, "genset": {"sizes_kw": [40]}},
                        multi_year={"expansion": [{"year": 40, "bess_add_kwh": [0, 100]}]})
    errors = {e["path"] for e in create(shared_app, inp)["errors"]}
    assert "multi_year.expansion[0].year" in errors
    assert "multi_year.expansion[0].bess_add_kwh" in errors
