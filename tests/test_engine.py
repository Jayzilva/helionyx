"""Simulation engine: hand-traced cases and property tests (FR-SIM-001…008, §7.1–7.6)."""

from __future__ import annotations

import numpy as np
import pvlib
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from helionyx.core.engine import dispatch as dsp
from helionyx.core.engine.pv import pv_ac_per_kwp
from helionyx.core.engine.timeaxis import HOUR_OF_DAY, HOURS, MONTH
from helionyx.core.engine.wind import turbine_output


def inputs(load, pv=None, wind=None, avail=None, period=None, n_periods=1, dis=(True,), ch=(True,), grid=0,
           export=0, grid_charging=0, cc=0, cc_hold=1, net_plus=0, eta=1.0, soc_min=0.0, soc0=1.0, soc_res=0.0,
           soc_sp=0.8, rmin=0.25, imp_max=1e9, exp_max=1e9):
    n = len(load)
    prm = np.zeros(dsp.N_PRM)
    prm[:] = [eta, eta, soc_min, soc0, soc_res, soc_sp, rmin, 0.08145, 0.246, imp_max, exp_max]
    flg = np.array([grid, export, grid_charging, cc, cc_hold, net_plus], dtype=np.int64)
    return dsp.SystemInputs(
        load=np.asarray(load, float), pv_unit=np.asarray(pv if pv is not None else np.zeros(n), float),
        wind_unit=np.asarray(wind if wind is not None else np.zeros(n), float),
        available=np.asarray(avail if avail is not None else np.ones(n), np.int8),
        period=np.asarray(period if period is not None else np.zeros(n), np.int64),
        month=MONTH[:n] if n <= HOURS else np.zeros(n, np.int64),
        discharge_mask=np.array(dis, np.bool_), charge_mask=np.array(ch, np.bool_), params=prm, flags=flg,
        n_periods=n_periods)


def test_battery_alone_then_unserved():
    inp = inputs([10.0] * 4, soc_min=0.0, soc0=1.0)
    agg, s = dsp.simulate_series(inp, 0, 0, 25.0, 100.0, 0)
    assert list(s[:, 7]) == pytest.approx([10, 10, 5, 0])     # discharge
    assert list(s[:, 8]) == pytest.approx([0, 0, 5, 10])      # unserved
    assert agg[dsp.A_UNS] == pytest.approx(15)


def test_load_following_min_load_and_charge():
    # Empty battery; genset 20 kW, min load 25 % = 5 kW; load 2 kW -> genset 5, surplus 3 charges battery
    inp = inputs([2.0, 30.0], soc0=0.0, rmin=0.25)
    agg, s = dsp.simulate_series(inp, 0, 0, 10.0, 10.0, 20.0)
    assert s[0, 3] == pytest.approx(5.0) and s[0, 6] == pytest.approx(3.0)
    # hour 2: load 30 > genset 20 -> battery covers 3 (all it holds), unserved 7
    assert s[1, 3] == pytest.approx(20.0) and s[1, 7] == pytest.approx(3.0) and s[1, 8] == pytest.approx(7.0)
    assert agg[dsp.A_FUEL] == pytest.approx(2 * 0.08145 * 20 + 0.246 * 25)
    assert agg[dsp.A_GEN_STARTS] == 1 and agg[dsp.A_GEN_HOURS] == 2


def test_cycle_charging_charges_to_setpoint_and_holds():
    # Battery 100 kWh at SOC 0 (floor 0); load 10; genset 50 -> CC target 10 + charge to 80 kWh limited by Pbmax 30
    inp = inputs([10.0] * 3, soc0=0.0, cc=1, soc_sp=0.8, rmin=0.0)
    _, s = dsp.simulate_series(inp, 0, 0, 100.0, 30.0, 50.0)
    assert s[0, 3] == pytest.approx(40.0) and s[0, 6] == pytest.approx(30.0)
    # hold: genset ran and SOC (0.3) < 0.8 -> keeps running although the battery could serve the load
    assert s[1, 3] > 0 and s[1, 7] == 0


