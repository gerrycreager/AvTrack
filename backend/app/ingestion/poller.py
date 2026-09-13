import asyncio
import logging
from datetime import datetime, timedelta

from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import select

from app.adsb.base import ADSBProvider
from app.config import settings
from app.db import SessionLocal
from app.ingestion.ws_manager import manager
from app.models import Aircraft, Position, PositionSource

logger = logging.getLogger(__name__)

# Climb/descent trend, REQUIREMENTS.md 3.1 -- deliberately computed from consecutive
# real position deltas here, not read from a provider-native field. adsb.lol does
# expose one (baro_rate) but FlightAware's track response and SWIM/FIXM don't, and
# per this project's established "don't trust a single provider field" principle
# (REQUIREMENTS.md 3.3), deriving it ourselves from altitude-over-time works
# uniformly across every provider instead of being inconsistently available.
VERTICAL_TREND_THRESHOLD_FPM = 150.0  # below this, call it "level" rather than noise
PREVIOUS_POSITION_MAX_AGE = timedelta(minutes=10)  # ignore a stale prior point


async def _tracked_tail_numbers() -> list[str]:
    async with SessionLocal() as session:
        result = await session.execute(select(Aircraft.tail_number).where(Aircraft.active.is_(True)))
        return [row[0] for row in result.all()]


async def _latest_positions_by_aircraft_id(session, aircraft_ids: list) -> dict:
    if not aircraft_ids:
        return {}
    result = await session.execute(
        select(Position)
        .where(Position.aircraft_id.in_(aircraft_ids))
        .distinct(Position.aircraft_id)
        .order_by(Position.aircraft_id, Position.ts_utc.desc())
    )
    return {p.aircraft_id: p for p in result.scalars()}


def _vertical_trend(prev: Position | None, new_altitude_ft: float | None, new_ts_utc: datetime) -> str | None:
    if prev is None or prev.altitude_ft is None or new_altitude_ft is None:
        return None
    elapsed = (new_ts_utc - prev.ts_utc).total_seconds()
    if elapsed <= 0 or (new_ts_utc - prev.ts_utc) > PREVIOUS_POSITION_MAX_AGE:
        return None
    rate_fpm = (new_altitude_ft - prev.altitude_ft) / (elapsed / 60.0)
    if rate_fpm > VERTICAL_TREND_THRESHOLD_FPM:
        return "climbing"
    if rate_fpm < -VERTICAL_TREND_THRESHOLD_FPM:
        return "descending"
    return "level"


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
        # Fetched before this cycle's inserts, so it's genuinely the *previous*
        # position, not the one being written in this same loop.
        prev_by_aircraft_id = await _latest_positions_by_aircraft_id(
            session, [a.id for a in aircraft_by_tail.values()]
        )
        for pos in positions:
            aircraft = aircraft_by_tail.get(pos.tail_number)
            if aircraft is None:
                continue
            vertical_trend = _vertical_trend(prev_by_aircraft_id.get(aircraft.id), pos.altitude_ft, pos.ts_utc)
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
                    "vertical_trend": vertical_trend,
                }
            )
            # TODO: feed recent_positions into app.ingestion.heuristics.detect_events
            # once that's designed, and persist any resulting TrackingEvent rows.
        await session.commit()


async def run_poll_loop(provider: ADSBProvider, interval_seconds: int | None = None) -> None:
    interval = interval_seconds if interval_seconds is not None else settings.adsb_poll_interval_seconds
    while True:
        try:
            await poll_once(provider)
        except Exception:
            logger.exception("ADS-B poll cycle failed (%s)", provider.name)
        await asyncio.sleep(interval)
