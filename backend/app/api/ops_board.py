"""Air Ops Status Board (REQUIREMENTS.md 3.3, added 2026-10-01) -- a live,
multi-aircraft digitization of the AOBD's wall board, grounded in the CAWG AOBD
course (/home/gerry/CAP/Training/AOBD/AOBD_course.pdf, pp.9-10): Sortie #, Tail #,
AC Type, Assigned Area, ATD/ETA/ATA, Last Ops Normal, overdue flagging.

ATD/ATA are exactly the existing engine_start/engine_stop events on Sortie -- no
new "departure/arrival" concept needed. ETA is never stored directly; it's computed
(ATD + ETE, or an AOBD override) via `_compute_eta_utc`, shared with
app/api/sorties.py's SortieOut so both endpoints agree. "Last Ops Normal" is also
not stored -- it's the latest of every TrackingEvent already attributed to the
sortie (sortie.events already includes every type: engine_start/stop, takeoff,
landing, touch_and_go, in_grid/out_grid/ops_check -- confirmed live, every event
gets sortie_id set when a sortie is open) and the aircraft's latest live Position,
whichever is more recent.

Per CAPR 100-3 S3.3.2, the Ops Normal check-in interval is set locally by the Net
Control Station / Communications Unit Leader for the mission, not a fixed CAP-wide
number -- confirmed by direct regulation research 2026-10-01. The CAWG AOBD
course's own ">10 minutes" is a CAWG-local default, not a national standard. This
is exactly why `overdue_threshold_minutes` is a per-sortie override (falling back
to Settings.default_overdue_threshold_minutes) rather than a hardcoded constant.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.events import _effective_time
from app.api.sorties import _compute_eta_utc
from app.config import settings
from app.db import get_session
from app.models import Aircraft, Position, Sortie
from app.schemas import OpsBoardOut, OpsBoardRow

router = APIRouter(tags=["ops-board"])


@router.get("/api/ops-board", response_model=OpsBoardOut)
async def get_ops_board(
    hours: int = Query(24, description="Include sorties started within this many hours"),
    session: AsyncSession = Depends(get_session),
):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    result = await session.execute(
        select(Sortie, Aircraft)
        .join(Aircraft, Aircraft.id == Sortie.aircraft_id)
        .where(Sortie.created_at >= cutoff)
        .order_by(Sortie.created_at.desc())
    )
    sorties_with_aircraft = result.all()

    aircraft_ids = {aircraft.id for _, aircraft in sorties_with_aircraft}
    latest_position_by_aircraft_id: dict = {}
    if aircraft_ids:
        pos_result = await session.execute(
            select(Position)
            .where(Position.aircraft_id.in_(aircraft_ids))
            .distinct(Position.aircraft_id)
            .order_by(Position.aircraft_id, Position.ts_utc.desc())
        )
        latest_position_by_aircraft_id = {p.aircraft_id: p for p in pos_result.scalars()}

    rows = []
    for sortie, aircraft in sorties_with_aircraft:
        atd_utc = _effective_time(sortie.engine_start_event) if sortie.engine_start_event else None
        ata_utc = _effective_time(sortie.engine_stop_event) if sortie.engine_stop_event else None
        eta_utc = _compute_eta_utc(sortie, atd_utc)
        position = latest_position_by_aircraft_id.get(sortie.aircraft_id)

        # "Last Ops Normal" -- latest of every event already attributed to this
        # sortie, and the aircraft's latest live position (only meaningfully newer
        # than the event log while actually airborne and transmitting).
        last_ops_normal_utc = None
        for event in sortie.events:
            t = _effective_time(event)
            if last_ops_normal_utc is None or t > last_ops_normal_utc:
                last_ops_normal_utc = t
        if position is not None and (last_ops_normal_utc is None or position.ts_utc > last_ops_normal_utc):
            last_ops_normal_utc = position.ts_utc

        threshold_minutes = sortie.overdue_threshold_minutes or settings.default_overdue_threshold_minutes
        overdue = False
        overdue_minutes = None
        if ata_utc is None and eta_utc is not None:
            overdue_since = eta_utc + timedelta(minutes=threshold_minutes)
            if now > overdue_since:
                overdue = True
                overdue_minutes = round((now - overdue_since).total_seconds() / 60)

        # State naming matches the sidebar's existing convention exactly
        # (STATE_LABEL in main.js: airborne/ground/"not-reporting") plus "complete",
        # not a second inconsistent naming scheme for the same concept.
        if ata_utc is not None:
            state = "complete"
        elif position is not None and position.altitude_ft is not None and position.altitude_ft > 0:
            state = "airborne"
        elif position is not None:
            state = "ground"
        else:
            state = "not-reporting"

        rows.append(
            OpsBoardRow(
                sortie_id=sortie.id,
                tail_number=aircraft.tail_number,
                callsign=aircraft.callsign,
                aircraft_type=aircraft.aircraft_type,
                sortie_number=sortie.sortie_number,
                mission_number=sortie.mission_number,
                pic_name=sortie.pic_name,
                assigned_area=sortie.assigned_area,
                atd_utc=atd_utc,
                ete_hours=sortie.ete_hours,
                eta_utc=eta_utc,
                ata_utc=ata_utc,
                last_ops_normal_utc=last_ops_normal_utc,
                overdue=overdue,
                overdue_minutes=overdue_minutes,
                state=state,
            )
        )

    return OpsBoardOut(generated_at=now, rows=rows)
