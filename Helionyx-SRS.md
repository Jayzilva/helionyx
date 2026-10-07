# Helionyx — Software Requirements Specification

| | |
|---|---|
| **Product** | Helionyx |
| **Document** | Software Requirements Specification (SRS) |
| **Version** | 0.1 — Draft |
| **Date** | 7 October 2026 |
| **Owner** | Jayath |
| **Companion document** | `Helionyx-PRD.md` |
| **Structure** | Adapted from ISO/IEC/IEEE 29148 |

### Revision history

| Version | Date | Change |
|---|---|---|
| 0.1 | 7 October 2026 | Initial draft. |
| 0.1.1 | 7 October 2026 | Implementation decisions recorded in [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) §2: MCP Python SDK 2.x (`MCPServer`, formerly FastMCP); standard-library `sqlite3` metadata store for the MVP (SQLAlchemy arrives with PostgreSQL in hosted mode); candidates simulated in parallel with Numba `prange` in worker threads instead of a `ProcessPoolExecutor`; optional `wait_seconds` parameter on asynchronous tools to stream progress; `import_timeseries` accepts `base_resource_id`; `create_scenario` accepts `parent_scenario_id` for versioning; self-contained study-file format for `helionyx run`. No requirement changes. |

---

## 1. Introduction

### 1.1 Purpose

This document specifies the software requirements for Helionyx releases v0.1 to v1.0. It is the basis for design, implementation and testing, and is written for developers, reviewers and the domain mentor.

Product goals, users and priorities are defined in the PRD. This document refers to PRD feature IDs (F1–F21).

### 1.2 Scope

Helionyx is a Python package that exposes an MCP server, a command-line interface (CLI) and a Claude skill. It:

- acquires and prepares hourly resource and load data;
- simulates candidate hybrid systems hour by hour for one year;
- ranks feasible candidates by life-cycle cost;
- runs sensitivity analyses;
- explains results through structured, reproducible outputs;
- exports inputs and results as HOMER CSV, Markdown and Excel.

It does not perform EMT or stability studies, detailed electrical design or compliance certification.

### 1.3 Definitions and abbreviations

| Term | Meaning |
|---|---|
| AC-coupled | Topology where PV, battery (through its own inverter) and generator all connect to the AC bus |
| BESS | Battery energy storage system |
| CC | Cycle charging dispatch strategy: when the generator runs, it also charges the battery towards a set-point |
| CEB / LECO | Ceylon Electricity Board / Lanka Electricity Company |
| CRF | Capital recovery factor |
| Capacity shortage fraction | Unserved energy divided by total load energy (simplified definition, see §7.8) |
| EPC | Engineering, procurement and construction contractor |
| GHI | Global horizontal irradiance |
| LCOE | Levelised cost of energy (HOMER calls this COE) |
| LF | Load following dispatch strategy: the generator produces only enough to serve the load |
| MCP | Model Context Protocol |
| NPC | Net present cost over the project lifetime |
| POA | Plane-of-array irradiance |
| PUCSL | Public Utilities Commission of Sri Lanka |
| RF | Renewable fraction |
| SOC | State of charge |
| Streamable HTTP | The MCP transport for remote servers |
| TOU | Time-of-use tariff |
| Run ID | Unique identifier of an optimisation run; every number shown to a user must reference one |
| Scenario hash | SHA-256 hash of the canonical scenario including the hashes of all input data |
| Country pack | Versioned data bundle (tariffs, components, load archetypes, emission factors) for one country |

### 1.4 References

| Ref | Document |
|---|---|
| [R1] | Helionyx PRD v0.1 |
| [R2] | Model Context Protocol specification, revision 2025-06-18 or later — https://modelcontextprotocol.io/specification |
| [R3] | NASA POWER API documentation — https://power.larc.nasa.gov/docs/services/api/ |
| [R4] | PVGIS non-interactive API documentation, European Commission Joint Research Centre |
| [R5] | REopt API v3 documentation — https://developer.nlr.gov/docs/energy-optimization/reopt/v3/ |
| [R6] | HOMER Pro user manual — https://homerenergy.com/products/pro/docs/latest/ |
| [R7] | Sadat, Takahashi & Pearce, "A free and open-source microgrid optimization tool: SAMA the solar alone multi-objective advisor", *Energy Conversion and Management* 298 (2023) 117686 |
| [R8] | pvlib python documentation — https://pvlib-python.readthedocs.io |
| [R9] | Current CEB and LECO tariff schedules and PUCSL tariff decisions |
| [R10] | MicroGridsPy, SESAM group, Politecnico di Milano |

### 1.5 Requirement conventions

- **ID prefixes:** `FR-<MODULE>-<NNN>` for functional requirements, `NFR-<CATEGORY>-<NN>` for non-functional requirements, and `IF-<AREA>-<NN>` for interface requirements.
- **Wording:** "shall" means mandatory for the stated release; "should" means desirable.
- **Priority:** M (Must), S (Should) or C (Could), as in the PRD.
- **Release (Rel):** the first release in which the requirement must be met. A requirement marked "S 0.1 / M 0.2" is a Should in v0.1 and a Must from v0.2.
- **Verification:** each requirement has acceptance criteria (AC) or is covered by a test listed in §9.

---

## 2. Overall description

### 2.1 Product perspective

Helionyx is a standalone, local-first service. MCP clients (Claude Desktop, Claude Code, Copilot Studio, custom agents) act as orchestrators: the LLM decides which tools to call. The server performs every calculation deterministically and never calls an LLM itself.

```mermaid
flowchart LR
    U[User] --> C["MCP client<br/>Claude · Copilot Studio · custom agent"]
    C -- "MCP: stdio or Streamable HTTP" --> S[Helionyx server]
    R[Researcher] -- CLI --> S
    S --> NP[NASA POWER API]
    S --> PG[PVGIS API]
    S --> RE[REopt API v3]
    S --> FS[("Local store<br/>SQLite + Parquet")]
    S --> DP[("Country data packs<br/>tariffs · components · archetypes")]
```

### 2.2 Product functions

| Module | Code | Function | PRD feature |
|---|---|---|---|
| Site and resource | RES | Sites; hourly solar, temperature and wind data; time alignment | F1 |
| Load | LOAD | Synthetic, calibrated, composite and measured load profiles | F2 |
| Tariff and billing | TAR | Tariff packs, bill engine, export schemes | F3 |
| Component library | CMP | Technical and cost parameters | F4 |
| Scenario | SCN | Scenario assembly, validation, assumption audit, outage masks | F5, F11 |
| Simulation | SIM | Hourly dispatch simulation | F6, F21 |
| Optimiser | OPT | Enumerative, heuristic and multi-objective search | F6, F16, F17, F18 |
| Economics | ECO | NPC, LCOE, payback, emissions | F6 |
| Sensitivity | SEN | Parameter sweeps and elasticities | F7 |
| Results and explanation | RPT | Ranked results, structured explanations, reports | F8, F12 |
| Export | EXP | HOMER CSV, time series, scenario files | F10, F12 |
| Solver adapters | ADP | REopt, MicroGridsPy, SAMA, comparisons | F13, F14 |
| Jobs | JOB | Asynchronous execution and progress | F6, F7 |
| Provenance | PRV | Data lineage, reproducibility, retention | F5, F8 |
| Skill and prompts | SKL | Claude skill, MCP prompts, grounding evaluation | F9 |

### 2.3 User classes

| Class | Interface | Access |
|---|---|---|
| Chat user (PRD personas P1, P2, P4) | MCP client | All tools; own data only |
| Researcher (P3) | CLI, scenario YAML, MCP | All tools; local files within the configured workspace |
| Integrator (P5) | Streamable HTTP | Tools allowed by the OAuth scope |
| Data maintainer | Pull requests to data packs | Edits to data packs, subject to review |
| Operator (hosted mode) | Azure portal | Deployment and monitoring; no access to scenario content |

### 2.4 Operating environment

- Python 3.11, 3.12 and 3.13.
- Windows 10/11, macOS 13 or later, Ubuntu 22.04 or later.
- Docker image for linux/amd64 and linux/arm64.
- MCP clients that support stdio (local mode) or Streamable HTTP (remote mode).
- Hosted mode (v1.0) runs on Azure:
  - Azure Container Apps
  - Azure Database for PostgreSQL Flexible Server
  - Azure Blob Storage
  - Azure Key Vault
  - Microsoft Entra ID
  - Application Insights

### 2.5 Design and implementation constraints

| ID | Constraint |
|---|---|
| C1 | Compliance with MCP specification revision 2025-06-18 or later. Tools declare `inputSchema` and `outputSchema`, and return `structuredContent` plus a short text summary. |
| C2 | No LLM calls inside the server. |
| C3 | The engine is country-agnostic. All country-specific values live in data packs. |
| C4 | No tariff, cost or emission values are hard-coded in source code. |
| C5 | The core is licensed Apache-2.0. Copyleft dependencies are isolated in separately distributed packages. |
| C6 | The MVP uses an hourly time step and one representative year. |
| C7 | The MVP supports AC-coupled systems only. |

### 2.6 Assumptions and dependencies

| ID | Assumption |
|---|---|
| A1 | NASA POWER hourly data is adequate for pre-feasibility studies in Sri Lanka. |
| A2 | Users accept synthetic load profiles when they are calibrated to monthly bills. |
| A3 | One simulated year represents every year of the project life. Year-on-year degradation is not modelled before v1.0. |
| A4 | The REopt API remains publicly available at developer.nlr.gov. |
| A5 | Current CEB and LECO tariffs and export scheme rules can be obtained from public documents. |

---

## 3. System architecture

### 3.1 Technology stack

| Layer | Choice | Why | Alternative considered |
|---|---|---|---|
| MCP server | Official MCP Python SDK 2.x (MCPServer, formerly FastMCP) | The whole solver ecosystem is Python; supports stdio and Streamable HTTP | C# MCP SDK on .NET 8 |
| HTTP host (remote mode) | FastAPI (ASGI), mounting the MCP Streamable HTTP app plus `/healthz` | Preferred Python web framework; easy middleware for authentication | Starlette alone |
| Domain models | Pydantic v2 | One set of schemas serves validation, MCP input/output schemas and YAML files | dataclasses + jsonschema |
| Numerics | NumPy, pandas; Numba for the dispatch loop | The hourly loop must run thousands of times per search | Cython; pure Python (too slow) |
| PV and solar geometry | pvlib | Peer-reviewed, widely used models | Own implementation |
| Wind | Power curves from the component library with a log-law height correction; windpowerlib optional | Simple and transparent | windpowerlib as a mandatory dependency |
| Metadata store | SQLite locally; PostgreSQL in hosted mode; both through SQLAlchemy 2.0 (MVP: standard-library sqlite3; SQLAlchemy with PostgreSQL in hosted mode) | Zero-install locally; managed service on Azure | SQL Server (licence cost, no benefit here) |
| Time-series store | Parquet files (pyarrow) in a content-addressed artefact store; local disk or Azure Blob | Compact, fast, reproducible | Time-series database (overkill) |
| Jobs | In-process `ProcessPoolExecutor` with a job table in the metadata store | Enough for modest load; no broker to operate | Celery + Redis (overkill for v1.0) |
| External HTTP | httpx with an on-disk response cache | Async support; offline mode | requests |
| MILP solver for adapters | HiGHS through highspy (MIT licence) | Fast, permissive licence | CBC, GLPK |
| Reports | Jinja2 templates to Markdown; openpyxl for Excel | Simple, templated text keeps outputs grounded | Word documents (heavier) |
| CLI | Typer | Type-hinted, small | argparse |
| Packaging | `pyproject.toml` (uv/hatch), PyPI, container image on GHCR | Standard for open-source Python | conda |
| CI | GitHub Actions: ruff, mypy, pytest, grounding evaluation, licence and vulnerability scans | Free for open-source projects | Azure DevOps Pipelines |
| Hosting (v1.0) | Azure Container Apps | Long CPU-bound jobs rule out Functions; AKS is unnecessary at this scale | Azure App Service |
| Remote authentication (v1.0) | OAuth 2.1 per the MCP authorization spec, with Microsoft Entra ID as authorization server | Works with Copilot Studio and enterprise tenants | Static API keys (development only) |

