"""Ingest endpoint for the r815 FDPS relay (deploy/fdps_relay.py, REQUIREMENTS.md 3.5).

The relay does a cheap text pre-filter and forwards raw FIXM/NAS XML here; this is
where the real parsing and Aircraft matching happens, deliberately kept on cosp1
rather than on r815 (keeps r815's piece genuinely lightweight, and keeps all the
actual business logic in one place with the rest of the backend).

Matches the same "don't trust provider flight-boundary/status fields" principle as
app/adsb/flightaware.py: this endpoint only ever writes what FDPS reports as a
position, with no attempt to infer landed/airborne status from FDPS's own flight
status fields (`fdpsFlightStatus` etc.) -- same reasoning as the two prior incidents
documented in REQUIREMENTS.md 3.3.
"""

import logging

from fastapi import APIRouter, Depends, Request
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adsb.fixm import parse_fixm_positions
from app.db import get_session
from app.ingestion.ws_manager import manager
from app.models import Aircraft, AircraftCallsign, Position, PositionSource

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/swim", tags=["swim"])


@router.post("/ingest")
async def ingest(request: Request, session: AsyncSession = Depends(get_session)):
    """Body is raw FIXM/NAS XML (one MessageCollection), Content-Type: application/xml.
    Not authenticated -- relies on this endpoint only being reachable from the LAN
    (192.168.0.57 isn't internet-exposed; only the r815 proxy is, and it doesn't
    proxy this path). Revisit if that stops being true."""
    body = (await request.body()).decode("utf-8", errors="replace")
    try:
        positions = parse_fixm_positions(body)
    except Exception:
        logger.exception("Failed to parse FIXM message from SWIM relay")
        return {"accepted": 0, "matched": 0}

    if not positions:
        return {"accepted": 0, "matched": 0}

    callsigns = {p.callsign for p in positions}
    result = await session.execute(
        select(Aircraft)
        .outerjoin(AircraftCallsign, AircraftCallsign.aircraft_id == Aircraft.id)
        .where((Aircraft.callsign.in_(callsigns)) | (AircraftCallsign.callsign.in_(callsigns)))
        .distinct()
    )
    aircraft_by_callsign: dict[str, Aircraft] = {}
    for aircraft in result.scalars().all():
        if aircraft.callsign in callsigns:
            aircraft_by_callsign[aircraft.callsign] = aircraft
        for cs in aircraft.callsigns:
            if cs.callsign in callsigns:
                aircraft_by_callsign[cs.callsign] = aircraft

    matched = 0
    for pos in positions:
        aircraft = aircraft_by_callsign.get(pos.callsign)
        if aircraft is None:
            # The relay's "CAP<digits>" pre-filter is a cheap heuristic, not an exact
            # match against our known fleet -- e.g. a non-CAP operator whose callsign
            # happens to contain "CAP" as a substring. Silently drop unmatched ones
            # rather than treating this as an error.
            continue
        matched += 1
        row = Position(
            aircraft_id=aircraft.id,
            ts_utc=pos.ts_utc,
            location=from_shape(Point(pos.longitude, pos.latitude), srid=4326),
            altitude_ft=pos.altitude_ft,
            ground_speed_kt=pos.ground_speed_kt,
            heading_deg=pos.heading_deg,
            source=PositionSource.swim,
            raw=None,  # raw FIXM stored nowhere yet -- add if debugging needs it later
        )
        session.add(row)
        await manager.broadcast_position(
            {
                "type": "position",
                "tail_number": aircraft.tail_number,
                "callsign": pos.callsign,
                "ts_utc": pos.ts_utc.isoformat(),
                "latitude": pos.latitude,
                "longitude": pos.longitude,
                "altitude_ft": pos.altitude_ft,
                "ground_speed_kt": pos.ground_speed_kt,
                "heading_deg": pos.heading_deg,
            }
        )

    await session.commit()
    return {"accepted": len(positions), "matched": matched}
