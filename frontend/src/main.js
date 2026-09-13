// Minimal Phase 1 map client. No build step on purpose -- this file (and index.html)
// is meant to be reusable more or less as-is inside a Tauri webview for the Phase 2
// desktop client (REQUIREMENTS.md open question 8), so avoid framework lock-in here
// until there's a real reason to add one.

// Top-down high-wing single silhouette (Cessna 182-like), replacing the earlier
// CSS clip-path dart shape per Gerry's request 2026-09-13, then re-profiled twice
// on live visual feedback: first for a wider fuselage/wing (a path Gerry supplied
// directly), then again (2026-09-13) because that version still read as too
// slender/high-aspect-ratio -- "looks more like a glider" -- so the wing chord,
// fuselage width, and tail stabilizer were all thickened further below. Hand-drawn
// path (viewBox 0 0 100 100, nose up/tail down, mirrored left/right around x=50)
// rather than an external icon asset -- no build step, no new dependency,
// consistent with this file's existing approach (see fixm.py-style preference for
// small hand-rolled logic over pulling in a library for something this contained).
// fill="currentColor" so upsertMarker can still set per-aircraft color via
// .style.color on the wrapping div; stroke="#ffffff" is a contrast outline
// against dark/busy basemaps.
const AIRCRAFT_ICON_SIZE = 35; // keep in sync with .aircraft-dot's width/height in index.html
const AIRCRAFT_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="${AIRCRAFT_ICON_SIZE}" height="${AIRCRAFT_ICON_SIZE}">
  <path d="M 50,14
           L 54.1,14
           L 55.5,25
           L 54.8,31
           L 92,31
           L 92,47.5
           L 54.1,47.5
           L 53.4,58
           L 53.4,72
           L 66,77
           L 66,83
           L 54.1,80
           L 51.4,90
           L 48.6,90
           L 45.9,80
           L 34,83
           L 34,77
           L 46.6,72
           L 46.6,58
           L 45.9,47.5
           L 8,47.5
           L 8,31
           L 45.2,31
           L 44.5,25 Z"
        fill="currentColor"
        stroke="#ffffff"
        stroke-width="1"
        stroke-linejoin="round"/>
