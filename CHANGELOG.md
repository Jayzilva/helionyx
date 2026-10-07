# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). Data packs use CalVer (`YYYY.MM.patch`).

## [Unreleased] — 1.0.0

Development continues on branch `feat/v1.0`; scope in `docs/IMPLEMENTATION_PLAN.md` §7.

### Added

- MCPB bundle (`mcpb/`, uv runtime): installs `helionyx` from PyPI for one-click installs in
  Claude Desktop and for Smithery, which now takes MCPB bundles or remote URLs.

### Removed

- `smithery.yaml`: Smithery no longer builds servers from a repository configuration.

## [0.3.0] — 2026-10-07 — Multi-year and packaging

### Added

- **Load growth** (FR-LOAD-009): `multi_year.load_growth_rate` scales year *y* load by
  (1 + g)^(y−1).
- **Capacity expansion** (FR-OPT-007): up to three `multi_year.expansion` stages, each adding
  searched PV, wind, battery or genset capacity in a given year. Year-by-year economics
  (`evaluate_years`), staged capital, per-bank replacement and salvage, and reliability
  constraints checked in every sampled year. Results carry a `multi_year` block per candidate.
- Published to PyPI (`uvx helionyx serve`); MCP Registry `server.json`, Glama and Smithery
  configuration; `CITATION.cff`, `DISCLAIMER.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue
  and pull-request templates; a trusted-publishing release workflow.

### Changed

- The Docker image starts the MCP server over stdio by default; `docker-compose.yml` runs
  Streamable HTTP and requires `HNX_API_KEY`.
- The REopt API key is sent in the `X-Api-Key` header instead of the URL, so it cannot appear
  in HTTP request logs.

## [0.2.0] — 2026-10-07 — Validation

Released with placeholder data: tariff rates, emission factors, fuel-curve defaults and LKR
component costs in the `lk` pack are still marked `unverified` (HNX-W001). The HOMER parity
study and the live grounding-evaluation run are deferred; pilot feedback comes from mock
pilot users.

### Added

- **Two-variable sensitivity grids** (FR-SEN-002): `run_sensitivity` accepts two variables and
  reports the optimal architecture (for example `PV+BESS`) and design in every cell, with an
  NPC elasticity per variable.
- **Excel reports** (FR-RPT-006): `export_report` with `format: "xlsx"` writes a workbook with
  Summary, Assumptions, Top designs, Cost breakdown, Monthly, Sensitivity and Provenance sheets.
- **Heuristic optimiser** (FR-OPT-005): `solver: "heuristic"`, a seeded multi-start pattern
  search for search spaces above the enumeration limit (up to 50 million candidates). It reaches
  the enumerated optimum, or a design within 1 % of its NPC, on RC-1 and RC-3 with a fraction of
  the evaluations. `get_results.search` reports the method and evaluation count.
- **Pareto front** (FR-OPT-006): new MCP tool `get_pareto_front` over NPC, annual CO₂ and
  capacity shortage.
- **Partial-year measured loads** (FR-LOAD-007): `import_timeseries` with `extend_partial`
  extends four weeks or more of measured load to a full year by day type, optionally scaled to
  monthly bills; the result is labelled partly synthetic (HNX-W002).
- **MicroGridsPy and SAMA cross-check adapters** (FR-ADP-003, FR-ADP-004) as separately
  licensed packages under `packages/` (`helionyx-microgridspy`, EUPL-1.2;
  `helionyx-sama`, AGPL-3.0). The core talks to them through a JSON subprocess protocol
  (`helionyx-adapter-io/1`, `docs/adapters.md`) and never imports them.
- **Data-pack releases** : `helionyx pack build` and `helionyx pack update` (SHA-256 verified,
  newer CalVer installs override the bundled pack).
- **HOMER parity kit** (F15): protocol, results template and `helionyx parity compare`
  against the PRD targets (`reference_cases/homer_parity/`).
- **Grounding transcript harness**: `evals/grounding/run_eval.py` runs the 30 prompts through
  Claude with the Helionyx tools and writes transcripts for `helionyx eval grounding`.
- **Mock pilot users** (`tests/test_pilot_mock.py`): an EPC engineer (P1) and a planner (P4)
  replay their user stories over MCP and check the PRD target of ranked results within five
  minutes.
- **Bearer-key authentication for HTTP mode** (`HNX_API_KEY`, development only); the CLI
  refuses a non-local bind without it.

### Changed

- Search spaces above `max_candidates` no longer fail at `create_scenario`; they raise a
  warning, and enumeration refuses them with a hint to use the heuristic solver.
- REopt adapter: zeroes US tax incentives and the battery fixed cost, sets PV degradation to 0,
  maps converter efficiencies, scales money into REopt's USD-sized input ranges and back, and
  returns HNX-E007 on an API rate limit. Verified against the live API on RC-2.
- HOMER export notes: the file format follows the HOMER Pro manual (import test in HOMER still
  part of the parity study).

## [0.1.0] — MVP (branch `feat/mvp-v0.1`)

### Added

- Python package `helionyx` with an MCP server (stdio and local Streamable HTTP), a Typer CLI
  and a Claude skill.
- 21 MCP tools covering sites, resource data, loads, tariffs and bills, components,
  scenarios, optimisation jobs, results, explanations, monthly summaries, sensitivity,
  run comparison and exports; 9 resources; 6 prompts; structured `HNX-Exxx` errors.
- Resource module: NASA POWER and PVGIS clients, CSV import, on-disk HTTP cache, offline
  mode, UTC → local time alignment, leap-day removal and gap filling.
- Load module: archetype synthesis, seeded variability, monthly calibration, composites and
  measured-load import.
- Numba dispatch kernel (PV via pvlib, wind, battery, genset; load following, cycle
  charging and grid-connected TOU strategy) with parallel enumerative search.
- Economics (NPC, LCOE, replacements, salvage, payback, IRR, emissions) and a bill engine
  (flat, block and TOU energy; demand, fixed and minimum charges; levies; net metering, net
  accounting and net plus).
- Scenario builder with pack defaults, assumption audit, validation rules, hashing,
  versioning, YAML round-trips and grid outage masks.
- One-variable sensitivity analysis with NPC elasticities.
- Exports: HOMER-importable series and parameter sheet, Markdown report, hourly time
  series, scenario YAML.
- REopt API v3 adapter with rate limiting and fixture replay.
- Grounding checker (`helionyx eval grounding`).
- `lk` country pack 2026.10.0: CEB and LECO tariff structures (placeholder rates),
  component library (placeholder costs), 11 load archetypes, emission factors, defaults and
  bundled NASA POWER 2023 samples for three sites.
- Reference cases RC-1 to RC-3 (plus an RC-2 outage variant).
- Documentation: README, quickstart, methodology, data-pack guide, skill guide and
  implementation plan; CI workflow, Dockerfile and contributor guide.

### Known limitations

- Tariff, cost and emission values in the `lk` pack are unverified placeholders.
- Excel reports, two-variable sensitivity and additional solver adapters arrive in v0.2.
- HTTP mode has no authentication until v1.0.
