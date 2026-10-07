# HOMER Pro parity study (v0.2)

Kit for a parity study against HOMER Pro. The study itself needs a HOMER Pro
licence (student or academic) and is run by hand; this folder holds the inputs, the
results template and the comparison tool.

## Protocol

1. Export the inputs of each reference case:

   ```bash
   helionyx run reference_cases/rc1_island_village.yaml      # note the scenario ID printed
   helionyx export homer <scenario_id> reference_cases/homer_parity/rc1
   ```

   Repeat for RC-2 and RC-3. Each folder gets `load_kw.txt`, `ghi_kw_m2.txt`, `temp_c.txt`,
   `wind_m_s.txt` and `homer_parameters.md`. The series follow the HOMER Pro import format
   (one value per line, 8,760 rows from midnight on 1 January, GHI in kW/m²).
2. In HOMER Pro, import the four series, enter every value from `homer_parameters.md`, use the
   same search space, economics and constraints, choose HOMER's **idealised storage model** and
   the same dispatch strategy (load following or cycle charging), and set operating reserve to 0 %.
3. Record HOMER's optimal design in a copy of `homer_results_template.csv`: sizes, NPC, LCOE
   (HOMER's COE), renewable fraction, fuel, excess electricity, unmet load (`capacity_shortage_pct`),
   the HOMER version and any notes. Use the same currency as Helionyx (LKR).
4. Compare:

   ```bash
   helionyx parity compare reference_cases/homer_parity/homer_results.csv --output reference_cases/homer_parity/report.md
   ```

   The report shows each metric against these targets: NPC and LCOE within ±10 %, renewable
   fraction within ±5 percentage points. Fuel, excess and unmet load are reported without a target.
5. For every difference beyond a target, find the cause and write it up, starting from the
   documented differences in `hnx://docs/methodology` §11 (dispatch priority, no operating reserve,
   idealised battery, single genset, hourly step, AC coupling).
6. Publish the filled CSV, the report and the analysis here and in the paper.

The targets are a hypothesis to test, not a release gate. A divergence that is
understood and explained is a valid result.

## Status

The tooling, inputs and template are ready. HOMER Pro results have not been entered yet:
they need HOMER Pro access.
