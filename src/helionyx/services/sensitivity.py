"""One-variable sensitivity analysis with NPC elasticities (FR-SEN-001, 003, 004)."""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from helionyx import DISCLAIMER
from helionyx.core.models.scenario import ScenarioInput
from helionyx.errors import ErrorCode, HelionyxError, validation
from helionyx.infra.db import now_iso
from helionyx.infra.ids import new_id
from helionyx.services.context import Helionyx, engine_version
from helionyx.services.run import evaluate_sizes, optimise
from helionyx.services.scenario import _Resolver, candidate_count, get_scenario, resolved_of

ELASTICITY_STEP = 0.01


class SweepVariable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=200, description="Dotted path in the scenario input, e.g. economics.fuel_price_per_l")
    values: list[float] = Field(min_length=1, max_length=25)


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    cur = data
    for k in keys[:-1]:
        nxt = cur.get(k)
        if nxt is None:
            nxt = {}
            cur[k] = nxt
        if not isinstance(nxt, dict):
            raise validation(f"Path '{path}' does not address a scenario field.", "Use a dotted path to a number.")
        cur = nxt
    cur[keys[-1]] = value


def _with_value(base_input: dict[str, Any], path: str, value: float) -> ScenarioInput:
    data = copy.deepcopy(base_input)
    _set_path(data, path, value)
    try:
        return ScenarioInput.model_validate(data)
    except ValidationError as exc:
        e = exc.errors()[0]
        raise validation(f"Value {value} is not valid for {path}: {e['msg']}",
                         "Check the path and the value range.") from exc


def _base_value(assumptions: list[dict[str, Any]], path: str) -> float | None:
    """Current value of an input path, from the assumption audit (handles component overrides)."""
    candidates = [path, path.replace(".overrides.", "."), path.replace(".overrides.", ".technical.")]
    for a in assumptions:
        if a["path"] in candidates and isinstance(a["value"], int | float) and not isinstance(a["value"], bool):
            return float(a["value"])
    return None


def _resolve(app: Helionyx, inp: ScenarioInput) -> Any:
    r = _Resolver(app, inp)
    s = r.resolve()
    if s is None or r.errors:
        msg = r.errors[0].message if r.errors else "scenario could not be resolved"
        raise validation(f"Sensitivity case is invalid: {msg}", "Choose values inside the valid range.")
    return s


def start_sensitivity(app: Helionyx, scenario_id: str, variables: list[SweepVariable], solver: str = "native",
                      owner: str = "local") -> dict[str, Any]:
    if len(variables) != 1:
        raise HelionyxError(ErrorCode.UNSUPPORTED_COMBINATION,
                            "v0.1 supports one-variable sweeps; two-variable grids arrive in v0.2.",
                            "Run one sweep per variable.")
    if solver != "native":
        raise HelionyxError(ErrorCode.UNSUPPORTED_COMBINATION, "Sensitivity uses the native solver in v0.1.",
                            "Use solver='native'.")
    doc = get_scenario(app, scenario_id)
    if doc["errors"]:
        raise validation("The scenario has validation errors.", "Fix them before running a sensitivity.")
    var = variables[0]
    total = doc["candidate_count"] * (len(var.values) + 1)
    limit = app.settings.max_sensitivity_evaluations
    if total > limit:
        raise HelionyxError(ErrorCode.SEARCH_SPACE_TOO_LARGE,
                            f"The sweep needs {total} evaluations; the limit is {limit}.",
                            "Use fewer values or a smaller search space.", {"evaluations": total, "limit": limit})
    # Validate every case up front so the job does not fail half-way.
    for v in var.values:
        _resolve(app, _with_value(doc["input"], var.path, v))
    batch_id = new_id("sen")
    batch = {"batch_id": batch_id, "scenario_id": scenario_id, "scenario_hash": doc["scenario_hash"],
             "variables": [var.model_dump()], "status": "queued", "engine": engine_version(),
             "created_at": now_iso()}
    app.db.put("batches", batch_id, batch, scenario_id=scenario_id)

    def job_fn(progress: Any) -> dict[str, Any]:
        return _execute(app, batch_id, doc, var, progress)

    job = app.jobs.submit("sensitivity", owner, job_fn, {"batch_id": batch_id, "scenario_id": scenario_id})
    batch["job_id"] = job["job_id"]
    app.db.put("batches", batch_id, batch, scenario_id=scenario_id)
    return {"job_id": job["job_id"], "batch_id": batch_id, "state": job["state"], "cases": len(var.values),
            "evaluations": total}


