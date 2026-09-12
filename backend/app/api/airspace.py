"""Special Use Airspace (Prohibited/Restricted/MOA/Warning/Alert) for the map
(REQUIREMENTS.md 3.2). Data comes from scripts/import_sua_airspace.py, which pulls
the same public FAA AIS feed CAP WxCOP already uses -- see that script's docstring.

Bbox-filtered, same convention as app/api/airfields.py.
"""

from fastapi import APIRouter, Depends, Query
from geoalchemy2.functions import ST_MakeEnvelope
from geoalchemy2.shape import to_shape
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
    features = []
    for area in result.scalars():
        geom = to_shape(area.geometry)
        features.append(
            {
                "type": "Feature",
                "geometry": geom.__geo_interface__,
                "properties": {
                    "id": str(area.id),
                    "name": area.name,
                    "type_code": area.type_code,
                    "type_name": SUA_TYPE_NAMES.get(area.type_code, area.type_code),
                    "city": area.city,
                    "state": area.state,
                    "lower_altitude": area.lower_altitude,
                    "upper_altitude": area.upper_altitude,
                    "times_of_use": area.times_of_use,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
