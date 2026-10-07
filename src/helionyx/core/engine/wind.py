"""Wind turbine output model (SRS §7.3)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


def hub_speed(v_anem: FloatArray, z_anem: float, z_hub: float, z0: float) -> FloatArray:
    """Logarithmic-law height correction."""
    return np.asarray(v_anem * np.log(z_hub / z0) / np.log(z_anem / z0), dtype=np.float64)


def density_ratio(elevation_m: float) -> float:
    """Air density relative to sea level for the standard atmosphere."""
    return float((1.0 - 2.25577e-5 * elevation_m) ** 5.25588)


def turbine_output(
    wind_10m: FloatArray,
    wind_50m: FloatArray | None,
    *,
    hub_height_m: float,
    power_curve: Sequence[tuple[float, float]],
    availability: float,
    roughness_length_m: float,
    elevation_m: float,
    air_density_correction: bool = True,
) -> FloatArray:
    """AC output of one turbine (kW) for every hour.

    The anemometer height (10 m or 50 m) closest to the hub is used. Output is
    zero outside the tabulated curve (below cut-in and above cut-out).
    """
    if wind_50m is not None and abs(hub_height_m - 50.0) < abs(hub_height_m - 10.0):
        v = hub_speed(wind_50m, 50.0, hub_height_m, roughness_length_m)
    else:
        v = hub_speed(wind_10m, 10.0, hub_height_m, roughness_length_m)
    speeds = np.array([p[0] for p in power_curve], dtype=np.float64)
    power = np.array([p[1] for p in power_curve], dtype=np.float64)
    p = np.interp(v, speeds, power, left=0.0, right=0.0)
    if air_density_correction:
        p = p * density_ratio(elevation_m)
    return np.asarray(np.clip(p, 0.0, None) * availability, dtype=np.float64)
