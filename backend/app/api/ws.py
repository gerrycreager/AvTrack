from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from geoalchemy2.shape import to_shape
from sqlalchemy import select

from app.db import SessionLocal
from app.ingestion.ws_manager import manager
from app.models import Aircraft, Position

router = APIRouter()

# Real bug found live 2026-09-13: a client that (re)connects -- e.g. any page
# refresh -- only ever receives broadcasts of *new* position updates, never the
# positions already known for aircraft that were tracked before it connected. With a
# multi-minute full poll cycle (see REQUIREMENTS.md 3.5), that meant a refresh could
# leave the "Aircraft Aloft" panel showing nothing for nearly the whole cycle time,
# purely due to unlucky timing, even though the backend was working correctly the
# whole time. SNAPSHOT_MAX_AGE bounds how stale a "last known position" is still
# worth backfilling -- reuses the same reasoning as MAX_POSITION_AGE in
# app/adsb/flightaware.py (don't resurrect a genuinely old, no-longer-current
# position just because it's the most recent row on file for that tail).
SNAPSHOT_MAX_AGE = timedelta(minutes=15)


async def _latest_known_positions() -> list[dict]:
    async with SessionLocal() as session:
        # Latest position per active aircraft, in one query (standard Postgres
        # "latest row per group" pattern via DISTINCT ON) rather than one query per
        # tail.
        result = await session.execute(
            select(Position, Aircraft.tail_number, Aircraft.callsign)
            .join(Aircraft, Aircraft.id == Position.aircraft_id)
            .where(Aircraft.active.is_(True))
            .where(Position.ts_utc > datetime.now(timezone.utc) - SNAPSHOT_MAX_AGE)
            .distinct(Position.aircraft_id)
            .order_by(Position.aircraft_id, Position.ts_utc.desc())
        )
        messages = []
        for position, tail_number, callsign in result.all():
            point = to_shape(position.location)
            messages.append(
                {
                    "type": "position",
                    "tail_number": tail_number,
                    "callsign": callsign,
                    "ts_utc": position.ts_utc.isoformat(),
                    "latitude": point.y,
                    "longitude": point.x,
                    "altitude_ft": position.altitude_ft,
                    "ground_speed_kt": position.ground_speed_kt,
                    "heading_deg": position.heading_deg,
                }
            )
        return messages


@router.websocket("/ws/positions")
async def positions_ws(websocket: WebSocket) -> None:
    """Broadcasts every position update to every connected client for now (Phase 1).
    Per-client filtering to a tracked list / operations-area geofence (REQUIREMENTS.md
    "typical client wants <10 aircraft, up to 50+ for big events") happens client-side
    for the scaffold; move it server-side (or onto the Phase 2 broker's topics) once
    that matters for bandwidth at scale.
    """
    await manager.connect(websocket)
    for msg in await _latest_known_positions():
        await websocket.send_json(msg)
    try:
        while True:
            # No inbound messages expected yet; just keep the connection open.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
