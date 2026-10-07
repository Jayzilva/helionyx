# Reference cases

Synthetic, published reference cases used for regression tests, cross-solver checks
and the v0.2 HOMER parity study (SRS §9.2). Each file is a self-contained *study file*:
`helionyx run reference_cases/rc2_hotel_negombo.yaml`.

| Case | File | Grid | Components | Key constraint |
|---|---|---|---|---|
| RC-1 | `rc1_island_village.yaml` | Off-grid | PV, wind, BESS, diesel | Capacity shortage ≤ 1 % |
| RC-2 | `rc2_hotel_negombo.yaml` | Grid-connected, TOU hotel tariff, net accounting | PV, BESS | Roof-limited PV (1,200 m²) |
| RC-2o | `rc2_hotel_negombo_outages.yaml` | As RC-2 with scheduled 18:30–20:30 outages | PV, BESS | — |
| RC-3 | `rc3_estate_village.yaml` | Off-grid | PV, BESS, diesel | Capacity shortage ≤ 2 % |

The three sites match the NASA POWER 2023 samples bundled in the `lk` pack, so the
cases run offline. Tariff and cost values in the pack are **unverified placeholders**;
results are for regression and method checks only.

`expected/` holds golden results produced by `tests/test_regression.py`
(regenerate with `HNX_UPDATE_GOLDEN=1 pytest tests/test_regression.py`).
