"""Bill engine and economics against hand calculations (FR-TAR-002…004, FR-ECO-001…003, §7.7)."""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from helionyx.core import economics as eco
from helionyx.core.billing import compute_bill
from helionyx.core.models.packs import Tariff

SRC = {"document": "test", "retrieved_at": dt.date(2026, 1, 1)}


def tariff(energy: dict, **charges) -> Tariff:
    data = {"id": "t@2026-01-01", "utility": "U", "category": "C", "name": "n", "currency": "LKR",
            "effective_from": "2026-01-01", "source": SRC,
            "charges": {"energy": energy, **charges},
            "export_schemes": {"net_metering": {"netting": "total", "credit_carry_forward": True, "year_end": "pay",
                                                "year_end_rate_per_kwh": 5.0},
                               "net_accounting": {"export_rate_per_kwh": 20.0},
                               "net_plus": {"export_rate_per_kwh": 25.0}}}
    if energy["type"] == "tou":
        data["tou_periods"] = [{"name": "day", "start": "05:30", "end": "18:30"},
                               {"name": "peak", "start": "18:30", "end": "22:30"},
                               {"name": "off_peak", "start": "22:30", "end": "05:30"}]
    return Tariff.model_validate(data)


def arr(v, p=1):
    return np.full((12, p), float(v))


def test_flat_with_fixed_min_and_levies():
    t = tariff({"type": "flat", "rate_per_kwh": 10.0}, fixed_monthly=100.0, minimum_monthly=500.0, levies_pct=10.0)
    b = compute_bill(t, "none", arr(30), arr(0), np.zeros(12))
    m = b.months[0]
    assert m.energy_charge == pytest.approx(300.0)
    assert m.minimum_adjustment == pytest.approx(100.0)  # 400 -> 500
    assert m.levies == pytest.approx(50.0)
    assert m.total == pytest.approx(550.0)
    assert b.total == pytest.approx(12 * 550.0)


def test_block_slabs_with_fixed_per_slab():
    t = tariff({"type": "block", "blocks": [{"up_to_kwh": 30, "rate_per_kwh": 6, "fixed_monthly": 100},
                                            {"up_to_kwh": 60, "rate_per_kwh": 9, "fixed_monthly": 250},
                                            {"up_to_kwh": None, "rate_per_kwh": 50, "fixed_monthly": 1000}]})
    m = compute_bill(t, "none", arr(75), arr(0), np.zeros(12)).months[0]
    assert m.energy_charge == pytest.approx(30 * 6 + 30 * 9 + 15 * 50)
    assert m.fixed_charge == pytest.approx(1000)
    m0 = compute_bill(t, "none", arr(0), arr(0), np.zeros(12)).months[0]
    assert m0.fixed_charge == pytest.approx(100) and m0.energy_charge == 0


def test_tou_and_demand():
    t = tariff({"type": "tou", "rates_per_kwh": {"day": 20, "peak": 40, "off_peak": 10}},
               demand={"per_kva_month": 1000, "power_factor_assumption": 0.9})
    imp = np.tile([100.0, 50.0, 30.0], (12, 1))
    m = compute_bill(t, "none", imp, np.zeros((12, 3)), np.full(12, 45.0), demand_peak_factor=1.1).months[0]
    assert m.energy_charge == pytest.approx(100 * 20 + 50 * 40 + 30 * 10)
    assert m.demand_charge == pytest.approx(45 * 1.1 / 0.9 * 1000)


def test_net_accounting_and_net_plus_credit():
    t = tariff({"type": "flat", "rate_per_kwh": 30.0})
    na = compute_bill(t, "net_accounting", arr(100), arr(40), np.zeros(12)).months[0]
    assert na.total == pytest.approx(100 * 30 - 40 * 20)
    npl = compute_bill(t, "net_plus", arr(100), arr(40), np.zeros(12)).months[0]
    assert npl.export_credit == pytest.approx(40 * 25)


def test_net_metering_carry_forward_and_year_end():
    t = tariff({"type": "flat", "rate_per_kwh": 30.0})
    imp = np.array([[100.0]] * 12)
    exp = np.array([[150.0]] + [[80.0]] * 11)
    b = compute_bill(t, "net_metering", imp, exp, np.zeros(12))
    # credit after each month: 50, 30, 10, then 10 kWh and 20 kWh billed
    assert [m.billed_kwh for m in b.months[:5]] == pytest.approx([0, 0, 0, 10, 20])
    assert b.year_end_payout == pytest.approx(0.0)
    exp_big = np.array([[150.0]] * 12)
    paid = compute_bill(t, "net_metering", imp, exp_big, np.zeros(12))
    assert paid.year_end_payout == pytest.approx(12 * 50 * 5.0)


def test_crf_and_real_rate():
    i = eco.real_discount_rate(0.12, 0.05)
    assert i == pytest.approx(0.07 / 1.05)
    assert eco.crf(0.1, 10) == pytest.approx(0.1627454, rel=1e-6)


def test_salvage_example_from_srs():
    # N = 25, R = 10: replaced at 10 and 20; R_rem = 5 -> S = 0.5 C_rep
    assert eco.replacement_years(10, 25) == [10, 20]
    assert eco.salvage_value(1000, 800, 10, 25) == pytest.approx(400)


def test_npc_hand_calculation_and_breakdown_sum():
    i, n = 0.08, 20
    item = eco.CostItem("pv", capital=1000.0, replacement_cost=900.0, lifetime_years=15, om_per_year=20.0)
    grid = eco.CostItem("grid", grid_purchases_per_year=100.0, grid_sales_per_year=10.0, escalation_grid=0.02)
    r = eco.evaluate([item, grid], i, n)
    om = sum(20.0 / (1 + i) ** y for y in range(1, n + 1))
    rep = 900.0 / (1 + i) ** 15
    salv = 900.0 * (15 - 5) / 15 / (1 + i) ** n
    g = sum((100.0 - 10.0) * 1.02 ** (y - 1) / (1 + i) ** y for y in range(1, n + 1))
    assert r.npc == pytest.approx(1000 + om + rep - salv + g, rel=1e-4)
    assert sum(r.by_type.values()) == pytest.approx(r.npc, rel=1e-4)
    assert r.annualised_cost == pytest.approx(eco.crf(i, n) * r.npc)


def test_fractional_replacement_year():
    reps = eco.replacement_years(7.5, 20)
    assert reps == [7.5, 15.0]


def test_payback_and_irr():
    base = [0.0] + [100.0] * 10
    cand = [300.0] + [40.0] * 10  # saves 60/yr for 300 extra capital -> 5 years
    pb = eco.payback(cand, base, 0.0)
    assert pb.simple_payback_yr == pytest.approx(5.0)
    assert pb.irr_pct is not None and 15.0 < pb.irr_pct < 16.0  # IRR of -300 + 60 x 10 is about 15.1 %
    none = eco.payback([0.0] + [100.0] * 10, base, 0.05)
    assert none.simple_payback_yr == 0.0


def test_lcoe_none_when_no_energy():
    assert eco.lcoe(100.0, 0.0, 0.0) is None
    assert math.isclose(eco.lcoe(100.0, 50.0, 50.0) or 0, 1.0)
