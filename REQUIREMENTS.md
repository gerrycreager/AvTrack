# AvTrack — Requirements

## 1. Mission Context

CAP flies >80% of daily USAF Air Tasking Orders, almost entirely CONUS/AK/HI/PR, using
volunteer-owned/operated Cessna 172/182/206 (and T182T/T206H) aircraft. All CAP powered
aircraft carry ADS-B In/Out, making fleet-wide position tracking feasible via commercial
ADS-B aggregators today, with a longer-term goal of direct FAA feed access.

Retired/changed programs (context, not requirements): glider program terminated (cost,
parts availability, non-US manufacture); ARCHER hyperspectral program ended; FLIR program
being reconstituted — refurbished units going mostly onto C206/T206 (some on 182-class),
new-acquisition units expected to be factory-installed by Textron/Cessna on 206-class
aircraft going forward.

## 2. Delivery Phases

- **Phase 1 — Web app**: browser-based map/tracking client, single deployable backend.
  Fastest path to something wings/mission bases can use.
- **Phase 2 — Native clients + cloud server**: lightweight Windows/Mac/Linux desktop
  clients talking to a cloud-hosted (likely CAP IT-operated) server over a pub/sub
  channel, backed by a geospatial database. Server must support many concurrent clients;
  typical client subscribes to <10 aircraft, but must scale to 50+ for large
  training/operations events.

Phase 1 should be built so its core data model (aircraft, positions, flights, logs)
is reusable as the Phase 2 server's schema — not thrown away.

## 3. Functional Requirements

### 3.1 Aircraft Identification & Tracking
- Track CAP and PARD callsigns by default.
- User-extensible: add other callsign prefixes, both long-term (standing) and
  temporary (event-scoped).
- Per-user/per-client list of specific CAP/PARD callsigns or N-numbers to track.
- Maintain a callsign ↔ tail (N-number) mapping table (source: provided by Gerry;
  needs an ingestion/update process — see open questions).
- **A tail can legitimately have more than one callsign (fixed 2026-09-13)**: per
  Gerry, a PARD tasking is a transponder callsign change on the *same* CAP aircraft,
  not a different aircraft — so tail↔callsign is many-to-one in reality, not 1:1.
  `Aircraft.callsign` stores only the routine/primary callsign for quick display;
  `AircraftCallsign` (new table, one row per known callsign a tail answers to,
  labeled e.g. "CAP"/"PARD") is the actual identity-lookup source. Live display
  (datablocks, side panel, sortie panel) was already unaffected by this, since it
  always shows whatever callsign the ADS-B feed currently reports rather than a
  stored value, and the "unlisted aircraft" highlighting was already keyed on
  tail_number, not callsign — the only real gap was `/api/aircraft/resolve` (tracked
  list entry by ident), now fixed to check both tables. No PARD callsign data/import
  path exists yet — `scripts/import_callsign_tails.py` only ever manages the
  "CAP"-labeled entry; PARD callsigns will need their own data source once available.
- Side panel: aircraft currently aloft, filtered to (a) the user's tracked list, or
  (b) any CAP aircraft entering the current operations area. Unlisted/unexpected CAP
  aircraft entering the area are visually highlighted.
- **Climb/descent indicator — shipped 2026-09-13**: a ▲/▼ caret next to the altitude
  figure (datablock and side panel both), ATC-datablock-style. Computed in
  `app/ingestion/poller.py` from consecutive real position deltas (altitude change
  over elapsed time between an aircraft's new position and its previous one, ignored
  if the previous point is >10 min stale), **not** read from any single provider's
  native field — adsb.lol does expose one (`baro_rate`) but FlightAware's track
  response and SWIM/FIXM don't, and per this project's established principle (3.3)
  of not trusting/depending on a single provider-specific field where deriving our
  own works uniformly, this is computed the same way regardless of source.
  ±150 ft/min threshold before calling it climbing/descending rather than "level"
  noise. Verified live against a real descending flight (N359CP, ~-540 fpm).
  Known gap: the initial WebSocket snapshot sent on connect (`ws.py`
  `_latest_known_positions()`) doesn't include trend yet — populates within one
  poll cycle after connecting, not fixed since it's a minor/transient gap.
- *Idea, not yet implemented (discussed 2026-09-13, explicitly "not a hard
  requirement")*: color-code aircraft icons by altitude using a ramp similar to
  ADS-B Exchange's, instead of (or alongside) the current known/unlisted color
  coding. Tradeoff already discussed and resolved: ADS-B Exchange's ramp doesn't use
  red, so red can stay reserved exclusively for the existing unlisted/unexpected-
  aircraft safety signal above — the ramp would apply to everything else. Probably
  lower-value for AvTrack than for a busy ADS-B Exchange-style view, since the
  typical tracked set here is small enough (<10, up to 50 for big events) that the
  datablock's text altitude is usually legible on its own; most useful at the higher
  end of that range.
- *Nice-to-have*: non-CAP traffic in the operations area flagged as a potential
  hazard/deconfliction concern (deferred — large scope, later phase). Discussed
  2026-09-13, not yet implemented — captured here so the reasoning isn't lost:
  - **Proximity criteria (first approximation, per Gerry)**: flag a potential
    conflict when another aircraft is within **5nm horizontal AND 2000ft vertical**
    of a CAP aircraft *simultaneously* — a cylinder test, not "close in either
    dimension" (an aircraft 5nm away at 15,000ft shouldn't trigger just because it's
    laterally nearby). Deliberately generous/inclusive rather than tight: this is
    airspace-awareness for ops/mission base, not a cockpit collision-avoidance
    system — roughly matches how EFB traffic displays (ForeFlight, Garmin) default
    their "nearby traffic" radius (typically 5–15nm / several thousand ft), well
    wider than formal TCAS traffic-advisory or FAA near-midair-collision thresholds
    (low hundreds of feet / a mile or two, but for actively converging traffic in
    the cockpit — a different problem). Given CAP operates mostly at low altitude,
    2000ft vertical is generous enough as-is without scaling by altitude regime.
  - **Visual treatment when flagged**: draw a 5nm-radius red circle around *both*
    the CAP aircraft and the offending traffic, to catch the eye on the map.

### 3.2 Map & Geospatial
- Zoom-to-area by ICAO airport ID, lat/lon, USNG (US National Grid), or MGRS, **with
  an adjustable radius** drawn as an operations-area circle around the point (added
  2026-09-12 per a live-exercise request from KBAK/Columbus Muni). Not yet wired into
  the "aircraft entering the operations area" highlighting logic (3.1) -- currently
  visual only.
- **Airfield icons/labels — toggleable + tiered by zoom (shipped 2026-09-13, revised
  2026-09-14)**: a checkbox (`#labels-toggle`) turns airfield *text labels* on/off
  independent of tiering. Both the icon (dot) layer and the label layer are tiered
  by zoom. First shipped 2026-09-13 as a simplified 2-tier scheme with dots always
  visible regardless of tier ("we can adjust later" per Gerry); revised 2026-09-14
  after Gerry reported the map as noisy ("all the airport icons are noisy... use the
  same logic found on r815 EWMC to display a graduated number of airfields based on
  zoom"). Rather than reuse WxCOP's `airport_tiers_endpoint.py` thresholds (used by
  the 09-13 version, and by coincidence identical to a CAPR 70-1 citation Gerry had
  proposed that turned out not to be an actual regulatory rule — see open question 2
  below), this revision reads **EWMC's actual live logic**
  (`enhanced_weather_map_complete.html` on r815) directly, since that's the specific
  page Gerry pointed at: a 5-tier scheme (tier 1 = military, tier 2 = paved runway
  ≥8000 ft, tier 3 = paved ≥5000 ft, tier 4 = paved ≥2500 ft, tier 5 = everything
  else — unpaved, short, or missing runway data), each tier gated by its own
  `TIER_MIN_ZOOM` (`{1:0, 2:3, 3:7, 4:12, 5:14}` in AvTrack; EWMC's own values are
  `{1:0, 2:3, 3:3, 4:7, 5:12}` but EWMC's tier 2 is a curated ~25-airport list plus a
  METAR-reporting flag AvTrack doesn't have data for, so AvTrack's tier 2 is instead
  computed the same paved/runway-length way as the others — collapsing EWMC's tiers
  2+3 into one, hence AvTrack's numbers are pushed out a bit vs. EWMC's).
  **Deliberate divergence from EWMC**: EWMC's underlying query requires
  `has_paved_runway AND longest_runway_ft >= 2500` for an airfield to appear *at
  all*, at any zoom — unpaved/short strips are permanently excluded there. Gerry
  explicitly did not want that for AvTrack, since small/unpaved fields matter for
  CAP ops, so tier 5 exists to eventually show everything EWMC would drop, just at
  a deeper zoom (14) instead of never. Endpoint (`/api/airfields`) remains
  bbox-filtered (`bounds=west,south,east,north`) and refetches on map pan/zoom
  (debounced) rather than shipping all ~22k airfields in one response.
  **Verification note**: tested via Playwright with a full 1.8s settle at each zoom
  level (shorter waits race the debounced bbox refetch and produce nonsensical
  results — a real pitfall hit twice while testing this). At zoom 6 over a real
  Midwest bbox, rendered dots (64) matched tier1+tier2 from the source data (12+52)
  exactly. At zoom 12/14 over a small-airfield-sparse test point, the *bbox fetch
  itself* returned only 1/0 airfields total — confirming the near-zero render count
  there is the shrinking viewport at high zoom, not the tier filter malfunctioning.
  **Two follow-up fixes the same day, both from Gerry using the live map**:
  - **Dots too small to discern**: `circle-radius` was `["interpolate", ..., 5, 1.5,
    10, 4, 14, 7]` — MapLibre clamps to the first stop's value below its lowest
    zoom, so at the map's own 3.2 startup zoom the dots were rendering at 1.5px.
    Rescaled to `3, 4, 6, 5, 10, 7, 14, 10` and thickened the stroke to 1.5px.
  - **Tier 2 ("major hub") criteria was wrong, not just the visuals**: Gerry:
    "At min zoom (all CONUS where we start) I'd display MIL and major air carrier
    hubs, then add to those," then after seeing the result: "it looks like you've
    got a lot more displayed than just MIL and major hubs." Checked the actual
    data rather than guess: the `paved >= 8000ft` proxy tier 2 had been using
    (see above) rendered **334 airfields nationally**, not major hubs — Craig Fld
    (former USAF pilot-training base), Southern California Logistics (former
    George AFB, 13,052ft), Colorado Springs Muni (13,500ft, long because of
    high-altitude density-altitude, not airline traffic), and 300+ more like
    them. Runway length doesn't imply airline traffic. Replaced with FAA's own
    hub classification (`Airfield.hub_type`, L/M/S/N, from the annual CY
    Enplanements report — `scripts/import_faa_hub_classification.py`,
    upsert-by-`faa_id`/Locid, 394/394 matched cleanly against the existing NASR
    import): tier 2 is now Large + Medium hub (63 airports nationally, CY2024) —
    a real, authoritative source, not a threshold guess. Also moved tier 2's
    `TIER_MIN_ZOOM` from 3 to 0 (paired with tier 1 at the same floor, matching
    "at min zoom... MIL and major air carrier hubs" as the intended baseline
    together, not military-only until zoom 3). Dropped to 244 rendered dots
    CONUS-wide at startup (from 529), all genuinely military or major-hub.
