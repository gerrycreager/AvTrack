"""Core schema. Shared between Phase 1 (single backend) and, unchanged, the Phase 2
server -- see REQUIREMENTS.md section 2.

Event logging follows the append-only + overlay pattern required in REQUIREMENTS.md
3.3: `TrackingEvent` rows are never mutated once written (auto-detected or via the
"NOW" button). A user correction to a time/field is recorded as an `EventEdit` layered
on top, so the raw detection is always recoverable.
"""

import enum
import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Aircraft(Base):
    """One row per tail number. Populated/refreshed from the monthly callsign<->tail
    CSV import -- see scripts/import_callsign_tails.py and REQUIREMENTS.md open
    question 1.

    `callsign` here is the *primary/routine* callsign only, kept for quick display
    without a join. It is NOT the sole identity key for a tail: per Gerry
    (2026-09-13), a PARD tasking is a callsign change in the same aircraft's
    transponder, not a different aircraft -- the same tail_number can legitimately be
    associated with more than one callsign over time (its routine CAP callsign, and a
    PARD callsign when tasked that way). See `AircraftCallsign` for the full set of
    callsigns a tail is known to answer to; anything doing identity lookup by callsign
    (see api/aircraft.py resolve_tracked_list) must check that table, not just this
    single field. Live display (datablocks, sortie panel) is unaffected by any of this
    since it already shows whatever callsign the ADS-B feed currently reports, not a
    stored one -- and the "unlisted aircraft" highlighting keys off tail_number, not
    callsign, so it's also already safe."""

    __tablename__ = "aircraft"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tail_number: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    callsign: Mapped[str | None] = mapped_column(String(20), index=True)
    callsign_prefix: Mapped[str | None] = mapped_column(String(10), index=True)
    aircraft_type: Mapped[str | None] = mapped_column(String(20))
    wing: Mapped[str | None] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    positions: Mapped[list["Position"]] = relationship(back_populates="aircraft")
    events: Mapped[list["TrackingEvent"]] = relationship(back_populates="aircraft")
    callsigns: Mapped[list["AircraftCallsign"]] = relationship(back_populates="aircraft", lazy="selectin")


class AircraftCallsign(Base):
    """Every callsign a tail is known to answer to -- routine CAP callsign, PARD
    callsign, or any other. See the note on Aircraft.callsign above for why this
    exists as a separate table rather than a single field."""

    __tablename__ = "aircraft_callsigns"

    id: Mapped[uuid.UUID] = _uuid_pk()
    aircraft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aircraft.id"), index=True)
    callsign: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(20))  # e.g. "CAP", "PARD" -- informational only

    aircraft: Mapped["Aircraft"] = relationship(back_populates="callsigns")


class Airfield(Base):
    """From FAA NASR (REQUIREMENTS.md open question 2 -- decided: NASR). No
    surface/length filter is applied at import time per CAPR 70-1 9.11.2.5.1.4 (TOLD
    verification governs, not a fixed minimum); `hard_surface`/`runway_length_ft` are
    kept so heuristics (e.g. touch-and-go eligibility, CAPR 9.11.2.5.1.5: hard surface
    >= 3000 ft) can filter per-use rather than at import."""

    __tablename__ = "airfields"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Wider than the nominal 3-4 char FAA/ICAO identifier length -- source data
    # (OurAirports local_code, in particular) isn't always that strictly formatted.
    icao_id: Mapped[str | None] = mapped_column(String(10), index=True)
    faa_id: Mapped[str | None] = mapped_column(String(10), index=True)
    name: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str] = mapped_column(Geometry(geometry_type="POINT", srid=4326))
    elevation_ft: Mapped[float | None] = mapped_column(Float)
    longest_runway_ft: Mapped[float | None] = mapped_column(Float)
    hard_surface_available: Mapped[bool | None] = mapped_column()
    # From NASR OWNERSHIP_TYPE_CODE (MA/MN/MR/CG). Drives label tier 1 -- see
    # app/api/airfields.py compute_tier(), matching the tiering convention already
    # established in CAP WxCOP's airport_tiers_endpoint.py.
    is_military: Mapped[bool] = mapped_column(default=False)


class PositionSource(str, enum.Enum):
    flightaware = "flightaware"
    swim = "swim"  # FAA SWIM/FDPS, via the r815 relay -- see REQUIREMENTS.md 3.5
    adsb_lol = "adsb_lol"  # api.adsb.lol -- free/open raw ADS-B, see app/adsb/adsb_lol.py
    other = "other"


