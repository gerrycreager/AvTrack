"""Sortie tracking -- PIC, sortie/mission number, engine start/stop, and repeatable
in-grid/out-grid/Operations Check waypoints (REQUIREMENTS.md 3.3, added 2026-09-13
per Gerry, modeled on the RPP recorded-times spreadsheet CAP already uses).

Engine start/stop times are backed by TrackingEvent/EventEdit (the same append-only
+ correction-overlay mechanism app/api/events.py already exposes for other event
types) rather than plain fields on Sortie -- corrections to those go through the
existing `POST /api/events/{id}/edits` endpoint, not a Sortie-specific one. Sortie
metadata (PIC, sortie/mission number) is plain and mutable via PATCH -- not
safety-critical the way a timestamp is.
"""

from datetime import datetime, timezone

import mgrs
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.events import _effective_time, _get_aircraft_or_404
from app.db import get_session
from app.models import DetectionMethod, EventType, Sortie, SortieWaypoint, TrackingEvent, WaypointLocationMethod
from app.schemas import SortieCreateIn, SortieOut, SortiePatchIn, SortieStopIn, WaypointCreateIn, WaypointOut

router = APIRouter(tags=["sorties"])
_mgrs = mgrs.MGRS()

WAYPOINT_TYPES = {EventType.in_grid, EventType.out_grid, EventType.ops_check}


def _to_waypoint_out(wp: SortieWaypoint) -> WaypointOut:
    return WaypointOut(
        id=wp.id,
        waypoint_type=wp.event.event_type.value,
        time_utc=_effective_time(wp.event),
        location_method=wp.location_method.value,
        raw_input=wp.raw_input,
        latitude=wp.latitude,
        longitude=wp.longitude,
        altitude_ft=wp.altitude_ft,
        fuel_remaining_hours=wp.fuel_remaining_hours,
        fuel_remaining_minutes=wp.fuel_remaining_minutes,
        comments=wp.comments,
    )


def _to_sortie_out(sortie: Sortie) -> SortieOut:
    return SortieOut(
        id=sortie.id,
        sortie_number=sortie.sortie_number,
        mission_number=sortie.mission_number,
        pic_name=sortie.pic_name,
        engine_start_utc=_effective_time(sortie.engine_start_event) if sortie.engine_start_event else None,
        engine_stop_utc=_effective_time(sortie.engine_stop_event) if sortie.engine_stop_event else None,
        waypoints=[_to_waypoint_out(w) for w in sortie.waypoints],
    )


async def _get_sortie_or_404(sortie_id: str, session: AsyncSession) -> Sortie:
    result = await session.execute(select(Sortie).where(Sortie.id == sortie_id))
    sortie = result.scalar_one_or_none()
    if sortie is None:
        raise HTTPException(404, "No such sortie")
    return sortie


