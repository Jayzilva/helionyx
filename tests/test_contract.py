"""MCP contract tests (IF-MCP-03…07, NFR-PERF-03, NFR-SAFE-01, NFR-USE-01)."""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from mcp import Client

from helionyx.api import mcp_server
from tests.conftest import make_app

EXPECTED_TOOLS = {
    "create_site", "fetch_resource", "import_timeseries", "synthesize_load", "list_tariffs", "get_tariff",
    "compute_bill", "list_components", "create_scenario", "validate_scenario", "run_optimization",
    "get_job_status", "cancel_job", "get_results", "explain_run", "get_monthly_summary", "run_sensitivity",
    "get_sensitivity_results", "compare_runs", "export_homer_csv", "export_report",
}
OPEN_WORLD = {"fetch_resource"}


@pytest.fixture(scope="module")
def session_result(tmp_path_factory):
    app = make_app(tmp_path_factory.mktemp("contract"))
    mcp_server.set_app(app)

    async def scenario():
        out = {}
        async with Client(mcp_server.build_server()) as c:
            out["tools"] = (await c.list_tools()).tools
            out["prompts"] = (await c.list_prompts()).prompts
            out["templates"] = (await c.list_resource_templates()).resource_templates
            out["resources"] = (await c.list_resources()).resources
            r = await c.call_tool("create_site", {"name": "Negombo", "latitude": 7.2083, "longitude": 79.8358})
            site = r.structured_content["site_id"]
            out["fetch"] = await c.call_tool("fetch_resource", {"site_id": site})
            res = out["fetch"].structured_content["dataset_id"]
            r = await c.call_tool("synthesize_load", {"site_id": site, "components": [{"archetype": "hotel",
                                                                                      "count": 60}]})
            load = r.structured_content["dataset_id"]
            r = await c.call_tool("create_scenario", {"scenario": {
                "name": "c", "site_id": site, "load_ids": [load], "resource_id": res,
                "grid": {"mode": "grid_connected", "tariff_id": "lk.ceb.H2"},
                "components": {"pv": {"sizes_kwp": [0, 100, 200]}, "bess": {"sizes_kwh": [0, 100]}}}})
            scn = r.structured_content["scenario_id"]
            out["validate"] = await c.call_tool("validate_scenario", {"scenario_id": scn})
            out["run"] = await c.call_tool("run_optimization", {"scenario_id": scn, "wait_seconds": 20})
            run_id = out["run"].structured_content["run_id"]
            st = await c.call_tool("get_job_status", {"job_id": out["run"].structured_content["job_id"],
                                                      "wait_seconds": 20})
            assert st.structured_content["state"] == "completed"
            out["results"] = await c.call_tool("get_results", {"run_id": run_id})
            out["explain"] = await c.call_tool("explain_run", {"run_id": run_id})
            out["error"] = await c.call_tool("get_results", {"run_id": "run_missing"})
            out["bad_lat"] = await c.call_tool("create_site", {"name": "x", "latitude": 99, "longitude": 1})
            out["methodology"] = await c.read_resource("hnx://docs/methodology")
            out["csv"] = await c.read_resource(f"hnx://runs/{run_id}/results.csv")
            out["prompt"] = await c.get_prompt("size_offgrid_village", {"location": "Delft", "households": "150"})
        return out

    try:
        yield asyncio.run(scenario())
    finally:
        mcp_server.set_app(None)
        app.close()


def test_tool_catalogue(session_result):
    tools = {t.name: t for t in session_result["tools"]}
    assert set(tools) == EXPECTED_TOOLS
    for name, t in tools.items():
        assert re.fullmatch(r"^[a-z][a-z0-9_]{2,63}$", name)
        assert t.input_schema and t.output_schema, name
        assert t.description
        assert t.annotations is not None
        assert bool(t.annotations.open_world_hint) == (name in OPEN_WORLD), name
    for name in ("list_tariffs", "get_tariff", "get_results", "explain_run", "validate_scenario"):
        assert tools[name].annotations.read_only_hint is True


def test_prompts_and_resources(session_result):
    assert {p.name for p in session_result["prompts"]} == {
        "size_cni_rooftop_tou", "size_offgrid_village", "diesel_replacement_island", "explain_for_client",
        "homer_crosscheck", "teach_me"}
    uris = {t.uri_template for t in session_result["templates"]}
    assert "hnx://runs/{run_id}/results.csv" in uris and "hnx://datasets/{dataset_id}.csv" in uris
    assert "Methodology" in session_result["methodology"].contents[0].text.split("\n")[0].title()
    assert session_result["csv"].contents[0].text.startswith("candidate_index")
    assert "validate_scenario" in session_result["prompt"].messages[0].content.text


def test_structured_outputs_and_sizes(session_result):
    fetch = session_result["fetch"]
    assert not fetch.is_error
    assert len(json.dumps(fetch.structured_content)) <= 4096          # FR-RES-009
    res = session_result["results"]
    assert len(json.dumps(res.structured_content)) <= 32_000           # NFR-PERF-03
    assert res.structured_content["disclaimer"].startswith("Helionyx results are pre-feasibility")
    assert res.structured_content["provenance"]["engine"].startswith("helionyx")
    assert len(res.content) == 1 and "\n\n" not in res.content[0].text  # one-paragraph summary
    ex = session_result["explain"].structured_content
    assert ex["disclaimer"] and ex["provenance"] and ex["sentences"]
    assert len(json.dumps(session_result["validate"].structured_content)) <= 32_000


def test_errors_are_tool_results_with_hints(session_result):
    for key, code in (("error", "HNX-E002"), ("bad_lat", "HNX-E001")):
        r = session_result[key]
        assert r.is_error
        err = r.structured_content["error"]
        assert err["code"] == code and err["hint"] and err["message"]


def test_explanation_numbers_exist_in_run_record(session_result):
    """Every number in the explanation's sentences appears in its structured facts (FR-RPT-002)."""
    from helionyx.evals import _flatten, _matches, _numbers

    ex = session_result["explain"].structured_content
    pool: list[float] = []
    _flatten({k: v for k, v in ex.items() if k != "sentences"}, pool)
    for sentence in ex["sentences"]:
        for raw, suffix, value in _numbers(sentence):
            assert _matches(value, raw, suffix, pool), (sentence, raw)
