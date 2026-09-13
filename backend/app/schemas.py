from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AircraftOut(BaseModel):
    id: UUID
    tail_number: str
    callsign: str | None
    callsign_prefix: str | None
    aircraft_type: str | None
    wing: str | None
    active: bool

    model_config = {"from_attributes": True}


class AircraftIn(BaseModel):
    tail_number: str
    callsign: str | None = None
    callsign_prefix: str | None = None
    aircraft_type: str | None = None
    wing: str | None = None
    active: bool = True


class TrackedListIn(BaseModel):
    """A client/user's ad-hoc tracked list (REQUIREMENTS.md 3.1) -- tail numbers or
    callsigns, resolved to Aircraft rows server-side."""

    idents: list[str]


class RosterUploadOut(BaseModel):
    """Result of a mission-specific roster CSV upload (REQUIREMENTS.md 3.1, added
    2026-09-13) -- additive only, never deactivates existing tracked aircraft (per
    Gerry: "for now add/supplement... we will eventually get a roster")."""

    added_or_updated: int
    unusual_suffix_tails: list[str]


class AirfieldOut(BaseModel):
    id: UUID
    icao_id: str | None
    faa_id: str | None
    name: str | None
    latitude: float
    longitude: float
    elevation_ft: float | None
    longest_runway_ft: float | None
    hard_surface_available: bool | None
    is_military: bool
    # 1=military, 2=paved>=8000ft, 3=paved>=5000ft, 4=paved>=2500ft, null=untiered.
    # Computed at query time (app/api/airfields.py), not stored -- see that module's
    # docstring for why these thresholds mirror CAP WxCOP's airport_tiers_endpoint.py
    # rather than a literal CAPR 70-1 citation.
    tier: int | None

    model_config = {"from_attributes": True}


class PositionOut(BaseModel):
    tail_number: str
    callsign: str | None
    ts_utc: datetime
    latitude: float
    longitude: float
    altitude_ft: float | None
    ground_speed_kt: float | None
    heading_deg: float | None


class EventEditOut(BaseModel):
    field_name: str
    old_value: str | None
    new_value: str
    edited_by: str
    note: str | None
    edited_at: datetime

    model_config = {"from_attributes": True}


class EventOut(BaseModel):
    """A TrackingEvent plus its effective (post-edit) time. `event_time_utc` is
    always the original, immutable, auto-detected-or-manual value; `effective_time_utc`
    reflects the latest edit if any -- see REQUIREMENTS.md 3.3 (raw log vs. user
    corrections)."""

    id: UUID
    event_type: str
    event_time_utc: datetime
    effective_time_utc: datetime
    detected_by: str
    confidence: float | None
    edits: list[EventEditOut]


class EventCreateIn(BaseModel):
    """Manual event logging -- the "NOW" button and its non-"now" sibling (backdating
    an event that was missed). event_time_utc defaults to server-now (UTC) if omitted,
    which is what the NOW button actually sends."""

    event_type: str
    event_time_utc: datetime | None = None
    logged_by: str = "unknown"  # no auth yet -- REQUIREMENTS.md open question 7


class EventEditIn(BaseModel):
    field_name: str = "event_time_utc"
    new_value: str
    edited_by: str = "unknown"
    note: str | None = None


class SortieCreateIn(BaseModel):
    """Starts a new sortie -- creates the engine_start TrackingEvent under the hood.
    See REQUIREMENTS.md 3.3."""

    sortie_number: int | None = None
    mission_number: str | None = None
    pic_name: str | None = None
    engine_start_utc: datetime | None = None  # defaults to server-now, same as EventCreateIn
    logged_by: str = "unknown"


class SortieStopIn(BaseModel):
    engine_stop_utc: datetime | None = None
    logged_by: str = "unknown"


class SortiePatchIn(BaseModel):
    """Plain metadata edits -- not the audit-trailed start/stop times, see
    Sortie's docstring for why those are different."""

    sortie_number: int | None = None
    mission_number: str | None = None
    pic_name: str | None = None


class WaypointCreateIn(BaseModel):
    waypoint_type: str  # in_grid / out_grid / ops_check
    location_method: str  # latlon / mgrs / named_point
    raw_input: str
    # Required for latlon and named_point; resolved server-side for mgrs.
    latitude: float | None = None
    longitude: float | None = None
    time_utc: datetime | None = None
    logged_by: str = "unknown"
    # Operations Check-specific -- REQUIREMENTS.md 3.3, ignored for in_grid/out_grid.
    altitude_ft: float | None = None
    fuel_remaining_hours: int | None = None
    fuel_remaining_minutes: int | None = None
    comments: str | None = None


class WaypointOut(BaseModel):
    id: UUID
    waypoint_type: str
    time_utc: datetime
    location_method: str
    raw_input: str
    latitude: float
    longitude: float
    altitude_ft: float | None
    fuel_remaining_hours: int | None
    fuel_remaining_minutes: int | None
    comments: str | None


class SortieOut(BaseModel):
    id: UUID
    sortie_number: int | None
    mission_number: str | None
    pic_name: str | None
    engine_start_utc: datetime | None
    engine_stop_utc: datetime | None
    waypoints: list[WaypointOut]