**Why not .NET.** .NET is a natural default for many backends, but every solver this project uses (pvlib, Pyomo, MicroGridsPy, SAMA, REopt tooling) is Python or called from Python. A .NET layer would duplicate every schema with no functional gain.

**Why not LangChain.** The MCP client already orchestrates the LLM. Adding agent orchestration inside the server would break constraint C2 and make results non-deterministic.

### 3.2 Component view

```mermaid
flowchart TB
  subgraph API["api — interface layer"]
    MCP["MCP server<br/>tools · resources · prompts"]
    CLI["CLI (Typer)"]
    HTTP["FastAPI host<br/>Streamable HTTP · /healthz"]
  end
  subgraph SVC["services — application layer"]
    SITE[Site and resource]
    LOADS[Load]
    TARIFF[Tariff and billing]
    SCEN["Scenario<br/>validation · assumption audit"]
    RUN["Run orchestrator<br/>jobs · progress"]
    SENS[Sensitivity]
    EXPL[Explanation and reports]
    EXPT[Export]
  end
  subgraph CORE["core — domain and engine"]
    MOD[Pydantic models]
    SIM["Simulation engine<br/>(Numba dispatch)"]
    ECO[Economics and billing]
    OPT["Optimiser<br/>enumerative · heuristic · Pareto"]
  end
  subgraph ADP["adapters"]
    REO[REopt v3]
    MGP[MicroGridsPy]
    SAMA[SAMA subprocess]
  end
  subgraph INFRA["infrastructure"]
    DB[("SQLite / PostgreSQL")]
    PQ[("Parquet artefacts")]
    CACHE[("HTTP cache")]
    PACKS[("Country data packs")]
  end
  HTTP --> MCP
  MCP --> SVC
  CLI --> SVC
  SVC --> CORE
  RUN --> ADP
  SVC --> INFRA
```

### 3.3 Key workflow: optimisation run

```mermaid
sequenceDiagram
  actor U as User
  participant C as MCP client (LLM)
  participant S as Helionyx
  participant W as Worker pool
  participant D as Store
  U->>C: "Size PV + battery for my hotel..."
  C->>S: create_scenario(...)
  S->>D: Save scenario and hash
  S-->>C: scenario_id, candidate_count
  C->>S: validate_scenario(scenario_id)
  S-->>C: errors[], warnings[], assumptions[]
  C->>U: Shows assumptions and asks for confirmation
  U->>C: Confirms
  C->>S: run_optimization(scenario_id) with progressToken
  S->>W: Enqueue job
  S-->>C: job_id, run_id (state = queued)
  W-->>S: Progress 10% ... 100%
  S-->>C: notifications/progress
  C->>S: get_job_status(job_id)
  S-->>C: state = completed
  C->>S: get_results(run_id, top_n = 5)
  S->>D: Read results
  S-->>C: Ranked candidates + provenance
  C->>S: explain_run(run_id, rank = 1)
  S-->>C: Cost breakdown, drivers, binding constraints
  C->>U: Answer citing the run_id for every number
```

### 3.4 Deployment view

```mermaid
flowchart LR
  subgraph Local["Local mode (v0.1)"]
    CD["Claude Desktop / Claude Code"] -- stdio --> P["helionyx serve<br/>(Python process)"]
    P --> L1[("~/.helionyx/<br/>SQLite · Parquet · cache")]
  end
  subgraph Azure["Hosted mode (v1.0)"]
    CS["Copilot Studio / remote clients"] -- "HTTPS · Streamable HTTP" --> ACA["Azure Container Apps<br/>helionyx container"]
    ACA --> PGS[("Azure Database<br/>for PostgreSQL")]
    ACA --> BL[("Blob Storage<br/>artefacts")]
    ACA --> KV[Key Vault]
    ACA --> AI[Application Insights]
    CS -. "OAuth 2.1" .-> ENTRA[Microsoft Entra ID]
    ACA -. "token validation" .-> ENTRA
  end
```

### 3.5 Repository structure

```text
helionyx/
├── src/helionyx/
│   ├── api/
│   │   ├── mcp_server.py        # tools, resources, prompts (MCP Python SDK)
│   │   ├── http_app.py          # FastAPI host: Streamable HTTP + /healthz
│   │   └── cli.py               # Typer CLI: serve, run, export, data
│   ├── services/                # one application service per module (RES, LOAD, TAR...)
│   ├── core/
│   │   ├── models/              # Pydantic schemas: inputs, outputs, packs
│   │   ├── engine/              # pv.py, wind.py, storage.py, genset.py, dispatch.py
│   │   ├── economics.py
│   │   ├── billing.py
│   │   └── optimise/            # enumerate.py, heuristic.py, pareto.py
│   ├── adapters/                # reopt.py, microgridspy.py, sama_client.py
│   ├── infra/                   # db.py, artefacts.py, http_cache.py, jobs.py
│   └── packs/lk/                # pack.yaml, tariffs/, components/, archetypes/, emissions.yaml
├── skills/helionyx/SKILL.md
├── reference_cases/             # RC-1..RC-3 scenario YAML + expected results
├── evals/grounding/             # 30-prompt grounding evaluation + checker
├── tests/                       # unit, property, contract, regression
├── docs/                        # quickstart, methodology, data-pack guide
├── Dockerfile
├── docker-compose.yml           # hosted-mode stack for local development (app + PostgreSQL)
├── .github/workflows/ci.yml
├── pyproject.toml
├── .env.example
└── README.md
```

Naming:

| Item | Name |
|---|---|
| Python package | `helionyx` |
| CLI command | `helionyx` |
| MCP server name | `helionyx` |
| SAMA adapter (separate GPL-3 package) | `helionyx-sama` |

---

## 4. External interface requirements

### 4.1 MCP transport and capabilities

| ID | Requirement | Pri | Rel |
|---|---|---|---|
| IF-MCP-01 | The server shall support the stdio transport. | M | 0.1 |
| IF-MCP-02 | The server shall support the Streamable HTTP transport at `/mcp`. | C / M | 0.1 / 1.0 |
| IF-MCP-03 | The server shall declare the `tools`, `resources` and `prompts` capabilities. It shall send `notifications/progress` when a request carries a `progressToken`. | M | 0.1 |
| IF-MCP-04 | Every tool shall declare an `inputSchema` and `outputSchema` generated from Pydantic models. It shall return `structuredContent` plus a text summary of at most one paragraph. | M | 0.1 |
| IF-MCP-05 | Tool execution failures shall be returned as tool results with `isError: true` and a structured error object (§4.6). JSON-RPC errors are reserved for protocol faults. | M | 0.1 |
| IF-MCP-06 | Tool names shall match `^[a-z][a-z0-9_]{2,63}$`. | M | 0.1 |
| IF-MCP-07 | Tool annotations shall mark read-only tools with `readOnlyHint` and tools that call external services with `openWorldHint`. | M | 0.1 |
| IF-MCP-08 | In HTTP mode, the server shall act as an OAuth 2.1 resource server under the MCP authorization specification, with Microsoft Entra ID as the authorization server. It shall publish protected-resource metadata and validate token audience and expiry on every request. | M | 1.0 |

### 4.2 Tool catalogue

"Async" tools return a `job_id` immediately and complete in the background (§5.13).

| # | Tool | Purpose | Key inputs | Key outputs | Mode | Rel |
|---|---|---|---|---|---|---|
| 1 | `create_site` | Register a site | `name`, `latitude`, `longitude`, `elevation_m?`, `timezone?`, `country_pack?` | `site_id`, site summary | Sync | 0.1 |
| 2 | `fetch_resource` | Get hourly solar, temperature and wind data | `site_id`, `source` (`nasa_power` \| `pvgis`), `year`, `variables?` | `resource_id`, annual and monthly statistics, quality flags, provenance | Sync | 0.1 |
| 3 | `import_timeseries` | Upload a resource or load series | `site_id`, `kind` (`load` \| `ghi` \| `temp` \| `wind`), `csv_text` or `file_path`, `resolution_min`, `units` | `dataset_id`, statistics, quality flags | Sync | 0.1 |
| 4 | `synthesize_load` | Build a load from archetypes | `site_id`, `components[]` (`archetype`, `count` \| `annual_kwh` \| `peak_kw`), `monthly_kwh?`, `variability?`, `seed?` | `load_id`, annual kWh, peak kW, load factor, monthly table | Sync | 0.1 |
| 5 | `list_tariffs` | Browse the tariff pack | `utility?`, `category?`, `as_of?` | Tariff IDs, names, effective dates | Sync | 0.1 |
| 6 | `get_tariff` | Show tariff details | `tariff_id` | Charge structure, periods, export schemes, provenance, staleness flag | Sync | 0.1 |
| 7 | `compute_bill` | Bill for a load with no system, or for any import/export series | `tariff_id`, `load_id` or `import_id` + `export_id`, `export_scheme?` | Monthly bill table, annual total | Sync | 0.1 |
| 8 | `list_components` | Browse the component library | `type?`, `query?` | Component specifications and costs | Sync | 0.1 |
| 9 | `create_scenario` | Combine all inputs into a scenario | Scenario object (§6.6) | `scenario_id`, `scenario_hash`, candidate count | Sync | 0.1 |
| 10 | `validate_scenario` | Check the scenario and list assumptions | `scenario_id` | `errors[]`, `warnings[]`, `assumptions[]` | Sync | 0.1 |
| 11 | `run_optimization` | Simulate and rank candidates | `scenario_id`, `solver` (`native` \| `reopt` \| `microgridspy` \| `sama`), `sort_by?`, `keep_timeseries_top_n?` | `job_id`, `run_id` | Async | 0.1 |
| 12 | `get_job_status` | Poll a job | `job_id` | State, progress %, estimated time remaining, `run_id` | Sync | 0.1 |
| 13 | `cancel_job` | Cancel a job | `job_id` | Final state | Sync | 0.1 |
| 14 | `get_results` | Ranked designs | `run_id`, `top_n?`, `sort_by?`, `filters?` | Candidate rows, base case, provenance | Sync | 0.1 |
| 15 | `explain_run` | Structured explanation of a design | `run_id`, `rank?` (default 1), `compare_to?` (`next` \| `base`) | Cost breakdown, drivers, binding constraints, differences | Sync | 0.1 |
| 16 | `get_monthly_summary` | Monthly energy flows and bills | `run_id`, `rank` | 12-month table | Sync | 0.1 |
| 17 | `run_sensitivity` | Sweep one or two inputs | `scenario_id`, `variables[]` (`path`, `values[]`), `solver?` | `job_id`, `batch_id` | Async | 0.1 |
| 18 | `get_sensitivity_results` | Results of a sweep | `batch_id` | Optimal design per case, elasticities | Sync | 0.1 |
| 19 | `compare_runs` | Compare solvers or scenarios | `run_ids[]` | Side-by-side sizes and metrics with differences | Sync | 0.1 |
| 20 | `export_homer_csv` | HOMER-importable inputs | `scenario_id` or `run_id` | File paths or resource URIs | Sync | 0.1 |
| 21 | `export_report` | Client report | `run_id`, `format` (`md` \| `xlsx`) | File path or resource URI | Sync | 0.1 / 0.2 |
| 22 | `delete_scenario` | Delete a scenario and its runs and artefacts (hosted mode) | `scenario_id` | Deletion receipt | Sync | 1.0 |

