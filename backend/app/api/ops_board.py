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
number -- confirmed by direct regulation research 2026-10-01. This is exactly why
`overdue_threshold_minutes` is a per-sortie override (falling back to
Settings.default_overdue_threshold_minutes) rather than a hardcoded constant.

**Two independent alert conditions, added 2026-10-14 per Gerry** -- replacing the
original single `overdue` flag's visual weight (it's still computed and shown, but
on its own no longer triggers strong alerting):
- **`no_contact_warning`** (soft, "orange halo, slow flashing"): purely "haven't
  heard from this aircraft in a while" -- independent of ETA entirely, fires at
  `no_contact_warning_minutes + no_contact_grace_minutes` (30+10=40 by default)
  minutes since Last Ops Normal.
- **`missing`** (hard, "flashing red"): the CAPR 70-10 "missing aircraft" case --
  overdue past ETA by the threshold AND no contact for that same threshold
  (default 60/60, both from the same `overdue_threshold_minutes`, not two separate
  numbers -- Gerry: "overdue... by an hour AND has not been communicated with for
  an hour"). Plain lateness without a contact gap, or a contact gap without also
  being past ETA, is each still visible in the data but deliberately NOT this
  alert -- Gerry: "that's a different issue."
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.events import _effective_time
from app.api.sorties import _compute_eta_utc
from app.auth import optional_mission_member
from app.config import settings
from app.db import get_session
from app.models import Aircraft, Position, Sortie
from app.schemas import OpsBoardOut, OpsBoardRow

router = APIRouter(tags=["ops-board"])


@router.get("/api/ops-board", response_model=OpsBoardOut)
async def get_ops_board(
    request: Request,
    hours: int = Query(24, description="Include sorties started within this many hours"),
    mission_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Board rows for sorties within the time window. Filtered to one
    mission's sorties when mission_id is given (the actual point of this
    scoping -- so one mission's IC/AOBD doesn't see every other mission's
    board rows); every sortie in the window regardless of mission when
    omitted (transitional fallback, see app/auth.py's optional_mission_member)."""
    await optional_mission_member(mission_id, request, session)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    query = (
        select(Sortie, Aircraft)
        .join(Aircraft, Aircraft.id == Sortie.aircraft_id)
        .where(Sortie.created_at >= cutoff)
    )
    if mission_id is not None:
        query = query.where(Sortie.mission_id == mission_id)
    result = await session.execute(query.order_by(Sortie.created_at.desc()))
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
        still_open = ata_utc is None

        overdue = False
        overdue_minutes = None
        if still_open and eta_utc is not None:
            overdue_since = eta_utc + timedelta(minutes=threshold_minutes)
            if now > overdue_since:
                overdue = True
                overdue_minutes = round((now - overdue_since).total_seconds() / 60)

        last_contact_minutes = None
        no_contact_warning = False
        if still_open and last_ops_normal_utc is not None:
            last_contact_minutes = round((now - last_ops_normal_utc).total_seconds() / 60)
            no_contact_trigger = settings.no_contact_warning_minutes + settings.no_contact_grace_minutes
            no_contact_warning = last_contact_minutes >= no_contact_trigger

        # "Missing" (CAPR 70-10's formal trigger): both halves true at once, not
        # either alone -- see module docstring.
        missing = overdue and last_contact_minutes is not None and last_contact_minutes >= threshold_minutes

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
                last_contact_minutes=last_contact_minutes,
                no_contact_warning=no_contact_warning,
                missing=missing,
                state=state,
            )
        )

    return OpsBoardOut(generated_at=now, rows=rows)
