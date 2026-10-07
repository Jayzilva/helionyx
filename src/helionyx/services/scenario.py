"""Scenario service: defaults resolution, assumption audit, validation and versioning (FR-SCN-001…009)."""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
from typing import Any

import yaml
from pydantic import ValidationError

from helionyx.core.models.packs import COST_FIELDS, TECHNICAL_MODELS, Component, Tariff
from helionyx.core.models.scenario import (
    Assumption,
    DatasetRef,
    ResolvedAvailability,
    ResolvedBess,
    ResolvedComponent,
    ResolvedComponents,
    ResolvedConstraints,
    ResolvedDispatch,
    ResolvedEconomics,
    ResolvedGenset,
    ResolvedGrid,
    ResolvedOptions,
    ResolvedPv,
    ResolvedScenario,
    ResolvedWind,
    ScenarioInput,
    SiteRef,
    SizeRange,
)
from helionyx.errors import ErrorCode, HelionyxError, Issue, WarningCode, validation, warning
from helionyx.infra.db import now_iso
from helionyx.infra.ids import new_id
from helionyx.infra.packs import Pack, get_pack
from helionyx.services.context import Helionyx
from helionyx.services.resource import get_dataset, get_site
from helionyx.services.tariff import resolve_tariff, staleness

SYSTEM_DATE = dt.date(2026, 10, 7)


def _err(code: ErrorCode, message: str, path: str | None = None, hint: str | None = None) -> Issue:
    return Issue(code=code.value, name=code.name, path=path, message=message, hint=hint)


