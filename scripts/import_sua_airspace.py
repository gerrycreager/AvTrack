"""Import Special Use Airspace (Prohibited/Restricted/MOA/Warning/Alert) from the
public FAA AIS hub.arcgis.com GeoJSON feed (REQUIREMENTS.md 3.2).

This is the exact same public, unauthenticated source CAP WxCOP already downloads
weekly for its own map (`/var/www/cap_winds_app/scripts/update_airspace_geo.py` on
r815, cron'd Sundays 03:30) -- confirmed 2026-09-12 by reading that script directly.
AvTrack downloads it independently rather than reading WxCOP's database or static
file at runtime, so there's no cross-application dependency: two consumers of the
same upstream FAA data, not one depending on the other.

FAA ADDS item dd0d1b726e504137ab3c41b21835d05b_0, covers CONUS/PR/VI, refreshed by
FAA on each 56-day AIRAC cycle. No API key needed.

Re-running is safe -- upserts keyed on the FAA GLOBAL_ID (source_id).

Usage:
    python scripts/import_sua_airspace.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx  # noqa: E402
from geoalchemy2.shape import from_shape  # noqa: E402
from shapely.geometry import shape  # noqa: E402
from shapely.ops import transform  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import SpecialUseAirspace  # noqa: E402

SUA_URL = (
    "https://hub.arcgis.com/api/v3/datasets/"
    "dd0d1b726e504137ab3c41b21835d05b_0/downloads/data?format=geojson&spatialRefId=4326&where=1=1"
)


async def import_sua() -> None:
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        resp = await client.get(SUA_URL)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        raise ValueError("FAA SUA GeoJSON had 0 features -- likely a bad response, aborting import")

    async with SessionLocal() as session:
        existing = {
            a.source_id: a for a in (await session.execute(select(SpecialUseAirspace))).scalars()
        }

        count = 0
        for feature in features:
            props = feature["properties"]
            source_id = props.get("GLOBAL_ID")
            if not source_id:
                continue

            area = existing.get(source_id) or SpecialUseAirspace(source_id=source_id)
            area.name = props.get("NAME")
            area.type_code = props.get("TYPE_CODE")
            area.city = props.get("CITY")
            area.state = props.get("STATE")
            area.lower_altitude = f"{props.get('LOWER_VAL', '')} {props.get('LOWER_UOM', '')} {props.get('LOWER_CODE', '')}".strip()
            area.upper_altitude = f"{props.get('UPPER_VAL', '')} {props.get('UPPER_UOM', '')} {props.get('UPPER_CODE', '')}".strip()
            area.times_of_use = props.get("TIMESOFUSE")
            # Source geometry includes a spurious Z=0 on every coordinate (seen in the
            # real feed) -- drop it so PostGIS stores true 2D geometry, matching every
            # other table's SRID 4326 2D convention (Airfield.location etc).
            geom = shape(feature["geometry"])
            if geom.has_z:
                geom = transform(lambda x, y, z=None: (x, y), geom)
            area.geometry = from_shape(geom, srid=4326)
            session.add(area)
            count += 1

        await session.commit()

    print(f"Imported/updated {count} special use airspace areas")


if __name__ == "__main__":
    asyncio.run(import_sua())
