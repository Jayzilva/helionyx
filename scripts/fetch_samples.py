"""Maintainer script: refresh the bundled NASA POWER sample sites in packs/lk/samples.

Usage: python scripts/fetch_samples.py
Data: NASA POWER hourly point API (RE community), UTC time standard. NASA POWER
data are freely available; cite "NASA Langley Research Center POWER Project".
"""

from __future__ import annotations

import gzip
from pathlib import Path

import httpx
import pandas as pd
import yaml

SITES = {
    "negombo": {"name": "Negombo (coastal, Western Province)", "lat": 7.2083, "lon": 79.8358},
    "delft_island": {"name": "Delft Island (Northern Province)", "lat": 9.5167, "lon": 79.6833},
    "hatton": {"name": "Hatton (hill country, Central Province)", "lat": 6.8916, "lon": 80.5955},
}
YEAR = 2023
PARAMS = "ALLSKY_SFC_SW_DWN,T2M,WS10M,WS50M"
URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
OUT = Path(__file__).resolve().parent.parent / "src" / "helionyx" / "packs" / "lk" / "samples"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for key, site in SITES.items():
        params = {"parameters": PARAMS, "community": "RE", "longitude": site["lon"], "latitude": site["lat"],
                  "start": f"{YEAR}0101", "end": f"{YEAR}1231", "format": "JSON", "time-standard": "UTC"}
        body = httpx.get(URL, params=params, timeout=120).json()
        p = body["properties"]["parameter"]
        keys = sorted(p["T2M"].keys())
        df = pd.DataFrame({
            "timestamp_utc": [f"{k[:4]}-{k[4:6]}-{k[6:8]}T{k[8:10]}:00Z" for k in keys],
            "ghi_w_m2": [p["ALLSKY_SFC_SW_DWN"][k] for k in keys],
            "temp_c": [p["T2M"][k] for k in keys],
            "wind10_m_s": [p["WS10M"][k] for k in keys],
            "wind50_m_s": [p["WS50M"][k] for k in keys],
        })
        fname = f"nasa_power_{key}_{YEAR}.csv.gz"
        with gzip.open(OUT / fname, "wb", compresslevel=9) as fh:
            fh.write(df.to_csv(index=False).encode())
        manifest[key] = {
            "file": fname, "name": site["name"], "latitude": site["lat"], "longitude": site["lon"],
            "elevation_m": float(body["geometry"]["coordinates"][2]), "year": YEAR, "time_standard": "UTC",
            "source": "NASA POWER hourly API, RE community (ALLSKY_SFC_SW_DWN, T2M, WS10M, WS50M)",
            "url": URL, "license": "NASA POWER data: no restrictions; cite NASA Langley Research Center POWER Project",
            "retrieved_at": pd.Timestamp.now("UTC").strftime("%Y-%m-%d"),
        }
        print(key, len(df), "rows")
    (OUT / "samples.yaml").write_text(yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
