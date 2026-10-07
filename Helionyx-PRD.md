# Helionyx — Product Requirements Document

| | |
|---|---|
| **Product** | Helionyx: open-source hybrid renewable energy sizing for AI assistants |
| **Document** | Product Requirements Document (PRD) |
| **Version** | 0.1 — Draft |
| **Date** | 7 October 2026 |
| **Owner** | Jayath |
| **Companion document** | `Helionyx-SRS.md` (Software Requirements Specification) |
| **Status** | Draft for review |

### Revision history

| Version | Date | Change |
|---|---|---|
| 0.1 | 7 October 2026 | Initial draft. |
| 0.1.1 | 7 October 2026 | MVP implementation started. The build plan, implementation decisions and open data items are tracked in [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md). No scope changes. |
| 0.2 | 7 October 2026 | v0.2 Validation code delivered (plan §6). Licence finding: MicroGridsPy (EUPL-1.2) joins SAMA (AGPL-3.0) as a separately distributed adapter package (§13). HOMER parity tooling ready; the study itself still needs HOMER Pro access (Q3) and the pilot users are still to be recruited. |

---

## 1. Summary

Helionyx is an open-source Model Context Protocol (MCP) server and Claude skill. It lets an engineer, student or planner size a hybrid renewable energy system (solar PV, wind, battery storage, diesel generator, utility grid) by talking to an AI assistant.

It reproduces the core HOMER Pro workflow using open-source engines that need no licence:

1. Simulate every candidate design hour by hour for a year.
2. Discard designs that break constraints.
3. Rank the rest by net present cost.

It ships with a Sri Lankan country pack containing:

- CEB and LECO tariffs and their export schemes.
- Local-time alignment for the UTC+05:30 offset.
- Grid outage modelling.
- Load archetypes for Sri Lankan building types.

The AI assistant never produces numbers itself. It asks scoping questions, calls deterministic tools and explains the results. Every figure it quotes comes from a solver run and carries a run ID that anyone can reproduce.

## 2. Problem

1. **Cost and access.**
   - HOMER Pro is the standard tool for hybrid system sizing and is taught in the University of Moratuwa CPD programme.
   - A commercial licence costs USD 1,575–4,650 per user per year.
   - The student licence (USD 59.89 for 4 months) is for non-commercial use only.
   - Small EPCs, NGOs and independent consultants in Sri Lanka often work without it.
2. **No AI access path.** HOMER's desktop application has no public automation API, and its SaaS API is a commercial arrangement, so AI assistants cannot drive it.
3. **Open tools are built for programmers.** SAMA, MicroGridsPy, REopt and PyPSA are capable, but they are libraries or APIs with no Sri Lankan data and no conversational interface.
4. **Unclaimed gap.** Existing power-system MCP servers (PowerMCP, PyPSA MCP, several PSCAD MCPs) cover power flow and simulation tools. None covers hybrid system sizing.
5. **Local inputs are scattered and error-prone.**
   - Tariffs are published in PUCSL decisions.
   - Export schemes differ: net metering, net accounting and net plus.
   - Hourly weather data in UTC does not line up with Sri Lankan clock hours.
   - Scheduled outages matter for battery sizing.
6. **Pre-feasibility work is slow.** Building 8,760-hour load profiles, setting up sensitivities and writing client explanations takes hours per proposal.

## 3. Vision and positioning

> Ask an AI assistant to size a solar–battery–diesel system for an island village in the Northern Province, and get a ranked, explainable, reproducible answer in minutes, free of charge.

Helionyx is a **pre-feasibility and teaching tool that complements HOMER**; it is not a clone of it. It:

- exports its inputs in a form HOMER can import;
- publishes a parity study against HOMER Pro;
- states openly where its simplified models differ.

### Design principles

1. **The LLM chooses tools; the solver produces numbers.** No number reaches the user unless a tool computed it.
2. **Every input has a source and a date.** Defaults are visible and auditable.
3. **The engine is country-agnostic; data packs are country-specific.** Sri Lanka (`lk`) is the first pack. Others (for example the Maldives, India or Bangladesh) can be added without changing the engine.
4. **Local-first.** It runs on a laptop over stdio with no account. A hosted mode is optional.
5. **Reproducible by default.** The same inputs and engine version always give the same results.

## 4. Goals and non-goals

### 4.1 Goals

