"""Self-contained study files for the CLI and reference cases.

A study file declares the site, resource and loads alongside the scenario, so a
researcher can run it without an MCP client (``helionyx run study.yaml``)::

    study:
      site: {name: Negombo hotel, latitude: 7.2083, longitude: 79.8358, country_pack: lk}
      resource: {source: nasa_power, year: 2023}
      loads:
        - components: [{archetype: hotel, count: 60}]
          monthly_kwh: [...]        # optional, 12 values
          seed: 42
    scenario:
      name: ...
      grid: {...}
      components: {...}

A plain scenario file (only the ``scenario`` key, with existing IDs) is also accepted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from helionyx.core.models.scenario import ScenarioInput
from helionyx.errors import validation
from helionyx.services import resource
from helionyx.services.context import Helionyx
from helionyx.services.load import LoadComponent, Variability, synthesize_load
from helionyx.services.scenario import create_scenario


class StudySite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    latitude: float
    longitude: float
    elevation_m: float | None = None
    timezone: str | None = None
    country_pack: str = "lk"


class StudyResource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = "nasa_power"
    year: int = 2023
    fill_long_gaps: bool = False


class StudyLoad(BaseModel):
    model_config = ConfigDict(extra="forbid")
    components: list[LoadComponent]
    monthly_kwh: list[float] | None = None
    variability: Variability | None = None
    seed: int = 42


class Study(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: StudySite
    resource: StudyResource = Field(default_factory=StudyResource)
    loads: list[StudyLoad] = Field(min_length=1)


def load_study_file(path: Path) -> tuple[Study | None, dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "scenario" not in data:
        raise validation("The file needs a top-level 'scenario' key.", "See docs/quickstart.md for the format.")
    study = Study.model_validate(data["study"]) if "study" in data else None
    return study, data["scenario"]


def create_from_study(app: Helionyx, path: Path) -> dict[str, Any]:
    study, scen = load_study_file(path)
    if study is not None:
        site = resource.create_site(app, study.site.name, study.site.latitude, study.site.longitude,
                                    study.site.elevation_m, study.site.timezone, study.site.country_pack)
        res = resource.fetch_resource(app, site["site_id"], study.resource.source, study.resource.year,
                                      study.resource.fill_long_gaps)
        load_ids = [synthesize_load(app, site["site_id"], ld.components, ld.monthly_kwh, ld.variability,
                                    ld.seed)["dataset_id"] for ld in study.loads]
        scen = {**scen, "site_id": site["site_id"], "resource_id": res["dataset_id"], "load_ids": load_ids}
    return create_scenario(app, ScenarioInput.model_validate(scen))
