# Helionyx — Implementation Plan (v0.1 MVP, v0.2 Validation, v1.0)

| | |
|---|---|
| **Scope** | v0.1: PRD F1–F9 plus the v0.1 "Should" items F10–F13. v0.2: PRD §10 Validation release (§6 below) |
| **Source documents** | [`Helionyx-PRD.md`](../Helionyx-PRD.md), [`Helionyx-SRS.md`](../Helionyx-SRS.md) |
| **Status** | v0.1 done (branch `feat/mvp-v0.1`); v0.2.0 released 7 October 2026 with placeholder data; v1.0 in development (branch `feat/v1.0`, §7) |
| **Last updated** | 7 October 2026 |

This plan turns the PRD and SRS into an ordered build. It records the
implementation decisions that the SRS leaves open, the work packages, and the
status of each. The weekly calendar in PRD §10.1 still applies; this document
tracks *what* is built and *how*, not dates.

---

## 1. Build order

Dependencies run strictly downwards: each layer only imports from layers below it.

```text
api        mcp_server.py · cli.py · http_app.py
services   site/resource · load · tariff · scenario · run (jobs) · sensitivity · explain · export
core       models · engine (pv, wind, dispatch kernel) · economics · billing · optimise
infra      settings · db (SQLite) · artefacts (Parquet) · http cache · jobs · packs loader
packs/lk   pack.yaml · tariffs · components · archetypes · emissions · defaults · sample data
```

| WP | Work package | SRS refs | Status |
|---|---|---|---|
| WP0 | Repository scaffold: `pyproject.toml`, src layout, ruff/mypy/pytest config, CI, Dockerfile, licence | §3.5, NFR-MAINT-01, NFR-LIC-01 | Done |
| WP1 | Domain models (Pydantic v2) and error model `HNX-Exxx` | §4.6, §6 | Done |
| WP2 | `lk` country pack: manifest, tariffs, components, 10 archetypes, emissions, defaults, bundled resource samples | FR-TAR-001, FR-CMP-001, FR-LOAD-002, §6.3 | Done (values unverified — see §4) |
| WP3 | Infrastructure: workspace, SQLite metadata store, content-addressed Parquet store, HTTP response cache | §6.2, FR-RES-008 | Done |
| WP4 | Resource module: NASA POWER, PVGIS, CSV import, UTC→local alignment, leap-day drop, gap filling | FR-RES-001…009 | Done |
| WP5 | Load module: archetypes, variability, monthly calibration, composites, measured import, statistics | FR-LOAD-001…006, 008 | Done |
| WP6 | Simulation engine: PV (pvlib), wind, battery, genset, LF/CC/grid-TOU dispatch in one Numba kernel | FR-SIM-001…009, §7.1–7.6 | Done |
| WP7 | Economics and bill engine: NPC, LCOE, replacements, salvage, payback, IRR, emissions; TOU/block/flat, demand, export schemes | FR-ECO-001…004, FR-TAR-002…008, §7.7–7.10 | Done |
| WP8 | Scenario builder: schema, defaults resolution, assumption audit, validation rules, hashing, versioning, YAML round-trip, outage masks | FR-SCN-001…009 | Done |
| WP9 | Optimiser and jobs: parallel enumeration, feasibility, ranking, base case, async jobs with progress/cancel/timeout/limits | FR-OPT-001…004, FR-JOB-001…006 | Done |
| WP10 | Sensitivity (one variable) with elasticities | FR-SEN-001, 003, 004 | Done |
| WP11 | Results, explanation, monthly summary, compare runs | FR-RPT-001…005, FR-ADP-005 | Done |
| WP12 | Exports: HOMER CSV + parameter sheet, Markdown report, time series CSV, scenario YAML | FR-EXP-001…003, FR-RPT-006 | Done |
| WP13 | MCP server: 21 tools, resources, 6 prompts, structured errors, annotations, stdio + Streamable HTTP | §4.1–4.5 | Done |
| WP14 | CLI: `serve`, `run`, `results`, `export`, `pack validate`, `eval grounding` | §4.7 | Done |
| WP15 | Claude skill, grounding checker and evaluation prompt set | FR-SKL-001…003, §9.5 | Done (eval transcripts to be captured) |
| WP16 | REopt v3 adapter (common adapter interface, input mapping, rate limit, fixture) | FR-ADP-001, 002 | Done — live run needs an API key |
| WP17 | Reference cases RC-1…RC-3 and regression goldens | §9.2, FR-PRV-003 | Done |
| WP18 | Documentation: README, quickstart, methodology, data-pack guide, skill guide, changelog | NFR-USE-03 | Done |

