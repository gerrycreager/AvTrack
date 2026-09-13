"""Zoom-to-area lookup (REQUIREMENTS.md 3.2): ICAO airport id, lat/lon, or
MGRS/USNG coordinate string, all resolved to a lat/lon the frontend can fly the map
to.

USNG and MGRS share the same grid (both NAD83/WGS84-based); the `mgrs` library's
parser is used for both. This has not been checked against edge cases (100km-square
letter ambiguity near datum/zone boundaries) -- fine for a Phase 1 "zoom to roughly
here" use case, worth revisiting if precision claims are ever needed.

Precision-box corners (added 2026-09-13, per Gerry): a truncated-precision MGRS
string (fewer than 5 digits per axis) isn't really "a point" -- per the MGRS spec, it
resolves to the SW corner of a box whose size is determined by how many digits were
given (0 digits/axis = 100km, 1 = 10km, 2 = 1km, 3 = 100m, 4 = 10m, 5 = 1m).
Confirmed live 2026-09-13: entering just a 100km square (`16SEJ`, 0 digits/axis) for
a point known to be well inside that square landed 56.9 nm from the expected
location -- correct per the SW-corner convention, but surprising without seeing the
box. This endpoint now also returns the box's four corners (computed via the
library's UTM-level MGRSToUTM/UTMToMGRS, not string-padding tricks, so each corner
is a real, independently-converted lat/lon rather than an assumed rectangle --
UTM grid squares aren't perfectly rectangular once reprojected to lat/lon, so the
four corners can differ very slightly from a naive bounding box) when the precision
is coarser than ~10m, letting the frontend draw the actual area of uncertainty
instead of a single (possibly far-off-target) marker.
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends
from geoalchemy2.shape import to_shape
import re
import mgrs

from app.db import get_session
from app.models import Airfield

router = APIRouter(prefix="/api/locate", tags=["locate"])
_mgrs = mgrs.MGRS()

# zone + band + 100km square + an even number of digits (split evenly between
# easting and northing).
_MGRS_PATTERN = re.compile(r"^(\d{1,2}[A-Za-z][A-Za-z]{2})(\d*)$")


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
    cleaned = grid.replace(" ", "").upper()
    try:
        lat, lon = _mgrs.toLatLon(cleaned)
    except Exception as exc:
        raise HTTPException(400, f"Could not parse MGRS/USNG grid '{grid}': {exc}") from exc

    bounds = _precision_box(cleaned)
    if bounds is None:
        return {"latitude": lat, "longitude": lon}

    # Center of the box, not the SW corner -- more useful to fly the map to when the
    # input is coarse (a corner can leave the actual area of interest half off-screen).
    center_lat = sum(c[0] for c in bounds) / 4
    center_lon = sum(c[1] for c in bounds) / 4
    return {"latitude": center_lat, "longitude": center_lon, "bounds": bounds}


def _precision_box(cleaned_mgrs: str) -> list[list[float]] | None:
    """Four corners [[lat,lon], SW, SE, NE, NW] of the area a truncated-precision
    MGRS string actually represents, or None if the string is already at (or near)
    1m precision, where a box wouldn't be visually meaningful. Each corner is
    independently converted (via UTM, not a naive bounding box) -- see module
    docstring for why that matters."""
    match = _MGRS_PATTERN.match(cleaned_mgrs)
    if not match:
        return None
    digits = match.group(2)
    if len(digits) % 2 != 0:
        return None  # malformed (odd digit count) -- toLatLon() will have already raised on this
    digits_per_axis = len(digits) // 2
    if digits_per_axis >= 5:
        return None  # already 1m precision -- no meaningful box to draw
    box_size_m = 10 ** (5 - digits_per_axis)

    zone, hemisphere, easting, northing = _mgrs.MGRSToUTM(cleaned_mgrs)
    corners_utm = [
        (easting, northing),  # SW
        (easting + box_size_m, northing),  # SE
        (easting + box_size_m, northing + box_size_m),  # NE
        (easting, northing + box_size_m),  # NW
    ]
    return [list(_mgrs.toLatLon(_mgrs.UTMToMGRS(zone, hemisphere, e, n, 5))) for e, n in corners_utm]
