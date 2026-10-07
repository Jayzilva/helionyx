"""Tariff service: browse tariffs, staleness, and bills (FR-TAR-001…008)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np

from helionyx.core.billing import compute_bill as engine_bill
from helionyx.core.engine.timeaxis import MONTH, period_index
from helionyx.core.models.packs import Tariff
from helionyx.errors import ErrorCode, HelionyxError, WarningCode, validation, warning
from helionyx.infra.packs import Pack, get_pack
from helionyx.services.context import Helionyx
from helionyx.services.resource import dataset_frame, get_dataset

EXPORT_SCHEMES = ("none", "net_metering", "net_accounting", "net_plus")


def tariff_period_index(tariff: Tariff, weekend_days: list[int]) -> np.ndarray:
    names = tariff.period_names
    if tariff.charges.energy.type != "tou" or not tariff.tou_periods:
        return np.zeros(8760, dtype=np.int64)
    rows = [(p.name, p.start, p.end, p.days) for p in tariff.tou_periods]
    return period_index(rows, names, weekend_days)


def staleness(pack: Pack, tariff: Tariff, on: dt.date) -> list[dict[str, Any]]:
    issues = []
    months = pack.manifest.staleness_months
    age_days = (on - pack.manifest.last_verified).days
    if age_days > months * 30.4:
        issues.append(warning(WarningCode.DATA_STALE,
                              f"Pack {pack.ref} was last verified on {pack.manifest.last_verified}, "
                              f"more than {months} months before the analysis date.",
                              path="grid.tariff", hint="Check for a newer pack release.").model_dump())
    if tariff.status != "verified":
        issues.append(warning(WarningCode.DATA_STALE,
                              f"Tariff {tariff.id} is marked unverified: its rates are placeholders.",
                              path="grid.tariff",
                              hint="Replace with the current PUCSL-approved rates or supply an inline tariff."
                              ).model_dump())
    newer = pack.newer_revision(tariff)
    if newer is not None:
        issues.append(warning(WarningCode.DATA_STALE, f"A newer revision {newer.id} exists.",
                              path="grid.tariff", hint="Use the newer revision unless the study date requires this one."
                              ).model_dump())
    return issues


def list_tariffs(utility: str | None = None, category: str | None = None, as_of: dt.date | None = None,
                 country_pack: str = "lk") -> dict[str, Any]:
    pack = get_pack(country_pack)
    rows = []
    for t in sorted(pack.tariffs.values(), key=lambda x: x.id):
        if utility and t.utility.lower() != utility.lower():
            continue
        if category and t.category.lower() != category.lower():
            continue
        if as_of and not (t.effective_from <= as_of and (t.effective_to is None or as_of <= t.effective_to)):
            continue
        rows.append({"tariff_id": t.id, "utility": t.utility, "category": t.category, "name": t.name,
                     "energy_type": t.charges.energy.type, "effective_from": t.effective_from.isoformat(),
                     "effective_to": t.effective_to.isoformat() if t.effective_to else None, "status": t.status,
                     "export_schemes": [k for k in EXPORT_SCHEMES[1:] if getattr(t.export_schemes, k) is not None]})
    return {"pack": pack.ref, "count": len(rows), "tariffs": rows}


def resolve_tariff(pack: Pack, tariff_id: str, on: dt.date) -> Tariff:
    """Accept a full revision ID (``lk.ceb.H2@2025-01-01``) or a category ID (``lk.ceb.H2``)."""
    if "@" in tariff_id:
        return pack.tariff(tariff_id)
    parts = tariff_id.split(".")
    if len(parts) == 3:
        found = pack.tariff_in_effect(parts[1], parts[2], on)
        if found is not None:
            return found
    return pack.tariff(tariff_id)


def get_tariff(tariff_id: str, country_pack: str = "lk", as_of: dt.date | None = None) -> dict[str, Any]:
    pack = get_pack(country_pack)
    on = as_of or dt.date.today()
    t = resolve_tariff(pack, tariff_id, on)
    return {"tariff": t.model_dump(mode="json"), "period_names": t.period_names,
            "warnings": staleness(pack, t, on), "pack": pack.ref}


def bill_for_series(tariff: Tariff, scheme: str, imports: np.ndarray, exports: np.ndarray, weekend_days: list[int],
                    demand_peak_factor: float) -> dict[str, Any]:
    pidx = tariff_period_index(tariff, weekend_days)
    P = len(tariff.period_names)
    imp_mp = np.zeros((12, P))
    exp_mp = np.zeros((12, P))
    np.add.at(imp_mp, (MONTH, pidx), imports)
    np.add.at(exp_mp, (MONTH, pidx), exports)
    peak = np.array([imports[MONTH == m].max() for m in range(12)])
    bill = engine_bill(tariff, scheme, imp_mp, exp_mp, peak, demand_peak_factor)
    return {"months": [m.as_dict() for m in bill.months], "annual_total": bill.total,
            "annual_purchases": bill.purchases, "annual_sales": bill.sales,
            "year_end_payout": bill.year_end_payout}


def compute_bill(app: Helionyx, tariff_id: str, load_id: str | None = None, import_id: str | None = None,
                 export_id: str | None = None, export_scheme: str = "none", demand_peak_factor: float = 1.0,
                 country_pack: str = "lk", as_of: dt.date | None = None) -> dict[str, Any]:
    pack = get_pack(country_pack)
    on = as_of or dt.date.today()
    tariff = resolve_tariff(pack, tariff_id, on)
    if export_scheme not in EXPORT_SCHEMES:
        raise validation(f"Unknown export scheme '{export_scheme[:40]}'.", f"Use one of {', '.join(EXPORT_SCHEMES)}.")
    if export_scheme != "none" and getattr(tariff.export_schemes, export_scheme) is None:
        raise HelionyxError(ErrorCode.UNSUPPORTED_COMBINATION,
                            f"Tariff {tariff.id} does not define the {export_scheme} scheme.",
                            "Choose a scheme listed by get_tariff, or 'none'.")
    if load_id:
        imports = dataset_frame(app, load_id)["load_kw"].to_numpy()
        exports = np.zeros_like(imports)
        refs = {"load_id": load_id}
    elif import_id:
        imports = dataset_frame(app, import_id)["load_kw"].to_numpy()
        exports = dataset_frame(app, export_id)["load_kw"].to_numpy() if export_id else np.zeros_like(imports)
        refs = {"import_id": import_id, "export_id": export_id}
    else:
        raise validation("Give load_id, or import_id (and optionally export_id).",
                         "Use the load_id from synthesize_load for a baseline bill.")
    for ds in refs.values():
        if ds and get_dataset(app, ds)["kind"] != "load":
            raise validation("Bill inputs must be load-type datasets (column load_kw).", "Use a load dataset ID.")
    out = bill_for_series(tariff, export_scheme, imports, exports, pack.manifest.weekend_days, demand_peak_factor)
    out.update(tariff_id=tariff.id, export_scheme=export_scheme, currency=tariff.currency,
               demand_peak_factor=demand_peak_factor, warnings=staleness(pack, tariff, on), refs=refs)
    return out
