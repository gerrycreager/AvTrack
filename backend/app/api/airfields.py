"""Airfield markers/labels for the map (REQUIREMENTS.md 3.2).

Bbox-filtered. Tiering originally matched CAP WxCOP's airport_tiers_endpoint.py
convention (military / paved>=8000ft / paved>=5000ft / paved>=2500ft), but Gerry
simplified this 2026-09-13 to just two tiers for now ("we can adjust later"):
military (always), or runway >=5000ft regardless of surface type. Revisit if/when a
finer breakdown is wanted again -- the original 4-tier logic is in git history.
"""

from fastapi import APIRouter, Depends, Query
from geoalchemy2.functions import ST_MakeEnvelope
from geoalchemy2.shape import to_shape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Airfield
from app.schemas import AirfieldOut

router = APIRouter(prefix="/api/airfields", tags=["airfields"])


def compute_tier(is_military: bool, hard_surface: bool | None, longest_runway_ft: float | None) -> int | None:
    if is_military:
        return 1
    if longest_runway_ft and longest_runway_ft >= 5000:
        return 2
    return None


@router.get("", response_model=list[AirfieldOut])
async def list_airfields(
    bounds: str | None = Query(None, description="west,south,east,north (WGS84) -- omit for no spatial filter"),
    max_tier: int | None = Query(
        None, description="Only return airfields at or above this tier (1=military .. 4=GA); omit for all, including untiered"
    ),
    session: AsyncSession = Depends(get_session),
):
    query = select(Airfield)
    if bounds:
        try:
            west, south, east, north = (float(x) for x in bounds.split(","))
        except ValueError:
            west = south = east = north = None
        if west is not None:
            query = query.where(Airfield.location.intersects(ST_MakeEnvelope(west, south, east, north, 4326)))

    result = await session.execute(query)
    airfields = result.scalars().all()

    out = []
    for a in airfields:
        tier = compute_tier(a.is_military, a.hard_surface_available, a.longest_runway_ft)
        if max_tier is not None and (tier is None or tier > max_tier):
            continue
        point = to_shape(a.location)
        out.append(
            AirfieldOut(
                id=a.id,
                icao_id=a.icao_id,
                faa_id=a.faa_id,
                name=a.name,
                latitude=point.y,
                longitude=point.x,
                elevation_ft=a.elevation_ft,
                longest_runway_ft=a.longest_runway_ft,
                hard_surface_available=a.hard_surface_available,
                is_military=a.is_military,
                tier=tier,
            )
        )
    return out