class _Resolver:
    def __init__(self, app: Helionyx, inp: ScenarioInput) -> None:
        self.app = app
        self.inp = inp
        self.assumptions: list[Assumption] = []
        self.errors: list[Issue] = []
        self.warnings: list[Issue] = []
        self.site = get_site(app, inp.site_id)
        self.pack: Pack = get_pack(self.site["country_pack"])

    # -------------------------------------------------------------- helpers
    def pick(self, path: str, user: Any, default: Any = None, *, system: tuple[Any, str] | None = None) -> Any:
        """Return the user value, else the pack default, else a system default; record the assumption."""
        if user is not None:
            self.assumptions.append(Assumption(path=path, value=_jsonable(user), origin="user"))
            return user
        if path in self.pack.defaults.values:
            d = self.pack.defaults.values[path]
            self.assumptions.append(Assumption(path=path, value=d.value, origin="pack_default",
                                               source=f"{self.pack.ref}: {d.source}", date=d.date))
            return d.value
        if system is not None:
            self.assumptions.append(Assumption(path=path, value=_jsonable(system[0]), origin="system_default",
                                               source=system[1], date=SYSTEM_DATE))
            return system[0]
        self.assumptions.append(Assumption(path=path, value=default, origin="system_default",
                                           source="Helionyx default", date=SYSTEM_DATE))
        return default

    def sizes(self, path: str, raw: list[float] | SizeRange) -> list[float]:
        if isinstance(raw, SizeRange):
            n = int(round((raw.max - raw.min) / raw.step)) + 1
            vals = [round(raw.min + k * raw.step, 6) for k in range(n) if raw.min + k * raw.step <= raw.max + 1e-9]
        else:
            vals = [float(v) for v in raw]
        if not vals or any(v < 0 for v in vals):
            self.errors.append(_err(ErrorCode.VALIDATION_FAILED, "Sizes must be a non-empty list of values ≥ 0.",
                                    path))
            return [0.0]
        if len(vals) > 400:
            self.errors.append(_err(ErrorCode.SEARCH_SPACE_TOO_LARGE, f"{path} has {len(vals)} sizes; limit 400.",
                                    path, "Use a coarser step."))
        self.assumptions.append(Assumption(path=path, value=sorted(set(vals)), origin="user"))
        return sorted(set(vals))

    def component(self, kind: str, path: str, spec: str | None, overrides: dict[str, Any]) -> ResolvedComponent | None:
        spec_id = spec or self.pack.defaults.default_components[kind]
        if spec is None:
            self.assumptions.append(Assumption(path=f"{path}.spec", value=spec_id, origin="pack_default",
                                               source=f"{self.pack.ref}: default {kind} component",
                                               date=self.pack.manifest.last_verified))
        if spec_id not in self.pack.components:
            self.errors.append(_err(ErrorCode.NOT_FOUND, f"Unknown component '{spec_id}'.", f"{path}.spec",
                                    "Use list_components to find a valid spec ID."))
            return None
        comp: Component = self.pack.components[spec_id]
        if comp.type != kind:
            self.errors.append(_err(ErrorCode.VALIDATION_FAILED, f"Component {spec_id} is a {comp.type}, not {kind}.",
                                    f"{path}.spec"))
            return None
        fx = 1.0
        if comp.currency != self.pack.manifest.currency:
            rate = self.inp.economics.fx_rate_per_usd if self.inp.economics else None
            if comp.currency == "USD" and rate:
                fx = rate
            else:
                self.errors.append(_err(ErrorCode.VALIDATION_FAILED,
                                        f"{spec_id} is priced in {comp.currency}; an exchange rate is needed.",
                                        "economics.fx_rate_per_usd", "Supply the LKR per USD rate to use."))
        costs = {f: getattr(comp, f) * (fx if f != "lifetime_years" else 1.0) for f in COST_FIELDS}
        technical = dict(comp.technical)
        src = f"{comp.source.document} ({comp.source.retrieved_at})"
        for key, val in costs.items():
            if key not in overrides:
                self.assumptions.append(Assumption(path=f"{path}.{key}", value=val, origin="pack_default",
                                                   source=f"{spec_id}: {src}", date=comp.source.retrieved_at))
        tech_fields = set(TECHNICAL_MODELS[kind].model_fields)
        for key, val in technical.items():
            if key not in overrides:
                self.assumptions.append(Assumption(path=f"{path}.technical.{key}", value=_jsonable(val),
                                                   origin="pack_default", source=f"{spec_id}: {src}",
                                                   date=comp.source.retrieved_at))
        for key, val in overrides.items():
            if key in COST_FIELDS:
                costs[key] = float(val)
                self.assumptions.append(Assumption(path=f"{path}.{key}", value=val, origin="user"))
            elif key in tech_fields:
                technical[key] = val
                self.assumptions.append(Assumption(path=f"{path}.technical.{key}", value=_jsonable(val),
                                                   origin="user"))
            else:
                self.errors.append(_err(ErrorCode.VALIDATION_FAILED, f"Unknown override '{key[:40]}' for {kind}.",
                                        f"{path}.overrides",
                                        f"Valid keys: {', '.join(sorted(set(COST_FIELDS) | tech_fields))}."))
        try:
            technical = TECHNICAL_MODELS[kind].model_validate(technical).model_dump()
        except ValidationError as exc:
            e = exc.errors()[0]
            self.errors.append(_err(ErrorCode.VALIDATION_FAILED, f"{kind} technical parameter invalid: {e['msg']}",
                                    f"{path}.technical.{'.'.join(str(x) for x in e['loc'])}"))
        if costs["lifetime_years"] <= 0:
            self.errors.append(_err(ErrorCode.VALIDATION_FAILED, "Lifetime must be greater than 0.",
                                    f"{path}.lifetime_years"))
        return ResolvedComponent(spec=spec_id, name=comp.name, technical=technical, **costs)

    # -------------------------------------------------------------- main
    def resolve(self) -> ResolvedScenario | None:
        inp = self.inp
        site = self.site
        pack = self.pack
        opts = inp.options
        analysis_date = self.pick("options.analysis_date", opts.analysis_date if opts else None,
                                  system=(dt.date.today(), "Date the scenario was created"))
        if isinstance(analysis_date, str):
            analysis_date = dt.date.fromisoformat(analysis_date)

        # datasets
        loads: list[DatasetRef] = []
        for lid in inp.load_ids:
            ds = get_dataset(self.app, lid)
            if ds["kind"] != "load":
                self.errors.append(_err(ErrorCode.VALIDATION_FAILED, f"{lid} is not a load dataset.", "load_ids"))
                continue
            loads.append(DatasetRef(dataset_id=lid, content_hash=ds["content_hash"], synthetic=ds["synthetic"]))
            if ds["synthetic"]:
                self.warnings.append(warning(WarningCode.SYNTHETIC_INPUT,
                                             f"Load {lid} is synthetic; results depend on the archetype shape.",
                                             "load_ids", "Import measured data or calibrate to 12 monthly bills."))
        res = get_dataset(self.app, inp.resource_id)
        if res["kind"] != "resource":
            self.errors.append(_err(ErrorCode.VALIDATION_FAILED, f"{inp.resource_id} is not a resource dataset.",
                                    "resource_id"))
        if res["site_id"] != inp.site_id:
            self.warnings.append(warning(WarningCode.PLAUSIBILITY, "The resource dataset belongs to another site.",
                                         "resource_id"))
        if res.get("quality_flags"):
            self.warnings.append(warning(WarningCode.GAP_FILLED, "Resource data contains filled gaps.",
                                         "resource_id"))
        resource = DatasetRef(dataset_id=inp.resource_id, content_hash=res["content_hash"], synthetic=res["synthetic"])

        # grid
        g = inp.grid
        tariff: Tariff | None = None
        if g.mode == "grid_connected":
            if g.tariff is not None:
                tariff = g.tariff
                self.assumptions.append(Assumption(path="grid.tariff", value=tariff.id, origin="user"))
            elif g.tariff_id:
                try:
                    tariff = resolve_tariff(pack, g.tariff_id, analysis_date)
                    self.assumptions.append(Assumption(path="grid.tariff_id", value=tariff.id, origin="user"
                                                       if "@" in g.tariff_id else "pack_default",
                                                       source=f"revision in effect on {analysis_date}",
                                                       date=tariff.effective_from))
                except HelionyxError as exc:
                    self.errors.append(_err(exc.code, exc.message, "grid.tariff_id", "Use list_tariffs."))
            else:
                self.errors.append(_err(ErrorCode.VALIDATION_FAILED, "A grid-connected scenario needs a tariff.",
                                        "grid.tariff_id", "Set grid.tariff_id (see list_tariffs)."))
            if tariff is not None:
                self.warnings.extend(Issue(**w) for w in staleness(pack, tariff, analysis_date))
        scheme = self.pick("grid.export_scheme", g.export_scheme) if g.mode == "grid_connected" else "none"
        if tariff is not None and scheme != "none" and getattr(tariff.export_schemes, scheme) is None:
            self.errors.append(_err(ErrorCode.UNSUPPORTED_COMBINATION,
                                    f"Tariff {tariff.id} does not define the {scheme} scheme.", "grid.export_scheme"))
        av_in = g.availability
        if g.mode == "grid_connected":
            av_type = self.pick("grid.availability.type", av_in.type if av_in else None, system=("always", "Grid "
                                "assumed always available unless outages are specified"))
        else:
            av_type = "always"
        availability = ResolvedAvailability(type=av_type)
        if av_type == "scheduled":
            if not av_in or not av_in.windows:
                self.errors.append(_err(ErrorCode.VALIDATION_FAILED, "Scheduled outages need windows.",
                                        "grid.availability.windows"))
            else:
                availability.windows = av_in.windows
        elif av_type == "stochastic":
            if not av_in or av_in.events_per_year is None or av_in.mean_duration_h is None:
                self.errors.append(_err(ErrorCode.VALIDATION_FAILED,
                                        "Random outages need events_per_year and mean_duration_h.",
                                        "grid.availability"))
            else:
                availability.events_per_year = av_in.events_per_year
                availability.mean_duration_h = av_in.mean_duration_h
                availability.seed = self.pick("grid.availability.seed", av_in.seed, system=(7, "Default outage seed"))
        connected = g.mode == "grid_connected"
        grid = ResolvedGrid(
            mode=g.mode, tariff=tariff, export_scheme=scheme,
            max_import_kw=float(self.pick("grid.max_import_kw", g.max_import_kw)) if connected else 0.0,
            max_export_kw=float(self.pick("grid.max_export_kw", g.max_export_kw)) if connected else 0.0,
            availability=availability)

        # components
        c = inp.components
        comps = ResolvedComponents()
        if c.pv is not None:
            base = self.component("pv", "components.pv", c.pv.spec, c.pv.overrides)
            if base is not None:
                lat = site["latitude"]
                comps.pv = ResolvedPv(
                    **base.model_dump(),
                    sizes_kwp=self.sizes("components.pv.sizes_kwp", c.pv.sizes_kwp),
                    dc_ac_ratio=float(self.pick("components.pv.dc_ac_ratio", c.pv.dc_ac_ratio,
                                                system=(1.2, "Typical rooftop DC/AC ratio"))),
                    tilt_deg=float(self.pick("components.pv.tilt_deg", c.pv.tilt_deg,
                                             system=(round(max(5.0, abs(lat)), 1),
                                                     "Tilt equal to the absolute latitude, minimum 5°"))),
                    azimuth_deg=float(self.pick("components.pv.azimuth_deg", c.pv.azimuth_deg,
                                                system=(180.0 if lat >= 0 else 0.0, "Equator-facing array"))))
        if c.wind is not None:
            base = self.component("wind", "components.wind", c.wind.spec, c.wind.overrides)
            if base is not None:
                counts = self.sizes("components.wind.counts", c.wind.counts)
                if any(abs(x - round(x)) > 1e-9 for x in counts):
                    self.errors.append(_err(ErrorCode.VALIDATION_FAILED, "Turbine counts must be whole numbers.",
                                            "components.wind.counts"))
                comps.wind = ResolvedWind(**base.model_dump(), counts=counts)
        if c.bess is not None:
            base = self.component("bess", "components.bess", c.bess.spec, c.bess.overrides)
            if base is not None:
                ppk = float(self.pick("components.bess.power_kw_per_kwh", c.bess.power_kw_per_kwh,
                                      system=(min(0.5, float(base.technical["c_rate_max"])),
                                              "0.5 kW per kWh (2-hour battery), capped at the maximum C-rate")))
                comps.bess = ResolvedBess(**base.model_dump(), sizes_kwh=self.sizes("components.bess.sizes_kwh",
                                                                                     c.bess.sizes_kwh),
                                          power_kw_per_kwh=ppk)
                if ppk > float(base.technical["c_rate_max"]) + 1e-9:
                    self.errors.append(_err(ErrorCode.VALIDATION_FAILED,
                                            f"Battery power {ppk} kW/kWh exceeds the maximum C-rate "
                                            f"{base.technical['c_rate_max']}.", "components.bess.power_kw_per_kwh",
                                            "Lower power_kw_per_kwh or choose a higher-power battery."))
                conv_in = c.converter
                conv = self.component("converter", "components.converter", conv_in.spec if conv_in else None,
                                      conv_in.overrides if conv_in else {})
                comps.converter = conv
        if c.genset is not None:
            base = self.component("genset", "components.genset", c.genset.spec, c.genset.overrides)
            if base is not None:
                comps.genset = ResolvedGenset(**base.model_dump(),
                                              sizes_kw=self.sizes("components.genset.sizes_kw", c.genset.sizes_kw))

        # dispatch
        d = inp.dispatch
        gd = d.grid if d else None
        period_names = tariff.period_names if tariff is not None else ["all"]
        dis_periods = self.pick("dispatch.grid.battery_discharge_periods", gd.battery_discharge_periods if gd else None)
        ch_periods = self.pick("dispatch.grid.charge_periods", gd.charge_periods if gd else None)
        if g.mode == "grid_connected" and tariff is not None:
            for path, plist in (("dispatch.grid.battery_discharge_periods", dis_periods),
                                ("dispatch.grid.charge_periods", ch_periods)):
                bad = [p for p in plist if p not in period_names]
                if bad and gd is not None and getattr(gd, path.rsplit(".", 1)[1]) is not None:
                    self.errors.append(_err(ErrorCode.VALIDATION_FAILED,
                                            f"Unknown tariff period(s) {bad}; tariff periods are {period_names}.",
                                            path))
            if tariff.charges.energy.type != "tou" and not (gd and gd.battery_discharge_periods):
                dis_periods = ["all"]
                self.assumptions.append(Assumption(path="dispatch.grid.battery_discharge_periods", value=["all"],
                                                   origin="system_default", date=SYSTEM_DATE,
                                                   source="Non-TOU tariff: battery may discharge at any hour"))
        dispatch = ResolvedDispatch(
            strategy=self.pick("dispatch.strategy", d.strategy if d else None),
            cc_setpoint_soc=float(self.pick("dispatch.cc_setpoint_soc", d.cc_setpoint_soc if d else None)),
            cc_hold_until_setpoint=bool(self.pick("dispatch.cc_hold_until_setpoint",
                                                  d.cc_hold_until_setpoint if d else None)),
            battery_discharge_periods=[p for p in dis_periods if p in period_names],
            grid_charging=bool(self.pick("dispatch.grid.grid_charging", gd.grid_charging if gd else None)),
            charge_periods=[p for p in ch_periods if p in period_names],
            soc_reserve_for_outage=float(self.pick("dispatch.grid.soc_reserve_for_outage",
                                                   gd.soc_reserve_for_outage if gd else None)))

        # economics
        e = inp.economics
        life = int(self.pick("economics.project_life_years", e.project_life_years if e else None))
        nominal = inflation = None
        if e is not None and e.real_discount_rate is not None:
            real = self.pick("economics.real_discount_rate", e.real_discount_rate)
        else:
            nominal = float(self.pick("economics.nominal_discount_rate", e.nominal_discount_rate if e else None))
            inflation = float(self.pick("economics.inflation_rate", e.inflation_rate if e else None))
            real = (nominal - inflation) / (1 + inflation)
        em = pack.emissions
        for path, s in (("economics.diesel_kg_co2_per_l", em.diesel_kg_co2_per_l),
                        ("economics.grid_kg_co2_per_kwh", em.grid_kg_co2_per_kwh),
                        ("economics.grid_renewable_fraction", em.grid_renewable_fraction)):
            self.assumptions.append(Assumption(path=path, value=s.value, origin="pack_default",
                                               source=f"{pack.ref}: {s.source}", date=s.date))
        currency = self.pick("economics.currency", e.currency if e else None,
                             system=(pack.manifest.currency, f"Pack currency ({pack.ref})"))
        if currency != pack.manifest.currency:
            self.errors.append(_err(ErrorCode.UNSUPPORTED_COMBINATION,
                                    f"Results are computed in the pack currency {pack.manifest.currency}.",
                                    "economics.currency", "Remove economics.currency or set it to the pack currency."))
        economics = ResolvedEconomics(
            currency=pack.manifest.currency, project_life_years=life, real_discount_rate=float(real),
            nominal_discount_rate=nominal, inflation_rate=inflation,
            fuel_price_per_l=float(self.pick("economics.fuel_price_per_l", e.fuel_price_per_l if e else None)),
            fuel_price_escalation_real=float(self.pick("economics.fuel_price_escalation_real",
                                                       e.fuel_price_escalation_real if e else None)),
            grid_price_escalation_real=float(self.pick("economics.grid_price_escalation_real",
                                                       e.grid_price_escalation_real if e else None)),
            system_fixed_capital=float(self.pick("economics.system_fixed_capital",
                                                 e.system_fixed_capital if e else None)),
            system_fixed_om_per_year=float(self.pick("economics.system_fixed_om_per_year",
                                                     e.system_fixed_om_per_year if e else None)),
            fx_rate_per_usd=e.fx_rate_per_usd if e else None,
            diesel_kg_co2_per_l=float(em.diesel_kg_co2_per_l.value),
            grid_kg_co2_per_kwh=float(em.grid_kg_co2_per_kwh.value),
            grid_renewable_fraction=float(em.grid_renewable_fraction.value))

        # constraints
        k = inp.constraints
        max_pv = k.max_pv_kwp if k else None
        if k is not None and k.roof_area_m2 is not None:
            dens = float(pack.defaults.roof_kwp_per_m2.value)
            roof_kwp = round(k.roof_area_m2 * dens, 3)
            self.assumptions.append(Assumption(path="constraints.roof_kwp_per_m2", value=dens, origin="pack_default",
                                               source=f"{pack.ref}: {pack.defaults.roof_kwp_per_m2.source}",
                                               date=pack.defaults.roof_kwp_per_m2.date))
            max_pv = min(max_pv, roof_kwp) if max_pv is not None else roof_kwp
        constraints = ResolvedConstraints(
            max_capacity_shortage=float(self.pick("constraints.max_capacity_shortage",
                                                  k.max_capacity_shortage if k else None)),
            min_renewable_fraction=k.min_renewable_fraction if k else None,
            max_genset_hours=k.max_genset_hours if k else None,
            max_export_kw=k.max_export_kw if k else None,
            max_pv_kwp=max_pv,
            max_initial_capital=k.max_initial_capital if k else None)
        options = ResolvedOptions(
            analysis_date=analysis_date,
            demand_peak_factor=float(self.pick("options.demand_peak_factor",
                                               opts.demand_peak_factor if opts else None)),
            max_candidates=int(self.pick("options.max_candidates", opts.max_candidates if opts else None)),
            keep_timeseries_top_n=int(self.pick("options.keep_timeseries_top_n",
                                                opts.keep_timeseries_top_n if opts else None)))
        seed = int(self.pick("seed", inp.seed))

        site_ref = SiteRef(site_id=site["site_id"], name=site["name"], latitude=site["latitude"],
                           longitude=site["longitude"], elevation_m=float(site.get("elevation_m") or 0.0),
                           timezone=site["timezone"], country_pack=site["country_pack"])
        if site.get("elevation_m") is None:
            self.assumptions.append(Assumption(path="site.elevation_m", value=0.0, origin="system_default",
                                               source="Sea level assumed: no elevation supplied", date=SYSTEM_DATE))
        if self.errors and any(i.code == ErrorCode.NOT_FOUND.value for i in self.errors):
            return None
        resolved = ResolvedScenario(name=inp.name, site=site_ref, loads=loads, resource=resource, grid=grid,
                                    components=comps, dispatch=dispatch, economics=economics,
                                    constraints=constraints, options=options, seed=seed,
                                    pack={pack.manifest.id: pack.manifest.version})
        self._rules(resolved)
        return resolved

    # -------------------------------------------------------------- validation rules (FR-SCN-009, FR-CMP-004)
    def _rules(self, s: ResolvedScenario) -> None:
        c = s.components
        if s.grid.mode == "off_grid" and c.genset is None and c.bess is None:
            self.warnings.append(warning(WarningCode.PLAUSIBILITY,
                                         "Off-grid system with no genset or battery: load can only be served "
                                         "while the sun shines or the wind blows.", "components"))
        if s.grid.mode == "off_grid" and c.pv is None and c.wind is None and c.genset is None:
            self.errors.append(_err(ErrorCode.VALIDATION_FAILED, "An off-grid scenario needs a generating "
                                    "component (pv, wind or genset).", "components"))
        if s.grid.export_scheme == "net_plus" and c.bess is not None and any(x > 0 for x in c.bess.sizes_kwh):
            self.errors.append(_err(ErrorCode.UNSUPPORTED_COMBINATION,
                                    "Net plus with a battery is not supported in this release.",
                                    "grid.export_scheme", "Use net_accounting, or remove the battery."))
        if c.bess is not None:
            t = c.bess.technical
            if not 0.5 <= t["round_trip_efficiency"] <= 1.0:
                self.errors.append(_err(ErrorCode.VALIDATION_FAILED, "Round-trip efficiency must be 0.5–1.0.",
                                        "components.bess.technical.round_trip_efficiency"))
            elif t["round_trip_efficiency"] < 0.8:
                self.warnings.append(warning(WarningCode.PLAUSIBILITY, "Round-trip efficiency below 0.8 is unusual "
                                             "for lithium batteries.", "components.bess.technical"))
            if not 0.0 <= t["soc_min"] <= 0.9:
                self.errors.append(_err(ErrorCode.VALIDATION_FAILED, "Minimum SOC must be 0–0.9.",
                                        "components.bess.technical.soc_min"))
        if c.genset is not None and not 0.0 <= c.genset.technical["min_load_ratio"] <= 0.6:
            self.errors.append(_err(ErrorCode.VALIDATION_FAILED, "Genset minimum load ratio must be 0–0.6.",
                                    "components.genset.technical.min_load_ratio"))
        if not -0.02 <= s.economics.real_discount_rate <= 0.2:
            self.warnings.append(warning(WarningCode.PLAUSIBILITY, f"Real discount rate "
                                         f"{s.economics.real_discount_rate:.3f} is unusual.", "economics"))
        # peak load versus maximum possible supply
        peak = 0.0
        for ref in s.loads:
            ds = get_dataset(self.app, ref.dataset_id)
            peak += float(ds["stats"].get("peak_kw", 0.0))
        supply = 0.0
        if c.pv:
            supply += max(c.pv.sizes_kwp) / c.pv.dc_ac_ratio
        if c.wind:
            supply += max(c.wind.counts) * float(c.wind.technical["rated_kw"])
        if c.genset:
            supply += max(c.genset.sizes_kw)
        if c.bess:
            supply += max(c.bess.sizes_kwh) * c.bess.power_kw_per_kwh
        if s.grid.mode == "grid_connected":
            supply += s.grid.max_import_kw
        if peak > supply + 1e-9:
            self.warnings.append(warning(WarningCode.PLAUSIBILITY,
                                         f"Peak load ({peak:.1f} kW) exceeds the largest possible supply "
                                         f"({supply:.1f} kW); every candidate will have unserved load.",
                                         "components", "Add larger sizes or a genset."))
        count = candidate_count(s)
        if count > s.options.max_candidates:
            self.errors.append(_err(ErrorCode.SEARCH_SPACE_TOO_LARGE,
                                    f"The search space has {count} candidates; the limit is "
                                    f"{s.options.max_candidates}.", "components",
                                    "Reduce the number of sizes per component."))


