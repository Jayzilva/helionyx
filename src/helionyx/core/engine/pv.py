"""PV output model (SRS §7.2).

Produces the AC output per kWp of array rating for every hour of the year, so
that the output of a candidate is simply ``Y_pv * profile`` (the inverter clip
``Y_pv / r_dc/ac`` scales linearly with ``Y_pv``).
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pandas as pd
import pvlib

from helionyx.core.engine.timeaxis import HOURS, local_index

FloatArray = npt.NDArray[np.float64]
G_STC = 1000.0
T_STC = 25.0


def plane_of_array(
    ghi: FloatArray,
    latitude: float,
    longitude: float,
    elevation_m: float,
    timezone: str,
    tilt_deg: float,
    azimuth_deg: float,
    albedo: float,
) -> FloatArray:
    """Plane-of-array irradiance (W/m²): solar position, Erbs decomposition, Hay–Davies transposition."""
    # Hourly values are averages over the hour, so geometry is evaluated at the midpoint.
    times = (local_index() + pd.Timedelta(minutes=30)).tz_localize(timezone, ambiguous="NaT",
                                                                   nonexistent="shift_forward")
    solpos = pvlib.solarposition.get_solarposition(times, latitude, longitude, altitude=elevation_m)
    ghi_s = pd.Series(np.clip(ghi, 0.0, None), index=times)
    erbs = pvlib.irradiance.erbs(ghi_s, solpos["zenith"], times)
    dni_extra = pvlib.irradiance.get_extra_radiation(times)
    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt_deg,
        surface_azimuth=azimuth_deg,
        solar_zenith=solpos["apparent_zenith"],
        solar_azimuth=solpos["azimuth"],
        dni=erbs["dni"],
        ghi=ghi_s,
        dhi=erbs["dhi"],
        dni_extra=dni_extra,
        model="haydavies",
        albedo=albedo,
    )
    out = np.nan_to_num(poa["poa_global"].to_numpy(dtype=np.float64), nan=0.0)
    return np.asarray(np.clip(out, 0.0, None), dtype=np.float64)


def pv_ac_per_kwp(
    ghi: FloatArray,
    temp_c: FloatArray,
    wind_m_s: FloatArray,
    *,
    latitude: float,
    longitude: float,
    elevation_m: float,
    timezone: str,
    tilt_deg: float,
    azimuth_deg: float,
    dc_ac_ratio: float,
    derating_factor: float,
    temp_coeff_pct_per_c: float,
    inverter_efficiency: float,
    faiman_u0: float,
    faiman_u1: float,
    albedo: float,
) -> FloatArray:
    """AC output in kW per kWp for 8,760 hours."""
    if len(ghi) != HOURS:
        raise ValueError("ghi must have 8760 values")
    g_t = plane_of_array(ghi, latitude, longitude, elevation_m, timezone, tilt_deg, azimuth_deg, albedo)
    t_cell = np.asarray(pvlib.temperature.faiman(g_t, temp_c, wind_m_s, u0=faiman_u0, u1=faiman_u1),
                        dtype=np.float64)
    alpha = temp_coeff_pct_per_c / 100.0
    dc = derating_factor * (g_t / G_STC) * (1.0 + alpha * (t_cell - T_STC))
    dc = np.clip(dc, 0.0, None)
    ac = np.minimum(inverter_efficiency * dc, 1.0 / dc_ac_ratio)
    return np.asarray(ac, dtype=np.float64)
