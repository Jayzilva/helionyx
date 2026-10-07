"""Bill engine (SRS FR-TAR-002…004, §7.10).

Bills are computed from monthly import and export energy per TOU period and the
monthly peak import, which the dispatch kernel aggregates for every candidate.

Order of a monthly bill:
  1. energy charge on billed kWh (TOU, block or flat);
  2. demand charge on peak import × demand_peak_factor / PF;
  3. fixed charge (block tariffs may set it per slab);
  4. minimum charge applied to the subtotal;
  5. levies as a percentage of the subtotal;
  6. export credit subtracted (net accounting / net plus), or kWh netted beforehand (net metering).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from helionyx.core.models.packs import Tariff

FloatArray = npt.NDArray[np.float64]
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass
class MonthlyBill:
    month: str
    import_kwh: float
    export_kwh: float
    billed_kwh: float
    peak_import_kw: float
    energy_charge: float
    demand_charge: float
    fixed_charge: float
    minimum_adjustment: float
    levies: float
    export_credit: float
    total: float

    def as_dict(self) -> dict[str, float | str]:
        return {
            "month": self.month,
            "import_kwh": self.import_kwh,
            "export_kwh": self.export_kwh,
            "billed_kwh": self.billed_kwh,
            "peak_import_kw": self.peak_import_kw,
            "energy_charge": self.energy_charge,
            "demand_charge": self.demand_charge,
            "fixed_charge": self.fixed_charge,
            "minimum_adjustment": self.minimum_adjustment,
            "levies": self.levies,
            "export_credit": self.export_credit,
            "total": self.total,
        }


@dataclass
class AnnualBill:
    months: list[MonthlyBill]
    year_end_payout: float

    @property
    def total(self) -> float:
        return sum(m.total for m in self.months) - self.year_end_payout

    @property
    def purchases(self) -> float:
        """Charges for energy, demand, fixed, minimum and levies."""
        return sum(m.total + m.export_credit for m in self.months)

    @property
    def sales(self) -> float:
        """Export credits and any year-end payout."""
        return sum(m.export_credit for m in self.months) + self.year_end_payout


def _block_charge(tariff: Tariff, kwh: float) -> tuple[float, float | None]:
    blocks = tariff.charges.energy.blocks or []
    charge = 0.0
    lower = 0.0
    fixed: float | None = None
    for b in blocks:
        upper = b.up_to_kwh if b.up_to_kwh is not None else float("inf")
        if kwh > lower:
            charge += (min(kwh, upper) - lower) * b.rate_per_kwh
            fixed = b.fixed_monthly if b.fixed_monthly is not None else fixed
        elif fixed is None and b.fixed_monthly is not None:
            fixed = b.fixed_monthly
        lower = upper
        if kwh <= upper:
            break
    return charge, fixed


def _energy_charge(tariff: Tariff, billed_by_period: FloatArray, names: list[str]) -> tuple[float, float | None]:
    e = tariff.charges.energy
    total_kwh = float(billed_by_period.sum())
    if e.type == "flat":
        return total_kwh * float(e.rate_per_kwh or 0.0), None
    if e.type == "block":
        return _block_charge(tariff, total_kwh)
    rates = e.rates_per_kwh or {}
    return float(sum(billed_by_period[k] * rates[n] for k, n in enumerate(names))), None


def compute_bill(
    tariff: Tariff,
    export_scheme: str,
    imp_mp: FloatArray,
    exp_mp: FloatArray,
    peak_import_kw: FloatArray,
    demand_peak_factor: float = 1.0,
    escalation_factor: float = 1.0,
) -> AnnualBill:
    """Monthly and annual bills for a year of imports/exports (arrays shaped (12, P))."""
    names = tariff.period_names
    ch = tariff.charges
    scheme = export_scheme
    nm = tariff.export_schemes.net_metering
    if scheme == "net_metering" and nm is None:
        scheme = "none"
    credit_by_period = np.zeros(len(names))
    credit_total = 0.0
    months: list[MonthlyBill] = []
    for m in range(12):
        imp = imp_mp[m].astype(np.float64)
        exp = exp_mp[m].astype(np.float64)
        billed = imp.copy()
        export_credit = 0.0
        if scheme == "net_metering" and nm is not None:
            if nm.netting == "by_period":
                net = imp - exp - credit_by_period
                billed = np.clip(net, 0.0, None)
                credit_by_period = np.clip(-net, 0.0, None) if nm.credit_carry_forward else np.zeros(len(names))
            else:
                net_total = float(imp.sum() - exp.sum() - credit_total)
                if net_total >= 0:
                    share = imp / imp.sum() if imp.sum() > 0 else np.full(len(names), 1.0 / len(names))
                    billed = share * net_total
                    credit_total = 0.0
                else:
                    billed = np.zeros(len(names))
                    credit_total = -net_total if nm.credit_carry_forward else 0.0
        elif scheme in ("net_accounting", "net_plus"):
            rate = getattr(tariff.export_schemes, scheme)
            if rate is not None:
                export_credit = float(exp.sum()) * rate.export_rate_per_kwh

        energy, block_fixed = _energy_charge(tariff, billed, names)
        demand = 0.0
        if ch.demand is not None and ch.demand.per_kva_month > 0:
            kva = float(peak_import_kw[m]) * demand_peak_factor / ch.demand.power_factor_assumption
            demand = kva * ch.demand.per_kva_month
        fixed = block_fixed if block_fixed is not None else ch.fixed_monthly
        subtotal = energy + demand + fixed
        min_adj = max(0.0, ch.minimum_monthly - subtotal)
        subtotal += min_adj
        levies = subtotal * ch.levies_pct / 100.0
        f = escalation_factor
        total = (subtotal + levies - export_credit) * f
        months.append(MonthlyBill(
            month=MONTHS[m],
            import_kwh=float(imp.sum()),
            export_kwh=float(exp.sum()),
            billed_kwh=float(billed.sum()),
            peak_import_kw=float(peak_import_kw[m]),
            energy_charge=energy * f,
            demand_charge=demand * f,
            fixed_charge=fixed * f,
            minimum_adjustment=min_adj * f,
            levies=levies * f,
            export_credit=export_credit * f,
            total=total,
        ))
    payout = 0.0
    if scheme == "net_metering" and nm is not None and nm.year_end == "pay":
        remaining = float(credit_by_period.sum()) + credit_total
        payout = remaining * nm.year_end_rate_per_kwh * escalation_factor
    return AnnualBill(months=months, year_end_payout=payout)