def _jsonable(v: Any) -> Any:
    if isinstance(v, dt.date):
        return v.isoformat()
    if hasattr(v, "model_dump"):
        return v.model_dump(mode="json")
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def size_axes(s: ResolvedScenario) -> dict[str, list[float]]:
    c = s.components
    return {
        "pv_kwp": c.pv.sizes_kwp if c.pv else [0.0],
        "wind_count": c.wind.counts if c.wind else [0.0],
        "bess_kwh": c.bess.sizes_kwh if c.bess else [0.0],
        "genset_kw": c.genset.sizes_kw if c.genset else [0.0],
    }


def candidate_count(s: ResolvedScenario) -> int:
    n = 1
    for axis in size_axes(s).values():
        n *= len(axis)
    return n


def enumerate_candidates(s: ResolvedScenario) -> list[tuple[float, float, float, float]]:
    ax = size_axes(s)
    return list(itertools.product(ax["pv_kwp"], ax["wind_count"], ax["bess_kwh"], ax["genset_kw"]))


def canonical_json(s: ResolvedScenario) -> str:
    """Canonical JSON of the inputs. Generated IDs are excluded; datasets enter through their content hashes."""
    data = s.model_dump(mode="json")
    data["site"].pop("site_id", None)
    for ref in [*data["loads"], data["resource"]]:
        ref.pop("dataset_id", None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def scenario_hash(s: ResolvedScenario) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(s).encode()).hexdigest()