class Position(Base):
    """Raw ADS-B position reports. High volume -- this is the table a real
    time-series/partitioning strategy will eventually apply to, not a design decision
    for the scaffold stage."""

    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    aircraft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aircraft.id"), index=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    location: Mapped[str] = mapped_column(Geometry(geometry_type="POINT", srid=4326))
    altitude_ft: Mapped[float | None] = mapped_column(Float)
    ground_speed_kt: Mapped[float | None] = mapped_column(Float)
    heading_deg: Mapped[float | None] = mapped_column(Float)
    source: Mapped[PositionSource] = mapped_column(Enum(PositionSource))
    raw: Mapped[dict | None] = mapped_column(JSON)

    aircraft: Mapped["Aircraft"] = relationship(back_populates="positions")


class EventType(str, enum.Enum):
    engine_start = "engine_start"
    takeoff = "takeoff"
    touch_and_go = "touch_and_go"
    landing = "landing"
    engine_stop = "engine_stop"
    # Sortie waypoint check-ins (REQUIREMENTS.md 3.3, added 2026-09-13) -- these
    # timestamps go through the same TrackingEvent/EventEdit append-only + overlay
    # mechanism as everything else here, per Gerry's explicit preference over a
    # separate/simpler mechanism just for these.
    in_grid = "in_grid"
    out_grid = "out_grid"
    ops_check = "ops_check"


class DetectionMethod(str, enum.Enum):
    auto = "auto"
    manual = "manual"  # includes the "NOW" button


class TrackingEvent(Base):
    """Append-only. Never update event_time_utc or delete a row here once it exists --
    corrections go in EventEdit. This table alone is CAP's flight-time-of-record audit
    trail (REQUIREMENTS.md 3.3/4)."""

    __tablename__ = "tracking_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    aircraft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aircraft.id"), index=True)
    airfield_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("airfields.id"))
    event_type: Mapped[EventType] = mapped_column(Enum(EventType))
    event_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    detected_by: Mapped[DetectionMethod] = mapped_column(Enum(DetectionMethod))
    confidence: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    aircraft: Mapped["Aircraft"] = relationship(back_populates="events")
    # lazy="selectin": default lazy-loading isn't safe under async SQLAlchemy (raises
    # MissingGreenlet if touched outside an explicit await) -- this relationship is
    # read in list_events without an explicit eager-load option, so it must not rely
    # on the caller remembering one.
    edits: Mapped[list["EventEdit"]] = relationship(back_populates="event", lazy="selectin")


class EventEdit(Base):
    """A user correction layered on a TrackingEvent. The event's *effective* time for
    display/reporting is the latest edit's new_value, if any; the original row is
    untouched."""

    __tablename__ = "event_edits"

    id: Mapped[uuid.UUID] = _uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tracking_events.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(50))
    old_value: Mapped[str | None] = mapped_column(String(200))
    new_value: Mapped[str] = mapped_column(String(200))
    edited_by: Mapped[str] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(500))
    edited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["TrackingEvent"] = relationship(back_populates="edits")


class WaypointLocationMethod(str, enum.Enum):
    """How a SortieWaypoint's location was entered. Bearing/radial-from-navaid and
    FAA-fix lookup are deferred (REQUIREMENTS.md 3.3, 2026-09-13 decision) -- both
    need a reference dataset (FAA NASR navaid/fix data) not yet imported."""

    latlon = "latlon"
    mgrs = "mgrs"
    named_point = "named_point"  # free-text place name ("over Smithville"), lat/lon entered manually


class Sortie(Base):
    """One sortie: engine start to engine stop (REQUIREMENTS.md 3.3, added
    2026-09-13 per Gerry, modeled on the RPP recorded-times spreadsheet CAP already
    uses). A new takeoff/landing/touch-and-go without an intervening engine stop is
    the *same* sortie, not a new one -- engine_stop_event_id stays null while a
    sortie is still in progress.

    Metadata (PIC, sortie/mission number) lives directly on this row -- not
    safety-critical in the same way a timestamp is, so no audit-trail overlay for
    these. The start/stop *times* are different: they're backed by the existing
    TrackingEvent/EventEdit append-only + correction-overlay mechanism (per Gerry's
    explicit preference) rather than plain fields here, so those specific
    corrections go through the same audit trail as every other tracked event."""

    __tablename__ = "sorties"

    id: Mapped[uuid.UUID] = _uuid_pk()
    aircraft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aircraft.id"), index=True)
    sortie_number: Mapped[int | None] = mapped_column()
    # "Global variable for a given session" (Gerry) is a frontend convenience
    # (pre-fills new-sortie forms) -- stored per-sortie here so each record is
    # self-contained even if the convenience default changes mid-day.
    mission_number: Mapped[str | None] = mapped_column(String(50))
    pic_name: Mapped[str | None] = mapped_column(String(100))
    engine_start_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tracking_events.id"))
    engine_stop_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tracking_events.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    aircraft: Mapped["Aircraft"] = relationship()
    engine_start_event: Mapped["TrackingEvent | None"] = relationship(
        foreign_keys=[engine_start_event_id], lazy="selectin"
    )
    engine_stop_event: Mapped["TrackingEvent | None"] = relationship(
        foreign_keys=[engine_stop_event_id], lazy="selectin"
    )
    waypoints: Mapped[list["SortieWaypoint"]] = relationship(back_populates="sortie", lazy="selectin")


