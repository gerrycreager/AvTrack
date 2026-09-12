"""Import airfields from an FAA NASR 28-day subscription extract (REQUIREMENTS.md open
question 2 -- decided: NASR over OurAirports).

Validated 2026-09-12 against a real NASR test extract
(NASR_10_1_TEST_CSV.zip). Column names (APT_BASE.csv / APT_RWY.csv) matched what was
assumed, EXCEPT: LAT_DECIMAL/LONG_DECIMAL are already plain decimal degrees (e.g.
"39.261915"), not DMS-encoded strings as originally guessed -- the DMS parser has been
removed. SITE_TYPE_CODE "A" = airport, confirmed (13,057 of ~19,400 NASR sites in the
test extract). SURFACE_TYPE_CODE hard-surface prefix matching (CONC/ASPH/PEM) checked
against the real value distribution and looks correct.

NASR is republished on a 28-day cycle (per Gerry) -- re-run this against each new cycle's
extract; it's an upsert keyed on faa_id, so re-running is safe.
https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/

Usage:
    python scripts/import_nasr_airfields.py path/to/CSV_Data/

No surface/length filter is applied here per CAPR 70-1 9.11.2.5.1.4 (TOLD verification
governs which runway is usable for a given operation, not a fixed cutoff at import
time) -- see REQUIREMENTS.md open question 2. `hard_surface_available` and
`longest_runway_ft` are stored per-airfield so downstream heuristics (e.g. the
touch-and-go pre-filter, CAPR 9.11.2.5.1.5: hard surface >= 3000 ft) can apply their
own criteria without another data source.
"""

import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from geoalchemy2.shape import from_shape  # noqa: E402
from shapely.geometry import Point  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Airfield  # noqa: E402

HARD_SURFACE_PREFIXES = ("CONC", "ASPH", "PEM", "PFC")  # concrete/asphalt variants
# NASR OWNERSHIP_TYPE_CODE values, confirmed against the real test extract 2026-09-13:
# MA=Military-Army, MN=Military-Navy, MR=Military-Air Force, CG=Coast Guard.
MILITARY_OWNERSHIP_CODES = {"MA", "MN", "MR", "CG"}


async def import_nasr(csv_dir: Path) -> None:
    base_rows = list(csv.DictReader((csv_dir / "APT_BASE.csv").open(newline="")))
    rwy_rows = list(csv.DictReader((csv_dir / "APT_RWY.csv").open(newline="")))

    runways_by_site: dict[str, list[dict]] = {}
    for r in rwy_rows:
        runways_by_site.setdefault(r["SITE_NO"], []).append(r)

    async with SessionLocal() as session:
        existing = {a.faa_id: a for a in (await session.execute(select(Airfield))).scalars() if a.faa_id}

        count = 0
        for row in base_rows:
            if row.get("SITE_TYPE_CODE") != "A":  # airports only, not heliports/seaplane bases
                continue
            faa_id = row["ARPT_ID"].strip().upper()
            runways = runways_by_site.get(row["SITE_NO"], [])
            longest_ft = max((float(r["RWY_LEN"]) for r in runways if r.get("RWY_LEN")), default=None)
            hard_surface = any(
                (r.get("SURFACE_TYPE_CODE") or "").upper().startswith(HARD_SURFACE_PREFIXES) for r in runways
            )

            airfield = existing.get(faa_id) or Airfield(faa_id=faa_id)
            airfield.icao_id = (row.get("ICAO_ID") or "").strip().upper() or None
            airfield.name = row.get("ARPT_NAME", "").strip().title()
            airfield.location = from_shape(
                Point(float(row["LONG_DECIMAL"]), float(row["LAT_DECIMAL"])), srid=4326
            )
            airfield.elevation_ft = float(row["ELEV"]) if row.get("ELEV") else None
            airfield.longest_runway_ft = longest_ft
            airfield.hard_surface_available = hard_surface
            airfield.is_military = (row.get("OWNERSHIP_TYPE_CODE") or "").strip().upper() in MILITARY_OWNERSHIP_CODES
            session.add(airfield)
            count += 1

        await session.commit()

    print(f"Imported/updated {count} airfields from {csv_dir}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    asyncio.run(import_nasr(Path(sys.argv[1])))
