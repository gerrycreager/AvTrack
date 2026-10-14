"""Unregistered CAP/PARD aircraft detection + approve (REQUIREMENTS.md 3.1,
added 2026-10-14). Per Gerry: "If an unregistered aircraft comes into the Area
of Operations, we need to know. We should be able to also register it with a
relatively simple click and approve process."

Kept as its own resource (not folded into api/aircraft.py) since DiscoveredCallsign
is a distinct candidate-list with its own list/approve/dismiss lifecycle, even
though approve writes into Aircraft -- same reasoning as keeping airfields.py
separate from airspace.py despite both being geo-data.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.integrations.adsb_discovery import haversine_nm
from app.models import Aircraft, AircraftCallsign, DiscoveredCallsign, OperationsArea
from app.schemas import AircraftOut, DiscoveredCallsignOut, OperationsAreaIn, OperationsAreaOut

router = APIRouter(prefix="/api/discovered", tags=["discovered"])


@router.get("", response_model=list[DiscoveredCallsignOut])
async def list_discovered(
    ao_filter: bool = Query(True, description="If false, ignore the AO and return all non-dismissed candidates nationwide"),
    session: AsyncSession = Depends(get_session),
):
    """Active (non-dismissed) discovered CAP/PARD candidates, AO-filtered by
    default. AO membership is computed here at read time, not stored -- see
    DiscoveredCallsign's docstring for why.

    `ao_filter=false` (added 2026-10-15, used by the map's always-on candidate
    markers -- see frontend/src/main.js refreshDiscoveredMarkers()) bypasses the
    AO entirely: per Gerry, "the aircraft should display on-screen when they are
    flying [regardless of our ongoing operation]... they'd not be in an
    operations table." General map situational awareness is unscoped; only the
    Ops Board's actionable alert list (the default, AO-scoped behavior here)
    is operation-specific."""
    rows = (await session.execute(select(DiscoveredCallsign).where(DiscoveredCallsign.dismissed_at.is_(None)))).scalars().all()
    if not ao_filter:
        return rows
    ao = (await session.execute(select(OperationsArea))).scalars().first()
    if ao is None:
        return rows
    return [
        r
        for r in rows
        if r.last_latitude is not None
        and r.last_longitude is not None
        and haversine_nm(r.last_latitude, r.last_longitude, ao.center_latitude, ao.center_longitude) <= ao.radius_nm
    ]


@router.post("/{discovered_id}/approve", response_model=AircraftOut)
async def approve_discovered(discovered_id: UUID, session: AsyncSession = Depends(get_session)):
    """One click, no form: tail_number + callsign only, mirroring api/aircraft.py's
    roster-upload minimal-data pattern. Sets AircraftCallsign.label from
    source_label (CAP or PARD) -- see Aircraft's docstring on why a tail can
    answer to more than one callsign (a PARD tasking is a callsign change on the
    same tail, not a different aircraft)."""
    row = await session.get(DiscoveredCallsign, discovered_id)
    if row is None:
        raise HTTPException(404, "Discovered candidate not found")

    aircraft = (await session.execute(select(Aircraft).where(Aircraft.tail_number == row.tail_number))).scalar_one_or_none()
    if aircraft is None:
        aircraft = Aircraft(tail_number=row.tail_number, callsign=row.callsign, active=True)
        session.add(aircraft)
    else:
        aircraft.active = True
        aircraft.callsign = aircraft.callsign or row.callsign
    await session.flush()

    cs = (await session.execute(select(AircraftCallsign).where(AircraftCallsign.callsign == row.callsign))).scalar_one_or_none()
    if cs is None:
        cs = AircraftCallsign(callsign=row.callsign)
        session.add(cs)
    cs.aircraft_id = aircraft.id
    cs.label = row.source_label

    row.dismissed_at = datetime.now(timezone.utc)  # approved rows also leave the alert list

    await session.commit()
    await session.refresh(aircraft)
    return aircraft


@router.post("/{discovered_id}/dismiss")
async def dismiss_discovered(discovered_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(DiscoveredCallsign, discovered_id)
    if row is None:
        raise HTTPException(404, "Discovered candidate not found")
    row.dismissed_at = datetime.now(timezone.utc)
    await session.commit()
    return {"status": "dismissed"}


@router.get("/ao", response_model=OperationsAreaOut | None)
async def get_ao(session: AsyncSession = Depends(get_session)):
    return (await session.execute(select(OperationsArea))).scalars().first()


@router.put("/ao", response_model=OperationsAreaOut)
async def set_ao(body: OperationsAreaIn, session: AsyncSession = Depends(get_session)):
    """Single active AO -- replaces any existing row (upsert-by-replace, not a
    per-mission list yet -- see OperationsArea's docstring)."""
    ao = (await session.execute(select(OperationsArea))).scalars().first()
    if ao is None:
        ao = OperationsArea(
            center_latitude=body.center_latitude,
            center_longitude=body.center_longitude,
            radius_nm=body.radius_nm,
        )
        session.add(ao)
    else:
        ao.center_latitude = body.center_latitude
        ao.center_longitude = body.center_longitude
        ao.radius_nm = body.radius_nm
    await session.commit()
    await session.refresh(ao)
    return ao
