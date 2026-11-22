from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AircraftOut(BaseModel):
    id: UUID
    tail_number: str
    callsign: str | None
    callsign_prefix: str | None
    aircraft_type: str | None
    wing: str | None
    default_pic_name: str | None
    active: bool
    # Display-only wing-name hint derived from the callsign's leading digits
    # (app/integrations/wing_lookup.py, added 2026-10-18) -- NOT a DB column;
    # the API layer sets it as a plain attribute on the ORM object before
    # returning, since Gerry's own framing ("usually but not always") means
    # this must never be treated as authoritative. Null when the callsign
    # doesn't match the usual CAP/PARD-shaped pattern, or its wing can't be
    # determined.
    wing_hint: str | None = None

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
    # Independent tiering for the Station Plot display mode (REQUIREMENTS.md 3.3a),
    # deliberately decoupled from `tier` above so the two schemes can be tuned
    # separately -- see compute_station_plot_tier() in app/api/airfields.py.
    station_plot_tier: int

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
    comments: str | None = None
    edits: list[EventEditOut]


class EventCreateIn(BaseModel):
    """Manual event logging -- the "NOW" button and its non-"now" sibling (backdating
    an event that was missed). event_time_utc defaults to server-now (UTC) if omitted,
    which is what the NOW button actually sends."""

    event_type: str
    event_time_utc: datetime | None = None
    logged_by: str = "unknown"  # no auth yet -- REQUIREMENTS.md open question 7
    # Comms quick-select menu (added 2026-10-26) -- the universal "place to enter
    # the appropriate message" every type gets, per Gerry, even the time-only
    # types (Wheels Up/Down, Touch & Go) that have no other structured field.
    # Stored in TrackingEvent.raw (no dedicated column -- this is the only
    # consumer), unlike SortieWaypoint.comments which has its own Text column.
    comments: str | None = None


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
    pic_cap_id: str | None = None  # authoritative identity for the pilot-edit permission, app/auth.py
    engine_start_utc: datetime | None = None  # defaults to server-now, same as EventCreateIn
    logged_by: str = "unknown"
    # Air Ops Status Board fields (REQUIREMENTS.md 3.3) -- all optional, a sortie
    # started without an ETE simply can't be overdue-flagged on the board.
    ete_hours: float | None = None
    assigned_area: str | None = None
    overdue_threshold_minutes: int | None = None


class SortieStopIn(BaseModel):
    engine_stop_utc: datetime | None = None
    logged_by: str = "unknown"
    comments: str | None = None  # comms quick-select menu's universal message field, added 2026-10-26


class SortieStartIn(BaseModel):
    """Promotes an existing planned (WMIRS-imported) sortie to active by logging
    its engine_start TrackingEvent -- see POST /api/sorties/{id}/start. Same shape
    as the engine-start portion of SortieCreateIn, but against an existing row
    instead of creating a new one."""

    engine_start_utc: datetime | None = None
    logged_by: str = "unknown"
    comments: str | None = None  # comms quick-select menu's universal message field, added 2026-10-26


class SortiePatchIn(BaseModel):
    """Plain metadata edits -- not the audit-trailed start/stop times, see
    Sortie's docstring for why those are different."""

    sortie_number: int | None = None
    mission_number: str | None = None
    pic_name: str | None = None
    pic_cap_id: str | None = None
    ete_hours: float | None = None
    assigned_area: str | None = None
    overdue_threshold_minutes: int | None = None
    eta_override_utc: datetime | None = None
    # Aircraft reassignment (REQUIREMENTS.md 3.3, added 2026-10-19) -- covers both
    # the "AOBD swaps the planned aircraft ahead of time" and "mismatch caught live
    # at first-call" triggers Gerry described. Only legal while the sortie is still
    # `planned` -- see app/api/sorties.py's patch_sortie, 409s otherwise.
    tail_number: str | None = None