## 2. Implementation decisions

Decisions the SRS leaves open, or where the MVP deliberately simplifies. Each one
is reversible without changing the public tool contract.

| # | Decision | Rationale |
|---|---|---|
| D1 | **MCP Python SDK 2.x** (`mcp.server.mcpserver.MCPServer`, the renamed FastMCP). Tools return `CallToolResult` annotated with a Pydantic output model, so every tool has an `outputSchema`, `structuredContent` and a one-paragraph text summary (IF-MCP-04). | Current SDK major version; v1 `FastMCP` import path no longer exists. |
| D2 | **Metadata store uses the standard-library `sqlite3`** behind a small repository class. SQLAlchemy 2.0 is introduced with PostgreSQL in hosted mode (v1.0). | One fewer dependency for local-first MVP; the repository class is the seam for the swap. |
| D3 | **Parallelism uses a Numba `prange` kernel over candidates** inside a worker thread, instead of a `ProcessPoolExecutor`. | Avoids pickling large arrays on Windows; each candidate writes to its own row so results are bit-identical for any thread count (FR-OPT-003). |
| D4 | **Search runs in chunks** (≈ 20 per job) so the job can report progress, honour `cancel_job` and enforce the timeout between chunks. | FR-JOB-002, FR-JOB-004. |
| D5 | `run_optimization` / `run_sensitivity` accept an optional `wait_seconds` (0–20, default 0). With 0 they return `job_id` immediately (FR-JOB-001); with a value they stream `notifications/progress` for up to that long (FR-JOB-003). | Progress notifications are only meaningful while a request is open. |
| D6 | **Year-boundary handling in time alignment** wraps circularly within the requested year (the first local half-hour of 1 January uses the last UTC hour of 31 December of the same year). Recorded in provenance. | Avoids a second API call for one hour; error is negligible for pre-feasibility. |
| D7 | **CC hold** (`cc_hold_until_setpoint`) applies in deficit steps only; in surplus steps the genset is always off. | Running a genset into a renewable surplus only produces excess energy. |
| D8 | **Block tariffs** are incremental slabs; each slab may carry its own fixed charge, applied when the monthly consumption falls in that slab. | Matches CEB domestic structure while keeping one schema. |
| D9 | **Converter** cost is charged per kW of battery inverter power (`bess_kw`); the PV inverter is included in the PV cost. | AC-coupled topology (C7). |
| D10 | **Elasticities** use a ±1 % central difference on the optimal design at the base point. Inputs with a base value of 0 report `null`. | FR-SEN-003. |
| D11 | **Excel reports** (`format: xlsx`) return HNX-E008 in v0.1. | Excel is a v0.2 Must (FR-RPT-006). |
| D12 | HTTP transport (IF-MCP-02, Could in 0.1) is available through Starlette + uvicorn with `/healthz`, **without authentication**, and binds to `127.0.0.1` by default. OAuth (IF-MCP-08) arrives in v1.0. | Lets integrators test locally; never expose it publicly before v1.0. |

## 3. Definition of done for v0.1

- `pytest` green: unit, property (Hypothesis: energy balance ≤ 0.001 kWh, SOC bounds, no simultaneous charge/discharge, energy-preserving load scaling), contract (every tool has input/output schema; errors carry hints; outputs carry provenance and disclaimer) and regression (RC-1…RC-3 byte-identical reruns).
- Benchmark: 1,000-candidate search ≤ 30 s warm on a 4-core laptop (NFR-PERF-01).
- `helionyx serve` connects to Claude Desktop / Claude Code and completes the PRD §8 happy path.
- Grounding evaluation (§9.5) run on 30 prompts with ≥ 95 % score — **manual step before release**.
- Docs per NFR-USE-03.

### Verification status (7 October 2026)

| Check | Result |
|---|---|
| Test suite (`pytest`, offline) | 81 tests pass: unit, Hypothesis property, MCP contract, regression, benchmark |
| Energy balance | Max error ≈ 3 × 10⁻¹⁴ kWh per step on every candidate (limit 0.001) |
| Reproducibility | RC-1…RC-3 rerun in fresh workspaces give byte-identical results; goldens in `reference_cases/expected/` |
| Performance | 1,000-candidate off-grid search: ≈ 9 s end to end (kernel ≈ 0.1 s; economics, payback and persistence dominate) |
| MCP | 21 tools with input/output schemas and annotations, 9 resources, 6 prompts; stdio and Streamable HTTP (`/mcp`, `/healthz`) |
| Lint / types | `ruff check src tests` clean; `mypy` strict on `core/` clean |
| Grounding evaluation | Checker and 30-prompt set shipped; **transcripts not yet captured** |

