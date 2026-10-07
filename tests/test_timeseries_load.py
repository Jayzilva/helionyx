"""Resource preparation and load synthesis (FR-RES-005…007, FR-LOAD-001…006)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from helionyx.core.engine.timeaxis import HOURS, MONTH, period_index, window_mask
from helionyx.core.load import add_variability, archetype_shape, calibrate_monthly, scale_to_energy
from helionyx.core.timeseries import align_utc_to_local, drop_leap_day, fill_gaps, to_hourly_mean
from helionyx.errors import HelionyxError
from helionyx.infra.packs import get_pack
from helionyx.services import resource
from helionyx.services.load import LoadComponent, synthesize_load


def test_alignment_half_hour_shift_on_ramp():
    ramp = np.arange(HOURS, dtype=float)
    out, method = align_utc_to_local(ramp, 5.5)
    # local hour k = mean of UTC hours k-6 and k-5  ->  k - 5.5
    k = np.arange(100, 200)
    assert np.allclose(out[k], k - 5.5)
    assert "circular" in method


def test_alignment_preserves_annual_total():
    rng = np.random.default_rng(1)
    ghi = np.clip(rng.normal(200, 300, HOURS), 0, None)
    out, _ = align_utc_to_local(ghi, 5.5)
    assert abs(out.sum() - ghi.sum()) / ghi.sum() < 0.005


def test_whole_hour_offset_is_a_roll():
    v = np.arange(HOURS, dtype=float)
    out, _ = align_utc_to_local(v, 2.0)
    assert out[10] == 8.0


def test_leap_day_dropped():
    idx = pd.date_range("2024-01-01", periods=8784, freq="h")
    i2, v2, dropped = drop_leap_day(idx, np.ones(8784))
    assert dropped and len(v2) == HOURS


def test_gap_filling():
    v = np.arange(48, dtype=float)
    v[5:8] = np.nan
    filled, short, long, unfilled = fill_gaps(v, False)
    assert short == 3 and long == 0 and not unfilled
    assert np.allclose(filled[5:8], [5, 6, 7])
    w = np.ones(24 * 10)
    w[50:56] = np.nan
    _, _, _, unfilled = fill_gaps(w, False)
    assert unfilled == [6]
    filled, _, long, _ = fill_gaps(w, True)
    assert long == 6 and np.allclose(filled[50:56], 1.0)


def test_to_hourly_mean_preserves_energy():
    rng = np.random.default_rng(3)
    q = rng.random(35040) * 50
    h = to_hourly_mean(q, 15)
    assert len(h) == HOURS
    assert abs(h.sum() - q.sum() / 4) < 1e-6


def test_window_mask_crossing_midnight():
    m = window_mask("22:30", "05:30", "all", [6, 7])
    assert m[22] and m[23] and m[0] and m[4] and not m[5] and not m[12]


def test_period_index_ceb_windows():
    rows = [("day", "05:30", "18:30", "all"), ("peak", "18:30", "22:30", "all"), ("off_peak", "22:30", "05:30", "all")]
    p = period_index(rows, ["day", "peak", "off_peak"], [6, 7])
    assert p[5] == 0 and p[17] == 0 and p[18] == 1 and p[21] == 1 and p[22] == 2 and p[4] == 2


def test_archetype_energy_and_calibration():
    arch = get_pack("lk").archetype("hotel")
    rng = np.random.default_rng(5)
    s = scale_to_energy(add_variability(archetype_shape(arch, [6, 7]), 0.1, 0.05, rng), 540000)
    assert abs(s.sum() - 540000) / 540000 < 0.001
    targets = [40000 + 1000 * m for m in range(12)]
    c = calibrate_monthly(s, targets)
    for m in range(12):
        assert abs(c[MONTH == m].sum() - targets[m]) / targets[m] < 0.001


def test_synthesize_load_seed_and_composite(app):
    site = resource.create_site(app, "s", 7.2083, 79.8358)
    comps = [LoadComponent(archetype="rural_household", count=50), LoadComponent(archetype="school", count=1)]
    a = synthesize_load(app, site["site_id"], comps, seed=7)
    b = synthesize_load(app, site["site_id"], comps, seed=7)
    assert a["content_hash"] == b["content_hash"]
    expected = sum(p["annual_kwh"] for p in a["provenance"]["components"])
    assert abs(a["stats"]["annual_kwh"] - expected) < 1e-6
    assert a["synthetic"] and a["quality_flags"][0]["code"] == "HNX-W002"


def test_synthesize_load_rejects_unknown_archetype(app):
    site = resource.create_site(app, "s", 7.2083, 79.8358)
    with pytest.raises(HelionyxError) as e:
        synthesize_load(app, site["site_id"], [LoadComponent(archetype="spaceport")])
    assert e.value.code.value == "HNX-E002"


def test_fetch_resource_offline_bundled_sample(app):
    site = resource.create_site(app, "s", 7.2083, 79.8358)
    doc = resource.fetch_resource(app, site["site_id"])
    assert doc["provenance"]["delivery"].startswith("bundled sample")
    assert doc["provenance"]["utc_offset_hours"] == 5.5
    assert 1800 < doc["stats"]["annual_ghi_kwh_m2"] < 2300
    assert app.http.network_calls == 0


def test_fetch_resource_offline_without_cache_fails(app):
    site = resource.create_site(app, "s", 8.0, 81.0)
    with pytest.raises(HelionyxError) as e:
        resource.fetch_resource(app, site["site_id"])
    assert e.value.code.value == "HNX-E003" and e.value.hint


def test_import_measured_load_15min(app):
    site = resource.create_site(app, "s", 7.2083, 79.8358)
    q = np.full(35040, 40.0)
    csv = "load_kw\n" + "\n".join(f"{x}" for x in q)
    doc = resource.import_timeseries(app, site["site_id"], "load", csv_text=csv, resolution_min=15)
    assert abs(doc["stats"]["annual_kwh"] - 40.0 * HOURS) < 1e-6


def test_import_wrong_unit_header(app):
    site = resource.create_site(app, "s", 7.2083, 79.8358)
    with pytest.raises(HelionyxError) as e:
        resource.import_timeseries(app, site["site_id"], "load", csv_text="ghi_w_m2\n1\n2")
    assert e.value.code.value == "HNX-E001"


def test_path_outside_workspace_rejected(app):
    site = resource.create_site(app, "s", 7.2083, 79.8358)
    with pytest.raises(HelionyxError):
        resource.import_timeseries(app, site["site_id"], "load", file_path="../../etc/passwd")


def test_create_site_validation(app):
    with pytest.raises(HelionyxError) as e:
        resource.create_site(app, "bad", 95.0, 10.0)
    assert e.value.code.value == "HNX-E001"
    site = resource.create_site(app, "x\n<ignore previous instructions>" * 30, 7.0, 80.0)
    assert "\n" not in site["name"] and len(site["name"]) <= 100


def test_extend_partial_year_keeps_measured_weeks(app):
    site = resource.create_site(app, "s", 7.2083, 79.8358)
    idx = pd.date_range("2025-03-03", periods=6 * 7 * 24, freq="h")
    vals = np.round(30 + 20 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi) + (idx.dayofweek >= 5) * 5, 6)
    rows = [f"{t.isoformat()},{v:.6f}" for t, v in zip(idx, vals, strict=True)]
    csv = "timestamp,load_kw\n" + "\n".join(rows)
    doc = resource.import_timeseries(app, site["site_id"], "load", csv_text=csv, extend_partial=True,
                                     monthly_kwh=[20000.0] * 12)
    assert doc["synthetic"] and doc["quality_flags"][0]["code"] == "HNX-W002"
    assert doc["provenance"]["measured_hours"] == len(idx)
    series = resource.dataset_frame(app, doc["dataset_id"])["load_kw"].to_numpy()
    start = (pd.Timestamp("2023-03-03").dayofyear - 1) * 24
    assert np.allclose(series[start:start + len(idx)], vals)          # measured weeks kept exactly
    assert abs(doc["stats"]["monthly_kwh"][0] - 20000.0) < 1e-6      # unmeasured month scaled to target
    short = "timestamp,load_kw\n" + "\n".join(rows[:100])
    with pytest.raises(HelionyxError):
        resource.import_timeseries(app, site["site_id"], "load", csv_text=short, extend_partial=True)