async def _reload_sortie(sortie_id, session: AsyncSession) -> Sortie:
    """Re-fetch a Sortie already in this session's identity map, forcing a real
    overwrite of its in-memory relationship state. Found live 2026-09-13: a plain
    select() for an already-identity-mapped object does NOT repopulate already-
    loaded relationship collections just because the row changed underneath it
    (even post-commit, even with expire_on_commit's default) -- e.g. add_waypoint's
    post-commit reload was consistently missing the waypoint it had just added,
    because `sortie` was already identity-mapped from this same request's earlier
    _get_sortie_or_404() call. `populate_existing=True` forces the overwrite."""
    result = await session.execute(
        select(Sortie).where(Sortie.id == sortie_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


def _resolve_location(method: WaypointLocationMethod, raw_input: str, lat: float | None, lon: float | None):
    if method == WaypointLocationMethod.mgrs:
        try:
            resolved_lat, resolved_lon = _mgrs.toLatLon(raw_input.replace(" ", "").upper())
        except Exception as exc:
            raise HTTPException(400, f"Could not parse MGRS/USNG grid '{raw_input}': {exc}") from exc
        return resolved_lat, resolved_lon
    # latlon and named_point both require the caller to have already resolved/entered
    # coordinates -- named_point has no gazetteer yet (REQUIREMENTS.md 3.3
    # 2026-09-13 decision: deferred), so the user enters them manually alongside the
    # place name.
    if lat is None or lon is None:
        raise HTTPException(422, f"latitude/longitude are required for location_method={method.value!r}")
    return lat, lon


@router.post("/api/aircraft/{tail_number}/sorties", response_model=SortieOut)
async def create_sortie(tail_number: str, body: SortieCreateIn, session: AsyncSession = Depends(get_session)):
    aircraft = await _get_aircraft_or_404(tail_number, session)
    start_event = TrackingEvent(
        aircraft_id=aircraft.id,
        event_type=EventType.engine_start,
        event_time_utc=body.engine_start_utc or datetime.now(timezone.utc),
        detected_by=DetectionMethod.manual,
        raw={"logged_by": body.logged_by},
    )
    session.add(start_event)
    await session.flush()  # need start_event.id before referencing it

    sortie = Sortie(
        aircraft_id=aircraft.id,
        sortie_number=body.sortie_number,
        mission_number=body.mission_number,
        pic_name=body.pic_name,
        engine_start_event_id=start_event.id,
    )
    session.add(sortie)
    await session.commit()
    sortie = await _reload_sortie(sortie.id, session)
    return _to_sortie_out(sortie)


@router.get("/api/aircraft/{tail_number}/sorties", response_model=list[SortieOut])
async def list_sorties(tail_number: str, session: AsyncSession = Depends(get_session)):
    aircraft = await _get_aircraft_or_404(tail_number, session)
    result = await session.execute(
        select(Sortie).where(Sortie.aircraft_id == aircraft.id).order_by(Sortie.created_at.desc()).limit(20)
    )
    sorties = result.scalars().unique().all()
    return [_to_sortie_out(s) for s in sorties]


@router.get("/api/aircraft/{tail_number}/sorties/current", response_model=SortieOut | None)
async def current_sortie(tail_number: str, session: AsyncSession = Depends(get_session)):
    """The aircraft's most recent sortie, if it's still in progress (no engine stop
    logged yet) -- drives the frontend's choice between "Start Sortie" and the
    in-progress sortie panel. Returns null (not 404) when there's no open sortie,
    since "nothing in progress" is a normal, expected state here."""
    aircraft = await _get_aircraft_or_404(tail_number, session)
    result = await session.execute(
        select(Sortie).where(Sortie.aircraft_id == aircraft.id).order_by(Sortie.created_at.desc()).limit(1)
    )
    sortie = result.scalar_one_or_none()
    if sortie is None or sortie.engine_stop_event_id is not None:
        return None
    return _to_sortie_out(sortie)


@router.post("/api/sorties/{sortie_id}/stop", response_model=SortieOut)
async def stop_sortie(sortie_id: str, body: SortieStopIn, session: AsyncSession = Depends(get_session)):
    sortie = await _get_sortie_or_404(sortie_id, session)
    stop_event = TrackingEvent(
        aircraft_id=sortie.aircraft_id,
        event_type=EventType.engine_stop,
        event_time_utc=body.engine_stop_utc or datetime.now(timezone.utc),
        detected_by=DetectionMethod.manual,
        raw={"logged_by": body.logged_by},
    )
    session.add(stop_event)
    await session.flush()
    sortie.engine_stop_event_id = stop_event.id
    await session.commit()
    sortie = await _reload_sortie(sortie.id, session)
    return _to_sortie_out(sortie)


@router.patch("/api/sorties/{sortie_id}", response_model=SortieOut)
async def patch_sortie(sortie_id: str, body: SortiePatchIn, session: AsyncSession = Depends(get_session)):
    sortie = await _get_sortie_or_404(sortie_id, session)
    if body.sortie_number is not None:
        sortie.sortie_number = body.sortie_number
    if body.mission_number is not None:
        sortie.mission_number = body.mission_number
    if body.pic_name is not None:
        sortie.pic_name = body.pic_name
    await session.commit()
    sortie = await _reload_sortie(sortie.id, session)
    return _to_sortie_out(sortie)


@router.post("/api/sorties/{sortie_id}/waypoints", response_model=SortieOut)
async def add_waypoint(sortie_id: str, body: WaypointCreateIn, session: AsyncSession = Depends(get_session)):
    sortie = await _get_sortie_or_404(sortie_id, session)
    try:
        waypoint_type = EventType(body.waypoint_type)
    except ValueError:
        raise HTTPException(422, f"Unknown waypoint_type '{body.waypoint_type}'") from None
    if waypoint_type not in WAYPOINT_TYPES:
        raise HTTPException(422, f"waypoint_type must be one of {[t.value for t in WAYPOINT_TYPES]}")
    try:
        location_method = WaypointLocationMethod(body.location_method)
    except ValueError:
        raise HTTPException(422, f"Unknown location_method '{body.location_method}'") from None

    latitude, longitude = _resolve_location(location_method, body.raw_input, body.latitude, body.longitude)

    event = TrackingEvent(
        aircraft_id=sortie.aircraft_id,
        event_type=waypoint_type,
        event_time_utc=body.time_utc or datetime.now(timezone.utc),
        detected_by=DetectionMethod.manual,
        raw={"logged_by": body.logged_by},
    )
    session.add(event)
    await session.flush()

    session.add(
        SortieWaypoint(
            sortie_id=sortie.id,
            event_id=event.id,
            location_method=location_method,
            raw_input=body.raw_input,
            latitude=latitude,
            longitude=longitude,
            altitude_ft=body.altitude_ft,
            fuel_remaining_hours=body.fuel_remaining_hours,
            fuel_remaining_minutes=body.fuel_remaining_minutes,
            comments=body.comments,
        )
    )
    await session.commit()
    sortie = await _reload_sortie(sortie.id, session)
    return _to_sortie_out(sortie)
