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

    @property
    def default_callsign_prefix_list(self) -> list[str]:
        return [p.strip().upper() for p in self.default_callsign_prefixes.split(",") if p.strip()]


settings = Settings()
