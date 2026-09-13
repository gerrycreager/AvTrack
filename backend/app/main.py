import asyncio
import contextlib
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.adsb.adsb_lol import AdsbLolProvider
from app.adsb.flightaware import FlightAwareProvider
from app.api import aircraft, airfields, airspace, events, gis, locate, sorties, swim, ws
from app.config import settings
from app.db import init_db
from app.ingestion.poller import run_poll_loop

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
app.include_router(events.router)
app.include_router(gis.router)
app.include_router(locate.router)
app.include_router(sorties.router)
app.include_router(swim.router)
app.include_router(ws.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def on_startup():
    await init_db()
    logger = logging.getLogger(__name__)
    app.state.poll_tasks = []

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


@app.on_event("shutdown")
async def on_shutdown():
    for task in getattr(app.state, "poll_tasks", []):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# Static frontend last, so it doesn't shadow /api and /ws routes above.
app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