class SortieWaypoint(Base):
    """An in-grid/out-grid/operations-check entry within a sortie (REQUIREMENTS.md
    3.3, added 2026-09-13 per Gerry). Repeatable per sortie -- e.g. multiple grids
    searched, or periodic ops checks, not just one of each. `waypoint_type` (which
    of in_grid/out_grid/ops_check this is) lives on the linked TrackingEvent, not
    duplicated here. `raw_input`/`location_method` preserve exactly what the user
    entered even after resolving to lat/lon for map display (accountability).

    `altitude_ft`/`fuel_remaining_hours`/`fuel_remaining_minutes`/`comments` are
    Operations Check-specific (per Gerry, 2026-09-13) -- left null for in_grid/
    out_grid entries, which only carry a location. `comments` is Text, not a
    bounded VARCHAR, per Gerry: "nominally ~200 chars but could be longer, and
    might eventually need an even bigger free-text block" -- a bounded column
    already caused a real truncation crash once this session (SpecialUseAirspace's
    times_of_use), not repeating that here."""

    __tablename__ = "sortie_waypoints"

    id: Mapped[uuid.UUID] = _uuid_pk()
    sortie_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sorties.id"), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tracking_events.id"))
    location_method: Mapped[WaypointLocationMethod] = mapped_column(Enum(WaypointLocationMethod))
    raw_input: Mapped[str] = mapped_column(String(200))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    altitude_ft: Mapped[float | None] = mapped_column(Float)
    fuel_remaining_hours: Mapped[int | None] = mapped_column()
    fuel_remaining_minutes: Mapped[int | None] = mapped_column()
    comments: Mapped[str | None] = mapped_column(Text)

    sortie: Mapped["Sortie"] = relationship(back_populates="waypoints")
    event: Mapped["TrackingEvent"] = relationship(lazy="selectin")


class SpecialUseAirspace(Base):
    """Permanent charted Special Use Airspace (Prohibited/Restricted/MOA/Warning/
    Alert/Danger) -- REQUIREMENTS.md 3.2. Reuses the exact public FAA AIS
    hub.arcgis.com GeoJSON source CAP WxCOP already pulls from weekly
    (`/var/www/cap_winds_app/scripts/update_airspace_geo.py` on r815) -- see
    scripts/import_sua_airspace.py, which mirrors that same download, not WxCOP's
    database directly (no runtime cross-database dependency on data2)."""

    __tablename__ = "special_use_airspace"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # FAA GLOBAL_ID -- stable across refreshes, used as the upsert key.
    source_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(100))
    # P / R / MOA / W / A / D -- see app/api/airspace.py SUA_TYPE_NAMES.
    type_code: Mapped[str | None] = mapped_column(String(10), index=True)
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(10), index=True)
    # Kept as text, not numeric -- source values include non-numeric altitudes
    # like "SFC" for the lower bound.
    lower_altitude: Mapped[str | None] = mapped_column(String(20))
    upper_altitude: Mapped[str | None] = mapped_column(String(20))
    times_of_use: Mapped[str | None] = mapped_column(Text)
    geometry: Mapped[str] = mapped_column(Geometry(srid=4326))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GisLayer(Base):
    """One imported SHP/KML/KMZ/GeoJSON file (REQUIREMENTS.md 3.2)."""

    __tablename__ = "gis_layers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(100))
    source_format: Mapped[str] = mapped_column(String(20))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    features: Mapped[list["GisFeature"]] = relationship(back_populates="layer")


class GisFeature(Base):
    __tablename__ = "gis_features"

    id: Mapped[uuid.UUID] = _uuid_pk()
    layer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gis_layers.id"), index=True)
    geometry: Mapped[str] = mapped_column(Geometry(srid=4326))
    properties: Mapped[dict | None] = mapped_column(JSON)

    layer: Mapped["GisLayer"] = relationship(back_populates="features")
