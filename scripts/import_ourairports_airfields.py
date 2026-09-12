"""Stopgap airfield import from OurAirports' public dataset (REQUIREMENTS.md open
question 2 -- decided long-term source is FAA NASR, but that script is unvalidated
against a real extract and NASR is a bigger download/prep task; this gets basic
ICAO/FAA-id zoom-to and airport markers working today).

No runway/surface data is loaded here (OurAirports splits that into a separate
runways.csv this script doesn't touch) -- `hard_surface_available` and
`longest_runway_ft` stay null until the NASR import (or a runways.csv pass) fills
them in. That's fine for zoom-to and map markers; it matters once the touch-and-go
heuristic (REQUIREMENTS.md open question 3) needs the CAPR 70-1 >=3000ft hard-surface
pre-filter.

Usage:
    python scripts/import_ourairports_airfields.py
"""

import asyncio
import csv
import io
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from geoalchemy2.shape import from_shape  # noqa: E402
from shapely.geometry import Point  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Airfield  # noqa: E402

AIRPORTS_CSV_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
RELEVANT_TYPES = {"small_airport", "medium_airport", "large_airport", "heliport", "seaplane_base"}
# CAP operates CONUS + AK + HI + PR (REQUIREMENTS.md mission context). OurAirports
# tags Puerto Rico under iso_country "PR", not "US".
RELEVANT_COUNTRIES = {"US", "PR"}


async def import_airports() -> None:
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        resp = await http_client.get(AIRPORTS_CSV_URL)
        resp.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(resp.text)))

    async with SessionLocal() as session:
        existing = {a.faa_id: a for a in (await session.execute(select(Airfield))).scalars() if a.faa_id}
        existing_by_icao = {a.icao_id: a for a in existing.values() if a.icao_id}

        count = 0
        for row in rows:
            if row.get("type") not in RELEVANT_TYPES:
                continue
            if row.get("iso_country") not in RELEVANT_COUNTRIES:
                continue

            icao_id = (row.get("icao_code") or row.get("gps_code") or "").strip().upper() or None
            faa_id = (row.get("local_code") or "").strip().upper() or None
            if not icao_id and not faa_id:
                continue  # nothing to key on / look up by

            airfield = (existing.get(faa_id) if faa_id else None) or (
                existing_by_icao.get(icao_id) if icao_id else None
            )
            if airfield is None:
                airfield = Airfield()
                session.add(airfield)

            airfield.icao_id = icao_id
            airfield.faa_id = faa_id
            airfield.name = row.get("name", "").strip()
            airfield.location = from_shape(
                Point(float(row["longitude_deg"]), float(row["latitude_deg"])), srid=4326
            )
            airfield.elevation_ft = float(row["elevation_ft"]) if row.get("elevation_ft") else None
            count += 1

        await session.commit()

    print(f"Imported/updated {count} airfields (US + PR) from OurAirports.")


if __name__ == "__main__":
    asyncio.run(import_airports())