</svg>`;

// Three no-API-key raster basemaps, all layers present at once with only one visible
// at a time (toggled via #basemap-select) -- simpler than map.setStyle(), which tears
// down and reloads everything including our own sources/layers below. Default is USGS
// Topo (labeled contours) per Gerry's request. CAP already has an ESRI/ArcGIS
// footprint elsewhere (see /home/gerry/CAP/ESRI, /home/gerry/CAP/GIS/ESRI); swap for
// an org ArcGIS Online basemap/token later if preferred over these public services.
const BASEMAPS = {
  topo: {
    tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"],
    attribution: "USGS The National Map",
  },
  streets: {
    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Esri, HERE, Garmin, FAO, NOAA, USGS, © OpenStreetMap contributors",
  },
  imagery: {
    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Esri, Maxar, Earthstar Geographics",
  },
};
const DEFAULT_BASEMAP = "topo";

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    // Required for any text/symbol layer (airfields-label uses text-field) --
    // MapLibre throws "requires a style glyphs property" and silently fails to add
    // the layer without this. Bug found live 2026-09-13 via a real browser test
    // (Playwright): the label layer was never actually being created at all, which
    // is why toggling "Airfield labels" appeared to do nothing -- there was nothing
    // there to toggle. OpenMapTiles' public font server, matching the "Open Sans
    // Regular" fontstack referenced below.
    glyphs: "https://fonts.openmaptiles.org/{fontstack}/{range}.pbf",
    sources: Object.fromEntries(
      Object.entries(BASEMAPS).map(([key, cfg]) => [
        `basemap-${key}`,
        { type: "raster", tiles: cfg.tiles, tileSize: 256, attribution: cfg.attribution },
      ])
    ),
    layers: Object.keys(BASEMAPS).map((key) => ({
      id: `basemap-${key}-layer`,
      type: "raster",
      source: `basemap-${key}`,
      layout: { visibility: key === DEFAULT_BASEMAP ? "visible" : "none" },
    })),
  },
  center: [-98.5, 39.8], // CONUS center
  zoom: 3.2,
});
window.map = map; // exposed deliberately for console/devtools debugging

document.getElementById("basemap-select").addEventListener("change", (e) => {
  const selected = e.target.value;
  for (const key of Object.keys(BASEMAPS)) {
    map.setLayoutProperty(`basemap-${key}-layer`, "visibility", key === selected ? "visible" : "none");
  }
});

const markers = new Map(); // tail_number -> maplibregl.Marker
const knownAircraft = new Set(); // tail_numbers from /api/aircraft -- see "unlisted" highlighting below
const latestByTail = new Map(); // tail_number -> last position payload, for the side panel

// Airfield label tiering (simplified 2026-09-13 per Gerry -- "adjust later"):
// tier 1 = military, tier 2 = any runway >=5000ft regardless of surface. See
// app/api/airfields.py compute_tier(). `labelMinZoom` is precomputed per feature here
// and applied via an imperative map.setFilter() on zoom change (see updateLabelFilter
// below), rather than a GL style zoom-expression, to keep the logic in one
// obviously-testable place.
const TIER_MIN_ZOOM = { 1: 4, 2: 6 };
const UNTIERED_MIN_ZOOM = 99; // effectively never -- dot still shows, just no persistent label

let labelsVisible = true;
let airfieldsFetchTimer = null;

function airfieldsToGeoJSON(rows) {
  return {
    type: "FeatureCollection",
    features: rows.map((a) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [a.longitude, a.latitude] },
      properties: {
        label: a.icao_id || a.faa_id || a.name,
        name: a.name,
        icao_id: a.icao_id,
        faa_id: a.faa_id,
        longest_runway_ft: a.longest_runway_ft,
        hard_surface_available: a.hard_surface_available,
        is_military: a.is_military,
        tier: a.tier,
        labelMinZoom: a.tier != null ? TIER_MIN_ZOOM[a.tier] : UNTIERED_MIN_ZOOM,
      },
    })),
  };
}

function updateLabelFilter() {
  if (!map.getLayer("airfields-label")) return;
  const zoom = map.getZoom();
  map.setFilter("airfields-label", ["<=", ["get", "labelMinZoom"], zoom]);
  map.setLayoutProperty("airfields-label", "visibility", labelsVisible ? "visible" : "none");
}

let airfieldsRequestId = 0;

async function refreshAirfields() {
  // Race-condition fix (found live 2026-09-13 via a real browser test): the initial
  // page load fires a huge CONUS-wide fetch (map's default zoom 3.2 view) at the same
  // time a locate()-triggered flyTo fires a much smaller bbox-scoped one. Nothing
  // guaranteed the responses applied in request order -- if the large nationwide
  // fetch happened to resolve *after* the small one, it silently overwrote it with
  // ~16k airfields' worth of data, one symptom of which was airfield labels
  // appearing to not work (not the actual bug -- see the `glyphs` fix above -- but a
  // second, independent issue found while verifying that fix in a browser). This
  // request-id guard discards any response that isn't from the most recently
  // *initiated* request, regardless of resolution order.
  const requestId = ++airfieldsRequestId;
  const b = map.getBounds();
  const bounds = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
  try {
    const res = await fetch(`/api/airfields?bounds=${bounds}`);
    const rows = await res.json();
    if (requestId !== airfieldsRequestId) return; // superseded by a newer request
    const geojson = airfieldsToGeoJSON(rows);
    const source = map.getSource("airfields");
    if (source) {
      source.setData(geojson);
    } else {
      map.addSource("airfields", { type: "geojson", data: geojson });
      map.addLayer({
        id: "airfields-layer",
        type: "circle",
        source: "airfields",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 1.5, 10, 4, 14, 7],
          "circle-color": ["case", ["get", "is_military"], "#c0392b", "#8a6d3b"],
          "circle-stroke-color": "#fff",
          "circle-stroke-width": 1,
        },
      });
      map.addLayer({
        id: "airfields-label",
        type: "symbol",
        source: "airfields",
        layout: {
          "text-field": ["get", "label"],
          "text-font": ["Open Sans Regular"], // must match a fontstack the style's `glyphs` server actually has
          "text-size": 11,
          "text-offset": [0, 1.1],
          "text-anchor": "top",
        },
        paint: { "text-color": "#e8eaed", "text-halo-color": "#14181c", "text-halo-width": 1 },
      });
      map.on("click", "airfields-layer", (e) => {
        const p = e.features[0].properties;
        const rwy = p.longest_runway_ft
          ? `${p.longest_runway_ft} ft, ${p.hard_surface_available ? "hard surface" : "not hard-surfaced"}`
          : "runway data not loaded";
        new maplibregl.Popup()
          .setLngLat(e.lngLat)
          .setHTML(
            `<strong>${p.name || p.label}</strong>${p.is_military ? " (MIL)" : ""}<br>${p.icao_id || ""} ${p.faa_id ? `(${p.faa_id})` : ""}<br>${rwy}`
          )
          .addTo(map);
      });
    }
    updateLabelFilter();
  } catch (err) {
    console.error("Failed to load airfields", err);
  }
}

function scheduleAirfieldsRefresh() {
  clearTimeout(airfieldsFetchTimer);
  airfieldsFetchTimer = setTimeout(refreshAirfields, 300); // debounce pan/zoom bursts
}

document.getElementById("labels-toggle").addEventListener("change", (e) => {
  labelsVisible = e.target.checked;
  updateLabelFilter();
});

// Operations-area circle (REQUIREMENTS.md 3.2 locate-with-radius). Equirectangular
// approximation -- accurate enough for the tens-of-nm radii this is meant for, not
// meant to be precise at large radii.
function circleGeoJSON([lon, lat], radiusNm, points = 64) {
  const radiusKm = radiusNm * 1.852;
  const distanceX = radiusKm / (111.32 * Math.cos((lat * Math.PI) / 180));
  const distanceY = radiusKm / 110.574;
  const coords = [];
  for (let i = 0; i <= points; i++) {
    const theta = (i / points) * 2 * Math.PI;
    coords.push([lon + distanceX * Math.cos(theta), lat + distanceY * Math.sin(theta)]);
  }
  return { type: "Feature", geometry: { type: "Polygon", coordinates: [coords] }, properties: {} };
}

function setOpsArea(center, radiusNm) {
  if (!map.isStyleLoaded()) {
    map.once("load", () => setOpsArea(center, radiusNm));
    return;
  }
  const feature = circleGeoJSON(center, radiusNm);
  const source = map.getSource("ops-area");
  if (source) {
    source.setData(feature);
  } else {
    map.addSource("ops-area", { type: "geojson", data: feature });
    map.addLayer({
      id: "ops-area-fill",
      type: "fill",
      source: "ops-area",
      paint: { "fill-color": "#2b6cb0", "fill-opacity": 0.08 },
    });
    map.addLayer({
      id: "ops-area-line",
      type: "line",
      source: "ops-area",
      paint: { "line-color": "#2b6cb0", "line-width": 2, "line-dasharray": [2, 2] },
    });
  }
}

async function loadKnownAircraft() {
  try {
    const res = await fetch("/api/aircraft");
    const rows = await res.json();
    rows.forEach((a) => knownAircraft.add(a.tail_number));
  } catch (err) {
    console.error("Failed to load aircraft list", err);
  }
}

function markerColor(tail) {
  return knownAircraft.has(tail) ? "#2b6cb0" : "#d9534f"; // red = unlisted, per REQUIREMENTS.md 3.1
}

// Ghost-aircraft fix (found live 2026-09-12): aircraft parked at KBAK, engines off,
// kept showing their last-known (mid-flight) altitude/airspeed indefinitely, because
// nothing ever told the UI a marker's data had gone stale. The backend refuses to
// report positions older than MAX_POSITION_AGE (app/adsb/flightaware.py) as "current",
// but a marker already drawn from an earlier valid update would still just sit there
// forever with no new messages ever arriving to update or clear it -- this sweep
// removes markers that haven't heard anything in a while, independent of whether the
// backend ever sends an explicit "gone" message (it doesn't, today).
//
// IMPORTANT, found live 2026-09-12 (2nd bug from the same feature): this threshold
// must stay comfortably longer than a full backend poll cycle, or this sweep deletes
// every marker before its next legitimate update ever arrives -- which looks exactly
// like "no aircraft ever shows" and is very easy to mistake for a data/connectivity
// bug when it's actually just this number being too tight. Measured full-cycle time
// with 22 tracked aircraft at the current per-call pacing
// (ADSB_INTER_REQUEST_DELAY_SECONDS): ~5.5-6 minutes -- the previous value here (5
// min) was *shorter* than that, guaranteeing this exact failure. Cycle time scales
// roughly linearly with tracked-aircraft count and pacing delay, both of which are
// still being tuned (see REQUIREMENTS.md 3.5 AeroAPI rate-limit notes) -- if aircraft
// start disappearing again after the fleet grows or pacing changes, check actual
// cycle time (gap between consecutive position rows for the same tail_number) before
// assuming anything else is broken, and raise this number to comfortably exceed it.
const STALE_THRESHOLD_MS = 12 * 60 * 1000;

function sweepStaleAircraft() {
  const now = Date.now();
  let changed = false;
  for (const [tail, pos] of latestByTail) {
    if (now - new Date(pos.ts_utc).getTime() > STALE_THRESHOLD_MS) {
      latestByTail.delete(tail);
      const marker = markers.get(tail);
      if (marker) {
        marker.remove();
        markers.delete(tail);
      }
      changed = true;
    }
  }
  if (changed) renderPanel();
}
setInterval(sweepStaleAircraft, 30000);

function upsertMarker(pos) {
  latestByTail.set(pos.tail_number, pos);
  let marker = markers.get(pos.tail_number);
  if (!marker) {
    const wrapper = document.createElement("div");
    wrapper.className = "aircraft-marker";
    const dot = document.createElement("div");
    dot.className = "aircraft-dot";
    dot.innerHTML = AIRCRAFT_SVG;
    const datablock = document.createElement("div");
    datablock.className = "aircraft-datablock";
    wrapper.appendChild(dot);
    wrapper.appendChild(datablock);
    wrapper.addEventListener("click", () => openSortiePanel(pos.tail_number));

    marker = new maplibregl.Marker({ element: wrapper })
      .setLngLat([pos.longitude, pos.latitude])
      .addTo(map);
    marker._dot = dot;
    marker._datablock = datablock;
    markers.set(pos.tail_number, marker);
  } else {
    marker.setLngLat([pos.longitude, pos.latitude]);
  }

  marker._dot.style.color = markerColor(pos.tail_number);
  marker._dot.style.transform = `rotate(${pos.heading_deg ?? 0}deg)`;
  // Datablock: callsign on one line, altitude/speed on the next -- ATC-style, per
  // Gerry's requirement that every airborne aircraft always shows this, not just on
  // click (the click target is the sortie panel, see openSortiePanel).
  const alt = pos.altitude_ft != null ? Math.round(pos.altitude_ft) : "—";
  const gs = pos.ground_speed_kt != null ? Math.round(pos.ground_speed_kt) : "—";
  marker._datablock.innerHTML = `<div class="cs">${pos.callsign ?? pos.tail_number}</div><div>${alt}ft ${gs}kt</div>`;

  renderPanel();
}

function renderPanel() {
  const list = document.getElementById("aircraft-list");
  const rows = [...latestByTail.values()].sort((a, b) => a.tail_number.localeCompare(b.tail_number));
  if (rows.length === 0) {
    // Distinguish "still connecting" from "connected, just no data yet" -- these look
    // identical to a viewer if both just say "Connecting..." forever, which is
    // actively misleading when e.g. ADS-B polling is paused server-side but the
    // WebSocket itself is fine. See REQUIREMENTS.md dev notes 2026-09-13.
    list.innerHTML = wsConnected
      ? "<em>Connected — no aircraft reporting.</em>"
      : "<em>Connecting…</em>";
    return;
  }
  list.innerHTML = rows
    .map((pos) => {
      const unlisted = !knownAircraft.has(pos.tail_number);
      return `
        <div class="aircraft-row ${unlisted ? "unlisted" : ""}">
          <div class="callsign">${pos.callsign ?? pos.tail_number}${unlisted ? " ⚠" : ""}</div>
          <div class="meta">${Math.round(pos.altitude_ft ?? 0)} ft · ${Math.round(pos.ground_speed_kt ?? 0)} kt</div>
        </div>`;
    })
    .join("");
}

let wsConnected = false;

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/positions`);
  ws.onopen = () => {
    wsConnected = true;
    renderPanel(); // flips "Connecting..." to "no aircraft reporting" even with zero data so far
  };
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "position") upsertMarker(msg);
  };
  ws.onclose = () => {
    wsConnected = false;
    renderPanel();
    setTimeout(connectWebSocket, 3000); // simple reconnect
  };
  ws.onerror = () => ws.close();
}

