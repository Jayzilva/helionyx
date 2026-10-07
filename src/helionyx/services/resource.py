"""Site and resource service (FR-RES-001…009)."""

from __future__ import annotations

import io
import zoneinfo
from typing import Any

import numpy as np
import pandas as pd

from helionyx.core.engine.timeaxis import HOURS, MONTH
from helionyx.core.timeseries import align_utc_to_local, drop_leap_day, fill_gaps, to_hourly_mean, utc_offset_hours
from helionyx.errors import ErrorCode, HelionyxError, WarningCode, validation, warning
from helionyx.infra.db import now_iso
from helionyx.infra.ids import new_id
from helionyx.infra.packs import get_pack
from helionyx.services.context import Helionyx

NASA_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc"
MAX_CSV_BYTES = 10 * 1024 * 1024
RESOURCE_COLUMNS = ("ghi_w_m2", "temp_c", "wind10_m_s", "wind50_m_s")
KIND_COLUMN = {"load": "load_kw", "ghi": "ghi_w_m2", "temp": "temp_c", "wind": "wind_m_s"}


def sanitise(text: str, limit: int = 100) -> str:
    """Names echoed back to the model are plain, short and single-line (NFR-SEC-05)."""
    clean = "".join(ch if ch.isprintable() else " " for ch in text).replace("\n", " ").strip()
    return clean[:limit]


# --------------------------------------------------------------------------- sites


def create_site(app: Helionyx, name: str, latitude: float, longitude: float, elevation_m: float | None = None,
                timezone: str | None = None, country_pack: str = "lk") -> dict[str, Any]:
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise validation("Latitude must be within ±90 and longitude within ±180 (WGS84 decimal degrees).",
                         "Check that latitude and longitude are not swapped.", latitude=latitude,
                         longitude=longitude)
    pack = get_pack(country_pack)
    tz = timezone or pack.manifest.timezone
    try:
        zoneinfo.ZoneInfo(tz)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
        raise validation(f"Unknown IANA time zone '{sanitise(tz)}'.", "Use a zone such as Asia/Colombo.") from exc
    site_id = new_id("site")
    doc = {"site_id": site_id, "name": sanitise(name), "latitude": latitude, "longitude": longitude,
           "elevation_m": elevation_m, "timezone": tz, "country_pack": country_pack,
           "elevation_origin": "user" if elevation_m is not None else "pending", "created_at": now_iso()}
    app.db.put("sites", site_id, doc)
    return doc


def get_site(app: Helionyx, site_id: str) -> dict[str, Any]:
    return app.db.get("sites", site_id, "site")


def _set_elevation(app: Helionyx, site: dict[str, Any], elevation_m: float, origin: str) -> None:
    if site.get("elevation_m") is None:
        site["elevation_m"] = elevation_m
        site["elevation_origin"] = origin
        app.db.put("sites", site["site_id"], site)


# --------------------------------------------------------------------------- datasets


def _store_dataset(app: Helionyx, site_id: str, kind: str, df: pd.DataFrame, source: str, synthetic: bool,
                   provenance: dict[str, Any], stats: dict[str, Any], flags: list[dict[str, Any]],
                   prefix: str) -> dict[str, Any]:
    content_hash, uri = app.artefacts.put_frame(df)
    dataset_id = new_id(prefix)
    doc = {"dataset_id": dataset_id, "site_id": site_id, "kind": kind, "source": source, "synthetic": synthetic,
           "resolution_min": 60, "columns": list(df.columns), "content_hash": content_hash, "artefact_uri": uri,
           "provenance": provenance, "stats": stats, "quality_flags": flags, "created_at": now_iso()}
    app.db.put("datasets", dataset_id, doc, site_id=site_id, kind=kind, content_hash=content_hash)
    return doc


def get_dataset(app: Helionyx, dataset_id: str) -> dict[str, Any]:
    return app.db.get("datasets", dataset_id, "dataset")


def dataset_frame(app: Helionyx, dataset_id: str) -> pd.DataFrame:
    doc = get_dataset(app, dataset_id)
    return app.artefacts.get_frame(doc["content_hash"])


