"""Client for WxCOP (CAP Weather Common Operating Picture), a sibling system on
host r815 (REQUIREMENTS.md 3.3a) -- feeds the airfield layer's Flight Impacts
(CAPR 70-1, migrating to 70-10) and Station Plot (METAR) display modes.

Connection recipe confirmed live 2026-10-14: r815's Apache does name-based vhost
dispatch, and its TLS cert isn't valid for the bare LAN IP, so an internal LAN call
has to hit the LAN IP directly with an explicit Host header rather than going
through TLS for what's already a same-network hop:
    curl --resolve wxcop.duckdns.org:80:192.168.0.200 http://wxcop.duckdns.org/...
`httpx` doesn't have a `--resolve` equivalent, so this sends the request to
`wxcop_base_url` (the LAN IP) with `Host: wxcop_host_header` set explicitly instead
-- equivalent effect.

data2 (WxCOP's own Postgres host) is confirmed down as of 2026-10-14 and not
expected back for about a week -- every DB-backed WxCOP endpoint (including both
of these) currently 500s with "No route to host". That isn't treated as unusual
here: this client fails soft and logs on *any* failure (connection refused,
timeout, non-2xx, unexpected shape) and never raises, mirroring
app/adsb/adsb_lol.py's bulk-call pattern -- callers get `None` and degrade the UI
accordingly, exactly like a transient network blip would be handled too.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def _get(path: str, params: dict) -> dict | None:
    if not settings.wxcop_enabled:
        return None
    try:
        async with httpx.AsyncClient(
            base_url=settings.wxcop_base_url,
            timeout=10.0,
            headers={"Host": settings.wxcop_host_header},
        ) as client:
            resp = await client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.warning("WxCOP request failed: GET %s %s", path, params, exc_info=True)
        return None


async def get_flight_impacts(bounds: str, hour: int = 1, op: str = "vfr") -> dict | None:
    """GET /CAP_WxCOP/api/weather-impacts/airports -- CAPR 70-1/70-10 Go/Caution/
    No-Go stoplight, keyed by reporting station. Response already includes a
    `color` field per airport, pre-computed by WxCOP -- don't re-derive it here."""
    return await _get(
        "/CAP_WxCOP/api/weather-impacts/airports",
        {"bounds": bounds, "hour": hour, "op": op},
    )


async def get_metar(bounds: str, limit: int = 2500) -> dict | None:
    """GET /CAP_WxCOP/api/weather-enhanced/metar/recent -- bulk recent METAR,
    DISTINCT ON station_id, feeds the Station Plot display mode."""
    return await _get(
        "/CAP_WxCOP/api/weather-enhanced/metar/recent",
        {"bounds": bounds, "limit": limit},
    )