| ID | Goal |
|---|---|
| G1 | Free, reproducible HOMER-style sizing usable from any MCP client (Claude Desktop, Claude Code, Copilot Studio, custom agents). |
| G2 | Sri Lankan defaults available out of the box, each dated and sourced. |
| G3 | Trustworthy AI explanations with no ungrounded numbers. |
| G4 | Validation against HOMER Pro on three reference cases, producing a publishable study. |
| G5 | Portfolio value across academia (a paper), career (a public repo and demo) and commercial use (a base for a proposal service). |

### 4.2 Non-goals

- EMT, transient or stability studies. These belong to the separate GridCode Copilot project.
- Detailed electrical design: cable sizing, protection, earthing, single-line diagrams.
- Bankable energy yield assessment (P50/P90) or project finance modelling.
- Real-time control, energy management systems or SCADA.
- Reading or writing `.homer` project files, or replicating HOMER's proprietary optimiser.
- Certifying compliance with CEB, LECO or PUCSL connection rules.
- A standalone web interface in the MVP. The MVP is used through chat clients and a command-line interface (CLI) only.

## 5. Users

| Persona | Who | Main need | How they use Helionyx |
|---|---|---|---|
| P1: EPC design engineer | Works at a solar EPC in the Western Province. Prepares 10–20 commercial rooftop and battery proposals a month. Has no HOMER licence. | Fast, defensible sizing under time-of-use (TOU) tariffs, and a clear explanation for the client | Chat-driven sizing, bill before and after, report export |
| P2: CPD learner or MSc student | Attends the UoM HOMER/PSCAD CPD courses or a renewable energy MSc | Understand *why* a design wins; experiment with sensitivities | Guided prompts, explanations, HOMER CSV export to compare with coursework |
| P3: Researcher or lecturer | University staff or postgraduate researcher | Reproducible, scriptable runs and transparent methods | CLI and scenario files, cross-solver comparison, parity data |
| P4: Rural electrification planner | NGO, provincial authority or development agency staff | Reliability versus cost trade-offs for off-grid villages, estates and islands; reducing diesel use | Off-grid scenarios, reliability constraints, sensitivity to fuel price |
| P5: Developer or integrator | Builds agents in Copilot Studio or custom apps | A stable remote tool API | Streamable HTTP endpoint with authentication |

## 6. User stories

| ID | Story | Persona |
|---|---|---|
| US-01 | As an EPC engineer, I describe a site, its consumption and its tariff in plain language, and within two minutes I get the five PV + battery designs with the lowest NPC. | P1 |
| US-02 | As an EPC engineer, I see the monthly electricity bill before and after the system so I can show the client their savings. | P1 |
| US-03 | As a planner, I model an off-grid island with PV, wind, battery and diesel, limiting unmet load to 1% of demand. | P4 |
| US-04 | As a planner, I model scheduled grid outages and see how much battery capacity is needed to ride through them. | P1, P4 |
| US-05 | As a learner, I ask "why did the battery get bigger when the diesel price went up?" and get an answer grounded in sensitivity results. | P2 |
| US-06 | As a researcher, I export the exact load and resource time series in HOMER's import format to compare results. | P2, P3 |
| US-07 | As any user, I see every assumption the tool filled in for me, with its source and date. | All |
| US-08 | As a researcher, I rerun a scenario by its ID and get identical results. | P3 |
| US-09 | As an EPC engineer, I export a client-ready report in Markdown and Excel. | P1 |
| US-10 | As a developer, I connect Helionyx to Copilot Studio over Streamable HTTP with authentication. | P5 |
| US-11 | As a planner, I compare the native engine's answer with REopt's for the same scenario. | P3, P4 |
| US-12 | As a data maintainer, I add a new tariff revision as a YAML file with effective dates, without changing code. | Maintainer |

## 7. Features and release priorities

Priority key: **M** = Must, **S** = Should, **C** = Could, **—** = not in that release.

