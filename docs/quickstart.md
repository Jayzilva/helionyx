# Quickstart

Goal: a first ranked result in under ten minutes.

> The `lk` pack's tariffs, costs and emission factors are **unverified placeholders**.
> Use the results to learn the tool, not to make decisions.

## 1. Install (2 minutes)

```bash
git clone <repository-url> helionyx
cd helionyx
uv venv
uv pip install -e ".[dev]"
```

Without uv: `python -m venv .venv` and `pip install -e ".[dev]"` inside it. Python 3.11–3.13.

## 2. Run a reference case offline (2 minutes)

```bash
helionyx run reference_cases/rc2_hotel_negombo.yaml
```

To prove it needs no network, set `HNX_OFFLINE=1` first. The `lk` pack bundles NASA POWER
2023 hourly data for three sites, and `fetch_resource` uses it automatically when a site's
coordinates match (within 0.01°) and the year is 2023:

| Sample | Latitude | Longitude | Used by |
|---|---|---|---|
| Negombo (coastal, Western Province) | 7.2083 | 79.8358 | RC-2 |
| Delft Island (Northern Province) | 9.5167 | 79.6833 | RC-1 |
| Hatton (hill country, Central Province) | 6.8916 | 80.5955 | RC-3 |

The command creates a site, loads the resource, synthesises a hotel load calibrated to 12
monthly bills, builds a scenario with 55 PV/battery candidates, simulates each for 8,760
hours and prints the top designs plus the grid-only base case. The first run includes Numba
compilation; later runs are faster.

Other commands:

```bash
helionyx results <run_id> --top 10
helionyx export report <run_id> --format md --output report.md
helionyx export homer <scenario_id> ./homer_files
```

All data is stored in the workspace (`~/.helionyx` by default; set `HNX_WORKSPACE` to change it).

## 3. Connect to Claude (2 minutes)

Claude Code:

```bash
claude mcp add helionyx -- helionyx serve
cp -r skills/helionyx ~/.claude/skills/
```

Claude Desktop — add to `claude_desktop_config.json` and restart:

```json
{ "mcpServers": { "helionyx": { "command": "helionyx", "args": ["serve"] } } }
```

## 4. The happy path (PRD §8)

Ask:

> Size a PV and battery system for a 60-room hotel in Negombo on the CEB hotel tariff. We
> have about 1,200 m² of usable roof.

The assistant should:

1. Ask up to three scoping questions (monthly consumption from bills, budget limit, export scheme).
2. Call `create_site`, `fetch_resource`, `synthesize_load` (with `monthly_kwh`), `get_tariff`,
   `compute_bill` (baseline), `create_scenario` and `validate_scenario`, then show the
   defaulted assumptions.
3. Call `run_optimization`, poll `get_job_status`, then `get_results` and `explain_run`.
4. Present the top three designs with NPC, LCOE, payback and the bill before and after
   (`get_monthly_summary`), each tagged with the run ID, plus the disclaimer.

Then try a sensitivity:

> How does the result change if batteries cost 60,000, 80,000 or 120,000 LKR per kWh?

This becomes `run_sensitivity` with:

```json
{
  "scenario_id": "scn_…",
  "variables": [{ "path": "components.bess.overrides.capital_per_unit",
                  "values": [60000, 80000, 120000] }]
}
```

followed by `get_sensitivity_results`, which returns the optimal design per value and the
NPC elasticity. Finally ask for a report (`export_report`).

## 5. Write your own study file

A study file declares the site, resource and loads next to the scenario, so it runs
without an MCP client:

```yaml
study:
  site: {name: Negombo hotel, latitude: 7.2083, longitude: 79.8358, country_pack: lk}
  resource: {source: nasa_power, year: 2023}
  loads:
    - components: [{archetype: hotel, count: 60}]
      monthly_kwh: [50000, 47000, 51000, 46000, 43000, 40000, 43000, 44000, 41000, 43000, 44000, 50000]
      seed: 42
scenario:
  name: My hotel
  grid: {mode: grid_connected, tariff_id: lk.ceb.H2, export_scheme: net_accounting}
  components:
    pv:   {sizes_kwp: {min: 0, max: 250, step: 25}}
    bess: {sizes_kwh: [0, 100, 200, 300]}
  constraints: {roof_area_m2: 1200}
```

- Each load entry accepts `components` (archetype plus one of `count`, `annual_kwh` or
  `peak_kw`), optional `monthly_kwh` (12 values), optional `variability` and `seed`.
- Sizes are a list or a `{min, max, step}` range. Fields you leave out take pack defaults,
  which `validate_scenario` lists with their source and date.
- A plain scenario file (only the `scenario` key, with existing `site_id`, `load_ids` and
  `resource_id`) is also accepted.
- Archetypes: `rural_household`, `urban_household`, `hotel`, `office`,
  `small_industry_1shift`, `small_industry_3shift`, `health_clinic`, `school`,
  `telecom_tower`, `cold_storage`, `estate_office`.

See `reference_cases/` for off-grid examples with wind and diesel.
