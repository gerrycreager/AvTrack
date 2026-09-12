"""adsb.lol provider -- free, unauthenticated, community-fed raw ADS-B aggregator
(REQUIREMENTS.md 3.5). Investigated 2026-09-12 as a possible fix for the exact gap
FDPS/STDDS have: both of those derive from ATC systems and only surface aircraft
with a flight-plan/beacon-code correlation, which a local VFR CAP sortie squawking
1200 with no flight following never gets. adsb.lol has no such requirement -- it's
raw receiver-network ADS-B, the same category of source as ADS-B Exchange, just
free and open (ODbL 1.0) rather than airplanes.live's actual policy (contact-us
gated despite docs implying otherwise, confirmed by live test from both this host
and r815) or FlightAware's per-call cost.

Live-tested 2026-09-12 near Columbus/KBAK (CAP's own operating area): a 50nm point
query found 22 real aircraft including GA traffic at 1750ft and 4000ft -- exactly
the low-altitude local-pattern traffic FDPS/STDDS structurally can't see. Not yet
confirmed against an actual CAP tail airborne (none were flying at test time) --
that's the next real-world check once this is wired into the poll loop.

API shape mirrors the ADSBExchange v2 convention (this project is one of several
forks of the same lineage; sister project api.adsb.one publishes near-identical
docs but blocked this environment's IP via Cloudflare -- adsb.lol had no such
issue). No API key.

IMPORTANT -- one bulk call per poll cycle, not one call per tail: confirmed live
2026-09-12 that `/v2/reg/` accepts a comma-separated list of registrations and
returns all matches in a single response (`/v2/reg/N1,N2,N3` -> all matching
aircraft in one `ac` array). An earlier version of this provider called
`/v2/reg/{tail}` once per tracked tail (mirroring flightaware.py's per-tail
pattern) and immediately hit a hard 429 wall after just 2 calls even at ~1s
spacing -- adsb.lol's real rate limit (undocumented -- "dynamic based on
environment load", no fixed number, no Retry-After/X-RateLimit-* headers to key
off) is apparently much stricter than the per-request pacing that works fine for
FlightAware. Batching into one request/cycle sidesteps the limit entirely instead
of trying to tune a delay against an unknown, unstable target.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.adsb.base import ADSBProvider, AircraftPosition

ADSB_LOL_BASE_URL = "https://api.adsb.lol"

# This is a live-state snapshot API (not a track-history API like FlightAware) --
# `seen_pos` in the response is already "seconds since this position was last
# updated," so a stale/vanished aircraft simply stops appearing in results at all
# rather than lingering with an old timestamp. This check is a defensive backstop
# matching the same "don't trust a single field, check data freshness" principle
# used elsewhere (REQUIREMENTS.md 3.3), not something expected to trigger often.
MAX_POSITION_AGE = timedelta(minutes=15)

logger = logging.getLogger(__name__)


class AdsbLolProvider(ADSBProvider):
    name = "adsb_lol"

    async def get_positions(self, tail_numbers: list[str]) -> list[AircraftPosition]:
        if not tail_numbers:
            return []
        async with httpx.AsyncClient(
            base_url=ADSB_LOL_BASE_URL,
            timeout=10.0,
            # httpx's default User-Agent ("python-httpx/x.x.x") gets a flat 403 from
            # this host's Cloudflare (confirmed live 2026-09-12: identical request
            # succeeds via curl's default UA, fails via httpx's) -- not a real
            # rate-limit or auth problem, just UA fingerprinting. Any normal-looking
            # UA clears it.
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        ) as client:
            try:
                resp = await client.get(f"/v2/reg/{','.join(tail_numbers)}")
                resp.raise_for_status()
            except Exception:
                logger.exception("adsb.lol bulk lookup failed for %d tails", len(tail_numbers))
                return []
            aircraft_list = resp.json().get("ac") or []

        # Match results back to requested tails by registration, case/whitespace
        # normalized -- don't assume the API echoes casing back exactly as sent.
        wanted = {t.strip().upper() for t in tail_numbers}
        positions: list[AircraftPosition] = []
        for ac in aircraft_list:
            reg = (ac.get("r") or "").strip().upper()
            if reg not in wanted:
                continue
            position = self._parse_one(reg, ac)
            if position is not None:
                positions.append(position)
        return positions

    def _parse_one(self, tail_number: str, ac: dict) -> AircraftPosition | None:
        seen_pos = ac.get("seen_pos")
        if seen_pos is not None and timedelta(seconds=seen_pos) > MAX_POSITION_AGE:
            return None
        if ac.get("lat") is None or ac.get("lon") is None:
            # Present in the aggregator (e.g. MLAT-only or a stale ground record) but
            # no usable position yet -- skip rather than guess.
            return None

        ts_utc = datetime.now(timezone.utc)
        if seen_pos is not None:
            ts_utc -= timedelta(seconds=seen_pos)

        # alt_baro is either a number of feet, or the literal string "ground" when the
        # aircraft is on the ground (a known field quirk in this API family) --
        # confirmed against real live responses 2026-09-12.
        alt_baro = ac.get("alt_baro")
        altitude_ft = 0.0 if alt_baro == "ground" else alt_baro

        flight = (ac.get("flight") or "").strip() or None  # padded to 8 chars in real responses

        return AircraftPosition(
            tail_number=tail_number,
            callsign=flight,
            ts_utc=ts_utc,
            latitude=ac["lat"],
            longitude=ac["lon"],
            altitude_ft=altitude_ft,
            ground_speed_kt=ac.get("gs"),
            heading_deg=ac.get("track"),  # true track over ground, not magnetic heading -- close enough here
            raw=ac,
        )
