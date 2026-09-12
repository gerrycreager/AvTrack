"""Import/refresh the callsign<->tail mapping from Gerry's CSV (REQUIREMENTS.md open
question 1 -- decided: CSV, re-imported monthly).

Expected columns (adjust to match the real export once you have a sample file):
    tail_number, callsign, callsign_prefix, aircraft_type, wing

Usage:
    python scripts/import_callsign_tails.py path/to/aircraft.csv

Behavior: upserts by tail_number. Any existing aircraft NOT present in this month's
CSV is marked active=False rather than deleted, so historical Position/TrackingEvent
rows never dangle and monthly fleet changes stay auditable. Re-run this any time a new
CSV is dropped -- it is idempotent.

Performance note (2026-09-13, per Gerry asking about ~600-tail scale -- CAP's full
national fleet is roughly that size): lookups below are plain Python dicts
(tail_number/callsign -> Aircraft), i.e. hash maps with O(1) average-case lookup --
already asymptotically better than any sort+search structure would be (that would be
O(log n) per lookup, a regression, not an optimization). Measured empirically against
a synthetic 600-row CSV: ~0.9s wall time total, almost entirely Python/asyncpg
startup overhead, not the matching logic. No algorithmic work needed here at this
scale.

Soft data-quality check (per Gerry, 2026-09-13): most CAP tail numbers end in CP, CA,
or CV, but not all -- so tails that don't match are logged as a warning, not rejected,
since that's expected to happen legitimately sometimes.

Also upserts an `AircraftCallsign` row (label="CAP") per aircraft alongside
`Aircraft.callsign` -- per Gerry (2026-09-13), a tail can legitimately answer to more
than one callsign (its routine CAP callsign, and a PARD callsign when tasked that way,
which is a transponder callsign change on the *same* aircraft, not a different one).
This script only ever manages the "CAP"-labeled entry; PARD callsigns get added
separately (no PARD data/import path exists yet as of this writing) without this
script touching them. Matches by callsign *string* first (not just aircraft), so a
callsign that gets reassigned to a different tail between monthly imports is handled
by moving the existing row rather than colliding with its unique constraint.
"""

import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Aircraft, AircraftCallsign  # noqa: E402


async def import_csv(csv_path: Path) -> None:
    with csv_path.open(newline="") as f:
        rows = list(csv.DictReader(f))

    COMMON_CAP_SUFFIXES = ("CP", "CA", "CV")  # most, not all, CAP tails end this way -- soft check only

    seen_tails: set[str] = set()
    unusual_suffix_tails: list[str] = []
    async with SessionLocal() as session:
        existing = {a.tail_number: a for a in (await session.execute(select(Aircraft))).scalars()}
        existing_callsigns = {c.callsign: c for c in (await session.execute(select(AircraftCallsign))).scalars()}

        for row in rows:
            tail = row["tail_number"].strip().upper()
            seen_tails.add(tail)
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
            aircraft.aircraft_type = (row.get("aircraft_type") or "").strip() or None
            aircraft.wing = (row.get("wing") or "").strip().upper() or None
            aircraft.active = True

            if callsign:
                cs_row = existing_callsigns.get(callsign)
                if cs_row is None:
                    cs_row = AircraftCallsign(callsign=callsign)
                    session.add(cs_row)
                    existing_callsigns[callsign] = cs_row
                cs_row.aircraft = aircraft
                cs_row.label = "CAP"

        for tail, aircraft in existing.items():
            if tail not in seen_tails:
                aircraft.active = False

        await session.commit()

    print(f"Imported {len(rows)} rows; {len(existing) - len(seen_tails & existing.keys())} aircraft deactivated.")
    if unusual_suffix_tails:
        print(
            f"Note: {len(unusual_suffix_tails)} tail(s) don't end in CP/CA/CV "
            f"(most CAP tails do, but not all -- not an error, just worth a glance): "
            f"{', '.join(sorted(unusual_suffix_tails))}"
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    asyncio.run(import_csv(Path(sys.argv[1])))
