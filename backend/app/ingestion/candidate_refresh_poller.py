"""Fast refresh for already-discovered CAP/PARD candidates (REQUIREMENTS.md 3.1,
added 2026-10-15). Keeps a known candidate's position/altitude/speed/heading near-
real-time via the same bulk /v2/reg/ lookup the registered-aircraft poller already
uses, instead of leaving it to the slow 22-point CONUS sweep's ~33min lap -- see
app.integrations.adsb_discovery.refresh_known_candidates's docstring for the full
rationale (found live via Gerry: CAP161 updated on ADS-B Exchange every <60sec while
AvTrack lagged by up to a full sweep lap, even though adsb.lol itself already had the
same fresh data).

Same run_poll_loop-style shape as app/ingestion/poller.py and
app/ingestion/ao_discovery_poller.py. Runs in-process (app/main.py's on_startup),
not a separate systemd unit, since it reads discovered_callsigns directly.
"""

import asyncio
import logging

from app.config import settings
from app.integrations.adsb_discovery import refresh_known_candidates

logger = logging.getLogger(__name__)


async def run_candidate_refresh_loop(interval_seconds: int | None = None) -> None:
    interval = interval_seconds if interval_seconds is not None else settings.candidate_refresh_poll_interval_seconds
    while True:
        try:
            updated = await refresh_known_candidates()
            if updated:
                logger.info("Candidate refresh: %d known candidate(s) re-sighted", updated)
        except Exception:
            logger.exception("Candidate refresh poll cycle failed")
        await asyncio.sleep(interval)
