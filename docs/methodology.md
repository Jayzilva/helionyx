# Methodology

The normative calculation method is in
[`src/helionyx/templates/methodology.md`](../src/helionyx/templates/methodology.md). The MCP
server serves the same file as the resource `hnx://docs/methodology`, so assistants and
users read one source. It implements SRS §7 (time base and energy balance, PV, wind,
battery, genset, dispatch, economics, metrics, emissions, billing and numerical rules) and
lists the documented differences from HOMER Pro.

## Implementation notes

These decisions refine the SRS where it leaves details open (see
[IMPLEMENTATION_PLAN.md §2](IMPLEMENTATION_PLAN.md)):

- **D6 — Year boundary in time alignment.** Converting UTC data to Asia/Colombo civil time
  (UTC+05:30) treats the year as circular: the first local half-hour of 1 January uses the
  last UTC hour of 31 December of the same year. This is recorded in provenance. The error is
  negligible for pre-feasibility work and avoids a second API call.
- **D7 — Cycle-charging hold.** `cc_hold_until_setpoint` keeps the genset running only in
  deficit hours. In surplus hours the genset is always off, because running it into a
  renewable surplus would only create excess energy.
- **D8 — Block tariffs.** Block (slab) energy charges are incremental. Each slab may carry
  its own fixed charge, applied when the month's consumption ends in that slab. This matches
  the CEB domestic structure with a single schema.

Other conventions worth knowing:

- An hour belongs to a TOU period or outage window when the window contains the hour's
  midpoint (so 18:30 windows align cleanly with hourly steps).
- Day types follow the 2023 calendar (1 January is a Sunday); the `lk` pack treats Saturday
  and Sunday as weekend days.
- Converter cost is charged per kW of battery inverter power; the PV inverter is in the PV
  cost (D9).
