"""FlightAware AeroAPI (v4) provider.

Verified against live data 2026-09 against real CAP tail numbers during an active
exercise. Two things the original AeroAPI-docs-only draft got wrong, now fixed:

1. There is no direct "give me tail N188CA's current position" endpoint. You must
   search `/flights/{ident}` (ident = tail number or callsign; returns a list of
   flight-leg objects, most-recent-first, each with its own `fa_flight_id`), then
   fetch `/flights/{fa_flight_id}/track` for that specific leg's position history.
   NOTE: this confirms REQUIREMENTS.md 3.3's flight-stitching concern in a different
   way than expected -- AeroAPI *does* split legs into separate fa_flight_id objects
   here, so the mis-stitching the user observed isn't happening at this layer for
   every case; AvTrack's own sortie-segmentation logic (not yet built, see
   app/ingestion/heuristics.py) still owns the job of not trusting any single leg
   object as ground truth for engine-on/off boundaries.
2. `altitude` in the track response is in **hundreds of feet** (e.g. `12` = 1200 ft),
   not feet directly -- multiplied by 100 below.

RATE LIMIT / QUOTA: an early live-traffic test (2026-09-12, 20 tracked aircraft, 15s
poll interval, 2 API calls/tail/cycle) hit AeroAPI 429s with "User has reached quota
limit" within ~4 poll cycles, and a same-day retry 429'd on every single tail
immediately -- looked at the time like a hard consumption cap (daily/monthly).
Re-tested 2026-09-13 at a 90s interval after a pause: the first ~9-10 calls in a fresh
cycle succeeded, then every call after that 429'd for the rest of the cycle. That
pattern (partial success then a wall, in the same session) reads much more like a
short-window rate limit (calls per minute or similar) that a back-to-back burst blows
through, not a hard exhausted quota -- so `adsb_inter_request_delay_seconds` (app/
config.py) now paces individual calls within a cycle instead of firing them all at
once. Neither theory is confirmed against FlightAware's actual published limits for
this key's plan -- that's still the thing to go check on the AeroAPI portal.
`_flight_id_cache` below separately cuts steady-state cost roughly in half (1 call/
tail/cycle instead of 2) by reusing a tail's `fa_flight_id` across cycles instead of
re-searching every time.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app.adsb.base import ADSBProvider, AircraftPosition
from app.config import settings

AEROAPI_BASE_URL = "https://aeroapi.flightaware.com/aeroapi"

# How long to trust a cached fa_flight_id before re-searching, even if the cached
# flight hasn't shown as landed. Re-searching sooner catches a new sortie faster but
# costs an extra call/tail each time it happens -- tune once the real quota is known.
SEARCH_REFRESH_INTERVAL = timedelta(minutes=5)

# Real bug found live 2026-09-12: aircraft parked on the ramp at KBAK, engines off,
# were showing nonzero altitude/airspeed in the UI. Cause: once a flight lands, its
# `/track` still returns that completed flight's last recorded point (near
# touchdown/rollout -- nonzero speed/altitude), and nothing checked how stale that
# point actually was before reporting it as the aircraft's *current* position. Fixed by
# age alone, deliberately NOT by trusting FlightAware's own `landed`/`actual_on` field
# -- see the comment at the actual check in _get_one for why (that field is itself
# known-unreliable, per a separate FlightAware landing-detection failure Gerry saw the
# day before). 15 min is generous on purpose: this API tier's track data can lag
# several minutes behind real time even for a confirmed-active flight.
MAX_POSITION_AGE = timedelta(minutes=15)

logger = logging.getLogger(__name__)


@dataclass
class _CachedFlight:
    fa_flight_id: str
    ident: str
    landed: bool
    cached_at: datetime


class FlightAwareProvider(ADSBProvider):
    name = "flightaware"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.flightaware_api_key
        if not self._api_key:
            raise RuntimeError("FLIGHTAWARE_API_KEY is not set")
        self._flight_cache: dict[str, _CachedFlight] = {}
        self._last_request_at: float | None = None  # asyncio.get_event_loop().time(), not wall clock

    async def _pace(self) -> None:
        """Enforce a minimum gap before every single AeroAPI call, not just between
        tails. Evidence 2026-09-13: pacing only between tails (not between a tail's own
        search+track pair) still hit the same wall at the same *call count* regardless
        of the per-tail delay used (2.5s and 6s both failed at the same point) --
        consistent with a rolling calls-per-minute limit, which only a per-call gate
        actually respects."""
        loop = asyncio.get_event_loop()
        now = loop.time()
        if self._last_request_at is not None:
            wait = settings.adsb_inter_request_delay_seconds - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_request_at = loop.time()

    async def get_positions(self, tail_numbers: list[str]) -> list[AircraftPosition]:
        positions: list[AircraftPosition] = []
        async with httpx.AsyncClient(
            base_url=AEROAPI_BASE_URL,
            headers={"x-apikey": self._api_key},
            timeout=10.0,
        ) as client:
            for tail in tail_numbers:
                # Any single tail's failure (no recent flight, transient API error,
                # unexpected response shape) must not abort the other tails in this
                # poll cycle -- that was the original bug: a 400 on one tail crashed
                # the whole run_poll_loop iteration for all tracked aircraft.
                try:
                    position = await self._get_one(client, tail)
                except Exception:
                    logger.exception("FlightAware lookup failed for %s", tail)
                    continue
                if position is not None:
                    positions.append(position)
        return positions

    async def _resolve_fa_flight_id(self, client: httpx.AsyncClient, tail_number: str) -> _CachedFlight | None:
        cached = self._flight_cache.get(tail_number)
        stale = cached is None or (datetime.now(timezone.utc) - cached.cached_at) > SEARCH_REFRESH_INTERVAL
        # A landed flight is worth re-checking sooner than the general staleness
        # window, since that's exactly when a new sortie may have started.
        if cached is not None and cached.landed:
            stale = True
        if not stale:
            return cached

        await self._pace()
        search_resp = await client.get(f"/flights/{tail_number}")
        if search_resp.status_code == 404:
            return None
        search_resp.raise_for_status()
        flights = search_resp.json().get("flights") or []
        if not flights:
            return None

        # Prefer a leg still in the air (no actual landing time yet); fall back to
        # the most recent leg overall (results are most-recent-first) for a last-known
        # position after landing.
        flight = next((f for f in flights if not f.get("actual_on")), flights[0])
        resolved = _CachedFlight(
            fa_flight_id=flight["fa_flight_id"],
            ident=flight.get("ident") or tail_number,
            landed=bool(flight.get("actual_on")),
            cached_at=datetime.now(timezone.utc),
        )
        self._flight_cache[tail_number] = resolved
        return resolved

    async def _get_one(self, client: httpx.AsyncClient, tail_number: str) -> AircraftPosition | None:
        flight = await self._resolve_fa_flight_id(client, tail_number)
        if flight is None:
            return None

        await self._pace()
        track_resp = await client.get(f"/flights/{flight.fa_flight_id}/track")
        if track_resp.status_code == 404:
            # Cached id went stale (e.g. AeroAPI expired/replaced it) -- drop the
            # cache entry so the next cycle re-searches instead of repeating this.
            self._flight_cache.pop(tail_number, None)
            return None
        track_resp.raise_for_status()
        points = track_resp.json().get("positions") or []
        if not points:
            return None
        last = points[-1]

        ts_utc = datetime.fromisoformat(last["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)
        age = datetime.now(timezone.utc) - ts_utc
        if age > MAX_POSITION_AGE:
            # Deliberately does NOT special-case on `flight.landed`. An earlier version
            # of this used `landed` as a hard "definitely not current" gate -- wrong,
            # per Gerry (2026-09-12): FlightAware's own landing detection already
            # failed on a real flight the day before (part of why REQUIREMENTS.md 3.3
            # says never trust a provider's flight-boundary/status fields as ground
            # truth -- same principle, different field this time: `actual_on`/`landed`
            # instead of flight-ID stitching). If FlightAware wrongly marks a flight
            # landed mid-touch-and-go, a hard gate on that flag would hide a genuinely
            # airborne aircraft. Freshness alone doesn't have that failure mode: a
            # touch-and-go keeps producing fresh track points throughout regardless of
            # what the `landed` flag says, so it never ages past this cutoff; a truly
            # parked, shut-down aircraft's last point stops updating and correctly ages
            # out. MAX_POSITION_AGE is set generously (15 min) since this API tier's
            # own track data can lag several minutes even for a genuinely active
            # flight (observed ~6-9 min lag on N121CP/CAP1221 while confirmed en
            # route) -- the goal here is catching truly stale/orphaned data, not
            # shaving latency.
            return None

        altitude_ft = last["altitude"] * 100 if last.get("altitude") is not None else None
        return AircraftPosition(
            tail_number=tail_number,
            callsign=flight.ident,
            ts_utc=ts_utc,
            latitude=last["latitude"],
            longitude=last["longitude"],
            altitude_ft=altitude_ft,
            ground_speed_kt=last.get("groundspeed"),
            heading_deg=last.get("heading"),
            raw=last,
        )
