import asyncio
import logging

from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import select

from app.adsb.base import ADSBProvider
from app.config import settings
from app.db import SessionLocal
from app.ingestion.ws_manager import manager
from app.models import Aircraft, Position, PositionSource

logger = logging.getLogger(__name__)


async def _tracked_tail_numbers() -> list[str]:
    async with SessionLocal() as session:
        result = await session.execute(select(Aircraft.tail_number).where(Aircraft.active.is_(True)))
        return [row[0] for row in result.all()]


async def poll_once(provider: ADSBProvider) -> None:
    tails = await _tracked_tail_numbers()
    if not tails:
        return
    positions = await provider.get_positions(tails)

    async with SessionLocal() as session:
        aircraft_by_tail = {
            a.tail_number: a
            for a in (await session.execute(select(Aircraft).where(Aircraft.tail_number.in_(tails)))).scalars()
        }
        for pos in positions:
            aircraft = aircraft_by_tail.get(pos.tail_number)
            if aircraft is None:
                continue
            row = Position(
                aircraft_id=aircraft.id,
                ts_utc=pos.ts_utc,
                location=from_shape(Point(pos.longitude, pos.latitude), srid=4326),
                altitude_ft=pos.altitude_ft,
                ground_speed_kt=pos.ground_speed_kt,
                heading_deg=pos.heading_deg,
                source=PositionSource(provider.name) if provider.name in PositionSource._value2member_map_ else PositionSource.other,
                raw=pos.raw,
            )
            session.add(row)
            await manager.broadcast_position(
                {
                    "type": "position",
                    "tail_number": pos.tail_number,
                    "callsign": pos.callsign,
                    "ts_utc": pos.ts_utc.isoformat(),
                    "latitude": pos.latitude,
                    "longitude": pos.longitude,
                    "altitude_ft": pos.altitude_ft,
                    "ground_speed_kt": pos.ground_speed_kt,
                    "heading_deg": pos.heading_deg,
                }
            )
            # TODO: feed recent_positions into app.ingestion.heuristics.detect_events
            # once that's designed, and persist any resulting TrackingEvent rows.
        await session.commit()


async def run_poll_loop(provider: ADSBProvider) -> None:
    while True:
        try:
            await poll_once(provider)
        except Exception:
            logger.exception("ADS-B poll cycle failed")
        await asyncio.sleep(settings.adsb_poll_interval_seconds)