# --------------------------------------------------------------------------- public API


def create_scenario(app: Helionyx, scenario: ScenarioInput, parent_scenario_id: str | None = None) -> dict[str, Any]:
    r = _Resolver(app, scenario)
    resolved = r.resolve()
    if resolved is None:
        first = r.errors[0]
        raise HelionyxError(ErrorCode(first.code), first.message, first.hint or "Fix the referenced ID.",
                            {"errors": [e.model_dump() for e in r.errors][:10]})
    big = [e for e in r.errors if e.code == ErrorCode.SEARCH_SPACE_TOO_LARGE.value]
    if big:
        raise HelionyxError(ErrorCode.SEARCH_SPACE_TOO_LARGE, big[0].message,
                            big[0].hint or "Reduce the search space.",
                            {"candidate_count": candidate_count(resolved),
                             "limit": resolved.options.max_candidates})
    h = scenario_hash(resolved)
    root_id, version = None, 1
    if parent_scenario_id:
        parent = get_scenario(app, parent_scenario_id)
        root_id, version = parent["root_id"], int(parent["version"]) + 1
        rows = app.db.query("SELECT MAX(version) AS v FROM scenarios WHERE root_id = ?", (root_id,))
        version = max(version, int(rows[0]["v"] or 0) + 1)
    scenario_id = new_id("scn")
    doc = {"scenario_id": scenario_id, "root_id": root_id or scenario_id, "version": version, "name": scenario.name,
           "scenario_hash": h, "candidate_count": candidate_count(resolved),
           "input": scenario.model_dump(mode="json", exclude_none=True), "resolved": resolved.model_dump(mode="json"),
           "errors": [e.model_dump() for e in r.errors], "warnings": [w.model_dump() for w in r.warnings],
           "assumptions": [a.model_dump(mode="json") for a in r.assumptions], "locked": False,
           "created_at": now_iso()}
    app.db.put("scenarios", scenario_id, doc, root_id=doc["root_id"], version=version, scenario_hash=h, locked=0)
    return doc