def resource_stats(df: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "ghi_w_m2" in df:
        ghi = df["ghi_w_m2"].to_numpy()
        out["annual_ghi_kwh_m2"] = float(ghi.sum() / 1000.0)
        out["monthly_ghi_kwh_m2"] = [float(ghi[MONTH == m].sum() / 1000.0) for m in range(12)]
        out["mean_daily_ghi_kwh_m2"] = float(ghi.sum() / 1000.0 / 365.0)
    if "temp_c" in df:
        out["mean_temp_c"] = float(df["temp_c"].mean())
    if "wind10_m_s" in df:
        out["mean_wind10_m_s"] = float(df["wind10_m_s"].mean())
    if "wind50_m_s" in df:
        out["mean_wind50_m_s"] = float(df["wind50_m_s"].mean())
    return out


def _prepare_utc_frame(raw: pd.DataFrame, timezone: str, year: int, fill_long_gaps: bool,
                       ) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    """Leap-day drop, gap filling and local alignment for a UTC hourly frame with RESOURCE_COLUMNS."""
    idx = pd.DatetimeIndex(pd.to_datetime(raw["timestamp_utc"], utc=True))
    flags: list[dict[str, Any]] = []
    out: dict[str, np.ndarray] = {}
    offset = utc_offset_hours(timezone, year)
    method = ""
    dropped = False
    for col in RESOURCE_COLUMNS:
        if col not in raw:
            continue
        values = raw[col].to_numpy(dtype=np.float64)
        values = np.where(values <= -990, np.nan, values)  # NASA fill value -999
        i2, v2, dropped = drop_leap_day(idx, values)
        if len(v2) != HOURS:
            raise HelionyxError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                                f"Expected 8,760 hourly values after the leap-day drop, got {len(v2)}.",
                                "Request a complete calendar year.", {"column": col})
        filled, short, long, unfilled = fill_gaps(v2, fill_long_gaps)
        if unfilled:
            raise validation(f"{col} has gaps longer than 3 hours ({max(unfilled)} h).",
                             "Set fill_long_gaps=true to fill them with the same-hour mean of nearby days, "
                             "or choose another year or source.", column=col, gap_hours=unfilled[:5])
        if short or long:
            flags.append(warning(WarningCode.GAP_FILLED, f"{col}: {short} short and {long} long gap hours filled.",
                                 path=col).model_dump())
        aligned, method = align_utc_to_local(filled, offset)
        if col == "ghi_w_m2":
            aligned = np.clip(aligned, 0.0, None)
        out[col] = aligned
    prov = {"time_standard_source": "UTC", "utc_offset_hours": offset, "alignment": method,
            "leap_day_dropped": dropped}
    return pd.DataFrame(out), prov, flags


def _nasa_power(app: Helionyx, site: dict[str, Any], year: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    pack = get_pack(site["country_pack"])
    for key, info in pack.samples.items():
        if (abs(info["latitude"] - site["latitude"]) < 0.01 and abs(info["longitude"] - site["longitude"]) < 0.01
                and info["year"] == year):
            prov = {"source": "nasa_power", "url": info["url"], "parameters": list(RESOURCE_COLUMNS),
                    "retrieved_at": info["retrieved_at"], "license": info["license"],
                    "delivery": f"bundled sample '{key}' in pack {pack.ref}"}
            _set_elevation(app, site, float(info["elevation_m"]), "nasa_power")
            return pack.sample_resource(key), prov
    params = {"parameters": "ALLSKY_SFC_SW_DWN,T2M,WS10M,WS50M", "community": "RE",
              "longitude": round(site["longitude"], 4), "latitude": round(site["latitude"], 4),
              "start": f"{year}0101", "end": f"{year}1231", "format": "JSON", "time-standard": "UTC"}
    body, cached = app.http.get_json(NASA_URL, params, "NASA POWER")
    try:
        p = body["properties"]["parameter"]
        keys = sorted(p["T2M"].keys())
        raw = pd.DataFrame({
            "timestamp_utc": [f"{k[:4]}-{k[4:6]}-{k[6:8]}T{k[8:10]}:00Z" for k in keys],
            "ghi_w_m2": [p["ALLSKY_SFC_SW_DWN"][k] for k in keys],
            "temp_c": [p["T2M"][k] for k in keys],
            "wind10_m_s": [p["WS10M"][k] for k in keys],
            "wind50_m_s": [p["WS50M"][k] for k in keys],
        })
        elevation = float(body["geometry"]["coordinates"][2])
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        raise HelionyxError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, "NASA POWER returned an unexpected response.",
                            "Retry later or import the series as CSV.") from exc
    _set_elevation(app, site, elevation, "nasa_power")
    prov = {"source": "nasa_power", "url": NASA_URL, "parameters": ["ALLSKY_SFC_SW_DWN", "T2M", "WS10M", "WS50M"],
            "retrieved_at": now_iso(), "license": "NASA POWER: no restrictions; cite NASA Langley POWER Project",
            "delivery": "cache" if cached else "network"}
    return raw, prov