| ID | Feature | Description | v0.1 MVP | v0.2 | v1.0 |
|---|---|---|---|---|---|
| F1 | Site and resource data | Hourly solar, temperature and wind data from NASA POWER and PVGIS; CSV upload; caching; alignment to Asia/Colombo civil time | M | M | M |
| F2 | Load profiles | Synthetic archetypes, calibration to monthly bills, composite loads, CSV import | M | M | M |
| F3 | Tariff library and bill engine | CEB/LECO categories; block and TOU energy charges; fixed and demand charges; export schemes; versioned YAML | M | M | M |
| F4 | Component and cost library | PV, wind, battery, diesel, converter and grid defaults in LKR/USD; versioned and overridable | M | M | M |
| F5 | Scenario builder | Scenario creation, validation and assumption audit | M | M | M |
| F6 | Native simulation and optimiser | Hourly dispatch (load following, cycle charging, grid-connected TOU strategy); HOMER-style economics; enumerative search | M | M | M |
| F7 | Sensitivity analysis | One-variable sweeps (v0.1); two-variable grids (v0.2) | M | M | M |
| F8 | Grounded explanation | Structured cost breakdown, drivers, binding constraints and comparisons, narrated by the skill with run IDs | M | M | M |
| F9 | Claude skill and MCP prompts | A workflow skill, plus prompts for clients that do not support skills | M | M | M |
| F10 | HOMER CSV export | Load and resource series in HOMER-importable files, plus a parameter sheet | S | M | M |
| F11 | Grid outage modelling | Scheduled and random grid availability | S | M | M |
| F12 | Report export | Markdown (v0.1), Excel (v0.2) | S | M | M |
| F13 | REopt adapter | Cross-check results via REopt API v3 | S | M | M |
| F14 | SAMA and MicroGridsPy adapters | Additional open-source solvers for cross-checking | — | M | M |
| F15 | HOMER parity study | Reference-case pack and a published comparison | — | M | M |
| F16 | Heuristic optimiser | For search spaces too large to enumerate | — | S | M |
| F17 | Multi-objective optimisation | Pareto front of cost, emissions and reliability | — | C | M |
| F18 | Multi-year load growth | Capacity expansion over the project life | — | — | S |
| F19 | Hosted remote mode | Streamable HTTP with OAuth (Microsoft Entra ID) on Azure Container Apps | C | S | M |
| F20 | Ecosystem packaging | MCP registry listing; contribution to PowerMCP | — | S | M |
| F21 | DC-coupled systems and micro-hydro | Additional system layouts and components | — | — | C |

## 8. MVP happy path

1. The user writes: *"Size a PV and battery system for a 60-room hotel in Negombo on the CEB hotel tariff. We have about 1,200 m² of usable roof."*
2. The skill asks up to three scoping questions, for example monthly consumption from recent bills, budget limit and export scheme.
3. The assistant calls these tools in order, then shows the assumptions it will use:
   1. `create_site`
   2. `fetch_resource`
   3. `synthesize_load` (calibrated to the bills)
   4. `get_tariff`
   5. `compute_bill` (baseline bill)
   6. `create_scenario`
   7. `validate_scenario`
4. It calls `run_optimization`, waits for the job to finish, then calls `get_results` and `explain_run`.
5. It presents the top three designs. Each shows NPC, LCOE, payback, and the bill before and after, tagged with the run ID. It also lists the defaulted assumptions.
6. The user asks for a sensitivity on battery price. The assistant calls `run_sensitivity` and explains the result.
7. The user exports a report with `export_report`.

## 9. Success metrics

| Area | Metric | Target | Release |
|---|---|---|---|
| Correctness | Hourly energy-balance error on every simulated step | ≤ 0.001 kWh | v0.1 |
| Reproducibility | Rerun of the same scenario hash with the same engine version | Identical results | v0.1 |
| Performance | 1,000-candidate search over 8,760 hours | ≤ 30 s on a 4-core laptop | v0.1 |
| Grounding | Share of numbers in assistant answers traceable to tool outputs (automated checker, 30-prompt evaluation set) | ≥ 95% in v0.1; 100% in v1.0, with any unmatched numbers flagged | v0.1 / v1.0 |
| Accuracy | NPC and LCOE compared with HOMER Pro on identical inputs, three reference cases | Within ±10% | v0.2 |
| Accuracy | Renewable fraction compared with HOMER Pro | Within ±5 percentage points | v0.2 |
| Usability | Median time from first prompt to ranked results for pilot users | ≤ 5 minutes | v0.2 |
| Adoption | GitHub stars / external contributors / pilot organisations | 50 / 3 / 2 | v1.0 |
| Academic | Parity paper submitted (MERCon or SoftwareX) | 1 | after v0.2 |
| Ecosystem | MCP registry listing and PowerMCP contribution | Done | v1.0 |

The HOMER parity targets are a hypothesis to test, not a release gate. If results diverge, the study reports where and why.

## 10. Release plan

Week 1 starts Monday 12 October 2026.