## 4. Open items blocking a public release

These come from SRS Appendix A and PRD §14. The code is complete for them; the
*data* is not.

| Ref | Item | Current state |
|---|---|---|
| V3 / Q1 | Real CEB and LECO tariff rates, TOU windows and export-scheme rules | Pack ships **placeholder rates marked `status: unverified`**. `validate_scenario` raises HNX-W001 until a maintainer verifies them. |
| V8 | Diesel and Sri Lankan grid emission factors | Indicative values, marked unverified. |
| V9 | Genset fuel-curve defaults vs local datasheets | Literature defaults (F0 = 0.08145, F1 = 0.246). |
| — | Component costs in LKR | Indicative market estimates, marked unverified. |
| V2 | PVGIS coverage for Sri Lanka | Adapter implemented; falls back with HNX-E003 when out of coverage. |
| V4 | REopt API key and field mapping | **Done (v0.2):** field names checked against the live `/help` schema; RC-2 solved live on developer.nlr.gov (PV 180 kWp, no battery, NPC within 2 % of native). US incentives zeroed. The shared `DEMO_KEY` is rate-limited: use a personal key. Off-grid mapping still not supported. |
| V5 | SAMA and MicroGridsPy licences | **Done (v0.2):** SAMAPy is AGPL-3.0 and MicroGridsPy is EUPL-1.2 — both copyleft, so both are separate packages run as subprocesses. |
| V6 | HOMER Pro import format | Format checked against the HOMER Pro manual (one value per line, 8,760 rows from midnight 1 January, GHI in kW/m²). An import test inside HOMER Pro is part of the parity study. |
| — | Grounding evaluation transcripts | Harness `evals/grounding/run_eval.py` captures them automatically through the Claude API. Needs Anthropic credentials and a budget approval to run (30 prompts × several tool turns). |
| — | HTTP mode authentication | Interim bearer key (`HNX_API_KEY`); non-local binds refused without it. OAuth with Entra ID remains v1.0. |

## 5. Deferred to v1.0 and later

Hosted mode with Entra ID (F19, IF-MCP-08), `delete_scenario` and retention, multi-year load
growth and capacity expansion (F18), DC coupling and micro-hydro (F21), ecosystem packaging
(F20), wind in the REopt and SAMA adapters, off-grid REopt mapping.

## 6. v0.2 Validation release

PRD §10: HOMER parity study, SAMA and MicroGridsPy adapters, two-variable sensitivity, Excel
reports, two pilot users. Plus the v0.2 "Must/Should" requirements in the SRS.

| WP | Work package | SRS refs | Status |
|---|---|---|---|
| WP19 | Two-variable sensitivity grids with optimal architecture per cell | FR-SEN-002 | Done |
| WP20 | Excel report (7 sheets) sharing one data model with the Markdown report | FR-RPT-006 | Done |
| WP21 | Heuristic optimiser (seeded multi-start pattern search, evaluation budget) | FR-OPT-005 | Done — within 1 % of the enumerated optimum on RC-1 and RC-3 |
| WP22 | Pareto front over NPC, CO₂ and capacity shortage (`get_pareto_front`) | FR-OPT-006 (Could) | Done |
| WP23 | Partial-year measured load extension | FR-LOAD-007 | Done |
| WP24 | Subprocess adapter protocol `helionyx-adapter-io/1` | FR-ADP-001 | Done |
| WP25 | `helionyx-microgridspy` package (EUPL-1.2), LP with HiGHS | FR-ADP-003 | Done — live runs on RC-1, RC-2 and RC-3 (SRS AC: RC-1 and RC-3) |
| WP26 | `helionyx-sama` package (AGPL-3.0), particle swarm | FR-ADP-004 | Done — live run on RC-3 agrees with MicroGridsPy within 0.1 % of NPC (docs/adapters.md) |
| WP27 | Data-pack releases: `pack build`, checksum-verified `pack update` | §6.2 | Done |
| WP28 | HOMER parity kit: protocol, template, `helionyx parity compare` | F15, §9.4 | Tooling done; **study deferred** — no HOMER Pro access (PRD Q3) |
| WP29 | REopt adapter hardening and live verification | FR-ADP-002 (Must in v0.2) | Done |
| WP30 | Grounding transcript harness | FR-SKL-003 | Done; live run deferred (needs Anthropic API credentials and budget) |
| WP31 | Interim HTTP bearer-key auth | NFR-SEC-03 (interim) | Done |
| WP32 | Mock pilot users (one EPC, one planner) replaying their user stories over MCP | PRD §10, §9 usability | Done (`tests/test_pilot_mock.py`); real pilots still to recruit |