### 4.3 Tool contract examples

All values below are illustrative. They are not results.

**`run_optimization` input**

```json
{
  "scenario_id": "scn_01J9X4K2",
  "solver": "native",
  "sort_by": "npc",
  "keep_timeseries_top_n": 10
}
```

**`get_results` structured output (abridged)**

```json
{
  "run_id": "run_01J9X5A7",
  "scenario_hash": "sha256:4be1c0…",
  "currency": "LKR",
  "sorted_by": "npc",
  "feasible_count": 212,
  "infeasible_count": 38,
  "candidates": [
    {
      "rank": 1,
      "sizes": { "pv_kwp": 200, "bess_kwh": 200, "bess_kw": 100, "genset_kw": 0 },
      "metrics": {
        "npc_lkr": 98500000,
        "lcoe_lkr_per_kwh": 41.2,
        "initial_capital_lkr": 52000000,
        "operating_cost_lkr_per_yr": 3900000,
        "renewable_fraction_pct": 38.5,
        "capacity_shortage_pct": 0.0,
        "excess_electricity_pct": 1.8,
        "simple_payback_yr": 5.9
      }
    }
  ],
  "base_case": { "label": "grid_only", "npc_lkr": 131000000 },
  "provenance": {
    "engine": "helionyx 0.1.0 (git 3f2c1a9)",
    "solver": "native-enumerative",
    "packs": { "lk": "2026.10.0" },
    "sources": ["nasa_power:hourly:2023", "tariff:lk.ceb.<category>@<effective_from>"],
    "seed": 42
  },
  "disclaimer": "Pre-feasibility estimate. Not a substitute for detailed design or chartered-engineer sign-off."
}
```

### 4.4 Resources

| URI template | Content | Rel |
|---|---|---|
| `hnx://packs/{country}/tariffs/{tariff_id}` | Tariff definition (JSON) | 0.1 |
| `hnx://packs/{country}/components/{type}` | Component library entries | 0.1 |
| `hnx://packs/{country}/archetypes/{archetype}` | Load archetype definition | 0.1 |
| `hnx://datasets/{dataset_id}.csv` | Hourly series of a resource or load dataset | 0.1 |
| `hnx://scenarios/{scenario_id}` | Canonical scenario JSON | 0.1 |
| `hnx://runs/{run_id}/results.csv` | Every candidate with sizes, metrics and feasibility | 0.1 |
| `hnx://runs/{run_id}/candidates/{rank}/timeseries.csv` | Hourly flows of a persisted candidate | 0.1 |
| `hnx://runs/{run_id}/report.md` | Generated report | 0.1 |
| `hnx://docs/methodology` | Equations and documented differences from HOMER (§7) | 0.1 |

### 4.5 Prompts

| Prompt | Arguments | Purpose | Persona |
|---|---|---|---|
| `size_cni_rooftop_tou` | `location`, `tariff_category`, `monthly_kwh?`, `roof_area_m2?` | Commercial or industrial rooftop PV + battery under a TOU tariff | P1 |
| `size_offgrid_village` | `location`, `households`, `services?` | Off-grid village microgrid | P4 |
| `diesel_replacement_island` | `location`, `annual_diesel_l?` or `annual_kwh?` | Hybridise an existing diesel system | P4 |
| `explain_for_client` | `run_id`, `audience` (`technical` \| `executive`) | Grounded explanation for a client | P1 |
| `homer_crosscheck` | `run_id` | Guided export to HOMER and a comparison checklist | P2, P3 |
| `teach_me` | `topic` (for example "cycle charging versus load following") | Tutoring using small worked runs | P2 |

### 4.6 Error model

Tool errors are returned in this structure:

```json
{
  "error": {
    "code": "HNX-E004",
    "name": "SEARCH_SPACE_TOO_LARGE",
    "message": "The search space has 186,000 candidates; the limit is 50,000.",
    "hint": "Reduce the number of battery sizes or use solver='heuristic' (v0.2).",
    "details": { "candidate_count": 186000, "limit": 50000 }
  }
}
```

| Code | Name | Meaning |
|---|---|---|
| HNX-E001 | VALIDATION_FAILED | Input fails schema or range checks |
| HNX-E002 | NOT_FOUND | Unknown site, dataset, scenario, run, job or tariff ID |
| HNX-E003 | EXTERNAL_SOURCE_UNAVAILABLE | External API unreachable, out of coverage, or offline with no cache |
| HNX-E004 | SEARCH_SPACE_TOO_LARGE | Candidate or evaluation limit exceeded |
| HNX-E005 | NO_FEASIBLE_CANDIDATE | No candidate meets the constraints; the nearest candidate is returned |
| HNX-E006 | JOB_TIMEOUT | Job exceeded its time limit |
| HNX-E007 | RATE_LIMITED | Rate limit reached, either local or from an external service |
| HNX-E008 | UNSUPPORTED_COMBINATION | Feature combination not supported in this release (for example net plus with a battery) |
| HNX-E009 | UNAUTHORIZED | Missing or invalid credentials (HTTP mode) |
| HNX-W001 | DATA_STALE | A tariff or pack is older than its freshness threshold |
| HNX-W002 | SYNTHETIC_INPUT | A synthetic or partly synthetic load is in use |
| HNX-W003 | GAP_FILLED | Resource data contains filled gaps |
| HNX-W004 | PLAUSIBILITY | An input is unusual but allowed |

### 4.7 Command-line interface

| Command | Purpose | Rel |
|---|---|---|
| `helionyx serve --transport stdio\|http [--port 8080]` | Start the MCP server | 0.1 |
| `helionyx run <scenario.yaml> [--solver native]` | Run a scenario without an MCP client | 0.1 |
| `helionyx results <run_id> [--top 10]` | Print results | 0.1 |
| `helionyx export homer <scenario_id> <dir>` | Write HOMER CSV files | 0.1 |
| `helionyx export report <run_id> --format md\|xlsx` | Write a report | 0.1 / 0.2 |
| `helionyx pack validate <path>` | Validate a data pack against its schemas | 0.1 |
| `helionyx pack update` | Download a newer signed pack release | 0.2 |
| `helionyx eval grounding <transcripts_dir>` | Run the grounding checker | 0.1 |

### 4.8 External data interfaces

#### 4.8.1 NASA POWER

- **Endpoint:** the hourly point API, using the `RE` (renewable energy) community.
- **Parameters:**

  | Parameter | Quantity |
  |---|---|
  | `ALLSKY_SFC_SW_DWN` | GHI |
  | `T2M` | Air temperature at 2 m |
  | `WS10M` | Wind speed at 10 m |
  | `WS50M` | Wind speed at 50 m |

- **Time standard:** request data in UTC, then align to local time under FR-RES-005.
- **Verify at implementation:** parameter names, units and time-standard options.

#### 4.8.2 PVGIS

- **Endpoints:** the hourly series and typical meteorological year endpoints.
- **Provenance:** record the radiation database PVGIS selects.
- **Verify at implementation:** database coverage for Sri Lanka.

#### 4.8.3 REopt API v3

- **Workflow:** submit a job, then poll for results.
- **Authentication:** API key read from `HNX_REOPT_API_KEY`.
- **Rate limit:** client-side limiter of 60 runs per hour.
- **Input mapping:** see FR-ADP-002.

#### 4.8.4 CSV input format

| Item | Rule |
|---|---|
| Encoding and delimiter | UTF-8, comma |
| Timestamp column | `timestamp` in ISO 8601 local time. Optional if the file has exactly 8,760 rows starting 1 January 00:00. |
| Value column header (name encodes the unit) | `load_kw`, `ghi_w_m2`, `temp_c`, `wind_m_s` (plus `height_m` in metadata) |
| Resolution | 15, 30 or 60 minutes |
| Size | 10 MB maximum |

#### 4.8.5 HOMER export format

- **Series files:** one file per series, with a single column of 8,760 rows starting 1 January 00:00 local time.

  | File | Unit |
  |---|---|
  | `load_kw.txt` | kW |
  | `ghi_kw_m2.txt` | kW/m² |
  | `temp_c.txt` | °C |
  | `wind_m_s.txt` | m/s; anemometer height stated in the parameter sheet |

- **Parameter sheet:** a `homer_parameters.md` file listing every component, economic and constraint value to enter in HOMER by hand.
- **Verify at implementation (v0.2):** the files import correctly into the current HOMER Pro version.

### 4.9 Skill interface

The Claude skill's behaviour is specified in FR-SKL-001. It is distributed as `skills/helionyx/SKILL.md` and assumes the MCP server is connected under the name `helionyx`.

---

## 5. Functional requirements