def test_grid_discharge_only_in_peak_and_reserve():
    period = [0, 1, 1, 0]
    inp = inputs([20.0] * 4, period=period, n_periods=2, dis=(False, True), ch=(False, False), grid=1,
                 soc_res=0.5, soc0=1.0)
    _, s = dsp.simulate_series(inp, 0, 0, 20.0, 20.0, 0)
    assert s[0, 7] == 0 and s[0, 4] == pytest.approx(20)
    assert s[1, 7] == pytest.approx(10)      # down to 50 % reserve
    assert s[2, 7] == pytest.approx(0)       # reserve kept for outages
    assert s[3, 7] == 0


def test_outage_uses_full_battery_and_no_grid():
    inp = inputs([10.0] * 3, avail=[1, 0, 0], grid=1, soc_res=0.5, dis=(False,))
    _, s = dsp.simulate_series(inp, 0, 0, 20.0, 20.0, 0)
    assert s[1, 4] == 0 and s[2, 4] == 0
    assert s[1, 7] == pytest.approx(10) and s[2, 7] == pytest.approx(10)


def test_export_and_curtailment_limits():
    inp = inputs([10.0], pv=[1.0], grid=1, export=1, exp_max=30.0)
    _, s = dsp.simulate_series(inp, 100.0, 0, 0, 0, 0)
    assert s[0, 5] == pytest.approx(30) and s[0, 9] == pytest.approx(60)


def test_net_plus_exports_all_pv():
    inp = inputs([10.0], pv=[1.0], grid=1, export=1, net_plus=1)
    _, s = dsp.simulate_series(inp, 50.0, 0, 0, 0, 0)
    assert s[0, 5] == pytest.approx(50) and s[0, 4] == pytest.approx(10)


def test_grid_charging_in_charge_period():
    inp = inputs([5.0, 5.0], period=[0, 0], grid=1, grid_charging=1, dis=(False,), ch=(True,), soc0=0.0)
    _, s = dsp.simulate_series(inp, 0, 0, 10.0, 4.0, 0)
    assert s[0, 6] == pytest.approx(4) and s[0, 4] == pytest.approx(9)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(0, 10_000), pv_kw=st.floats(0, 300), n_wt=st.integers(0, 3), e_nom=st.floats(0, 800),
    c_rate=st.floats(0.1, 1.0), gen=st.sampled_from([0.0, 20.0, 60.0]), grid=st.integers(0, 1),
    cc=st.integers(0, 1), eta=st.floats(0.8, 1.0), soc_min=st.floats(0.0, 0.5), soc_res=st.floats(0.0, 0.6),
    export=st.integers(0, 1), gch=st.integers(0, 1),
)
def test_property_energy_balance_soc_bounds(seed, pv_kw, n_wt, e_nom, c_rate, gen, grid, cc, eta, soc_min, soc_res,
                                            export, gch):
    rng = np.random.default_rng(seed)
    n = 24 * 20
    load = rng.uniform(0, 80, n)
    pv = np.clip(np.sin((HOUR_OF_DAY[:n] - 6) / 12 * np.pi), 0, None) * rng.uniform(0.3, 1.0, n)
    wind = rng.uniform(0, 10, n)
    avail = (rng.random(n) > 0.1).astype(np.int8)
    period = (HOUR_OF_DAY[:n] >= 18).astype(np.int64)
    inp = inputs(load, pv, wind, avail, period, 2, (False, True), (True, False), grid, export, gch, cc, 1, 0, eta,
                 soc_min, 1.0, soc_res, 0.8, 0.25, 60.0, 40.0)
    agg, s = dsp.simulate_series(inp, pv_kw, n_wt, e_nom, e_nom * c_rate, gen)
    assert agg[dsp.A_BAL_ERR] <= 1e-3
    assert np.all(s[:, :10] >= -1e-9)
    assert np.all(s[:, 6] * s[:, 7] == 0)            # no simultaneous charge and discharge
    if e_nom > 0:
        assert agg[dsp.A_E_MIN] >= soc_min * e_nom - 1e-9 or soc_min * e_nom > e_nom
        assert agg[dsp.A_E_MAX] <= e_nom + 1e-9
    if grid == 0:
        assert agg[dsp.A_IMP] == 0 and agg[dsp.A_EXP] == 0
    assert np.all(s[avail == 0, 4] == 0) if grid == 1 else True


