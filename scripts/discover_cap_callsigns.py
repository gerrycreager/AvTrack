"""Passive CAP callsign/tail discovery from adsb.lol's live feed (REQUIREMENTS.md
3.1, added 2026-09-15). Per Gerry: "we can also capture CAP callsigns and tail
numbers from the live feed to populate the background list. Won't be exhaustive
but it will get us more tails to work with. Let's start a multi-day capture."

This is a long-running service (systemd unit, deploy/avtrack-cap-discovery.service),
not a one-shot script -- it loops forever, sweeping a fixed grid of CONUS points via
adsb.lol's `/v2/point/{lat}/{lon}/{radius}` (up to 250nm radius; no wildcard/prefix
callsign search exists on this API -- confirmed live 2026-09-14, `/v2/callsign/CAP`
returns zero results, it's exact-match only), one point per cycle, filtering results
client-side for a CAP-shaped `flight` field (`^CAP\\s?\\d+$`, tolerating the same
"CAP 1234" vs "CAP1234" inconsistency seen in real WIMRS exports). Matches upsert
into `discovered_callsigns` -- a candidate list, NOT auto-promoted into `Aircraft`
(see that model's docstring for why).

Pacing: one point query per cycle, comfortably spaced (default 90s) -- adsb.lol's
undocumented rate limit is aggressive (app/adsb/adsb_lol.py found it triggering
after just ~2 rapid calls), so this deliberately trades sweep speed for reliability;
a full 20-point grid pass takes ~30 min, which is more than fast enough across a
multi-day capture window. A 429 (or any request failure) is logged and skipped, not
fatal -- the loop just tries the next point next cycle.

The grid is a coarse, non-exhaustive CONUS coverage (per Gerry: "won't be
exhaustive") biased toward populated/likely-CAP-active regions rather than a
gap-free tiling -- good enough to pick up additional real tails over days of
running, not a claim of complete coverage.

Usage (normally started via systemd, not run directly):
    python scripts/discover_cap_callsigns.py
"""

import asyncio
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Aircraft, DiscoveredCallsign  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("discover_cap_callsigns")

ADSB_LOL_BASE_URL = "https://api.adsb.lol"
CAP_CALLSIGN_RE = re.compile(r"^CAP\s?\d+$", re.IGNORECASE)
POLL_INTERVAL_SECONDS = 90
RADIUS_NM = 250

# Coarse CONUS grid, biased toward populated areas where CAP is more likely to be
# flying -- not a gap-free tiling of the whole country (see module docstring).
GRID_POINTS = [
    (47.6, -122.3),  # Seattle
    (45.5, -122.7),  # Portland
    (37.8, -122.4),  # SF Bay Area
    (34.0, -118.2),  # Los Angeles
    (32.7, -117.2),  # San Diego
    (33.4, -112.1),  # Phoenix
    (39.7, -104.9),  # Denver
    (35.1, -106.6),  # Albuquerque
    (32.8, -96.8),  # Dallas
    (29.8, -95.4),  # Houston
    (39.1, -94.6),  # Kansas City
    (44.9, -93.3),  # Minneapolis
    (41.9, -87.6),  # Chicago
    (36.2, -86.8),  # Nashville
    (33.7, -84.4),  # Atlanta
    (25.8, -80.2),  # Miami
    (28.5, -81.4),  # Orlando
    (35.2, -80.8),  # Charlotte
    (38.9, -77.0),  # DC / Baltimore-Washington
    (40.7, -74.0),  # NYC
    (42.4, -71.1),  # Boston
    (39.3, -85.9),  # Columbus/Indianapolis (KBAK area -- CAP's own current ops area)
]


async def _fetch_point(client: httpx.AsyncClient, lat: float, lon: float) -> list[dict]:
    try:
        resp = await client.get(f"/v2/point/{lat}/{lon}/{RADIUS_NM}")
        resp.raise_for_status()
    except Exception:
        logger.warning("adsb.lol point query failed for (%s, %s)", lat, lon, exc_info=True)
        return []
    return resp.json().get("ac") or []


async def _upsert_matches(matches: list[dict]) -> int:
    if not matches:
        return 0
    async with SessionLocal() as session:
        known_tails = set((await session.execute(select(Aircraft.tail_number))).scalars())
        new_count = 0
        for m in matches:
            tail = m["tail"]
            if tail in known_tails:
                continue  # already a real Aircraft row -- nothing to discover
            stmt = (
                pg_insert(DiscoveredCallsign)
                .values(
                    tail_number=tail,
                    callsign=m["callsign"],
                    last_latitude=m["lat"],
                    last_longitude=m["lon"],
                )
                .on_conflict_do_update(
                    constraint="uq_discovered_tail_callsign",
                    set_={
                        "last_seen_at": func.now(),
                        "sample_count": DiscoveredCallsign.sample_count + 1,
                        "last_latitude": m["lat"],
                        "last_longitude": m["lon"],
                    },
                )
            )
            await session.execute(stmt)
            new_count += 1
        await session.commit()
    return new_count


def _extract_cap_matches(aircraft_list: list[dict]) -> list[dict]:
    matches = []
    for ac in aircraft_list:
        flight = (ac.get("flight") or "").strip()
        reg = (ac.get("r") or "").strip().upper()
        if not flight or not reg:
            continue
        if CAP_CALLSIGN_RE.match(flight):
            matches.append({"tail": reg, "callsign": flight.upper(), "lat": ac.get("lat"), "lon": ac.get("lon")})
    return matches


async def run_forever() -> None:
    logger.info("Starting CAP callsign discovery: %d grid points, %ds between queries", len(GRID_POINTS), POLL_INTERVAL_SECONDS)
    grid_index = 0
    async with httpx.AsyncClient(
        base_url=ADSB_LOL_BASE_URL,
        timeout=10.0,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
    ) as client:
        while True:
            lat, lon = GRID_POINTS[grid_index]
            grid_index = (grid_index + 1) % len(GRID_POINTS)

            aircraft_list = await _fetch_point(client, lat, lon)
            matches = _extract_cap_matches(aircraft_list)
            if matches:
                new_count = await _upsert_matches(matches)
                logger.info(
                    "(%s, %s): %d CAP-shaped callsign(s) seen, %d not already known: %s",
                    lat,
                    lon,
                    len(matches),
                    new_count,
                    ", ".join(f"{m['callsign']}/{m['tail']}" for m in matches),
                )

            await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_forever())
