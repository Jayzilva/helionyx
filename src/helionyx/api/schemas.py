"""Tool output schemas (IF-MCP-04).

Each model names the fields a client can rely on; ``extra="allow"`` keeps the
schema open for additional detail fields without breaking clients. Numeric
fields carry their unit in the name suffix or in a ``units`` map (FR-RPT-003).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Out(BaseModel):
    model_config = ConfigDict(extra="allow")


class SiteOut(Out):
    site_id: str
    name: str
    latitude: float
    longitude: float
    elevation_m: float | None = None
    timezone: str
    country_pack: str


class DatasetOut(Out):
    dataset_id: str
    site_id: str
    kind: str
    source: str
    synthetic: bool
    content_hash: str
    stats: dict[str, Any]
    quality_flags: list[dict[str, Any]]
    provenance: dict[str, Any]
    resource_uri: str


class TariffListOut(Out):
    pack: str
    count: int
    tariffs: list[dict[str, Any]]


class TariffOut(Out):
    tariff: dict[str, Any]
    period_names: list[str]
    warnings: list[dict[str, Any]]
    pack: str


class BillOut(Out):
    tariff_id: str
    export_scheme: str
    currency: str
    months: list[dict[str, Any]]
    annual_total: float
    warnings: list[dict[str, Any]]


class ComponentListOut(Out):
    pack: str
    count: int
    components: list[dict[str, Any]]


class ScenarioOut(Out):
    scenario_id: str
    version: int
    scenario_hash: str
    candidate_count: int
    error_count: int
    warning_count: int
    resource_uri: str


class ValidationOut(Out):
    scenario_id: str
    scenario_hash: str
    valid: bool
    candidate_count: int
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    assumptions: list[dict[str, Any]]


class JobOut(Out):
    job_id: str
    state: str
    progress_pct: float | None = None
    eta_s: float | None = None
    run_id: str | None = None
    batch_id: str | None = None


class ResultsOut(Out):
    run_id: str
    scenario_hash: str
    currency: str
    sorted_by: str
    feasible_count: int
    infeasible_count: int
    candidates: list[dict[str, Any]]
    base_case: dict[str, Any] | None
    provenance: dict[str, Any]
    disclaimer: str


class ExplainOut(Out):
    run_id: str
    rank: int
    currency: str
    npc: float
    npc_by_cost_type: dict[str, Any]
    top_cost_drivers: list[dict[str, Any]]
    binding_constraints: list[dict[str, Any]]
    dispatch_statistics: dict[str, Any]
    uncertain_inputs: list[dict[str, Any]]
    sentences: list[str]
    provenance: dict[str, Any]
    disclaimer: str


class MonthlyOut(Out):
    run_id: str
    rank: int
    currency: str
    months: list[dict[str, Any]]
    annual_totals: dict[str, float]
    disclaimer: str


class SensitivityOut(Out):
    batch_id: str
    variable: str
    cases: list[dict[str, Any]]
    elasticities: list[dict[str, Any]]
    disclaimer: str


class CompareOut(Out):
    rank: int
    runs: list[dict[str, Any]]
    reference_run_id: str
    disclaimer: str


class ExportOut(Out):
    files: list[str] | None = None
    path: str | None = None
    resource_uri: str | None = None
