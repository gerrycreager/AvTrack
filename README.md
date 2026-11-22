# AvTrack

Phase 1 scaffold. New here? [`OVERVIEW.md`](OVERVIEW.md) explains what this
is and why in plain language; [`ARCHITECTURE.md`](ARCHITECTURE.md) is the
technical map of how it's put together. `REQUIREMENTS.md` is the detailed
spec and build history. This file is just "how do I run it."

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
~/miniconda3/envs/avtrack-py312/bin/python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # then fill in FLIGHTAWARE_API_KEY
alembic upgrade head         # creates/updates the schema -- see ALEMBIC.md
uvicorn app.main:app --reload --app-dir .
```

Open http://localhost:8000 — the map should load. Without `FLIGHTAWARE_API_KEY` set,
ADS-B polling is disabled (logged at startup) but everything else still works.

### Why the venv is built from a conda Python, not `/usr/bin/python3`

Found live 2026-10-25: an Ubuntu 24.04.1 upgrade bumped the system default Python
to 3.14 and removed `/usr/bin/python3.12` outright, which broke the existing venv
(every script in it has a `#!.../python3.12` shebang that stopped resolving) --
`avtrack-backend.service` sat in a restart loop with "203/EXEC". Rebuilding
straight under the new system 3.14 doesn't work either: `shapely`, `mgrs`,
`asyncpg`, and `python-qpid-proton` (all real deps here, see `requirements.txt`)
had zero prebuilt wheels for 3.14 at the time and failed to compile from source
(missing dev headers for the new ABI, then a toolchain/linker mismatch once
headers were available) -- normal lag for any CPython release that young, not a
fluke.

Per Gerry: deliberately stay on 3.12 rather than chase whatever's newest --
3.12 is still a fully-supported, non-EOL release, it's what this app was
actually built and tested against, and running the bleeding-edge interpreter
buys nothing here (no language feature AvTrack needs) while exposing it to
exactly this kind of breakage every time the C-extension ecosystem hasn't
caught up yet. Gerry is also just an Anaconda/miniconda user generally, so
using conda to source the 3.12 interpreter (rather than e.g. the `deadsnakes`
PPA) was his preference, not just the fastest fix available at the time.

The fix: a dedicated, isolated conda env holding ONLY the Python 3.12
interpreter --

```
~/miniconda3/bin/conda create -n avtrack-py312 python=3.12 -y
```

-- then `backend/.venv` is built from that interpreter (see the one-time setup
above), not from whatever `/usr/bin/python3` currently points to. This
deliberately decouples AvTrack's venv from apt/Ubuntu's system Python entirely,
so a future Ubuntu upgrade changing the system default again can't break this
the same way -- only removing/breaking the `avtrack-py312` conda env itself
would. To recreate the venv from scratch (e.g. after a future break, or on a
new host with miniconda already installed): `conda create -n avtrack-py312
python=3.12 -y`, then the normal venv-from-that-interpreter steps above.

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