### v0.2 implementation decisions

| # | Decision | Rationale |
|---|---|---|
| D13 | MicroGridsPy is isolated like SAMA (separate package, subprocess), contrary to the SRS assumption that only SAMA needed isolation. | MicroGridsPy is EUPL-1.2 (copyleft); constraint C5. |
| D14 | Adapters exchange JSON files through a documented protocol instead of importing solver APIs. | Licence isolation, solver-specific Python versions, crash isolation. |
| D15 | Money sent to REopt and SAMA is divided by a currency scale (FX rate, else 300) and scaled back. | REopt range-checks USD magnitudes; SAMA's penalty weights assume USD. Linear costs make the optimum scale-invariant. |
| D16 | Enumeration limits move from `create_scenario` to `run_optimization`; scenarios up to 50 million candidates are accepted for the heuristic. | FR-OPT-005 needs scenarios larger than `max_candidates`. |
| D17 | Installed pack releases override the bundled pack only when their CalVer is newer. | A stale download can never downgrade data. |
| D18 | v0.2.0 ships with the placeholder `lk` pack data (marked `unverified`, HNX-W001). Verified values arrive as a data-pack release (`pack update`), no code release needed. | Verified tariff and cost data are not yet available; the pack release path (WP27) decouples data from code. |
| D19 | Pilot users are simulated by scripted MCP sessions until real pilots are recruited. | Exercises the PRD §8 happy path and the five-minute target in CI. |
| D20 | Multi-year runs simulate sample years and interpolate between them (default every 5 years, plus each stage boundary and the last year). | Simulating all 25 years would multiply run time by 25 for a sub-percent change in NPC. |

## 7. v1.0 release

PRD §10: multi-objective optimisation, hosted mode, ecosystem packaging, paper draft. Plus the
v1.0 requirements in the SRS and the items deferred in §5.

| WP | Work package | SRS / PRD refs | Status |
|---|---|---|---|
| WP33 | Annual load growth for multi-year analysis | FR-LOAD-009, F18 | Done (`multi_year.load_growth_rate`) |
| WP34 | Multi-year capacity expansion with load growth | FR-OPT-007, F18 | Done (`multi_year.expansion`, up to 3 stages; native and heuristic solvers) |
| WP35 | Pareto front and heuristic search promoted to Must: acceptance tests (no dominated point returned; heuristic within 1 % on every reference case) | FR-OPT-005, FR-OPT-006, F16, F17 | Code done in v0.2; tests to extend |
| WP36 | OAuth 2.1 resource server with Microsoft Entra ID: protected resource metadata, issuer/audience/expiry checks, per-user data isolation; replaces `HNX_API_KEY` | IF-MCP-08, NFR-SEC-03, AT-10 | Open |
| WP37 | `delete_scenario` and 90-day retention in hosted mode | FR-PRV-004 | Open |
| WP38 | Azure Container Apps deployment (TLS only, managed identity, Application Insights) | F19, IF-MCP-02 | Open |
| WP39 | OpenTelemetry export for logs and per-run timing | NFR-OBS-01 | Open |
| WP40 | Ecosystem packaging: MCP registry listing, PowerMCP contribution | F20 | PyPI 0.3.0 published; server.json, glama.json, smithery.yaml ready; registry listing and PowerMCP open |
| WP41 | Grounding checker v1.0 criteria (score 1.0, unmatched numbers flagged) and a full Sonnet transcript run | §9.5, FR-SKL-003 | Harness ready (Sonnet only, token budget) |
| WP42 | Wind in the REopt and SAMA adapters; off-grid REopt mapping | FR-ADP-002, FR-ADP-004 | Open |
| WP43 | DC-coupled PV–battery topology and micro-hydro | FR-SIM-010, F21 (Could) | Open |
| — | HOMER parity study | F15 (Must in v1.0), PRD Q3 | Blocked: no HOMER Pro access |
| — | Verified `lk` pack data (tariffs, emission factors, fuel curves, LKR costs) | V3, V8, V9 | Placeholder data until publication (D18) |
| — | Real pilot users and paper draft | PRD §10 | Not code tasks |