- **Area of Operations (AO) / Area of Interest (AOI) — desirement, not yet built**:
  Gerry wants to be able to identify airfields within an AO and/or a (typically
  larger) AOI specifically for label purposes, selected either by (a) a list-input of
  airfield idents, or (b) selecting airfields directly on the map. This is a
  generalization of the existing single locate+radius circle above (which could become
  "the AO") — needs its own design pass: likely a second independent circle/shape for
  AOI, a persisted "pinned for labeling" airfield set independent of the tier system
  (so a user can force-label a specific small strip inside their AO even if it's
  untiered), and a UI for building that set via list entry or map click-select. Not
  scoped in detail yet -- explicitly a "desirement," not committed.
- **Special Use Airspace (SUA) — shipped 2026-09-12**: Prohibited/Restricted/MOA/
  Warning/Alert/Danger areas, toggleable by group (matching CAP WxCOP's EWMC visual
  convention: red/orange/purple). `special_use_airspace` table, populated by
  `scripts/import_sua_airspace.py`, which pulls the exact same public FAA AIS
  hub.arcgis.com GeoJSON feed CAP WxCOP already downloads weekly for its own map
  (confirmed by reading `/var/www/cap_winds_app/scripts/update_airspace_geo.py` on
  r815 directly) -- **not** a runtime dependency on WxCOP's database (data2,
  192.168.0.60): two independent consumers of the same upstream FAA data. Re-run
  the import script periodically (FAA refreshes this on each 56-day AIRAC cycle;
  WxCOP's version crons it weekly, AvTrack's isn't cron'd yet -- do that once this
  is confirmed useful beyond initial testing). Note: what's in **WxCOP's own
  database** (`observations.national_defense_airspace`, `observations.tfr`,
  `observations.stadium_tfrs` on data2) is TFR data (temporary), a genuinely
  different thing from this permanent charted SUA -- not reused here, may be worth
  a separate live-TFR layer later.
