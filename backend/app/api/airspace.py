"""Special Use Airspace (Prohibited/Restricted/MOA/Warning/Alert) for the map
(REQUIREMENTS.md 3.2). Data comes from scripts/import_sua_airspace.py, which pulls
the same public FAA AIS feed CAP WxCOP already uses -- see that script's docstring.

Bbox-filtered, same convention as app/api/airfields.py.
"""

import asyncio
import json

import shapely.wkb
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from geoalchemy2.functions import ST_MakeEnvelope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import SpecialUseAirspace

router = APIRouter(prefix="/api/airspace", tags=["airspace"])

# Matches the type/color convention already used by CAP WxCOP's EWMC map
# (enhanced_weather_map_complete.html on r815) -- kept consistent so the same visual
# language carries over for pilots/staff already familiar with it.
SUA_TYPE_NAMES = {
    "P": "Prohibited",
    "R": "Restricted",
    "MOA": "Military Operations Area",
    "W": "Warning Area",
    "A": "Alert Area",
    "D": "Danger Area",
}


@router.get("/sua")
async def list_sua(
    request: Request,
    west: float = Query(...),
    south: float = Query(...),
    east: float = Query(...),
    north: float = Query(...),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(SpecialUseAirspace).where(
            SpecialUseAirspace.geometry.intersects(ST_MakeEnvelope(west, south, east, north, 4326))
        )
    )
    areas = result.scalars().all()
    # Same fix as app/api/airfields.py (found live 2026-11-02, see its comment for
    # the full story): this loop has no `await` in it, so it ran inline on
    # FastAPI's single event loop -- SUA's geometries are often large polygons
    # (this took up to ~13s at a wide bbox), stalling the ADS-B poll loops and
    # every other concurrent request for the same duration. A plain
    # asyncio.to_thread() was tried first and confirmed live NOT sufficient (the
    # GIL still let it stall other requests); a real OS process
    # (app.state.cpu_pool, see app/main.py) has no GIL to contend with. No
    # tier/filter concept exists for SUA (unlike airfields) -- every intersecting
    # area is safety-relevant regardless of zoom, so the fix here is only to stop
    # it blocking the event loop, not to filter rows.
    #
    # Only plain, picklable data crosses the process boundary -- area.geometry's
    # raw WKB bytes (`.data`), not the geoalchemy2 WKBElement itself; re-parsed
    # with shapely directly in the worker (same approach as airfields.py,
    # confirmed to produce identical geometry to geoalchemy2's to_shape()).
    rows = [
        (
            area.geometry.data,
            area.id,
            area.name,
            area.type_code,
            area.city,
            area.state,
            area.lower_altitude,
            area.upper_altitude,
            area.times_of_use,
        )
        for area in areas
    ]
    loop = asyncio.get_running_loop()
    body = await loop.run_in_executor(request.app.state.cpu_pool, _build_sua_features, rows)
    # Raw Response with pre-dumped JSON, not a dict -- same reasoning as
    # airfields.py: FastAPI's own json.dumps() of a ~20MB nested structure was
    # itself still stalling the event loop for 2+ seconds even after the
    # geometry-parsing work moved to a worker process. Doing json.dumps() in the
    # worker too and shipping back a string fixes that.
    return Response(content=body, media_type="application/json")


def _build_sua_features(rows: list[tuple]) -> str:
    """Runs in a worker process (see list_sua above) -- must not touch anything
    outside its own arguments (no DB session, no app state). Returns an
    already-dumped JSON string, not a Python object -- see the caller's comment."""
    features = []
    for wkb_data, id_, name, type_code, city, state, lower_altitude, upper_altitude, times_of_use in rows:
        geom = shapely.wkb.loads(bytes(wkb_data))
        features.append(
            {
                "type": "Feature",
                "geometry": geom.__geo_interface__,
                "properties": {
                    "id": str(id_),
                    "name": name,
                    "type_code": type_code,
                    "type_name": SUA_TYPE_NAMES.get(type_code, type_code),
                    "city": city,
                    "state": state,
                    "lower_altitude": lower_altitude,
                    "upper_altitude": upper_altitude,
                    "times_of_use": times_of_use,
                },
            }
        )
    return json.dumps({"type": "FeatureCollection", "features": features})
