"""OpenSky Network provider -- second concurrent, free ADS-B source (REQUIREMENTS.md
3.5, added 2026-10-24). Registered under Gerry's capnhq.gov account after adsb.fi and
ADSB One both turned out to be Cloudflare-blocked from our networks (see
app/adsb/adsb_lol.py's docstring) and ADS-B Exchange's reply pointed at their paid
tier instead of the free CAP/SAR feeder program. OpenSky's "Standard" tier (4,000
credits/day) is confirmed (their own docs, and live on Gerry's account) to be the
normal registered-account tier -- NOT gated behind running a feeder, which only
raises the ceiling further (to 8,000/day).

AUTH: OAuth2 client_credentials only (OpenSky retired Basic auth 2026-03-18).
Live-confirmed 2026-10-24: POST client_id/client_secret as form data to
https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token,
get back a Bearer `access_token` good for `expires_in` seconds (1800 live). Cache and
reuse across polls; refresh proactively a bit before expiry, and also on a live 401
(belt-and-suspenders, matching FlightAware's existing pattern of not trusting a
single invalidation signal).

IDENTITY: unlike adsb.lol/FlightAware, /states/all can only be filtered by icao24
(24-bit transponder hex), never by registration -- there is no /reg/-style lookup.
AvTrack's own tables are registration-first, so this provider does its own DB lookup
to translate tail_number -> Aircraft.icao24 before calling out. A tail with no known
icao24 yet (never seen by a provider that reports "hex", i.e. adsb.lol -- see
poller.py's opportunistic backfill) is silently skipped for this provider only;
adsb.lol remains the provider that discovers it in the first place.

CREDIT COST -- measured live 2026-10-24, NOT what OpenSky's own docs claim: their
docs describe a "serial-only" (icao24-filtered) /states/all call as 1 credit, but
3 back-to-back real calls each dropped the account's X-Rate-Limit-Remaining by
exactly 4. Treat it as 4 credits/call until proven otherwise -- see
config.py's opensky_poll_interval_seconds for the budget math this drives.

RATE LIMIT: OpenSky is actually explicit here (unlike adsb.lol's undocumented,
guessed-at backoff) -- a 429 carries X-Rate-Limit-Retry-After-Seconds telling us
exactly how long to wait. Honor that directly instead of guessing.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.adsb.base import ADSBProvider, AircraftPosition
from app.config import settings
from app.db import SessionLocal
from app.models import Aircraft

OPENSKY_API_BASE_URL = "https://opensky-network.org/api"
OPENSKY_TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

# last_contact is a unix timestamp of the aircraft's last update of any kind (not
# necessarily position) -- a defensive staleness backstop, same principle as
# adsb.lol's MAX_POSITION_AGE and FlightAware's, not expected to trigger often since
# we only ever ask for tails we expect to be live.
MAX_POSITION_AGE = timedelta(minutes=15)

# Refresh this many seconds before the token's real expiry, so a poll cycle never
# starts a call with a token that expires mid-flight.
TOKEN_REFRESH_MARGIN_SECONDS = 60

logger = logging.getLogger(__name__)


class OpenSkyProvider(ADSBProvider):
    name = "opensky"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None) -> None:
        self._client_id = client_id or settings.opensky_client_id
        self._client_secret = client_secret or settings.opensky_client_secret
        if not self._client_id or not self._client_secret:
            raise RuntimeError("OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET are not set")
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0  # time.monotonic()
        self._blocked_until: float = 0.0  # time.monotonic(), from a 429's Retry-After

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if self._access_token is not None and time.monotonic() < self._token_expires_at:
            return self._access_token
        resp = await client.post(
            OPENSKY_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        body = resp.json()
        self._access_token = body["access_token"]
        expires_in = body.get("expires_in", 1800)
        self._token_expires_at = time.monotonic() + expires_in - TOKEN_REFRESH_MARGIN_SECONDS
        return self._access_token

    async def _known_icao24s(self, tail_numbers: list[str]) -> dict[str, str]:
        """tail_number -> icao24 for whichever of the requested tails we already
        know a hex for. Tails with no known icao24 yet are simply absent from the
        returned dict -- see this module's docstring."""
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(Aircraft.tail_number, Aircraft.icao24).where(
                        Aircraft.tail_number.in_(tail_numbers), Aircraft.icao24.is_not(None)
                    )
                )
            ).all()
        return {tail: icao24 for tail, icao24 in rows}

    async def get_positions(self, tail_numbers: list[str]) -> list[AircraftPosition]:
        if time.monotonic() < self._blocked_until:
            return []
        icao24_by_tail = await self._known_icao24s(tail_numbers)
        if not icao24_by_tail:
            return []
        tail_by_icao24 = {v: k for k, v in icao24_by_tail.items()}

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                token = await self._get_token(client)
                resp = await client.get(
                    f"{OPENSKY_API_BASE_URL}/states/all",
                    params=[("icao24", icao24) for icao24 in icao24_by_tail.values()],
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 401:
                    # Belt-and-suspenders, matching FlightAware's existing distrust of
                    # a single invalidation signal -- force a fresh token and retry
                    # exactly once rather than looping.
                    self._access_token = None
                    token = await self._get_token(client)
                    resp = await client.get(
                        f"{OPENSKY_API_BASE_URL}/states/all",
                        params=[("icao24", icao24) for icao24 in icao24_by_tail.values()],
                        headers={"Authorization": f"Bearer {token}"},
                    )
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("X-Rate-Limit-Retry-After-Seconds", 300))
                    self._blocked_until = time.monotonic() + retry_after
                    logger.warning("OpenSky rate-limited -- backing off %.0fs (per Retry-After header)", retry_after)
                    return []
                resp.raise_for_status()
            except Exception:
                logger.exception("OpenSky states/all lookup failed for %d tails", len(icao24_by_tail))
                return []

        body = resp.json()
        states = body.get("states") or []
        now = datetime.now(timezone.utc)
        positions: list[AircraftPosition] = []
        for state in states:
            icao24 = (state[0] or "").strip().lower()
            tail = tail_by_icao24.get(icao24)
            if tail is None:
                continue
            position = self._parse_one(tail, state, now)
            if position is not None:
                positions.append(position)
        return positions

    def _parse_one(self, tail_number: str, state: list, now: datetime) -> AircraftPosition | None:
        # Field order per OpenSky's documented state vector, confirmed 2026-10-24:
        # 0 icao24, 1 callsign, 2 origin_country, 3 time_position, 4 last_contact,
        # 5 longitude, 6 latitude, 7 baro_altitude, 8 on_ground, 9 velocity,
        # 10 true_track, 11 vertical_rate, 12 sensors, 13 geo_altitude, 14 squawk,
        # 15 spi, 16 position_source.
        latitude, longitude = state[6], state[5]
        if latitude is None or longitude is None:
            return None  # known to the API but no current position (ground station only, etc.)

        last_contact = state[4]
        if last_contact is not None:
            ts_utc = datetime.fromtimestamp(last_contact, tz=timezone.utc)
            if now - ts_utc > MAX_POSITION_AGE:
                return None
        else:
            ts_utc = now

        on_ground = bool(state[8])
        baro_altitude_m = state[7]
        geo_altitude_m = state[13]
        altitude_m = baro_altitude_m if baro_altitude_m is not None else geo_altitude_m
        altitude_ft = 0.0 if on_ground else (altitude_m * 3.28084 if altitude_m is not None else None)

        velocity_ms = state[9]
        ground_speed_kt = velocity_ms * 1.94384 if velocity_ms is not None else None

        callsign = (state[1] or "").strip() or None

        return AircraftPosition(
            tail_number=tail_number,
            callsign=callsign,
            ts_utc=ts_utc,
            latitude=latitude,
            longitude=longitude,
            altitude_ft=altitude_ft,
            ground_speed_kt=ground_speed_kt,
            heading_deg=state[10],
            raw={
                "icao24": state[0],
                "origin_country": state[2],
                "time_position": state[3],
                "last_contact": last_contact,
                "on_ground": on_ground,
                "vertical_rate": state[11],
                "squawk": state[14],
                "spi": state[15],
                "position_source": state[16],
            },
        )
