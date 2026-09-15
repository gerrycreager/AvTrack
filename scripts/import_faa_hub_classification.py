"""Import FAA's commercial-service hub classification (REQUIREMENTS.md 3.2,
added 2026-09-14) -- replaces the "paved runway >= 8000ft" proxy tier-2
("major air carrier hub") had been using. Gerry caught live that the runway-
length proxy actually rendered 334 airfields nationally, most NOT hubs (former
military fields, high-altitude GA strips needing longer runways for
density-altitude reasons, logistics/test fields). Runway length doesn't imply
airline traffic; FAA's own hub classification does.

Source: FAA CY Enplanements report (published annually, ~September for the
prior calendar year), e.g.
https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger/ARP-cy2024-all-enplanements.xlsx
"CY <year> Enplanements at All Airports" on
https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger

The 'Hub' column is FAA's own L/M/S/N classification (Large/Medium/Small/
Nonhub, by share of total US enplanements) -- see app/api/airfields.py
compute_tier(), which treats L+M as "major hub" (63 airports, CY2024; matches
common industry usage of "major hub" better than Large alone). 'Locid' is
FAA's own local identifier, matched here against Airfield.faa_id (NASR's
ARPT_ID, same identifier system) -- not icao_id, since Locid is a 3-letter FAA
code (e.g. "ANC"), not the 4-letter ICAO code.

Re-run this whenever the FAA publishes a new CY report (annually) -- it's an
upsert keyed on faa_id match, unmatched Locids are reported but not fatal
(mostly non-CONUS/territory airports whose faa_id in NASR may not exactly
match this report's Locid).

Usage:
    python scripts/import_faa_hub_classification.py path/to/ARP-cyNNNN-all-enplanements.xlsx
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import openpyxl  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Airfield  # noqa: E402

VALID_HUB_TYPES = {"L", "M", "S", "N"}


async def import_hub_classification(xlsx_path: Path) -> None:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))  # row 1 is the header

    async with SessionLocal() as session:
        existing = {a.faa_id: a for a in (await session.execute(select(Airfield))).scalars() if a.faa_id}

        matched = 0
        unmatched = []
        for row in rows:
            # The report has trailer/summary rows at the end (e.g. a "Large Count"
            # row with the count in the Locid column) -- str()-coerce rather than
            # assume these are always text, and let the hub_type check below
            # harmlessly skip anything that isn't a real airport row.
            locid = str(row[3] or "").strip().upper()  # column D: Locid
            hub_type = str(row[7] or "").strip().upper()  # column H: Hub
            if not locid or hub_type not in VALID_HUB_TYPES:
                continue
            airfield = existing.get(locid)
            if airfield is None:
                unmatched.append(locid)
                continue
            airfield.hub_type = hub_type
            matched += 1

        await session.commit()

    print(f"Updated hub_type on {matched} airfields from {xlsx_path}")
    if unmatched:
        print(f"{len(unmatched)} Locids in the FAA report had no matching Airfield.faa_id: {', '.join(unmatched)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    asyncio.run(import_hub_classification(Path(sys.argv[1])))