// ── Sortie panel: click an aircraft to view/edit its event log (REQUIREMENTS.md 3.3).
// No auto-detection exists yet (app/ingestion/heuristics.py is a stub), so today
// every event here comes from these manual "log now" buttons -- same backend path
// auto-detection will feed into later.
const EVENT_TYPES = ["engine_start", "takeoff", "touch_and_go", "landing", "engine_stop"];
let currentSortieTail = null;

// Display timezone (REQUIREMENTS.md 4): always starts at UTC on load, never persisted
// across reloads -- deliberate, so a stale non-UTC selection from a previous
// user/shift on a shared browser can't silently carry over. The user may change it
// for their own session via #tz-select.
let displayTimezone = "UTC";
document.getElementById("tz-select").addEventListener("change", (e) => {
  displayTimezone = e.target.value;
  if (currentSortieTail) refreshSortieEvents(currentSortieTail);
});

function fmtInTz(isoUtc) {
  return new Date(isoUtc).toLocaleString([], { hour12: false, timeZone: displayTimezone }) + ` ${displayTimezone}`;
}

// <input type="datetime-local"> only natively understands the browser's own local
// timezone, not an arbitrary IANA zone -- these two helpers do the conversion by hand
// via Intl so the input can represent wall-clock time in `displayTimezone` instead.