class WaypointCreateIn(BaseModel):
    waypoint_type: str  # in_grid / out_grid / ops_check / find / pirep / rtb
    location_method: str  # latlon / mgrs / named_point
    raw_input: str
    # Required for latlon and named_point; resolved server-side for mgrs.
    latitude: float | None = None
    longitude: float | None = None
    time_utc: datetime | None = None
    logged_by: str = "unknown"
    # Per-type fields (REQUIREMENTS.md 3.3) -- the backend accepts any of these
    # for any waypoint_type; the frontend's comms quick-select menu is what
    # actually restricts which fields it shows per Gerry's exact per-type list
    # (app/api/sorties.py's WAYPOINT_TYPES / add_waypoint docstring has it).
    altitude_ft: float | None = None
    fuel_remaining_hours: int | None = None
    fuel_remaining_minutes: int | None = None
    # RTB-specific: estimated time enroute back to base, added 2026-10-26.
    ete_hours: int | None = None
    ete_minutes: int | None = None
    comments: str | None = None
    # PIREP-specific (added 2026-10-26): a real, DB-enforced bound, per Gerry
    # ("PIREPs are usually short and regardless can be condensed") -- unlike
    # `comments` above, deliberately bounded. max_length here gives a clean
    # 422 instead of letting an over-long value reach the DB's own
    # VARCHAR(500) constraint (SortieWaypoint.pirep_report, models.py).
    pirep_report: str | None = Field(None, max_length=500)


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
    ete_hours: int | None
    ete_minutes: int | None
    comments: str | None
    pirep_report: str | None = None


class SortieOut(BaseModel):
    id: UUID
    sortie_number: int | None
    mission_number: str | None
    pic_name: str | None
    pic_cap_id: str | None = None
    engine_start_utc: datetime | None
    engine_stop_utc: datetime | None
    takeoff_utc: datetime | None
    landing_utc: datetime | None
    waypoints: list[WaypointOut]
    events: list[EventOut]
    # Air Ops Status Board fields (REQUIREMENTS.md 3.3). eta_utc is computed
    # server-side (ATD + ETE, or the AOBD override) so the frontend never re-derives
    # the arithmetic itself -- see app/api/sorties.py's _to_sortie_out.
    ete_hours: float | None
    eta_override_utc: datetime | None
    eta_utc: datetime | None
    overdue_threshold_minutes: int | None
    assigned_area: str | None
    # WMIRS import fields (REQUIREMENTS.md 3.3, added 2026-10-19). `status` is
    # computed server-side (Sortie.effective_status()) so the frontend branches on
    # one value instead of re-deriving the start/stop/cancelled null-chain itself.
    status: str  # planned / active / complete / cancelled
    wmirs_sortie_id: str | None
    planned_date: datetime | None
    departure_raw: str | None
    destination_raw: str | None
    cancelled_at: datetime | None


class OpsBoardRow(BaseModel):
    """One row of the Air Ops Status Board -- see app/api/ops_board.py."""

    sortie_id: UUID
    tail_number: str
    callsign: str | None
    aircraft_type: str | None
    sortie_number: int | None
    mission_number: str | None
    pic_name: str | None
    assigned_area: str | None
    atd_utc: datetime | None
    ete_hours: float | None
    eta_utc: datetime | None
    ata_utc: datetime | None
    last_ops_normal_utc: datetime | None
    overdue: bool
    overdue_minutes: int | None
    # Two independent alert conditions (REQUIREMENTS.md 3.3, added 2026-10-14) --
    # see ops_board.py's module docstring for why these are separate from `overdue`.
    last_contact_minutes: int | None
    no_contact_warning: bool  # soft: no contact in 30+10min, independent of ETA
    missing: bool  # hard: overdue AND no contact, both past the same threshold
    state: str  # planned / airborne / ground / not-reporting / complete


class OpsBoardOut(BaseModel):
    generated_at: datetime
    rows: list[OpsBoardRow]


class DiscoveredCallsignOut(BaseModel):
    """One unregistered CAP/PARD-shaped candidate -- see app/api/discovered.py
    and models.py's DiscoveredCallsign."""

    id: UUID
    tail_number: str
    callsign: str
    source_label: str
    first_seen_at: datetime
    last_seen_at: datetime
    sample_count: int
    last_latitude: float | None
    last_longitude: float | None
    last_altitude_ft: float | None
    last_ground_speed_kt: float | None
    last_heading_deg: float | None
    # Same display-only wing-name hint as AircraftOut.wing_hint -- see that
    # field's docstring. Set as a plain attribute on the ORM object before
    # returning, not a real column.
    wing_hint: str | None = None

    model_config = {"from_attributes": True}