def _pvgis(app: Helionyx, site: dict[str, Any], year: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    params = {"lat": site["latitude"], "lon": site["longitude"], "startyear": year, "endyear": year,
              "outputformat": "json", "components": 0}
    body, cached = app.http.get_json(PVGIS_URL, params, "PVGIS")
    try:
        rows = body["outputs"]["hourly"]
        db = body["inputs"]["meteo_data"]["radiation_db"]
        raw = pd.DataFrame({
            # PVGIS hourly timestamps are UTC at HH:10 (centre of the satellite slot); treat as hour start.
            "timestamp_utc": [f"{r['time'][:4]}-{r['time'][4:6]}-{r['time'][6:8]}T{r['time'][9:11]}:00Z"
                              for r in rows],
            "ghi_w_m2": [r["G(i)"] for r in rows],
            "temp_c": [r["T2m"] for r in rows],
            "wind10_m_s": [r["WS10m"] for r in rows],
        })
    except (KeyError, TypeError, IndexError) as exc:
        raise HelionyxError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                            "PVGIS has no data for this location or year.",
                            "Use source='nasa_power' instead.") from exc
    prov = {"source": "pvgis", "url": PVGIS_URL, "radiation_db": db, "retrieved_at": now_iso(),
            "license": "PVGIS © European Union", "delivery": "cache" if cached else "network"}
    return raw, prov


def fetch_resource(app: Helionyx, site_id: str, source: str = "nasa_power", year: int = 2023,
                   fill_long_gaps: bool = False) -> dict[str, Any]:
    site = get_site(app, site_id)
    if not 2001 <= year <= 2100:
        raise validation("Year must be 2001 or later.", "Use a complete past calendar year, e.g. 2023.")
    if source == "nasa_power":
        raw, prov = _nasa_power(app, site, year)
    elif source == "pvgis":
        raw, prov = _pvgis(app, site, year)
    else:
        raise validation(f"Unknown resource source '{sanitise(source)}'.", "Use 'nasa_power' or 'pvgis'.")
    df, prep, flags = _prepare_utc_frame(raw, site["timezone"], year, fill_long_gaps)
    if "wind10_m_s" not in df:
        df["wind10_m_s"] = 0.0
    prov.update(prep, year=year)
    doc = _store_dataset(app, site_id, "resource", df, source, False, prov, resource_stats(df), flags, "res")
    return doc


# --------------------------------------------------------------------------- CSV import


def _read_csv(app: Helionyx, csv_text: str | None, file_path: str | None) -> pd.DataFrame:
    if (csv_text is None) == (file_path is None):
        raise validation("Provide exactly one of csv_text or file_path.", "Paste the CSV or give a workspace path.")
    if file_path is not None:
        path = app.settings.resolve_user_path(file_path)
        if not path.exists():
            raise validation("CSV file not found in the workspace.", f"Copy it under {app.settings.workspace}.")
        if path.stat().st_size > MAX_CSV_BYTES:
            raise validation("CSV file exceeds 10 MB.", "Resample to hourly or trim the file.")
        data = path.read_bytes()
    else:
        assert csv_text is not None
        data = csv_text.encode("utf-8")
        if len(data) > MAX_CSV_BYTES:
            raise validation("CSV text exceeds 10 MB.", "Resample to hourly or trim the data.")
    try:
        return pd.read_csv(io.BytesIO(data))
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
        raise validation("CSV could not be parsed.", "Use UTF-8, comma-delimited, with a header row.") from exc


