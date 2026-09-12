"""Zoom-to-area lookup (REQUIREMENTS.md 3.2): ICAO airport id, lat/lon, or
MGRS/USNG coordinate string, all resolved to a lat/lon the frontend can fly the map
to.

USNG and MGRS share the same grid (both NAD83/WGS84-based); the `mgrs` library's
parser is used for both. This has not been checked against edge cases (100km-square
letter ambiguity near datum/zone boundaries) -- fine for a Phase 1 "zoom to roughly
here" use case, worth revisiting if precision claims are ever needed.
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends
from geoalchemy2.shape import to_shape
import mgrs

from app.db import get_session
from app.models import Airfield

router = APIRouter(prefix="/api/locate", tags=["locate"])
_mgrs = mgrs.MGRS()


@router.get("/icao/{icao_id}")
async def locate_icao(icao_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Airfield).where(Airfield.icao_id == icao_id.upper())
    )
    airfield = result.scalar_one_or_none()
    if airfield is None:
        raise HTTPException(404, f"No airfield with ICAO id {icao_id.upper()}")
    point = to_shape(airfield.location)
    return {"latitude": point.y, "longitude": point.x, "name": airfield.name}


@router.get("/latlon")
async def locate_latlon(lat: float, lon: float):
    return {"latitude": lat, "longitude": lon}


@router.get("/mgrs/{grid}")
async def locate_mgrs(grid: str):
    """Also accepts USNG strings -- see module docstring."""
    try:
        lat, lon = _mgrs.toLatLon(grid.replace(" ", "").upper())
    except Exception as exc:
        raise HTTPException(400, f"Could not parse MGRS/USNG grid '{grid}': {exc}") from exc
    return {"latitude": lat, "longitude": lon}
