from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Aircraft, AircraftCallsign
from app.schemas import AircraftOut, TrackedListIn

router = APIRouter(prefix="/api/aircraft", tags=["aircraft"])


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
