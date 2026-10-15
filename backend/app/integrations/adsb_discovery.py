"""Shared adsb.lol point-radius query + CAP/PARD callsign matching + upsert logic
(REQUIREMENTS.md 3.1, extracted 2026-10-14). Used by both scripts/discover_cap_callsigns.py
(the slow 22-point CONUS sweep) and app/ingestion/ao_discovery_poller.py (the fast
poller scoped to the live OperationsArea) -- extracted here so both stay in sync on
the CAP/PARD regex and upsert shape instead of carrying two copies that can drift.

See scripts/discover_cap_callsigns.py's original module docstring for the full
history/rationale (adsb.lol's point-radius endpoint, its undocumented rate limit,
the UA-fingerprinting 403, the "candidate list, not auto-promoted" design) -- not
repeated here.

PARD callsigns follow the same numeric-suffix shape as CAP's, just a different
prefix word (confirmed with Gerry 2026-10-14): "PARD123", "PARD 123", etc.
"""

import logging
import math

import httpx
import re
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.models import Aircraft, DiscoveredCallsign

logger = logging.getLogger(__name__)

ADSB_LOL_BASE_URL = "https://api.adsb.lol"
# httpx's default User-Agent gets a flat 403 from this host's Cloudflare (same
# fingerprinting issue documented in app/adsb/adsb_lol.py) -- any normal-looking UA
# clears it.
ADSB_LOL_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
# adsb.lol's point-radius endpoint caps the radius at 250nm.
ADSB_LOL_MAX_RADIUS_NM = 250

CALLSIGN_RE = re.compile(r"^(CAP|PARD)\s?\d+$", re.IGNORECASE)


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=ADSB_LOL_BASE_URL,
        timeout=10.0,
        headers={"User-Agent": ADSB_LOL_USER_AGENT},
    )


async def fetch_point(client: httpx.AsyncClient, lat: float, lon: float, radius_nm: float) -> list[dict]:
    radius = min(int(radius_nm), ADSB_LOL_MAX_RADIUS_NM)
    try:
        resp = await client.get(f"/v2/point/{lat}/{lon}/{radius}")
        resp.raise_for_status()
    except Exception:
        logger.warning("adsb.lol point query failed for (%s, %s, %snm)", lat, lon, radius, exc_info=True)
        return []
    return resp.json().get("ac") or []


def extract_matches(aircraft_list: list[dict]) -> list[dict]:
    matches = []
    for ac in aircraft_list:
        flight = (ac.get("flight") or "").strip()
        reg = (ac.get("r") or "").strip().upper()
        if not flight or not reg:
            continue
        m = CALLSIGN_RE.match(flight)
        if m:
            # alt_baro is either a number of feet or the literal string "ground"
            # (same quirk handled in app/adsb/adsb_lol.py) -- normalized here since
            # this feeds a plain Float column, not passed through as Any.
            alt_baro = ac.get("alt_baro")
            matches.append(
                {
                    "tail": reg,
                    "callsign": flight.upper(),
                    "lat": ac.get("lat"),
                    "lon": ac.get("lon"),
                    "source_label": m.group(1).upper(),
                    "altitude_ft": 0.0 if alt_baro == "ground" else alt_baro,
                    "ground_speed_kt": ac.get("gs"),
                    "heading_deg": ac.get("track"),
                }
            )
    return matches


async def upsert_matches(matches: list[dict]) -> int:
    """Upserts into discovered_callsigns, skipping any tail already a real
    Aircraft row. Never clears dismissed_at on a re-sighting -- see
    DiscoveredCallsign's docstring for why a dismissed candidate must stay
    dismissed through further sightings."""
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
                    source_label=m["source_label"],
                    last_latitude=m["lat"],
                    last_longitude=m["lon"],
                    last_altitude_ft=m["altitude_ft"],
                    last_ground_speed_kt=m["ground_speed_kt"],
                    last_heading_deg=m["heading_deg"],
                )
                .on_conflict_do_update(
                    constraint="uq_discovered_tail_callsign",
                    set_={
                        "last_seen_at": func.now(),
                        "sample_count": DiscoveredCallsign.sample_count + 1,
                        "last_latitude": m["lat"],
                        "last_longitude": m["lon"],
                        "last_altitude_ft": m["altitude_ft"],
                        "last_ground_speed_kt": m["ground_speed_kt"],
                        "last_heading_deg": m["heading_deg"],
                    },
                )
            )
            await session.execute(stmt)
            new_count += 1
        await session.commit()
    return new_count


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_nm = 3440.065  # Earth radius in nautical miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r_nm * math.asin(math.sqrt(a))
