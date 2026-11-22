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

from datetime import datetime, timedelta, timezone
from uuid import UUID

import mgrs
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.events import _effective_time, _get_aircraft_or_404, _to_event_out
from app.auth import optional_mission_member, require_time_logging_permission
from app.db import get_session
from geoalchemy2.shape import to_shape

from app.models import (
    Aircraft,
    DetectionMethod,
    EventType,
    Mission,
    Position,
    Sortie,
    SortieWaypoint,
    TrackingEvent,
    WaypointLocationMethod,
)
from app.schemas import (
    PositionOut,
    SortieCreateIn,
    SortieOut,
    SortiePatchIn,
    SortieStartIn,
    SortieStopIn,
    WaypointCreateIn,
    WaypointOut,
)

router = APIRouter(tags=["sorties"])
_mgrs = mgrs.MGRS()

# find/pirep/rtb added 2026-10-26 for the comms quick-select menu
# (REQUIREMENTS.md 3.3) -- same repeatable-waypoint mechanism as
# in_grid/out_grid/ops_check, just three more EventType values.
WAYPOINT_TYPES = {
    EventType.in_grid,
    EventType.out_grid,
    EventType.ops_check,
    EventType.find,
    EventType.pirep,
    EventType.rtb,
}


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
        ete_hours=wp.ete_hours,
        ete_minutes=wp.ete_minutes,
        comments=wp.comments,
        pirep_report=wp.pirep_report,
    )


def _compute_eta_utc(sortie: Sortie, engine_start_utc: datetime | None) -> datetime | None:
    """ATD + ETE, per the CAWG AOBD course's own worked example (ATD 10:20 + ETE
    3.2hr -> ETA 13:32) -- an AOBD never types an ETA in directly. An explicit
    `eta_override_utc` (a crew calling in an extension) always wins when set.
    Shared with app/api/ops_board.py, which needs the identical arithmetic."""
    if sortie.eta_override_utc is not None:
        return sortie.eta_override_utc
    if engine_start_utc is not None and sortie.ete_hours is not None:
        return engine_start_utc + timedelta(hours=sortie.ete_hours)
    return None


