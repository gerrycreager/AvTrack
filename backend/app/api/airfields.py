"""Airfield markers/labels for the map (REQUIREMENTS.md 3.2).

Bbox-filtered. Tiering originally matched CAP WxCOP's airport_tiers_endpoint.py
convention, then got simplified to 2 tiers 2026-09-13 ("we can adjust later"), then
restored to a finer breakdown 2026-09-14 per Gerry: "all the airport icons are
noisy... let's use the same logic found on r815 EWMC to display a graduated number
of airfields based on zoom." Read EWMC's actual logic directly
(enhanced_weather_map_complete.html on r815): a 5-tier scheme (military / major-hub
/ regional>=7000ft / local 5000-6999ft / small 2500-4999ft) each gated by a
`TIER_MIN_ZOOM` threshold (`{1:0, 2:3, 3:3, 4:7, 5:12}` there), applied to BOTH icon
and label visibility, not just labels.

Two real differences from EWMC, both deliberate:
- EWMC's tier 2 ("major hub") is a curated list of ~25 named major airports plus a
  "has_reporting" (METAR-station) flag AvTrack doesn't have -- collapsed here into a
  single "paved >= 8000ft" tier computed the same way as the others, since AvTrack
  has no equivalent curated/reporting data source.
- EWMC's underlying query requires `has_paved_runway AND longest_runway_ft >= 2500`
  for an airfield to appear AT ALL, at any zoom -- unpaved/short strips are
  permanently invisible there. Gerry explicitly did NOT want that for AvTrack
  ("keep all visible eventually... matters for CAP ops at small/unpaved fields") --
  so there's a 5th tier here for everything else (unpaved, <2500ft, or missing
  runway data) that EWMC simply excludes, gated by an even deeper zoom instead of
  being hidden outright.
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


def compute_tier(is_military: bool, hard_surface: bool | None, longest_runway_ft: float | None) -> int:
    """Every airfield gets a tier now (never None) -- see module docstring for why
    tier 5 exists instead of excluding these outright the way EWMC does."""
    if is_military:
        return 1
    if hard_surface and longest_runway_ft and longest_runway_ft >= 8000:
        return 2
    if hard_surface and longest_runway_ft and longest_runway_ft >= 5000:
        return 3
    if hard_surface and longest_runway_ft and longest_runway_ft >= 2500:
        return 4
    return 5


@router.get("", response_model=list[AirfieldOut])
async def list_airfields(
    bounds: str | None = Query(None, description="west,south,east,north (WGS84) -- omit for no spatial filter"),
    max_tier: int | None = Query(
        None,
        description="Only return airfields at or above this tier "
        "(1=military, 2=paved>=8000ft, 3=paved>=5000ft, 4=paved>=2500ft, 5=everything else); omit for all",
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
        if max_tier is not None and tier > max_tier:
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
