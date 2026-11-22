"""WMIRS "AirSorties" daily planning export import (REQUIREMENTS.md 3.3, added
2026-10-19). Per Gerry: sorties get created in WMIRS; AvTrack syncs from the daily
planning CSV/XLSX WMIRS exports rather than duplicating that planning work. Column
semantics and every decision below are grounded in two real WMIRS exports (not
guessed) -- see the project memory this was designed in for the full history.

Only the "Powered Sorties" sheet is read. "Glider Sorties" exists in both real
samples seen but has never had a header row or any data in either -- there's nothing
to parse, and gliders aren't ADS-B-tracked the way this app's scope assumes, so it's
silently ignored rather than guessed at.

**Upsert key is (mission_id, wmirs_sortie_id)** -- see Sortie.wmirs_sortie_id's
comment in models.py for why that pair, not a bare global-unique column.

**"Complete" rows are never created or updated from this importer.** WMIRS's
planning export never carries real engine-on/off timestamps (EstDeparture/
EstArrival are estimates, not AvTrack's own audited TrackingEvent log) -- if a
"Complete" row has no matching local sortie yet, there's nothing accurate this
importer could backfill, so it's skipped and reported, never faked into existence.
If a row we already imported later shows "Complete" in WMIRS, that's WMIRS
confirming what (in real usage) should already have happened locally via
POST /sorties/{id}/start + /stop -- this importer leaves it untouched either way.

**A local sortie that has already moved past `planned` is never touched by a
re-import**, even if WMIRS's row for the same wmirs_sortie_id changed (status,
tail, times). Once a sortie is active/complete/cancelled locally, WMIRS data for
it is stale by definition and silently applying it (e.g. reassigning the aircraft
on a sortie that's already flying) would be actively dangerous -- skipped and
reported instead, same reasoning as the `planned`-only guard on
POST /sorties/{id} aircraft reassignment and POST /sorties/{id}/cancel.

**Aircraft are never auto-created here.** TailNo must already resolve to a real
`Aircraft` row (the NHQ-roster-driven table) -- a WMIRS row for a tail AvTrack
doesn't know about yet is reported as a warning, not used to invent a new Aircraft
record from planning-sheet data alone.
"""

import io
import logging
from datetime import datetime, timezone
from uuid import UUID

import openpyxl
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_mission_member
from app.db import get_session
from app.models import Aircraft, AircraftCallsign, Airfield, Mission, Sortie, User
from app.schemas import WmirsImportOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["wmirs-import"])

SHEET_NAME = "Powered Sorties"

# Confirmed live against two real exports (2026-10-19) -- see models.py's
# Sortie.wmirs_raw comment. Import fails loudly if any of these is missing rather
# than silently reading the wrong column, since WMIRS could reorder/rename columns
# between export versions without warning.
REQUIRED_COLUMNS = (
    "SortieStatus",
    "SortieNo",
    "SortieDate",
    "TailNo",
    "Callsign",
    "Pilot",
    "DepartureLocation",
    "Destination",
    "EstDeparture",
    "EstArrival",
)

# Confirmed real values (2026-10-19): "Complete" (flown), "Approved/Released" and
# "Pending Release" (both pre-flight). Treated as a known-but-not-closed set --
# anything else encountered falls back to "planned" rather than raising, since a
# new status string from a future WMIRS version shouldn't hard-fail the whole
# import.
STATUS_COMPLETE = "Complete"


async def _resolve_airfield(session: AsyncSession, code: str | None, cache: dict) -> Airfield | None:
    """faa_id match first (covers every bare WMIRS code confirmed so far), then
    K/P/T-prefixed icao_id (K for CONUS, P/T for OCONUS/Pacific/Caribbean) --
    confirmed by Gerry as a general rule, NOT a guaranteed one: a few Ix-prefixed
    LIDs don't actually take a K prefix. A miss on both is reported as a warning,
    never guessed at further."""
    if not code:
        return None
    code = code.strip().upper()
    if not code:
        return None
    if code in cache:
        return cache[code]
    airfield = (await session.execute(select(Airfield).where(Airfield.faa_id == code))).scalars().first()
    if airfield is None:
        for prefix in ("K", "P", "T"):
            airfield = (
                (await session.execute(select(Airfield).where(Airfield.icao_id == f"{prefix}{code}")))
                .scalars()
                .first()
            )
            if airfield is not None:
                break
    cache[code] = airfield
    return airfield


