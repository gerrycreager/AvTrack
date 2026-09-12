"""Sortie/event view + edit endpoints -- backs the click-an-aircraft panel
(REQUIREMENTS.md 3.3) and the "NOW" button. No automatic detection exists yet
(app/ingestion/heuristics.py is still a stub), so today every event here is
manually logged; the schema doesn't change once auto-detection lands, only
`detected_by` starts showing "auto" rows too.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Aircraft, DetectionMethod, EventEdit, EventType, TrackingEvent
from app.schemas import EventCreateIn, EventEditIn, EventEditOut, EventOut

router = APIRouter(tags=["events"])


async def _get_aircraft_or_404(tail_number: str, session: AsyncSession) -> Aircraft:
    result = await session.execute(select(Aircraft).where(Aircraft.tail_number == tail_number.upper()))
    aircraft = result.scalar_one_or_none()
    if aircraft is None:
        raise HTTPException(404, f"No aircraft with tail number {tail_number.upper()}")
    return aircraft


def _effective_time(event: TrackingEvent) -> datetime:
    time_edits = [e for e in event.edits if e.field_name == "event_time_utc"]
    if not time_edits:
        return event.event_time_utc
    latest = max(time_edits, key=lambda e: e.edited_at)
    return datetime.fromisoformat(latest.new_value)


def _to_event_out(event: TrackingEvent) -> EventOut:
    return EventOut(
        id=event.id,
        event_type=event.event_type.value,
        event_time_utc=event.event_time_utc,
        effective_time_utc=_effective_time(event),
        detected_by=event.detected_by.value,
        confidence=event.confidence,
        edits=[EventEditOut.model_validate(e) for e in event.edits],
    )


@router.get("/api/aircraft/{tail_number}/events", response_model=list[EventOut])
async def list_events(tail_number: str, session: AsyncSession = Depends(get_session)):
    aircraft = await _get_aircraft_or_404(tail_number, session)
    result = await session.execute(
        select(TrackingEvent)
        .where(TrackingEvent.aircraft_id == aircraft.id)
        .order_by(TrackingEvent.event_time_utc.desc())
        .limit(50)
    )
    events = result.scalars().unique().all()
    return [_to_event_out(e) for e in events]


@router.post("/api/aircraft/{tail_number}/events", response_model=EventOut)
async def create_event(tail_number: str, body: EventCreateIn, session: AsyncSession = Depends(get_session)):
    aircraft = await _get_aircraft_or_404(tail_number, session)
    try:
        event_type = EventType(body.event_type)
    except ValueError:
        raise HTTPException(422, f"Unknown event_type '{body.event_type}'") from None

    event = TrackingEvent(
        aircraft_id=aircraft.id,
        event_type=event_type,
        event_time_utc=body.event_time_utc or datetime.now(timezone.utc),
        detected_by=DetectionMethod.manual,
        raw={"logged_by": body.logged_by},
    )
    session.add(event)
    await session.commit()
    await session.refresh(event, attribute_names=["edits"])
    return _to_event_out(event)


@router.post("/api/events/{event_id}/edits", response_model=EventOut)
async def edit_event(event_id: str, body: EventEditIn, session: AsyncSession = Depends(get_session)):
    """Adds a correction on top of an existing event -- never mutates the original
    row (REQUIREMENTS.md 3.3/4: append-only tracking log, edits layered on top)."""
    result = await session.execute(select(TrackingEvent).where(TrackingEvent.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(404, "No such event")

    old_value = None
    if body.field_name == "event_time_utc":
        old_value = _effective_time(event).isoformat()
        # Validate it parses as a timestamp before persisting the correction.
        try:
            datetime.fromisoformat(body.new_value)
        except ValueError:
            raise HTTPException(422, "new_value must be an ISO-8601 UTC timestamp") from None

    session.add(
        EventEdit(
            event_id=event.id,
            field_name=body.field_name,
            old_value=old_value,
            new_value=body.new_value,
            edited_by=body.edited_by,
            note=body.note,
        )
    )
    await session.commit()
    await session.refresh(event, attribute_names=["edits"])
    return _to_event_out(event)
