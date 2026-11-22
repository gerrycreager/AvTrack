# AvTrack — Architecture

This is the conceptual map for a contributor. `README.md` is "how do I run
it"; `REQUIREMENTS.md` is the detailed spec and build history (large,
written as a running log, not meant to be read front-to-back). This doc is
"how is the system put together, today."

## Stack

FastAPI backend (`backend/app/`), PostgreSQL + PostGIS, a plain-JS/Leaflet
(not a frontend framework) single-page map at `frontend/index.html`,
Alembic for schema migrations. No build step on the frontend — it's served
directly, so a template change is live on reload with nothing to compile.

## Data flow

```
ADS-B sources (FlightAware / ADSB.lol / ADSB.fi / OpenSky)
        |  normalized by backend/app/adsb/*.py (shared base.py interface)
        v
backend/app/ingestion/poller.py  -->  Position rows in Postgres
        |
        +--> backend/app/ingestion/ao_discovery_poller.py
        |      (matches CAP/PARD-shaped callsigns not yet in the roster,
        |       scoped to the live Area of Operations)
        v
FastAPI routers (backend/app/api/*.py)  <-->  frontend/index.html (Leaflet)
                                         <-->  WebSocket (app/ingestion/ws_manager.py)
                                               for live position push
```

Map base layers (VFR sectional, NASR aviation detail) are fetched directly
from WxCOP's self-hosted MapCache WMTS endpoint — AvTrack doesn't store or
re-derive chart data itself, it just points at WxCOP's tile source with a
fully-qualified URL (necessary since the two apps run on different ports/
origins on the same host). `backend/app/integrations/wxcop_client.py` is
the analogous path for pulling live weather context from WxCOP's API rather
than re-implementing it.

## Core data model (`backend/app/models.py`)

- **Aircraft / AircraftCallsign** — a tail can answer to more than one
  callsign (a PARD tasking is typically the same physical aircraft under a
  different transponder callsign, not a different aircraft); `Aircraft`
  holds the primary/display callsign, `AircraftCallsign` is the real
  identity-lookup table (one row per callsign a tail is known to answer to).
- **Position** — raw ADS-B position reports, tagged with their source
  provider.
- **Sortie / SortieWaypoint** — the tasked-flight unit everything else
  hangs off: PIREPs, waypoints, and what the Ops Board actually displays
  (sortie-scoped, not "whatever's currently transmitting").
- **TrackingEvent / EventEdit** — detected events (e.g. takeoff/landing)
  with an edit history, since automatic detection sometimes needs a human
  correction.
- **DiscoveredCallsign** — candidate unregistered aircraft seen inside the
  Area of Operations, pending approve/dismiss.
- **User / UserSession / RegistrationRequest** — CAP ID + password + TOTP
  auth, session tokens (not JWT — no secret key to manage, logout is a real
  DELETE), admin-approved self-service registration.
- **Mission / MissionMembership / MissionAircraft / OperationsArea** —
  mission-scoped grouping and the persisted AO circle used by the discovery
  pollers.
- **Airfield / SpecialUseAirspace / GisLayer / GisFeature** — map reference
  data.

## Why two discovery pollers, not one

`scripts/discover_cap_callsigns.py` does a slow, wide 22-point CONUS sweep;
`app/ingestion/ao_discovery_poller.py` is a fast (60s), narrow poll scoped
to whatever Area of Operations is currently set. They share the same
matching logic (`app/integrations/adsb_discovery.py`) rather than
duplicating it. The wide sweep exists so an aircraft can be discovered even
before any AO is set for the day; the narrow one is what actually matters
operationally once a mission is live.

## Why FastAPI + a plain Leaflet frontend, not something more "standard"

FastAPI's async support matters here because this app's whole job is
continuously polling multiple external ADS-B sources while serving live
WebSocket updates to connected clients — a sync framework would make that
materially more awkward. A framework-free frontend was a deliberate choice
for a Phase 1 "fastest path to something wings can use" web app: no build
step means no build-tooling maintenance burden, and the whole point of
Phase 1 is that its data model — not its frontend stack — is what carries
forward into Phase 2's native clients.
