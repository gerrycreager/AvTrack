import asyncio
import contextlib
import logging
from concurrent.futures import ProcessPoolExecutor

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.adsb.adsb_fi import AdsbFiProvider
from app.adsb.adsb_lol import AdsbLolProvider
from app.adsb.flightaware import FlightAwareProvider
from app.adsb.opensky import OpenSkyProvider
from app.api import (
    aircraft,
    airfields,
    airspace,
    auth,
    discovered,
    events,
    gis,
    locate,
    missions,
    ops_board,
    registration,
    reports,
    sorties,
    swim,
    weather,
    wmirs_import,
    ws,
)
from app.config import settings
from app.db import init_db
from app.ingestion.ao_discovery_poller import run_ao_discovery_loop
from app.ingestion.candidate_refresh_poller import run_candidate_refresh_loop
from app.ingestion.poller import run_poll_loop
from app.ingestion.position_retention import run_position_retention_loop

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AvTrack")


@app.middleware("http")
async def no_cache_for_frontend(request, call_next):
    """Without an explicit Cache-Control, browsers fall back to their own heuristic
    caching of static files (main.js, index.html) -- unpredictable, and confirmed
    2026-09-13 to be the likely cause of a run of "my fix isn't showing up even after
    a refresh" reports (checked: no Cache-Control header was being sent at all).
    Force revalidation on every load instead -- cheap, since it's still a 304 over the
    existing ETag when nothing actually changed, just never silently stale."""
    response = await call_next(request)
    if not request.url.path.startswith(("/api", "/ws")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


app.include_router(aircraft.router)
app.include_router(airfields.router)
app.include_router(airspace.router)
app.include_router(auth.router)
app.include_router(discovered.router)
app.include_router(events.router)
app.include_router(gis.router)
app.include_router(locate.router)
app.include_router(missions.router)
app.include_router(ops_board.router)
app.include_router(registration.router)
app.include_router(reports.router)
app.include_router(sorties.router)
app.include_router(swim.router)
app.include_router(weather.router)
app.include_router(wmirs_import.router)
app.include_router(ws.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def on_startup():
    await init_db()
    logger = logging.getLogger(__name__)
    app.state.poll_tasks = []

    # Shared CPU-bound worker pool (added 2026-11-02, found live) -- app/api/
    # airfields.py and app/api/airspace.py's per-row geometry-serialization loops
    # are heavy enough (up to ~13s at a wide bbox) that running them as a plain
    # asyncio.to_thread() still let the GIL stall other concurrent requests/the
    # ADS-B poll loops for seconds at a time (confirmed live: one of three
    # concurrent requests to `/` took 6s while a big SUA call ran). A real OS
    # process has no GIL to contend with the event loop's own thread. Created
    # once here, not per-request -- spawning a process pool is itself expensive.
    # max_workers=2 is deliberately small: this is for occasional heavy map
    # queries, not a general-purpose compute pool.
    app.state.cpu_pool = ProcessPoolExecutor(max_workers=2)

    # Independent of each other -- adsb.lol has no per-call cost (unlike AeroAPI, see
    # REQUIREMENTS.md 3.5's cost-pause history), so it defaults on regardless of
    # whether FlightAware polling is enabled.
    if not settings.adsb_polling_enabled:
        logger.warning("ADSB_POLLING_ENABLED=false -- FlightAware polling paused.")
    elif settings.flightaware_api_key:
        provider = FlightAwareProvider()
        app.state.poll_tasks.append(asyncio.create_task(run_poll_loop(provider)))
    else:
        logger.warning(
            "FLIGHTAWARE_API_KEY not set -- FlightAware polling disabled. Set it in .env to enable."
        )

    if settings.adsblol_polling_enabled:
        provider = AdsbLolProvider()
        app.state.poll_tasks.append(
            asyncio.create_task(run_poll_loop(provider, interval_seconds=settings.adsblol_poll_interval_seconds))
        )
    else:
        logger.warning("ADSBLOL_POLLING_ENABLED=false -- adsb.lol polling paused.")

    # Third free concurrent provider (REQUIREMENTS.md 3.5, added 2026-10-24) -- see
    # app/adsb/adsb_fi.py. Offset by half its own interval so it interleaves with
    # adsb.lol's identical 30s cadence instead of firing in lockstep with it --
    # see run_poll_loop's initial_delay_seconds docstring (REQUIREMENTS.md 3.5,
    # 2026-10-24). adsb.lol itself is left at offset 0/untouched -- it's actively
    # recovering from its own throttle episode, not the poller to add stagger risk
    # to.
    if settings.adsbfi_polling_enabled:
        provider = AdsbFiProvider()
        app.state.poll_tasks.append(
            asyncio.create_task(
                run_poll_loop(
                    provider,
                    interval_seconds=settings.adsbfi_poll_interval_seconds,
                    initial_delay_seconds=settings.adsbfi_poll_interval_seconds / 2,
                )
            )
        )
    else:
        logger.warning("ADSBFI_POLLING_ENABLED=false -- adsb.fi polling paused.")

    # Second free concurrent provider (REQUIREMENTS.md 3.5, added 2026-10-24) -- see
    # app/adsb/opensky.py for why this exists and its credit-budget math.
    if settings.opensky_polling_enabled:
        if settings.opensky_client_id and settings.opensky_client_secret:
            provider = OpenSkyProvider()
            app.state.poll_tasks.append(
                asyncio.create_task(run_poll_loop(provider, interval_seconds=settings.opensky_poll_interval_seconds))
            )
        else:
            logger.warning(
                "OPENSKY_CLIENT_ID/OPENSKY_CLIENT_SECRET not set -- OpenSky polling disabled. Set them in .env to enable."
            )
    else:
        logger.warning("OPENSKY_POLLING_ENABLED=false -- OpenSky polling paused.")

    # Fast, AO-scoped CAP/PARD discovery (REQUIREMENTS.md 3.1) -- independent of
    # the above pollers, which only ever track already-registered tails.
    if settings.ao_discovery_polling_enabled:
        app.state.poll_tasks.append(
            asyncio.create_task(run_ao_discovery_loop(interval_seconds=settings.ao_discovery_poll_interval_seconds))
        )
    else:
        logger.warning("AO_DISCOVERY_POLLING_ENABLED=false -- AO-scoped discovery polling paused.")

    # Fast refresh for already-discovered candidates (REQUIREMENTS.md 3.1) -- keeps
    # a known candidate's position near-real-time via the same bulk /v2/reg/ shape as
    # the registered-tail pollers above, instead of leaving it to the slow sweep's
    # lap. See app.ingestion.candidate_refresh_poller's docstring.
    if settings.candidate_refresh_polling_enabled:
        app.state.poll_tasks.append(
            asyncio.create_task(
                run_candidate_refresh_loop(interval_seconds=settings.candidate_refresh_poll_interval_seconds)
            )
        )
    else:
        logger.warning("CANDIDATE_REFRESH_POLLING_ENABLED=false -- candidate refresh polling paused.")

    # Hard retention policy for raw Position rows (REQUIREMENTS.md 3.5, added
    # 2026-10-19) -- see app.ingestion.position_retention's docstring.
    if settings.position_retention_enabled:
        app.state.poll_tasks.append(
            asyncio.create_task(
                run_position_retention_loop(interval_seconds=settings.position_retention_check_interval_seconds)
            )
        )
    else:
        logger.warning("POSITION_RETENTION_ENABLED=false -- Position rows will grow unbounded.")


@app.on_event("shutdown")
async def on_shutdown():
    for task in getattr(app.state, "poll_tasks", []):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    pool = getattr(app.state, "cpu_pool", None)
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


# Static frontend last, so it doesn't shadow /api and /ws routes above.
app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