def _execute(app: Helionyx, batch_id: str, doc: dict[str, Any], var: SweepVariable, progress: Any) -> dict[str, Any]:
    base_s = resolved_of(doc)
    n = len(var.values) + 1
    progress(1.0, "base case")
    base_cands, _ = optimise(app, base_s, "npc")
    base_best = next((c for c in base_cands if c["rank"] == 1), None)
    cases = []
    for k, value in enumerate(var.values):
        s = _resolve(app, _with_value(doc["input"], var.path, value))
        cands, _ = optimise(app, s, "npc")
        best = next((c for c in cands if c["rank"] == 1), None)
        cases.append({
            "value": value, "candidate_count": candidate_count(s),
            "feasible_count": sum(1 for c in cands if c["feasible"]),
            "optimal_sizes": best["sizes"] if best else None,
            "optimal_metrics": {m: best["metrics"].get(m) for m in ("npc", "lcoe_per_kwh", "initial_capital",
                                                                   "renewable_fraction_pct", "annual_bill",
                                                                   "fuel_l_per_yr", "simple_payback_yr")}
            if best else None,
        })
        progress(100.0 * (k + 2) / (n + 1), f"case {k + 1}/{len(var.values)}")
    base_val = _base_value(doc["assumptions"], var.path)
    elasticity = None
    if base_best is not None and base_val not in (None, 0.0):
        assert base_val is not None
        npcs = []
        for f in (1 - ELASTICITY_STEP, 1 + ELASTICITY_STEP):
            s = _resolve(app, _with_value(doc["input"], var.path, base_val * f))
            sz = base_best["sizes"]
            rec = evaluate_sizes(app, s, [(sz["pv_kwp"], sz["wind_count"], sz["bess_kwh"], sz["genset_kw"])])[0]
            npcs.append(rec["metrics"]["npc"])
        npc0 = base_best["metrics"]["npc"]
        elasticity = ((npcs[1] - npcs[0]) / npc0) / (2 * ELASTICITY_STEP) if npc0 else None
    result = {
        "batch_id": batch_id, "scenario_id": doc["scenario_id"], "variable": var.path,
        "base_value": base_val,
        "base_optimal_sizes": base_best["sizes"] if base_best else None,
        "base_optimal_npc": base_best["metrics"]["npc"] if base_best else None,
        "cases": cases,
        "elasticities": [{"path": var.path, "npc_elasticity": elasticity, "method":
                          f"central difference ±{ELASTICITY_STEP:.0%} on the base optimal design"}],
    }
    batch = app.db.get("batches", batch_id, "sensitivity batch")
    batch.update(status="completed", result=result, finished_at=now_iso())
    app.db.put("batches", batch_id, batch, scenario_id=doc["scenario_id"])
    progress(100.0, "completed")
    return {"batch_id": batch_id}


def get_sensitivity_results(app: Helionyx, batch_id: str) -> dict[str, Any]:
    batch = app.db.get("batches", batch_id, "sensitivity batch")
    if batch["status"] != "completed":
        job = app.jobs.status(batch["job_id"]) if batch.get("job_id") else {"state": batch["status"]}
        raise HelionyxError(ErrorCode.VALIDATION_FAILED, f"Sensitivity batch {batch_id} is not complete "
                            f"(state: {job['state']}).", "Poll get_job_status until it completes.",
                            {"state": job["state"], "job_id": batch.get("job_id"), "error": job.get("error")})
    doc = get_scenario(app, batch["scenario_id"])
    out: dict[str, Any] = dict(batch["result"])
    out.update(currency=doc["resolved"]["economics"]["currency"],
               provenance={"engine": batch["engine"], "scenario_hash": batch["scenario_hash"],
                           "solver": "native-enumerative", "packs": doc["resolved"]["pack"]},
               disclaimer=DISCLAIMER)
    return out