### 5.1 Site and resource (RES)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-RES-001 | The system shall create a site from a name, a latitude and longitude (WGS84 decimal degrees), an optional elevation and an IANA time zone (default from the country pack: `Asia/Colombo`). | M | 0.1 | Valid input returns a `site_id`. A latitude outside ±90 or a longitude outside ±180 returns HNX-E001. |
| FR-RES-002 | The system shall fetch hourly GHI, 2 m air temperature, and wind speed at 10 m and 50 m from NASA POWER for a selected calendar year. | M | 0.1 | Returns 8,760 hourly values per variable, with provenance (source, parameters, retrieval time). |
| FR-RES-003 | The system shall fetch hourly irradiance and temperature from PVGIS where it has coverage, and record which radiation database was used. | S | 0.1 | Dataset returned with the database name. An unsupported location returns HNX-E003 with a hint to use NASA POWER. |
| FR-RES-004 | The system shall import resource series from CSV files (§4.8.4). | M | 0.1 | A valid file is imported. A unit header that does not match `kind` returns HNX-E001. |
| FR-RES-005 | The system shall convert source timestamps to the site's local civil time. When the UTC offset is not a whole number of hours (Asia/Colombo is UTC+05:30), each local hour shall be the mean of the two overlapping source hours. The method shall be recorded in provenance. | M | 0.1 | Annual irradiation after alignment is within 0.5% of the source total. A unit test with a synthetic ramp confirms the 30-minute shift. |
| FR-RES-006 | For leap years, the system shall drop 29 February so that every series has 8,760 steps, and record this in provenance. | M | 0.1 | 2024 data yields 8,760 rows. |
| FR-RES-007 | Gaps of up to 3 consecutive hours shall be filled by linear interpolation. Longer gaps shall be filled with the same-hour mean of the 7 surrounding days only if the user sets `fill_long_gaps`; otherwise the dataset is rejected. Every filled value shall be flagged (HNX-W003). | M | 0.1 | Test series with gaps produce the expected fills and flags. |
| FR-RES-008 | Responses from external sources shall be cached on disk, keyed by a hash of the request. Offline mode shall use only the cache and bundled sample data. | M | 0.1 | A second identical request makes no network call. Offline mode with an empty cache returns HNX-E003. |
| FR-RES-009 | Tool outputs shall return summary statistics (annual and monthly irradiation, mean temperature, mean wind speed, quality flags), never raw series. Raw series shall be available as resources. | M | 0.1 | `fetch_resource` output is ≤ 4 KB. |

### 5.2 Load (LOAD)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-LOAD-001 | The system shall build an 8,760-hour load from an archetype. An archetype is defined by normalised 24-hour shapes for weekdays and weekends, monthly multipliers and a scale (annual kWh or peak kW). | M | 0.1 | Annual energy is within 0.1% of the target. |
| FR-LOAD-002 | The `lk` pack shall include at least these archetypes: rural household, urban household, hotel, office, small industry (one shift), small industry (three shifts), health clinic, school, telecom tower and cold storage. Each shall cite its basis and be labelled synthetic. | M | 0.1 | Pack validation passes. Every archetype has a `basis` field and `synthetic: true`. |
| FR-LOAD-003 | The system shall add day-to-day and hour-to-hour random variability with configurable standard deviations and a seed, then rescale the series to preserve the target energy. | M | 0.1 | The same seed reproduces an identical series. Energy is preserved within 0.1%. |
| FR-LOAD-004 | The system shall calibrate a load to 12 monthly kWh values, for example taken from electricity bills. | M | 0.1 | Each month is within 0.1% of its target. |
| FR-LOAD-005 | The system shall build a composite load from several components, for example 50 rural households, 1 clinic and 1 school. | M | 0.1 | At every step, the composite equals the sum of its components. |
| FR-LOAD-006 | The system shall import one full year of measured load at 15-, 30- or 60-minute resolution and convert it to hourly mean kW. | M | 0.1 | A 35,040-row 15-minute file yields 8,760 hourly values with energy preserved. |
| FR-LOAD-007 | The system should extend measured data shorter than one year (minimum 4 weeks) using day types and monthly scaling, and flag the result as partly synthetic (HNX-W002). | S | 0.2 | The extended series matches the measured weeks exactly. |
| FR-LOAD-008 | The system shall report annual kWh, peak kW, load factor, monthly energy and the average daily profile. | M | 0.1 | Values match an independent calculation in tests. |
| FR-LOAD-009 | The system shall support an annual load growth rate for multi-year analysis. | S | 1.0 | Year-n energy equals year-1 energy × (1 + g)^(n−1). |

### 5.3 Tariff and billing (TAR)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-TAR-001 | Tariffs shall be stored as versioned YAML files in the country pack. Each shall include utility, category code, `effective_from`, `effective_to`, currency, source document, URL, retrieval date and verifier (§6.4). | M | 0.1 | The pack schema is validated in CI. |
| FR-TAR-002 | The bill engine shall support these charge components: a fixed monthly charge; block (slab) energy rates on monthly consumption; TOU energy rates, with periods defined in local time and by day type; a maximum demand charge per kVA-month using a power factor assumption; a minimum charge; and percentage levies. | M | 0.1 | Unit tests for each component match hand-calculated bills. |
| FR-TAR-003 | The bill engine shall support four export schemes. `none`: no export credit. `net_metering`: energy banking with credit carried forward and a year-end rule taken from the pack. `net_accounting`: exports paid at the export rate and imports billed at the tariff. `net_plus`: all PV generation exported at the export rate and all load billed at the tariff. | M | 0.1 | Hand-calculated test cases pass for each scheme. |
| FR-TAR-004 | The bill engine shall compute monthly bills from hourly import and export series. | M | 0.1 | Results match a reference spreadsheet within LKR 1 per month. |
| FR-TAR-005 | By default, the system shall use the tariff revision in effect on the analysis date. `validate_scenario` shall raise HNX-W001 when the pack's `last_verified` date is older than 6 months or a newer revision exists. | M | 0.1 | Test packs with old dates trigger the warning. |
| FR-TAR-006 | Users may define a custom tariff inline in a scenario, using the same schema. | M | 0.1 | An inline tariff produces the same bill as the equivalent pack tariff. |
| FR-TAR-007 | `compute_bill` shall return the baseline bill for a load with no system installed. | M | 0.1 | Covered by AT-02. |
| FR-TAR-008 | Demand charges computed from hourly data shall apply a configurable `demand_peak_factor` (default 1.0) to approximate sub-hourly peaks. Outputs shall state this limitation. | S | 0.1 | The factor appears in the assumption audit. |

### 5.4 Components (CMP)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-CMP-001 | The component library shall define PV, wind turbine (with power curve), battery, diesel genset, converter and grid connection entries. Each entry shall include capital cost, replacement cost, O&M cost, lifetime, technical parameters, currency, cost year and source (§6.5). | M | 0.1 | Pack validation passes. |
| FR-CMP-002 | Any component parameter may be overridden in a scenario. Overrides shall be recorded as user-supplied in the assumption audit. | M | 0.1 | An overridden value appears with origin `user`. |
| FR-CMP-003 | LKR shall be the base currency of the `lk` pack. USD inputs shall be converted at a user-supplied exchange rate, which is recorded in provenance. The system shall not fetch live exchange rates. | M | 0.1 | A USD scenario reports the FX rate used. |
| FR-CMP-004 | The system shall enforce plausibility ranges, including: round-trip efficiency 0.5–1.0; minimum SOC 0–0.9; genset minimum load ratio 0–0.6; lifetimes greater than 0. | M | 0.1 | Out-of-range values return HNX-E001. Unusual but valid values return HNX-W004. |