- **Import GIS overlays: Shapefile, KML/KMZ, GeoJSON — shipped 2026-09-12**: for
  ad hoc, operation-specific reference layers (e.g. USAF/CAP intercept-training
  "play areas" for a specific exercise like FERTILE KEYNOTE, or joint-exercise
  practice areas) -- distinct from the permanent SUA layer above. Upload via
  `/api/gis/layers` (multipart), stored in the `gis_layers`/`gis_features` tables
  (already scaffolded in the original schema), rendered as its own map layer with
  a delete control. Parsing uses the system GDAL/OGR `ogr2ogr` CLI (`/usr/bin/
  ogr2ogr`, not a hand-rolled per-format parser or a Python GDAL binding -- Gerry's
  explicit preference 2026-09-12, "why are you handrolling anything when we should
  be able to import shp/kml/kmz") to convert any of the three formats to GeoJSON in
  one step, including reprojection to EPSG:4326 and flattening any Z/altitude
  coordinate (`-dim XY`, needed because real KML commonly carries one). **Gotcha
  found live**: Gerry's miniconda install shadows both the `ogr2ogr` binary AND
  (less obviously) the `PROJ_LIB` env var with an older/mismatched PROJ database --
  the real system GDAL 3.8 binary still failed to reproject until `PROJ_LIB`/
  `PROJ_DATA` were explicitly overridden to `/usr/share/proj` for the subprocess.
  Same shadowing pattern as psql/curl/python3 elsewhere in this project, just via
  an env var instead of PATH this time.
- Aircraft rendered as map icons with live position updates.
- **MGRS precision-box visualization — shipped 2026-09-13**: a truncated-precision
  MGRS/USNG locate query (fewer than 5 digits/axis) now returns and draws the actual
  box that precision level represents, not just a single (possibly misleading)
  point. Real incident that prompted this: Gerry entered `16S EJ` (0 digits/axis,
  just the 100km grid square) expecting to land within ~20nm of a point he knew was
  in that square; it's the SW-corner convention per the MGRS spec, and the actual
  distance was 56.9nm — correct behavior, but surprising without seeing the box
  that makes the ~141km-diagonal uncertainty visible. `app/api/locate.py`'s
  `_precision_box()` computes all four corners via the `mgrs` library's UTM-level
  `MGRSToUTM`/`UTMToMGRS` (each corner independently converted, not a naive lat/lon
  bounding box — UTM grid squares aren't perfectly rectangular once reprojected).
  Box size follows the MGRS digit-count convention exactly (0 digits/axis = 100km,
  1 = 10km, 2 = 1km, 3 = 100m, 4 = 10m; 5 = 1m, where no box is drawn since it
  wouldn't be visually meaningful). Frontend draws a dashed red polygon and uses
  `fitBounds()` instead of a fixed-zoom `flyTo` so the whole box is visible.
  **Real bug found and fixed along the way**: `setOpsArea`/`setMgrsBox` both
  guarded map-readiness with `if (!map.isStyleLoaded()) map.once("load", retry)`.
  `isStyleLoaded()` tracks whether currently-visible *tiles* have finished
  loading, not whether the style is ready for new sources — it can sit at `false`
  for seconds during completely normal use, well after the map's own one-time
  `load` event has already fired. Once past that point, the deferred retry
  registers for an event that will never fire again, and the feature silently
  never appears — confirmed live (Gerry: "I looked and didn't see the MGRS box
  either") before being traced to this. Fixed with a `mapStyleReady` flag set
  exactly once by the map's real `load` event, never rechecked after.

### 3.3 Flight / Takeoff-Landing Logging
- **Do not trust the ADS-B provider's own flight/sortie segmentation as ground truth.**
  Confirmed by direct observation (2026-09, CAP training exercise): FlightAware's flight
  object reported one continuous ~4-hour flight for an aircraft that actually flew 3
  distinct sorties, landing and shutting down the engine for 5–10 min between each.
  Providers apply their own stitching heuristics across gaps and will silently merge
  real separate sorties into one reported flight. AvTrack must derive sortie boundaries
  independently from the raw position/velocity time series (sustained ground speed ≈0
  at/near a known airfield for a threshold duration is a full-stop/engine-off candidate,
  regardless of what the provider's "flight" grouping says), not from provider flight
  IDs or boundaries. This is a harder, more fundamental problem than the touch-and-go
  discriminator above and should be designed/tested first — touch-and-go detection only
  matters within a sortie that's already been correctly bounded.
- **Second confirmed instance of the same failure mode (2026-09-12)**: a live "current
  position" bug (parked, shut-down aircraft at KBAK showing in-flight altitude/airspeed
  in the UI) was first "fixed" by trusting FlightAware's `actual_on`/landed flag as a
  hard signal for "not current anymore" — Gerry caught that this is the same mistake in
  a different spot: FlightAware's own landing detection had already failed on a real
  flight the day before. Corrected in `app/adsb/flightaware.py` to use data freshness
  alone (age of the last track point) rather than any provider-asserted flight-status
  field — a touch-and-go keeps producing fresh points regardless of what `landed` says,
  so it can't be wrongly hidden, while a genuinely parked aircraft's last point stops
  updating and ages out on its own. Worth remembering as a general rule going forward:
  **no field a provider labels as flight status/boundary/landing is trustworthy as a
  hard gate** — freshness/continuity of raw position data is the only thing to key
  logic off of.
- Automatic detection of takeoff and landing at known airfields.
- Heuristic must distinguish full-stop landings from touch-and-go / training patterns
  (not yet designed — flag as an R&D task, not a spec'd feature).
- "NOW" button for manual current-time event logging (engine start/stop, takeoff,
  landing) as a human-in-the-loop override/supplement to auto-detection.
- Engine start/stop times enterable in any designated timezone; stored in UTC.
- Takeoff/landing **times** (and possibly other fields) are user-editable after the
  fact, but the system must retain an immutable underlying tracking log — i.e. edits
  are corrections layered over raw data, not overwrites. This implies an audit trail
  / event-sourcing style log, not just a mutable "flights" row.
- **Sortie tracking — shipped 2026-09-13 (backend only, no UI yet)**: PIC, sortie
  number, mission number, engine start/stop, and repeatable in-grid/out-grid/
  Operations Check waypoints, modeled directly on the RPP recorded-times
  spreadsheet CAP already uses for this (`~gerry/CAP/AvTrack` on r815, see e.g.
  `RPP recorded times - KBAK -- 26-1 example.xlsm`; that file's per-row columns —
  Callsign/Instructor/Sortie/Dest/Eng Start/Wheels Up/Wheels Down/Eng Stop — mapped
  directly to this design, with "Instructor" generalized to a single "PIC" field
  per Gerry: "for the RPP this is 'instructor' but normally it'd be PIC... we can
  go with one 'Pilot in Command'... and let the user adapt for edge cases").
  Design decisions (all confirmed with Gerry before building):
  - **Engine start/stop reuse TrackingEvent/EventEdit** (the existing append-only +
    correction-overlay audit trail), not plain fields on the new `Sortie` table —
    Gerry's explicit preference, consistent with this section's established
    "immutable log, edits layered on top" principle. `Sortie.engine_start_event_id`/
    `engine_stop_event_id` are nullable FKs; `engine_stop_event_id` stays null while
    a sortie is in progress — **a new takeoff/landing/touch-and-go without an
    intervening engine stop is the same sortie, not a new one** (per Gerry).
  - **In-grid/out-grid/Operations Check are a repeatable list** (`SortieWaypoint`,
    one row per check-in), not fixed one-each fields — a sortie can log multiple
    grids searched or periodic ops checks. Each waypoint's *timestamp* also goes
    through TrackingEvent/EventEdit (new `EventType` values `in_grid`/`out_grid`/
    `ops_check`); its *location* fields live on `SortieWaypoint` itself since
    TrackingEvent has no location fields.
  - **Location entry, phase 1 of 2**: lat/lon, MGRS (server-side conversion via the
    `mgrs` package, already a dependency from `app/api/locate.py`), or a free-text
    "named point" (e.g. "over Smithville" — user enters approximate lat/lon
    manually since there's no gazetteer yet). **Deferred**: bearing/radial-from-
    navaid and "recognized FAA fix" lookup both need a reference dataset AvTrack
    doesn't have yet (FAA NASR navaid and fix data, a separate import similar to
    `scripts/import_nasr_airfields.py` / `import_sua_airspace.py`) — build that
    import first if/when those two input modes are wanted.
  - **Operations Check carries extra fields** (2026-09-13, added after the initial
    design): altitude, fuel remaining as separate hours/minutes integers (not a
    parsed "HH:MM" string), and a comments field. `comments` is `Text`, not a
    bounded `VARCHAR` — Gerry: "nominally ~200 varchar but could be [longer]... we
    might need an even larger free-text comment block eventually" — a bounded
    column already caused a real truncation crash once this session
    (`SpecialUseAirspace.times_of_use`), not repeating that mistake here.
  - **Mission number format, unresolved**: per Gerry, CAP mission numbers follow
    `YY-X-NNNN` (2-digit year, a single mission-type designator like T/1/A, 4-digit
    sequence), "per CAPR 70-1 (I think)". Searched CAPR 70-1 (2008 text,
    `~gerry/CAP/Air Operations/R_701_with_ICL_2008_Incorporated...pdf`) and CAPR
    60-3 (`~gerry/CAP/Publications/Regulations/R_603...pdf`, turned out to be
    Cadet Programs, unrelated) — neither contains the mission-numbering scheme
    text, so the exact format/designator list is **not independently verified
    against a located regulation**. `mission_number` is currently a free-text
    field with no format validation at all (not even the soft/advisory check this
    should probably get) — add real validation once the correct source document is
    found, or Gerry confirms the format directly.
  - **API**: `app/api/sorties.py` — `POST`/`GET /api/aircraft/{tail}/sorties`,
    `GET .../sorties/current` (most recent sortie if still in progress, else
    `null`), `POST /api/sorties/{id}/stop`, `PATCH /api/sorties/{id}` (metadata
    only), `POST /api/sorties/{id}/waypoints`.
  - **Frontend UI — shipped 2026-09-13**: opens via the existing
    `openSortiePanel()` click handler (aircraft icon or sidebar row, no new
    click-wiring needed). Start-sortie form (Sortie #/Mission #/PIC, Mission #
    pre-filled from the `localStorage` session default) when no sortie is in
    progress; in-progress view (summary + Stop button + add-waypoint form with a
    location-method selector and conditional ops-check fields + waypoint list)
    once one is. The pre-existing raw NOW-button event log is kept, now tucked
    under a collapsed `<details>` so it doesn't compete with the new primary
    workflow. `window.openSortiePanel` also exposed for devtools/test access,
    matching the existing `window.map` debug-hook pattern. Verified end-to-end via
    Playwright: start → add an in-grid (lat/lon) and an ops-check (MGRS +
    altitude/fuel/comments) waypoint → stop → panel correctly reverts.
  - **Rapid callsign/tail lookup + manual 24hr-clock time entry — shipped
    2026-09-13**, per Gerry's real comms workflow: a radio call gives callsign
    first, in this order (Callsign, Instructor, Sortie #, Destination, Engine
    Start time — the RPP column order again), and the aircraft frequently has
    **no live position yet** when that call comes in — "pilots will often have
    spent some minutes doing initial checks immediately after engine start, and
    in some cases will not call 'til they're airborne and leaving the area."
    Two real gaps this exposed, both fixed:
    - **No way to open an aircraft's panel without clicking a map icon or
      sidebar row** (both require a live position to already exist). New
      `#jump-input` (top-left toolbar, next to the existing locate bar) resolves
      a typed callsign/tail via the existing `/api/aircraft/resolve` endpoint and
      opens the sortie panel directly — `openSortiePanel()` already tolerated no
      live position (falls back to displaying the tail number), so this was
      purely a lookup-and-open gap, not a panel-logic one.
    - **Every timestamp only ever defaulted to "server now"** — no way to log
      the *actual* reported time when it wasn't the literal current moment.
      Added a reusable time-entry field to engine start, engine stop, and every
      waypoint: a free-typed `HHMM` (24hr clock) text field, deliberately **not**
      a native `<input type="time">` (locale-dependent AM/PM rendering can't
      guarantee 24hr display across browsers, and typing into a spinner widget
      is slower than 4 keystrokes during a live radio call) + a "Now" button
      that fills it with the current time (in the panel's selected display
      timezone) for the cases where it genuinely is "right now." Left blank,
      it still means "use server-now," preserving the original quick-log
      behavior for when that's actually correct.
    - **Gap in that fix, found 2026-09-15**: "engine start, engine stop, and
      every waypoint" above left out takeoff/touch-and-go/landing -- those
      stayed NOW-only buttons with no time-entry field, so wheels-up/wheels-
      down could only ever be logged as "whenever I happen to click the
      button," never the actual reported time. Gerry: "I still don't see any
      way to enter wheels up (takeoff) or wheels down (landing) times." Fixed
      by giving the whole raw event-log button row one shared time-entry field
      (same `timeEntryFieldHtml`/HHMM pattern, same "blank = server-now"
      fallback) rather than one field per button, matching how the waypoint
      form already shares a single time field across its 3 waypoint types.
      Backend already supported `event_time_utc` on this endpoint
      (`EventCreateIn`) -- only the frontend never sent one for these event
      types. Verified live: typed `0130`, clicked takeoff, the logged event's
      `effective_time_utc` was exactly `01:30Z`, not server-now.
  - **Standalone session Mission # + per-tail default PIC — shipped 2026-09-13**:
    two related pre-fill conveniences, both feeding the Start Sortie form.
    (1) A "Session Mission #" field in the layers panel, settable *before* opening
    any aircraft's panel — previously the only way to set the session-level
    mission-number default was to open a specific aircraft's Start Sortie form
    first and type it there, per Gerry: "can I start the comms session and enter
    the mission number somehow or does that have to be per-sortie?" Same
    `localStorage` key as before, just exposed somewhere proactive. (2) The
    mission-specific roster upload (3.1 above) now also accepts an optional
    `pilot` or `instructor` column (either name), stored as
    `Aircraft.default_pic_name`, pre-filling PIC for that tail — distinct from
    the per-sortie `pic_name` actually logged, which stays independently
    editable. Roster CSV column matching was also made case-insensitive while
    touching that code — the original version required an exact-lowercase
    header match, which would have silently treated a differently-cased column
    as absent rather than erroring.
  - **End-of-operating-period comms log PDF — shipped 2026-09-13**: per Gerry,
    "missions can span days and there can be several operating periods per
    day" — at the end of one, a PDF of "the effective communications logs with
    the time when every entry was received, allowing several entered at once
    to be noted within the same timestamp, and including any comments."
    `GET /api/reports/comms-log?start=...&end=...` (reportlab, pure-Python, no
    system deps) — every `TrackingEvent` across all aircraft **received**
    (`created_at` — when the row was actually written, which the append-only
    schema already tracked separately from the reported/backdated
    `event_time_utc`) within the range, grouped under one timestamp when
    several land in the same minute (matching real paper comms-log
    convention — the report doesn't repeat the time on every line), each row
    showing aircraft, entry type, effective (reported) time, and any waypoint
    comments. "Operating period" is **not** a stored/named concept — the
    range is picked at generation time, matching how an Incident Commander
    actually declares period boundaries operationally (a live decision), not
    something software should presume to know in advance. Trigger UI: a
    "Comms Log Report" section in the layers panel, `datetime-local`
    start/end pickers (not the rapid HHMM fields used elsewhere — this is a
    deliberate occasional action, not rapid-fire radio logging, and a period
    can span multiple days). **Gotcha found live**: a naive (no timezone
    suffix) start/end input silently produced an empty report instead of an
    error — dangerous for an accountability document, since it reads as
    "nothing happened this period" rather than "your input was ambiguous."
    Fixed by explicitly treating naive input as UTC rather than leaving it to
    whatever the database driver does by default.
    **Mission #/Sortie # added 2026-09-14** per Gerry: "we need to add the
    mission number at the top and the sortie to each entry." Resolves through
    `TrackingEvent.sortie_id` (added the same day for the sortie-linkage fix
    above — a fortunate dependency, not planned together). The report spans an
    arbitrary time range, not one mission, so more than one mission number can
    legitimately appear in a single report (two aircraft on different missions
    in the same operating period) — the header lists every distinct one
    present rather than assuming exactly one. **Real bug caught by Gerry before
    it shipped**: "the entry can be a single line under the header on the top
    of each page" — a plain `Paragraph` in reportlab's `story` list only
    renders once, so on a multi-page log the mission number would have
    vanished after page 1. Fixed by moving the title/mission-number line out
    of `story` and into a `draw_running_header()` callback wired to both
    `onFirstPage` and `onLaterPages` in `doc.build()` — reportlab's actual
    per-page mechanism, verified against a synthetic 7-page log (60 grouped
    entries) that the header and page number repeat correctly on every page.
    **Layout compacted 2026-09-15** per Gerry: "each entry has a lot of extra
    info... these don't have to have all the separation... the current format
    with the heading row for each one is overkill." The per-received-minute
    Heading4 + its own small table-with-header-row (one pair per group) became
    a single continuous table for the whole report — one column header row
    (Date-Time Group/Callsign/Sortie #/Entry/Effective Time/Comments), one line
    per entry, a thin rule between rows instead of a full grid. The
    date-time-group convention (only print it on a group's first row, not
    every row — "allowing several entered at once to be noted within the same
    timestamp") is preserved within the single table rather than dropped.
    Uses reportlab's `Table(..., repeatRows=1)` so the one header row still
    repeats on every page, same idea as the running page header above —
    verified together against a synthetic 2-page log. Same day, Gerry: "Mission
    Number and Operating Period can be at the top of each sheet" — the Period/
    Generated line moved out of `story` (page-1-only) into the same
    `draw_running_header()` callback as the mission line, so both now repeat
    on every page rather than just the first.
  - **Mission-specific roster upload — shipped 2026-09-13**: `POST
    /api/aircraft/roster` (small upload widget in the Aircraft Aloft panel) —
    same CSV shape/upsert logic as `scripts/import_callsign_tails.py`
    (tail_number/callsign/callsign_prefix), but web-exposed and
    **additive-only**: unlike that script, never deactivates aircraft missing
    from the upload. Per Gerry, 2026-09-13: "now that we can see almost any CAP
    aircraft [via adsb.lol], we need to have a way to upload callsigns for a
    specific mission" — and on additive-vs-replace: "for now add/supplement...
    we will eventually get a roster" (i.e. a real mission-scoped roster concept,
    separate from the standing monthly wing-wide list, is a future enhancement,
    not this — this is a lightweight interim tool). Verified live: uploading
    N184CP/CAP3318 and N718CP/CAP418 got them tracked and reporting immediately.
  - **Idea, not yet implemented (Gerry, 2026-09-13)**: rather than only
    manually-curated rosters, passively build one from real traffic — a
    "discovery" feed watching adsb.lol for *any* `CAP\d+`-shaped callsign
    nationally (not just tails already in our Aircraft table, which is all the
    current bulk `/v2/reg/` poll does) over some arbitrary window (Gerry
    suggested 10 days as an example), logging whatever callsign/tail pairs show
    up as candidates. Would need a different query pattern than the current
    per-cycle bulk lookup (e.g. `/v2/callsign/` with a wildcard/prefix, if
    adsb.lol supports one — not yet checked) since this is "find anything CAP-
    shaped" rather than "look up these specific known tails."
  - **Takeoff/landing linked to their sortie + editable after stop — shipped
    2026-09-14**, per Gerry: "we need take-off and landing to a full stop
    associated with a sortie" and "if I end the sortie there's no way to edit
    it?" (both true before this). Two real gaps, confirmed by reading the code,
    not just reported symptoms:
    - `TrackingEvent` had no link to `Sortie` at all except for `engine_start`/
      `engine_stop` (via dedicated FK columns on `Sortie`) — takeoff, landing,
      and touch-and-go were logged as ordinary events tied only to the
      aircraft, with no way to tell which sortie a given one belonged to short
      of guessing from timestamps, which breaks the moment an aircraft flies
      more than one sortie a day (confirmed completely normal from the real
      2026-09-14 WIMRS roster). Fixed: `TrackingEvent.sortie_id` (nullable FK),
      auto-populated from whichever sortie is open on that aircraft when the
      event is created — every event type, not just waypoints (which already
      had this via `SortieWaypoint`). `Sortie` gained `takeoff_event_id`/
      `landing_event_id`, same pattern as `engine_start_event_id`/
      `engine_stop_event_id`. **touch_and_go deliberately does not get its own
      slot** ("touch and go isn't a normal entry" — Gerry) — it's just an
      ordinary event attributed to the sortie via `sortie_id`, since a sortie
      can have zero, one, or several during pattern work. `landing_event_id` is
      overwritten by the *latest* landing logged (not just the first) — covers
      a "stop and go" (a real full stop, immediately followed by another
      takeoff, no engine stop in between) without needing to model multiple
      landings, on the understanding (Gerry: "that's something that could be
      handled in edits") that this system isn't primarily meant to track
      taxi-back/stop-and-go training — "I envision this one as used for
      training missions where there's a lot of maneuvering and some
      touch-and-go landings but really no taxi-back requirements." Migration
      `b3ec32e22ace`.
    - **Once a sortie's engine-stop was logged, there was no way to see or edit
      it again** — `GET .../sorties/current` correctly returns `null` for a
      stopped sortie, but the frontend's only response to `null` was to show
      the *Start Sortie* form, with no path to a completed one. The backend's
      `PATCH /api/sorties/{id}` already supported editing sortie #/mission #/
      PIC regardless of stop state — nothing in the frontend ever called it.
      Gerry: "I'm editing virtually every sortie in this current training
      evolution," so this wasn't a rare edge case, it was the normal workflow,
      silently broken. Fixed: a "Recent sorties" panel (collapsed `<details>`,
      under the Start Sortie form) lists the last 5 completed sorties for that
      tail (`GET /api/aircraft/{tail}/sorties`, filtered to ones with an
      `engine_stop_utc`), each with an Edit toggle wired to the existing PATCH
      endpoint. Takeoff/landing times now also show in the in-progress summary
      and the recent-sorties list. **Real bug found while verifying this
      live**: the NOW-button takeoff/landing buttons only refreshed the raw
      event log (`refreshSortieEvents`), not the sortie summary
      (`refreshSortieInfo`) — so logging a takeoff/landing didn't update its
      own "Takeoff: —" / "Landing: —" display until the panel was closed and
      reopened. Fixed by having `logEvent()` refresh both.
    - **Landing-detection design refined, still R&D** (open question 3 below
      updated with the concrete numbers): Gerry proposed light-GA stall speed
      ≈57kt, groundspeed <40kt sustained 90s, or no ADS-B contact for 3min, as
      the "landed" signal, and asked for literature on how other systems do
      this. Checked the actual ingestion code first: `app/adsb/adsb_lol.py`
      already reads `alt_baro == "ground"` — a direct decode of the aircraft's
      own ADS-B surface-position squitter (when its transponder has a squat/
      weight-on-wheels switch wired in), a known convention in the dump1090/
      readsb family adsb.lol is built on. That's currently only used to zero
      `altitude_ft`, not kept as a signal — it should be the *primary* landed
      signal when present (stronger evidence than any speed threshold, since
      it's the aircraft's own on-ground state, not inferred), with Gerry's
      speed/duration rule as the fallback for installs that don't wire up that
      switch. The "no contact for 3min ⇒ landed" fallback needs a corroborating
      condition (already-low/descending altitude, near a known airfield)
      before trusting it — bare silence during low-altitude maneuvering well
      outside receiver-dense coverage is a normal, frequent event for CAP
      training, not evidence of landing (this is the same crowdsourced-
      coverage-gap point Gerry raised independently, and the same failure mode
      already documented above for FlightAware's `landed` flag).

### 3.4 Weather
- MRMS composite reflectivity and velocity overlay.
- GLM (GOES-R Geostationary Lightning Mapper) strike data overlay.

### 3.5 Data Sources & Redundancy
- ADS-B: agnostic long-term; FlightAware (dev API key already in hand) for initial
  build. Design the ingestion layer behind a provider-agnostic interface so a second
  provider can be added for redundancy without a rewrite.
- **AeroAPI quota clarified 2026-09-13 — it's pay-per-call, not a fixed free quota.**
  Queried AeroAPI's own `GET /account/usage` endpoint directly (no portal login
  needed): lifetime usage on this key is **1,582 calls, $13.33 total cost, 99.3%
  success rate** (1,571 succeeded / 11 failed). Cost breakdown: `/flights/{ident}`
  search ~$0.005/call (789 calls, $3.94, 0 failures), `/flights/{id}/track`
  ~$0.012/call (792 calls, $9.37, 11 failures — the 429s from the earlier pacing
  investigation below). This reframes the 2026-09-12 "consumption cap" theory: the
  429s were a **rate limit** (calls-per-minute-ish), not a quota being exhausted —
  AeroAPI happily keeps billing past whatever the free/included tier is. Since the
  per-call pacing fix (below), confirmed **zero 429s** across 492 calls in the
  current session. What's still unknown: the actual plan's spending cap/included
  credit — `/account/usage` shows spend, not the limit, so that's only visible on
  FlightAware's own billing page. Worth a look given 2+ weeks of exercise still
  ahead. **The 50+-aircraft-per-event scale requirement (section 4) concern below
  still stands regardless** — REST polling cost and cycle time both scale linearly
  with tracked-aircraft count no matter what the actual dollar limit turns out to
  be, which is the more durable argument for FlightAware's push-based products (e.g.
  Firehose) or the SWIM/FDPS path (3.5 below) over tuning interval/caching further.
- **AeroAPI polling paused again 2026-09-13, this time as a deliberate decision, not
  a debugging pause**: Gerry didn't realize AeroAPI was pay-per-call until seeing the
  real $13.33 figure above, and doesn't want to keep incurring cost —
  `ADSB_POLLING_ENABLED=false` in `.env`. **Real operational consequence: AvTrack has
  no live tracking data source at all right now** until either this gets explicitly
  turned back on, or SWIM/FDPS (3.5 below) is actually built and connected — that's
  now the primary near-term goal, not a someday-nice-to-have. Don't re-enable AeroAPI
  polling without an explicit decision to spend more against this key.
- **Full poll-cycle duration is now the real scale constraint in practice, confirmed
  2026-09-12** — a direct consequence of the pacing fix above. With per-call pacing at
  `ADSB_INTER_REQUEST_DELAY_SECONDS` (6s as of this writing) applied before *every*
  AeroAPI call, a full cycle through 22 tracked aircraft measured at **~5.5-6
  minutes** (gap between consecutive position rows for the same tail_number). This
  caused a real bug: the frontend's ghost-aircraft sweep (3.3, `STALE_THRESHOLD_MS` in
  main.js) was set to 5 minutes — *shorter* than the actual cycle time — so it deleted
  every aircraft marker before its next legitimate update could arrive, which looked
  exactly like "no aircraft ever shows" / "aircraft disappeared" and was reported as
  such mid-exercise before the actual cause (a timing mismatch between two numbers
  that both needed tuning together) was found. Fixed by raising the frontend
  threshold to 12 min, but the underlying scale problem remains: cycle time scales
  roughly linearly with tracked-aircraft count × pacing delay, so this will keep
  getting worse as the fleet list grows, and re-confirms the "REST polling doesn't
  scale to 50+ aircraft" concern above independent of the quota/rate-limit question.
  A secondary inefficiency contributing to cycle time: `_resolve_fa_flight_id` forces
  a fresh 2-call search+track every single cycle for any aircraft FlightAware marks
  landed (bypassing the 5-min cache), which is most of the fleet at any given moment
  since most tracked aircraft are grounded most of the time — worth revisiting (e.g. a
  shorter-but-nonzero re-check interval for landed aircraft instead of every cycle) if
  cycle time needs to come down rather than just raising the frontend threshold again.
- **ADS-B coverage is not uniform nationally** — expect real gaps from terrain masking
  (mountainous regions, parts of AK) and low-altitude operation away from
  ground-station/satellite coverage. A coverage gap while genuinely airborne must not
  be misread as a landing; see 3.3 for why a provider's own flight boundary can't be
  trusted to disambiguate this either way (it fails in both directions — false merges
  across real stops, and potential false splits across coverage gaps). Multi-provider
  redundancy (below) improves raw coverage but does not by itself fix flight-stitching
  errors — that requires AvTrack's own sortie-segmentation logic per 3.3.
- **FAA SWIM — researched 2026-09-12, path identified**: Gerry has created a SWIM
  account. Findings (not yet implemented or field-verified against a live
  subscription):
  - **Right service: SFDPS** (SWIM Flight Data Publication Service) — national
    en-route flight tracking derived from ERAM, the right fit for "track aircraft
    nationally." STDDS (terminal/TRACON-local) could supplement near specific
    airports later but isn't the primary target.
  - **Access path: SCDS** (SWIM Cloud Distribution Service, scds.faa.gov), *not* the
    dedicated-network NESG path — FAA's own description calls SCDS "a publicly
    accessible cloud-based infrastructure... a simplified, quick method of accessing
    FAA SWIM data." Self-service signup: create an account, submit a subscription +
    justification, accept Terms & Conditions. No CAP/USAF-auxiliary-specific
    fast path found — appears to be the same process as any other requester.
  - **Connectivity is internet-reachable — no VPN/FTI required**: SCDS is consumed
    over TLS-encrypted TCP (`tcps://host:port`), confirmed against a real open-source
    reference client ([solacese/swim-feed-handler](https://github.com/solacese/swim-feed-handler))
    and again 2026-09-13 against FAA/L3Harris's own official "Consumer JumpStart" kit
    (Gerry downloaded `jumpstart-latest.tar.gz`, extracted at
    `r815:~/CAP/AvTrack/jumpstart/`). This directly resolves the "does SWIM require a
    dedicated government network" concern — it does not, for the SCDS path.
  - **Protocol confirmed: standard AMQP 1.0, not proprietary Solace SMF** — the
    JumpStart kit's `AMQPConsumer.java` connects via
    `org.apache.qpid.jms.JmsConnectionFactory` (Apache Qpid's AMQP 1.0 JMS client),
    plain username/password SASL auth, subscribing to a named queue. This matters a
    lot: it means the Python integration can use `python-qpid-proton` (the reference
    Python AMQP 1.0 library, same Apache project) directly — **no Java, no JMS
    bridge, no Solace-proprietary client library needed.** Meaningfully de-risks
    `app/adsb/sfdps.py`.
  - **Credentials/parameters to hand off once approved** (per the JumpStart README,
    more complete than the earlier guess): `providerUrl` (`tcps://host:port`),
    `queue` (name), `connectionFactory`, `username`, `password`, `vpn` (Solace Message
    VPN name — each SWIM product lives in its own Message VPN). All six are provided
    together when the subscription is created.
  - **Data format — the real cost**: SFDPS delivers **XML (FIXM/AIXM-family aviation
    schemas)** over JMS pub/sub, not JSON. This means genuinely new parsing work, not
    a drop-in replacement for the FlightAware `ADSBProvider` implementation — an
    XML/FIXM parser mapping onto the existing `AircraftPosition` dataclass. Exact
    SFDPS message field names unconfirmed — the authoritative
    [SFDPS Data Consumer Reference Manual](https://nsrr.faa.gov/sites/default/files/enroute-ad-pub-v2/SFDPS%20Data%20Consumer%20Reference%20Manual%20v2.1.4.pdf)
    exists but wasn't successfully fetched; read it once a subscription is live and
    real messages can be tested against it.
  - **Two subscriptions received 2026-09-13** (same SCDS account, same broker
    `ems1.swim.faa.gov:55443`, same connection factory `gcreager.capnhq.gov.CF`,
    same password for both):
    - **STDDS** (Message VPN `STDDS`) — terminal/TRACON-local, as originally expected
      to be a secondary/supplemental source.
    - **FDPS** (Message VPN `FDPS`) — **this is the SFDPS-equivalent national
      flight-tracking service originally targeted.** Confirmed via the SCDS portal's
      own filter list: the subscription includes a "Flight FIXM → Track Message"
      filter set to **1-minute update frequency, ALL flights, ALL operators, ALL
      origin/destination airports** — exactly the nationwide position-track data
      AvTrack needs, delivered as a continuous push feed rather than
      per-aircraft polling. This is a real architectural upgrade over FlightAware
      once working: no rate limits, no per-tail request pacing, no polling interval
      tradeoffs — SWIM pushes updates as they happen. The subscription also includes
      a "Flight FIXM → Flight Plan Information" filter (planning data, not position)
      and an "Airspace AIXM → Special Activities Airspace" filter (MOAs/restricted
      areas etc.) — both potentially useful later but not the immediate target.
    - Credentials for both stored in `.env` as `SWIM_STDDS_*` / `SWIM_FDPS_*`
      (shared `SWIM_USERNAME`/`SWIM_PROVIDER_URL`/`SWIM_CONNECTION_FACTORY`).
  - **Connection resolved 2026-09-13 — root cause was IP allowlisting, not the
    client.** Building a minimal Python client with `python-qpid-proton` required
    chasing down three missing system packages in sequence before TLS even worked
    (`python3.12-dev` for build headers, `libssl-dev` for OpenSSL, then `pkg-config` —
    proton's build silently falls back to a no-op SSL stub if `pkg-config openssl`
    fails, with no error, which is a nasty trap worth remembering). Even with all
    three installed and raw TLS confirmed clean (`openssl s_client` showed a valid
    real FAA cert, full TLS 1.3 handshake), both the Python/proton client *and* the
    official Java JumpStart client hung/silently-closed when run from this dev box
    (cosp1, `209.248.104.165`). Cross-checked by running the **identical, unmodified**
    JumpStart jar from r815 (`209.248.90.253`) instead — **it worked immediately**,
    streaming real live FAA data. Confirms the dev box's egress IP simply isn't
    allowlisted for this subscription; r815's is (not because it was expected to be —
    Gerry's working theory was the IP of wherever the SCDS portal signup happened,
    e.g. airport wifi, but r815 is a static box that predates the signup, so the
    actual allowlist mechanism is still not fully understood, just empirically
    confirmed to key off r815's IP specifically). **Architectural implication**:
    whatever process actually consumes the SWIM feed long-term needs to either run
    from r815's network path, or Gerry needs to get cosp1's IP added to the
    allowlist — this needs a decision before `app/adsb/sfdps.py` gets built for real,
    since it changes where that code has to live relative to the rest of the backend
    (which runs on cosp1 per the earlier "keep load off r815" decision).
  - **Real FDPS data captured and inspected 2026-09-13** (20s capture from r815,
    351,322 lines / ~4,544 unique flight callsigns — genuinely national-scale, not a
    trickle). Confirms:
    - **Not airline-only**, despite the SCDS portal's Flight Operator filter list
      only showing airline names: real GA aircraft types appear in the raw feed
      (`C172`, `C206`, `C150`, `C208`, `BE33`), including a confirmed Cessna 172.
    - **Real field names for the future parser**: callsign is
      `flightIdentification` element's `aircraftIdentification` attribute (e.g.
      `aircraftIdentification="UAL1304"`); operator is
      `operator > operatingOrganization > organization name="..."`; position is
      `enRoute > position` with `altitude`, `actualSpeed > surveillance`,
      `position > location > pos` (lat lon), `trackVelocity > x`/`y`, plus a
      *predicted* `targetPosition`/`targetAltitude` (a freebie AvTrack doesn't get
      from FlightAware). Namespaces: `fixm.aero/base|flight|foundation/3.0` wrapped in
      an FAA-specific `faa.aero/nas/3.0` `MessageCollection`/`FlightMessageType`
      envelope — this NAS envelope is FAA-specific, not pure FIXM, so don't build the
      parser purely from a generic FIXM 3.0 spec without checking against real
      captured messages like this one.
    - **Real, unresolved concern — VFR flights barely represented**: in the same
      20s/4,544-callsign sample, `initialFlightRules="IFR"` appeared 70 times vs. only
      **1** `="VFR"`. The one confirmed GA/C172 example was itself on an IFR flight
      plan (KORD→KSNA). No CAP callsigns appeared in this window at all. Plausibly
      just statistical (CAP is a tiny fraction of national traffic in any 20s window),
      but also consistent with a real structural gap: FDPS derives from ERAM, which
      is fundamentally an IFR traffic-management system — a lot of what CAP actually
      flies locally (touch-and-go patterns, VFR search training, short local sorties
      without a filed IFR plan) may simply never generate an ERAM/FDPS record at all,
      regardless of subscription filter settings. **Not resolved — needs a longer
      capture during a known-active CAP VFR sortie to confirm either way** before
      treating FDPS as a FlightAware replacement rather than a supplement.
    - **STDDS may be the better fit for local VFR coverage, but scope needs
      checking**: the STDDS sample captured earlier had an explicit `<adsb>1</adsb>`
      flag and looked like straight radar/ADS-B surveillance data, not
      flight-plan-derived — which would plausibly include VFR traffic FDPS misses.
      But that sample showed source facility `U90` (Tucson) data, not Indiana/KBAK —
      worth checking whether the STDDS subscription's geographic/facility scope
      actually covers CAP's real areas of operation, or needs reconfiguring on the
      SCDS portal.
  - **Coverage concern effectively confirmed 2026-09-12/13**: deployed the full
    r815 relay pipeline (`avtrack-fdps-consumer.service` + `fdps_relay.py`, see
    `deploy/`) live for a sustained, multi-hour run spanning many log rotations
    (rotates at 100MB, every 1-3 min at national FDPS volume). The relay's
    pre-filter is a broad `"CAP\d+"` regex — it does **not** restrict to
    AvTrack's own 24 tracked tails, it would catch *any* Civil Air Patrol
    callsign nationally. Result: **zero** `"CAP<digits>"` matches across the
    entire sustained run. Independently, at 1955Z on 2026-09-12 Gerry confirmed
    via ADS-B Exchange that ~30 real CAP aircraft were airborne across CONUS at
    that exact moment (none happened to be on AvTrack's own tail list, but that's
    irrelevant to this test — the relay filter isn't tail-specific). Zero FDPS
    matches while 30 CAP aircraft were genuinely flying nationally is strong
    evidence this is a real structural gap, not sampling noise: FDPS derives from
    ERAM (IFR traffic management), and CAP's typical flying (local VFR training,
    touch-and-go patterns, search training, mostly without a filed IFR plan)
    plausibly never generates an ERAM/FDPS record at all, regardless of
    subscription filter settings.
  - **Implication**: FDPS should be treated as unlikely to be a usable primary
    or even supplementary source for AvTrack's actual use case (tracking CAP
    training sorties) until/unless a specific CAP flight is confirmed to appear
    in it. STDDS (terminal/local surveillance, not flight-plan-derived) remains
    the more plausible candidate for VFR coverage but its geographic/facility
    scope needs checking against CAP's real areas of operation (the one STDDS
    sample captured so far was facility `U90`/Tucson, not an area CAP was
    actively flying) — see open item above. Until STDDS is evaluated the same
    way, AvTrack has no confirmed-working live tracking source (AeroAPI is
    paused for cost, FDPS appears structurally unable to see CAP's VFR
    sorties).
  - **STDDS consumer/relay stood up 2026-09-12**, running alongside the FDPS
    ones on r815 (`avtrack-stdds-consumer.service` + `avtrack-stdds-relay.service`,
    `deploy/stdds_relay.py`; separate working directory
    `~/CAP/AvTrack/jumpstart-stdds/` since the JumpStart client's `FileOutput`
    writes to a hardcoded `./log/messages.log` relative to cwd — running two
    instances from the same directory would clobber each other's log).
    **STDDS's real format is TAIS** (Terminal Automation Information System),
    structurally nothing like FDPS's FIXM/NAS: root element
    `<ns2:TATrackAndFlightPlan>`, one message per facility report containing
    many `<record>` blocks (one per tracked aircraft in that facility's
    airspace at that instant), callsign at `<flightPlan><acid>...</acid>`
    (not an XML attribute). First live sample captured facility `<src>BNA</src>`
    (Nashville) — **not** the `U90`/Tucson facility seen in the earlier one-off
    sample, so this subscription is not pinned to a single facility; real
    geographic scope still unconfirmed beyond "more than just Tucson."
  - The relay currently only *detects* (`"MATCH src=... acid=[...]"` in
    `journalctl -u avtrack-stdds-relay`) rather than forwarding to cosp1 —
    `/api/swim/ingest` only knows how to parse FIXM (via `app/adsb/fixm.py`),
    so forwarding TAIS there would just generate harmless-but-noisy parse
    failures with no benefit until STDDS is confirmed useful. If a real CAP
    match shows up, next step is a `parse_tais_positions` parser parallel to
    `fixm.py`, then flip `stdds_relay.py`'s `FORWARD_ENABLED`.
  - **Facility coverage confirmed broad, 2026-09-12**: extracted every distinct
    `<src>` facility code across ~1GB of accumulated STDDS data — **~140 distinct
    facilities nationally**, not scoped to one region as earlier one-off samples
    suggested. Critically, **`IND` (Indianapolis) is present**, along with
    several other facilities near CAP's Indiana operating area (`HUF`/Terre
    Haute, `SBN`/South Bend, `FWA`/Fort Wayne, `EVV`/Evansville, `CVG`/
    Cincinnati). So subscription/facility scope is **not** the blocker for
    seeing CAP1203 (KBAK-area) or CAP2040 — the relevant TRACON is already in
    this feed. Zero CAP matches so far is therefore more likely explained by
    (a) CAP aircraft not being in IND's airspace at the exact sampled moments,
    or (b) — the more likely structural reason — **TAIS only emits an `<acid>`
    for aircraft ATC has flight-plan/beacon-code-correlated** (a discrete
    squawk tied to a flight plan or flight-following request); a VFR CAP
    sortie squawking 1200 with no flight following would still paint as a
    primary/secondary radar target on the scope but would **not** get a
    correlated `<acid>` record in TAIS's output, so it would stay invisible to
    this feed regardless of geographic coverage. Worth confirming with aircrew
    whether they're requesting flight following — if not, that's the actual
    gap, not the SWIM subscription.
  - **STDDS's internal sub-products researched 2026-09-12** (FAA STDDS
    documentation): STDDS bundles four distinct data types — **TAIS**
    (Terminal Automation Information Service — STARS track + flight plan data,
    what we're already consuming), **SMES** (Surface Movement Event Service —
    ASDE-X/ASSC surface/ground movement + OOOI gate events), **TDES** (Tower
    Departure Event Service — TDLS/EFSTS clearance/taxi events), and **ITWS**
    (terminal weather, not aircraft tracking). Of these, **TAIS is the only one
    relevant to airborne position tracking** — SMES/TDES are ground-only, ITWS
    is weather-only. So there is no better *airborne*-tracking option hiding
    inside STDDS; TAIS is already the right choice.
  - **Other SCDS-available SWIM products researched 2026-09-12**: beyond
    SFDPS/FDPS and STDDS, the FAA's broader SWIM catalog includes Traffic Flow
    Management (TFM) Data (TFMS/TFDM — still flight-plan/flow-based, likely
    the same VFR-visibility gap as FDPS), NOTAM Distribution Service (aeronautical
    notices, not position data — not applicable), and ITWS (weather, not
    applicable). No distinct "raw ADS-B, no flight-plan-correlation-required"
    SWIM product was confirmed to exist as a standalone SCDS subscription
    option — surveillance/ADS-B data appears to feed *into* STARS/TAIS rather
    than being separately republished uncorrelated. **Conclusion: FDPS and
    STDDS/TAIS (both already connected) appear to be the full extent of what
    SCDS offers for this use case** — no untried better SWIM product was found.
    If TAIS confirmed-VFR-correlation gap (above) turns out to be the real
    blocker, the fix is operational (CAP aircrews requesting flight following)
    or organizational (asking FAA/SCDS support directly whether an
    uncorrelated surveillance product exists that isn't in the public
    documentation), not a different SWIM subscription to chase.

- **adsb.lol — free, working, coverage gap solved — added 2026-09-12**. Investigated
  as an alternative after the SWIM/TAIS VFR-correlation gap above: since SFDPS/STDDS
  both derive from ATC systems and only surface aircraft with a flight-plan/beacon-
  code correlation, raw crowdsourced ADS-B (the ADS-B Exchange/airplanes.live
  category of source — receiver network, no ATC involvement, no correlation
  requirement) was the obvious thing to check.
  - **airplanes.live itself is gated**, despite its own docs implying open access:
    live-tested from both this host and r815, the real `api.airplanes.live` v2 API
    returns `"Please contact us..."` for actual queries. A user forum thread found
    separately corroborates this — full access appears reserved for feeders
    (people running their own receiver, contributing data back) or manually
    approved users, not a self-serve API like SCDS was.
  - **`api.adsb.lol`, a sister project in the same aggregator lineage, is genuinely
    open** — no signup, no API key, ODbL 1.0 licensed. Same v2 endpoint shape as
    ADSBExchange-family APIs: `/v2/reg/{tail}`, `/v2/callsign/{cs}`,
    `/v2/point/{lat}/{lon}/{radius}`, `/v2/mil`, etc. (Another sister site,
    `api.adsb.one`, hit a Cloudflare IP block from this environment — not tested
    further since adsb.lol already worked.)
  - **Gotcha found live**: httpx's default User-Agent string gets a flat 403 from
    this host's Cloudflare; curl's default UA (or any normal-looking one) passes.
    Not a real auth/rate-limit issue — `app/adsb/adsb_lol.py` sets an explicit
    browser-like `User-Agent` header to clear it.
  - **Gotcha found live #2 — batch, don't loop**: `/v2/reg/` accepts a
    comma-separated list of registrations and returns all matches in one response.
    An initial implementation called it once per tracked tail (mirroring
    `flightaware.py`'s per-tail pattern) and hit a hard, immediate rate-limit wall
    (429s) after just ~2 calls even at ~1s spacing — adsb.lol's real limit is
    undocumented ("dynamic based on environment load", no fixed number, no
    `Retry-After`/`X-RateLimit-*` headers to key off) and apparently much stricter
    than per-request pacing can work around. Rewritten to issue **one bulk request
    per poll cycle** for the whole tracked fleet instead — confirmed this fully
    eliminates the 429s (13/13 and 25/25-tail test batches both succeeded cleanly
    in a single call).
  - **Coverage gap directly confirmed solved, 2026-09-12**: with the poll loop
    live, the very first real result was **N821CP/CAP1121, squawking 1200 (VFR, no
    flight following)** — showing up immediately and correctly on the map. This is
    exactly the traffic FDPS/STDDS structurally cannot see (no flight-plan/
    beacon-code correlation), confirming raw ADS-B was the right category of fix.
  - **Implementation**: `app/adsb/adsb_lol.py` (`AdsbLolProvider`, `name="adsb_lol"`),
    `PositionSource.adsb_lol` added (hand-written migration, same Postgres-enum
    pattern as `swim`). Runs as an **independent poll loop alongside FlightAware**
    (`app/main.py`), gated by its own `ADSBLOL_POLLING_ENABLED` (default true —
    unlike AeroAPI this has no per-call cost, so it doesn't share AeroAPI's
    cost-pause) and `ADSBLOL_POLL_INTERVAL_SECONDS` (default 30) settings.
    `app/ingestion/poller.run_poll_loop()` now takes an optional per-provider
    interval instead of always reading the single global
    `adsb_poll_interval_seconds`, so multiple providers can run on different
    cadences concurrently.
  - **Not yet done**: this has only been proven with one real aircraft over a short
    window, not validated across a whole exercise day the way FlightAware was.
    Worth watching for: coverage gaps in genuinely rural areas with no nearby
    volunteer receiver (raw ADS-B's real weak point, vs. FAA's own radar/receiver
    network), and whether adsb.lol's "dynamic" rate limit ever bites the bulk
    single-call approach at a larger fleet size than tested here (25 tails).

### 3.6 Reliability / Data Management
- Routine, automated database backups (flight data is the thing that must not be lost).

## 4. UX Philosophy (per Gerry, 2026-09-13)

- Start with **broad, varied selection options** exposed directly in the UI rather
  than guessing up front which ones matter most — err toward more controls, not fewer,
  in early iterations.
- Once real usage exists, **passively collect data on what's actually used** (which
  filters, toggles, locate methods, basemap choices, etc. get touched) to inform
  simplification later. Not yet implemented — no usage-telemetry mechanism exists in
  the codebase today; this is a real future requirement (an internal-use-only signal,
  not analytics sent to a third party), not just a vague intention.
- Over time, **reduce/consolidate rarely-used options** or move them behind an
  "Advanced" disclosure rather than deleting them outright — progressive disclosure,
  not feature removal, once usage data justifies it.

## 5. Non-Functional Requirements

- **Scale**: typical client tracks <10 aircraft; must support 50+ aircraft in a single
  operation/event without degrading. Design position update fan-out (pub/sub) around
  this, not around per-client polling.
- **Multi-client**: server must serve many simultaneous clients, each with an
  independent tracked-aircraft subset (implies topic-per-aircraft or geofenced
  pub/sub, not one broadcast firehose).
- **Timezone correctness**: all persisted timestamps in UTC. Display/edit UI **always
  defaults to UTC**; the user may switch to another timezone for their own
  viewing/editing session, but that choice is not persisted across reloads -- a stale
  selection must not silently carry over to a different user/shift on a shared
  browser. (Corrected 2026-09-12 -- an earlier pass defaulted to browser-local time,
  which was wrong for exactly that reason.)
- **Auditability**: raw tracking/event log is append-only; user edits are recorded as
  a distinguishable overlay, not destructive updates.
- **Portability (Phase 2)**: desktop client must run on Windows, macOS, and Linux from
  one codebase — implies a cross-platform toolkit decision (see open questions).

## 6. Development Environment Notes

- Gerry is finishing up work on CAP WxCOP first; sustained development on AvTrack
  picks up afterward on older hardware in his shop.
- Initial prototyping/testing can start now on this current machine in the meantime.
- Implication: keep the dev stack easy to stand up on modest/older hardware too
  (lightweight containers, avoid anything requiring beefy local resources) — consistent
  with the infra-agnostic, portable-components direction above.
- **Process note (2026-09-13)**: requirements are evolving fast and will keep doing so
  — a real 2-week live exercise is surfacing needs faster than any upfront requirements
  pass would have. Agreed approach: keep patching the running Phase 1 prototype through
  the exercise (a mid-exercise rebuild would cost the thing people are actively using),
  but keep this document current in real time so a clean rebuild is *cheap and
  low-risk* once the requirement set settles after the exercise, rather than treating
  the current codebase as precious.
- **Real outage, 2026-09-13**: the cosp1 backend had only ever been started manually
  (`nohup uvicorn ... &`), which doesn't survive a reboot. Gerry rebooted cosp1 for a
  software update; the backend never came back; r815's Apache reverse proxy
  (`deploy/r815-avtrack.conf`) started returning 503 Service Unavailable to the
  public URL — a real user-facing outage, purely from not having this as a real
  service. Fixed with `deploy/avtrack-backend.service` (systemd, enabled +
  `Restart=always`, `Requires=postgresql.service`) — survives both reboots and
  crashes now. Lesson: **any process this project depends on staying up needs a
  real systemd unit from the start**, not a manually-started background process,
  even during active/iterative development — this is the second time in the
  session a manually-run process turned out to matter more than expected (the
  first being the SWIM/FDPS relay, which got a systemd unit from the start;
  the backend itself didn't, until this outage).
- **Also observed same day, unrelated to AvTrack**: r815 load average briefly hit
  ~12-20 (uptime showed a 15-min average around 15) due to WxCOP's own scheduled
  jobs (`satellite_cache_updater.py`, `mrms_cache_updater.py`,
  `ingest_model_site_wx.py --conus-only`) overlapping — the two cache-updater
  scripts showed multiple concurrent instances running at once (started minutes
  apart, earlier ones still alive), consistent with a cron interval shorter than
  the job's actual runtime causing pile-up. AvTrack's own r815 footprint
  (FDPS/STDDS consumers + relays) remained negligible throughout (low single-digit
  % CPU each) — confirmed via `ps aux --sort=-%cpu` at the time. Not an AvTrack
  fix — flagged for whoever maintains WxCOP's cron scheduling, not acted on here.
- **r815 disk space, checked 2026-09-13**: root filesystem (`/dev/sda3`, 938GB) is at
  97% (29GB free) — tight enough to be worth watching now that a persistent
  always-on service (the FDPS consumer, below) runs there. Found 5 additional
  physical disks (`sdb`/`sdc`/`sdd`/`sde`/`sdf`, ~1.4TB combined) with partitions but
  **not currently mounted anywhere**. `/etc/fstab` comments them as
  `#NFS-REPLACED /dev/sdb1 /LDM ...` etc. — these were the local mount points for
  `/LDM`, `/LDM/radar`, `/LDM/models/gfs`, `/LDM/models/hrrr` before WxCOP's NFS
  cutover to `data1` (192.168.0.61), which is what's actually live now (confirmed via
  `df -h`). Strongly suggests ~1.4TB of stale pre-migration LDM data, but **not
  touched, mounted, or reclaimed** — this is CAP weather infrastructure with a
  migration history only Gerry actually understands; needs his explicit confirmation
  before any of that space gets reused for anything, AvTrack included.
- **Future infra plan (per Gerry, 2026-09-13)**: once back from TDY, stand up a small
  dedicated Raspberry Pi 4B to run the SWIM/FDPS consumer + relay instead of r815 —
  removes both the r815 disk/resource pressure and the "borrowing CAP weather infra
  for an unrelated project" concern entirely, at the cost of another physical box to
  maintain. r815 hosting it now is treated as an interim measure, not the permanent
  answer.
- **Alembic set up (2026-09-13)** — resolves the schema-churn symptom of the above:
  `app/db.py` no longer calls `Base.metadata.create_all()` at startup; schema changes
  now go through `alembic revision --autogenerate` + `alembic upgrade head`. Baseline
  migration also repaired a real piece of drift found on first run (a foreign key
  silently dropped by an earlier manual `DROP TABLE ... CASCADE`, never restored).
  Reference/tutorial for Gerry (new to Alembic): `backend/ALEMBIC.md`.

## 7. Open Questions / Decisions Needed

These aren't blocking Phase 1 prototyping, but should be pinned down before the
architecture (especially the Phase 2 server/pub-sub layer) is locked in:

1. **Callsign↔tail data — DECIDED (format + cadence)**: Gerry will provide a CSV,
   re-imported **monthly**. This is *not* a one-time seed — CAP adds/removes aircraft
   from the fleet over time — so build the import as a repeatable job (re-run on a new
   CSV drop each month), not a one-shot migration. Still open: manual re-upload vs. a
   scheduled/automated job, and whether it should eventually sync from a CAP system of
   record (eServices/WMIRS) instead of a flat file.
2. **Known-airfield database — CAPR 70-1 checked, corrects initial assumption**:
   Verified against `/home/gerry/CAP/Publications/Regulations/R_701_with_ICL_2008_Incorporated_1AE7DBFB50E71.pdf`
   (base reg 31 Mar 2020 + ICL 25-03, 7 Jul 2025 — current) and the AK Wing 70-1
   supplement. There is **no** blanket "hard-surface only, ≥2,500 ft" rule for general
   CAP operations:
   - §9.11.2.5.1.1–.5.1.3: any civilian airport in the FAA Chart Supplement, military
     fields (PPR), or wing/higher-approved sites — no surface-type or length floor.
   - §9.11.2.5.1.4: the actual governing rule is **TOLD (Takeoff/Landing Distance)
     verification per aircraft/runway/conditions for each specific operation** — not a
     fixed minimum. So the general known-airfield DB should *not* be pre-filtered to
     hard-surface/2500 ft+; it should cover the full public-use airport set (NASR or
     OurAirports), with TOLD-style suitability computed per-operation later if needed.
   - §9.11.2.5.1.5: **touch-and-go specifically** requires a **hard-surfaced runway
     ≥3,000 ft** (or takeoff+landing roll sum, whichever is greater) — directly useful
     as a pre-filter for the touch-and-go heuristic below (item 3), not for the general
     airfield DB.
   - §9.11.8.5: Simulated Forced Landings to touchdown require hard-surface ≥3,000 ft
     (with a CAP Instructor Pilot aboard) or ≥5,000 ft (without).
   - **Alaska**: the base reg has no Alaska-specific carve-out; the AK Wing supplement's
     real exception is for **water (floatplane) and snow/ski landing surfaces**, not a
     general hard-surface waiver.

   **Source — DECIDED: FAA NASR** (authoritative/FAA-sourced, despite the messier
   format vs. OurAirports which was used for WxCOP).
3. **Touch-and-go heuristic — first draft proposed, with an IP caution**: greenfield
   for CAP's use case. A 2024 US patent (12,304,653, "Aircraft touch-and-go detection")
   exists in this exact space — **the logic below is derived independently from
   first-principles ADS-B semantics (altitude, ground speed, runway geometry) and the
   peer-reviewed MDPI go-around-detection methodology, not from reading or implementing
   that patent's claims.** Nobody on this project should consult the patent text as a
   design source. Before AvTrack ships this feature beyond internal CAP prototyping,
   get CAP legal/IP counsel to review it given the existing patent landscape — that's a
   sign-off gate, not an engineering task. Proposed logic, to be validated against
   recorded CAP sortie tracks before any thresholds are hardcoded:
   1. **Runway match**: track points within ~0.5 nm of a runway centerline, heading
      within ±30° of runway heading; AGL = reported altitude − runway threshold
      elevation (needs the airfield DB's runway geometry/elevation, reinforcing the
      FAA NASR vs. OurAirports decision above). Per CAPR 70-1 §9.11.2.5.1.5, CAP
      touch-and-gos are only legal on hard-surfaced runways ≥3,000 ft — use that as a
      candidate pre-filter on which runways can even produce a true touch-and-go, which
      should cut false positives (a low-AGL/low-speed event at a short/soft runway is
      more likely a genuine full stop or an anomaly, not a training touch-and-go).
   2. **Landing candidate**: AGL descends through ~50 ft with ground speed falling
      toward typical light-GA touchdown speed (55–65 kt for C172/182/206-class).
      Note: ADS-B often has a brief data gap right at touchdown (antenna shadowing) —
      "signal gap near a runway, resumes shortly after" is itself a supporting signal,
      not just noise to ignore.
   3. **Discriminator, evaluated in the ~20–90s window after the low point** —
      refined 2026-09-14 with concrete numbers from Gerry ("assume the stall speed
      for our aircraft is 57 kts. If speed is below 40 kts for 90 sec... they have
      almost certainly landed"), layered with a stronger signal found while
      checking the ingestion code, and one caution on the silence-based fallback:
      - **Primary signal, when present**: `app/adsb/adsb_lol.py` already reads
        `alt_baro == "ground"` from the feed — a direct decode of the aircraft's
        own ADS-B surface-position squitter (requires a squat/weight-on-wheels
        switch wired to the transponder; a known convention in the dump1090/
        readsb family adsb.lol is built on). This is the aircraft's own reported
        on-ground state, not an inferred proxy — stronger evidence than any speed
        threshold, and should decide "landed" outright (sustained across 2+
        updates, to rule out a single bad report) whenever it's available. Not
        currently kept as its own field (only used to zero `altitude_ft`) —
        needs to be.
      - **Fallback signal (installs without a squat switch, or gaps)**:
        groundspeed sustained below ~40kt for ≥90s while at/near a runway is a
        physically sound full-stop signal — 40kt is comfortably below stall
        speed (~57kt reference) for CAP's fleet, so sustained flight at that
        speed isn't possible; 90s is long enough to rule out a touch-and-go's
        brief low-speed moment during the flare (typically re-accelerating
        within 20–30s per the touch-and-go case below).
      - *Touch-and-go*: ground speed re-accelerates past rotation speed (~55–60 kt)
        within ~20–30s, altitude climbs continuously toward pattern altitude
        (500–1000 ft AGL), track stays aligned with runway heading.
      - *Full stop*: ground speed stays at taxi speed (<40 kt) for >90s, and/or
        the track leaves the runway polygon onto a taxiway/ramp, and/or ADS-B stops
        entirely for several minutes with no re-climb (engine shutdown).
      - **Signal loss ≥3min, evaluated with caution**: Gerry proposed "not seen
        for 3 min ⇒ landed at the last visible position" as a fallback for gaps
        in the primary/fallback signals above. Sound in principle, but needs a
        corroborating condition (already low/descending altitude, near a known
        airfield) before it's trusted alone — bare silence during low-altitude
        maneuvering well outside receiver-dense coverage is a normal, frequent
        event for CAP training (crowdsourced ADS-B has no universal coverage,
        per Gerry), not evidence of landing on its own. This is the same failure
        mode already documented above for trusting FlightAware's `landed` flag —
        don't repeat it with silence as the new hard gate.
      - **Out of scope for this system**: "stop and go" (a real full stop,
        immediately followed by another takeoff, no engine stop, no taxi-back)
        is not the primary use case here — per Gerry, "I envision this one as
        used for training missions where there's a lot of maneuvering and some
        touch-and-go landings but really no taxi-back requirements." Misfires on
        that specific pattern are acceptable and correctable via the manual edit
        workflow (see 3.3's sortie `landing_event_id`, which already assumes
        this — latest-landing-wins, not modeled as multiple landings).
   4. **Go-around** (related, distinct case): no low-AGL/low-speed point is reached at
      all — climb resumes from a local minimum altitude, filtered to <2500 ft AGL.

   Design sources actually used: [Go-Around Detection Using Crowd-Sourced ADS-B Position
   Data, MDPI Aerospace 2020](https://www.mdpi.com/2226-4310/7/2/16) (peer-reviewed,
   methodology only); [FAA ADS-B FAQ](https://www.faa.gov/air_traffic/technology/equipadsb/resources/faq).
   US Patent 12,304,653 is noted above only as an IP-landscape awareness flag — its
   claims/specification were **not** read or used to shape this design.
   **Next step**: prototype this logic against recorded FlightAware tracks of known CAP
   touch-and-go sorties to tune the thresholds before wiring up live detection.
4. **Geospatial DB choice — DECIDED**: PostGIS. Gerry is most familiar with it and
   likes its cost model. Phase 1 backend should use PostGIS from the start so there's
   no migration into Phase 2.
5. **Pub/sub technology**: given the infra-agnostic direction, favor a self-hostable
   broker over a cloud-managed one. Leading candidate: MQTT via a self-hostable broker
   (e.g. Mosquitto or EMQX) — lightweight, portable across any host, good desktop-client
   library support on Win/Mac/Linux. Alternative: Postgres LISTEN/NOTIFY or Redis
   Streams if we want one fewer moving part alongside the DB. Not yet decided.
6. **Hosting — DECIDED (deferred by design)**: CAP/IT will choose the cloud
   provider/platform; AvTrack should not assume or lock in a specific one. Build on
   portable, self-hostable components (containerized services, standard PostgreSQL+
   PostGIS, a self-hostable pub/sub broker rather than a provider-managed pub/sub
   service) so the same stack runs on AWS, Azure, GCP, or on-prem/shop hardware
   without a rewrite. Revisit only if CAP/IT states a hard constraint.
7. **Auth/identity**: does client login tie into an existing CAP identity system
   (eServices SSO) or is AvTrack its own user store, at least for Phase 1? Leaning
   toward a pluggable auth layer (standalone accounts for Phase 1, SSO adapter later)
   to match the infra-agnostic approach above — confirm.
8. **Cross-platform client toolkit (Phase 2)**: e.g. Electron/Tauri (web-tech reuse
   from Phase 1) vs. a native-per-platform approach. Tauri would let you reuse a lot
   of the Phase 1 web frontend and keeps the client lightweight, matching the
   "lighter-weight client" goal — leaning this direction but not decided.
9. **MRMS/GLM data access — DECIDED (path)**: not via public NOAA feeds — CAP already
   receives MRMS and GLM over the Unidata LDM (Local Data Manager) protocol on LAN
   hosts `r815`, `data1`, `data2`. Plan: stand up (or extend) an LDM instance to relay
   a feed to the AvTrack server. Checked this system (`/home/gerry/CAP/AvTrack`'s dev
   box) — no `ldmd` currently installed/running here, so an LDM feed isn't available
   locally yet. Gerry can set up a dedicated LDM server for MRMS/GLM when he's back in
   his shop mid-October 2026; until then this data source is deferred for local
   dev/testing (may need a small canned/recorded MRMS+GLM sample dataset to prototype
   the weather overlay against in the meantime). Related local folders already exist:
   `/home/gerry/CAP/Weather/{LDM,MRMS,GLM,Unidata,WxCOP}`.
10. **GIS import formats beyond SHP/KML/KMZ**: worth adding GeoJSON explicitly since
    it's the easiest to round-trip in a web map stack.
