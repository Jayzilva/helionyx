"""Scenario schemas (SRS §5.5, §6.6).

``ScenarioInput`` is what a user or LLM supplies: almost every field is optional.
``ResolvedScenario`` is the fully specified, canonical scenario after defaults
from the country pack have been applied. Its canonical JSON is hashed (FR-SCN-006).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from helionyx.core.models.packs import Tariff


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SizeRange(Strict):
    min: float = Field(ge=0)
    max: float = Field(ge=0)
    step: float = Field(gt=0)

    @model_validator(mode="after")
    def _order(self) -> SizeRange:
        if self.max < self.min:
            raise ValueError("range max must be >= min")
        return self


Sizes = list[float] | SizeRange


# --------------------------------------------------------------------------- input


class OutageWindow(Strict):
    days: Literal["all", "weekday", "weekend"] = "all"
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


class AvailabilityInput(Strict):
    type: Literal["always", "scheduled", "stochastic"] = "always"
    windows: list[OutageWindow] | None = None
    events_per_year: float | None = Field(None, ge=0, le=2000)
    mean_duration_h: float | None = Field(None, gt=0, le=240)
    seed: int | None = None


ExportScheme = Literal["none", "net_metering", "net_accounting", "net_plus"]


class GridInput(Strict):
    mode: Literal["off_grid", "grid_connected"]
    tariff_id: str | None = None
    tariff: Tariff | None = Field(None, description="Inline custom tariff (FR-TAR-006)")
    export_scheme: ExportScheme | None = None
    max_import_kw: float | None = Field(None, gt=0)
    max_export_kw: float | None = Field(None, ge=0)
    availability: AvailabilityInput | None = None


class PvInput(Strict):
    spec: str | None = None
    sizes_kwp: Sizes
    dc_ac_ratio: float | None = Field(None, ge=0.8, le=2.0)
    tilt_deg: float | None = Field(None, ge=0, le=90)
    azimuth_deg: float | None = Field(None, ge=0, lt=360)
    overrides: dict[str, Any] = Field(default_factory=dict)


class WindInput(Strict):
    spec: str | None = None
    counts: Sizes
    overrides: dict[str, Any] = Field(default_factory=dict)


class BessInput(Strict):
    spec: str | None = None
    sizes_kwh: Sizes
    power_kw_per_kwh: float | None = Field(None, gt=0, le=4)
    overrides: dict[str, Any] = Field(default_factory=dict)


class GensetInput(Strict):
    spec: str | None = None
    sizes_kw: Sizes
    overrides: dict[str, Any] = Field(default_factory=dict)


class ConverterInput(Strict):
    spec: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


class ComponentsInput(Strict):
    pv: PvInput | None = None
    wind: WindInput | None = None
    bess: BessInput | None = None
    genset: GensetInput | None = None
    converter: ConverterInput | None = None


class GridDispatchInput(Strict):
    battery_discharge_periods: list[str] | None = None
    grid_charging: bool | None = None
    charge_periods: list[str] | None = None
    soc_reserve_for_outage: float | None = Field(None, ge=0, le=1)


class DispatchInput(Strict):
    strategy: Literal["load_following", "cycle_charging"] | None = None
    cc_setpoint_soc: float | None = Field(None, gt=0, le=1)
    cc_hold_until_setpoint: bool | None = None
    grid: GridDispatchInput | None = None


class EconomicsInput(Strict):
    currency: str | None = None
    project_life_years: int | None = Field(None, ge=1, le=50)
    nominal_discount_rate: float | None = Field(None, ge=-0.05, le=0.5)
    inflation_rate: float | None = Field(None, ge=-0.05, le=0.5)
    real_discount_rate: float | None = Field(None, ge=-0.05, le=0.5)
    fuel_price_per_l: float | None = Field(None, ge=0)
    fuel_price_escalation_real: float | None = Field(None, ge=-0.2, le=0.3)
    grid_price_escalation_real: float | None = Field(None, ge=-0.2, le=0.3)
    system_fixed_capital: float | None = Field(None, ge=0)
    system_fixed_om_per_year: float | None = Field(None, ge=0)
    fx_rate_per_usd: float | None = Field(None, gt=0, description="Local currency per USD, user supplied")


class ConstraintsInput(Strict):
    max_capacity_shortage: float | None = Field(None, ge=0, le=1)
    min_renewable_fraction: float | None = Field(None, ge=0, le=1)
    max_genset_hours: float | None = Field(None, ge=0, le=8760)
    max_export_kw: float | None = Field(None, ge=0)
    max_pv_kwp: float | None = Field(None, ge=0)
    roof_area_m2: float | None = Field(None, gt=0)
    max_initial_capital: float | None = Field(None, gt=0)


class OptionsInput(Strict):
    analysis_date: dt.date | None = None
    demand_peak_factor: float | None = Field(None, ge=1.0, le=2.0)
    max_candidates: int | None = Field(None, ge=1, le=200_000)
    keep_timeseries_top_n: int | None = Field(None, ge=0, le=50)


class ExpansionStageInput(Strict):
    """Capacity added at the start of project year ``year`` (FR-OPT-007). Each list is searched."""

    year: int = Field(ge=2, le=50)
    pv_add_kwp: Sizes | None = None
    wind_add_count: list[int] | None = None
    bess_add_kwh: Sizes | None = None
    genset_add_kw: Sizes | None = None


class MultiYearInput(Strict):
    """Multi-year analysis: load growth (FR-LOAD-009) and capacity expansion (FR-OPT-007)."""

    load_growth_rate: float | None = Field(None, ge=-0.1, le=0.3, description="Annual growth of load energy")
    sample_every_years: int | None = Field(None, ge=1, le=10, description=(
        "Simulate every k-th year and interpolate between; stage years and the last year are always simulated"))
    expansion: list[ExpansionStageInput] = Field(default_factory=list, max_length=3)


class ScenarioInput(Strict):
    name: str = Field(max_length=256)
    site_id: str
    load_ids: list[str] = Field(min_length=1, max_length=20)
    resource_id: str
    grid: GridInput
    components: ComponentsInput
    dispatch: DispatchInput | None = None
    economics: EconomicsInput | None = None
    constraints: ConstraintsInput | None = None
    options: OptionsInput | None = None
    multi_year: MultiYearInput | None = None
    seed: int | None = None


# --------------------------------------------------------------------------- resolved


class ResolvedComponent(Strict):
    spec: str
    name: str
    capital_per_unit: float
    replacement_per_unit: float
    om_per_unit_year: float
    lifetime_years: float
    technical: dict[str, Any]


class ResolvedPv(ResolvedComponent):
    sizes_kwp: list[float]
    dc_ac_ratio: float
    tilt_deg: float
    azimuth_deg: float


class ResolvedWind(ResolvedComponent):
    counts: list[float]


class ResolvedBess(ResolvedComponent):
    sizes_kwh: list[float]
    power_kw_per_kwh: float


class ResolvedGenset(ResolvedComponent):
    sizes_kw: list[float]


class ResolvedComponents(Strict):
    pv: ResolvedPv | None = None
    wind: ResolvedWind | None = None
    bess: ResolvedBess | None = None
    genset: ResolvedGenset | None = None
    converter: ResolvedComponent | None = None


class ResolvedAvailability(Strict):
    type: Literal["always", "scheduled", "stochastic"]
    windows: list[OutageWindow] = Field(default_factory=list)
    events_per_year: float = 0.0
    mean_duration_h: float = 0.0
    seed: int = 0


class ResolvedGrid(Strict):
    mode: Literal["off_grid", "grid_connected"]
    tariff: Tariff | None
    export_scheme: ExportScheme
    max_import_kw: float
    max_export_kw: float
    availability: ResolvedAvailability


class ResolvedDispatch(Strict):
    strategy: Literal["load_following", "cycle_charging"]
    cc_setpoint_soc: float
    cc_hold_until_setpoint: bool
    battery_discharge_periods: list[str]
    grid_charging: bool
    charge_periods: list[str]
    soc_reserve_for_outage: float


class ResolvedEconomics(Strict):
    currency: str
    project_life_years: int
    real_discount_rate: float
    nominal_discount_rate: float | None
    inflation_rate: float | None
    fuel_price_per_l: float
    fuel_price_escalation_real: float
    grid_price_escalation_real: float
    system_fixed_capital: float
    system_fixed_om_per_year: float
    fx_rate_per_usd: float | None
    diesel_kg_co2_per_l: float
    grid_kg_co2_per_kwh: float
    grid_renewable_fraction: float


class ResolvedConstraints(Strict):
    max_capacity_shortage: float
    min_renewable_fraction: float | None
    max_genset_hours: float | None
    max_export_kw: float | None
    max_pv_kwp: float | None
    max_initial_capital: float | None


class ResolvedOptions(Strict):
    analysis_date: dt.date
    demand_peak_factor: float
    max_candidates: int
    keep_timeseries_top_n: int


class ResolvedExpansionStage(Strict):
    year: int
    pv_add_kwp: list[float]
    wind_add_count: list[float]
    bess_add_kwh: list[float]
    genset_add_kw: list[float]


class ResolvedMultiYear(Strict):
    load_growth_rate: float
    sample_every_years: int
    expansion: list[ResolvedExpansionStage]


class SiteRef(Strict):
    site_id: str
    name: str
    latitude: float
    longitude: float
    elevation_m: float
    timezone: str
    country_pack: str


class DatasetRef(Strict):
    dataset_id: str
    content_hash: str
    synthetic: bool


class ResolvedScenario(Strict):
    name: str
    site: SiteRef
    loads: list[DatasetRef]
    resource: DatasetRef
    grid: ResolvedGrid
    components: ResolvedComponents
    dispatch: ResolvedDispatch
    economics: ResolvedEconomics
    constraints: ResolvedConstraints
    options: ResolvedOptions
    seed: int
    pack: dict[str, str]
    multi_year: ResolvedMultiYear | None = None


class Assumption(BaseModel):
    path: str
    value: Any
    origin: Literal["user", "pack_default", "system_default"]
    source: str | None = None
    date: dt.date | None = None
