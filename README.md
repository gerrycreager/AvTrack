# AvTrack

Phase 1 scaffold. See `REQUIREMENTS.md` for the actual spec — this file is just
"how do I run it."

## Native dev (default on this machine)

PostgreSQL 16 + PostGIS are already installed locally. One-time setup:

```
sudo -u postgres psql -c "CREATE ROLE avtrack WITH LOGIN PASSWORD 'avtrack_dev_only';"
sudo -u postgres psql -c "CREATE DATABASE avtrack OWNER avtrack;"
sudo -u postgres psql -d avtrack -c "CREATE EXTENSION IF NOT EXISTS postgis;"
```

Then:

```
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # then fill in FLIGHTAWARE_API_KEY
alembic upgrade head         # creates/updates the schema -- see ALEMBIC.md
uvicorn app.main:app --reload --app-dir .
```

Open http://localhost:8000 — the map should load. Without `FLIGHTAWARE_API_KEY` set,
ADS-B polling is disabled (logged at startup) but everything else still works.

## Schema changes

Managed by Alembic now, not app-startup `create_all()`. See `backend/ALEMBIC.md` —
written as a from-scratch reference/tutorial, not just a cheat sheet.

## Docker (for later — e.g. once this needs to look like what CAP/IT will run)

```
docker compose up --build
```

Not required for local dev; kept in the repo for portability, not because you need to
learn it right now.

## Data imports

```
python scripts/import_callsign_tails.py path/to/aircraft.csv     # monthly
python scripts/import_nasr_airfields.py path/to/NASR/CSV_Data/   # per NASR 28-day cycle
```

Both need `backend` on `PYTHONPATH` — run from the repo root with the venv active;
the scripts add `backend` to `sys.path` themselves.
