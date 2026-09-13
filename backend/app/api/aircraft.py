import csv
import io

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Aircraft, AircraftCallsign
from app.schemas import AircraftOut, RosterUploadOut, TrackedListIn

router = APIRouter(prefix="/api/aircraft", tags=["aircraft"])

# Most, not all, CAP tails end this way -- soft check only, matches
# scripts/import_callsign_tails.py.
COMMON_CAP_SUFFIXES = ("CP", "CA", "CV")


@router.get("", response_model=list[AircraftOut])
async def list_aircraft(session: AsyncSession = Depends(get_session)):
    """All known aircraft (from the callsign/tail import). The side panel
    (REQUIREMENTS.md 3.1) combines this with live positions client-side / via /ws."""
    result = await session.execute(select(Aircraft).where(Aircraft.active.is_(True)))
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
async def upload_roster(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    """Mission-specific callsign/tail roster upload (REQUIREMENTS.md 3.1, added
    2026-09-13) -- same CSV shape and upsert logic as scripts/import_callsign_tails.py
    (tail_number, callsign, callsign_prefix columns), but via the web UI instead of an
    SSH+CLI script run, and deliberately additive-only: unlike that script, this never
    deactivates aircraft missing from the upload. Per Gerry: "for now add/supplement...
    we will eventually get a roster" -- a real mission-scoped roster concept (separate
    from the standing monthly wing-wide list) is a future enhancement, not this."""
    content = (await file.read()).decode("utf-8-sig")  # -sig: tolerate an Excel-exported BOM
    rows = list(csv.DictReader(io.StringIO(content)))

    unusual_suffix_tails: list[str] = []
    existing = {a.tail_number: a for a in (await session.execute(select(Aircraft))).scalars()}
    existing_callsigns = {c.callsign: c for c in (await session.execute(select(AircraftCallsign))).scalars()}

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
        callsign = (row.get("callsign") or "").strip().upper() or None
        aircraft.callsign = callsign
        aircraft.callsign_prefix = (row.get("callsign_prefix") or "").strip().upper() or None
        aircraft.active = True

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