| Release | Weeks | Dates | Scope |
|---|---|---|---|
| v0.1 MVP | 1–6 | 12 Oct – 22 Nov 2026 | F1–F9, plus the F10–F13 items marked S |
| v0.2 Validation | 7–10 | 23 Nov – 20 Dec 2026 | HOMER parity study, SAMA and MicroGridsPy adapters, two-variable sensitivity, Excel reports, two pilot users |
| v1.0 | 11–16 | 21 Dec 2026 – 31 Jan 2027 | Multi-objective optimisation, hosted mode, ecosystem packaging, paper draft (includes a holiday buffer) |

### 10.1 MVP weekly plan

| Week | Deliverable |
|---|---|
| 1 | Repository, CI, Pydantic schemas, data-pack format, tariff YAML schema, reference-case definitions |
| 2 | Resource module (NASA POWER, PVGIS, CSV) with time alignment; load module with archetypes and calibration |
| 3 | Simulation engine (PV, wind, battery, genset, dispatch) with energy-balance property tests |
| 4 | Economics, enumerative optimiser, bill engine, base-case comparison |
| 5 | MCP server (tools, resources, prompts), asynchronous jobs, Claude skill |
| 6 | Sensitivity, explanation, exports, documentation, demo video, PyPI release `0.1.0` |

## 11. Dependencies

| Type | Dependency | Notes |
|---|---|---|
| Data | NASA POWER and PVGIS APIs | Free; responses cached; offline sample data shipped |
| Data | PUCSL, CEB and LECO tariff documents; fuel price notices | Curated manually into dated YAML files |
| Software | MCP Python SDK, pvlib, NumPy, Numba, pandas, Pydantic | All permissively licensed |
| Software | REopt API v3 key; SAMA; MicroGridsPy | Optional adapters; licence checks required |
| People | Domain mentor (UoM or industry) | Reviews methods in each phase |
| People | HOMER Pro access | Student or academic licence for the parity study |
| People | Two pilot users (one EPC, one planner) | Feedback during v0.2 |

## 12. Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Results diverge from HOMER because of undocumented dispatch or storage details | High | Medium | Feed identical CSV inputs into both tools; use HOMER's idealised storage model; report and explain any divergence |
| R2 | Tariff data goes out of date | High | High | Dated YAML with effective dates and sources; staleness warnings; maintainer checklist |
| R3 | The assistant states numbers that no tool produced | Medium | High | Structured outputs with run IDs; rules in the skill; grounding checker in CI |
| R4 | SAMA's GPL-3 licence obligations spread to the core | Medium | Medium | Ship the SAMA adapter as a separate optional package run as a subprocess; the core stays Apache-2.0 |
| R5 | External API outages or rate limits (REopt allows 60 runs per hour) | Medium | Medium | Caching, offline data, CSV upload; all adapters optional |
| R6 | Users treat outputs as final design sign-off | Medium | High | Pre-feasibility disclaimer in every report and explanation |
| R7 | Synthetic load profiles misrepresent real sites | High | Medium | Calibration to bills, import of measured data, "synthetic" labelling |
| R8 | Long runs exceed MCP client timeouts | Medium | Medium | Asynchronous job pattern with progress notifications |
| R9 | Scope creep into EMT studies or detailed design | Medium | Medium | Explicit non-goals; a separate GridCode Copilot project |
| R10 | The project depends on a single maintainer | High | Medium | Documentation, a contributor guide, tests and small modules |

## 13. Licensing and governance

- **Core code:** Apache-2.0.
- **Data packs:** CC BY 4.0, citing the original source for each record.
- **Copyleft engines** (SAMA, AGPL-3.0; MicroGridsPy, EUPL-1.2): separate optional packages, run as subprocesses.
- **Disclaimer:** outputs are pre-feasibility estimates. They do not replace detailed design or sign-off by a chartered engineer.

## 14. Open questions

| ID | Question | Owner | Needed by |
|---|---|---|---|
| Q1 | Which export schemes are currently open to new applicants, and at what rates? | Jayath + domain mentor | Week 2 |
| Q2 | Can anonymised measured hourly load data (hotel, factory, village) be obtained for calibration? | Jayath | Week 4 |
| Q3 | Will a UoM lecturer co-author the parity study and provide HOMER Pro academic access? | Jayath | Week 6 |
| Q4 | Is micro-hydro (relevant to estates) needed before v1.0? | Pilot users | v0.2 review |
| Q5 | Who funds compute for a public hosted demo, and what free-tier limits apply? | Jayath | Week 10 |
| Q6 | Is "Helionyx" clear for domain registration (e.g., helionyx.dev / .io) and free of trademark conflicts in energy and software classes? Name decided; PyPI and GitHub names confirmed free on 7 October 2026. | Jayath | Week 1 |
