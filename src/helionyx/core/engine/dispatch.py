"""Hourly dispatch simulation (SRS §7.1, §7.4–7.6) as a Numba kernel.

One call to :func:`simulate_batch` simulates many candidates in parallel. Each
candidate writes only to its own output rows, so results do not depend on the
number of threads (FR-OPT-003).
"""

from __future__ import annotations

from dataclasses import dataclass

import numba as nb
import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

# ---- aggregate indices (per candidate, annual)
A_LOAD, A_PV, A_WT, A_GEN, A_IMP, A_EXP, A_CH, A_DIS, A_UNS, A_XS = range(10)
A_THR, A_FUEL, A_GEN_HOURS, A_GEN_STARTS, A_BAL_ERR, A_E_MIN, A_E_MAX = range(10, 17)
A_OUTAGE_UNS, A_OUTAGE_LOAD, A_OUTAGE_HOURS, A_PEAK_EXP = range(17, 21)
N_AGG = 21

# ---- monthly flow indices
M_FIELDS = ("load", "pv", "wind", "genset", "grid_import", "grid_export",
            "bess_charge", "bess_discharge", "unserved", "excess")
N_MON = len(M_FIELDS)

# ---- hourly series columns (persisted for top-N candidates)
SERIES_FIELDS = ("load_kw", "pv_kw", "wind_kw", "genset_kw", "grid_import_kw", "grid_export_kw",
                 "bess_charge_kw", "bess_discharge_kw", "unserved_kw", "excess_kw", "bess_soc",
                 "grid_available")
N_SER = len(SERIES_FIELDS)

# ---- parameter vector
P_ETA_C, P_ETA_D, P_SOC_MIN, P_SOC0, P_SOC_RES, P_SOC_SP, P_RMIN, P_F0, P_F1, P_IMP_MAX, P_EXP_MAX = range(11)
N_PRM = 11
# ---- flag vector
F_GRID, F_EXPORT, F_GRID_CHARGING, F_STRATEGY_CC, F_CC_HOLD, F_NET_PLUS = range(6)
N_FLG = 6


@dataclass(frozen=True)
class SystemInputs:
    """Hourly inputs shared by every candidate of a scenario."""

    load: FloatArray          # kW
    pv_unit: FloatArray       # kW AC per kWp
    wind_unit: FloatArray     # kW per turbine
    available: npt.NDArray[np.int8]   # 1 = grid available
    period: npt.NDArray[np.int64]     # TOU period index per hour
    month: npt.NDArray[np.int64]      # 0..11
    discharge_mask: npt.NDArray[np.bool_]  # per period
    charge_mask: npt.NDArray[np.bool_]     # per period
    params: FloatArray
    flags: npt.NDArray[np.int64]
    n_periods: int