def _to_sortie_out(sortie: Sortie) -> SortieOut:
    engine_start_utc = _effective_time(sortie.engine_start_event) if sortie.engine_start_event else None
    return SortieOut(
        id=sortie.id,
        sortie_number=sortie.sortie_number,
        mission_number=sortie.mission_number,
        pic_name=sortie.pic_name,
        pic_cap_id=sortie.pic_cap_id,
        engine_start_utc=engine_start_utc,
        engine_stop_utc=_effective_time(sortie.engine_stop_event) if sortie.engine_stop_event else None,
        takeoff_utc=_effective_time(sortie.takeoff_event) if sortie.takeoff_event else None,
        landing_utc=_effective_time(sortie.landing_event) if sortie.landing_event else None,
        waypoints=[_to_waypoint_out(w) for w in sortie.waypoints],
        events=[_to_event_out(e) for e in sortie.events],
        ete_hours=sortie.ete_hours,
        eta_override_utc=sortie.eta_override_utc,
        eta_utc=_compute_eta_utc(sortie, engine_start_utc),
        overdue_threshold_minutes=sortie.overdue_threshold_minutes,
        assigned_area=sortie.assigned_area,
        status=sortie.effective_status(),
        wmirs_sortie_id=sortie.wmirs_sortie_id,
        planned_date=sortie.planned_date,
        departure_raw=sortie.departure_raw,
        destination_raw=sortie.destination_raw,
        cancelled_at=sortie.cancelled_at,
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
async def create_sortie(
    tail_number: str,
    body: SortieCreateIn,
    request: Request,
    mission_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """mission_id is OPTIONAL (transitional -- see app/auth.py's
    optional_mission_member) so this ships ahead of every caller always
    sending it. When given, the new sortie is tagged with it, and
    mission_number defaults from Mission.mission_number when the caller
    didn't type one in -- still independently editable afterward, same as
    before.

    Gated by require_time_logging_permission (COMMS/AOBD/IC/Ops only, added
    2026-10-26), not plain optional_mission_member -- this route logs the
    sortie's engine_start time, same as the comms quick-select menu."""
    await require_time_logging_permission(mission_id, request, session)
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

    mission_number = body.mission_number
    if not mission_number and mission_id is not None:
        mission = await session.get(Mission, mission_id)
        mission_number = mission.mission_number if mission else None

    sortie = Sortie(
        aircraft_id=aircraft.id,
        mission_id=mission_id,
        sortie_number=body.sortie_number,
        mission_number=mission_number,
        pic_name=body.pic_name,
        pic_cap_id=body.pic_cap_id,
        engine_start_event_id=start_event.id,
        ete_hours=body.ete_hours,
        assigned_area=body.assigned_area,
        overdue_threshold_minutes=body.overdue_threshold_minutes,
    )
    session.add(sortie)
    await session.flush()  # need sortie.id before linking start_event back to it
    start_event.sortie_id = sortie.id
    await session.commit()
    sortie = await _reload_sortie(sortie.id, session)
    return _to_sortie_out(sortie)


@router.get("/api/aircraft/{tail_number}/sorties", response_model=list[SortieOut])
async def list_sorties(
    tail_number: str,
    request: Request,
    mission_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Filtered to this mission's own sorties for this tail when mission_id is
    given; every sortie ever logged for this tail (across missions) when
    omitted -- the latter is the pre-mission-scoping behavior, kept as a
    transitional fallback (see app/auth.py's optional_mission_member)."""
    await optional_mission_member(mission_id, request, session)
    aircraft = await _get_aircraft_or_404(tail_number, session)
    query = select(Sortie).where(Sortie.aircraft_id == aircraft.id)
    if mission_id is not None:
        query = query.where(Sortie.mission_id == mission_id)
    result = await session.execute(query.order_by(Sortie.created_at.desc()).limit(20))
    sorties = result.scalars().unique().all()
    return [_to_sortie_out(s) for s in sorties]


@router.get("/api/aircraft/{tail_number}/sorties/current", response_model=SortieOut | None)
async def current_sortie(
    tail_number: str,
    request: Request,
    mission_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """The aircraft's most recent sortie, if it's still in progress (no engine stop
    logged yet) -- drives the frontend's choice between "Start Sortie" and the
    in-progress sortie panel. Returns null (not 404) when there's no open sortie,
    since "nothing in progress" is a normal, expected state here.

    When mission_id is given, only considers THAT mission's sorties for this
    tail -- a tail flying for a different mission shouldn't look "in progress"
    to a caller who isn't on it. Omitted, this is the original cross-mission
    behavior (transitional fallback)."""
    await optional_mission_member(mission_id, request, session)
    aircraft = await _get_aircraft_or_404(tail_number, session)
    query = select(Sortie).where(Sortie.aircraft_id == aircraft.id)
    if mission_id is not None:
        query = query.where(Sortie.mission_id == mission_id)
    result = await session.execute(query.order_by(Sortie.created_at.desc()).limit(1))
    sortie = result.scalar_one_or_none()
    if sortie is None or sortie.engine_stop_event_id is not None:
        return None
    return _to_sortie_out(sortie)


TRACK_WINDOW = timedelta(hours=8)  # per Gerry, 2026-10-24 -- flat window, not "since takeoff"


@router.get("/api/aircraft/{tail_number}/track", response_model=list[PositionOut])
async def aircraft_track(
    tail_number: str,
    request: Request,
    mission_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Selectable flight track/trail for the map (REQUIREMENTS.md 3.1, added
    2026-10-24 per Gerry) -- every Position row for this tail in the last
    TRACK_WINDOW, oldest first, so the frontend can draw it as a single
    polyline without re-sorting. Not mission-scoped data in any real sense
    (a Position isn't tied to a Mission) -- mission_id here only gates access
    the same transitional way every other per-tail route does (see
    app/auth.py's optional_mission_member), not filtering content."""
    await optional_mission_member(mission_id, request, session)
    aircraft = await _get_aircraft_or_404(tail_number, session)
    result = await session.execute(
        select(Position)
        .where(Position.aircraft_id == aircraft.id)
        .where(Position.ts_utc > datetime.now(timezone.utc) - TRACK_WINDOW)
        .order_by(Position.ts_utc.asc())
    )
    positions = result.scalars().all()
    return [
        PositionOut(
            tail_number=aircraft.tail_number,
            callsign=aircraft.callsign,
            ts_utc=p.ts_utc,
            latitude=to_shape(p.location).y,
            longitude=to_shape(p.location).x,
            altitude_ft=p.altitude_ft,
            ground_speed_kt=p.ground_speed_kt,
            heading_deg=p.heading_deg,
        )
        for p in positions
    ]


@router.post("/api/sorties/{sortie_id}/start", response_model=SortieOut)
async def start_sortie(
    sortie_id: str, body: SortieStartIn, request: Request, session: AsyncSession = Depends(get_session)
):
    """Promotes an existing `planned` sortie (created by the WMIRS importer,
    app/api/wmirs_import.py) to `active` by logging its engine_start event on
    THIS row -- not create_sortie(), which always makes a brand-new row.
    create_sortie() stays the walk-up/no-WMIRS-plan path, unchanged. This is
    what the "first call" radio check-in resolves to once Callsign/Sortie ID/
    Pilot/Destination/ETE are confirmed against the planned row (REQUIREMENTS.md
    3.3, added 2026-10-19) -- and, since 2026-10-26, what the comms quick-select
    menu's Engine Start button actually calls for an already-existing (e.g.
    WMIRS-planned) sortie, rather than the generic events route.

    Gated by require_time_logging_permission using the SORTIE's own mission_id
    (added 2026-10-26), not a client-supplied one -- can't be spoofed by
    passing a different mission_id than the sortie actually belongs to."""
    sortie = await _get_sortie_or_404(sortie_id, session)
    await require_time_logging_permission(sortie.mission_id, request, session)
    if sortie.effective_status() != "planned":
        raise HTTPException(409, f"Sortie is {sortie.effective_status()}, not planned -- cannot start")
    start_event = TrackingEvent(
        aircraft_id=sortie.aircraft_id,
        sortie_id=sortie.id,
        event_type=EventType.engine_start,
        event_time_utc=body.engine_start_utc or datetime.now(timezone.utc),
        detected_by=DetectionMethod.manual,
        raw={"logged_by": body.logged_by, "comments": body.comments} if body.comments else {"logged_by": body.logged_by},
    )
    session.add(start_event)
    await session.flush()
    sortie.engine_start_event_id = start_event.id
    await session.commit()
    sortie = await _reload_sortie(sortie.id, session)
    return _to_sortie_out(sortie)


@router.post("/api/sorties/{sortie_id}/cancel", response_model=SortieOut)
async def cancel_sortie(sortie_id: str, session: AsyncSession = Depends(get_session)):
    """A planned sortie that "for some reason doesn't make" (Gerry) -- e.g. a
    WMIRS-planned leg that gets scrubbed before it ever flies. Only legal while
    still `planned`: a sortie that already flew needs stop_sortie, not this, and
    one that's already cancelled is a no-op (idempotent, same convention as
    Mission.close/DiscoveredCallsign.dismiss)."""
    sortie = await _get_sortie_or_404(sortie_id, session)
    status = sortie.effective_status()
    if status == "cancelled":
        return _to_sortie_out(sortie)
    if status != "planned":
        raise HTTPException(409, f"Sortie is {status}, not planned -- cannot cancel")
    sortie.cancelled_at = datetime.now(timezone.utc)
    await session.commit()
    sortie = await _reload_sortie(sortie.id, session)
    return _to_sortie_out(sortie)


@router.post("/api/sorties/{sortie_id}/stop", response_model=SortieOut)
async def stop_sortie(
    sortie_id: str, body: SortieStopIn, request: Request, session: AsyncSession = Depends(get_session)
):
    """Gated by require_time_logging_permission (added 2026-10-26) using the
    sortie's own mission_id -- also what the comms quick-select menu's Engine
    Stop button calls, rather than the generic events route."""
    sortie = await _get_sortie_or_404(sortie_id, session)
    await require_time_logging_permission(sortie.mission_id, request, session)
    stop_event = TrackingEvent(
        aircraft_id=sortie.aircraft_id,
        sortie_id=sortie.id,
        event_type=EventType.engine_stop,
        event_time_utc=body.engine_stop_utc or datetime.now(timezone.utc),
        detected_by=DetectionMethod.manual,
        raw={"logged_by": body.logged_by, "comments": body.comments} if body.comments else {"logged_by": body.logged_by},
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
    if body.tail_number is not None:
        # Aircraft reassignment (REQUIREMENTS.md 3.3, added 2026-10-19) -- only
        # while still planned. Covers both the "AOBD swaps the planned aircraft
        # ahead of time" and "mismatch caught live at first-call" cases Gerry
        # described (RPP precedent): same endpoint, two different UI triggers.
        # Never allowed once engine_start is logged -- that's the aircraft of
        # record for this sortie from then on.
        if sortie.effective_status() != "planned":
            raise HTTPException(409, f"Sortie is {sortie.effective_status()}, not planned -- cannot reassign aircraft")
        tail = body.tail_number.strip().upper()
        aircraft = (await session.execute(select(Aircraft).where(Aircraft.tail_number == tail))).scalar_one_or_none()
        if aircraft is None:
            raise HTTPException(404, f"No such aircraft '{tail}'")
        sortie.aircraft_id = aircraft.id
    if body.sortie_number is not None:
        sortie.sortie_number = body.sortie_number
    if body.mission_number is not None:
        sortie.mission_number = body.mission_number
    if body.pic_name is not None:
        sortie.pic_name = body.pic_name
    if body.pic_cap_id is not None:
        sortie.pic_cap_id = body.pic_cap_id
    if body.ete_hours is not None:
        sortie.ete_hours = body.ete_hours
    if body.assigned_area is not None:
        sortie.assigned_area = body.assigned_area
    if body.overdue_threshold_minutes is not None:
        sortie.overdue_threshold_minutes = body.overdue_threshold_minutes
    if body.eta_override_utc is not None:
        sortie.eta_override_utc = body.eta_override_utc
    await session.commit()
    sortie = await _reload_sortie(sortie.id, session)
    return _to_sortie_out(sortie)


@router.post("/api/sorties/{sortie_id}/waypoints", response_model=SortieOut)
async def add_waypoint(
    sortie_id: str, body: WaypointCreateIn, request: Request, session: AsyncSession = Depends(get_session)
):
    """Gated by require_time_logging_permission (added 2026-10-26) using the
    sortie's own mission_id -- every comms quick-select waypoint type
    (in/out-grid, ops check, find, pirep, rtb) goes through this one route."""
    sortie = await _get_sortie_or_404(sortie_id, session)
    await require_time_logging_permission(sortie.mission_id, request, session)
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
        sortie_id=sortie.id,
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
            ete_hours=body.ete_hours,
            ete_minutes=body.ete_minutes,
            comments=body.comments,
            pirep_report=body.pirep_report,
        )
    )
    await session.commit()
    sortie = await _reload_sortie(sortie.id, session)
    return _to_sortie_out(sortie)