def _hourly_values(df: pd.DataFrame, col: str, resolution_min: int, is_power: bool) -> np.ndarray:
    if resolution_min not in (15, 30, 60):
        raise validation("resolution_min must be 15, 30 or 60.", "Resample the data to one of those resolutions.")
    values = df[col].to_numpy(dtype=np.float64)
    if "timestamp" in df.columns:
        idx = pd.DatetimeIndex(pd.to_datetime(df["timestamp"]))
        if idx.tz is not None:
            raise validation("Timestamps must be local civil time without a UTC offset.",
                             "Remove the offset; Helionyx assumes local time for CSV input.")
        _, values, _ = drop_leap_day(idx, values)
    per_hour = 60 // resolution_min
    expected = HOURS * per_hour
    if len(values) != expected:
        raise validation(f"Expected {expected} rows for one year at {resolution_min}-minute resolution, "
                         f"got {len(values)}.", "Provide exactly one calendar year starting 1 January 00:00.")
    if np.isnan(values).any():
        filled, short, long, unfilled = fill_gaps(values, fill_long_gaps=False, max_short=3 * per_hour)
        if unfilled:
            raise validation("The CSV has gaps longer than 3 hours.", "Fill or remove the gaps before import.")
        values = filled
    return to_hourly_mean(values, resolution_min) if is_power or per_hour > 1 else values


def import_timeseries(app: Helionyx, site_id: str, kind: str, csv_text: str | None = None,
                      file_path: str | None = None, resolution_min: int = 60, units: str | None = None,
                      height_m: float | None = None, base_resource_id: str | None = None) -> dict[str, Any]:
    site = get_site(app, site_id)
    df = _read_csv(app, csv_text, file_path)
    if kind not in KIND_COLUMN:
        raise validation(f"Unknown kind '{sanitise(kind)}'.", "Use one of: load, ghi, temp, wind.")
    col = KIND_COLUMN[kind]
    if col not in df.columns:
        raise validation(f"Column '{col}' is required for kind '{kind}' (the header encodes the unit).",
                         f"Rename the value column to '{col}'.", columns=[sanitise(c, 40) for c in df.columns][:10])
    values = _hourly_values(df, col, resolution_min, is_power=(kind == "load"))
    prov = {"source": "csv_upload", "file": sanitise(file_path) if file_path else "inline", "units": col,
            "resolution_min": resolution_min, "retrieved_at": now_iso(), "license": "user supplied"}
    if kind == "load":
        if (values < 0).any():
            raise validation("Load values must be non-negative.", "Check the sign convention of the meter data.")
        from helionyx.core.load import load_stats
        frame = pd.DataFrame({"load_kw": values})
        return _store_dataset(app, site_id, "load", frame, "csv_upload", False, prov, load_stats(values), [], "load")
    # resource: start from a base resource dataset (if any) and replace one variable
    if base_resource_id:
        base = get_dataset(app, base_resource_id)
        if base["kind"] != "resource":
            raise validation("base_resource_id must refer to a resource dataset.", "Use an ID from fetch_resource.")
        frame = dataset_frame(app, base_resource_id).copy()
        prov["base_resource_id"] = base_resource_id
    else:
        frame = pd.DataFrame({"ghi_w_m2": np.zeros(HOURS), "temp_c": np.full(HOURS, 25.0),
                              "wind10_m_s": np.zeros(HOURS)})
        prov["defaults_for_missing_columns"] = "ghi 0 W/m², temp 25 °C, wind 0 m/s"
    if kind == "ghi":
        frame["ghi_w_m2"] = np.clip(values, 0, None)
    elif kind == "temp":
        frame["temp_c"] = values
    else:
        h = height_m or 10.0
        prov["anemometer_height_m"] = h
        if abs(h - 50) < abs(h - 10):
            frame["wind50_m_s"] = values
        else:
            frame["wind10_m_s"] = values
    _ = site
    return _store_dataset(app, site_id, "resource", frame, "csv_upload", False, prov, resource_stats(frame), [],
                          "res")
