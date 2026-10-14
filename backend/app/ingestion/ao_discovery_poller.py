"""Fast, AO-scoped CAP/PARD callsign discovery (REQUIREMENTS.md 3.1, added
2026-10-14). Unlike scripts/discover_cap_callsigns.py's slow 22-point CONUS
sweep, this queries only around the currently-persisted OperationsArea, every
cycle, for near-real-time "unregistered aircraft entered the AO" alerting on
the Ops Board (see app/api/discovered.py). Runs in-process (app/main.py's
on_startup), not a separate systemd unit, since it only makes sense tied to
this app's live AO state -- a standalone script would have no way to know the
AO without duplicating the DB read.

Same run_poll_loop-style shape as app/ingestion/poller.py. The actual
query/regex/upsert logic is shared with the slow CONUS sweep via
app/integrations/adsb_discovery.py.
"""

import asyncio
import logging

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.integrations.adsb_discovery import extract_matches, fetch_point, new_client, upsert_matches
from app.models import OperationsArea

logger = logging.getLogger(__name__)


async def poll_once() -> None:
    async with SessionLocal() as session:
        ao = (await session.execute(select(OperationsArea))).scalars().first()
    if ao is None:
        return  # no AO set yet -- nothing to scope the query to

    async with new_client() as client:
        aircraft_list = await fetch_point(client, ao.center_latitude, ao.center_longitude, ao.radius_nm)
    matches = extract_matches(aircraft_list)
    if matches:
        new_count = await upsert_matches(matches)
        logger.info("AO poll: %d CAP/PARD-shaped callsign(s), %d not already known", len(matches), new_count)


async def run_ao_discovery_loop(interval_seconds: int | None = None) -> None:
    interval = interval_seconds if interval_seconds is not None else settings.ao_discovery_poll_interval_seconds
    while True:
        try:
            await poll_once()
        except Exception:
            logger.exception("AO discovery poll cycle failed")
        await asyncio.sleep(interval)