def test_batch_matches_series_and_thread_independent():
    import numba

    rng = np.random.default_rng(0)
    load = rng.uniform(10, 60, HOURS)
    pv = np.clip(np.sin((HOUR_OF_DAY - 6) / 12 * np.pi), 0, None) * 0.8
    inp = inputs(load, pv, None, None, None, 1, (True,), (True,), 0, 0, 0, 0, 1, 0, 0.95, 0.1, 1.0, 0, 0.8, 0.25)
    sizes = rng.uniform(0, 200, (50, 5))
    sizes[:, 1] = 0
    r1 = dsp.simulate_batch(inp, *[sizes[:, k].copy() for k in range(5)])
    prev = numba.get_num_threads()
    numba.set_num_threads(1)
    try:
        r2 = dsp.simulate_batch(inp, *[sizes[:, k].copy() for k in range(5)])
    finally:
        numba.set_num_threads(prev)
    assert np.array_equal(r1.agg, r2.agg)
    agg, _ = dsp.simulate_series(inp, *sizes[7])
    assert np.array_equal(agg, r1.agg[7])


def test_pv_model_matches_independent_pvlib_reference():
    import pandas as pd

    from helionyx.core.engine.timeaxis import local_index

    rng = np.random.default_rng(2)
    times = (local_index() + pd.Timedelta(minutes=30)).tz_localize("Asia/Colombo")
    cs = pvlib.location.Location(7.2, 79.8, "Asia/Colombo").get_clearsky(times)["ghi"].to_numpy()
    ghi = cs * rng.uniform(0.5, 1.0, HOURS)
    temp = np.full(HOURS, 28.0)
    wind = np.full(HOURS, 2.0)
    ours = pv_ac_per_kwp(ghi, temp, wind, latitude=7.2, longitude=79.8, elevation_m=0, timezone="Asia/Colombo",
                         tilt_deg=10, azimuth_deg=180, dc_ac_ratio=1.2, derating_factor=0.9,
                         temp_coeff_pct_per_c=-0.35, inverter_efficiency=0.97, faiman_u0=25, faiman_u1=6.84,
                         albedo=0.2)
    # Independent reference with pvlib's PVWatts DC model and a hand-written clip
    sp = pvlib.solarposition.get_solarposition(times, 7.2, 79.8)
    erbs = pvlib.irradiance.erbs(ghi, sp["zenith"], times)
    poa = pvlib.irradiance.get_total_irradiance(10, 180, sp["apparent_zenith"], sp["azimuth"], erbs["dni"], ghi,
                                                erbs["dhi"], dni_extra=pvlib.irradiance.get_extra_radiation(times),
                                                model="haydavies", albedo=0.2)["poa_global"].fillna(0).clip(lower=0)
    tc = pvlib.temperature.faiman(poa, temp, wind, 25, 6.84)
    dc = pvlib.pvsystem.pvwatts_dc(poa, tc, 0.9, -0.0035)
    ref = np.minimum(0.97 * np.clip(dc, 0, None), 1 / 1.2)
    assert abs(ours.sum() - ref.sum()) / ref.sum() < 0.005


def test_wind_power_curve():
    curve = [(0.0, 0.0), (3.0, 0.0), (11.0, 10.0), (25.0, 10.0)]
    v = np.array([2.0, 7.0, 12.0, 26.0])
    out = turbine_output(v, None, hub_height_m=10.0, power_curve=curve, availability=1.0, roughness_length_m=0.03,
                         elevation_m=0.0, air_density_correction=False)
    assert out == pytest.approx([0.0, 5.0, 10.0, 0.0])
