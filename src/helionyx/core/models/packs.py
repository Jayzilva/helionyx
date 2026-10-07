"""Schemas for country data packs (SRS §6.3–6.5).

Packs are country-specific; the engine never hard-codes tariff, cost or emission
values (constraints C3, C4). Every record carries a source and a date.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DayType = Literal["all", "weekday", "weekend"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Strict):
    document: str
    url: str | None = None
    retrieved_at: dt.date
    verified_by: str | None = None


class Sourced(Strict):
    """A single default value with its source and date (PRD principle 2)."""

    value: Any
    source: str
    date: dt.date


# --------------------------------------------------------------------------- manifest


class PackManifest(Strict):
    id: str
    name: str
    version: str = Field(pattern=r"^\d{4}\.\d{2}\.\d+$", description="CalVer YYYY.MM.patch")
    last_verified: dt.date
    maintainers: list[str]
    license: str
    timezone: str
    currency: str
    weekend_days: list[int] = Field(default=[6], description="ISO weekday numbers (Mon=1 .. Sun=7)")
    staleness_months: int = 6


# --------------------------------------------------------------------------- tariffs


class TouPeriod(Strict):
    name: str
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    days: DayType = "all"


class Block(Strict):
    up_to_kwh: float | None = Field(None, description="Upper bound of the slab; null for the last slab")
    rate_per_kwh: float = Field(ge=0)
    fixed_monthly: float | None = Field(None, ge=0, description="Fixed charge when consumption ends in this slab")


class EnergyCharge(Strict):
    type: Literal["flat", "block", "tou"]
    rate_per_kwh: float | None = Field(None, ge=0)
    blocks: list[Block] | None = None
    rates_per_kwh: dict[str, float] | None = None

    @model_validator(mode="after")
    def _check(self) -> EnergyCharge:
        if self.type == "flat" and self.rate_per_kwh is None:
            raise ValueError("flat energy charge needs rate_per_kwh")
        if self.type == "block" and not self.blocks:
            raise ValueError("block energy charge needs blocks")
        if self.type == "tou" and not self.rates_per_kwh:
            raise ValueError("tou energy charge needs rates_per_kwh")
        return self


class DemandCharge(Strict):
    per_kva_month: float = Field(0.0, ge=0)
    power_factor_assumption: float = Field(0.9, gt=0, le=1)


class Charges(Strict):
    fixed_monthly: float = Field(0.0, ge=0)
    energy: EnergyCharge
    demand: DemandCharge | None = None
    minimum_monthly: float = Field(0.0, ge=0)
    levies_pct: float = Field(0.0, ge=0, le=100)


class NetMetering(Strict):
    netting: Literal["by_period", "total"] = "total"
    credit_carry_forward: bool = True
    year_end: Literal["forfeit", "pay"] = "forfeit"
    year_end_rate_per_kwh: float = Field(0.0, ge=0)


class ExportRate(Strict):
    export_rate_per_kwh: float = Field(ge=0)
    settlement: Literal["monthly"] = "monthly"


class ExportSchemes(Strict):
    net_metering: NetMetering | None = None
    net_accounting: ExportRate | None = None
    net_plus: ExportRate | None = None


class Tariff(Strict):
    id: str
    utility: str
    category: str
    name: str
    currency: str
    effective_from: dt.date
    effective_to: dt.date | None = None
    status: Literal["verified", "unverified"] = "unverified"
    source: Source
    tou_periods: list[TouPeriod] | None = None
    charges: Charges
    export_schemes: ExportSchemes = Field(default_factory=ExportSchemes)
    notes: str | None = None

    @model_validator(mode="after")
    def _check_periods(self) -> Tariff:
        if self.charges.energy.type == "tou":
            if not self.tou_periods:
                raise ValueError("tou tariff needs tou_periods")
            names = {p.name for p in self.tou_periods}
            missing = set(self.charges.energy.rates_per_kwh or {}) ^ names
            if missing:
                raise ValueError(f"tou rates and periods disagree: {sorted(missing)}")
        return self

    @property
    def period_names(self) -> list[str]:
        if self.charges.energy.type == "tou" and self.tou_periods:
            seen: list[str] = []
            for p in self.tou_periods:
                if p.name not in seen:
                    seen.append(p.name)
            return seen
        return ["all"]


# --------------------------------------------------------------------------- components

ComponentType = Literal["pv", "wind", "bess", "genset", "converter"]
COST_FIELDS = ("capital_per_unit", "replacement_per_unit", "om_per_unit_year", "lifetime_years")
UNIT_BY_TYPE: dict[str, str] = {"pv": "kWp", "wind": "turbine", "bess": "kWh", "genset": "kW", "converter": "kW"}


class PvTechnical(Strict):
    derating_factor: float = Field(0.9, gt=0, le=1)
    temp_coeff_pct_per_c: float = Field(-0.35, ge=-1, le=0)
    inverter_efficiency: float = Field(0.97, gt=0, le=1)
    faiman_u0: float = 25.0
    faiman_u1: float = 6.84
    albedo: float = Field(0.2, ge=0, le=1)


class WindTechnical(Strict):
    rated_kw: float = Field(gt=0)
    hub_height_m: float = Field(gt=0)
    power_curve: list[tuple[float, float]] = Field(description="(wind speed m/s, output kW) pairs")
    availability: float = Field(0.95, gt=0, le=1)
    roughness_length_m: float = Field(0.03, gt=0)
    air_density_correction: bool = True

    @field_validator("power_curve")
    @classmethod
    def _sorted(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        speeds = [p[0] for p in v]
        if speeds != sorted(speeds) or len(v) < 2:
            raise ValueError("power curve must have at least two points sorted by wind speed")
        return v


class BessTechnical(Strict):
    round_trip_efficiency: float = Field(0.92)
    soc_min: float = Field(0.1)
    soc_initial: float = Field(1.0, ge=0, le=1)
    c_rate_max: float = Field(0.5, gt=0)
    float_life_years: float = Field(12, gt=0)
    lifetime_throughput_kwh_per_kwh: float = Field(3000, gt=0)


class GensetTechnical(Strict):
    min_load_ratio: float = Field(0.25)
    fuel_curve_intercept_l_per_h_per_kw: float = Field(0.08145, ge=0)
    fuel_curve_slope_l_per_kwh: float = Field(0.246, gt=0)
    lifetime_hours: float = Field(15000, gt=0)


class ConverterTechnical(Strict):
    efficiency: float = Field(0.96, gt=0, le=1)


TECHNICAL_MODELS: dict[str, type[Strict]] = {
    "pv": PvTechnical,
    "wind": WindTechnical,
    "bess": BessTechnical,
    "genset": GensetTechnical,
    "converter": ConverterTechnical,
}


class Component(Strict):
    id: str
    type: ComponentType
    name: str
    currency: str
    cost_year: int
    status: Literal["verified", "unverified"] = "unverified"
    source: Source
    unit: str
    capital_per_unit: float = Field(ge=0)
    replacement_per_unit: float = Field(ge=0)
    om_per_unit_year: float = Field(ge=0)
    lifetime_years: float = Field(gt=0)
    technical: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Component:
        if self.unit != UNIT_BY_TYPE[self.type]:
            raise ValueError(f"{self.type} costs must be per {UNIT_BY_TYPE[self.type]}, not {self.unit}")
        TECHNICAL_MODELS[self.type].model_validate(self.technical)
        return self

    def technical_model(self) -> Any:
        return TECHNICAL_MODELS[self.type].model_validate(self.technical)


# --------------------------------------------------------------------------- archetypes


class Archetype(Strict):
    id: str
    name: str
    basis: str
    synthetic: bool = True
    unit_label: str = "unit"
    typical_annual_kwh: float = Field(gt=0, description="Annual energy of one unit")
    weekday: list[float] = Field(min_length=24, max_length=24)
    weekend: list[float] = Field(min_length=24, max_length=24)
    monthly: list[float] = Field(min_length=12, max_length=12)
    day_sigma: float = Field(0.10, ge=0, le=1)
    hour_sigma: float = Field(0.05, ge=0, le=1)

    @field_validator("weekday", "weekend", "monthly")
    @classmethod
    def _non_negative(cls, v: list[float]) -> list[float]:
        if any(x < 0 for x in v) or sum(v) <= 0:
            raise ValueError("shape values must be non-negative with a positive sum")
        return v


# --------------------------------------------------------------------------- emissions and defaults


class Emissions(Strict):
    diesel_kg_co2_per_l: Sourced
    grid_kg_co2_per_kwh: Sourced
    grid_renewable_fraction: Sourced


class PackDefaults(Strict):
    """Flat map of dotted scenario paths to sourced default values."""

    values: dict[str, Sourced]
    default_components: dict[str, str]
    roof_kwp_per_m2: Sourced