@nb.njit(cache=True, nogil=True)
def _simulate_one(load, pv_unit, wt_unit, avail, period, month, dis_mask, ch_mask,
                  pv_kw, n_wt, e_nom, p_bmax, gen_kw, prm, flg,
                  agg, imp_mp, exp_mp, peak, mon, record, series):  # pragma: no cover - jitted
    eta_c = prm[P_ETA_C]
    eta_d = prm[P_ETA_D]
    soc_min = prm[P_SOC_MIN]
    soc_res = prm[P_SOC_RES]
    soc_sp = prm[P_SOC_SP]
    rmin = prm[P_RMIN]
    f0 = prm[P_F0]
    f1 = prm[P_F1]
    imp_max = prm[P_IMP_MAX]
    exp_max = prm[P_EXP_MAX]
    grid_mode = flg[F_GRID]
    export_allowed = flg[F_EXPORT]
    grid_charging = flg[F_GRID_CHARGING]
    cc = flg[F_STRATEGY_CC]
    cc_hold = flg[F_CC_HOLD]
    net_plus = flg[F_NET_PLUS]

    E = prm[P_SOC0] * e_nom
    e_floor_offgrid = soc_min * e_nom
    e_floor_grid = max(soc_res, soc_min) * e_nom
    e_sp = soc_sp * e_nom
    gen_prev = False
    agg[:] = 0.0
    imp_mp[:, :] = 0.0
    exp_mp[:, :] = 0.0
    peak[:] = 0.0
    mon[:, :] = 0.0
    agg[A_E_MIN] = E
    agg[A_E_MAX] = E

    n = load.shape[0]
    for t in range(n):
        L = load[t]
        ppv = pv_kw * pv_unit[t]
        pwt = n_wt * wt_unit[t]
        pg = 0.0
        pimp = 0.0
        pexp = 0.0
        pch = 0.0
        pdis = 0.0
        puns = 0.0
        pxs = 0.0
        grid_up = grid_mode == 1 and avail[t] == 1
        p = period[t]
        cmax = 0.0
        if e_nom > 0.0:
            cmax = min(p_bmax, max(0.0, e_nom - E) / eta_c)

        if grid_up and net_plus == 1:
            # Net plus: all renewable output exported, load served from the grid (§7.6.3).
            re = ppv + pwt
            pexp = min(re, exp_max)
            pxs = re - pexp
            pimp = min(L, imp_max)
            rem = L - pimp
            if rem > 1e-12 and gen_kw > 0.0:
                pg = min(max(rem, rmin * gen_kw), gen_kw)
                if pg >= rem:
                    pxs += pg - rem
                    rem = 0.0
                else:
                    rem -= pg
            if rem > 1e-12:
                puns = rem
        else:
            N = L - ppv - pwt
            if N <= 0.0:
                S = -N
                pch = min(S, cmax)
                S -= pch
                if grid_up and export_allowed == 1:
                    pexp = min(S, exp_max)
                    S -= pexp
                pxs = S
            elif grid_up:
                if e_nom > 0.0 and dis_mask[p]:
                    d = min(p_bmax, max(0.0, E - e_floor_grid) * eta_d)
                    pdis = min(N, d)
                    N -= pdis
                pimp = min(N, imp_max)
                N -= pimp
                if N > 1e-12:
                    if gen_kw > 0.0:
                        pg = min(max(N, rmin * gen_kw), gen_kw)
                        if pg >= N:
                            sur = pg - N
                            if pdis == 0.0:
                                c = min(sur, cmax)
                                pch += c
                                sur -= c
                            pxs += sur
                        else:
                            puns = N - pg
                    else:
                        puns = N
                if pdis == 0.0 and grid_charging == 1 and e_nom > 0.0 and ch_mask[p]:
                    room = cmax - pch
                    extra = min(room, imp_max - pimp)
                    if extra > 0.0:
                        pch += extra
                        pimp += extra
            else:
                d = 0.0
                if e_nom > 0.0:
                    d = min(p_bmax, max(0.0, E - e_floor_offgrid) * eta_d)
                hold = (cc == 1 and cc_hold == 1 and gen_prev and gen_kw > 0.0 and E < e_sp - 1e-9)
                if N <= d and not hold:
                    pdis = N
                elif gen_kw > 0.0:
                    target = N
                    if cc == 1 and e_nom > 0.0:
                        target = N + min(cmax, max(0.0, e_sp - E) / eta_c)
                    pg = min(max(target, rmin * gen_kw), gen_kw)
                    if pg >= N:
                        sur = pg - N
                        pch = min(sur, cmax)
                        pxs = sur - pch
                    else:
                        pdis = min(N - pg, d)
                        puns = N - pg - pdis
                else:
                    pdis = d
                    puns = N - d

        # storage update
        if e_nom > 0.0:
            E = E + eta_c * pch - pdis / eta_d
            if E < 0.0:
                E = 0.0
            if E > e_nom:
                E = e_nom
            if E < agg[A_E_MIN]:
                agg[A_E_MIN] = E
            if E > agg[A_E_MAX]:
                agg[A_E_MAX] = E

        err = abs(ppv + pwt + pg + pimp + pdis + puns - (L + pch + pexp + pxs))
        if err > agg[A_BAL_ERR]:
            agg[A_BAL_ERR] = err

        if pg > 0.0:
            agg[A_GEN_HOURS] += 1.0
            if not gen_prev:
                agg[A_GEN_STARTS] += 1.0
            agg[A_FUEL] += f0 * gen_kw + f1 * pg
            gen_prev = True
        else:
            gen_prev = False

        agg[A_LOAD] += L
        agg[A_PV] += ppv
        agg[A_WT] += pwt
        agg[A_GEN] += pg
        agg[A_IMP] += pimp
        agg[A_EXP] += pexp
        agg[A_CH] += pch
        agg[A_DIS] += pdis
        agg[A_UNS] += puns
        agg[A_XS] += pxs
        if e_nom > 0.0:
            agg[A_THR] += pdis / eta_d
        if grid_mode == 1 and avail[t] == 0:
            agg[A_OUTAGE_UNS] += puns
            agg[A_OUTAGE_LOAD] += L
            agg[A_OUTAGE_HOURS] += 1.0

        if pexp > agg[A_PEAK_EXP]:
            agg[A_PEAK_EXP] = pexp

        m = month[t]
        imp_mp[m, p] += pimp
        exp_mp[m, p] += pexp
        if pimp > peak[m]:
            peak[m] = pimp
        mon[m, 0] += L
        mon[m, 1] += ppv
        mon[m, 2] += pwt
        mon[m, 3] += pg
        mon[m, 4] += pimp
        mon[m, 5] += pexp
        mon[m, 6] += pch
        mon[m, 7] += pdis
        mon[m, 8] += puns
        mon[m, 9] += pxs

        if record:
            series[t, 0] = L
            series[t, 1] = ppv
            series[t, 2] = pwt
            series[t, 3] = pg
            series[t, 4] = pimp
            series[t, 5] = pexp
            series[t, 6] = pch
            series[t, 7] = pdis
            series[t, 8] = puns
            series[t, 9] = pxs
            series[t, 10] = E / e_nom if e_nom > 0.0 else 0.0
            series[t, 11] = 1.0 if grid_up else 0.0


