"""adsb.fi provider -- third concurrent ADS-B source (REQUIREMENTS.md 3.5, added
2026-10-24). Same lineage/API shape as adsb.lol (app/adsb/adsb_lol.py) -- both are
readsb/ADSBExchange-v2-convention forks -- confirmed live against the real,
documented endpoint from github.com/adsbfi/opendata, after an earlier attempt
wrongly concluded adsb.fi was Cloudflare-blocked (see adsb_lol.py's docstring for
the correction: that was our own bad URL, not a real block).

ENDPOINT: base `https://opendata.adsb.fi/api`, registration lookup at
`/v2/registration/[reg]` -- NOT `/v2/reg/[reg]` like adsb.lol; that was the wrong
guess that caused the earlier false "blocked" conclusion. Confirmed live
2026-10-24 that this endpoint also accepts a comma-separated list, same bulk
convention as adsb.lol (`/v2/registration/N1,N2` -> both in one `ac` array).

RATE LIMIT -- this one IS documented, unlike adsb.lol's "dynamic" guesswork
(github.com/adsbfi/opendata): public endpoints are capped at 1 request/second, and
**any 400/401/403/404/429 response counts toward a temporary IP restriction for
"excessive invalid requests"** -- so unlike adsb.lol, a malformed request here is a
real, documented way to get blocked, not just a wasted call. This provider's own
earlier wrong-URL testing already generated several of those from two different
networks before the correct path was found; watch for a restriction that isn't
self-inflicted by *this* code before assuming adsb.fi itself is being flaky.

No API key for the public tier. Feeding adsb.fi unlocks a higher-rate feeder-only
snapshot endpoint, not pursued here -- this provider only uses the public tier.
"""

import logging

import httpx

from app.adsb import rate_limit
from app.adsb.base import ADSBProvider, AircraftPosition
from app.adsb.adsb_lol import MAX_POSITION_AGE, _parse_one_readsb_v2

ADSB_FI_BASE_URL = "https://opendata.adsb.fi/api"

logger = logging.getLogger(__name__)


class AdsbFiProvider(ADSBProvider):
    name = "adsb_fi"

    async def get_positions(self, tail_numbers: list[str]) -> list[AircraftPosition]:
        if not tail_numbers:
            return []
        if rate_limit.adsb_fi.blocked():
            return []
        async with httpx.AsyncClient(
            base_url=ADSB_FI_BASE_URL,
            timeout=10.0,
            # Same Cloudflare UA fingerprinting precaution as adsb.lol -- harmless
            # here if unnecessary, cheap insurance if adsb.fi's edge behaves the
            # same way.
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        ) as client:
            try:
                resp = await client.get(f"/v2/registration/{','.join(tail_numbers)}")
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    rate_limit.adsb_fi.on_429()
                logger.exception("adsb.fi bulk lookup failed for %d tails", len(tail_numbers))
                return []
            except Exception:
                logger.exception("adsb.fi bulk lookup failed for %d tails", len(tail_numbers))
                return []
            rate_limit.adsb_fi.on_success()
            aircraft_list = resp.json().get("ac") or []

        wanted = {t.strip().upper() for t in tail_numbers}
        positions: list[AircraftPosition] = []
        for ac in aircraft_list:
            reg = (ac.get("r") or "").strip().upper()
            if reg not in wanted:
                continue
            position = _parse_one_readsb_v2(reg, ac, MAX_POSITION_AGE)
            if position is not None:
                positions.append(position)
        return positions
