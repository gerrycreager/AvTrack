"""Backoff helper for an aggregator's rate limit (REQUIREMENTS.md 3.5).

Originally single-instance, module-level state for adsb.lol alone, shared by both
in-process callers that hit it -- app/adsb/adsb_lol.py's registered-aircraft poller
and app/integrations/adsb_discovery.py's candidate discovery/refresh. Both run in
this same uvicorn process and share one remote rate limit, so a 429 on either call
site must back off both -- that's still true and is why those two call sites share
one `RateLimiter` instance (`adsb_lol` below).

Turned into a class 2026-10-24 when adsb.fi was added as a third caller hitting a
*different* remote host with its own, independent limit -- sharing adsb.lol's
module-level state across both would have been wrong (a 429 from one host has
nothing to do with the other's budget). Each host gets its own instance instead.

Found live 2026-10-18 (adsb.lol): a sustained 429 storm (every call from this host
failing, continuously, for 20+ minutes -- not the single-cycle blip a prior session
assumed) with zero backoff anywhere. Each caller just logged the failure and
retried next cycle 30-60s later, forever, which never gives adsb.lol a quiet
window to lift the limit and may well be what triggered it in the first place
(two large bulk calls roughly every 15-30s, indefinitely). This cooldown is the
fix: skip the network call entirely while blocked, doubling the cooldown on each
further 429 up to a cap, resetting to the base on any success.
"""

import logging
import time

logger = logging.getLogger(__name__)

_BASE_COOLDOWN_SECONDS = 30.0
_MAX_COOLDOWN_SECONDS = 3600.0


class RateLimiter:
    def __init__(self, label: str, max_cooldown_seconds: float = _MAX_COOLDOWN_SECONDS) -> None:
        self._label = label
        self._max_cooldown_seconds = max_cooldown_seconds
        self._cooldown_seconds = 0.0
        self._blocked_until = 0.0

    def blocked(self) -> bool:
        return time.monotonic() < self._blocked_until

    def on_429(self) -> None:
        self._cooldown_seconds = (
            min(self._cooldown_seconds * 2, self._max_cooldown_seconds) if self._cooldown_seconds else _BASE_COOLDOWN_SECONDS
        )
        self._blocked_until = time.monotonic() + self._cooldown_seconds
        logger.warning("%s rate-limited -- backing off %.0fs", self._label, self._cooldown_seconds)

    def on_success(self) -> None:
        self._cooldown_seconds = 0.0


# adsb.lol -- raised to a 3600s (1hr) ceiling 2026-10-20, see this module's docstring.
# Shared by app/adsb/adsb_lol.py and app/integrations/adsb_discovery.py.
adsb_lol = RateLimiter("adsb.lol")

# adsb.fi -- own instance, own remote host, own budget. Public-tier limit is a
# documented 1 req/sec (adsbfi/opendata on GitHub); a 429 here is a real abuse
# signal, not adsb.lol's undocumented "dynamic load" guesswork, but the same
# skip-while-blocked/exponential-backoff shape still applies.
adsb_fi = RateLimiter("adsb.fi")
