"""Mock pilot users (PRD §10 v0.2: one EPC engineer, one planner).

Each pilot replays its persona's user stories as the sequence of MCP tool calls an
assistant would make, and checks the PRD §9 usability target: first prompt to ranked
results in ≤ 5 minutes. Server time only; LLM latency is not part of this check.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from helionyx.api import mcp_server
from tests.conftest import DELFT, NEGOMBO, VILLAGE, make_app

TIME_TO_RESULTS_S = 300


async def _call(c: Client, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    r = await c.call_tool(tool, args)
    assert not r.is_error, f"{tool}: {r.content}"
    return r.structured_content


async def _finish(c: Client, job: dict[str, Any]) -> None:
    while (await _call(c, "get_job_status", {"job_id": job["job_id"], "wait_seconds": 20}))["state"] != "completed":
        pass


async def _site(c: Client, name: str, coords: tuple[float, float], components: list[dict[str, Any]]) -> dict[str, str]:
    site = (await _call(c, "create_site", {"name": name, "latitude": coords[0], "longitude": coords[1]}))["site_id"]
    res = (await _call(c, "fetch_resource", {"site_id": site}))["dataset_id"]
    load = (await _call(c, "synthesize_load", {"site_id": site, "components": components}))["dataset_id"]
    return {"site_id": site, "resource_id": res, "load_id": load}


async def _optimise(c: Client, scenario: dict[str, Any], **opts: Any) -> tuple[str, str]:
    scn = (await _call(c, "create_scenario", {"scenario": scenario}))["scenario_id"]
    assert (await _call(c, "validate_scenario", {"scenario_id": scn}))["valid"]
    job = await _call(c, "run_optimization", {"scenario_id": scn, "wait_seconds": 20, **opts})
    await _finish(c, job)
    return scn, job["run_id"]


async def epc_engineer() -> dict[str, Any]:
    """P1: hotel rooftop PV + battery under the CEB TOU tariff (US-01, 02, 04, 07, 09)."""
    out: dict[str, Any] = {}
    async with Client(mcp_server.build_server()) as c:
        start = time.perf_counter()
        ids = await _site(c, "Negombo hotel", NEGOMBO, [{"archetype": "hotel", "count": 60}])
        scenario = {
            "name": "pilot EPC", "site_id": ids["site_id"], "load_ids": [ids["load_id"]],
            "resource_id": ids["resource_id"],
            "grid": {"mode": "grid_connected", "tariff_id": "lk.ceb.H2", "export_scheme": "net_accounting",
                     "availability": {"type": "scheduled",
                                      "windows": [{"days": "all", "start": "18:30", "end": "20:30"}]}},
            "components": {"pv": {"sizes_kwp": [0, 100, 150, 200]}, "bess": {"sizes_kwh": [0, 100, 200]}},
            "constraints": {"max_capacity_shortage": 1.0},
        }
        _, run_id = await _optimise(c, scenario)
        out["results"] = await _call(c, "get_results", {"run_id": run_id, "top_n": 5})
        out["elapsed_s"] = time.perf_counter() - start
        out["monthly"] = await _call(c, "get_monthly_summary", {"run_id": run_id})
        out["explain"] = await _call(c, "explain_run", {"run_id": run_id})
        out["md"] = await _call(c, "export_report", {"run_id": run_id, "format": "md"})
        out["xlsx"] = await _call(c, "export_report", {"run_id": run_id, "format": "xlsx"})
    return out


async def planner() -> dict[str, Any]:
    """P4: off-grid village with PV, battery and diesel, unmet load ≤ 1 % (US-03, 05, 11)."""
    out: dict[str, Any] = {}
    async with Client(mcp_server.build_server()) as c:
        start = time.perf_counter()
        ids = await _site(c, "Delft village", DELFT, VILLAGE)
        scenario = {
            "name": "pilot planner", "site_id": ids["site_id"], "load_ids": [ids["load_id"]],
            "resource_id": ids["resource_id"], "grid": {"mode": "off_grid"},
            "components": {"pv": {"sizes_kwp": [0, 50, 100]}, "bess": {"sizes_kwh": [0, 150, 300]},
                           "genset": {"sizes_kw": [0, 40, 60]}},
            "constraints": {"max_capacity_shortage": 0.01},
        }
        scn, run_id = await _optimise(c, scenario)
        out["results"] = await _call(c, "get_results", {"run_id": run_id, "top_n": 5})
        out["elapsed_s"] = time.perf_counter() - start
        _, heur_id = await _optimise(c, scenario, solver="heuristic", max_evaluations=60)
        out["compare"] = await _call(c, "compare_runs", {"run_ids": [run_id, heur_id]})
        out["pareto"] = await _call(c, "get_pareto_front", {"run_id": run_id})
        job = await _call(c, "run_sensitivity", {"scenario_id": scn, "wait_seconds": 20, "variables": [
            {"path": "economics.fuel_price_per_l", "values": [250, 350, 450]}]})
        await _finish(c, job)
        out["sensitivity"] = await _call(c, "get_sensitivity_results", {"batch_id": job["batch_id"]})
    return out


@pytest.fixture(scope="module")
def pilots(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    app = make_app(tmp_path_factory.mktemp("pilots"))
    mcp_server.set_app(app)
    try:
        return {"epc": asyncio.run(epc_engineer()), "planner": asyncio.run(planner())}
    finally:
        mcp_server.set_app(None)
        app.close()


@pytest.mark.parametrize("who", ["epc", "planner"])
def test_time_to_ranked_results(pilots, who):
    p = pilots[who]
    assert p["results"]["candidates"], who
    assert p["elapsed_s"] <= TIME_TO_RESULTS_S
    assert p["results"]["provenance"] and p["results"]["disclaimer"]


def test_epc_bill_before_and_after(pilots):
    months = pilots["epc"]["monthly"]["months"]
    assert len(months) == 12
    assert all("bill_before" in m and "bill_after" in m for m in months)
    assert sum(m["bill_after"] for m in months) < sum(m["bill_before"] for m in months)


def test_epc_reports_written(pilots):
    md = Path(pilots["epc"]["md"]["path"])
    assert md.exists() and "Assumptions" in md.read_text(encoding="utf-8")
    assert Path(pilots["epc"]["xlsx"]["path"]).stat().st_size > 0


def test_planner_meets_reliability_and_cross_checks(pilots):
    p = pilots["planner"]
    assert all(cand["metrics"]["capacity_shortage_pct"] <= 1.0 + 1e-9 for cand in p["results"]["candidates"])
    assert len(p["compare"]["runs"]) == 2
    assert p["pareto"]["front_size"] >= 1


def test_planner_fuel_price_sensitivity(pilots):
    s = pilots["planner"]["sensitivity"]
    assert len(s["cases"]) == 3
    assert all(case["optimal_architecture"] for case in s["cases"])