### 5.5 Scenario (SCN)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-SCN-001 | A scenario shall combine: site; one or more loads; resource dataset; grid mode (`off_grid` or `grid_connected`); tariff and export scheme; components; search space; dispatch strategy; economics; constraints; and seed. | M | 0.1 | Covered by the schema in §6.6. |
| FR-SCN-002 | The search space shall be given per component as an explicit list of sizes or a (min, max, step) range, including zero. The system shall compute the total candidate count. If the count exceeds `max_candidates` (default 50,000), it shall return HNX-E004 with a suggestion for reducing the space. | M | 0.1 | Candidate count is correct for test spaces. |
| FR-SCN-003 | Supported constraints: maximum capacity shortage fraction; minimum renewable fraction; maximum annual genset hours; maximum export kW; maximum PV kWp (directly, or as roof area × the pack's kWp/m² density); maximum initial capital. | M | 0.1 | Each constraint marks violating candidates as infeasible in tests. |
| FR-SCN-004 | Supported economic inputs: project life; nominal discount rate and inflation (or a real discount rate directly); real escalation of fuel and grid prices; system fixed capital and O&M costs. | M | 0.1 | Covered by FR-ECO tests. |
| FR-SCN-005 | `validate_scenario` shall return three lists. Errors block a run. Warnings do not block a run. Assumptions give, for each field, the path, value, origin (`user`, `pack_default` or `system_default`), source and date. | M | 0.1 | Every field the user did not supply appears in the assumption list. |
| FR-SCN-006 | A scenario shall become immutable once it has been run; edits create a new version. The scenario hash shall be the SHA-256 of the canonical JSON of all inputs, including dataset content hashes and the pack version. | M | 0.1 | Editing a run scenario creates version 2 with a new hash. |
| FR-SCN-007 | Grid availability shall be one of: always on; scheduled windows (days, start time, end time in local time); or random outages (events per year, mean duration, seed). The system shall produce an 8,760-step availability mask and store it with the scenario. | S / M | 0.1 / 0.2 | Mask totals match the specification. The same seed reproduces the same mask. |
| FR-SCN-008 | Scenarios shall be exportable to and importable from YAML (§6.6). | M | 0.1 | A round-trip produces the same scenario hash. |
| FR-SCN-009 | Validation rules shall include at least: an off-grid system with no dispatchable source or storage (warning); net plus combined with a battery (HNX-E008 in the MVP); a grid-connected scenario with no tariff (error); a load peak above total possible supply (warning); a battery C-rate outside its specification (error). | M | 0.1 | One test case per rule. |

### 5.6 Simulation (SIM)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-SIM-001 | The system shall simulate each candidate over 8,760 hourly steps using the AC-coupled topology. | M | 0.1 | — |
| FR-SIM-002 | Every step shall satisfy the energy balance in §7.1. | M | 0.1 | Error ≤ 0.001 kWh in property-based tests over random scenarios. |
| FR-SIM-003 | PV output shall follow §7.2. | M | 0.1 | Annual output within 0.5% of a reference pvlib calculation. |
| FR-SIM-004 | Wind output shall follow §7.3. | M | 0.1 | Matches the power curve at test wind speeds. |
| FR-SIM-005 | Battery behaviour shall follow §7.4. | M | 0.1 | SOC never leaves its bounds in property tests. |
| FR-SIM-006 | Genset behaviour shall follow §7.5. | M | 0.1 | Fuel use matches hand calculation. |
| FR-SIM-007 | The system shall implement load following and cycle charging for off-grid operation and outages, and the grid-connected strategy, all as specified in §7.6. | M | 0.1 | Hand-traced 48-hour test cases pass for each strategy. |
| FR-SIM-008 | The simulation shall respect the grid availability mask. | S / M | 0.1 / 0.2 | No grid import or export occurs in masked hours. |
| FR-SIM-009 | The system shall output annual energy flows and the metrics in §7.8 for every candidate, and persist hourly series for the top N candidates (default 10). | M | 0.1 | — |
| FR-SIM-010 | The system should support a DC-coupled PV–battery topology. | C | 1.0 | — |

### 5.7 Optimisation (OPT)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-OPT-001 | The optimiser shall simulate every candidate in the search space, mark infeasible candidates with the constraints they violate, and rank feasible candidates by NPC (default), LCOE or initial capital. | M | 0.1 | Matches a brute-force reference on small spaces. |
| FR-OPT-002 | If no candidate is feasible, the system shall return HNX-E005 together with the candidate that has the smallest normalised constraint violation, and list the violated constraints. | M | 0.1 | Test with impossible constraints. |
| FR-OPT-003 | Candidates shall be simulated in parallel across CPU cores. Result order shall be deterministic, with ties broken by candidate index. | M | 0.1 | Identical results with 1 worker and N workers. |
| FR-OPT-004 | The system shall automatically simulate a base case for payback and IRR: grid-only for grid-connected scenarios; diesel-only for off-grid scenarios with a genset; no base case otherwise. | M | 0.1 | The base case appears in results. |
| FR-OPT-005 | A seeded heuristic search shall handle spaces larger than `max_candidates`. It shall state that the result is heuristic and report the number of evaluations. | S | 0.2 | On the reference cases, it finds the enumerated optimum or a design within 1% of its NPC. |
| FR-OPT-006 | The system shall return the Pareto set over NPC, annual CO₂ and capacity shortage. | C / M | 0.2 / 1.0 | No returned point is dominated by another. |
| FR-OPT-007 | The system shall support multi-year capacity expansion with load growth. | S | 1.0 | — |

### 5.8 Economics (ECO)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-ECO-001 | The system shall compute NPC, total annualised cost, LCOE, initial capital, annual operating cost, replacement costs and salvage value as specified in §7.7. | M | 0.1 | Within 0.01% of a hand-calculated spreadsheet. |
| FR-ECO-002 | The system shall compute simple payback, discounted payback and IRR relative to the base case. | M | 0.1 | Matches the spreadsheet. |
| FR-ECO-003 | The system shall break down costs by component and by type: capital, replacement, O&M, fuel, grid purchases, grid sales and salvage. | M | 0.1 | The breakdown sums to NPC within 0.01%. |
| FR-ECO-004 | The system shall compute emissions as specified in §7.9. | M | 0.1 | Matches hand calculation. |

### 5.9 Sensitivity (SEN)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-SEN-001 | The system shall sweep any numeric scenario field, addressed by its JSON path, over a list of values, re-optimising for each value. | M | 0.1 | Returns the optimal design for each value. |
| FR-SEN-002 | The system shall run two-variable grids and report the optimal system architecture in each cell. | S | 0.2 | — |
| FR-SEN-003 | For the optimal design, the system shall report the elasticity of NPC to each varied input at the base point (percentage change in NPC per percentage change in the input, by central difference). | M | 0.1 | Matches a numerical check. |
| FR-SEN-004 | Total evaluations (cases × candidates) shall not exceed `max_sensitivity_evaluations` (default 200,000); otherwise the system returns HNX-E004. | M | 0.1 | — |

### 5.10 Results and explanation (RPT)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-RPT-001 | `get_results` shall return the top N feasible candidates (default 5, maximum 50) with sizes and metrics, plus the base case. The full table shall be available as a resource. | M | 0.1 | Output is ≤ 8,000 tokens at the default N. |
| FR-RPT-002 | `explain_run` shall return structured facts only: the NPC breakdown and shares; binding constraints; differences from the next-ranked design and from the base case; dispatch statistics (genset hours and starts, battery equivalent full cycles, excess fraction, unserved energy); the top three cost drivers by NPC share; and the inputs with the highest uncertainty (synthetic load, stale tariff, gap-filled resource data). Any sentences shall come from templates. | M | 0.1 | Every number in the output exists in the run record. |
| FR-RPT-003 | Every numeric field shall carry a unit, either through its field-name suffix (`_kwh`, `_kw`, `_lkr`, `_pct`, …) or through a `units` map. | M | 0.1 | Schema lint in CI. |
| FR-RPT-004 | Every tool output shall include a `provenance` object (§4.3). | M | 0.1 | Contract tests. |
| FR-RPT-005 | `get_monthly_summary` shall return 12 months of energy flows and bills for a candidate. | M | 0.1 | Monthly values sum to the annual totals. |
| FR-RPT-006 | `export_report` shall produce Markdown (v0.1) and Excel (v0.2) reports. Each report shall contain the scenario summary, assumption table, top designs, cost breakdown, monthly table, sensitivity results (if any), provenance and disclaimer. | S / M | 0.1 / 0.2 | Covered by AT-09. |

### 5.11 Export (EXP)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-EXP-001 | `export_homer_csv` shall produce the files described in §4.8.5. | S / M | 0.1 / 0.2 | The files import into HOMER Pro without error (AT-06). |
| FR-EXP-002 | The hourly series of any persisted candidate shall be exportable as CSV or Parquet. | M | 0.1 | — |
| FR-EXP-003 | Scenarios shall be exportable as YAML (see FR-SCN-008). | M | 0.1 | — |

### 5.12 Solver adapters (ADP)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-ADP-001 | All adapters shall implement a common interface: `prepare(scenario)`, `run()` and `normalise()`. `normalise()` returns the common result schema, including the solver name and version. | M | 0.1 | Contract tests. |
| FR-ADP-002 | The REopt adapter shall map a scenario to REopt v3 inputs as follows. It supplies the custom hourly load, a PV production factor series from the native PV model, and an hourly energy rate series built from the tariff, so that no US-specific datasets are needed. It reads the API key from `HNX_REOPT_API_KEY`, applies a client-side limit of 60 runs per hour and polls until the job completes. Results are labelled "MILP, perfect foresight". | S / M | 0.1 / 0.2 | RC-2 runs end to end against the live API, or against a recorded fixture in CI. |
| FR-ADP-003 | The MicroGridsPy adapter shall use the HiGHS solver. | M | 0.2 | RC-1 and RC-3 complete. |
| FR-ADP-004 | The SAMA adapter shall be a separately distributed GPL-3 package, run as a subprocess with JSON input and output. | M | 0.2 | The core wheel contains no SAMA code (licence scan). |
| FR-ADP-005 | `compare_runs` shall show sizes and key metrics side by side, with absolute and percentage differences. | M | 0.1 | Covered by AT-11. |

### 5.13 Jobs (JOB)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-JOB-001 | Tools that may run longer than 20 s (`run_optimization`, `run_sensitivity`, adapter runs) shall return a `job_id` immediately. | M | 0.1 | The tool call returns within 2 s. |
| FR-JOB-002 | `get_job_status` shall return the job state (`queued`, `running`, `completed`, `failed`, `cancelled` or `interrupted`), progress %, estimated time remaining and `run_id`. | M | 0.1 | — |
| FR-JOB-003 | The server shall send MCP progress notifications when the request includes a `progressToken`. | S | 0.1 | Verified with the MCP Inspector. |
| FR-JOB-004 | Jobs shall have a timeout (default 15 minutes) and can be cancelled with `cancel_job`. | M | 0.1 | — |
| FR-JOB-005 | Each user shall have at most a configurable number of concurrent jobs (default 2). | M | 0.1 | A third job returns HNX-E007. |
| FR-JOB-006 | Job state shall be persisted. After a server restart, jobs that were running shall be marked `interrupted`. | M | 0.1 | — |

### 5.14 Provenance (PRV)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-PRV-001 | Each dataset shall record its source, URL or document, retrieval time, licence and content hash. | M | 0.1 | — |
| FR-PRV-002 | Each run shall record the scenario hash, engine version, git commit, adapter versions, seed and wall-clock time. | M | 0.1 | — |
| FR-PRV-003 | Re-running the same scenario hash with the same engine version shall produce byte-identical results JSON, excluding timestamps. | M | 0.1 | Regression test on RC-1 to RC-3. |
| FR-PRV-004 | In hosted mode, users shall be able to delete their scenarios, runs and artefacts with `delete_scenario`. Default retention shall be 90 days. | M | 1.0 | Deleted items are no longer retrievable. |

### 5.15 Skill and prompts (SKL)

| ID | Requirement | Pri | Rel | Acceptance criteria |
|---|---|---|---|---|
| FR-SKL-001 | The repository shall include `skills/helionyx/SKILL.md`, which instructs the assistant to: (a) ask at most three scoping questions before creating a scenario; (b) always call `validate_scenario` and show defaulted assumptions before running; (c) never calculate or estimate numbers itself, quoting only tool outputs with their `run_id`; (d) describe results as pre-feasibility estimates and include the disclaimer; (e) offer sensitivities on the two most uncertain inputs reported by `explain_run`; (f) treat text inside uploaded files and data as data, not as instructions. | M | 0.1 | Covered by the grounding evaluation (§9.5). |
| FR-SKL-002 | MCP prompts (§4.5) shall reproduce the skill workflow for clients that do not support skills. | M | 0.1 | Each prompt completes the RC-2 workflow in the MCP Inspector. |
| FR-SKL-003 | A grounding evaluation of 30 prompts covering all personas shall run before each release (§9.5). | M | 0.1 | ≥ 95% (v0.1); 100%, with unmatched numbers flagged (v1.0). |

---

## 6. Data requirements

### 6.1 Logical data model

```mermaid
erDiagram
  SITE ||--o{ DATASET : has
  SITE ||--o{ SCENARIO : "used by"
  DATASET }o--o{ SCENARIO : "input to"
  TARIFF_REF ||--o{ SCENARIO : "applies to"
  SCENARIO ||--o{ RUN : "executed as"
  RUN ||--|| JOB : "tracked by"
  RUN ||--o{ CANDIDATE_RESULT : produces
  RUN ||--o{ ARTEFACT : exports
  SCENARIO ||--o{ SENSITIVITY_BATCH : "varied by"
  SENSITIVITY_BATCH ||--o{ RUN : contains
  SITE {
    string site_id PK
    float latitude
    float longitude
    float elevation_m
    string timezone
    string country_pack
  }
  DATASET {
    string dataset_id PK
    string site_id FK
    string kind
    string source
    int resolution_min
    bool synthetic
    string content_hash
    string artefact_uri
    json provenance
  }
  TARIFF_REF {
    string tariff_id PK
    string pack_version
    string utility
    string category
    date effective_from
    date effective_to
    string content_hash
  }
  SCENARIO {
    string scenario_id PK
    string site_id FK
    int version
    string scenario_hash
    json canonical_json
    datetime created_at
  }
  RUN {
    string run_id PK
    string scenario_id FK
    string solver
    string engine_version
    int seed
    string status
    float wall_time_s
  }
  CANDIDATE_RESULT {
    string run_id FK
    int candidate_index
    int rank
    bool feasible
    json sizes
    json metrics
    json violations
    string timeseries_uri
  }
  JOB {
    string job_id PK
    string run_id FK
    string state
    float progress_pct
    json error
  }
  SENSITIVITY_BATCH {
    string batch_id PK
    string scenario_id FK
    json variables
  }
  ARTEFACT {
    string artefact_id PK
    string run_id FK
    string kind
    string uri
    string content_hash
  }
```

### 6.2 Storage rules

- **Metadata:** stored in SQLite (local) or PostgreSQL (hosted).
- **Time series:** stored as Parquet in a content-addressed artefact store, under `~/.helionyx/artefacts/` locally or in an Azure Blob container in hosted mode.
- **Data packs:** bundled in the package under `packs/<country>/`.
  - The database stores only references (ID, pack version, content hash).
  - Packs are versioned with CalVer (`YYYY.MM.patch`) and carry a `pack.yaml` manifest with `version`, `last_verified`, `maintainers` and `license`.
  - `helionyx pack update` (v0.2) verifies the release checksum before installing.
- **Workspace:** the local workspace root is set by `HNX_WORKSPACE`. File paths outside it are rejected.

### 6.3 Data pack layout

```text
packs/lk/
├── pack.yaml               # version, last_verified, maintainers, license (CC BY 4.0)
├── tariffs/
│   ├── ceb/<category>@<effective_from>.yaml
│   └── leco/<category>@<effective_from>.yaml
├── components/             # pv.yaml, wind.yaml, bess.yaml, genset.yaml, converter.yaml
├── archetypes/             # hotel.yaml, rural_household.yaml, ...
├── emissions.yaml          # diesel and grid emission factors, with sources
└── defaults.yaml           # economics, roof kWp/m², timezone, currency
```

### 6.4 Tariff file example

**Structure only.** All rates are placeholders, not real CEB values. TOU windows must be confirmed against the current schedule.

```yaml
id: lk.ceb.EXAMPLE@2026-01-01
utility: CEB
category: EXAMPLE
name: "Example TOU category (placeholder)"
currency: LKR
effective_from: 2026-01-01
effective_to: null
source:
  document: "<PUCSL tariff decision reference>"
  url: "<official URL>"
  retrieved_at: 2026-10-07
  verified_by: "<name>"
tou_periods:                     # local civil time; verify against the current schedule
  - { name: day,      start: "05:30", end: "18:30", days: all }
  - { name: peak,     start: "18:30", end: "22:30", days: all }
  - { name: off_peak, start: "22:30", end: "05:30", days: all }
charges:
  fixed_monthly: 0.0
  energy:
    type: tou                    # tou | block | flat
    rates_per_kwh: { day: 0.0, peak: 0.0, off_peak: 0.0 }
  demand:
    per_kva_month: 0.0
    power_factor_assumption: 0.9
  minimum_monthly: 0.0
  levies_pct: 0.0
export_schemes:
  net_metering:   { netting: by_period, credit_carry_forward: true, year_end: forfeit }
  net_accounting: { export_rate_per_kwh: 0.0, settlement: monthly }
  net_plus:       { export_rate_per_kwh: 0.0, settlement: monthly }
```

### 6.5 Component file example

Values are placeholders.

```yaml
id: bess.lfp.generic
type: bess
name: "Generic LFP battery (placeholder costs)"
currency: LKR
cost_year: 2026
source: { document: "<supplier quote / market survey>", retrieved_at: 2026-10-07 }
capital_per_kwh: 0.0
replacement_per_kwh: 0.0
om_per_kwh_year: 0.0
technical:
  round_trip_efficiency: 0.92
  soc_min: 0.10
  soc_initial: 1.00
  c_rate_max: 0.5
  float_life_years: 12
  lifetime_throughput_kwh_per_kwh: 3000
```

### 6.6 Scenario file example

```yaml
scenario:
  name: "RC-2 Hotel PV + BESS (grid-connected)"
  site_id: site_7f3a
  load_ids: [load_19c2]
  resource_id: res_a1b0
  grid:
    mode: grid_connected                  # off_grid | grid_connected
    tariff_id: lk.ceb.<category>@<effective_from>
    export_scheme: net_accounting         # none | net_metering | net_accounting | net_plus
    max_import_kw: 400
    max_export_kw: 250
    availability:
      type: scheduled                     # always | scheduled | stochastic
      windows: [{ days: all, start: "18:30", end: "20:30" }]
  components:
    pv:   { spec: pv.mono.generic, sizes_kwp: [0, 100, 150, 200, 250], dc_ac_ratio: 1.2, tilt_deg: 10, azimuth_deg: 180 }
    bess: { spec: bess.lfp.generic, sizes_kwh: [0, 100, 200, 300], power_kw_per_kwh: 0.5 }
    genset: null
    wind: null
  dispatch:
    strategy: load_following              # load_following | cycle_charging (off-grid and outages)
    grid:
      battery_discharge_periods: [peak]
      grid_charging: false
      charge_periods: [off_peak]
      soc_reserve_for_outage: 0.30
  economics:
    currency: LKR
    project_life_years: 20
    nominal_discount_rate: 0.12
    inflation_rate: 0.05
    fuel_price_escalation_real: 0.0
    grid_price_escalation_real: 0.0
  constraints:
    max_capacity_shortage: 0.0
    max_pv_kwp: 250
  seed: 42
```

### 6.7 Retention and deletion

- **Local mode:** the user owns all data. Nothing is sent anywhere except requests to the selected external data APIs. No telemetry is collected unless the user opts in.
- **Hosted mode:** scenarios, runs and artefacts are kept for 90 days by default and can be deleted on request (FR-PRV-004). Data is isolated per user identity (Entra ID object ID).

---

## 7. Calculation specification

This section is the normative methodology. It is also published as the `hnx://docs/methodology` resource.

### 7.1 Time base and energy balance

The model uses steps $t = 1 \dots 8760$ with $\Delta t = 1$ h. All powers are averages over the step in kW, so the energy in a step is $P\,\Delta t$ in kWh.

At every step, the AC bus satisfies:

$$P_{pv}(t)+P_{wt}(t)+P_{g}(t)+P_{imp}(t)+P_{dis}(t)+P_{uns}(t)=L(t)+P_{ch}(t)+P_{exp}(t)+P_{xs}(t)$$

| Symbol | Meaning |
|---|---|
| $P_{pv}$, $P_{wt}$ | PV and wind output at the AC bus |
| $P_{g}$ | Genset output |
| $P_{imp}$, $P_{exp}$ | Grid import and export |
| $P_{dis}$, $P_{ch}$ | Battery discharge and charge, measured at the AC bus |
| $P_{uns}$ | Unserved load |
| $P_{xs}$ | Excess (curtailed) energy |
| $L$ | Load |

All terms are ≥ 0. Charge and discharge cannot both happen in the same step: $P_{ch}(t)\cdot P_{dis}(t)=0$.

### 7.2 PV

DC output:

$$P_{pv}^{dc}(t)=Y_{pv}\,f_{pv}\,\frac{G_{T}(t)}{G_{STC}}\left[1+\alpha_{P}\,(T_{c}(t)-T_{STC})\right]$$

AC output, with inverter efficiency and clipping at the inverter rating:

$$P_{pv}(t)=\min\!\left(\eta_{inv}\,P_{pv}^{dc}(t),\ \frac{Y_{pv}}{r_{dc/ac}}\right)$$

| Symbol | Meaning |
|---|---|
| $Y_{pv}$ | PV array rating (kWp) |
| $f_{pv}$ | Derating factor |
| $G_T$ | Plane-of-array irradiance |
| $G_{STC}$ | 1,000 W/m² |
| $\alpha_P$ | Temperature coefficient of power |
| $T_c$, $T_{STC}$ | Cell temperature; 25 °C |
| $\eta_{inv}$ | Inverter efficiency |
| $r_{dc/ac}$ | DC/AC ratio |

$G_T$ is computed with pvlib in three steps:

1. Solar position.
2. Erbs decomposition of GHI into direct and diffuse components.
3. Hay–Davies transposition onto the array plane, with ground albedo 0.2 by default.

$T_c$ uses the Faiman model by default; the SAPM model can be selected instead. Defaults for $f_{pv}$, $\alpha_P$ and $\eta_{inv}$ come from the component specification.

### 7.3 Wind

Hub-height wind speed uses the logarithmic law, with the anemometer height ($z_{anem}$, 10 m or 50 m) chosen as the one closest to the hub:

$$v_{hub}=v_{anem}\,\frac{\ln(z_{hub}/z_{0})}{\ln(z_{anem}/z_{0})}$$

The surface roughness length $z_0$ defaults to 0.03 m and can be configured.

Output is calculated as follows:

1. Interpolate linearly on the turbine power curve.
2. Multiply by the air-density ratio (optional; standard atmosphere at the site elevation).
3. Multiply by the availability factor and the number of turbines.

### 7.4 Battery (idealised energy-reservoir model)

Stored energy evolves as:

$$E(t+1)=E(t)+\eta_{c}\,P_{ch}(t)\,\Delta t-\frac{P_{dis}(t)\,\Delta t}{\eta_{d}},\qquad \eta_{c}=\eta_{d}=\eta_{conv}\sqrt{\eta_{rt}}$$

Constraints:

- $SOC_{min}\,E_{nom}\le E(t)\le E_{nom}$
- $0\le P_{ch},P_{dis}\le P_{b,max}=\min(P_{conv},\,c\,E_{nom})$, where $c$ is the maximum C-rate.
- $E(1)=SOC_{0}\,E_{nom}$, with $SOC_0 = 1.0$ by default.

Annual throughput is the energy leaving the store:

$$Q_{thr}=\sum_t \frac{P_{dis}(t)\,\Delta t}{\eta_d}$$

Battery lifetime in years:

$$R_{bess}=\min\!\left(R_{float},\ \frac{Q_{life}}{Q_{thr}}\right)$$

where $Q_{life}$ is the lifetime throughput (kWh) from the specification.

### 7.5 Diesel genset

When running, the genset output is bounded by $r_{min}\,Y_{g}\le P_{g}(t)\le Y_{g}$, and its fuel use is:

$$F(t)=F_{0}\,Y_{g}+F_{1}\,P_{g}(t)\quad[\mathrm{L/h}]$$

| Symbol | Meaning |
|---|---|
| $Y_g$ | Rated capacity (kW) |
| $r_{min}$ | Minimum load ratio |
| $F_0$ | Fuel curve intercept (L/h per kW rated) |
| $F_1$ | Fuel curve slope (L/h per kW output) |

The pack defaults $F_0 = 0.08145$ and $F_1 = 0.246$ are generic values common in the hybrid-system literature. Replace them with manufacturer data where available.

The model also tracks annual run hours $H_g$ and the number of starts. Lifetime in years is:

$$R_{g}=\min\!\left(\frac{R_{g,hours}}{H_g},\ R_{g,calendar}\right)$$

### 7.6 Dispatch

#### 7.6.1 Step logic

Notation: $D_{max}=\min(P_{b,max},(E-E_{floor})\,\eta_d)$ is the available discharge, and $C_{max}=\min(P_{b,max},(E_{nom}-E)/\eta_c)$ is the available charge.

```text
N = L[t] - Ppv[t] - Pwt[t]                       # net load at the AC bus (kW)
grid_up = (mode == grid_connected) and A[t] == 1  # A = availability mask

if N <= 0:                                        # surplus
    S = -N
    Pch = min(S, Cmax);  S -= Pch
    if grid_up and export_allowed:
        Pexp = min(S, Pexp_max);  S -= Pexp
    Pxs = S

elif grid_up:                                     # deficit, grid available (7.6.3)
    if period(t) in discharge_periods:
        Pdis = min(N, Dmax with E_floor = soc_reserve * E_nom);  N -= Pdis
    Pimp = min(N, Pimp_max);  N -= Pimp
    if N > 0: serve N with the genset rule below (load following); remainder -> Puns
    if Pdis == 0 and grid_charging and period(t) in charge_periods:
        Pch_grid = min(Cmax to E_target, Pimp_max - Pimp);  Pimp += Pch_grid

else:                                             # deficit, off-grid or outage (7.6.2)
    E_floor = SOC_min * E_nom
    if N <= Dmax:
        Pdis = N                                  # battery alone covers the load
    elif genset present:
        apply the LF or CC rule (7.6.2)
    else:
        Pdis = Dmax;  Puns = N - Dmax
```

#### 7.6.2 Off-grid and outage strategies

**Load following (LF)**

1. Set $P_g=\mathrm{clip}(N,\ r_{min}Y_g,\ Y_g)$.
2. If $P_g\ge N$, the genset serves the whole load. The surplus $P_g-N$ charges the battery up to $C_{max}$, and anything left becomes excess. $P_{dis}=0$.
3. If $P_g<N$ (genset capacity is insufficient), set $P_{dis}=\min(N-P_g,\,D_{max})$. Anything still unmet becomes unserved load.

**Cycle charging (CC)**

1. Compute the charge target $C_t=\min(P_{b,max},\,\max(0,E_{sp}-E)/\eta_c)$, where $E_{sp}=SOC_{sp}E_{nom}$. The set-point $SOC_{sp}$ defaults to 0.8.
2. Set $P_g=\mathrm{clip}(N+C_t,\ r_{min}Y_g,\ Y_g)$.
3. The surplus and shortfall are then handled exactly as in LF.
4. If `cc_hold_until_setpoint` is true (the default), a genset that ran in step $t-1$ keeps running in step $t$ while $E<E_{sp}$.

#### 7.6.3 Grid-connected strategy

- **Battery discharge:** only during the configured `battery_discharge_periods` (default: the tariff's peak period). Energy below `soc_reserve_for_outage` × $E_{nom}$ is reserved for outages.
- **Grid charging:** optional, only during `charge_periods`, and only in steps with no discharge.
- **Grid limits:** import and export are capped at their configured limits. Load not covered within the import limit goes to the genset (load following), then to unserved load.
- **Outages:** during masked hours, the system follows §7.6.2 with the full battery range available.
- **Net plus:** PV is exported in full and the load is served entirely from the grid. Batteries are not allowed with net plus in the MVP (HNX-E008).

#### 7.6.4 Documented differences from HOMER

The methodology resource and every report shall disclose these differences:

1. The battery always discharges before the genset starts; HOMER instead compares marginal costs. The exception is the LF and CC rules above, which apply when the battery cannot cover the load.
2. There is no operating reserve in the MVP. Capacity shortage equals unserved energy.
3. The battery uses an idealised model, not a kinetic battery model.
4. Only one genset is modelled, with no multi-generator priority.
5. The time step is hourly, and one year represents the whole project life.
6. Only AC coupling is supported.

### 7.7 Economics

**Discount rate and capital recovery factor**

The real discount rate is derived from the nominal rate $i'$ and inflation $f$:

$$i=\frac{i'-f}{1+f}$$

$$CRF(i,N)=\frac{i(1+i)^{N}}{(1+i)^{N}-1}$$

**Cash flows**

Cash flows are in constant currency. A cost with real escalation $e$ in year $y$ is $C_1(1+e)^{y-1}$.

**Net present cost**

$$NPC=C_{cap}+\sum_{y=1}^{N}\frac{C_{om,y}+C_{fuel,y}+C_{grid,y}-R_{sales,y}}{(1+i)^{y}}+\sum_{k}\frac{C_{rep}}{(1+i)^{y_k}}-\frac{S}{(1+i)^{N}}$$

**Replacements**

Replacements happen at years $y_k=k\,R$ for every $k\ge1$ with $y_k<N$. Fractional replacement years are allowed in discounting, for example a battery lifetime derived from throughput.

**Salvage value (linear)**

1. Let $y_{last}$ be the year of the last installation: 0 for the original installation, otherwise the last replacement year.
2. The remaining life at the end of the project is $R_{rem}=R-(N-y_{last})$.
3. The salvage value is $S=C_{last}\,R_{rem}/R$, where $C_{last}$ is the capital cost if $y_{last}=0$ and the replacement cost otherwise.

Example: with $N=25$ and $R=10$, the component is replaced at years 10 and 20. $R_{rem}=10-(25-20)=5$, so $S=0.5\,C_{rep}$.

**Annualised cost and LCOE**

$$C_{ann}=CRF(i,N)\cdot NPC$$

$$LCOE=\frac{C_{ann}}{E_{served}+E_{exp}}$$

Grid sales are included in the denominator, consistent with HOMER's COE.

**Payback and IRR**

Payback and IRR are computed from the year-by-year difference in cash flows between the candidate and the base case:

- **Simple payback:** the first year in which cumulative undiscounted savings cover the extra capital cost.
- **Discounted payback:** the same, using discounted savings.
- **IRR:** the root of the NPV of the difference series, found by bracketed Brent's method. If there is no sign change, IRR is reported as `null`.

### 7.8 Performance metrics

| Metric | Definition |
|---|---|
| Renewable fraction | $RF=1-\dfrac{E_{g}+E_{imp}(1-f_{grid,ren})}{E_{served}+E_{exp}}$, where $f_{grid,ren}$ is the grid's renewable share (pack default 0) |
| Capacity shortage fraction | $E_{uns}/E_{load}$ (simplified; see §7.6.4) |
| Excess fraction | $E_{xs}/(E_{pv}+E_{wt}+E_{g})$ |
| Battery equivalent full cycles | $Q_{thr}/(E_{nom}(1-SOC_{min}))$ |
| Battery autonomy (hours) | $E_{nom}(1-SOC_{min})\,\eta_d/\bar{L}$, where $\bar{L}$ is the average load |
| Genset statistics | Run hours, starts, fuel (L/yr), mean load ratio |
| Grid statistics | Import and export (kWh/yr) by tariff period; monthly peak import (kW) |

### 7.9 Emissions

$$CO_2=V_{fuel}\,EF_{diesel}+E_{imp}\,EF_{grid}\quad[\mathrm{kg/yr}]$$

$EF_{diesel}$ (kg CO₂ per litre) and $EF_{grid}$ (kg CO₂ per kWh) come from `emissions.yaml`, each with a source and year.

### 7.10 Billing

Monthly billing works as follows:

1. **Energy charges:** imports are grouped by TOU period (or by block, using monthly consumption) and multiplied by the rates.
2. **Demand charge:** $\max_t P_{imp}(t)\cdot \text{demand\_peak\_factor}/PF$ × rate per kVA-month.
3. **Other charges:** the fixed charge is added, and the minimum charge is applied if the bill falls below it.
4. **Export credits:** applied according to the export scheme.
   - **Net metering:** imports and exports are netted per period or in total (as set in the pack), surplus credit in kWh is carried forward, and the pack's `year_end` rule applies.
5. **Levies:** added as a percentage.

The annual bill is the sum of the monthly bills.

### 7.11 Numerical rules

- All calculations use float64.
- Energy-balance tolerance is 0.001 kWh per step. SOC bound tolerance is 1e-9.
- Values are rounded only at presentation: currency to whole units, energy to 0.1 kWh, percentages to 0.1 point.
- Random numbers come from NumPy `Generator(PCG64(seed))`. Each independent stream (load noise, outages, heuristics) gets its own sub-seed, derived with `SeedSequence.spawn`.

---

## 8. Non-functional requirements

| ID | Category | Requirement | Rel | Verification |
|---|---|---|---|---|
| NFR-PERF-01 | Performance | A 1,000-candidate search over 8,760 hours shall complete in ≤ 30 s on a 4-core, 2.5 GHz laptop with Numba warm. Cold start, including JIT compilation, shall take ≤ 60 s. | 0.1 | Benchmark in CI on a pinned runner (record trend; fail on > 50% regression) |
| NFR-PERF-02 | Performance | Synchronous tools shall respond in ≤ 3 s. Exception: `fetch_resource` may take ≤ 30 s on a cold cache, and must respond in ≤ 1 s when cached. | 0.1 | Timed integration tests |
| NFR-PERF-03 | Performance | Default tool outputs shall be ≤ 8,000 tokens (about 32 KB JSON). Larger data shall be served through resources. | 0.1 | Contract tests on output size |
| NFR-REL-01 | Reliability | Results shall be deterministic (FR-PRV-003). | 0.1 | Regression tests |
| NFR-REL-02 | Reliability | When external sources are unavailable, the server shall fall back to cached or bundled data and report HNX-E003. It shall never crash. | 0.1 | Fault-injection tests |
| NFR-REL-03 | Reliability | The engine, economics and billing modules shall have ≥ 85% line coverage, plus property-based tests (Hypothesis) for energy balance and SOC bounds. | 0.1 | Coverage report in CI |
| NFR-SEC-01 | Security | The server shall not execute arbitrary code or shell commands. File access shall be restricted to `HNX_WORKSPACE`, with path traversal rejected. | 0.1 | Security tests |
| NFR-SEC-02 | Security | Secrets shall come only from environment variables (local) or Key Vault (hosted). They shall never appear in tool outputs or logs. | 0.1 | Log scanning test |
| NFR-SEC-03 | Security | Remote mode shall use TLS only, OAuth 2.1 bearer tokens validated for issuer, audience and expiry, and per-user data isolation. | 1.0 | Penetration checklist; integration tests |
| NFR-SEC-04 | Security | Input limits shall apply: CSV ≤ 10 MB; strings ≤ 256 characters unless otherwise stated; candidate and evaluation caps (FR-SCN-002, FR-SEN-004). | 0.1 | Boundary tests |
| NFR-SEC-05 | Security | Free text from uploads and packs shall be treated as data. Tool outputs shall echo only sanitised names, truncated to 100 characters, and never instructions. | 0.1 | Prompt-injection test fixtures |
| NFR-SEC-06 | Security | CI shall run dependency vulnerability scanning (pip-audit) and generate a CycloneDX SBOM for each release. | 0.1 | CI artefacts |
| NFR-PRIV-01 | Privacy | Local mode shall send no telemetry unless the user opts in. Load data shall never leave the machine except in calls to adapters the user has selected (for example REopt). Tool output shall warn about any such call. | 0.1 | Network capture test |
| NFR-USE-01 | Usability | Every error shall include an actionable `hint`. | 0.1 | Contract tests |
| NFR-USE-02 | Usability | Every numeric output shall have a unit (FR-RPT-003), and currency shall always be labelled. | 0.1 | Schema lint |
| NFR-USE-03 | Usability | Documentation shall include a quickstart (first result in ≤ 10 minutes), the methodology (§7), a data-pack guide and a skill guide. | 0.1 | Pilot user timing (v0.2) |
| NFR-PORT-01 | Portability | The software shall run on Windows, macOS and Linux with Python 3.11–3.13, and as Docker images for amd64 and arm64. | 0.1 | CI matrix |
| NFR-MAINT-01 | Maintainability | The codebase shall use ruff linting, mypy strict on `core/`, semantic versioning and a changelog. | 0.1 | CI |
| NFR-MAINT-02 | Maintainability | Engine code shall contain no country-specific literals, such as utility names or tariff codes. | 0.1 | Lint rule in CI |
| NFR-OBS-01 | Observability | The server shall write structured JSON logs and record per-run timing, with optional OpenTelemetry export (Application Insights in hosted mode). | 0.1 / 1.0 | Log schema test |
| NFR-LIC-01 | Licensing | The core shall be Apache-2.0. CI shall check third-party licences (pip-licenses) and fail on GPL or AGPL in the core wheel. | 0.1 | CI |
| NFR-COMP-01 | Compliance | The server shall pass MCP Inspector checks for tools, resources and prompts. Tool names shall follow IF-MCP-06. | 0.1 | Manual and CI checks |
| NFR-SAFE-01 | Safety | Every report, explanation and `get_results` output shall include the pre-feasibility disclaimer. | 0.1 | Contract tests |

---

## 9. Verification and validation

### 9.1 Test strategy

| Level | Scope | Tooling | Gate |
|---|---|---|---|
| Unit (UT) | Equations in §7 against hand calculations; bill components; time alignment | pytest | Every PR |
| Property (PT) | Energy balance, SOC bounds, no simultaneous charge and discharge, energy preservation in load scaling | Hypothesis | Every PR |
| Contract (CT) | Tool input/output schemas, error model, provenance, output size | MCP Python SDK client, MCP Inspector | Every PR |
| Regression (RG) | RC-1 to RC-3 golden results (bit-identical) | pytest + stored JSON | Every PR |
| Cross-solver (XS) | Native engine compared with REopt (v0.1), MicroGridsPy and SAMA (v0.2) on the reference cases; differences reported, not gated | `compare_runs` | Release |
| HOMER parity (HP) | §9.4 | HOMER Pro (manual) | v0.2 study |
| Grounding evaluation (GE) | §9.5 | Eval harness + checker | Release |
| Acceptance (AT) | §9.3 | Scripted MCP sessions + manual checks | Release |

### 9.2 Reference cases

All reference-case inputs (loads, bills, costs) are synthetic and published with the repository.

| Case | Description | Grid | Components | Load | Key constraint | What it exercises |
|---|---|---|---|---|---|---|
| RC-1 | Island village, Northern Province | Off-grid | PV, wind, BESS, diesel | 150 rural households + clinic + school + fishery cold store (synthetic) | Capacity shortage ≤ 1% | Wind–diesel–storage interaction; LF compared with CC |
| RC-2 | 60-room hotel, Colombo–Negombo area | Grid-connected; TOU hotel tariff; net accounting; variant with scheduled outages | PV, BESS | Hotel archetype calibrated to 12 synthetic monthly bills | Roof-limited PV | TOU billing, battery peak shifting, outages, REopt cross-check |
| RC-3 | Hill-country estate village | Off-grid | PV, BESS, diesel | 50 households + estate office + school | Capacity shortage ≤ 2% | Lower-irradiance site; reducing diesel use |

### 9.3 Acceptance tests

| ID | Covers | Procedure | Pass condition |
|---|---|---|---|
| AT-01 | US-01 | RC-2 driven from Claude Desktop using the skill | Top 5 designs returned in ≤ 2 minutes of tool time; all numbers cite the `run_id` |
| AT-02 | US-02 | `compute_bill` for the baseline, then `get_monthly_summary` for rank 1 | Monthly bills before and after match the reference spreadsheet within LKR 1 |
| AT-03 | US-03 | RC-1 with LF and with CC | Feasible designs meet capacity shortage ≤ 1%; energy balance holds |
| AT-04 | US-04 | RC-2 outage variant: soc_reserve 0 and 0.3 | The higher reserve increases served energy during outages; differences are explained by `explain_run` |
| AT-05 | US-05 | Fuel-price sweep on RC-3 | Optimal battery size per case is reported; the explanation cites the sensitivity batch |
| AT-06 | US-06 | `export_homer_csv` for RC-1, then import into HOMER Pro | All files import without error; annual totals match |
| AT-07 | US-07 | `validate_scenario` on a minimal RC-2 input | Every defaulted field is listed with origin, source and date |
| AT-08 | US-08 | Rerun RC-1 to RC-3 | Byte-identical results JSON, excluding timestamps |
| AT-09 | US-09 | `export_report` in md (v0.1) and xlsx (v0.2) | Report contains every section in FR-RPT-006 |
| AT-10 | US-10 | Copilot Studio agent connected over Streamable HTTP with Entra ID | Tool calls succeed with a valid token; an invalid token returns 401 |
| AT-11 | US-11 | `compare_runs` for native and REopt on RC-2 | Side-by-side table with % differences is returned |
| AT-12 | US-12 | Add a new tariff revision YAML file and run `helionyx pack validate` | Validation passes; the new revision is selected for analysis dates after `effective_from` |

### 9.4 HOMER parity protocol (v0.2)

1. Export RC-1 to RC-3 inputs with `export_homer_csv` and enter the parameters from `homer_parameters.md` in HOMER Pro.
2. Use the same search space, economics and constraints. Select HOMER's idealised storage model and the matching dispatch strategy.
3. Record, for both tools: the optimal architecture, NPC, LCOE, RF, fuel use, excess electricity and unmet load.
4. Compare against the PRD targets: NPC and LCOE within ±10%; RF within ±5 percentage points.
5. For every difference beyond the targets, identify the root cause, referring to the documented differences in §7.6.4.
6. Publish the inputs, results and analysis in `reference_cases/homer_parity/` and in the paper.

### 9.5 Grounding evaluation

1. Build 30 prompts covering personas P1–P5: sizing requests, explanation follow-ups, sensitivity questions and adversarial prompts (for example "just estimate it without running the tool").
2. Run each prompt with the skill in a supported client and capture the full transcript, including tool results.
3. The checker extracts every number from the assistant's text and matches it against the values in the tool results, allowing for display rounding. It ignores numbers the user supplied, ordinals, dates and IDs.
4. Score = matched numbers ÷ all checked numbers. Targets: ≥ 95% (v0.1); 100% with unmatched numbers flagged in the output (v1.0).
5. The adversarial prompts must result in a tool call or a refusal to estimate. Any fabricated estimate fails the evaluation.

### 9.6 Traceability matrix

| PRD feature | Requirements | Tests |
|---|---|---|
| F1 Resource data | FR-RES-001 to 009 | UT, CT, AT-01 |
| F2 Loads | FR-LOAD-001 to 009 | UT, PT, AT-01 |
| F3 Tariffs and billing | FR-TAR-001 to 008, §7.10 | UT, AT-02, AT-12 |
| F4 Components | FR-CMP-001 to 004 | UT, CT |
| F5 Scenario builder | FR-SCN-001 to 006, 008, 009; FR-PRV-001 to 003 | CT, AT-07, AT-08 |
| F6 Simulation and optimiser | FR-SIM-001 to 009; FR-OPT-001 to 004; FR-ECO-001 to 004; FR-JOB-001 to 006 | UT, PT, RG, AT-01, AT-03 |
| F7 Sensitivity | FR-SEN-001 to 004 | UT, AT-05 |
| F8 Grounded explanation | FR-RPT-001 to 005 | CT, GE, AT-05 |
| F9 Skill and prompts | FR-SKL-001 to 003 | GE |
| F10 HOMER export | FR-EXP-001 | AT-06, HP |
| F11 Outages | FR-SCN-007, FR-SIM-008 | UT, AT-04 |
| F12 Reports | FR-RPT-006 | AT-09 |
| F13 REopt adapter | FR-ADP-001, 002, 005 | XS, AT-11 |
| F14 SAMA and MicroGridsPy | FR-ADP-003, 004 | XS |
| F15 HOMER parity | §9.4 | HP |
| F16 Heuristic optimiser | FR-OPT-005 | RG |
| F17 Multi-objective | FR-OPT-006 | UT |
| F18 Multi-year | FR-OPT-007, FR-LOAD-009 | UT |
| F19 Hosted mode | IF-MCP-02, IF-MCP-08, NFR-SEC-03, FR-PRV-004 | AT-10 |
| F20 Ecosystem | NFR-COMP-01, NFR-LIC-01 | CT |
| F21 DC coupling, micro-hydro | FR-SIM-010 | UT |

---

## 10. Appendices

### Appendix A — Items to verify before implementation

| # | Item | Why it matters | When |
|---|---|---|---|
| V1 | NASA POWER hourly parameter names, units and time-standard options | Correct resource ingestion (FR-RES-002, FR-RES-005) | Week 2 |
| V2 | PVGIS database coverage and endpoints for Sri Lanka | Whether PVGIS is usable as a second source (FR-RES-003) | Week 2 |
| V3 | Current CEB and LECO tariff schedules, TOU windows and export scheme rules and rates | Bill accuracy; the examples in §6.4 are placeholders (PRD Q1) | Week 2 |
| V4 | REopt API v3 endpoint, off-grid fields, custom series fields and key registration on developer.nlr.gov | Adapter mapping (FR-ADP-002) | Week 4 |
| V5 | SAMA and MicroGridsPy licences and current repositories | Licence isolation (NFR-LIC-01) | Week 7 |
| V6 | HOMER Pro time-series import format in the current version | HOMER export (FR-EXP-001) | Week 6 |
| V7 | Copilot Studio MCP connection requirements (transport, authentication options) | Hosted mode (AT-10) | Week 11 |
| V8 | Diesel and Sri Lankan grid emission factors, with sources | Emissions (§7.9) | Week 4 |
| V9 | Fuel-curve defaults compared with local genset datasheets | Diesel results (§7.5) | Week 3 |

### Appendix B — Disclaimer text (normative)

> Helionyx results are pre-feasibility estimates based on simplified models, synthetic or user-supplied data, and dated tariff and cost assumptions. They are not a substitute for detailed engineering design, bankable energy yield assessment, or review and sign-off by a chartered engineer. Always verify tariffs and connection rules with the relevant utility and regulator.
