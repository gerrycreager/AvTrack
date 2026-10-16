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
from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, String, Text, UniqueConstraint, func
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
    # Pre-fills the PIC field on the Start Sortie form for this tail (REQUIREMENTS.md
    # 3.3, added 2026-09-13, per Gerry: "build in the ability to read a column header
    # with pilot (or instructor) and populate that with callsigns and tails"). A
    # default, not a record of who actually flew -- the per-sortie pic_name on Sortie
    # is still what's actually logged and is independently editable at Start Sortie
    # time.
    default_pic_name: Mapped[str | None] = mapped_column(String(100))
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
    # FAA's own commercial-service hub classification (L/M/S/N -- Large/Medium/
    # Small/Nonhub, by share of total US enplanements), from the annual CY
    # Enplanements report -- see scripts/import_faa_hub_classification.py. Added
    # 2026-09-14 to replace a "paved runway >= 8000ft" proxy for tier 2 ("major
    # air carrier hub") after Gerry caught it live: that threshold actually pulled
    # in 334 airfields nationally, most of them not hubs at all (former military
    # fields, high-altitude GA fields needing longer runways, logistics/test
    # strips) -- runway length alone doesn't mean airline traffic. Nullable: most
    # airfields (GA/non-commercial) have no hub classification at all, which is a
    # real, expected value here, not missing data.
    hub_type: Mapped[str | None] = mapped_column(String(1))


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
    trail (REQUIREMENTS.md 3.3/4).

    `sortie_id` (added 2026-09-14 per Gerry: "we need take-off and landing to a full
    stop associated with a sortie") links every event type to whichever sortie was
    open on that aircraft when it was logged -- not just waypoints, which already had
    this via SortieWaypoint. Nullable because not every event happens during an open
    sortie (e.g. engine_start itself, logged before the Sortie row exists yet -- see
    app/api/sorties.py create_sortie for the flush-then-link ordering)."""

    __tablename__ = "tracking_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    aircraft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aircraft.id"), index=True)
    sortie_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sorties.id"), index=True)
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
    corrections go through the same audit trail as every other tracked event.

    `takeoff_event_id`/`landing_event_id` (added 2026-09-14 per Gerry: "we need
    take-off and landing to a full stop associated with a sortie") follow the exact
    same pattern as engine_start/engine_stop. touch_and_go deliberately does NOT get
    a slot here ("touch and go isn't a normal entry" -- Gerry) -- it's an ordinary
    TrackingEvent attributed to this sortie via sortie_id, not a promoted milestone.
    `landing_event_id` is updated to the *latest* landing logged, not just the
    first -- covers the "stop and go" training case Gerry flagged (a real full stop
    on the runway, immediately followed by another takeoff, same sortie, no engine
    stop in between): each stop-and-go's landing overwrites this field, so it always
    reflects the most recent one, with the understanding (per Gerry, "this is
    something that could be handled in edits") that misattribution here is a manual
    correction, not something the write path needs to solve perfectly."""

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
    takeoff_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tracking_events.id"))
    landing_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tracking_events.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Air Ops Status Board support (REQUIREMENTS.md 3.3/AOBD board, added 2026-10-01).
    # Grounded in the CAWG AOBD course's own worked example (ATD 10:20 + ETE 3.2hr ->
    # ETA 13:32): ETA is never typed in directly by an AOBD, it's computed as
    # ATD (engine_start_event) + ETE, so there's no stored "eta" field here, just the
    # ETE an aircrew reports at brief time. Single float hours (not an hours+minutes
    # pair like Ops Check fuel remaining) per Gerry's explicit choice, matching the
    # course's own ".1 = 6 minutes" convention literally rather than this app's usual
    # split-field pattern.
    ete_hours: Mapped[float | None] = mapped_column(Float)
    # AOBD override for the computed ETA (course's overdue-escalation step: "crew
    # called in and extended their ETA") -- takes precedence over
    # engine_start + ete_hours when set.
    eta_override_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Per-sortie override of the >10min-late overdue trigger -- the course's printed
    # board has "OPS NORMAL REQ'D EVERY ___" as a user-fillable blank, not a fixed
    # number. Null means use Settings.default_overdue_threshold_minutes (10).
    overdue_threshold_minutes: Mapped[int | None] = mapped_column()
    # Status-board "Assigned Area" column (course: what grid/route the crew is
    # working, written as a grid # or route name, not a geometry). Free text,
    # mutable like mission_number/pic_name -- not audit-trailed, it's metadata, not
    # a safety-critical timestamp.
    assigned_area: Mapped[str | None] = mapped_column(String(100))

    aircraft: Mapped["Aircraft"] = relationship()
    engine_start_event: Mapped["TrackingEvent | None"] = relationship(
        foreign_keys=[engine_start_event_id], lazy="selectin"
    )
    engine_stop_event: Mapped["TrackingEvent | None"] = relationship(
        foreign_keys=[engine_stop_event_id], lazy="selectin"
    )
    takeoff_event: Mapped["TrackingEvent | None"] = relationship(foreign_keys=[takeoff_event_id], lazy="selectin")
    landing_event: Mapped["TrackingEvent | None"] = relationship(foreign_keys=[landing_event_id], lazy="selectin")
    waypoints: Mapped[list["SortieWaypoint"]] = relationship(back_populates="sortie", lazy="selectin")
    events: Mapped[list["TrackingEvent"]] = relationship(
        foreign_keys="TrackingEvent.sortie_id", order_by="TrackingEvent.event_time_utc", lazy="selectin"
    )


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


class DiscoveredCallsign(Base):
    """A tail/callsign pair seen live on adsb.lol with a CAP- or PARD-shaped
    callsign, not already in `Aircraft` (REQUIREMENTS.md 3.1, added 2026-09-15,
    PARD + AO-scoping added 2026-10-14) -- see scripts/discover_cap_callsigns.py
    (slow 22-point CONUS sweep) and app/ingestion/ao_discovery_poller.py (fast
    poller scoped to the live OperationsArea, for near-real-time "an unregistered
    aircraft entered the AO" alerting on the Ops Board). Per Gerry: "we can also
    capture CAP callsigns and tail numbers from the live feed to populate the
    background list... won't be exhaustive but it will get us more tails to work
    with." / "If an unregistered aircraft comes into the Area of Operations, we
    need to know."

    This is a *candidate* list, not auto-promoted into Aircraft -- a real tail
    matching the CAP/PARD shape almost always is one, but false positives are
    possible (any operator can squawk anything) and Aircraft is the audited
    source of truth other features key off of (roster upload, sortie tracking),
    so promotion (`app/api/discovered.py`'s approve endpoint) is a deliberate
    separate step, not automatic.

    AO membership is NOT stored here -- it's computed at read time in
    app/api/discovered.py from (last_latitude, last_longitude) against the
    current OperationsArea, since storing it would go stale the moment the AO
    moves. `dismissed_at` survives re-sightings (the pollers' upsert never clears
    it), so a declined candidate stays off the alert list even as it keeps
    accumulating sample_count."""

    __tablename__ = "discovered_callsigns"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tail_number: Mapped[str] = mapped_column(String(10), index=True)
    callsign: Mapped[str] = mapped_column(String(20))
    # Which regex alternative matched -- "CAP" or "PARD" (default "CAP" for rows
    # that predate this column). Drives the approve endpoint's AircraftCallsign.label
    # and the Ops Board badge text.
    source_label: Mapped[str] = mapped_column(String(10), default="CAP")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sample_count: Mapped[int] = mapped_column(default=1)
    last_latitude: Mapped[float | None] = mapped_column(Float)
    last_longitude: Mapped[float | None] = mapped_column(Float)
    # Added 2026-10-15 so the map's candidate datablock can show airspeed/altitude
    # instead of the tail number (per Gerry) -- same "ground" alt_baro quirk as
    # app/adsb/adsb_lol.py, normalized to 0.0 before storage here (not left as a
    # string) since this column is plain Float, not Any.
    last_altitude_ft: Mapped[float | None] = mapped_column(Float)
    last_ground_speed_kt: Mapped[float | None] = mapped_column(Float)
    # Added 2026-10-15 per Gerry: "the aircraft icon doesn't show what direction it's
    # moving" -- true track over ground from adsb.lol's `track` field, same field/caveat
    # as Position.heading_deg (app/adsb/adsb_lol.py: "not magnetic heading -- close
    # enough here"). Drives the discovered-candidate marker's rotation on the frontend.
    last_heading_deg: Mapped[float | None] = mapped_column(Float)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("tail_number", "callsign", name="uq_discovered_tail_callsign"),)