class LoginIn(BaseModel):
    """CAP ID + password + a TOTP authenticator-app code -- MFA, per Gerry's
    explicit choice (REQUIREMENTS.md open question 7, added 2026-10-16). See
    app/auth.py."""

    cap_id: str
    password: str
    totp_code: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str
    totp_code: str


class UserOut(BaseModel):
    id: UUID
    cap_id: str
    display_name: str
    # Added 2026-11-02 for app/api/registration.py's admin review routes --
    # the frontend uses this to decide whether to show the admin-requests
    # link at all; require_admin() (app/auth.py) is the real server-side gate.
    is_admin: bool = False

    model_config = {"from_attributes": True}


class MissionOut(BaseModel):
    id: UUID
    name: str
    mission_number: str | None
    created_by: UUID
    created_at: datetime
    closed_at: datetime | None
    # The CALLING user's role on this mission -- not a column on Mission itself
    # (a mission has no single "role"), filled in per-request from the caller's
    # own MissionMembership row. Descriptive only, per Gerry -- see that model's
    # docstring in models.py.
    role: str | None = None


class MeOut(BaseModel):
    user: UserOut
    missions: list[MissionOut]


class RegistrationRequestIn(BaseModel):
    """Self-submitted account request (REQUIREMENTS.md 3.7 follow-up, added
    2026-11-02). See app/api/registration.py and RegistrationRequest's own
    docstring in models.py for the full request-then-approve flow."""

    cap_id: str
    display_name: str
    email: str
    note: str | None = None


class RegistrationRequestOut(BaseModel):
    id: UUID
    cap_id: str
    display_name: str
    email: str
    note: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RegistrationSetupStartOut(BaseModel):
    """What the account-completion page (frontend/complete-setup.html) needs
    to show before the requester has set anything -- deliberately minimal, no
    token/internal ids echoed back."""

    cap_id: str
    display_name: str


class RegistrationSetupIn(BaseModel):
    password: str


class RegistrationSetupOut(BaseModel):
    """Shown once, same as scripts/create_user.py's CLI output. qrcode_data_uri
    is a ready-to-render `data:image/png;base64,...` string -- embedded directly
    here rather than a separate image route, since by the time a separate
    request could fetch one, setup_completed_at would already be set and the
    token's own validity window would have already closed. otpauth_uri is the
    same secret as raw text, for manual entry if scanning isn't an option."""

    otpauth_uri: str
    qrcode_data_uri: str


class MissionCreateIn(BaseModel):
    name: str
    mission_number: str | None = None
    role: str | None = None  # the creator's own role tag on this mission


class MissionJoinIn(BaseModel):
    """Self-service join (POST /api/missions/{id}/join, added 2026-10-18) --
    just an optional role tag, since the joining user is already implicit
    (the caller's own session), unlike MissionMemberIn's add-by-CAP-ID."""

    role: str | None = None


class MissionMemberIn(BaseModel):
    cap_id: str
    role: str | None = None


class MissionMemberOut(BaseModel):
    cap_id: str
    display_name: str
    role: str | None


class WmirsImportOut(BaseModel):
    """Result of a WMIRS "AirSorties" planning export upload -- see
    app/api/wmirs_import.py. `warnings` is deliberately a flat list of
    human-readable strings (missing-aircraft, unresolved-airfield, callsign
    mismatch, and skip reasons all land here) rather than a separate typed list
    per concern -- there's no caller yet that needs to branch on warning type,
    just show them to the importing AOBD/COMMS."""

    created: int
    updated: int
    skipped: int
    warnings: list[str]


class OperationsAreaIn(BaseModel):
    center_latitude: float
    center_longitude: float
    radius_nm: float


class OperationsAreaOut(OperationsAreaIn):
    id: UUID
    updated_at: datetime

    model_config = {"from_attributes": True}
