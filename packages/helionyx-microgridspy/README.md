# helionyx-microgridspy

Cross-check adapter that solves a Helionyx scenario with
[MicroGridsPy](https://github.com/MicroGridsPy/MicroGridsPy) (linopy + HiGHS, typical-year LP).

It is a **separate package** because MicroGridsPy is licensed under EUPL-1.2, while the
Helionyx core is Apache-2.0. The core never imports it; it runs this program as a
subprocess and exchanges JSON files (`helionyx-adapter-io/1`, see `docs/adapters.md`):

```bash
pip install ./packages/helionyx-microgridspy      # into any Python ≥ 3.10 environment
helionyx-microgridspy run input.json output.json
```

If the program is not on the `PATH` of the Helionyx server, set
`HNX_MICROGRIDSPY_CMD` to its full path. Then call `run_optimization` with
`solver: "microgridspy"` and compare with `compare_runs`.

## Mapping and known differences

| Helionyx | MicroGridsPy |
|---|---|
| Hourly load, PV AC output per kWp, turbine output per unit | `load_demand.csv`, `resource_availability.csv` (capacity factors; PV inverter efficiency and DC/AC set to 1 because Helionyx's series already include them) |
| Real discount rate | `wacc` of every technology |
| Capital, O&M per year, lifetime | `specific_investment_cost_*`, `fixed_om_share_per_year` (= O&M / capital), lifetime |
| Battery round-trip efficiency, converter efficiency, minimum SOC, kW/kWh | charge and discharge efficiency (√RTE × converter), depth of discharge, C-rates |
| Genset fuel curve | full-load efficiency from `F0 + F1` and diesel LHV 9.9 kWh/L |
| Hourly TOU energy price, export rate | `grid_import_price.csv`, `grid_export_price.csv` (demand and fixed charges are not modelled) |
| `max_capacity_shortage`, `min_renewable_fraction` | `max_lost_load_fraction`, `min_renewable_penetration` |

- MicroGridsPy sizes continuously (LP) and dispatches with perfect foresight.
- Costs are annualised per component with `CRF(wacc, lifetime)`; replacement costs that differ
  from the initial cost and salvage value are not modelled. The adapter reports
  `NPC = total annual cost / CRF(real rate, project life)` so it can be compared with Helionyx.
- Scheduled or random grid outages are not passed on; the grid is treated as always available.
