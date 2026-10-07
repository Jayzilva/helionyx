# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). Data packs use CalVer (`YYYY.MM.patch`).

## [Unreleased] — 0.1.0

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