def get_scenario(app: Helionyx, scenario_id: str) -> dict[str, Any]:
    return app.db.get("scenarios", scenario_id, "scenario")


def resolved_of(doc: dict[str, Any]) -> ResolvedScenario:
    return ResolvedScenario.model_validate(doc["resolved"])


def lock_scenario(app: Helionyx, doc: dict[str, Any]) -> None:
    if not doc.get("locked"):
        doc["locked"] = True
        app.db.put("scenarios", doc["scenario_id"], doc, root_id=doc["root_id"], version=doc["version"],
                   scenario_hash=doc["scenario_hash"], locked=1)


def validate_scenario(app: Helionyx, scenario_id: str) -> dict[str, Any]:
    doc = get_scenario(app, scenario_id)
    # Re-run resolution so warnings reflect the current pack and date.
    r = _Resolver(app, ScenarioInput.model_validate(doc["input"]))
    resolved = r.resolve()
    current_hash = scenario_hash(resolved) if resolved is not None else None
    return {"scenario_id": scenario_id, "scenario_hash": doc["scenario_hash"], "valid": not r.errors,
            "inputs_changed_since_creation": current_hash is not None and current_hash != doc["scenario_hash"],
            "candidate_count": doc["candidate_count"],
            "errors": [e.model_dump() for e in r.errors], "warnings": [w.model_dump() for w in r.warnings],
            "assumptions": [a.model_dump(mode="json") for a in r.assumptions]}


def scenario_to_yaml(doc: dict[str, Any]) -> str:
    return yaml.safe_dump({"scenario": doc["input"]}, sort_keys=False, allow_unicode=True)


def scenario_from_yaml(text: str) -> ScenarioInput:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise validation("Scenario YAML could not be parsed.", "Check indentation and quoting.") from exc
    if not isinstance(data, dict) or "scenario" not in data:
        raise validation("Scenario YAML must have a top-level 'scenario' key.", "See SRS §6.6 for the format.")
    try:
        return ScenarioInput.model_validate(data["scenario"])
    except ValidationError as exc:
        e = exc.errors()[0]
        raise validation(f"Scenario field {'.'.join(str(x) for x in e['loc'])}: {e['msg']}",
                         "Fix the field and retry.") from exc
