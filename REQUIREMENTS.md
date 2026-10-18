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
  **Revised 2026-09-15** — the "tracked list" half of this was never actually
  built (no persistent selection UI ever existed; the sidebar only ever showed
  `latestByTail`, i.e. aircraft currently transmitting, full stop) — per Gerry:
  "we need to see what aircraft are loaded, and if they're airborne. Once a set
  of tails is loaded, it should be displayed." Rather than the original
  aloft-only-filtered-by-a-list design, the sidebar now shows every tail in
  `knownAircraft` (the full `/api/aircraft` roster — 29 tails currently, small
  enough this doesn't need its own separate "tracked subset" concept) at all
  times, each tagged AIRBORNE / ON GROUND / NOT REPORTING
  (`altitude_ft > 0` from a live position = airborne, reusing the same
  provider-normalized "ground" convention already established in
  app/adsb/adsb_lol.py, rather than inventing a new one) — sorted airborne
  first, then on-ground, then quiet. Unlisted/unexpected live traffic (not in
  the roster) still gets the existing red/⚠ treatment, unchanged.
- **Unregistered CAP/PARD aircraft detection + one-click approve — shipped
  2026-10-14**: per Gerry, "If an unregistered aircraft comes into the Area of
  Operations, we need to know. We should be able to also register it with a
  relatively simple click and approve process." Builds on the existing
  `DiscoveredCallsign` candidate table (2026-09-15, see below) rather than
  replacing it: `app/integrations/adsb_discovery.py` now matches both CAP- and
  PARD-shaped callsigns (`^(CAP|PARD)\s?\d+$`, PARD added this date), shared by
  two pollers — the existing slow 22-point CONUS sweep
  (`scripts/discover_cap_callsigns.py`, re-enabled: confirmed disabled/inactive
  on cosp1 at the start of this work despite being documented as running) and a
  new fast in-process poller (`app/ingestion/ao_discovery_poller.py`, 60s
  default) scoped to a newly-persisted **Area of Operations** (`OperationsArea`
  table — the AO circle in 3.2 below was previously frontend-only; the existing
  locate+radius control now also `PUT`s it to the backend, and a page reload
  restores it via `GET`). `GET /api/discovered` lists non-dismissed candidates,
  AO-filtered by haversine distance computed at read time (not stored, so it
  never goes stale as the AO moves). The alert surfaces as a new top section of
  the existing Air Ops Status Board (3.3 below), per Gerry's preference over a
  separate panel — `POST /api/discovered/{id}/approve` creates the `Aircraft` (+
  `AircraftCallsign`, labeled CAP/PARD) row with just tail+callsign, genuinely
  one click, no form; `POST .../dismiss` marks it `dismissed_at` so a declined
  candidate stops reappearing even as the pollers keep updating its
  `last_seen_at`/`sample_count` on further sightings. Verified live: real
  CONUS-sweep candidates correctly AO-filtered in/out as a candidate's position
  was moved relative to the AO; approve/dismiss verified via curl and Playwright
  (Ops Board panel) against isolated fake test tails, cleaned up afterward.
  **Candidates also shown directly on the map — added 2026-10-15**: per Gerry,
  "the aircraft should display on-screen when they are flying. If they're not
  part of our ongoing operation, not accepted from the candidates' table,
  they'd not be in an operations table for query." The Ops Board section above
  stays AO-scoped (operation-specific); this is separate, general situational
  awareness, deliberately unscoped (`GET /api/discovered?ao_filter=false`).
  A candidate's position is only as fresh as the last discovery sweep (never
  true live tracking, since `poll_once()` only ever polls registered tails) —
  shown as a distinct amber/dashed marker (reusing the existing aircraft-icon
  DOM-marker pattern, not the websocket-fed live-position one) as long as its
  last sighting is under 20 minutes old, then dropped. Clicking one opens a
  popup with Approve/Dismiss, same one-click semantics as the Ops Board
  section, sharing the same two endpoints via new `approveDiscovered`/
  `dismissDiscovered` helpers. Verified live against real (not fake) discovered
  candidates nationwide — caught and corrected one real mistake while doing so:
  a test click accidentally approved a genuine candidate (N207CA/CAP3727) into
  the roster, reverted immediately via psql + clearing `dismissed_at` so it
  returned to pending for Gerry's own review, rather than left in the live
  roster as a side effect of testing.
  **Datablock content changed 2026-10-15**: per Gerry, "the data labels
  doesn't need the tail number but does need airspeed and altitude" — the
  candidate marker's second line now shows `altitude_ft`/`ground_speed_kt`
  (same "—" placeholder convention as the known-aircraft datablock) instead
  of the tail number. Required capturing altitude/speed on the backend side
  too: `DiscoveredCallsign.last_altitude_ft`/`last_ground_speed_kt` (new
  columns, migration `b1081d4d9bbc`), populated by both the slow CONUS-sweep
  scanner and the AO-scoped fast poller via the shared
  `app/integrations/adsb_discovery.py` module (`extract_matches()`/
  `upsert_matches()`), with the same `alt_baro=="ground"` → `0.0`
  normalization convention used elsewhere. Note for future debugging: the
  standalone `avtrack-cap-discovery.service` systemd unit runs as a long-lived
  Python process, not re-imported per cycle — it needed an explicit
  `systemctl restart` after this change before new sightings actually carried
  altitude/speed; the in-process `avtrack-backend` poller picked the change up
  automatically on its own already-scheduled restart.
  **Staleness-window bug, found and fixed 2026-10-15**: per Gerry, two real
  aircraft he could see on ADS-B Exchange (CAP161, CAP2165) weren't showing
  on AvTrack's map. Queried adsb.lol directly for both callsigns and
  confirmed both were actively broadcasting at that moment (`seen: 0.0-0.3`
  seconds) — ruling out an adsb.lol coverage gap like the CAP4634/CAP2717
  case earlier the same day. Root cause: `DISCOVERED_STALE_MINUTES` (20) was
  shorter than the slow 22-point CONUS sweep's full-lap time (22 × 90s =
  33min under ideal conditions), so any candidate relying solely on that net
  — i.e. outside the active AO, or with no AO set — was guaranteed to
  flicker off the map for part of every cycle even while continuously
  flying; CAP161 had in fact been sighted 36min earlier by the previous
  (pre-restart) sweep process and simply hadn't been re-sighted yet. Fixed
  by raising the constant to 45min, comfortably over one lap. Separately,
  CAP2165 (Minneapolis) had never been sighted at all as of this report —
  not a bug, just bad timing: `avtrack-cap-discovery` had been restarted
  ~11min earlier (to pick up the altitude/speed columns above) and restarts
  reset the sweep back to grid index 0, so the in-progress lap hadn't yet
  reached the Minneapolis grid point again. Confirmed it appeared once that
  point was swept.
  **Follow-on: the wider window traded flicker for ghost markers — found and
  fixed the same session.** Gerry immediately noticed the reverse case: CAP3226
  showed on AvTrack but not on ADS-B Exchange. Direct adsb.lol query confirmed
  it: 0 results, meaning it had actually landed/stopped broadcasting, but our
  marker — built from a 38min-old sighting — was still well inside the new
  45min staleness window and looked exactly as "live" as a fresh one. This is
  the inherent cost of widening the window: a slow-sweep-only candidate can
  never be *both* flicker-free *and* always current, since the system only
  learns the truth once per lap. Rather than chase a second magic cutoff,
  made the marker disclose its own trustworthiness instead: `discovered-marker`
  opacity now fades continuously with age (`1 - ageMin/DISCOVERED_STALE_MINUTES`,
  floored at 0.35) and the candidate's popup gained a "Last seen Xm ago --
  position/track may be stale, not live" line, so a 40min-old marker reads as
  visibly less certain than a 2min-old one instead of both looking identical.
  **Direction of flight added in the same pass**: per Gerry, "the aircraft icon
  doesn't show what direction it's moving" — candidate markers had never
  captured heading at all. Added `DiscoveredCallsign.last_heading_deg` (adsb.lol's
  `track` field, same "true track, not magnetic" caveat as `Position.heading_deg`
  in `app/adsb/adsb_lol.py`; migration `c1881ce66ec8`) through the same shared
  `adsb_discovery.py` capture path as altitude/speed, and rotate the marker's
  SVG via the same `transform: rotate()` convention the known-aircraft marker
  already uses. Verified the rotation renders correctly with a one-off synthetic
  value on a live candidate row (staging data, not roster data, freely
  correctable) rather than waiting out a full ~33min sweep lap for a real
  heading to land naturally; the next real sighting overwrites it.
  **Root cause actually fixed, not just hidden, 2026-10-15**: per Gerry,
  "According to ADS-B Exchange, CAP161 is reporting regularly, last report
  within 60 sec of NOW" — while AvTrack's own data for that tail still lagged
  by up to a full slow-sweep lap. Queried adsb.lol directly and it already had
  the identical sub-second-fresh data (`seen: 0.0`), proving the gap was
  entirely AvTrack's own poll strategy, not data availability — the slow
  22-point sweep is the right tool for *discovering* a brand-new candidate
  nobody's seen yet, but a bad one for *tracking* a candidate already known,
  since it only revisits any given point once per ~33min lap regardless of
  how often that aircraft itself updates. Added a dedicated fast poller
  (`app/ingestion/candidate_refresh_poller.py`,
  `refresh_known_candidates()` in `adsb_discovery.py`) that re-sights every
  already-discovered, non-dismissed candidate every 30s via the same bulk
  `/v2/reg/` call shape the registered-aircraft poller already uses safely
  (one call per cycle covering every known candidate tail at once, same
  rate-limit-avoidance rationale as `app/adsb/adsb_lol.py`) — matches a
  returned aircraft back to a candidate row only when its current flight
  callsign still matches that row's stored callsign exactly, so a tail now
  squawking something else is left for the slow sweep to catch as its own
  distinct row, same as before. Verified live: CAP161 went from a
  46-minute-old sighting to a 5-second-old one with real altitude (1475ft)/
  speed (102kt)/heading (180°) on the very next cycle; CAP2165 likewise
  refreshed and showed it had landed (0ft, 8.2kt taxi speed); CAP3226 (the
  earlier-confirmed landed/ghost candidate) correctly stayed un-refreshed
  since it no longer appears in adsb.lol's results at all, and dropped off
  the map on its own once stale. With real sightings landing every ~30s
  instead of once per ~33min lap, `DISCOVERED_STALE_MINUTES` also dropped
  back down from 45 to 5 (≈10x the refresh cadence) — the earlier fade/
  "Last seen Xm ago" UI from the previous fix is unchanged, it's just acting
  on much more accurate data now.
  **`known_tails` dedup bug, found and fixed 2026-10-18**: `upsert_matches()`
  in `adsb_discovery.py` (shared by both pollers above) built its "already a
  real Aircraft" skip-list from every `Aircraft.tail_number` regardless of
  `active`, so any tail already present as one of the ~549 `active=False`
  bulk-seeded nationwide reference rows was silently excluded from
  `discovered_callsigns` even while actively flying with a matching CAP/PARD
  callsign — confirmed live with N817CP/CAP3531 near Tulsa, OK, a real flight
  that never appeared as a candidate. Fixed by scoping that query to
  `Aircraft.active.is_(True)`; an inactive tail is now treated exactly like a
  brand-new unknown one, so it gets a `DiscoveredCallsign` row again. No
  change needed in `approve_discovered()` (`app/api/discovered.py`) — it
  already set `aircraft.active = True` on an existing-but-inactive match, so
  approving one of these now-surfaced candidates correctly restores
  visibility.
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
  2026-09-12 per a live-exercise request from KBAK/Columbus Muni). **Backend-persisted
  as of 2026-10-14** (`OperationsArea`, see 3.1's unregistered-aircraft-detection
  entry above) so the AO-scoped discovery poller can geofence against it — the
  locate+radius control now also `PUT`s the AO to the backend, not just drawing it.
  Still not wired into the *already-tracked/registered* "aircraft entering the
  operations area" highlighting logic — that remains a separate, unbuilt gap from
  the unregistered-aircraft detection feature, which only concerns candidates not
  yet in the roster.
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
  - **Mil/civ moved from the dot to the LABEL — changed 2026-10-15**: per Gerry,
    "just like EWMC, Airfield tier (mil vs civ) should be reflected on the
    LABEL color and not a radio button selection. It's toggled with labels."
    Previously mil/civ only showed up if you happened to have the "Tier
    colors" radio option selected on the airfield-display-mode selector (added
    2026-10-14 for Flight Impacts/Station Plot) — picking Flight Impacts or
    Station Plot instead made it disappear entirely. Checked EWMC's actual
    implementation (`enhanced_weather_map_complete.html` on r815) rather than
    guess: there, the dot is colored by flight category (VFR/MVFR/IFR/LIFR),
    and mil/civ is carried *only* by the label — a 5-tier CSS pill
    (`.apt-t1`..`.apt-t5`: military always tier 1/red, civilian tiers 2-5 by
    hub/runway length), applied whenever a label renders, gated only by the
    labels visibility toggle, never a separate "mode." Ported that split:
    `airfields-layer`'s dot is now a flat neutral `#5b6b7a` in the renamed
    "Default" mode (mil/civ no longer lives there at all), and
    `airfields-label`'s `text-color`/`text-halo-color` are a `tier`-keyed
    `match` expression mirroring EWMC's 5 colors, set once at layer creation —
    not touched by `setAirfieldDisplayMode()` — so it's on whenever labels are,
    regardless of which dot-coloring mode is active. First pass approximated
    EWMC's colored pill with a heavier `text-halo-color` since MapLibre
    symbol-layer text has no native CSS background-box property — per Gerry,
    "What we've got for labels might work but isn't currently looking good."
    **Redesigned 2026-10-15** to use the actual native mechanism for this:
    `icon-image` + `icon-text-fit:"both"`. Five small rounded-rect images (one
    per tier, fill+border colors still EWMC's exact `.apt-t1`..`.apt-t5`
    values) are drawn on an offscreen `<canvas>` once at map load
    (`registerAirfieldTierBadges()`) and registered via `map.addImage()`;
    the label layer's `icon-image` is a `tier`-keyed `match` referencing
    them, and `icon-text-fit:"both"` lets GL stretch each badge to the
    rendered text's own bounding box — a true filled/bordered background,
    not an approximation. Verified live (Playwright + `queryRenderedFeatures`)
    against a tier-1 military field (red/white) and tier-2 hub (blue/white):
    both render a solid pill exactly matching EWMC's colors. **Known
    limitation, not yet addressed**: tiers 3-5 use EWMC's light gray/green/
    purple fills, which read clearly on EWMC's own (typically more colorful)
    basemap but are quite subtle against AvTrack's default light USGS Topo
    basemap — worth flagging to Gerry rather than silently living with it,
    since it's a basemap-contrast issue, not a bug in the mechanism itself.
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
    **Start/End fields pinned to UTC — fixed 2026-10-15**: per Gerry, "those
    should be in 24 hr clock, in UTC." The backend-generated PDF itself was
    already always Zulu/24hr; the gap was the input fields, which previously
    followed the app's global `displayTimezone` convenience toggle (defaults to
    UTC, but user-switchable, affecting every other time display in the app) —
    so if that toggle wasn't on UTC, what you typed could silently drift out of
    sync with what the official document actually printed. Now hardcoded to
    `"UTC"` regardless of that toggle, with an explicit "(UTC)" label on both
    fields. The native `datetime-local` widget's on-screen 12h/24h rendering is
    otherwise a browser/OS locale setting outside this app's control; the
    underlying value is already forced 24hr (`hourCycle: "h23"`).
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
  - **Passive CAP callsign/tail discovery — shipped 2026-09-15**, the idea
    proposed 2026-09-13 (directly below, kept for history) now built: per
    Gerry, "we can also capture CAP callsigns and tail numbers from the live
    feed to populate the background list. Won't be exhaustive but it will get
    us more tails to work with. Let's start a multi-day capture... set up a
    timed task." Checked the open question from the original idea first:
    adsb.lol has **no wildcard/prefix callsign search** — confirmed live,
    `/v2/callsign/CAP` returns zero results, it's exact-match only. So this
    instead sweeps a fixed, coarse, non-exhaustive grid of 22 CONUS points
    (biased toward populated/likely-CAP-active metro areas, not a gap-free
    tiling) via `/v2/point/{lat}/{lon}/250`, one point per cycle, filtering
    client-side for a CAP-shaped `flight` field (`^CAP\s?\d+$`). Matches land
    in a new `discovered_callsigns` table (`Aircraft`-shaped candidates, NOT
    auto-promoted into `Aircraft` itself — see that model's docstring) via
    `scripts/discover_cap_callsigns.py`, running continuously as
    `avtrack-cap-discovery.service` (systemd, `enabled`, `Restart=always` —
    same reasoning as `avtrack-backend.service`'s real outage lesson, and
    directly because Gerry flagged r815's daily 0700 UTC reboot as something
    this needs to survive without a manual restart). Paced at 90s between
    queries (adsb.lol's undocumented rate limit is aggressive — the existing
    `/v2/reg/` bulk provider hit a hard 429 wall after just ~2 rapid calls —
    so this trades sweep speed for reliability; a full 22-point pass takes
    ~30min, comfortably fast enough across a multi-day capture). **Verified
    live immediately on startup**: found real new CAP tails not yet in the
    roster within the first two grid cycles (Seattle: N866CP/CAP3666,
    N761CP/CAP3661; a third, N886CP/CAP3686, was already known) — confirming
    both the matching logic and the "gets us more tails" premise work in
    practice, not just in theory.
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

### 3.3a AOBD/OSC Support Element (AvTrack + WxCOP integration, added 2026-10-01)

Per Gerry: "a way to integrate a subset of WxCOP and AvTrack into a support
element for an Air Operations Branch Director [AOBD] and Ops Section Chief
[OSC]." Phase 1 scoped to AOBD specifically (his own priority call); OSC is a
deliberate later phase. Grounded in actual CAP documentation, not generic NIMS
taskbook assumptions, per Gerry's explicit correction: "CAP AOBD doesn't yet
follow the NIMS taskbook."

- **Research sources, and two sourcing corrections (2026-10-14)**: per Gerry,
  "you MUST use national regulations unless there's a Region or Wing
  supplement approved... don't look for a random regulation on the website
  from, e.g., CAWG, and assume it holds nationally." Two things changed
  about the regulation lineage used here as a result:
  1. The live national publications index
     (`gocivilairpatrol.com/members/publications` — explicitly fetchable per
     Gerry, no real access restriction despite the "members" path) still
     listed `CAPR 60-3(I)` (26 Dec 2012) as current when checked directly
     2026-10-14. **But per Gerry directly: "CAP 60-3 is now a Cadet Services
     publication"** — so the Ops/AOBD-relevant lineage is **CAPR 70-1
     (current) → CAPR 70-10 (DRAFT, not yet issued)**, not 60-3(I); citations
     below updated accordingly rather than left on a reg that's moved to a
     different directorate.
  2. `/home/gerry/CAP/Training/AOBD/AOBD_course.pdf` (CAWG, 2002) remains the
     only local source for the Air Ops Status Board's actual layout
     (Assigned Area, the ATD/ETE/ETA/ATA columns) and the overdue-escalation
     workflow shape — **neither CAPR 70-1 nor the 70-10 draft specifies a
     status-board layout at all**, so this is still genuinely wing-local
     practice, labeled as such everywhere it's cited rather than presented
     as a CAP-wide standard. Checked the next-most-authoritative source up
     from wing-local directly, per Gerry — **NESA (National Emergency
     Services Academy) is "deemed the national testbed for new processes
     and procedures within the Mission Aircrew School,"** more authoritative
     than a single wing's course though still not itself a regulation.
     NESA's Mission Aircrew School page (`nesa.cap.gov/about/schools/mas`)
     lists exactly one task guide — "Aircrew & Flightline," filename-dated
     ~Apr 2005 — nothing AOBD/FRO/Air-Ops-Branch-specific. Per Gerry, this
     gap is real and expected, not a sourcing miss: "NESA Task Guides are
     somewhat older, as is 60-3(I), and will benefit from revision over
     time." **This project's AOBD/OSC work is itself headed toward that
     revision** — per Gerry, "what we're working on may be adopted
     nationally, and that is the basis for the collaboration group
     forming" — i.e. this isn't purely implementing an existing standard,
     it may become/inform one, which is worth keeping in mind for how
     carefully design rationale gets documented here.
  `CAPF 104` (Mission Flight Plan/Briefing Form — briefer must be
  AOBD-trainee-or-higher, requires a qualified FRO) is a national form.
  - **CAPR 70-1 (current, 31 MAR 2020 + ICL 25-03) read directly and
    compared against the CAPR 70-10 draft, 2026-10-14** — see the ORM/
    weather-threshold entry below for what's identical vs. what the draft
    actually changes. One real discrepancy worth being precise about: the
    "missing aircraft" follow-up time in the **current, in-effect** CAPR
    70-1 §9.11.3.4.3 is **"no later than two hours"** from ETA, not one hour
    — the 70-10 draft §10.11.4.5.3.1 is the one proposing to tighten this
    to one hour. Gerry's recollection ("that's been the standard... for a
    long time") was about the 1-hour figure specifically; per the actual
    current regulation text, 2 hours is what's been standard, and 1 hour is
    the new proposed tightening. The Ops Board's `default_overdue_threshold_minutes`
    (60) matches the *proposed* draft standard, which is what Gerry asked
    for built regardless — flagged here so the distinction stays traceable
    if 70-10's final issued text changes before it's formally in effect.
  Also researched: required comms calls per **CAPR 100-3** §3.3.2 (Ops Normal
  checks — interval is NCS/CUL-set *locally*, not a fixed CAP-wide number —
  this directly validates the per-sortie `overdue_threshold_minutes` design
  below over a hardcoded constant) and §3.3.1 (roll call), **CAPR 70-1**
  Attachment 2 (Alternate Flight Following check-in schedule, substitutes for
  a filed FAA flight plan when an IC has tracking/comms mechanisms in place;
  also confirms CAPR 70-1's own Sortie definition matches AvTrack's existing
  sortie-boundary logic exactly), and **CAPR 100-1**/100-3 §1.11 (station/
  message log requirement — AvTrack's existing comms-log PDF already serves
  this function, confirmed by regulation text, not assumed). No distinct named
  "departure/wheels-up/landing" call is mandated by either regulation beyond
  what AvTrack's existing EventTypes already cover.

- **Air Ops Status Board — shipped 2026-10-01**: a new live, auto-refreshing
  (`GET /api/ops-board?hours=24`, 20s poll) multi-aircraft board, digitizing
  the CAWG course's own wall board (Sortie #/Tail #/AC Type/Mission #/PIC/
  Assigned Area/ATD/ETA/ATA/Last Ops Normal/status), opened via a new "Ops
  Board" toolbar button, docked as a wide bottom panel (unlike the narrow
  per-aircraft sortie-panel rail, since this is a multi-aircraft glance view).
  Row click opens the existing `openSortiePanel()` rather than duplicating
  edit UI — the board is triage, not where corrections happen.
  - **`Sortie` gained 4 nullable columns** (migration `0f7705ba70e4`):
    `ete_hours` (single float, matching the CAWG course's own ".1 = 6 min"
    convention literally, per Gerry's explicit choice over this app's usual
    hours+minutes split-field pattern), `eta_override_utc`, `assigned_area`,
    `overdue_threshold_minutes`. **No stored ETA or "last ops normal"
    field** — both are computed server-side (`_compute_eta_utc` in
    `app/api/sorties.py`, shared with `ops_board.py`): ETA = ATD (existing
    `engine_start_event`) + ETE, exactly the CAWG course's own worked example
    (ATD 10:20 + ETE 3.2hr → ETA 13:32), verified live to the second against
    real backdated test data. Last Ops Normal = latest of every
    `TrackingEvent` already attributed to the sortie (confirmed `sortie.events`
    already includes engine_start/stop, takeoff/landing, touch_and_go, in/out
    grid, ops_check — every event type gets `sortie_id` set when a sortie is
    open) and the aircraft's latest live `Position`, whichever is newer —
    consistent with this project's established derive-don't-duplicate
    convention rather than another place state can drift.
  - **Overdue trigger**: >N minutes past ETA, still open (no `ata_utc`),
    where N is `Sortie.overdue_threshold_minutes` or
    `Settings.default_overdue_threshold_minutes` — per-sortie overridable
    because CAPR 100-3 confirms the underlying Ops Normal check-in interval
    is genuinely locally NCS/CUL-set, not a fixed national number. **Default
    changed 2026-10-14** from 10 (the CAWG course's wing-local figure,
    initially used for lack of a better source) to **60**, per CAPR 70-10
    draft §10.11.4.5.3.1's national "missing aircraft follow-up time not
    later than one hour after ETA" — Gerry: "that's been the standard for
    CAP and FAA for a long time," confirmed by reading the actual 70-10
    draft (`~/CAP/CAPR 70-10 Complete.pdf`), not assumed.
  - **Two independent alert conditions, added 2026-10-14** — replacing plain
    `overdue`'s visual weight entirely (it's still computed and shown as a
    data point, but no longer itself triggers strong alerting, per Gerry:
    "that's a different issue"):
    - **`no_contact_warning`** (soft — "a slow flashing reminder... orange
      halo"): purely time-since-Last-Ops-Normal, independent of ETA/overdue
      entirely. Fires at `Settings.no_contact_warning_minutes` (30) +
      `Settings.no_contact_grace_minutes` (10) = 40 minutes of silence by
      default — two separate settings, not one number, specifically so the
      grace period can be tuned independently of the base threshold (Gerry:
      "a 10 min grace period on comms beyond the 30 min no contact").
    - **`missing`** (hard — "an alert flashing red"): CAPR 70-10's actual
      compound trigger — overdue past ETA by the threshold **and** no
      contact for that same threshold, both reusing
      `overdue_threshold_minutes`/default 60, not two separate numbers
      (Gerry: "overdue... by an hour AND has not been communicated with for
      an hour"). Verified live with real backdated sorties: a 45-min-silent/
      not-yet-overdue sortie correctly showed `no_contact_warning` only; a
      70-min-silent/70-min-overdue sortie correctly showed `missing` (with
      `no_contact_warning` also true, as expected, since `missing` requires
      a strictly longer silence than the soft trigger).
    - **Visual**: `no-contact-warning` rows get a slow (2.4s) pulsing orange
      inset-box-shadow halo on their cells (`ops-board-halo-orange`
      keyframe); `missing` rows get a hard on/off (0.9s, `steps(2,start)`)
      red background flash (`ops-board-flash-red`) — deliberately different
      animation *character*, not just color, so the two severities read as
      distinct at a glance, not just "more of the same alarm." Applied to
      the `<td>` elements, not the bare `<tr>` — box-shadow/background
      animation on a table row itself isn't reliably rendered cross-browser.
      Text badges ("NO CONTACT 45m" / "MISSING — NOTIFY NOC") carry the same
      information for accessibility/screenshots, not color alone.
    - Sort priority on the board: `missing` > `no_contact_warning` >
      `overdue` > ordinary state ranking — a missing aircraft always sorts
      to the top regardless of its airborne/ground/etc. state.
  - **State naming**: reuses the sidebar's existing `airborne`/`ground`/
    `"not-reporting"` convention exactly (plus `complete`) rather than
    inventing a second naming scheme for the same concept — caught and fixed
    during live verification (first draft used `on_ground`/`not_reporting`,
    which silently broke the `status-${state}` CSS class lookup).
  - **Verified live, not just code-reviewed**: ETA arithmetic exact to the
    second against a real backdated sortie; `overdue`/`overdue_minutes`
    correct and responsive to a live `overdue_threshold_minutes` PATCH;
    `state` transitions correctly to `complete` on stop and drops out of
    overdue; Playwright-confirmed the 20s poll fires on schedule (exactly 1
    extra fetch over a 21s window) and stops cleanly when the panel closes
    (0 fetches over 21s post-close).

- **WxCOP integration — researched live, not yet built**: WxCOP (`/var/www/
  cap_winds_app` on r815, Flask) has a real, callable JSON API, confirmed by
  actually calling it from cosp1 (not assumed from reading source). Key
  findings:
  - **It's CAPR 70-1 / CAPF 70-1A, not "CAPR 70-10."** Gerry raised 70-10
    three times with increasing confidence; checked directly against the
    *live* `weather_impacts_api.py` on r815 each time (most recently modified
    Sep 5, after Gerry's "recently updated" claim) — its own docstring
    consistently reads "CAPR 70-1 Weather Impacts stoplight data." No CAPR
    70-10 exists in any local CAP regulation file either. Proceeding on
    CAPR 70-1 per Gerry's own fallback instruction.
  - **Relevant blueprints** (confirmed via `app.py`'s `register_blueprint`
    calls): `weather_impacts_api` (the CAPR 70-1 stoplight, `GET /airports`
    takes `bounds`/`hour`/`op`/`min_rwy` — bbox order matches AvTrack's own
    `/api/airfields?bounds=` convention), `weather_api` (METAR including
    `flight_category`, mounted `/api/weather`), `weather_enhanced_api` (NDA +
    stadium TFRs only — **general FAA TFRs have no WxCOP endpoint at all**,
    confirmed by grepping every blueprint for `tfr`), `airmet_sigmet_api`
    (confirmed live and working). None of the data-serving blueprints require
    the app-level `auth.py` login (only admin tooling does).
  - **Live connection recipe, confirmed working**: `http://192.168.0.200`
    (r815's LAN IP) with an explicit `Host: wxcop.duckdns.org` header —
    required because Apache does name-based vhost dispatch and r815's TLS
    cert isn't valid for the bare LAN IP, so this sidesteps needing TLS for
    an internal LAN call entirely.
  - **Live infrastructure finding, flagged to Gerry separately from AvTrack
    work**: every DB-backed WxCOP endpoint tested 2026-10-01 returned 500 —
    r815 cannot reach data2 (WxCOP's own Postgres host) at all ("No route to
    host," port 5432). Pre-existing WxCOP-side outage, unrelated to anything
    in this repo.
  - **iframe embedding is blocked by design**: `X-Frame-Options SAMEORIGIN`
    set on every response in `cap_winds.conf`, confirmed live. Rules out
    "just embed the WxCOP page" for anything — on-demand products (METAR
    text/TAF/AIRMET-SIGMET/general radar) become new-tab links instead,
    per Gerry's own direction for EWMC as "a second window."
  - **EWMC already has "Station Plots"** (very recent — dated 2026-09-22 in
    its own code comments): colored by `flight_category` (VFR `#00b300` /
    MVFR `#0099ff` / IFR `#cc0000` / LIFR `#ff00ff`), zoom-tiered, sourced
    from `weather_api`'s METAR endpoints. This changes the original plan —
    Gerry: "the 'dots' are now, in EWMC, a station plot with color
    representing Flight Category... We need another way to code CAPR 70-1
    constraints" — i.e. Flight Category (observed conditions) and Flight
    Impacts (forecast-based Go/Caution/No-Go) are two distinct things Gerry
    wants both shown, and since EWMC's station-plot pattern is earmarked for
    Flight Category, Flight Impacts needs a *different* visual encoding on
    AvTrack's airfield layer — **not yet designed, open item below**.

- **CAPR 70-10 (DRAFT) confirmed real and read directly, 2026-10-14** —
  `~/CAP/CAPR 70-10 Complete.pdf`, found after Gerry pointed to
  `~/CAP/Regulations` (actually empty; the file was directly under `~/CAP/`).
  States "Supersedes: CAPR 70-1, 31 MAR 2020" in its own header — Gerry
  was right all along that 70-10 was coming and would supersede 70-1; every
  earlier correction this session was accurate for the then-current live
  WxCOP code, not a refutation of what Gerry was tracking. Note for future
  precision: CAPF 70-1 ("Pre-Flight Risk Assessment Worksheet," a form)
  is a different thing from the now-superseded CAPR 70-1 (the
  regulation) — easy to conflate, kept distinct in this doc going forward.
  Key content, compared directly against current CAPR 70-1 §9.11.3.4
  (same local file already cited for the Alternate Flight Following
  procedures above), not yet acted on beyond the overdue-threshold change:
  - **ORM score release-authority bands — new to the 70-10 draft.**
    §10.11.4.2: FRO 0-14, SFRO 15-29, SFRO + Wing CC/CD/DO concurrence
    30-59, CAP/DO or designee via NOC greater than 60. Current CAPR 70-1
    §9.11.3.4.2 does **not** hardcode these numbers in the regulation text
    — it says CAP/DO publishes the score ranges separately in WMIRS, so
    this specific set of numbers can only be verified against the draft,
    not independently against the current regulation's own text.
  - **IFR release weather thresholds — unchanged between current 70-1 and
    the 70-10 draft**, confirmed by reading both directly: any FRO may
    approve >= 800ft ceiling AND >= 2SM visibility (or approach minimums,
    whichever higher) [70-1 §9.11.3.4.8.1 = 70-10 draft §10.11.4.7.3]; SFRO
    required for < 800-2 but >= 500-1 [§9.11.3.4.8.2 = §10.11.4.7.4]; SFRO +
    Wing CC/CD/DO concurrence required down to FAA minimums
    [§9.11.3.4.8.3 = §10.11.4.7.5]. Safe to use as the Flight Impacts
    feature's Go/Caution/No-Go breakpoints regardless of which regulation
    is technically in force by the time that ships — not yet compared
    against WxCOP's own `ingest_glmp_impacts.py` thresholds, worth checking
    before building the airfield-coloring feature.
  - **Runway-length vs. TOLD — also unchanged**: any FRO may approve when
    departure runway >= calculated takeoff+landing ground roll; SFRO
    required when shorter [70-1 §9.11.3.4.7 = 70-10 draft §10.11.4.6].

- **Flight Impacts & Station Plot airfield display modes (shipped 2026-10-14).**
  `app/integrations/wxcop_client.py` (the previously-planned WxCOP client) now
  exists, backing `GET /api/weather/impacts` and `GET /api/weather/metar`
  (`app/api/weather.py`) — both same-origin proxies, always HTTP 200 with an
  `available` flag, since WxCOP/data2 being unreachable is a real, expected
  state to design for (data2 had an extended outage starting 2026-10-14, back
  up the same day but mid-restart for a software update). The airfield layer
  now has a single mutually-exclusive display-mode selector (Gerry's explicit
  call — these don't stack): **Tier colors** (unchanged default), **Flight
  Impacts** (recolors airfields by WxCOP's pre-computed Go/Caution/No-Go
  `color`, grey fallback for unmatched/military/unreachable), **Station
  Plot** (a METAR station plot — wind barb, sky cover, temp/dewpoint,
  ceiling/visibility, flight-category color — rendered as a MapLibre DOM
  marker per airfield, modeled directly on WxCOP's EWMC feature per Gerry's
  request). Flight-category colors (VFR/MVFR/IFR/LIFR) are WxCOP's exact
  values, confirmed by Gerry to match the USAF/ForeFlight/likely-FAA
  convention, not just a WxCOP-specific choice. Station Plot uses its own
  independent zoom-tiering (`station_plot_tier`, `compute_station_plot_tier()`
  in `app/api/airfields.py`) rather than reusing the existing tier-colors
  `tier` field — deliberately decoupled per Gerry, with default thresholds set
  lower (more detail at a more zoomed-out view) than EWMC's own, since AvTrack
  is typically opened already scoped to one wing (state-sized) or occasionally
  a region (several wings), not CONUS-wide like WxCOP has to support — not yet
  tuned against real field use. **Note on how this feature was scoped**:
  unlike the TRACS research the same day (see the clean-sheet-design memory),
  Gerry explicitly asked for this one to be modeled on WxCOP's own EWMC
  feature, which he iteratively designed himself — so layout deviations from
  the textbook synoptic station-plot (barb overlapping the sky-cover symbol,
  gust always in fixed hazard red, no on-plot altimeter) were carried over
  faithfully as his own deliberate choices, not something to "fix."
  Verified live via Playwright against both the real (currently reachable)
  WxCOP instance and a mocked unavailable response — both display modes and
  the graceful-degradation path confirmed working with zero console errors.
- **Click an airfield to see its METAR/TAF — shipped 2026-10-15.** Per Gerry:
  "like EWMC we need to be able to click on an airfield and see its METAR and
  TAF if available." Read EWMC's actual click handlers on r815
  (`enhanced_weather_map_complete.html`) rather than inventing a new split:
  EWMC uses two different popups depending on mode — `showNearestTafPopup`
  when Station Plot is active (the METAR is already drawn on the marker
  itself, so the click only needs to add TAF — not necessarily the clicked
  station's own, since most small GA fields don't have one, hence "nearest"
  via WxCOP's `/api/weather-enhanced/taf/nearest` KNN lookup) and
  `showStationPopup` everywhere else (full METAR + TAF detail, since nothing
  else on screen already shows the METAR — via WxCOP's
  `/api/weather/station/<id>`, which already has its own KQ-association/
  host-station TAF-fallback logic server-side, not re-derived here). Ported
  both verbatim: two new same-origin proxy endpoints
  (`GET /api/weather/station/{id}`, `GET /api/weather/taf-nearest`,
  `app/api/weather.py` + `app/integrations/wxcop_client.py`, same
  always-200-with-`available`-flag convention as the other two weather
  routes), wired into both the `airfields-layer` click (full detail) and the
  Station Plot marker click (nearest TAF, or full detail as a fallback for a
  no-METAR marker). Verified live: KIND showed real METAR+TAF text in the
  full-detail popup; a Station Plot full-plot marker showed the correct
  nearest-TAF text.
  **Two real bugs found and fixed while verifying this live** (both pre-existed
  this feature, just newly exposed by testing it): (1) `renderStationPlotMarkers`
  had no per-row error isolation — a single uncaught exception partway through
  its loop silently killed every marker from that point on, with no visible
  error, which is very likely the actual cause behind Gerry's separate
  "station plots don't have any text data" report the same session (intermittent,
  since it depended on exactly which row triggered it); now wrapped per-row in
  try/catch so one bad row can't blank the whole display. (2) The new Station
  Plot TAF popup never appeared at all (not even a loading flash) until a fix:
  MapLibre's `Popup` defaults to `closeOnClick: true`, and a DOM Marker's click
  bubbles up through the map container MapLibre itself listens on for that
  close-on-click behavior, so the very click that opened the popup immediately
  closed it again — needed `e.stopPropagation()`, the same pattern the existing
  discovered-aircraft marker click handler already used (a reusable lesson:
  any new MapLibre Marker click handler that opens a Popup needs this, it's not
  optional).
  **Separately traced (not fixed — lives in WxCOP's own codebase on r815, a
  sibling system outside this repo) a real root cause behind the bulk Station
  Plot text sometimes not loading at a normal regional zoom**: WxCOP's
  `/api/weather-enhanced/metar/recent` endpoint hangs indefinitely for some
  medium-sized regional bounding boxes specifically, confirmed by querying it
  directly (bypassing AvTrack's backend entirely) — a huge CONUS-wide bbox and
  a tiny single-airport bbox both respond in low single-digit seconds, but
  (e.g.) a ~4°×2° Indiana-area bbox never returns at all regardless of result
  `limit` (tested down to `limit=50`), while WxCOP's *other* endpoint
  (`/api/weather-impacts/airports`) answers the identical bbox in ~1.4s. Read
  the query in `weather_enhanced_api.py` on r815: a `DISTINCT ON (station_id)`
  CTE joined to `observations.airports`, filtered by
  `ST_Intersects(m.location::geometry, {bbox})` — the `::geometry` cast inside
  the predicate is a plausible culprit if `observations.metar.location` is
  indexed as `geography` rather than `geometry` (a geography-typed spatial
  index may not get used once the column is cast away from its indexed type
  for every row), but this wasn't confirmed against the live query plan and
  could equally be data2 host load/instability (data2 was confirmed down for
  about a week starting around this time, see the WxCOP outage note above) --
  flagged to Gerry as a WxCOP-side issue to investigate there, not fixed here.
- **Explicitly not yet built** (scoped, not forgotten):
  - User-selectable airfield pinning/highlighting, independent of the
    existing zoom-tier auto-reveal — per Gerry: "if the auto-expose as we
    zoom in isn't sufficient, the AOBD or OSC signed on to use this should be
    able to identify specific airfields they will want highlighted." Revives
    the previously-deferred AO/AOI "pinned for labeling" idea with a concrete
    use case.
  - EWMC-as-second-window button, and on-demand new-tab links for METAR/TAF/
    AIRMET-SIGMET/radar.
  - OSC board / ICS-220 generation — separate future phase.
  - True proximity/deconfliction (5nm/2000ft cylinder test) — unrelated
    pre-existing deferred idea, not folded into this work.

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

### 3.7 Per-User / Per-Mission Accounts (added 2026-10-16)

AvTrack started single-tenant/no-login: one shared aircraft roster, one global
Area of Operations (AO), one shared pool of sorties/events, visible identically to
anyone who could reach the box. Per Gerry: *"We started with tracking, added the
concept of communications. Now it's becoming obvious that assisting Civil Air
Patrol incident management team members, specifically Air Operations Branch and
Operation Section, as well as communications will be useful. All those groups
need to be able to see a particular mission(s) so there is a common operating
picture for air operations."* One person may be on several missions at once, each
with its own aircraft and AO.

**Decisions (resolves open question 7 below):**
- **Auth**: standalone AvTrack accounts (not CAP eServices SSO, at least for
  Phase 1), behind a swappable `app/auth.py` layer so an SSO adapter can replace
  just that module later without reworking every route. Login identity is **CAP
  ID**, not email (no email column/infra exists). MFA is required: CAP ID +
  password + a TOTP authenticator-app code — Gerry's explicit choice over
  email/SMS one-time codes, since this app has no outbound mail/SMS
  infrastructure to build either on. No self-service signup: accounts are
  provisioned by `scripts/create_user.py` (prints a scannable enrollment QR
  code), matching this project's existing one-off-admin-script pattern.
- **`Mission` is a real, first-class entity** (not just the free-text
  `Sortie.mission_number` string), with `MissionMembership` recording who's on
  it. Per Gerry: *"Any of those four areas can set up a Mission... and can add
  aircraft not originally on the list if they come into the AO"* — any logged-in
  user can create a mission and add members (by CAP ID), no role restricts
  either action.
- **Role is descriptive only, never a permission gate.** There's a real
  hierarchy (IC > Ops > AOBD > COMMS), but per Gerry: *"each and every one of
  those could be entering data to track aircraft."* A role tag is stored per
  person-per-mission for display, but authorization is binary — membership in
  the mission, nothing finer-grained.
- **"Sites" and "Area of Operations" are the same concept.** Per Gerry: *"An AO
  might be around a possible incident site or might be a radius around an
  airfield for, say, a training exercise. Define by location/radius using MGRS
  or lat/lon, or GARS."* No separate "sites" entity — the existing center+radius
  `OperationsArea` (3.1/3.2) becomes per-mission, with GARS added as a third way
  to specify its center alongside the existing lat/lon and MGRS support.
- Full design/rollout plan: `/home/gerry/.claude/plans/distributed-humming-sprout.md`.

**Shipped (2026-10-16, backend-only, phase 1 of the rollout plan — no frontend
change yet, no behavior change for Gerry's existing anonymous usage):** `User`/
`UserSession`/`Mission`/`MissionMembership` tables; `app/auth.py`
(`get_current_user`/`require_mission_member`, bcrypt password hashing, TOTP
verification, opaque DB-backed session tokens — no JWT/secret-key to manage);
`POST /api/auth/login` (CAP ID + password + TOTP code → session cookie),
`/logout`, `/me`, `/change-password`; `POST /api/missions` (create, creator
auto-joins), `GET /api/missions/mine`, `GET`/`POST /api/missions/{id}/members`.
Verified live via curl: full login/MFA-reject/logout cycle, mission creation,
membership listing, a 404 adding an unknown CAP ID, and a 403 for a non-member
hitting another mission's members endpoint.

**Shipped (2026-10-16, phase 2):** `OperationsArea` is now one row per mission
(was one global row); a new `MissionAircraft` join table scopes which aircraft
belong to which mission (`Aircraft` itself stays the global tail-number
identity table — ADS-B polling is unaffected). `GET/POST /api/aircraft`,
`GET/PUT /api/discovered/ao`, and `GET/POST /api/discovered/{id}/approve|dismiss`
all take an **optional** `mission_id` query param: omitted, they behave exactly
as before (global/single-AO); given, they're mission-scoped and require
membership (`app/auth.py`'s `optional_mission_member`). This optional-param
design is deliberate and transitional — it lets the backend ship ahead of the
frontend (phase 3) without an outage in between; `mission_id` should become
required and the fallback branch deleted once the frontend always sends it.
Approving a discovered candidate into a mission now also links it into that
mission's `MissionAircraft` roster, not just the global `Aircraft` table —
exactly the "add aircraft not originally on the list if they come into the AO"
case. `Sortie`/`GisLayer` gained a nullable `mission_id` column (schema only,
for the future backfill) but are **not yet** filtered/required by their APIs —
deliberately deferred to a later pass to keep this phase's scope to the two
things Gerry specifically called out (aircraft, AO); the Ops Board and sortie
tracking remain global for now. Verified live via curl: two test missions with
independent AOs that don't clobber each other, independent aircraft rosters
(a roster upload into one mission invisible to another), and an
approve-into-mission that only affects the approving mission's roster.

**Shipped (2026-10-16, phase 3):** a login gate (`#login-overlay` in
`index.html`) now blocks the tracking UI (map markers, roster, Ops Board,
AO) until `GET /api/auth/me` succeeds -- reference layers (airfields, SUA,
uploaded GIS layers) still load unauthenticated, matching the backend (only
identity/mission-scoped routes require a session). A mission switcher
(`#mission-select`, replacing nothing -- it sits above the pre-existing
"Session Mission #" free-text field, which is untouched and still just a
display convenience) lets a user pick which of their missions is active;
every mission-scoped fetch (`loadKnownAircraft`, roster upload,
`loadPersistedOpsArea`, the `locate()` AO PUT, `refreshDiscoveredMarkers`,
the Ops Board's discovered-candidates fetch, approve/dismiss) now carries
`?mission_id=` when one is selected, via a single `withMissionParam()`
helper. Inline "+ New mission" and "Add Member" mini-forms cover mission
creation and adding a member by CAP ID. **IMPORTANT, found live while
building this**: `frontend/` is served directly with no build step, so these
changes are live on the running app the moment the files are saved -- the
live app now requires a login, and there is no account yet except ones
Claude provisioned for its own testing (created and deleted). **Gerry must
run `scripts/create_user.py <his CAP ID> "Gerry Creager"` himself** (per
standing security practice, Claude does not set a human's password) before
he can use the app again; this was flagged to him directly, not left for him
to discover.

**Shipped (2026-10-16)**: Gerry's real account exists (CAP ID 530673, created
by him directly on cosp1 -- Claude never saw his password, per standing
practice). `scripts/backfill_legacy_mission.py` (idempotent, safe to re-run)
created a "Legacy" Mission owned by his account and attached everything that
predated Mission as a real entity: the 30 active aircraft (all of them --
there was no pre-existing OperationsArea row to attach, confirmed empty
earlier) and all 7 existing Sortie rows.

**Shipped (2026-10-16, Sortie/Ops Board scoping)**: `POST/GET /api/aircraft/{tail}/sorties`,
`GET .../sorties/current`, and `GET /api/ops-board` all take the same optional
`mission_id` pattern as phase 2 -- given, a tail's "current sortie" and the Ops
Board are scoped to that one mission (the actual point: a tail flying for
Mission A no longer looks "in progress" or shows up on the board to someone
on Mission B); omitted, unchanged cross-mission behavior. Creating a sortie
with a mission_id now also defaults `mission_number` from `Mission.mission_number`
when the caller didn't type one in (still independently editable after, same
as before). Also closed REQUIREMENTS.md open question 7's other loose end:
every `logged_by`/`edited_by` field the frontend sends was hardcoded to the
literal string `"gerry"` -- now `currentUser.display_name` via a
`loggedByName()` helper, so a second person logging in doesn't get
misattributed to Gerry. Verified live via curl and Playwright: a sortie
created under one test mission showed on that mission's Ops Board with the
right mission number, was absent from a second mission's board, and
`sorties/current` correctly returned null for the second mission while
showing in-progress for the first.

**Not yet done (phase 4):** GARS coordinate parsing (for AO-center entry) is
not yet built. The
`optional_mission_member`/`mission_id`-omitted fallback paths added in
phases 2-3 (see app/auth.py's docstring on it) are a deliberate transitional
seam, not permanent -- once every caller always sends mission_id, those
fallback branches should be deleted.

**Shipped (2026-10-18, mission lifecycle + aircraft unlink + multi-tab
context + wing hint):** full design/rollout plan
`/home/gerry/.claude/plans/distributed-humming-sprout.md`. Closes the gap
that made the auto-generated "Legacy" mission (holding most of Gerry's
active aircraft/sorties) permanently stuck, and the lack of any way to
remove an aircraft from a mission's roster or run independent missions in
separate browser tabs.
- **Mission lifecycle**: `DELETE /api/missions/{id}/members/me` (leave --
  deletes only the caller's own `MissionMembership` row), `POST .../close`
  and `POST .../reopen` (set/clear `Mission.closed_at`, both idempotent,
  matching `DiscoveredCallsign.dismissed_at`'s convention) -- three
  separate, distinct actions, not merged. `list_mine()` (`app/api/missions.py`)
  and `me()` (`app/api/auth.py`) no longer each independently re-implement
  the "every mission this user belongs to" loop -- both now call one shared
  `app.api.auth.list_user_missions(user, session, include_closed=False)`
  helper. Closing a mission only affects whether it shows up in the picker
  by default; its sortie/aircraft history stays fully intact and queryable.
- **Remove aircraft from a mission**: `DELETE /api/aircraft/{aircraft_id}?mission_id=...`
  (`app/api/aircraft.py`, `mission_id` required) deletes only the
  `MissionAircraft` link row -- never touches `Aircraft.active` or the
  `Aircraft` row itself, per Gerry: "we don't eliminate an aircraft from the
  database, we take it off a mission." Verified live: removing a disposable
  aircraft from a mission dropped it from that mission's roster while the
  global (no `mission_id`) listing still showed it `active: true`.
- **Browse and self-join missions**: `GET /api/missions?q=<text>` (any
  logged-in user, NOT gated by membership -- that's the point) lists open
  missions, a capped recency-sorted default (20) when `q` is omitted,
  substring match against name/mission_number (uncapped) when given.
  `POST /{id}/join` self-service-joins (also not gated by
  `require_mission_member`), upsert-or-noop against `MissionMembership`.
  This is a new discoverability surface (mission names/numbers visible to
  any logged-in user) -- confirmed acceptable by Gerry, matching the
  existing low bar of self-service add-by-CAP-ID. Frontend: one
  "Join a mission..." combobox (not two separate controls) backs both the
  browse and search cases.
- **Multi-tab mission context, fixed**: per Gerry, "If they have 3 screens
  open with one login, they can still access 3 individual missions" --
  previously all tabs shared one `localStorage` key with no URL involvement,
  so they fought over one stored value on reload. `currentMissionId`
  (`frontend/src/main.js`) now seeds from the URL's `?mission_id=` first,
  falling back to `localStorage` only when absent; every place that changes
  the active mission routes through one `setCurrentMissionId()` helper that
  updates the URL (via `history.replaceState`, never `pushState`) and
  `localStorage` together. `localStorage` is now purely "the default a
  brand-new tab starts with" -- deliberately no `storage` event listener, so
  tabs stay independent. A bookmarked/middle-clicked `?mission_id=...` link
  now opens its own independent mission context in a new tab.
- **Callsign-to-wing display hint**: `data/callsign_wing_lookup.csv`
  (Gerry's authoritative 52-row Wing ID table, moved from the repo root and
  tracked in git) loaded by `app/integrations/wing_lookup.py`'s
  `wing_hint_for_callsign()` -- a pure function, keyed off the callsign's
  leading wing-prefix digits (1 digit for wings 01-09, 2 digits for 10-52,
  per the CSV's own pattern column). Surfaced as a small, parenthetical,
  display-only badge next to the callsign on discovered-candidate rows and
  roster rows (`AircraftOut.wing_hint`/`DiscoveredCallsignOut.wing_hint`,
  set as a plain attribute on the ORM row before returning, not a real
  column) -- never gates approve/dismiss or any authorization/data-integrity
  decision, since Gerry's own framing was "usually but not always."
- **Verified live**: curl, with disposable test missions/users/aircraft --
  full close/reopen idempotency and default-visibility toggling, mission-
  scoped vs. global aircraft listing around the unlink route, leave dropping
  a mission off one user's list while it stayed on another's, and a
  non-member browsing/joining an open mission. Playwright, with two browser
  contexts under one disposable test account -- independent `?mission_id=`
  per tab, a brand-new same-context tab falling back to the `localStorage`
  default, and the Leave/Close/Reopen/Remove-aircraft buttons' `confirm()`
  flows and resulting dropdown/button-visibility state. All disposable
  rows/accounts were deleted afterward; the real "Legacy" mission and
  Gerry's account/aircraft were untouched throughout.

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
7. **Auth/identity — DECIDED (2026-10-16)**: standalone AvTrack accounts for
   Phase 1 (CAP ID + password + TOTP MFA, not CAP eServices SSO), behind a
   swappable layer so an SSO adapter can replace it later without reworking
   every route — see 3.7 for the full decision and what's shipped so far.
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