// UTC Date -> wall-clock string in `tz`, for populating the input.
function utcToZonedInputValue(date, tz) {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const p = Object.fromEntries(dtf.formatToParts(date).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
}

// Wall-clock string (as entered, meant to represent a time IN `tz`) -> UTC Date.
// Standard trick: parse the naive string as if it were UTC, see what that instant
// looks like when rendered in `tz`, and use the discrepancy as the zone's offset at
// that moment (correct across DST since Intl uses real IANA data) to correct it.
function zonedInputValueToUtc(wallClockStr, tz) {
  const asIfUtc = new Date(`${wallClockStr}Z`);
  const rendered = utcToZonedInputValue(asIfUtc, tz);
  const renderedAsUtc = new Date(`${rendered}Z`);
  const offsetMs = asIfUtc.getTime() - renderedAsUtc.getTime();
  return new Date(asIfUtc.getTime() + offsetMs);
}

async function openSortiePanel(tailNumber) {
  currentSortieTail = tailNumber;
  const panel = document.getElementById("sortie-panel");
  const pos = latestByTail.get(tailNumber);
  document.getElementById("sortie-title").textContent = pos?.callsign ?? tailNumber;
  document.getElementById("sortie-subtitle").textContent = tailNumber;
  panel.hidden = false;

  const buttons = document.getElementById("sortie-log-buttons");
  buttons.innerHTML = "";
  for (const type of EVENT_TYPES) {
    const btn = document.createElement("button");
    btn.textContent = `${type.replace(/_/g, " ")} — NOW`;
    btn.addEventListener("click", () => logEvent(tailNumber, type));
    buttons.appendChild(btn);
  }

  await refreshSortieEvents(tailNumber);
}

async function refreshSortieEvents(tailNumber) {
  const container = document.getElementById("sortie-events");
  container.innerHTML = "<em>Loading…</em>";
  try {
    const res = await fetch(`/api/aircraft/${tailNumber}/events`);
    const events = await res.json();
    if (events.length === 0) {
      container.innerHTML = "<em>No events logged yet.</em>";
      return;
    }
    container.innerHTML = "";
    for (const ev of events) {
      const row = document.createElement("div");
      row.className = "event-row";
      const edited = ev.edits.length > 0;
      row.innerHTML = `
        <div class="event-type">${ev.event_type.replace(/_/g, " ")}</div>
        <div class="event-time">${fmtInTz(ev.effective_time_utc)}${edited ? '<span class="edited-tag">edited</span>' : ""}</div>
        <div class="edit-controls">
          <input type="datetime-local" step="1" />
          <button>Correct time (${displayTimezone})</button>
        </div>`;
      const input = row.querySelector("input");
      input.value = utcToZonedInputValue(new Date(ev.effective_time_utc), displayTimezone);
      row.querySelector("button").addEventListener("click", async () => {
        const newUtc = zonedInputValueToUtc(input.value, displayTimezone).toISOString();
        await fetch(`/api/events/${ev.id}/edits`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ field_name: "event_time_utc", new_value: newUtc, edited_by: "gerry" }),
        });
        refreshSortieEvents(tailNumber);
      });
      container.appendChild(row);
    }
  } catch (err) {
    container.innerHTML = "<em>Failed to load events.</em>";
    console.error(err);
  }
}