class User(Base):
    """A standalone AvTrack account (REQUIREMENTS.md open question 7, added
    2026-10-16 per Gerry's per-user/per-mission request -- IC/Ops/AOBD/COMMS all
    need to log in and share a mission's common operating picture). Login
    identity is CAP ID, not email -- there's no email column here since nothing
    in this app sends mail. No self-service signup: accounts are provisioned by
    scripts/create_user.py, matching this project's existing one-off-admin-script
    pattern (scripts/import_callsign_tails.py etc.) rather than a signup/reset
    flow this app has no mail/SMS infrastructure to build.

    This table and app/auth.py's get_current_user/require_mission_member are
    deliberately the only things that know how a request maps to an identity --
    a future CAP eServices SSO adapter replaces that module, not every route
    that depends on it."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    cap_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Base32 TOTP secret (RFC 6238), generated once at account creation. Login is
    # CAP ID + password + a 6-digit authenticator-app code -- Gerry's explicit
    # choice over email/SMS one-time codes, since this app has no outbound
    # mail/SMS infrastructure to build either of those on.
    totp_secret: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memberships: Mapped[list["MissionMembership"]] = relationship(back_populates="user", lazy="selectin")


class UserSession(Base):
    """A logged-in session. DB-backed (not a stateless JWT) so logout is a real
    DELETE, matching this codebase's generally DB-centric style. Only the
    SHA-256 hash of the cookie's token is stored here -- the raw token lives
    only in the HttpOnly session cookie, so a DB read alone can't produce a
    usable session."""

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Mission(Base):
    """A real, first-class mission -- the scoping entity for a common operating
    picture shared by IC/Ops/AOBD/COMMS (added 2026-10-16 per Gerry: "those
    groups need to be able to see a particular mission(s)"). Replaces the
    free-text Sortie.mission_number as the actual backend concept a user's data
    (aircraft, AO, sorties) is scoped by; `mission_number` is kept here too
    (same CAP YY-X-NNNN free-text convention Sortie.mission_number already used)
    purely for display/default-fill, now backed by a real row.

    `closed_at` is a timestamp, not a bool, matching DiscoveredCallsign's
    `dismissed_at` convention elsewhere in this codebase."""

    __tablename__ = "missions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    mission_number: Mapped[str | None] = mapped_column(String(50))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["MissionMembership"]] = relationship(back_populates="mission", lazy="selectin")


class MissionMembership(Base):
    """Who can see/edit a given Mission's data. Per Gerry, role is descriptive
    only -- "IC"/"Ops"/"AOBD"/"COMMS"/free text, shown on the common operating
    picture -- and is NEVER checked for authorization: "each and every one of
    those could be entering data to track aircraft." Authorization is purely
    "does a row exist here for (mission, user)?" -- see app/auth.py's
    require_mission_member, which is the only place this table's membership
    (not role) is enforced."""

    __tablename__ = "mission_memberships"

    id: Mapped[uuid.UUID] = _uuid_pk()
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str | None] = mapped_column(String(30))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    mission: Mapped["Mission"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="memberships")

    __table_args__ = (UniqueConstraint("mission_id", "user_id", name="uq_mission_membership"),)


class OperationsArea(Base):
    """The single active Area of Operations (AO) -- REQUIREMENTS.md 3.1/3.2's
    AO/AOI concept, promoted 2026-10-14 from pure-frontend (frontend/src/main.js's
    setOpsArea(), driven only by the locate+radius control) to a real backend
    concept so app/ingestion/ao_discovery_poller.py can geofence the fast
    discovery poll against it, and app/api/discovered.py can filter candidates by
    it. Single row by design (the API layer upserts the one row rather than ever
    inserting a second) -- not a per-mission table yet, matching the existing
    locate+radius control's one-at-a-time semantics; a future per-mission AO is a
    separate enhancement, not scoped here.

    Plain lat/lon/radius floats, not a PostGIS Geometry -- the only consumer is a
    point-radius membership check (haversine, see
    app/integrations/adsb_discovery.py), and every other AO-shaped thing in this
    codebase (circleGeoJSON()) already treats it as center+radius, not a stored
    polygon."""

    __tablename__ = "operations_area"

    id: Mapped[uuid.UUID] = _uuid_pk()
    center_latitude: Mapped[float] = mapped_column(Float)
    center_longitude: Mapped[float] = mapped_column(Float)
    radius_nm: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