@nb.njit(cache=True, parallel=True, nogil=True)
def _simulate_batch(load, pv_unit, wt_unit, avail, period, month, dis_mask, ch_mask,
                    pv_kw, n_wt, e_nom, p_bmax, gen_kw, prm, flg,
                    agg, imp_mp, exp_mp, peak, mon):  # pragma: no cover - jitted
    dummy = np.zeros((1, N_SER))
    for i in nb.prange(pv_kw.shape[0]):
        _simulate_one(load, pv_unit, wt_unit, avail, period, month, dis_mask, ch_mask,
                      pv_kw[i], n_wt[i], e_nom[i], p_bmax[i], gen_kw[i], prm, flg,
                      agg[i], imp_mp[i], exp_mp[i], peak[i], mon[i], False, dummy)


@dataclass
class BatchResult:
    agg: FloatArray        # (n, N_AGG)
    imp_mp: FloatArray     # (n, 12, P) import kWh by month and period
    exp_mp: FloatArray     # (n, 12, P)
    peak: FloatArray       # (n, 12) monthly peak import kW
    monthly: FloatArray    # (n, 12, N_MON)


def simulate_batch(inp: SystemInputs, pv_kw: FloatArray, n_wt: FloatArray, e_nom: FloatArray,
                   p_bmax: FloatArray, gen_kw: FloatArray) -> BatchResult:
    n = pv_kw.shape[0]
    P = inp.n_periods
    res = BatchResult(
        agg=np.zeros((n, N_AGG)),
        imp_mp=np.zeros((n, 12, P)),
        exp_mp=np.zeros((n, 12, P)),
        peak=np.zeros((n, 12)),
        monthly=np.zeros((n, 12, N_MON)),
    )
    if n == 0:
        return res
    _simulate_batch(inp.load, inp.pv_unit, inp.wind_unit, inp.available, inp.period, inp.month,
                    inp.discharge_mask, inp.charge_mask,
                    np.ascontiguousarray(pv_kw, dtype=np.float64), np.ascontiguousarray(n_wt, dtype=np.float64),
                    np.ascontiguousarray(e_nom, dtype=np.float64), np.ascontiguousarray(p_bmax, dtype=np.float64),
                    np.ascontiguousarray(gen_kw, dtype=np.float64), inp.params, inp.flags,
                    res.agg, res.imp_mp, res.exp_mp, res.peak, res.monthly)
    return res


def simulate_series(inp: SystemInputs, pv_kw: float, n_wt: float, e_nom: float, p_bmax: float,
                    gen_kw: float) -> tuple[FloatArray, FloatArray]:
    """Simulate one candidate and return (aggregates, hourly series of shape (8760, N_SER))."""
    P = inp.n_periods
    agg = np.zeros(N_AGG)
    series = np.zeros((inp.load.shape[0], N_SER))
    _simulate_one(inp.load, inp.pv_unit, inp.wind_unit, inp.available, inp.period, inp.month,
                  inp.discharge_mask, inp.charge_mask, float(pv_kw), float(n_wt), float(e_nom),
                  float(p_bmax), float(gen_kw), inp.params, inp.flags,
                  agg, np.zeros((12, P)), np.zeros((12, P)), np.zeros(12), np.zeros((12, N_MON)),
                  True, series)
    return agg, series