def _utc(value: datetime | None) -> datetime | None:
    """WMIRS/CAP date-times are already UTC ("Zulu," CAP policy, confirmed by
    Gerry) -- openpyxl hands back a naive datetime, so this only labels it UTC,
    never converts."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _clean_callsign(raw: str | None) -> str | None:
    """Per Gerry: WMIRS's Callsign column is inconsistently formatted ("CAP 3427",
    "CAP1210 ", bare "329") -- strip ALL whitespace, not just the ends, before
    using it for anything."""
    if not raw:
        return None
    cleaned = "".join(raw.split()).upper()
    return cleaned or None


def _parse_sheet(file_bytes: bytes) -> list[dict]:
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        raise HTTPException(400, f"Could not read XLSX file: {exc}") from exc
    if SHEET_NAME not in wb.sheetnames:
        raise HTTPException(400, f"No '{SHEET_NAME}' sheet found in this file")
    ws = wb[SHEET_NAME]
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        return []
    header = [(h or "").strip() if isinstance(h, str) else h for h in header]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise HTTPException(400, f"Missing expected column(s) in '{SHEET_NAME}': {missing}")
    col_index = {name: i for i, name in enumerate(header)}

    def get(row: tuple, name: str):
        idx = col_index.get(name)
        return row[idx] if idx is not None and idx < len(row) else None

    out = []
    for row in rows_iter:
        if row is None or all(v is None for v in row):
            continue
        out.append({name: get(row, name) for name in header if name})
    return out


@router.post("/api/missions/{mission_id}/wmirs-import", response_model=WmirsImportOut)
async def import_wmirs_sorties(
    mission_id: UUID,
    file: UploadFile = File(...),
    user: User = Depends(require_mission_member()),
    session: AsyncSession = Depends(get_session),
):
    mission = await session.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(404, "No such mission")

    content = await file.read()
    rows = _parse_sheet(content)

    created = updated = skipped = 0
    warnings: list[str] = []
    airfield_cache: dict = {}

    aircraft_by_tail = {a.tail_number: a for a in (await session.execute(select(Aircraft))).scalars()}
    callsigns_by_aircraft: dict = {}
    for cs in (await session.execute(select(AircraftCallsign))).scalars():
        callsigns_by_aircraft.setdefault(cs.aircraft_id, set()).add(cs.callsign)

    existing_sorties = {
        s.wmirs_sortie_id: s
        for s in (
            await session.execute(
                select(Sortie).where(Sortie.mission_id == mission_id, Sortie.wmirs_sortie_id.is_not(None))
            )
        )
        .scalars()
        .all()
    }

    for row in rows:
        wmirs_sortie_id = str(row.get("SortieNo") or "").strip()
        tail = str(row.get("TailNo") or "").strip().upper()
        wmirs_status = str(row.get("SortieStatus") or "").strip()
        if not wmirs_sortie_id or not tail:
            warnings.append(f"Row skipped -- missing SortieNo or TailNo (SortieNo={wmirs_sortie_id!r}, TailNo={tail!r})")
            skipped += 1
            continue

        existing = existing_sorties.get(wmirs_sortie_id)

        if wmirs_status == STATUS_COMPLETE and existing is None:
            # No real engine-on/off timestamps to backfill from a planning export --
            # see module docstring. Nothing useful to create.
            warnings.append(f"{wmirs_sortie_id}: skipped -- WMIRS shows Complete but AvTrack never tracked it")
            skipped += 1
            continue

        if existing is not None and existing.effective_status() != "planned":
            warnings.append(
                f"{wmirs_sortie_id}: skipped -- already {existing.effective_status()} locally, "
                "WMIRS data ignored to avoid overwriting a sortie that's already flying"
            )
            skipped += 1
            continue

        aircraft = aircraft_by_tail.get(tail)
        if aircraft is None:
            warnings.append(f"{wmirs_sortie_id}: skipped -- tail {tail} is not a known Aircraft (add it via the roster first)")
            skipped += 1
            continue

        cleaned_callsign = _clean_callsign(row.get("Callsign"))
        if cleaned_callsign is not None:
            known = callsigns_by_aircraft.get(aircraft.id, set())
            if aircraft.callsign and aircraft.callsign.strip().upper() == cleaned_callsign:
                pass
            elif cleaned_callsign not in known:
                warnings.append(
                    f"{wmirs_sortie_id}: callsign mismatch -- WMIRS says {cleaned_callsign} for tail {tail}, "
                    f"AvTrack knows it as {aircraft.callsign or '(none)'} -- verify this wasn't an aircraft swap"
                )

        departure_raw = (str(row.get("DepartureLocation")).strip().upper() if row.get("DepartureLocation") else None)
        destination_raw = (str(row.get("Destination")).strip().upper() if row.get("Destination") else None)
        departure_airfield = await _resolve_airfield(session, departure_raw, airfield_cache)
        destination_airfield = await _resolve_airfield(session, destination_raw, airfield_cache)
        if departure_raw and departure_airfield is None:
            warnings.append(f"{wmirs_sortie_id}: departure code '{departure_raw}' did not resolve to a known airfield")
        if destination_raw and destination_airfield is None:
            warnings.append(f"{wmirs_sortie_id}: destination code '{destination_raw}' did not resolve to a known airfield")

        wmirs_raw = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}

        if existing is None:
            sortie = Sortie(
                aircraft_id=aircraft.id,
                mission_id=mission_id,
                mission_number=mission.mission_number,
                wmirs_sortie_id=wmirs_sortie_id,
                pic_name=str(row.get("Pilot") or "").strip() or None,
                planned_date=_utc(row.get("SortieDate")),
                departure_raw=departure_raw,
                destination_raw=destination_raw,
                departure_airfield_id=departure_airfield.id if departure_airfield else None,
                destination_airfield_id=destination_airfield.id if destination_airfield else None,
                wmirs_raw=wmirs_raw,
            )
            session.add(sortie)
            created += 1
        else:
            # Re-import of a still-planned row -- this IS the "WMIRS swapped the
            # aircraft after we already had the list" case Gerry described, so
            # aircraft_id is deliberately one of the fields refreshed here.
            existing.aircraft_id = aircraft.id
            existing.pic_name = str(row.get("Pilot") or "").strip() or existing.pic_name
            existing.planned_date = _utc(row.get("SortieDate")) or existing.planned_date
            existing.departure_raw = departure_raw
            existing.destination_raw = destination_raw
            existing.departure_airfield_id = departure_airfield.id if departure_airfield else None
            existing.destination_airfield_id = destination_airfield.id if destination_airfield else None
            existing.wmirs_raw = wmirs_raw
            updated += 1

    await session.commit()
    return WmirsImportOut(created=created, updated=updated, skipped=skipped, warnings=warnings)