async function logEvent(tailNumber, eventType) {
  await fetch(`/api/aircraft/${tailNumber}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_type: eventType, logged_by: "gerry" }), // logged_by hardcoded until auth exists (REQUIREMENTS.md open question 7)
  });
  refreshSortieEvents(tailNumber);
}

document.getElementById("sortie-close").addEventListener("click", () => {
  document.getElementById("sortie-panel").hidden = true;
  currentSortieTail = null;
});

async function locate(query) {
  query = query.trim();
  let url;
  if (/^[A-Za-z]{3,4}$/.test(query)) {
    url = `/api/locate/icao/${query}`;
  } else if (/^-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?$/.test(query)) {
    const [lat, lon] = query.split(",").map((s) => parseFloat(s.trim()));
    url = `/api/locate/latlon?lat=${lat}&lon=${lon}`;
  } else {
    url = `/api/locate/mgrs/${encodeURIComponent(query.replace(/\s+/g, ""))}`;
  }
  const res = await fetch(url);
  if (!res.ok) {
    alert(`Could not locate "${query}"`);
    return;
  }
  const { latitude, longitude } = await res.json();
  const radiusNm = parseFloat(document.getElementById("locate-radius").value) || 0;
  if (radiusNm > 0) {
    setOpsArea([longitude, latitude], radiusNm);
  }
  // Zoom out enough to see the whole radius circle rather than a fixed zoom level.
  const zoom = radiusNm > 0 ? Math.max(4, 11 - Math.log2(Math.max(radiusNm, 1) / 10)) : 11;
  map.flyTo({ center: [longitude, latitude], zoom });
}

document.getElementById("locate-btn").addEventListener("click", () => {
  locate(document.getElementById("locate-input").value);
});
document.getElementById("locate-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") locate(e.target.value);
});

// ── Special Use Airspace (REQUIREMENTS.md 3.2) ──────────────────────────────
// Same three visual groups + colors as CAP WxCOP's EWMC map
// (enhanced_weather_map_complete.html on r815) so the convention carries over for
// anyone already used to that display. All off by default -- SUA is dense enough
// nationally (~1500 areas) that showing it unasked would clutter the map.
const SUA_GROUPS = {
  "sua-prohib": { types: ["P"], fill: "#cc0000", line: "#b40000", checkboxId: "sua-prohib-toggle" },
  "sua-restrict": { types: ["R"], fill: "#dc6400", line: "#dc6400", checkboxId: "sua-restrict-toggle" },
  "sua-moa": { types: ["MOA", "W", "A", "D"], fill: "#5000c8", line: "#5000c8", checkboxId: "sua-moa-toggle" },
};
let suaRequestId = 0;

async function refreshSua() {
  const requestId = ++suaRequestId;
  const b = map.getBounds();
  const bounds = `west=${b.getWest()}&south=${b.getSouth()}&east=${b.getEast()}&north=${b.getNorth()}`;
  try {
    const res = await fetch(`/api/airspace/sua?${bounds}`);
    const geojson = await res.json();
    if (requestId !== suaRequestId) return; // superseded, same race-guard as refreshAirfields
    const source = map.getSource("sua");
    if (source) {
      source.setData(geojson);
      return;
    }
    map.addSource("sua", { type: "geojson", data: geojson });
    for (const [id, group] of Object.entries(SUA_GROUPS)) {
      const filter = ["in", ["get", "type_code"], ["literal", group.types]];
      map.addLayer({
        id: `${id}-fill`,
        type: "fill",
        source: "sua",
        filter,
        layout: { visibility: "none" },
        paint: { "fill-color": group.fill, "fill-opacity": 0.2 },
      });
      map.addLayer({
        id: `${id}-line`,
        type: "line",
        source: "sua",
        filter,
        layout: { visibility: "none" },
        paint: { "line-color": group.line, "line-width": 1.5, "line-opacity": 0.9 },
      });
      map.on("click", `${id}-fill`, (e) => {
        const p = e.features[0].properties;
        new maplibregl.Popup()
          .setLngLat(e.lngLat)
          .setHTML(
            `<strong>${p.name || ""}</strong> (${p.type_name})<br>${p.city || ""} ${p.state || ""}<br>${p.lower_altitude || "?"} - ${p.upper_altitude || "?"}<br>${p.times_of_use || ""}`
          )
          .addTo(map);
      });
    }
  } catch (err) {
    console.error("Failed to load SUA", err);
  }
}

let suaFetchTimer = null;
function scheduleSuaRefresh() {
  clearTimeout(suaFetchTimer);
  suaFetchTimer = setTimeout(refreshSua, 300);
}

for (const [id, group] of Object.entries(SUA_GROUPS)) {
  document.getElementById(group.checkboxId).addEventListener("change", (e) => {
    const visibility = e.target.checked ? "visible" : "none";
    if (!map.getLayer(`${id}-fill`)) return; // source/layers not created until first refreshSua()
    map.setLayoutProperty(`${id}-fill`, "visibility", visibility);
    map.setLayoutProperty(`${id}-line`, "visibility", visibility);
  });
}

// ── Custom mission GIS layers (REQUIREMENTS.md 3.2 -- SHP/KML/KMZ upload) ───
// Each uploaded layer gets its own map source/layer pair, all sharing one accent
// color (distinct from SUA's palette above and from aircraft markers) since these
// are ad hoc operational overlays, not a categorized reference dataset.
const GIS_LAYER_COLOR = "#f4d03f";
const gisLayers = new Map(); // id -> {name, feature_count}

async function refreshGisLayerList() {
  const res = await fetch("/api/gis/layers");
  const layers = await res.json();
  gisLayers.clear();
  for (const layer of layers) gisLayers.set(layer.id, layer);
  renderGisLayerList();
  for (const layer of layers) {
    if (!map.getSource(`gis-${layer.id}`)) await addGisLayerToMap(layer.id);
  }
}

async function addGisLayerToMap(layerId) {
  const res = await fetch(`/api/gis/layers/${layerId}/geojson`);
  const geojson = await res.json();
  map.addSource(`gis-${layerId}`, { type: "geojson", data: geojson });
  map.addLayer({
    id: `gis-${layerId}-fill`,
    type: "fill",
    source: `gis-${layerId}`,
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": GIS_LAYER_COLOR, "fill-opacity": 0.15 },
  });
  map.addLayer({
    id: `gis-${layerId}-line`,
    type: "line",
    source: `gis-${layerId}`,
    paint: { "line-color": GIS_LAYER_COLOR, "line-width": 2 },
  });
  map.on("click", `gis-${layerId}-fill`, (e) => {
    new maplibregl.Popup()
      .setLngLat(e.lngLat)
      .setHTML(`<pre style="margin:0;font-size:11px;">${JSON.stringify(e.features[0].properties, null, 1)}</pre>`)
      .addTo(map);
  });
}

function removeGisLayerFromMap(layerId) {
  for (const suffix of ["-fill", "-line"]) {
    if (map.getLayer(`gis-${layerId}${suffix}`)) map.removeLayer(`gis-${layerId}${suffix}`);
  }
  if (map.getSource(`gis-${layerId}`)) map.removeSource(`gis-${layerId}`);
}

function renderGisLayerList() {
  const container = document.getElementById("gis-layers-list");
  if (gisLayers.size === 0) {
    container.innerHTML = '<em style="color:#9aa4ad;">None uploaded</em>';
    return;
  }
  container.innerHTML = "";
  for (const [id, layer] of gisLayers) {
    const row = document.createElement("div");
    row.className = "gis-layer-row";
    row.innerHTML = `<span class="gis-layer-name">${layer.name} (${layer.feature_count})</span><button title="Remove">✕</button>`;
    row.querySelector("button").addEventListener("click", async () => {
      await fetch(`/api/gis/layers/${id}`, { method: "DELETE" });
      removeGisLayerFromMap(id);
      gisLayers.delete(id);
      renderGisLayerList();
    });
    container.appendChild(row);
  }
}

document.getElementById("gis-upload-btn").addEventListener("click", async () => {
  const input = document.getElementById("gis-upload-input");
  const file = input.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  formData.append("name", file.name.replace(/\.[^.]+$/, ""));
  const res = await fetch("/api/gis/layers", { method: "POST", body: formData });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(`Upload failed: ${err.detail || res.statusText}`);
    return;
  }
  input.value = "";
  await refreshGisLayerList();
});

map.on("load", refreshAirfields);
map.on("moveend", scheduleAirfieldsRefresh); // covers both pan and zoom (zoom-only still fires moveend)
map.on("zoom", updateLabelFilter); // instant label-tier feedback, ahead of the debounced bbox refetch
map.on("load", refreshSua);
map.on("moveend", scheduleSuaRefresh);
map.on("load", refreshGisLayerList);
loadKnownAircraft().then(connectWebSocket);
