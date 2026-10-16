import csv
import io
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import optional_mission_member
from app.db import get_session
from app.models import Aircraft, AircraftCallsign, MissionAircraft
from app.schemas import AircraftOut, RosterUploadOut, TrackedListIn

router = APIRouter(prefix="/api/aircraft", tags=["aircraft"])

# Most, not all, CAP tails end this way -- soft check only, matches
# scripts/import_callsign_tails.py.
COMMON_CAP_SUFFIXES = ("CP", "CA", "CV")


@router.get("", response_model=list[AircraftOut])
async def list_aircraft(
    request: Request, mission_id: UUID | None = Query(None), session: AsyncSession = Depends(get_session)
):
    """This mission's assigned aircraft (via MissionAircraft) when mission_id
    is given; the full global active roster when it's omitted -- the latter is
    a transitional fallback for the current, pre-login frontend (added
    2026-10-16, see app/auth.py's optional_mission_member) and should go away
    once the frontend always sends mission_id. The side panel (REQUIREMENTS.md
    3.1) combines this with live positions client-side / via /ws."""
    await optional_mission_member(mission_id, request, session)
    if mission_id is None:
        result = await session.execute(select(Aircraft).where(Aircraft.active.is_(True)))
        return result.scalars().all()
    result = await session.execute(
        select(Aircraft)
        .join(MissionAircraft, MissionAircraft.aircraft_id == Aircraft.id)
        .where(MissionAircraft.mission_id == mission_id, Aircraft.active.is_(True))
    )
    return result.scalars().all()


@router.post("/resolve", response_model=list[AircraftOut])
async def resolve_tracked_list(body: TrackedListIn, session: AsyncSession = Depends(get_session)):
    """Resolve a user-entered tracked list (tail numbers and/or callsigns,
    REQUIREMENTS.md 3.1) to known Aircraft rows. Unmatched idents are silently
    dropped -- the frontend is responsible for warning the user about entries with no
    match, since that's as likely to be a typo as a genuinely unknown aircraft.

    Matches against tail_number, Aircraft.callsign (the routine/primary callsign), AND
    AircraftCallsign (any other callsign the tail answers to, e.g. a PARD callsign --
    see the note on Aircraft.callsign in models.py for why a tail can have more than
    one)."""
    idents = {i.strip().upper() for i in body.idents if i.strip()}
    if not idents:
        return []
    result = await session.execute(
        select(Aircraft)
        .outerjoin(AircraftCallsign, AircraftCallsign.aircraft_id == Aircraft.id)
        .where(
            (Aircraft.tail_number.in_(idents))
            | (Aircraft.callsign.in_(idents))
            | (AircraftCallsign.callsign.in_(idents))
        )
        .distinct()
    )
    return result.scalars().all()


@router.post("/roster", response_model=RosterUploadOut)
async def upload_roster(
    request: Request,
    mission_id: UUID | None = Query(None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Mission-specific callsign/tail roster upload (REQUIREMENTS.md 3.1, added
    2026-09-13) -- same CSV shape and upsert logic as scripts/import_callsign_tails.py
    (tail_number, callsign, callsign_prefix columns), but via the web UI instead of an
    SSH+CLI script run, and deliberately additive-only: unlike that script, this never
    deactivates aircraft missing from the upload. Per Gerry: "for now add/supplement...
    we will eventually get a roster" -- that real mission-scoped roster concept (separate
    from the standing monthly wing-wide list) is `mission_id`/MissionAircraft, added
    2026-10-16: when given, every uploaded row is also linked into that mission's
    roster, not just the global Aircraft table. Omitted, this is the original
    global-only behavior (transitional fallback, see app/auth.py's
    optional_mission_member).

    Also accepts an optional `pilot` or `instructor` column (either name -- per Gerry,
    2026-09-13, "build in the ability to read a column header with pilot (or
    instructor)"), stored as Aircraft.default_pic_name -- pre-fills the PIC field on
    that tail's Start Sortie form (app/api/sorties.py), not itself the record of who
    actually flew. Column matching is case-insensitive (found live: the original
    version required the CSV header to be exactly lowercase `tail_number` etc, which
    would have silently treated a differently-cased header as absent) and blank/absent
    values leave any existing default untouched rather than clearing it, matching this
    endpoint's overall additive-only philosophy."""
    await optional_mission_member(mission_id, request, session)
    content = (await file.read()).decode("utf-8-sig")  # -sig: tolerate an Excel-exported BOM
    rows = [{(k or "").strip().lower(): v for k, v in row.items()} for row in csv.DictReader(io.StringIO(content))]

    unusual_suffix_tails: list[str] = []
    existing = {a.tail_number: a for a in (await session.execute(select(Aircraft))).scalars()}
    existing_callsigns = {c.callsign: c for c in (await session.execute(select(AircraftCallsign))).scalars()}
    existing_links = (
        {m.aircraft_id for m in (await session.execute(select(MissionAircraft).where(MissionAircraft.mission_id == mission_id))).scalars()}
        if mission_id is not None
        else set()
    )

    for row in rows:
        tail = (row.get("tail_number") or "").strip().upper()
        if not tail:
            continue
        if not tail.endswith(COMMON_CAP_SUFFIXES):
            unusual_suffix_tails.append(tail)
        aircraft = existing.get(tail)
        if aircraft is None:
            aircraft = Aircraft(tail_number=tail)
            session.add(aircraft)
            existing[tail] = aircraft
            await session.flush()  # need aircraft.id before linking it into the mission below
        if mission_id is not None and aircraft.id not in existing_links:
            session.add(MissionAircraft(mission_id=mission_id, aircraft_id=aircraft.id))
            existing_links.add(aircraft.id)
        callsign = (row.get("callsign") or "").strip().upper() or None
        aircraft.callsign = callsign
        aircraft.callsign_prefix = (row.get("callsign_prefix") or "").strip().upper() or None
        aircraft.active = True

        pic_name = (row.get("pilot") or row.get("instructor") or "").strip() or None
        if pic_name:
            aircraft.default_pic_name = pic_name

        if callsign:
            cs_row = existing_callsigns.get(callsign)
            if cs_row is None:
                cs_row = AircraftCallsign(callsign=callsign)
                session.add(cs_row)
                existing_callsigns[callsign] = cs_row
            cs_row.aircraft = aircraft
            cs_row.label = "CAP"

    await session.commit()
    return RosterUploadOut(added_or_updated=len(rows), unusual_suffix_tails=sorted(set(unusual_suffix_tails)))
