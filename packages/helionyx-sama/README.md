# helionyx-sama

Cross-check adapter that sizes a Helionyx scenario with
[SAMA / SAMAPy](https://github.com/Sas1997/SAMA) (Sadat, Takahashi & Pearce, 2023).

It is a **separate package** licensed AGPL-3.0, because SAMAPy is AGPL-3.0. The Apache-2.0
Helionyx core never imports it: it runs this program as a subprocess and exchanges JSON files
(`helionyx-adapter-io/1`, see `docs/adapters.md`).

```bash
pip install ./packages/helionyx-sama
helionyx-sama run input.json output.json
```

Set `HNX_SAMA_CMD` if the program is not on the `PATH` of the Helionyx server, then run
`run_optimization` with `solver: "sama"`. If you offer Helionyx as a network service with
this adapter installed, the AGPL obligations apply to this package and SAMAPy.

## Mapping and known differences

- **PV output:** SAMA always computes PV from irradiance and temperature. The adapter sets the
  derating factor to 1, the temperature coefficient to 0 and the irradiance to
  `PV AC output per kWp × 1000 / inverter efficiency`, so SAMA's PV output equals Helionyx's.
- **Battery:** Li-ion packs of SAMA's default 1.002 kWh; round-trip efficiency, minimum SOC and
  lifetime throughput come from the scenario.
- **Inverter:** SAMA sizes one inverter for the DC side (PV and battery). Its cost per kW is the
  Helionyx converter cost; Helionyx PV costs already include the PV inverter, so expect SAMA's
  capital to be somewhat higher.
- **Genset:** fuel curve `F0`/`F1`, minimum load ratio and lifetime hours map directly.
  Helionyx O&M per kW-year is converted to SAMA's per operating hour by dividing by 8,760.
- **Economics:** real discount rate (inflation 0), no tax credits, no budget limit.
- **Constraints:** SAMA treats loss-of-power-supply probability and renewable fraction as
  penalties, not hard limits. The adapter reports `status: "infeasible"` when the returned
  design breaks them.
- **Wind** is not mapped in this version (SAMA uses its own turbine curve).
- **Optimiser:** particle swarm, seeded from the scenario seed. `options.max_iterations`
  (default 150) and `options.population` (default 50) set the effort; a run takes a few minutes.
