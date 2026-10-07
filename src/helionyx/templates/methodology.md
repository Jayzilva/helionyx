# Helionyx methodology (v0.1)

This is the normative calculation method of Helionyx. Every number
Helionyx reports comes from these equations, applied deterministically.

## 1. Time base and energy balance

The model simulates 8,760 hourly steps of one representative, non-leap year in
local civil time. Powers are hourly averages in kW, so energy per step is kW × 1 h.
At every step the AC bus balances:

    P_pv + P_wt + P_g + P_imp + P_dis + P_uns = L + P_ch + P_exp + P_xs

All terms are ≥ 0, and the battery never charges and discharges in the same hour.
The simulation checks the balance at every step; the largest error is reported as
`max_energy_balance_error_kwh` (tolerance 0.001 kWh).

Day types (weekday/weekend) follow the 2023 calendar. A TOU period or outage window
applies to an hour when the window contains the hour's midpoint (for example, the
hour 18:00–19:00 belongs to a period starting at 18:30).

## 2. Resource data

- NASA POWER hourly data (GHI, 2 m temperature, 10 m and 50 m wind) is requested in UTC.
- Local hours are built from UTC hours. For Asia/Colombo (UTC+05:30) each local hour is the
  mean of the two overlapping UTC hours. The series is treated as circular at the year boundary.
- 29 February is dropped so every series has 8,760 steps.
- Gaps of up to 3 h are filled linearly; longer gaps only on request, with the same-hour mean
  of the three days before and after. Filled values raise warning HNX-W003.

## 3. PV

    P_dc = Y_pv · f_pv · (G_T / 1000) · [1 + α_P (T_c − 25)]
    P_pv = min(η_inv · P_dc, Y_pv / r_dc/ac)

G_T comes from pvlib: solar position at the hour midpoint, Erbs decomposition of GHI,
and Hay–Davies transposition (albedo 0.2 by default). Cell temperature uses the Faiman model.

## 4. Wind

Hub-height speed uses the logarithmic law from the anemometer height (10 m or 50 m)
closest to the hub, with roughness length z0 (default 0.03 m). Output is interpolated on
the power curve (zero outside it), multiplied by the standard-atmosphere air-density
ratio at the site elevation, the availability factor and the number of turbines.

## 5. Battery (idealised energy reservoir)

    E(t+1) = E(t) + η_c P_ch − P_dis / η_d,   η_c = η_d = η_conv · √η_rt
    SOC_min · E_nom ≤ E ≤ E_nom,   P_ch, P_dis ≤ P_b,max = E_nom × power_kw_per_kwh

Lifetime = min(float life, lifetime throughput / annual throughput).

## 6. Diesel genset

When running, r_min · Y_g ≤ P_g ≤ Y_g and fuel use is F = F0 · Y_g + F1 · P_g (L/h).
Lifetime = min(rated hours / annual run hours, calendar life).

## 7. Dispatch

- **Surplus** (renewables ≥ load): charge the battery, then export (if an export scheme
  applies and the grid is up), then curtail.
- **Deficit, grid available:** discharge the battery only in the configured discharge periods
  (default: tariff peak) down to the outage reserve; import up to the import limit; then the
  genset (load following); then unserved. Optional grid charging in the charge periods.
- **Deficit, off-grid or outage:** the battery serves the load if it can (down to SOC_min).
  Otherwise the genset runs:
  - *Load following:* P_g = clip(N, r_min Y_g, Y_g); surplus genset energy charges the battery.
  - *Cycle charging:* P_g = clip(N + charge-to-set-point, r_min Y_g, Y_g); with
    `cc_hold_until_setpoint` the genset keeps running while SOC is below the set-point (default 0.8).
- **Net plus:** all renewable output is exported and the load is served from the grid
  (batteries not allowed with net plus in v0.1).

## 8. Economics

Real discount rate i = (i′ − f) / (1 + f). Cash flows are in constant currency;
fuel and grid costs may escalate at a real rate e as C1 (1 + e)^(y−1).

    NPC = C_cap + Σ_y (O&M + fuel + grid purchases − grid sales)_y / (1+i)^y
              + Σ_k C_rep / (1+i)^(y_k) − S / (1+i)^N

Replacements happen at y_k = k·R < N (fractional years allowed). Salvage is linear:
S = C_last · R_rem / R with R_rem = R − (N − y_last).

    CRF = i(1+i)^N / ((1+i)^N − 1)
    LCOE = CRF · NPC / (E_served + E_export)

Simple and discounted payback and IRR (Brent's method) come from the year-by-year
cash-flow difference with the base case: grid-only for grid-connected scenarios,
diesel-only (smallest searched genset that covers the peak) for off-grid scenarios
with a genset.

## 9. Metrics

| Metric | Definition |
|---|---|
| Renewable fraction | 1 − (E_g + E_imp (1 − f_grid,ren)) / (E_served + E_exp) |
| Capacity shortage | E_unserved / E_load |
| Excess electricity | E_curtailed / (E_pv + E_wt + E_g) |
| Battery equivalent full cycles | annual throughput / (E_nom (1 − SOC_min)) |
| Battery autonomy | E_nom (1 − SOC_min) η_d / average load |
| CO₂ | fuel × EF_diesel + E_imp × EF_grid |

## 10. Billing

Imports are grouped by month and TOU period. Monthly bill = energy charge (TOU, block or
flat) + demand charge (peak import × demand_peak_factor / PF × rate per kVA-month) + fixed
charge; the minimum charge applies to that subtotal; levies are a percentage of it; export
credits are then subtracted. Net metering instead nets kWh before pricing, carries surplus
credit forward and applies the pack's year-end rule. Hourly data understates 15-minute
demand peaks; `demand_peak_factor` approximates the difference.

## 11. Documented differences from HOMER Pro

1. The battery always discharges before the genset starts; HOMER compares marginal costs.
2. There is no operating reserve; capacity shortage equals unserved energy.
3. The battery uses an idealised model, not a kinetic battery model.
4. Only one genset is modelled.
5. Hourly time step; one year represents the whole project life.
6. AC coupling only.

## 12. Numerical rules

float64 throughout; values are rounded only for display. Random numbers use NumPy
`Generator(PCG64)` with sub-seeds from `SeedSequence.spawn`. The same scenario hash and
engine version always give identical results.
