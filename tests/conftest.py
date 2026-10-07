from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from helionyx.core.models.scenario import ScenarioInput
from helionyx.infra.settings import Settings
from helionyx.services import resource, scenario
from helionyx.services.context import Helionyx
from helionyx.services.load import LoadComponent, synthesize_load

ROOT = Path(__file__).resolve().parent.parent
NEGOMBO = (7.2083, 79.8358)
DELFT = (9.5167, 79.6833)
HATTON = (6.8916, 80.5955)


def make_app(tmp: Path) -> Helionyx:
    s = Settings(workspace=tmp.resolve(), offline=True)
    return Helionyx(s)


@pytest.fixture
def app(tmp_path: Path):
    a = make_app(tmp_path)
    yield a
    a.close()


@pytest.fixture(scope="session")
def shared_app(tmp_path_factory: pytest.TempPathFactory):
    a = make_app(tmp_path_factory.mktemp("shared"))
    yield a
    a.close()


def setup_site(app: Helionyx, coords: tuple[float, float] = NEGOMBO, archetypes: list[dict[str, Any]] | None = None,
               monthly_kwh: list[float] | None = None) -> dict[str, str]:
    site = resource.create_site(app, "test site", coords[0], coords[1])
    res = resource.fetch_resource(app, site["site_id"], "nasa_power", 2023)
    comps = [LoadComponent(**c) for c in (archetypes or [{"archetype": "hotel", "count": 60}])]
    ld = synthesize_load(app, site["site_id"], comps, monthly_kwh, None, 42)
    return {"site_id": site["site_id"], "resource_id": res["dataset_id"], "load_id": ld["dataset_id"]}


def hotel_input(ids: dict[str, str], **over: Any) -> ScenarioInput:
    data: dict[str, Any] = {
        "name": "hotel test",
        "site_id": ids["site_id"], "load_ids": [ids["load_id"]], "resource_id": ids["resource_id"],
        "grid": {"mode": "grid_connected", "tariff_id": "lk.ceb.H2", "export_scheme": "net_accounting"},
        "components": {"pv": {"sizes_kwp": [0, 100, 200]}, "bess": {"sizes_kwh": [0, 100, 200]}},
        "options": {"analysis_date": "2026-10-07"},
    }
    data.update(over)
    return ScenarioInput.model_validate(data)


def offgrid_input(ids: dict[str, str], **over: Any) -> ScenarioInput:
    data: dict[str, Any] = {
        "name": "village test",
        "site_id": ids["site_id"], "load_ids": [ids["load_id"]], "resource_id": ids["resource_id"],
        "grid": {"mode": "off_grid"},
        "components": {"pv": {"sizes_kwp": [0, 50, 100]}, "bess": {"sizes_kwh": [0, 150, 300]},
                       "genset": {"sizes_kw": [0, 40, 60]}},
        "constraints": {"max_capacity_shortage": 0.01},
        "options": {"analysis_date": "2026-10-07"},
    }
    data.update(over)
    return ScenarioInput.model_validate(data)


VILLAGE = [{"archetype": "rural_household", "count": 150}, {"archetype": "health_clinic", "count": 1},
           {"archetype": "school", "count": 1}]


@pytest.fixture(scope="session")
def hotel_ids(shared_app: Helionyx) -> dict[str, str]:
    return setup_site(shared_app)


@pytest.fixture(scope="session")
def village_ids(shared_app: Helionyx) -> dict[str, str]:
    return setup_site(shared_app, DELFT, VILLAGE)


def create(app: Helionyx, inp: ScenarioInput) -> dict[str, Any]:
    return scenario.create_scenario(app, inp)
