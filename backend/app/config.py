from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Native-run default: repo_root/frontend. Docker Compose overrides both via env
# (DATABASE_URL uses the `db` service hostname; FRONTEND_DIR=/app/frontend, matching
# the volume mount in docker-compose.yml).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    # Absolute path -- .env lives at the repo root, but the app may be launched with
    # `backend/` as cwd (as the native-run README instructions do), and a relative
    # ".env" would silently resolve to (and miss) backend/.env instead.
    model_config = SettingsConfigDict(env_file=str(_REPO_ROOT / ".env"), extra="ignore")

    database_url: str = "postgresql+asyncpg://avtrack:avtrack_dev_only@localhost:5432/avtrack"
    frontend_dir: str = str(_REPO_ROOT / "frontend")
    flightaware_api_key: str = ""
    # Pause ADS-B polling without removing the key -- see the RATE LIMIT / QUOTA note
    # in app/adsb/flightaware.py.
    adsb_polling_enabled: bool = True
    default_callsign_prefixes: str = "CAP,PARD"
    # Conservative default pending a known AeroAPI quota/rate limit -- see the
    # RATE LIMIT / QUOTA note in app/adsb/flightaware.py. Raise once that's known.
    adsb_poll_interval_seconds: int = 60
    # Delay between each tail's API call(s) within one poll cycle. Evidence 2026-09-13:
    # a fresh 90s-interval cycle succeeded for the first ~9-10 back-to-back calls, then
    # 429'd for the rest -- looks like a short-window rate limit we were bursting past,
    # not the hard exhausted quota it looked like on 2026-09-12. This paces calls
    # instead. Value is a first guess, not a confirmed number from FlightAware/the
    # AeroAPI portal -- tune once that's known.
    adsb_inter_request_delay_seconds: float = 2.5
    # adsb.lol is free/unauthenticated with no per-call cost (unlike AeroAPI), so this
    # defaults on independently of adsb_polling_enabled -- see app/adsb/adsb_lol.py.
    adsblol_polling_enabled: bool = True
    adsblol_poll_interval_seconds: int = 30
    # Air Ops Status Board (REQUIREMENTS.md 3.3) "missing aircraft" trigger, minutes
    # past the computed ETA -- changed 2026-10-14 from the CAWG AOBD course's local
    # 10-minute default to CAPR 70-10 draft S10.11.4.5.3.1's national "missing
    # aircraft follow-up time not later than one hour after ETA" ("that's been the
    # standard for CAP and FAA for a long time" -- Gerry). Per-sortie overridable
    # via Sortie.overdue_threshold_minutes; this is only the fallback. Used for BOTH
    # halves of the "missing" alert (see ops_board.py): overdue past ETA by this
    # much AND no contact for this long -- not two separate numbers, per Gerry's
    # "an hour AND an hour" framing.
    default_overdue_threshold_minutes: int = 60
    # Independent early-warning threshold (REQUIREMENTS.md 3.3, added 2026-10-14) --
    # NOT tied to ETA/overdue at all, purely "haven't heard from this aircraft in a
    # while." Per Gerry: "a slow flashing reminder if an aircraft has not been heard
    # from in 30 min... a 10 min grace period on comms beyond the 30 min no
    # contact" -- the actual trigger is the sum of these two, not a single number,
    # so the grace period can be tuned independently of the base threshold.
    no_contact_warning_minutes: int = 30
    no_contact_grace_minutes: int = 10
    # WxCOP integration (REQUIREMENTS.md 3.3a) -- airfield layer's Flight Impacts and
    # Station Plot display modes, see app/integrations/wxcop_client.py for the
    # connection-recipe rationale. Same kill-switch convention as adsb_polling_enabled.
    wxcop_base_url: str = "http://192.168.0.200"
    wxcop_host_header: str = "wxcop.duckdns.org"
    wxcop_enabled: bool = True
    # Fast, AO-scoped CAP/PARD callsign discovery (REQUIREMENTS.md 3.1, added
    # 2026-10-14) -- separate cadence from both the tracked-tail pollers above
    # (different call shape: adsb.lol point-radius, not bulk /v2/reg/) and the
    # slow 22-point CONUS discovery sweep (scripts/discover_cap_callsigns.py,
    # 90s/point) -- this queries only the current Area of Operations, every
    # cycle, for near-real-time "unregistered aircraft entered the AO" alerting
    # on the Ops Board. Same kill-switch convention as adsb_polling_enabled.
    # 60s: faster than the slow net's 90s/point pace (this is the near-real-time
    # path and makes the same one-call-per-cycle as that net), but not so
    # aggressive it risks adsb.lol's known rate limit now that two independent
    # callers hit the same point-radius endpoint family -- a starting number to
    # tune once real 429 behavior with both pollers running is observed.
    ao_discovery_polling_enabled: bool = True
    ao_discovery_poll_interval_seconds: int = 60
    # Fast refresh for already-discovered candidates (REQUIREMENTS.md 3.1, added
    # 2026-10-15) -- see app.ingestion.candidate_refresh_poller's docstring for why
    # this exists (found live: a known candidate's position lagged by up to a full
    # slow-sweep lap even though adsb.lol itself had sub-second-fresh data for it).
    # 30s matches the registered-aircraft poller's cadence (adsb_poll_interval_seconds
    # default) since both use the identical one-bulk-call-per-cycle /v2/reg/ shape,
    # already proven safe against adsb.lol's rate limit at that cadence/tail-count.
    # Same kill-switch convention as adsb_polling_enabled.
    candidate_refresh_polling_enabled: bool = True
    candidate_refresh_poll_interval_seconds: int = 30
    # Per-user/per-mission accounts (REQUIREMENTS.md open question 7, added
    # 2026-10-16 per Gerry). Sessions are opaque random tokens (app/auth.py),
    # not signed JWTs, so there's no secret key to manage -- just a TTL.
    # Whether the session cookie carries Secure is decided per-request in
    # app/api/auth.py's login(), not a global flag here -- this deployment is
    # genuinely dual-path (plain-HTTP direct LAN at 192.168.0.200:8000 for
    # testing, HTTPS via the r815 reverse proxy for everything else), and a
    # single global setting can only be correct for one of those at a time
    # (found live 2026-10-22: flipping a global secure=True broke LAN login).
    session_ttl_hours: int = 12
    # Hard retention policy for raw Position rows (REQUIREMENTS.md 3.5, added
    # 2026-10-19). Per Gerry: a hard cutoff, 30 days initially, "could decrease to
    # 14 days" -- config-driven so tightening it later is a one-line .env change,
    # not a code change. Position has no other pruning and was already at 23k
    # rows / 19MB after ~6 weeks with only one provider polling; this stops
    # unbounded growth before it becomes a real problem. Same kill-switch
    # convention as the other pollers.
    position_retention_days: int = 30
    position_retention_enabled: bool = True
    position_retention_check_interval_seconds: int = 3600
    # OpenSky Network (REQUIREMENTS.md 3.5, added 2026-10-24) -- second concurrent
    # free provider, registered under Gerry's capnhq.gov account (Standard tier,
    # confirmed via OpenSky's own docs: this is the normal registered-account tier,
    # NOT gated behind running a feeder -- that only raises it further, to 8,000/day).
    # Credentials are an OAuth2 client_id/client_secret pair (OpenSky retired Basic
    # auth 2026-03-18), not a single key -- see app/adsb/opensky.py.
    opensky_client_id: str = ""
    opensky_client_secret: str = ""
    # No per-call cost (unlike AeroAPI) so this defaults on like adsb.lol's, not off
    # like FlightAware's -- same kill-switch convention regardless.
    opensky_polling_enabled: bool = True
    # Measured live 2026-10-24 against the real account: an icao24-filtered
    # /states/all call costs 4 credits, not the 1 credit OpenSky's docs suggest for
    # a "serial-only" query (confirmed by 3 back-to-back calls, remaining count
    # dropped by exactly 4 each time) -- going by the docs' own number here would
    # have silently exhausted the day's budget in under 17 minutes at adsb.lol's 30s
    # cadence. At 4,000 credits/day ÷ 4 = 1,000 calls/day max, a 90s interval
    # (960 calls/day) leaves real headroom for retries and the dev/test cycle,
    # while adsb.lol stays the fast, primary poller.
    opensky_poll_interval_seconds: int = 90
    # adsb.fi (REQUIREMENTS.md 3.5, added 2026-10-24) -- third concurrent free
    # provider, same lineage/shape as adsb.lol. No API key for the public tier.
    # Documented, explicit rate limit (unlike adsb.lol's undocumented guesswork):
    # 1 request/second public, see app/adsb/adsb_fi.py. One bulk call per cycle,
    # so interval_seconds=1.5 -> ~0.67 req/sec, still real margin under the
    # documented cap (not running at the exact edge), since adsb.fi's own policy
    # counts any 4xx toward a temporary IP restriction (unlike adsb.lol, where a
    # bad request just wastes a call). Tightened from 30s -> 2s 2026-10-24, then
    # 2s -> 1.5s 2026-11-02, both times per Gerry wanting a tighter
    # operating-picture cadence; adsb.lol itself was deliberately left untouched
    # at 30s (mid-recovery from its own throttle episode -- see
    # project_adsblol_throttling memory).
    adsbfi_polling_enabled: bool = True
    adsbfi_poll_interval_seconds: float = 1.5

    # Self-service account requests (REQUIREMENTS.md 3.7 follow-up, added
    # 2026-11-02) -- no SMTP credentials live here at all. app/email.py shells
    # out to the system `msmtp -a gmail` relay already used by CAP WxCOP's own
    # alerting (confirmed live: AvTrack's backend already runs as the `gerry`
    # user, which already has read access to /etc/msmtprc's ACL -- no new
    # secret, no copy, nothing in this file to configure for sending itself).
    # Comma-separated -- who gets the "new registration request" email.
    admin_notification_emails: str = ""
    # The real, externally-reachable base URL for links sent in email (e.g.
    # https://wxcop.duckdns.org:2468) -- never the LAN-only bind address the
    # backend itself listens on. Required (no safe default) for any emailed
    # link to be useful to someone off-LAN.
    public_base_url: str = ""

    @property
    def admin_notification_email_list(self) -> list[str]:
        return [e.strip() for e in self.admin_notification_emails.split(",") if e.strip()]

    @property
    def default_callsign_prefix_list(self) -> list[str]:
        return [p.strip().upper() for p in self.default_callsign_prefixes.split(",") if p.strip()]


settings = Settings()
