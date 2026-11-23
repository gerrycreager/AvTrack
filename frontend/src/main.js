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
const AIRCRAFT_ICON_SIZE = 40; // keep in sync with .aircraft-dot's width/height in index.html
const AIRCRAFT_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="${AIRCRAFT_ICON_SIZE}" height="${AIRCRAFT_ICON_SIZE}">
  <path d="M 50,14
           L 54.9,14
           L 56.6,25
           L 55.8,29.4
           L 92,29.4
           L 92,49.1
           L 54.9,49.1
           L 54.1,58
           L 54.1,72
           L 66,77
           L 66,83
           L 54.9,80
           L 51.7,90
           L 48.3,90
           L 45.1,80
           L 34,83
           L 34,77
           L 45.9,72
           L 45.9,58
           L 45.1,49.1
           L 8,49.1
           L 8,29.4
           L 44.2,29.4
           L 43.4,25 Z"
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
// Each entry is a base raster layer; `overlay` (optional) is a second,
// transparent raster layer drawn on top of it, same two-layer shape used for
// `streets` and `hybrid` below.
const BASEMAPS = {
  // Real FAA VFR sectional raster, self-hosted on r815 (2026-11-21) --
  // same source/pipeline as CAP_WxCOP's EWMC/Flight Ops Impacts pages,
  // served via the same MapCache WMTS endpoint on this host, so there's
  // no separate tiling/storage for AvTrack to maintain. Fetch/
  // reprojection credit: Charlie Jackson, Capt, PCR-WA-068, creator of
  // capgrids.com -- see /var/www/cap_winds_app/data/sectionals/README.md.
  sectional: {
    // Fully-qualified, not a relative path: AvTrack is served on its own
    // port (2468, see /etc/apache2/sites-available/avtrack.conf), which
    // proxies everything else straight to the AvTrack backend -- a
    // relative "/mapcache/..." path would resolve against that origin
    // and 404, not against the main site's Apache/mod_mapcache.
    tiles: ["https://wxcop.duckdns.org/mapcache/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=sectional_vfr&STYLE=&TILEMATRIXSET=GoogleMapsCompatible&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/png"],
    attribution: "FAA — chart automation by capgrids.com",
  },
  topo: {
    tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"],
    attribution: "USGS The National Map",
  },
  // Replaced World_Street_Map 2026-10-25 per Gerry: "Streets should not show
  // any relief: Just streets, highways, interstates, even named dirt roads,
  // but not relief" -- World_Street_Map bakes in shaded relief/land-cover
  // texture as part of its cartography, which isn't a toggle. Esri's own
  // Light Gray Canvas base (flat, no relief) + its dedicated roads-only
  // World_Transportation reference layer gives exactly "just streets."
  streets: {
    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Esri",
    overlays: [
      {
        tiles: [
          "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}",
        ],
        attribution: "Esri",
      },
    ],
  },
  imagery: {
    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Esri, Maxar, Earthstar Geographics",
  },
  // Satellite background + roads + boundaries foreground (roads added
  // 2026-10-25, boundaries added 2026-11-02, both per Gerry). First roads
  // attempt used Reference/World_Boundaries_and_Places (boundaries + place
  // labels only, no actual road lines) for the overlay -- against solid
  // imagery that read as "nothing's really there" (Gerry: "it appears the
  // hybrid layer has streets underneath (not visible) the satellite layer").
  // Switched to World_Transportation, which actually draws road/highway
  // lines. Boundaries (state/national) are a second, separate overlay drawn
  // above that -- deliberately baked into the hybrid basemap itself, not a
  // separate checkbox anywhere: per Gerry, "the boundaries should just
  // appear" when hybrid is selected, not be a listed/selectable toggle.
  // World_Boundaries_and_Places also bundles light place-name labels; not
  // worth a separate layer just to strip those back out.
  hybrid: {
    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Esri, Maxar, Earthstar Geographics",
    overlays: [
      {
        tiles: [
          "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}",
        ],
        attribution: "Esri",
      },
      {
        tiles: [
          "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        ],
        attribution: "Esri",
      },
    ],
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
      Object.entries(BASEMAPS).flatMap(([key, cfg]) => {
        const srcs = [[`basemap-${key}`, { type: "raster", tiles: cfg.tiles, tileSize: 256, attribution: cfg.attribution }]];
        (cfg.overlays ?? []).forEach((ov, i) => {
          srcs.push([`basemap-${key}-overlay-${i}`, { type: "raster", tiles: ov.tiles, tileSize: 256, attribution: ov.attribution }]);
        });
        return srcs;
      })
    ),
    // Any feature layer added later via addLayer() stacks on top of all of
    // these by default -- only needs to keep each basemap's own overlay(s)
    // (if any) listed right after its base here so they draw above that one
    // base layer specifically, in array order (e.g. hybrid's boundaries draw
    // above its roads, which draw above its imagery).
    layers: Object.entries(BASEMAPS).flatMap(([key, cfg]) => {
      const visible = key === DEFAULT_BASEMAP;
      const layers = [
        {
          id: `basemap-${key}-layer`,
          type: "raster",
          source: `basemap-${key}`,
          layout: { visibility: visible ? "visible" : "none" },
        },
      ];
      (cfg.overlays ?? []).forEach((_, i) => {
        layers.push({
          id: `basemap-${key}-overlay-${i}-layer`,
          type: "raster",
          source: `basemap-${key}-overlay-${i}`,
          layout: { visibility: visible ? "visible" : "none" },
        });
      });
      return layers;
    }),
  },
  center: [-98.5, 39.8], // CONUS center
  zoom: 3.2,
});
window.map = map; // exposed deliberately for console/devtools debugging
window.openSortiePanel = openSortiePanel; // same -- lets devtools/tests open the panel for any tail directly

// ── Login + mission scoping (added 2026-10-16, per-user/per-mission work) ──
// mission_id stays OPTIONAL on every backend call during this rollout (see
// app/auth.py's optional_mission_member docstring) -- omitting it preserves
// the exact pre-login global/single-AO behavior, so currentMissionId starts
// null (no mission selected) rather than forcing a choice before the map is
// usable.
//
// Multi-tab fix (2026-10-18 plan): the URL's `?mission_id=` query param is now
// the SOURCE OF TRUTH, not localStorage. Gerry's own example: "If they have 3
// screens open with one login, they can still access 3 individual missions" --
// under the old design all tabs shared one localStorage key with no URL
// involvement, so they fought over one stored value on reload instead of each
// holding an independent mission. localStorage is demoted to "the default a
// brand-new tab starts with" ONLY -- set once at load, never read again after
// that, and deliberately NOT kept in sync via a `storage` event listener (that
// would just reintroduce the exact cross-tab coupling this fix removes).
// Every place that changes the active mission must go through
// setCurrentMissionId() below so the URL/localStorage stay consistent with the
// in-memory value.
const CURRENT_MISSION_KEY = "avtrack_current_mission_id";
let currentUser = null;
let currentMissions = [];
let currentMissionId = new URLSearchParams(location.search).get("mission_id") || localStorage.getItem(CURRENT_MISSION_KEY) || null;

// Single chokepoint for changing the active mission: updates the in-memory
// value, the URL (via history.replaceState -- never pushState, so switching
// missions doesn't pollute browser back/forward history), and localStorage
// (this tab's new "brand-new-tab default" going forward). Does NOT reload any
// mission-scoped data itself -- callers already do that explicitly afterward.
function setCurrentMissionId(missionId) {
  currentMissionId = missionId || null;
  const url = new URL(location.href);
  if (currentMissionId) {
    url.searchParams.set("mission_id", currentMissionId);
    localStorage.setItem(CURRENT_MISSION_KEY, currentMissionId);
  } else {
    url.searchParams.delete("mission_id");
    localStorage.removeItem(CURRENT_MISSION_KEY);
  }
  history.replaceState(null, "", url);
}

function withMissionParam(url) {
  if (!currentMissionId) return url;
  const qp = `mission_id=${encodeURIComponent(currentMissionId)}`;
  return url.includes("?") ? `${url}&${qp}` : `${url}?${qp}`;
}

// Real identity now exists (replaces the "gerry" placeholders REQUIREMENTS.md
// open question 7 flagged) -- falls back to "unknown" only if somehow called
// before login completes, matching the backend schema's own default.
function loggedByName() {
  return currentUser ? currentUser.display_name : "unknown";
}

// The active mission's own mission_number (YY-X-NNNN), if it has one -- takes
// priority over the free-text "Session Mission #" convenience field when
// pre-filling Start Sortie, now that Mission is a real backend entity.
function activeMissionNumber() {
  const mission = currentMissions.find((m) => m.id === currentMissionId);
  return mission?.mission_number || null;
}

// The calling user's own free-text role on the active mission (MissionOut.role,
// "IC/Ops/AOBD/COMMS, optional" -- never authoritative, see MissionMembership's
// docstring in models.py). Used only to pick a *default* tab in the sortie
// panel (REQUIREMENTS.md 3.3, added 2026-10-26) -- never to gate anything,
// since this is free text and often blank.
function activeMissionRole() {
  const mission = currentMissions.find((m) => m.id === currentMissionId);
  return mission?.role || null;
}

// Haversine distance (nm) + initial bearing (deg, 0-360) -- used by the Status
// tab's "distance/bearing from Bullseye" (added 2026-10-26). No existing
// frontend helper for this; the backend has an equivalent for AO-radius
// membership checks (app/integrations/adsb_discovery.py) but that's a
// different computation (point-in-radius, not distance+bearing) and isn't
// exposed to the frontend, so this is a small, separate implementation.
const EARTH_RADIUS_NM = 3440.065;
function haversineNm(lat1, lon1, lat2, lon2) {
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_NM * Math.asin(Math.sqrt(a));
}
function initialBearingDeg(lat1, lon1, lat2, lon2) {
  const toRad = (d) => (d * Math.PI) / 180;
  const dLon = toRad(lon2 - lon1);
  const y = Math.sin(dLon) * Math.cos(toRad(lat2));
  const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) - Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLon);
  return (((Math.atan2(y, x) * 180) / Math.PI) + 360) % 360;
}

// True once the map's initial style load has completed. Deliberately NOT
// map.isStyleLoaded() -- found live 2026-09-13 (Gerry: "I looked and didn't see the
// MGRS box either"): that method tracks whether all currently-visible *tiles* have
// finished loading, not whether the style is ready to accept new sources/layers,
// and it can sit at false for seconds at a time during normal use (e.g. right after
// a pan/zoom). setOpsArea/setMgrsBox used to guard on it with
// `if (!isStyleLoaded()) map.once("load", retry)` -- broken, since MapLibre's
// "load" event fires exactly once at startup; if the guard is hit after that (the
// common case, since it's easy for isStyleLoaded() to be false well into normal
// use), the deferred retry registers for an event that will never fire again, and
// the feature just silently never appears. This flag is set exactly once, by the
// map's real "load" event, and never rechecked after that.
let mapStyleReady = false;
map.on("load", () => {
  mapStyleReady = true;
  registerAirfieldTierBadges();
  registerObstructionIcons();
});

// Aviation Chart mode (set true by the "aviation" option below) swaps the
// topo/sectional pair itself based on zoom -- see applyAviationLayer() --
// so this plain-selection handler only runs for the other, non-crossover
// basemap choices.
document.getElementById("basemap-select").addEventListener("change", (e) => {
  const selected = e.target.value;
  if (selected === "aviation") {
    aviationModeActive = true;
    for (const [key, cfg] of Object.entries(BASEMAPS)) {
      map.setLayoutProperty(`basemap-${key}-layer`, "visibility", "none");
      (cfg.overlays ?? []).forEach((_, i) => map.setLayoutProperty(`basemap-${key}-overlay-${i}-layer`, "visibility", "none"));
    }
    const wantSectional = map.getZoom() >= AVIATION_CROSSOVER_ZOOM;
    map.setLayoutProperty("basemap-topo-layer", "visibility", wantSectional ? "none" : "visible");
    map.setLayoutProperty("basemap-sectional-layer", "visibility", wantSectional ? "visible" : "none");
    setAviationOverlays(!wantSectional);
    return;
  }
  aviationModeActive = false;
  setAviationOverlays(false);
  for (const [key, cfg] of Object.entries(BASEMAPS)) {
    const visible = key === selected;
    map.setLayoutProperty(`basemap-${key}-layer`, "visibility", visible ? "visible" : "none");
    (cfg.overlays ?? []).forEach((_, i) => {
      map.setLayoutProperty(`basemap-${key}-overlay-${i}-layer`, "visibility", visible ? "visible" : "none");
    });
  }
});

const markers = new Map(); // tail_number -> maplibregl.Marker
const knownAircraft = new Set(); // tail_numbers from /api/aircraft -- see "unlisted" highlighting below
const callsignByTail = new Map(); // tail_number -> callsign, so a loaded-but-not-yet-reporting tail still shows its callsign in the sidebar
const defaultPicByTail = new Map(); // tail_number -> default_pic_name, from roster upload's optional pilot/instructor column
const latestByTail = new Map(); // tail_number -> last position payload, for the side panel
// tail_number -> Aircraft.id, added 2026-10-18 (mission-lifecycle plan item 3)
// so the per-aircraft "Remove from mission" button has something to call
// DELETE /api/aircraft/{id} with -- loadKnownAircraft() previously discarded
// this id entirely since nothing needed it before.
const aircraftIdByTail = new Map();
// tail_number -> display-only wing-name hint (app/integrations/wing_lookup.py,
// added 2026-10-18) -- never used for anything but the badge below.
const wingHintByTail = new Map();

// Airfield icon/label tiering (restored to a finer breakdown 2026-09-14 per Gerry:
// "all the airport icons are noisy... use the same logic found on r815 EWMC to
// display a graduated number of airfields based on zoom" -- ported from EWMC's
// actual TIER_MIN_ZOOM there ({1:0, 2:3, 3:3, 4:7, 5:12}), collapsed from EWMC's
// 5 tiers to AvTrack's computed 5 (see app/api/airfields.py compute_tier() for the
// exact thresholds and why tier 5 exists here at all -- EWMC excludes those
// airfields outright, AvTrack deliberately doesn't). Unlike the 2026-09-13
// simplification, this now gates the DOT layer too, not just labels -- that's the
// actual fix for "noisy," since dots previously always showed regardless of zoom
// or tier. `minZoom` is precomputed per feature here and applied via an imperative
// map.setFilter() on zoom change (see updateAirfieldZoomFilter below), rather than
// a GL style zoom-expression, to keep the logic in one obviously-testable place.
// Tier 2 moved from minZoom 3 to 0 (2026-09-14, same day) per Gerry: "At min zoom
// (all CONUS where we start) I'd display MIL and major air carrier hubs, then add
// to those" -- previously tier 2 only started at zoom 3, which happened to be
// under the map's default startup zoom (3.2) so it *looked* right on first load,
// but zooming out further than that dropped major hubs while military stayed --
// not the paired floor Gerry wants. Tiers 1 and 2 now share the same zoom-0 floor.
// Rebalanced 2026-10-25 per Gerry: "we're not seeing more and smaller
// airfields as we zoom in." The filter itself was working correctly -- the
// thresholds just had two dead zones that made it LOOK broken across the
// zoom range people actually use: nothing new appeared anywhere between z7
// and z12 (a 5-level gap), and tier 5 (the "smaller" fields -- ~78% of all
// airfields) didn't unlock until z14, well past typical regional/single-wing
// usage (project memory: AvTrack is usually opened at that scale, not
// CONUS-wide). Spaced to 2-3 zoom levels apart instead, so each step in
// actually reveals something new.
const TIER_MIN_ZOOM = { 1: 0, 2: 0, 3: 6, 4: 9, 5: 11 };

// Airfield LABEL tier coloring (REQUIREMENTS.md 3.2, added 2026-10-15) -- per
// Gerry: "just like EWMC, Airfield tier (mil vs civ) should be reflected on the
// LABEL color and not a radio button selection. It's toggled with labels." Ported
// from EWMC's `.apt-t1`..`.apt-t5` CSS pill classes
// (enhanced_weather_map_complete.html on r815, lines ~124-156): military is always
// tier 1 (red), civilian airfields fall into tiers 2-5 by hub/runway length --
// same `tier` field airfields-layer's dot used to be colored by (see below for why
// that moved here). EWMC renders these as a colored CSS background pill on a
// Leaflet divIcon; MapLibre's native symbol-layer text has no background-box
// equivalent, so this approximates the pill with text-color + a heavier
// text-halo-color outline rather than a true filled background -- a deliberate
// adaptation to the GL renderer, not a claim of pixel parity with EWMC's HTML
// markers. Applied once, statically, on the label layer itself (not touched by
// setAirfieldDisplayMode) so it shows whenever labels are on, independent of
// whichever dot-coloring mode (Flight Impacts/Station Plot) is selected -- exactly
// the "not a radio button selection" part of the ask.
const AIRFIELD_TIER_TEXT_COLORS = { 1: "#ffffff", 2: "#ffffff", 3: "#111111", 4: "#111111", 5: "#333333" };

// EWMC-style boxed labels (redesign, 2026-10-15) -- the text-color/halo approximation
// above wasn't enough per Gerry: "look at how EWMC displays labels (in a box with a
// tier-related background)... What we've got for labels might work but isn't currently
// looking good." MapLibre's symbol-layer text has no native CSS-style background-box
// property; the actual native mechanism for this is icon-image + icon-text-fit:"both"
// -- register a small raster icon per tier via map.addImage() (see
// registerAirfieldTierBadges below) and let GL stretch it to the text's own bbox.
// Fill colors ported directly from EWMC's .apt-t1..apt-t5 pill classes
// (enhanced_weather_map_complete.html on r815); borders are EWMC's border colors for
// the same classes, since halo alone couldn't show a true border.
const AIRFIELD_TIER_BADGE_FILL = {
  1: "rgba(200,30,30,0.93)",
  2: "rgba(25,80,200,0.93)",
  3: "rgba(220,220,220,0.93)",
  4: "rgba(150,200,150,0.90)",
  5: "rgba(200,200,255,0.88)",
};
const AIRFIELD_TIER_BADGE_BORDER = { 1: "#800000", 2: "#003a99", 3: "#888888", 4: "#669966", 5: "#9999aa" };

function makeTierBadgeImage(fill, border) {
  const w = 32,
    h = 20,
    r = 7;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.beginPath();
  ctx.moveTo(r, 0);
  ctx.arcTo(w, 0, w, h, r);
  ctx.arcTo(w, h, 0, h, r);
  ctx.arcTo(0, h, 0, 0, r);
  ctx.arcTo(0, 0, w, 0, r);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = border;
  ctx.stroke();
  return ctx.getImageData(0, 0, w, h);
}

// Must run before airfields-label is created (map.on("load", refreshAirfields) below)
// so icon-image's match expression always resolves against already-registered images --
// registered in the same "load" handler as mapStyleReady, which fires first.
function registerAirfieldTierBadges() {
  for (const tier of [1, 2, 3, 4, 5]) {
    const id = `airfield-tier-badge-${tier}`;
    if (!map.hasImage(id)) {
      map.addImage(id, makeTierBadgeImage(AIRFIELD_TIER_BADGE_FILL[tier], AIRFIELD_TIER_BADGE_BORDER[tier]));
    }
  }
}

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
        minZoom: TIER_MIN_ZOOM[a.tier] ?? 14,
      },
    })),
  };
}

function updateAirfieldZoomFilter() {
  if (!map.getLayer("airfields-layer")) return;
  const zoom = map.getZoom();
  // Dots: gated by tier/zoom now too (this is the actual "noisy icons" fix --
  // previously every airfield's dot always showed regardless of zoom or tier, only
  // the label was zoom-gated).
  map.setFilter("airfields-layer", ["<=", ["get", "minZoom"], zoom]);
  map.setFilter("airfields-label", ["<=", ["get", "minZoom"], zoom]);
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
  const maxTier = maxAirfieldTierForZoom(map.getZoom());
  try {
    const res = await fetch(`/api/airfields?bounds=${bounds}&max_tier=${maxTier}`);
    const rows = await res.json();
    if (requestId !== airfieldsRequestId) return; // superseded by a newer request
    latestAirfieldRows = rows; // reused by the Flight Impacts/Station Plot weather modes below
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
          // Sized up 2026-09-14 per Gerry: "the dots for the airfields displayed
          // are too small to discern." The old scale (1.5px at zoom 5, clamped to
          // that below zoom 5 -- MapLibre holds the first stop's value below its
          // lowest zoom) was barely visible at the map's own 3.2 startup zoom.
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 4, 6, 5, 10, 7, 14, 10],
          // Neutral as of 2026-10-15 -- mil/civ moved to the LABEL's tier coloring
          // below (see AIRFIELD_TIER_TEXT_COLORS/AIRFIELD_TIER_BADGE_FILL), per
          // Gerry's "not a radio button selection" direction. This is also what
          // setAirfieldDisplayMode("tier") resets it to.
          "circle-color": "#5b6b7a",
          "circle-stroke-color": "#fff",
          "circle-stroke-width": 1.5,
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
          // EWMC-style boxed background -- see registerAirfieldTierBadges above for why
          // icon-image+icon-text-fit is the mechanism and how the icons were generated.
          "icon-image": [
            "match",
            ["get", "tier"],
            1,
            "airfield-tier-badge-1",
            2,
            "airfield-tier-badge-2",
            3,
            "airfield-tier-badge-3",
            4,
            "airfield-tier-badge-4",
            "airfield-tier-badge-5",
          ],
          "icon-text-fit": "both",
          "icon-text-fit-padding": [3, 6, 3, 6],
        },
        paint: {
          "text-color": ["match", ["get", "tier"], 1, AIRFIELD_TIER_TEXT_COLORS[1], 2, AIRFIELD_TIER_TEXT_COLORS[2], 3, AIRFIELD_TIER_TEXT_COLORS[3], 4, AIRFIELD_TIER_TEXT_COLORS[4], AIRFIELD_TIER_TEXT_COLORS[5]],
        },
      });
      map.on("click", "airfields-layer", (e) => {
        const p = e.features[0].properties;
        const rwy = p.longest_runway_ft
          ? `${p.longest_runway_ft} ft, ${p.hard_surface_available ? "hard surface" : "not hard-surfaced"}`
          : "runway data not loaded";
        const headerHtml = `<strong>${p.name || p.label}</strong>${p.is_military ? " (MIL)" : ""}<br>${p.icao_id || ""} ${p.faa_id ? `(${p.faa_id})` : ""}<br>${rwy}`;
        showAirfieldWeatherPopup(e.lngLat, p.icao_id, headerHtml);
      });
    }
    updateAirfieldZoomFilter();
    // Piggyback the active weather mode's refetch on this same debounced bbox
    // refresh (REQUIREMENTS.md 3.3a) rather than running a second moveend timer --
    // both need latestAirfieldRows to be current anyway.
    if (airfieldDisplayMode === "impacts") fetchFlightImpactsData();
    else if (airfieldDisplayMode === "station-plot") fetchStationPlotData();
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
  updateAirfieldZoomFilter();
});

// ── Airfield weather display modes: Flight Impacts & Station Plot ──────────
// REQUIREMENTS.md 3.3a. Mutually exclusive with the default tier-colored dots
// above (Gerry's explicit call, 2026-10-14) -- Tier colors / Flight Impacts /
// Station Plot are one radio-group mode, not independent stackable toggles, so
// nothing fights over the same dot's fill color. Both weather modes depend on
// WxCOP (host r815) via the backend's /api/weather/* proxy
// (app/api/weather.py -> app/integrations/wxcop_client.py), which can go down
// independently of AvTrack itself (data2, WxCOP's own DB host, had an
// extended outage starting 2026-10-14 and is mid-restart as of the same day) --
// both fetch functions below are written to degrade visibly, not silently or
// as an error, whenever `available` comes back false.

// Flight Impacts (CAPR 70-1, migrating to 70-10) -- WxCOP's own weather-impacts
// API already classifies each airport GREEN/YELLOW/RED/UNKNOWN (FRO / SFRO /
// CAP-DO-via-NOC approval authority, confirmed live against weather_impacts_api.py
// on r815), so this just maps those labels to colors rather than re-deriving the
// stoplight logic here.
const FLIGHT_IMPACTS_COLORS = { GREEN: "#2e7d32", YELLOW: "#f9a825", RED: "#c62828" };
const FLIGHT_IMPACTS_FALLBACK = "#808080"; // no WxCOP data for this airfield (most military fields included) or WxCOP unreachable

// Reused as-is from WxCOP/EWMC's station plots -- per Gerry (2026-10-14), these
// also match the USAF/ForeFlight (and likely FAA) flight-category convention, not
// just WxCOP's own choice, so there's a second reason to keep them rather than
// picking an AvTrack-specific palette.
const FLIGHT_CATEGORY_COLORS = { VFR: "#00b300", MVFR: "#0099ff", IFR: "#cc0000", LIFR: "#ff00ff", unknown: "#808080" };

// Deliberately independent of TIER_MIN_ZOOM above (Gerry's explicit call,
// 2026-10-14: station plots get their own tiering, not reused from the dot
// layer's tier field) -- keyed by station_plot_tier (app/api/airfields.py
// compute_station_plot_tier(), mirrors EWMC's own military/hub/regional/local/
// small classification). Thresholds start lower (more detail at a more
// zoomed-out view) than EWMC's own values on purpose: EWMC has to cover the
// whole CONUS/multiple regions at once, while AvTrack is typically opened
// already scoped to one wing (state-sized) or occasionally a region (several
// wings) -- see Gerry's FLWG Hurricane Isaias example. Starting point, not yet
// tuned against real field use.
const STATION_PLOT_MIN_ZOOM = { 1: 0, 2: 0, 3: 4, 4: 6, 5: 6 };
const STATION_PLOT_FULL_ZOOM_OFFSET = 3; // full plot at minZoom+this; barb-only between minZoom and that

// Server-side row-count reduction (added 2026-11-02, found live) -- the backend
// has supported a max_tier filter since this endpoint was written
// (app/api/airfields.py), but refreshAirfields() below never sent it, so every
// call fetched and serialized all ~22k airfields regardless of zoom, then
// filtered client-side after the fact. Uses whichever of TIER_MIN_ZOOM /
// STATION_PLOT_MIN_ZOOM is MORE PERMISSIVE at each tier -- they're deliberately
// decoupled (see both tables' comments above) and Station Plot's thresholds are
// uniformly lower, so taking the min of the two per tier guarantees this never
// excludes a row either display mode would still want to show; client-side
// filtering below is unchanged and still the real gate for what's drawn.
function maxAirfieldTierForZoom(zoom) {
  let maxTier = 1;
  for (let t = 1; t <= 5; t++) {
    if (zoom >= Math.min(TIER_MIN_ZOOM[t], STATION_PLOT_MIN_ZOOM[t])) maxTier = t;
  }
  return maxTier;
}

let airfieldDisplayMode = "tier"; // "tier" | "impacts" | "station-plot"
let latestAirfieldRows = []; // kept in sync by refreshAirfields; reused here instead of a second bbox fetch
let latestMetarByIcao = {};
let metarAvailable = false;
const stationPlotMarkers = new Map(); // airfield id -> maplibregl.Marker
let weatherPollInterval = null;
const WEATHER_POLL_MS = 2 * 60 * 1000; // matches EWMC's own refresh cadence

function currentBoundsParam() {
  const b = map.getBounds();
  return `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
}

function clearStationPlotMarkers() {
  for (const marker of stationPlotMarkers.values()) marker.remove();
  stationPlotMarkers.clear();
}

async function fetchFlightImpactsData() {
  const statusEl = document.getElementById("airfield-mode-status");
  try {
    const res = await fetch(`/api/weather/impacts?bounds=${currentBoundsParam()}`);
    const data = await res.json();
    if (!map.getLayer("airfields-layer")) return;
    if (!data.available) {
      map.setPaintProperty("airfields-layer", "circle-color", FLIGHT_IMPACTS_FALLBACK);
      statusEl.textContent = "Flight Impacts unavailable (WxCOP unreachable)";
      return;
    }
    // icao_id-keyed match expression -- airfields absent here (including most
    // military fields, which WxCOP's reporting-station data mostly doesn't cover)
    // fall through to the neutral fallback rather than a forced tier color.
    const matchExpr = ["match", ["get", "icao_id"]];
    let count = 0;
    for (const a of data.airports) {
      if (!a.station_id) continue;
      matchExpr.push(a.station_id, FLIGHT_IMPACTS_COLORS[a.color] || FLIGHT_IMPACTS_FALLBACK);
      count++;
    }
    matchExpr.push(FLIGHT_IMPACTS_FALLBACK);
    map.setPaintProperty("airfields-layer", "circle-color", count > 0 ? matchExpr : FLIGHT_IMPACTS_FALLBACK);
    statusEl.textContent = `Flight Impacts (${count} stations)`;
  } catch (err) {
    console.error("Failed to load Flight Impacts", err);
    statusEl.textContent = "Flight Impacts unavailable";
  }
}

// Calm (<3kt, WxCOP's own threshold) draws an open circle; otherwise a shaft with
// pennants (50kt) / full barbs (10kt) / half barbs (5kt) built up from the speed
// rounded to the nearest 5kt -- same construction as EWMC's windBarbSvg(), ported
// from Leaflet divIcon HTML to a MapLibre DOM marker. Direction is applied by the
// caller via a CSS transform:rotate() on the wrapping element, not baked into the
// path, so this one function serves any direction.
function windBarbSvg(speedKts, color) {
  if (speedKts == null || speedKts < 3) {
    return `<svg width="20" height="30" viewBox="0 0 20 30"><circle cx="10" cy="15" r="6" fill="none" stroke="${color}" stroke-width="2"/></svg>`;
  }
  const spd = Math.round(speedKts / 5) * 5;
  let remaining = spd;
  const pennants = Math.floor(remaining / 50);
  remaining %= 50;
  const fullBarbs = Math.floor(remaining / 10);
  remaining %= 10;
  const halfBarbs = Math.floor(remaining / 5);

  let marks = "";
  let pos = 2; // distance from the tip (top, feather end) down the shaft toward the station
  for (let i = 0; i < pennants; i++) {
    marks += `<path d="M10,${pos} L17,${pos + 5} L10,${pos + 8} Z" fill="${color}"/>`;
    pos += 8;
  }
  for (let i = 0; i < fullBarbs; i++) {
    marks += `<line x1="10" y1="${pos}" x2="17" y2="${pos + 5}" stroke="${color}" stroke-width="2"/>`;
    pos += 5;
  }
  for (let i = 0; i < halfBarbs; i++) {
    marks += `<line x1="10" y1="${pos}" x2="13.5" y2="${pos + 2.5}" stroke="${color}" stroke-width="2"/>`;
    pos += 5;
  }
  return `<svg width="20" height="30" viewBox="0 0 20 30">
    <line x1="10" y1="2" x2="10" y2="28" stroke="${color}" stroke-width="2"/>
    ${marks}
  </svg>`;
}

// Standard station-model sky-cover coverage circle (SKC/CLR open, FEW/SCT partial
// fill, BKN mostly filled with a cut wedge, OVC fully filled) -- colored by flight
// category rather than black/white, EWMC's own deliberate deviation from the
// synoptic standard, carried over per Gerry's direction to match it. Hand-rolled
// pie-slice approximation, not a byte-for-byte port of EWMC's exact paths (those
// weren't extracted) -- close enough to the same visual convention.
function skyCoverSvg(skyConditions, color) {
  const covers = (skyConditions || []).map((c) => c.cover);
  let cover = "SKC";
  if (covers.includes("OVC")) cover = "OVC";
  else if (covers.includes("BKN")) cover = "BKN";
  else if (covers.includes("SCT")) cover = "SCT";
  else if (covers.includes("FEW")) cover = "FEW";

  if (cover === "SKC" || cover === "CLR" || covers.length === 0) {
    return `<svg width="20" height="20" viewBox="0 0 20 20"><circle cx="10" cy="10" r="9" fill="none" stroke="${color}" stroke-width="2"/></svg>`;
  }
  if (cover === "OVC") {
    return `<svg width="20" height="20" viewBox="0 0 20 20"><circle cx="10" cy="10" r="9" fill="${color}"/></svg>`;
  }
  if (cover === "BKN") {
    return `<svg width="20" height="20" viewBox="0 0 20 20">
      <circle cx="10" cy="10" r="9" fill="${color}"/>
      <path d="M10,10 L10,1 A9,9 0 0,0 2.4,15 Z" fill="#14181c"/>
    </svg>`;
  }
  if (cover === "SCT") {
    return `<svg width="20" height="20" viewBox="0 0 20 20">
      <circle cx="10" cy="10" r="9" fill="none" stroke="${color}" stroke-width="2"/>
      <path d="M10,10 L10,1 A9,9 0 0,1 17.6,15 Z" fill="${color}"/>
    </svg>`;
  }
  // FEW
  return `<svg width="20" height="20" viewBox="0 0 20 20">
    <circle cx="10" cy="10" r="9" fill="none" stroke="${color}" stroke-width="2"/>
    <path d="M10,10 L10,1 A9,9 0 0,1 15.36,4.64 Z" fill="${color}"/>
  </svg>`;
}

// Lowest BKN/OVC layer's height_ft -- the bulk METAR response has no precomputed
// ceiling field, same derivation EWMC does client-side.
function deriveCeilingFt(skyConditions) {
  const heights = (skyConditions || [])
    .filter((c) => c.cover === "BKN" || c.cover === "OVC")
    .map((c) => c.height_ft)
    .filter((h) => h != null);
  return heights.length ? Math.min(...heights) : null;
}

function stationPlotSize(showFullPlot) {
  return showFullPlot ? [70, 44] : [34, 34];
}

// Inner HTML only -- the wrapper element (size/positioning) is created once by
// renderStationPlotMarkers() and mutated in place on update, same pattern as
// upsertMarker's aircraft dot/datablock, rather than rebuilt from scratch each
// time. Layout matches EWMC's own full-plot convention (temp upper-left, dewpoint
// lower-left, combined wind-barb+sky-cover symbol center with the barb drawn
// rotated on top of the sky-cover circle, ceiling upper-right, visibility
// lower-right, gust bottom-center in a fixed hazard red regardless of flight
// category) -- these are Gerry's own deliberate WxCOP design choices, carried
// over faithfully rather than "corrected" to the textbook synoptic layout.
function buildStationPlotInnerHtml(metar, showFullPlot) {
  const color = FLIGHT_CATEGORY_COLORS[metar.flight_category] || FLIGHT_CATEGORY_COLORS.unknown;
  const dir = metar.wind_dir ?? 0;
  const barbLeft = showFullPlot ? 18 : 7;
  const barb = `<div style="position:absolute; top:0; left:${barbLeft}px; transform:rotate(${dir}deg);">${windBarbSvg(metar.wind_speed_kts, color)}</div>`;
  if (!showFullPlot) return barb;

  const sky = `<div style="position:absolute; top:0; left:18px;">${skyCoverSvg(metar.sky_conditions, color)}</div>`;
  const tempC = metar.temp_c != null ? Math.round(metar.temp_c) : "—";
  const dewC = metar.dewpoint_c != null ? Math.round(metar.dewpoint_c) : "—";
  const ceilFt = deriveCeilingFt(metar.sky_conditions);
  const ceilTxt = ceilFt != null ? `C${Math.round(ceilFt / 100)}` : "";
  const visTxt = metar.visibility_sm != null ? `${metar.visibility_sm}SM` : "";
  const gustTxt = metar.wind_gust_kts ? `G${Math.round(metar.wind_gust_kts)}` : "";
  return `
    <div class="sp-field" style="position:absolute; left:0; top:8px; width:16px; text-align:right;">${tempC}</div>
    <div class="sp-field" style="position:absolute; left:0; top:20px; width:16px; text-align:right;">${dewC}</div>
    ${sky}
    ${barb}
    <div class="sp-field" style="position:absolute; left:52px; top:2px;">${ceilTxt}</div>
    <div class="sp-field" style="position:absolute; left:52px; top:22px;">${visTxt}</div>
    <div class="sp-field sp-gust" style="position:absolute; left:18px; top:26px; width:34px; text-align:center;">${gustTxt}</div>`;
}

// Click-to-see-METAR/TAF (REQUIREMENTS.md 3.3a, added 2026-10-15) -- per Gerry:
// "like EWMC we need to be able to click on an airfield and see its METAR and
// TAF if available." Deliberately mirrors EWMC's own actual split rather than
// inventing a new one (read directly from enhanced_weather_map_complete.html on
// r815, not guessed): Station Plot mode already draws the METAR on the marker
// itself, so its click only needs the lightweight nearest-TAF lookup
// (showNearestTafPopup below); every other mode's click needs the full detail
// (showAirfieldWeatherPopup), since nothing else on screen already shows it.
function formatRawWxText(raw) {
  // METAR/TAF raw text often has embedded newlines (TAF FM-groups especially) --
  // escape for HTML then let CSS white-space:pre-wrap render them, same as EWMC's
  // formatTAF.
  const esc = (raw || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return `<pre style="white-space:pre-wrap; font: 11px ui-monospace, monospace; margin:4px 0 0; max-height:180px; overflow-y:auto;">${esc}</pre>`;
}

async function showNearestTafPopup(lngLat, metar) {
  const popup = new maplibregl.Popup({ closeButton: true })
    .setLngLat(lngLat)
    .setHTML(`<div style="font-size:12px; min-width:220px;"><strong>${metar.station_id}</strong><br>Loading nearest TAF…</div>`)
    .addTo(map);
  try {
    const res = await fetch(`/api/weather/taf-nearest?lat=${metar.latitude}&lon=${metar.longitude}`);
    const data = await res.json();
    if (!data.available || !data.found) {
      popup.setHTML(
        `<div style="font-size:12px; min-width:220px;"><strong>${metar.station_id}</strong><hr>` +
          `<span style="color:#888;">${data.error || "TAF unavailable"}</span></div>`
      );
      return;
    }
    const srcNote =
      data.station_id === metar.station_id
        ? ""
        : ` <span style="color:#888; font-weight:normal;">(nearest TAF: ${data.station_id}, ${data.distance_nm}nm)</span>`;
    popup.setHTML(
      `<div style="font-size:12px; min-width:260px;"><strong>${metar.station_id}</strong>${srcNote}${formatRawWxText(data.raw_text)}</div>`
    );
  } catch (err) {
    popup.setHTML(`<div style="font-size:12px;"><strong>${metar.station_id}</strong><hr><span style="color:#888;">TAF lookup failed</span></div>`);
  }
}

async function showAirfieldWeatherPopup(lngLat, icaoId, headerHtml) {
  const popup = new maplibregl.Popup({ closeButton: true })
    .setLngLat(lngLat)
    .setHTML(`<div style="font-size:12px; min-width:220px;">${headerHtml}<br>Loading weather…</div>`)
    .addTo(map);
  if (!icaoId) {
    popup.setHTML(`<div style="font-size:12px; min-width:220px;">${headerHtml}<br><span style="color:#888;">No station ID on file</span></div>`);
    return;
  }
  try {
    const res = await fetch(`/api/weather/station/${icaoId}`);
    const data = await res.json();
    if (!data.available) {
      popup.setHTML(`<div style="font-size:12px; min-width:220px;">${headerHtml}<br><span style="color:#888;">Weather unavailable (WxCOP unreachable)</span></div>`);
      return;
    }
    const obs = data.metars && data.metars[0];
    const taf = data.taf;
    if (!obs && !taf) {
      popup.setHTML(
        `<div style="font-size:12px; min-width:220px;">${headerHtml}<br><span style="color:#888;">${data.metar_note || "No recent METAR or TAF"}</span></div>`
      );
      return;
    }
    let metarHtml = "";
    if (obs) {
      const color = FLIGHT_CATEGORY_COLORS[obs.flight_category] || FLIGHT_CATEGORY_COLORS.unknown;
      let wind = `${obs.wind_dir ?? "---"}° @ ${obs.wind_speed_kts ?? 0}kt`;
      if (obs.wind_gust_kts) wind += ` G${obs.wind_gust_kts}`;
      const temp = obs.temp_c != null ? `${obs.temp_c}°C` : "N/A";
      const dew = obs.dewpoint_c != null ? `${obs.dewpoint_c}°C` : "N/A";
      metarHtml = `
        <div style="margin-top:4px;">
          <span style="color:${color}; font-weight:700;">${obs.flight_category || ""}</span>
          &nbsp;Wind ${wind}&nbsp;·&nbsp;${temp}/${dew}&nbsp;·&nbsp;Vis ${obs.visibility_sm ?? "?"}SM
        </div>
        ${formatRawWxText(obs.raw_text)}`;
    } else if (data.metar_note) {
      metarHtml = `<div style="color:#888; margin-top:4px;">${data.metar_note}</div>`;
    }
    let tafHtml = "";
    if (taf && taf.raw_text) {
      const tafSrcNote =
        data.station && taf.station_id && taf.station_id !== data.station.station_id
          ? ` <span style="color:#888; font-weight:normal;">(via ${taf.station_id})</span>`
          : "";
      tafHtml = `<hr style="margin:6px 0;"><strong>TAF</strong>${tafSrcNote}${formatRawWxText(taf.raw_text)}`;
    }
    const kqAssoc = data.kq_association;
    const kqHtml = kqAssoc
      ? `<div style="background:#1a3a5c; color:#7ec8e3; padding:3px 6px; border-radius:3px; font-size:10px; margin-top:4px;">KQ Association: ${kqAssoc.kq_station} ↔ ${kqAssoc.host_station}${kqAssoc.notes ? ` — ${kqAssoc.notes}` : ""}</div>`
      : "";
    popup.setHTML(`<div style="font-size:12px; min-width:260px;">${headerHtml}${metarHtml}${kqHtml}${tafHtml}</div>`);
  } catch (err) {
    popup.setHTML(`<div style="font-size:12px; min-width:220px;">${headerHtml}<br><span style="color:#888;">Weather lookup failed</span></div>`);
  }
}

async function fetchStationPlotData() {
  try {
    const res = await fetch(`/api/weather/metar?bounds=${currentBoundsParam()}`);
    const data = await res.json();
    metarAvailable = !!data.available;
    latestMetarByIcao = {};
    if (metarAvailable) {
      for (const m of data.metars) latestMetarByIcao[m.station_id] = m;
    }
  } catch (err) {
    console.error("Failed to load station plot METAR", err);
    metarAvailable = false;
    latestMetarByIcao = {};
  }
  renderStationPlotMarkers();
}

// Re-renders from already-fetched data (latestAirfieldRows/latestMetarByIcao) --
// no network call. Called instantly on zoom (barb-only <-> full-plot tier
// transitions) and after every fetchStationPlotData()/refreshAirfields() update;
// the network refetch itself stays on refreshAirfields's existing debounced
// moveend cadence rather than a second timer.
function renderStationPlotMarkers() {
  if (airfieldDisplayMode !== "station-plot") return;
  const statusEl = document.getElementById("airfield-mode-status");
  const zoom = map.getZoom();
  const seen = new Set();
  for (const a of latestAirfieldRows) {
    try {
      const minZ = STATION_PLOT_MIN_ZOOM[a.station_plot_tier] ?? 6;
      if (zoom < minZ) continue;
      seen.add(a.id);
      const metar = a.icao_id ? latestMetarByIcao[a.icao_id] : null;
      const showFull = zoom >= minZ + STATION_PLOT_FULL_ZOOM_OFFSET;
      const [w, h] = metar ? stationPlotSize(showFull) : [10, 10];
      const innerHtml = metar ? buildStationPlotInnerHtml(metar, showFull) : "";

      let marker = stationPlotMarkers.get(a.id);
      if (!marker) {
        const el = document.createElement("div");
        el.addEventListener("click", (e) => {
          // stopPropagation is required here -- Popup defaults to closeOnClick:true,
          // and a marker click bubbles up through the DOM to the map container
          // MapLibre itself listens on, so without this the same click that opens
          // the popup immediately closes it again (found live 2026-10-15: the
          // popup never appeared, not even a "Loading..." flash, same pattern the
          // existing discovered-marker click handler below already guards against).
          e.stopPropagation();
          // Station Plot mode: the METAR is already drawn on the marker itself, so
          // the click only adds TAF (EWMC's showNearestTafPopup) -- for a no-data
          // dot there's no METAR to anchor that lookup on text/label, so fall back
          // to the full station-detail popup instead (still useful: a "no METAR"
          // field can still have its own or a KQ-associated TAF).
          if (marker._metar) {
            showNearestTafPopup([a.longitude, a.latitude], marker._metar);
          } else {
            const headerHtml = `<strong>${a.icao_id || a.faa_id || a.name}</strong>${a.is_military ? " (MIL)" : ""}`;
            showAirfieldWeatherPopup([a.longitude, a.latitude], a.icao_id, headerHtml);
          }
        });
        marker = new maplibregl.Marker({ element: el }).setLngLat([a.longitude, a.latitude]).addTo(map);
        stationPlotMarkers.set(a.id, marker);
      }
      const el = marker.getElement();
      el.className = metar ? "station-plot-marker" : "station-plot-marker station-plot-nodata";
      el.style.width = `${w}px`;
      el.style.height = `${h}px`;
      el.innerHTML = innerHtml;
      marker._metar = metar;
    } catch (err) {
      // One bad row (e.g. unexpected METAR shape) shouldn't blank the whole display --
      // found live 2026-10-15 debugging "station plots have no text data": a single
      // uncaught exception partway through this loop was silently killing every
      // marker after (and including) the failing one, with nothing surfaced to the
      // user beyond an empty map. Log it and keep going.
      console.error("Station plot marker failed for", a.icao_id || a.id, err);
    }
  }
  // Drop markers for airfields that fell out of the viewport/zoom-tier range.
  for (const [id, marker] of stationPlotMarkers.entries()) {
    if (!seen.has(id)) {
      marker.remove();
      stationPlotMarkers.delete(id);
    }
  }
  statusEl.textContent = metarAvailable
    ? `Station Plot (${Object.keys(latestMetarByIcao).length} reporting)`
    : "Station Plot: METAR unavailable (WxCOP unreachable) — showing airfields only";
}

function setAirfieldDisplayMode(mode) {
  airfieldDisplayMode = mode;
  clearInterval(weatherPollInterval);
  weatherPollInterval = null;
  const statusEl = document.getElementById("airfield-mode-status");

  if (mode === "tier") {
    clearStationPlotMarkers();
    if (map.getLayer("airfields-layer")) {
      map.setLayoutProperty("airfields-layer", "visibility", "visible");
      map.setPaintProperty("airfields-layer", "circle-color", "#5b6b7a"); // neutral -- mil/civ now lives on the label, see AIRFIELD_TIER_TEXT_COLORS
    }
    statusEl.textContent = "";
    return;
  }
  if (mode === "impacts") {
    clearStationPlotMarkers();
    if (map.getLayer("airfields-layer")) map.setLayoutProperty("airfields-layer", "visibility", "visible");
    fetchFlightImpactsData();
    weatherPollInterval = setInterval(fetchFlightImpactsData, WEATHER_POLL_MS);
    return;
  }
  // station-plot
  if (map.getLayer("airfields-layer")) map.setLayoutProperty("airfields-layer", "visibility", "none");
  fetchStationPlotData();
  weatherPollInterval = setInterval(fetchStationPlotData, WEATHER_POLL_MS);
}

for (const el of document.querySelectorAll('input[name="airfield-mode"]')) {
  el.addEventListener("change", (e) => {
    if (e.target.checked) setAirfieldDisplayMode(e.target.value);
  });
}

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
  if (!mapStyleReady) {
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

// Added 2026-10-16 alongside mission-switching -- a mission with no AO set
// must not keep showing the PREVIOUS mission's circle on the map.
function clearOpsArea() {
  const source = map.getSource("ops-area");
  if (source) source.setData({ type: "FeatureCollection", features: [] });
}

async function loadKnownAircraft() {
  try {
    const res = await fetch(withMissionParam("/api/aircraft"));
    const rows = await res.json();
    rows.forEach((a) => {
      knownAircraft.add(a.tail_number);
      if (a.default_pic_name) defaultPicByTail.set(a.tail_number, a.default_pic_name);
      if (a.callsign) callsignByTail.set(a.tail_number, a.callsign);
      aircraftIdByTail.set(a.tail_number, a.id);
      if (a.wing_hint) wingHintByTail.set(a.tail_number, a.wing_hint);
    });
    renderPanel(); // the roster itself just changed (e.g. after a roster upload) -- reflect it immediately
  } catch (err) {
    console.error("Failed to load aircraft list", err);
  }
}

// Whether an AO is currently set -- tracked client-side (added 2026-10-15) purely
// so renderDiscoveredSection below can phrase its count correctly ("in AO" vs
// nationwide); the actual filtering is always done server-side.
let aoIsSet = false;
// The AO center, reused as the Bullseye reference point for the Status tab's
// distance/bearing (added 2026-10-26) -- per Gerry, AvTrack has no distinct
// Bullseye concept yet, only this AO center, so that's what's used for now.
let bullseyeCenter = null; // {lat, lon} | null

// Restores the persisted Area of Operations on page load (REQUIREMENTS.md 3.1,
// added 2026-10-14) so a refresh doesn't visually lose the AO the backend still
// has -- the AO is otherwise only (re-)drawn from a fresh locate() call.
async function loadPersistedOpsArea() {
  try {
    const res = await fetch(withMissionParam("/api/discovered/ao"));
    if (!res.ok) return;
    const ao = await res.json();
    aoIsSet = !!ao;
    bullseyeCenter = ao ? { lat: ao.center_latitude, lon: ao.center_longitude } : null;
    if (ao) setOpsArea([ao.center_longitude, ao.center_latitude], ao.radius_nm);
    else clearOpsArea(); // e.g. switched to a mission with no AO set yet
  } catch (err) {
    console.error("Failed to load persisted Area of Operations", err);
  }
}

// Ground/airborne distinction added 2026-10-25 per Gerry (spotted CAP418 sitting
// on the ramp at Fullerton Municipal, hard to tell apart from an airborne tail at
// a glance). Unlisted/unexpected stays red regardless of state -- that's a
// separate "pay attention, unknown aircraft" warning (REQUIREMENTS.md 3.1) and
// should still catch your eye even if the unknown aircraft is parked, not just
// flying. Within known aircraft: gray on the ground, blue airborne.
function markerColor(tail, pos) {
  if (!knownAircraft.has(tail)) return "#d9534f";
  return airborneState(pos) === "ground" ? "#8a8f98" : "#2b6cb0";
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

  marker._dot.style.color = markerColor(pos.tail_number, pos);
  marker._dot.style.transform = `rotate(${pos.heading_deg ?? 0}deg)`;
  // Datablock: callsign on one line, altitude/speed on the next -- ATC-style, per
  // Gerry's requirement that every airborne aircraft always shows this, not just on
  // click (the click target is the sortie panel, see openSortiePanel). Climb/descent
  // caret (per Gerry, 2026-09-13) sits right after altitude, matching real ATC
  // datablock convention -- computed server-side from consecutive position deltas
  // (app/ingestion/poller.py), not read from any single provider field.
  //
  // Callsign-only on the ground (added 2026-10-25 per Gerry): "the whole
  // datablock" (alt/speed line) is reserved for airborne aircraft -- a parked
  // tail's altitude/speed line is noise at best (see the CAP418 ground-speed
  // artifact discussion the same day) and clutters the map alongside every
  // other ramp-sitting aircraft.
  if (airborneState(pos) === "ground") {
    marker._datablock.innerHTML = `<div class="cs">${pos.callsign ?? pos.tail_number}</div>`;
  } else {
    const alt = pos.altitude_ft != null ? Math.round(pos.altitude_ft) : "—";
    const gs = pos.ground_speed_kt != null ? Math.round(pos.ground_speed_kt) : "—";
    marker._datablock.innerHTML = `<div class="cs">${pos.callsign ?? pos.tail_number}</div><div>${alt}ft${verticalTrendArrow(pos.vertical_trend)} ${gs}kt</div>`;
  }

  renderPanel();
}

function verticalTrendArrow(trend) {
  if (trend === "climbing") return "▲";
  if (trend === "descending") return "▼";
  return ""; // "level", or unknown (e.g. no prior position yet to compare against)
}

// altitude_ft > 0 as "airborne" (2026-09-15, REQUIREMENTS.md 3.1) -- both
// providers already normalize an on-ground report to altitude_ft=0.0 (see
// app/adsb/adsb_lol.py's alt_baro=="ground" handling), so this reuses that
// existing convention rather than a new one. An approximation, not physics --
// good enough for "is it flying right now," not a safety-critical distinction.
function airborneState(pos) {
  return pos.altitude_ft != null && pos.altitude_ft > 0 ? "airborne" : "ground";
}

// Discovered (unregistered) CAP/PARD candidates currently flying -- shown
// directly on the map (REQUIREMENTS.md 3.1, added 2026-10-15). Per Gerry: "The
// aircraft should display on-screen when they are flying. If they're not part
// of our ongoing operation, not accepted from the candidates' table, they'd not
// be in an operations table for query." So this is general situational
// awareness, deliberately unscoped by the AO (`ao_filter=false` -- see
// app/api/discovered.py) -- separate from the Ops Board's AO-scoped alert
// section (renderDiscoveredSection below), which stays operation-specific.
// A candidate here has never been polled for a live position (poll_once() in
// app/ingestion/poller.py only ever tracks registered tails) -- its position is
// only as fresh as the last discovery sweep (the fast AO-scoped poller when an
// AO is set, or the slow 22-point CONUS sweep otherwise), so "recently
// discovered" is a proxy for "currently flying," not true live tracking. A
// candidate drops off the map once its last sighting goes stale, same
// reasoning as sweepStaleAircraft above for registered aircraft.
const discoveredMarkers = new Map(); // DiscoveredCallsign.id -> maplibregl.Marker
// History, 2026-10-15: originally 20, then briefly 45 after a bug was found live
// (Gerry: CAP161/CAP2165 visible on ADS-B Exchange weren't showing on our map --
// confirmed via adsb.lol directly that both were actively broadcasting, ruling out
// a coverage gap). Root cause then was that a candidate seen only by the slow
// 22-point CONUS sweep is re-sighted once per ~33min lap, so a 20min window
// guaranteed it would flicker off the map for part of every cycle even while
// flying. Raising it to 45 fixed the flicker but immediately traded it for the
// opposite problem (Gerry, same session): CAP3226 kept showing on our map well
// after it had actually landed, looking exactly as "live" as a fresh sighting, for
// up to 45min. The real fix was the refresh cadence, not this number: every
// already-discovered candidate is now kept near-real-time by a dedicated bulk
// /v2/reg/ poller (app/ingestion/candidate_refresh_poller.py, 30s cadence -- same
// mechanism the registered-aircraft poller already used) instead of waiting on the
// slow sweep's lap, so a still-flying candidate is re-confirmed every ~30s and a
// landed one simply stops updating. With that in place this only needs to be a
// healthy margin over that 30s cadence, not a hedge against a 33min lap -- 5min is
// ~10x the refresh interval, so a false "stale" during ordinary network jitter is
// very unlikely, while a genuinely landed aircraft now goes stale in ~5min instead
// of up to 45.
const DISCOVERED_STALE_MINUTES = 5;
const DISCOVERED_POLL_MS = 45000;
// Reused by renderPanel() (Aircraft Aloft) -- added 2026-11-02 per Gerry: the
// roster-only view rarely matched what was actually flying (36 tracked tails
// vs. whatever subset of CAP-shaped traffic nationwide happens to be up at
// any moment -- confirmed live, near-zero overlap). Rather than a second
// fetch, Aircraft Aloft just reads the same `live` set this already computes.
let latestDiscoveredLive = [];

function approveDiscovered(id) {
  return fetch(withMissionParam(`/api/discovered/${id}/approve`), { method: "POST" })
    .then(() => Promise.all([loadKnownAircraft(), refreshDiscoveredMarkers()]))
    .then(() => {
      if (!document.getElementById("ops-board-panel").hidden) refreshOpsBoard();
    });
}

function dismissDiscovered(id) {
  return fetch(withMissionParam(`/api/discovered/${id}/dismiss`), { method: "POST" })
    .then(() => refreshDiscoveredMarkers())
    .then(() => {
      if (!document.getElementById("ops-board-panel").hidden) refreshOpsBoard();
    });
}

async function refreshDiscoveredMarkers() {
  let rows;
  try {
    const res = await fetch(withMissionParam("/api/discovered?ao_filter=false"));
    rows = await res.json();
  } catch (err) {
    console.error("Failed to load discovered candidates", err);
    return;
  }
  const now = Date.now();
  const live = rows.filter(
    (r) =>
      now - new Date(r.last_seen_at).getTime() < DISCOVERED_STALE_MINUTES * 60000 &&
      r.last_latitude != null &&
      r.last_longitude != null
  );
  latestDiscoveredLive = live;
  renderPanel(); // Aircraft Aloft reuses this set too -- see its declaration above
  const liveIds = new Set(live.map((r) => r.id));
  for (const [id, marker] of discoveredMarkers) {
    if (!liveIds.has(id)) {
      marker.remove();
      discoveredMarkers.delete(id);
    }
  }
  for (const r of live) {
    let marker = discoveredMarkers.get(r.id);
    if (!marker) {
      const wrapper = document.createElement("div");
      wrapper.className = "discovered-marker";
      const dot = document.createElement("div");
      dot.className = "discovered-dot";
      dot.innerHTML = AIRCRAFT_SVG;
      const datablock = document.createElement("div");
      datablock.className = "discovered-datablock";
      wrapper.appendChild(dot);
      wrapper.appendChild(datablock);
      wrapper.addEventListener("click", (e) => {
        e.stopPropagation();
        const ageMin = Math.round((Date.now() - new Date(r.last_seen_at).getTime()) / 60000);
        const popup = new maplibregl.Popup({ closeButton: true })
          .setLngLat(marker.getLngLat())
          .setHTML(
            `<div style="font-size:12px;"><strong>${r.source_label} ${r.callsign}</strong> / ${r.tail_number}<br/>
             Unregistered -- not part of any tracked operation<br/>
             Last seen ${ageMin} min ago -- position/track may be stale, not live<br/>
             <button class="discovered-approve" data-id="${r.id}">Approve</button>
             <button class="discovered-dismiss" data-id="${r.id}">Dismiss</button></div>`
          )
          .addTo(map);
        const el = popup.getElement();
        el.querySelector(".discovered-approve").addEventListener("click", () => {
          approveDiscovered(r.id);
          popup.remove();
        });
        el.querySelector(".discovered-dismiss").addEventListener("click", () => {
          dismissDiscovered(r.id);
          popup.remove();
        });
      });
      marker = new maplibregl.Marker({ element: wrapper }).setLngLat([r.last_longitude, r.last_latitude]).addTo(map);
      marker._datablock = datablock;
      marker._dot = dot;
      marker._wrapper = wrapper;
      discoveredMarkers.set(r.id, marker);
    } else {
      marker.setLngLat([r.last_longitude, r.last_latitude]);
    }
    // Direction of flight -- per Gerry: "the aircraft icon doesn't show what
    // direction it's moving" (2026-10-15). Same rotate-the-whole-SVG convention as
    // the known-aircraft marker above; falls back to unrotated (nose up) when
    // adsb.lol didn't report a track, same as that marker's `?? 0`.
    marker._dot.style.transform = `rotate(${r.last_heading_deg ?? 0}deg)`;
    // Altitude/speed, not tail number -- per Gerry: "the data labels doesn't need the
    // tail number but does need airspeed and altitude" (2026-10-14). Same "—" placeholder
    // convention as the known-aircraft datablock above for rows that predate the
    // last_altitude_ft/last_ground_speed_kt columns or simply haven't had a fresh sighting yet.
    const alt = r.last_altitude_ft != null ? Math.round(r.last_altitude_ft) : "—";
    const gs = r.last_ground_speed_kt != null ? Math.round(r.last_ground_speed_kt) : "—";
    marker._datablock.innerHTML = `<div class="cs">${r.callsign}</div><div>${alt}ft ${gs}kt</div>`;
    // Staleness fade -- found live 2026-10-15: widening DISCOVERED_STALE_MINUTES to
    // 45 (see that constant's comment) fixed still-flying candidates flickering off
    // the map between slow-sweep laps, but it also means a candidate that's actually
    // landed/stopped broadcasting (confirmed live for CAP3226: 0 adsb.lol results,
    // yet still inside the 45min window) now lingers at its last-known position for
    // up to 45min looking exactly as "live" as a fresh sighting. Rather than guess a
    // second cutoff, fade opacity continuously with age so the marker itself discloses
    // its own trustworthiness -- full strength when fresh, visibly faded once stale
    // enough to doubt, same spirit as the "Last seen Xm ago" popup line above.
    const ageMin = (now - new Date(r.last_seen_at).getTime()) / 60000;
    marker._wrapper.style.opacity = String(Math.max(0.35, 1 - ageMin / DISCOVERED_STALE_MINUTES));
  }
}

setInterval(refreshDiscoveredMarkers, DISCOVERED_POLL_MS);

// Reverted 2026-10-18 per Gerry: "the 'aircraft aloft' column should ONLY show
// aircraft that are reporting and airborne" -- on-ground/not-reporting roster
// tails belong on the Ops Board (3.3, which already has its own ground/
// not-reporting states per-sortie), not here. This undoes the 2026-09-15
// change that made this panel show every loaded tail regardless of state;
// that history is kept here only as context for why "aloft" once meant more
// than its name. Unlisted live traffic (not in the roster) still shows --
// that's unaffected by this change, since it was always filtered to
// latestByTail (i.e. already reporting) in the first place.
//
// Extended 2026-11-02 per Gerry: roster-only was rarely useful -- the 36
// tracked tails are scattered nationwide and almost never overlap with
// whatever CAP-shaped traffic actually happens to be flying at any given
// moment (confirmed live, repeatedly: zero overlap). Now also includes
// currently-airborne *discovered* (not-yet-roster) candidates, same source
// already polled for the map's amber/dashed markers (refreshDiscoveredMarkers,
// latestDiscoveredLive). Considered scoping this to the current map viewport
// ("if they're within the viewframe they need to be on the list") but Gerry
// settled on simpler: "if they're airborne anywhere" they belong on the
// list -- no viewport filtering, nationwide, same as the map markers already are.
function renderPanel() {
  const list = document.getElementById("aircraft-list");

  const trackedRows = [...latestByTail.entries()]
    .filter(([, pos]) => airborneState(pos) === "airborne")
    .map(([tail, pos]) => ({ tail, pos, unlisted: !knownAircraft.has(tail), discovered: false }));
  const trackedTails = new Set(trackedRows.map((r) => r.tail));

  const discoveredRows = latestDiscoveredLive
    .filter((r) => r.last_altitude_ft != null && r.last_altitude_ft > 0 && !trackedTails.has(r.tail_number))
    .map((r) => ({
      tail: r.tail_number,
      discoveredId: r.id,
      callsign: r.callsign,
      altitude_ft: r.last_altitude_ft,
      ground_speed_kt: r.last_ground_speed_kt,
      discovered: true,
    }));

  const rows = [...trackedRows, ...discoveredRows];
  if (rows.length === 0) {
    // Distinguish "still connecting" from "connected, just no data yet" -- these look
    // identical to a viewer if both just say "Connecting..." forever, which is
    // actively misleading when e.g. ADS-B polling is paused server-side but the
    // WebSocket itself is fine. See REQUIREMENTS.md dev notes 2026-09-13.
    list.innerHTML = wsConnected
      ? "<em>Connected — no aircraft airborne.</em>"
      : "<em>Connecting…</em>";
    return;
  }

  rows.sort((a, b) => (a.tail ?? "").localeCompare(b.tail ?? ""));

  list.innerHTML = rows
    .map((row) => {
      if (row.discovered) {
        const callsign = row.callsign ?? row.tail;
        const meta = `${Math.round(row.altitude_ft ?? 0)} ft · ${Math.round(row.ground_speed_kt ?? 0)} kt`;
        return `
          <div class="aircraft-row discovered" data-tail="${row.tail}">
            <div class="callsign">
              <span>${callsign}</span>
              <span class="status-badge status-discovered">DISCOVERED</span>
            </div>
            <div class="meta">${meta}</div>
            <button class="aloft-approve-btn" data-id="${row.discoveredId}" title="Add to the tracked aircraft roster">Approve</button>
          </div>`;
      }
      const { tail, pos, unlisted } = row;
      const callsign = pos.callsign ?? callsignByTail.get(tail) ?? tail;
      const meta = `${Math.round(pos.altitude_ft ?? 0)} ft${verticalTrendArrow(pos.vertical_trend)} · ${Math.round(pos.ground_speed_kt ?? 0)} kt`;
      const wingHint = wingHintByTail.get(tail);
      const wingBadge = wingHint ? ` <span class="wing-hint" title="Display hint only, from callsign pattern -- not authoritative">(${wingHint})</span>` : "";
      // Remove-from-mission button (item 3, 2026-10-18 plan) -- only when a
      // mission is active AND this tail is actually linked to it; there's
      // nothing to unlink for a row that was never linked (unlisted), so this
      // deliberately skips withMissionParam()'s optional-fallback pattern.
      const removeBtn =
        currentMissionId && !unlisted
          ? `<button class="aircraft-remove-btn" data-tail="${tail}" title="Remove from this mission">✕</button>`
          : "";
      return `
        <div class="aircraft-row state-airborne ${unlisted ? "unlisted" : ""}" data-tail="${tail}">
          <div class="callsign">
            <span>${callsign}${wingBadge}${unlisted ? " ⚠" : ""}</span>
            <span class="aircraft-row-actions">
              <span class="status-badge status-airborne">AIRBORNE</span>
              ${removeBtn}
            </span>
          </div>
          <div class="meta">${meta}</div>
        </div>`;
    })
    .join("");

  // Row click opens the per-aircraft sortie/comms panel -- same pattern as the
  // Ops Board's own rows (main.js renderOpsBoardTable) and the map marker
  // (upsertMarker). Found live 2026-10-25: this panel's rows had no click
  // handler at all, unlike those other two entry points. Discovered rows are
  // deliberately excluded -- there's no Aircraft/Sortie record yet to open.
  list.querySelectorAll(".aircraft-row:not(.discovered)").forEach((row) => {
    row.addEventListener("click", () => openSortiePanel(row.dataset.tail));
  });

  list.querySelectorAll(".aloft-approve-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      approveDiscovered(btn.dataset.id);
    });
  });

  list.querySelectorAll(".aircraft-remove-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const tail = btn.dataset.tail;
      if (!confirm(`Remove ${tail} from this mission? It stays in the global aircraft roster.`)) return;
      const aircraftId = aircraftIdByTail.get(tail);
      if (!aircraftId) return;
      await fetch(`/api/aircraft/${aircraftId}?mission_id=${encodeURIComponent(currentMissionId)}`, { method: "DELETE" });
      knownAircraft.delete(tail);
      await loadKnownAircraft();
    });
  });
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
//
// Comms quick-select menu (added 2026-10-26, per Gerry's exact radio-call
// vocabulary -- a static list, not a dropdown, deliberately answering the WMIRS
// comms-log anti-pattern memory: "the menu of loggable comms events wasn't
// designed around the actual aircraft-tracking workflow").
//
// Engine Start/Stop ARE in this list (corrected 2026-10-26 -- an earlier version
// of this menu left them out, routing them only through the dedicated Start/Stop
// Sortie forms below. Per Gerry: "That's a COMMS issue per sortie," i.e. the
// same kind of radio call as everything else here, not a separate
// administrative action. It also wasn't just a style preference: for a
// WMIRS-imported planned sortie -- which already exists with a sortie #/mission #
// but no engine_start logged yet -- GET .../sorties/current already returns it
// (status "planned", engine_stop_event_id still null), so the panel shows this
// in-progress view, not the Start Sortie form; the Start Sortie form only ever
// appears when no Sortie row exists at all yet. Without Engine Start in this
// menu, a WMIRS-planned sortie would have had NO way to log engine start at
// all. Fixed properly, not just moved: app/api/events.py's create_event now
// also links Sortie.engine_start_event_id (first wins)/engine_stop_event_id
// (latest wins) for these two types, mirroring takeoff/landing exactly --
// previously that route only ever linked takeoff/landing, so this would have
// silently failed to link the sortie even with the button present.
// "Touch & Go" removed (same date) -- per Gerry, not on his list and not a
// reportable entity; it was carried over unasked from the old raw-event-log's
// button set.
//
// `kind: "event"` -> POST /api/aircraft/{tail}/events (TrackingEvent only, no
// SortieWaypoint -- matches takeoff/landing's existing shape).
// `kind: "waypoint"` -> POST /api/sorties/{id}/waypoints (SortieWaypoint +
// backing TrackingEvent, same as the pre-existing in_grid/out_grid/ops_check).
// `fields` lists the STRUCTURED extras beyond the universal time + comments
// every type gets, per Gerry's exact per-type list:
// "Engine Start should have time of start, and destination or search area.
// Wheels up will have just the time of takeoff. In-grid and Out-of-grid should
// have location, altitude, fuel remaining... Find will have location, time,
// and a description field... RTB call should have location, fuel remaining,
// estimated time enroute." PIREP: location + altitude, confirmed by Gerry.
const COMMS_TYPES = {
  // engine_start/engine_stop use their own dedicated backend routes
  // (POST .../sorties/{id}/start and /stop), not the generic events route --
  // those routes already exist specifically for this (start was built for
  // "the first call radio check-in" against a WMIRS-planned sortie, per its
  // own docstring) and correctly link Sortie.engine_start_event_id/
  // engine_stop_event_id, same as stop already did for Stop Sortie.
  engine_start: { label: "Engine Start", kind: "sortie-start", fields: ["destination_area"] },
  wheels_up: { label: "Wheels Up", kind: "event", eventType: "takeoff", fields: [] },
  in_grid: { label: "In-Grid", kind: "waypoint", fields: ["location", "altitude", "fuel"] },
  out_grid: { label: "Out-Grid", kind: "waypoint", fields: ["location", "altitude", "fuel"] },
  find: { label: "Find", kind: "waypoint", fields: ["location"] },
  pirep: { label: "PIREP", kind: "waypoint", fields: ["location", "altitude"] },
  ops_check: { label: "Ops Check", kind: "waypoint", fields: ["location", "altitude", "fuel"] },
  rtb: { label: "RTB", kind: "waypoint", fields: ["location", "fuel", "ete"] },
  wheels_down: { label: "Wheels Down", kind: "event", eventType: "landing", fields: [] },
  engine_stop: { label: "Engine Stop", kind: "sortie-stop", fields: [] },
};
let currentSortieTail = null;
let currentCommsType = null; // which quick-select button is open, if any

// Comms/Status tabs (added 2026-10-26) -- per Gerry: a comms operator wants
// the quick-select menu, but AOBD/Ops/IC want a status view (time aloft,
// position, fuel/time of last call, etc) reachable the *same* way (click the
// aircraft). Both tabs are always one click away for anyone -- only the
// DEFAULT tab is hinted by the user's own mission role (see
// activeMissionRole()), never hard-gated, since that role is free text today.
let currentSortieTab = "comms";

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

// ── Manual 24hr-clock time entry, with a "Now" fill button (REQUIREMENTS.md 3.3,
// added 2026-09-13) ── Per Gerry: a plain "NOW" button alone isn't enough for
// engine start/stop -- pilots often don't call it in until several minutes
// afterward (sometimes not until airborne, clear of the field), so the *actual*
// reported time has to be enterable directly, in 24hr clock, not just "whatever
// time it is when comms gets around to logging it." Kept as a free-typed HHMM
// text field rather than a native <input type="time"> -- a native time picker's
// spinner/AM-PM rendering is locale-dependent (can't guarantee 24hr display
// across browsers) and slower to type into quickly during a live radio call than
// just typing 4 digits. Today's date (in the selected display timezone) is
// assumed -- logging a time from a prior day isn't supported yet.
function currentHHMM(tz) {
  const s = utcToZonedInputValue(new Date(), tz); // "YYYY-MM-DDTHH:MM:SS"
  return s.slice(11, 13) + s.slice(14, 16);
}

// Returns a UTC Date, or null if the field is empty/unparseable (caller should
// treat null as "use server-now", matching every event-creation endpoint's
// default when a time is omitted).
function hhmmInputToUtc(hhmmText, tz) {
  const digits = (hhmmText || "").replace(/[^0-9]/g, "");
  if (digits.length !== 4) return null;
  const hh = digits.slice(0, 2);
  const mm = digits.slice(2, 4);
  const todayLocalDate = utcToZonedInputValue(new Date(), tz).slice(0, 10);
  return zonedInputValueToUtc(`${todayLocalDate}T${hh}:${mm}:00`, tz);
}

// A time-entry field + "Now" button, as an HTML fragment -- used identically for
// engine start, engine stop, and every waypoint type. `idPrefix` keeps element
// ids unique when more than one appears in the panel at once.
function timeEntryFieldHtml(idPrefix, label) {
  return `
    <div class="sortie-field"><label>${label}</label>
      <input type="text" id="${idPrefix}-time" placeholder="HHMM (24hr)" style="flex: 1;" />
      <button type="button" class="now-btn" id="${idPrefix}-now-btn" style="flex: 0 0 44px;">Now</button>
    </div>`;
}

function wireTimeEntryField(idPrefix) {
  document.getElementById(`${idPrefix}-now-btn`).addEventListener("click", () => {
    document.getElementById(`${idPrefix}-time`).value = currentHHMM(displayTimezone);
  });
}

// null (field left blank -- becomes "use server-now") or an ISO UTC string ready
// to send as e.g. engine_start_utc/time_utc.
function readTimeEntryField(idPrefix) {
  const utcDate = hhmmInputToUtc(document.getElementById(`${idPrefix}-time`).value, displayTimezone);
  return utcDate ? utcDate.toISOString() : null;
}

async function openSortiePanel(tailNumber) {
  currentSortieTail = tailNumber;
  const panel = document.getElementById("sortie-panel");
  const pos = latestByTail.get(tailNumber);
  document.getElementById("sortie-title").textContent = pos?.callsign ?? tailNumber;
  document.getElementById("sortie-subtitle").textContent = tailNumber;
  panel.hidden = false;

  // Track selection doesn't carry over between aircraft -- switching tails
  // while the panel's open always starts from "off" rather than silently
  // showing the previous tail's trail under the new one's title.
  document.getElementById("track-toggle").checked = false;
  setTrackFeature(EMPTY_TRACK);

  // The comms quick-select menu itself is built inside renderInProgressSortie
  // (it needs the sortie's mission #/sortie # to prepopulate the form header),
  // not here -- reset which type was open so switching aircraft doesn't leave a
  // stale type's form showing under the new tail.
  currentCommsType = null;
  // Default tab hinted by the user's own role on the active mission -- loose,
  // case-insensitive substring match since role is free text; "status" is the
  // fallback for anyone else (AOBD/Ops/IC, or simply no role set).
  const role = (activeMissionRole() || "").toLowerCase();
  currentSortieTab = role.includes("comm") ? "comms" : "status";

  await refreshSortieEvents(tailNumber);
  await refreshSortieInfo(tailNumber);
}

// ── Sortie tracking (REQUIREMENTS.md 3.3, added 2026-09-13) ─────────────────
// "Mission # ... can be a global variable for a given session" (Gerry) -- a
// frontend-only convenience default that pre-fills new-sortie forms, carried
// across aircraft within the same browser session. Each sortie still stores its
// own mission_number server-side, so this is just a typing shortcut, not the
// record of truth.
const MISSION_NUMBER_KEY = "avtrack_session_mission_number";
const WAYPOINT_TYPE_LABELS = {
  in_grid: "In-Grid",
  out_grid: "Out-Grid",
  ops_check: "Ops Check",
  find: "Find",
  pirep: "PIREP",
  rtb: "RTB",
};

// Mission number format -- advisory only, added 2026-09-14 per Gerry: fiscal year
// (2 digits) - mission type (related to but not confirmed identical to the A/B/C
// CAP Standard 72-2 Mission Symbol set -- Gerry: "I'll have to find" the exact
// list) - a "quasi-serial" assigned when WMIRS approves the mission, i.e. not
// necessarily a fixed-width sequential number. Neither CAPR 70-1 nor CAPR 60-3(I)
// ("CAP Emergency Services Training and Operational Missions" -- the actual
// regulation for this subject; CAPR 60-3 itself is a same-numbered but unrelated
// Cadet Programs reg, confirmed by reading both directly) spells out this format
// as a written rule -- it appears to be a WMIRS system convention, not a
// regulatory requirement, so this deliberately WARNS on a mismatch rather than
// blocking: a real WMIRS-assigned mission number using a designator character
// nobody's seen yet must never be rejected by AvTrack.
const MISSION_NUMBER_PATTERN = /^\d{2}-[A-Za-z0-9]-\d{3,5}$/;
const MISSION_NUMBER_HINT_TEXT = "Doesn't look like YY-X-NNNN (fiscal year - mission type - sequence) -- saved as entered either way.";

function wireMissionNumberHint(input, insertAfter = input) {
  const hint = document.createElement("div");
  hint.className = "mission-number-hint";
  hint.textContent = MISSION_NUMBER_HINT_TEXT;
  hint.hidden = true;
  insertAfter.insertAdjacentElement("afterend", hint);
  const update = () => {
    const value = input.value.trim();
    hint.hidden = value === "" || MISSION_NUMBER_PATTERN.test(value);
  };
  input.addEventListener("input", update);
  update();
}

// Standalone session-level Mission # (REQUIREMENTS.md 3.3, added 2026-09-13) -- per
// Gerry: "can I start the comms session and enter the mission number somehow or
// does that have to be per-sortie?" This is the same localStorage key the Start
// Sortie form already reads/writes, just exposed somewhere settable *before*
// opening any specific aircraft's panel (previously the only way to set it was to
// open an aircraft's panel and type it into that aircraft's Start Sortie form).
const sessionMissionInput = document.getElementById("session-mission-number");
sessionMissionInput.value = localStorage.getItem(MISSION_NUMBER_KEY) ?? "";
sessionMissionInput.addEventListener("input", () => {
  localStorage.setItem(MISSION_NUMBER_KEY, sessionMissionInput.value.trim());
});
wireMissionNumberHint(sessionMissionInput);

async function refreshSortieInfo(tailNumber) {
  const container = document.getElementById("sortie-info");
  container.innerHTML = "<em>Loading…</em>";
  try {
    const res = await fetch(withMissionParam(`/api/aircraft/${tailNumber}/sorties/current`));
    const sortie = await res.json(); // null if no sortie in progress
    if (sortie) {
      renderInProgressSortie(tailNumber, sortie);
    } else {
      renderStartSortieForm(tailNumber);
    }
    renderRecentSorties(tailNumber); // independent of in-progress/not -- always the last few completed ones
  } catch (err) {
    container.innerHTML = "<em>Failed to load sortie info.</em>";
    console.error(err);
  }
}

function renderStartSortieForm(tailNumber) {
  const container = document.getElementById("sortie-info");
  const savedMissionNumber = activeMissionNumber() ?? localStorage.getItem(MISSION_NUMBER_KEY) ?? "";
  // Roster-upload default (optional pilot/instructor column), not a per-session
  // memory like mission number -- each tail can have its own default PIC.
  const defaultPic = defaultPicByTail.get(tailNumber) ?? "";
  container.innerHTML = `
    <h3>Start Sortie</h3>
    <div class="sortie-field"><label>Sortie #</label><input type="number" id="new-sortie-number" /></div>
    <div class="sortie-field"><label>Mission #</label><input type="text" id="new-mission-number" placeholder="YY-X-NNNN" value="${savedMissionNumber}" /></div>
    <div class="sortie-field"><label>PIC</label><input type="text" id="new-pic-name" value="${defaultPic}" /></div>
    <div class="sortie-field"><label>PIC CAP ID</label><input type="text" id="new-pic-cap-id" placeholder="optional" /></div>
    <div class="sortie-field"><label>Assigned Area</label><input type="text" id="new-assigned-area" placeholder="Grid 12 / Route A" /></div>
    <div class="sortie-field"><label>ETE (hrs)</label><input type="number" step="0.1" min="0" id="new-ete-hours" placeholder="e.g. 3.2" /></div>
    ${timeEntryFieldHtml("new-engine-start", "Eng Start")}
    <button class="sortie-action-btn" id="start-sortie-btn">Start Sortie</button>
  `;
  wireTimeEntryField("new-engine-start");
  const newMissionNumberInput = document.getElementById("new-mission-number");
  wireMissionNumberHint(newMissionNumberInput, newMissionNumberInput.closest(".sortie-field"));
  document.getElementById("start-sortie-btn").addEventListener("click", async () => {
    const sortieNumber = document.getElementById("new-sortie-number").value;
    const missionNumber = document.getElementById("new-mission-number").value.trim();
    const picName = document.getElementById("new-pic-name").value.trim();
    const picCapId = document.getElementById("new-pic-cap-id").value.trim();
    const assignedArea = document.getElementById("new-assigned-area").value.trim();
    const eteHours = document.getElementById("new-ete-hours").value;
    if (missionNumber) localStorage.setItem(MISSION_NUMBER_KEY, missionNumber);
    const res = await fetch(withMissionParam(`/api/aircraft/${tailNumber}/sorties`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sortie_number: sortieNumber ? parseInt(sortieNumber, 10) : null,
        mission_number: missionNumber || null,
        pic_name: picName || null,
        pic_cap_id: picCapId || null,
        assigned_area: assignedArea || null,
        ete_hours: eteHours ? parseFloat(eteHours) : null,
        engine_start_utc: readTimeEntryField("new-engine-start"),
        logged_by: loggedByName(),
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`Could not start sortie: ${err.detail ?? res.statusText}`);
      return;
    }
    await refreshSortieInfo(tailNumber);
    await refreshSortieEvents(tailNumber);
  });
}

// Past (already-stopped) sorties -- REQUIREMENTS.md 3.3, added 2026-09-14 per
// Gerry: "if I end the sortie there's no way to edit it?" Once engine_stop_event_id
// is set, GET .../sorties/current returns null and the panel falls back to the
// Start Sortie form above with no way to see or fix a completed sortie's metadata,
// even though PATCH /api/sorties/{id} already supported it server-side -- nothing in
// the frontend ever called it. Gerry: "I'm editing virtually every sortie in this
// current training evolution," so this isn't a rare case, it's the normal workflow.
async function renderRecentSorties(tailNumber) {
  const container = document.getElementById("recent-sorties");
  if (!container) return;
  container.innerHTML = "<em>Loading…</em>";
  try {
    const res = await fetch(withMissionParam(`/api/aircraft/${tailNumber}/sorties`));
    const sorties = (await res.json()).filter((s) => s.engine_stop_utc); // in-progress one (if any) is shown above instead
    if (sorties.length === 0) {
      container.innerHTML = "<em>No completed sorties yet.</em>";
      return;
    }
    container.innerHTML = "";
    for (const sortie of sorties.slice(0, 5)) {
      const row = document.createElement("div");
      row.className = "recent-sortie-row";
      row.innerHTML = `
        <div class="sortie-summary-row">
          <span>Sortie #${sortie.sortie_number ?? "—"} · ${sortie.mission_number ?? "no mission #"}</span>
          <button type="button" class="edit-sortie-btn">Edit</button>
        </div>
        <div class="sortie-summary-row"><span>PIC: ${sortie.pic_name ?? "—"}</span></div>
        <div class="sortie-summary-row">
          <span>Takeoff: ${sortie.takeoff_utc ? fmtInTz(sortie.takeoff_utc) : "—"}</span>
          <span>Landing: ${sortie.landing_utc ? fmtInTz(sortie.landing_utc) : "—"}</span>
        </div>
        <div class="sortie-summary-row">
          <span>Eng Start: ${sortie.engine_start_utc ? fmtInTz(sortie.engine_start_utc) : "—"}</span>
          <span>Eng Stop: ${sortie.engine_stop_utc ? fmtInTz(sortie.engine_stop_utc) : "—"}</span>
        </div>
        <div class="recent-sortie-edit" hidden>
          <div class="sortie-field"><label>Sortie #</label><input type="number" class="edit-sortie-number" value="${sortie.sortie_number ?? ""}" /></div>
          <div class="sortie-field"><label>Mission #</label><input type="text" class="edit-mission-number" value="${sortie.mission_number ?? ""}" /></div>
          <div class="sortie-field"><label>PIC</label><input type="text" class="edit-pic-name" value="${sortie.pic_name ?? ""}" /></div>
          <button type="button" class="sortie-action-btn save-sortie-btn">Save</button>
        </div>
      `;
      const editPanel = row.querySelector(".recent-sortie-edit");
      const editMissionNumberInput = row.querySelector(".edit-mission-number");
      wireMissionNumberHint(editMissionNumberInput, editMissionNumberInput.closest(".sortie-field"));
      row.querySelector(".edit-sortie-btn").addEventListener("click", () => {
        editPanel.hidden = !editPanel.hidden;
      });
      row.querySelector(".save-sortie-btn").addEventListener("click", async () => {
        const sortieNumber = row.querySelector(".edit-sortie-number").value;
        const missionNumber = row.querySelector(".edit-mission-number").value.trim();
        const picName = row.querySelector(".edit-pic-name").value.trim();
        await fetch(`/api/sorties/${sortie.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            sortie_number: sortieNumber ? parseInt(sortieNumber, 10) : null,
            mission_number: missionNumber || null,
            pic_name: picName || null,
          }),
        });
        renderRecentSorties(tailNumber);
      });
      container.appendChild(row);
    }
  } catch (err) {
    container.innerHTML = "<em>Failed to load recent sorties.</em>";
    console.error(err);
  }
}

function renderInProgressSortie(tailNumber, sortie) {
  const container = document.getElementById("sortie-info");
  // Generalized 2026-10-26 (was ops_check-only) -- altitude/fuel/ete/comments
  // now populate for any comms quick-select type that asked for them (see
  // COMMS_TYPES), not just Ops Check.
  const waypointRows = sortie.waypoints
    .map((wp) => {
      const parts = [];
      if (wp.altitude_ft != null) parts.push(`${wp.altitude_ft}ft`);
      if (wp.fuel_remaining_hours != null || wp.fuel_remaining_minutes != null) {
        parts.push(`fuel ${wp.fuel_remaining_hours ?? 0}h${wp.fuel_remaining_minutes ?? 0}m`);
      }
      if (wp.ete_hours != null || wp.ete_minutes != null) {
        parts.push(`ETE ${wp.ete_hours ?? 0}h${wp.ete_minutes ?? 0}m`);
      }
      if (wp.comments) parts.push(wp.comments);
      if (wp.pirep_report) parts.push(wp.pirep_report);
      const extra = parts.length ? ` · ${parts.join(" · ")}` : "";
      return `
        <div class="waypoint-row">
          <span class="wp-type">${WAYPOINT_TYPE_LABELS[wp.waypoint_type] ?? wp.waypoint_type}</span>
          <span class="wp-meta">${fmtInTz(wp.time_utc)} · ${wp.raw_input}${extra}</span>
        </div>`;
    })
    .join("");

  container.innerHTML = `
    <h3>Sortie In Progress</h3>
    <div class="sortie-summary-row"><span>Sortie #${sortie.sortie_number ?? "—"}</span><span>Eng Start: ${sortie.engine_start_utc ? fmtInTz(sortie.engine_start_utc) : "—"}</span></div>
    <div class="sortie-summary-row"><span>PIC: ${sortie.pic_name ?? "—"}</span><span>Mission: ${sortie.mission_number ?? "—"}</span></div>
    <div class="sortie-summary-row"><span>Area: ${sortie.assigned_area ?? "—"}</span><span>ETA: ${sortie.eta_utc ? fmtInTz(sortie.eta_utc) : "—"}</span></div>
    <div class="sortie-summary-row"><span>Takeoff: ${sortie.takeoff_utc ? fmtInTz(sortie.takeoff_utc) : "—"}</span><span>Landing: ${sortie.landing_utc ? fmtInTz(sortie.landing_utc) : "—"}</span></div>
    ${timeEntryFieldHtml("stop-engine", "Eng Stop")}
    <button class="sortie-action-btn stop" id="stop-sortie-btn">Stop Sortie</button>

    <div class="sortie-divider"></div>
    <div class="panel-mode-toggle" id="sortie-tab-toggle">
      <button class="panel-mode-btn" id="sortie-tab-comms-btn">Comms</button>
      <button class="panel-mode-btn" id="sortie-tab-status-btn">Status</button>
    </div>

    <div id="comms-tab-content">
      <h3>Log Radio Call</h3>
      <div class="log-buttons" id="comms-type-buttons"></div>
      <div id="comms-form"></div>

      <div class="sortie-divider"></div>
      <div id="waypoint-list">${waypointRows || '<em style="color:#9aa4ad; font-size:11px;">No waypoints logged yet.</em>'}</div>
    </div>
    <div id="status-tab-content"></div>
  `;
  wireTimeEntryField("stop-engine");

  document.getElementById("stop-sortie-btn").addEventListener("click", async () => {
    const res = await fetch(`/api/sorties/${sortie.id}/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ engine_stop_utc: readTimeEntryField("stop-engine"), logged_by: loggedByName() }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`Could not stop sortie: ${err.detail ?? res.statusText}`);
      return;
    }
    await refreshSortieInfo(tailNumber);
    await refreshSortieEvents(tailNumber);
  });

  document.getElementById("sortie-tab-comms-btn").addEventListener("click", () => {
    currentSortieTab = "comms";
    renderSortieTab(tailNumber, sortie);
  });
  document.getElementById("sortie-tab-status-btn").addEventListener("click", () => {
    currentSortieTab = "status";
    renderSortieTab(tailNumber, sortie);
  });

  const commsButtons = document.getElementById("comms-type-buttons");
  commsButtons.innerHTML = "";
  for (const [key, cfg] of Object.entries(COMMS_TYPES)) {
    const btn = document.createElement("button");
    btn.textContent = cfg.label;
    btn.dataset.type = key;
    btn.addEventListener("click", () => {
      currentCommsType = key;
      renderCommsForm(tailNumber, sortie);
    });
    commsButtons.appendChild(btn);
  }
  renderCommsForm(tailNumber, sortie);
  renderSortieTab(tailNumber, sortie);
}

// Switches between the Comms quick-select menu and the Status view -- both
// always reachable, see currentSortieTab's own comment for why neither is
// hard-gated by role.
function renderSortieTab(tailNumber, sortie) {
  document.getElementById("sortie-tab-comms-btn").classList.toggle("active", currentSortieTab === "comms");
  document.getElementById("sortie-tab-status-btn").classList.toggle("active", currentSortieTab === "status");
  document.getElementById("comms-tab-content").hidden = currentSortieTab !== "comms";
  document.getElementById("status-tab-content").hidden = currentSortieTab !== "status";
  if (currentSortieTab === "status") renderStatusTab(tailNumber, sortie);
}

// A starting point per Gerry ("I'll think of other things"): time aloft,
// position in decimal degrees, distance/bearing from the Bullseye, fuel
// remaining + time of last call, and current altitude -- everything computed
// client-side from data already loaded for this panel, no new endpoint.
function renderStatusTab(tailNumber, sortie) {
  const container = document.getElementById("status-tab-content");
  const pos = latestByTail.get(tailNumber);

  let timeAloft = "Not airborne";
  if (sortie.takeoff_utc) {
    const ms = Date.now() - new Date(sortie.takeoff_utc).getTime();
    const totalMin = Math.max(0, Math.round(ms / 60000));
    timeAloft = `${Math.floor(totalMin / 60)}h ${totalMin % 60}m`;
  }

  let positionLine = "No live position";
  let bullseyeLine = "Bullseye not set for this mission";
  if (pos) {
    positionLine = `${pos.latitude.toFixed(4)}, ${pos.longitude.toFixed(4)}`;
    if (bullseyeCenter) {
      const nm = haversineNm(bullseyeCenter.lat, bullseyeCenter.lon, pos.latitude, pos.longitude);
      const brg = initialBearingDeg(bullseyeCenter.lat, bullseyeCenter.lon, pos.latitude, pos.longitude);
      bullseyeLine = `${nm.toFixed(1)} nm, ${Math.round(brg)}° from Bullseye`;
    }
  }

  // Most recent waypoint that actually carries a fuel reading -- not
  // necessarily the most recent waypoint overall (e.g. a Find with no fuel
  // field shouldn't shadow an earlier Ops Check's fuel reading).
  const fuelWaypoints = sortie.waypoints.filter((wp) => wp.fuel_remaining_hours != null || wp.fuel_remaining_minutes != null);
  const lastFuel = fuelWaypoints.length ? fuelWaypoints[fuelWaypoints.length - 1] : null;
  const fuelLine = lastFuel
    ? `${lastFuel.fuel_remaining_hours ?? 0}h ${lastFuel.fuel_remaining_minutes ?? 0}m, at ${fmtInTz(lastFuel.time_utc)}`
    : "No fuel report logged yet";

  const lastCallLine = sortie.waypoints.length ? fmtInTz(sortie.waypoints[sortie.waypoints.length - 1].time_utc) : "No calls logged yet";
  const altitudeLine = pos?.altitude_ft != null ? `${Math.round(pos.altitude_ft)} ft` : "—";

  container.innerHTML = `
    <div class="sortie-field"><label>Time aloft</label><span>${timeAloft}</span></div>
    <div class="sortie-field"><label>Position</label><span>${positionLine}</span></div>
    <div class="sortie-field"><label>Bullseye</label><span>${bullseyeLine}</span></div>
    <div class="sortie-field"><label>Altitude</label><span>${altitudeLine}</span></div>
    <div class="sortie-field"><label>Fuel rem.</label><span>${fuelLine}</span></div>
    <div class="sortie-field"><label>Last call</label><span>${lastCallLine}</span></div>
  `;
}

// One shared, type-driven prepopulated form for the comms quick-select menu
// (added 2026-10-26, replacing the old flat "Add Waypoint" form -- see
// COMMS_TYPES above for the per-type field list and why Engine Start/Stop
// aren't here). Prepopulates with Mission #/Callsign/Sortie #/current time per
// Gerry's exact spec; only the fields that type actually needs beyond that are
// shown, not every field for every type (directly answering the WMIRS
// comms-log anti-pattern memory).
function renderCommsForm(tailNumber, sortie) {
  const container = document.getElementById("comms-form");
  document.querySelectorAll("#comms-type-buttons button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.type === currentCommsType);
  });
  if (!currentCommsType) {
    container.innerHTML = "";
    return;
  }
  const cfg = COMMS_TYPES[currentCommsType];
  const pos = latestByTail.get(tailNumber);
  const callsign = pos?.callsign ?? callsignByTail.get(tailNumber) ?? tailNumber;

  const fieldsHtml = [
    cfg.fields.includes("location")
      ? `
        <div class="sortie-field"><label>Location</label>
          <select id="wp-method">
            <option value="latlon">Lat/Lon</option>
            <option value="mgrs">MGRS</option>
            <option value="named_point">Named point</option>
          </select>
        </div>
        <div id="wp-location-inputs"></div>`
      : "",
    cfg.fields.includes("altitude")
      ? `<div class="sortie-field"><label>Altitude</label><input type="number" id="wp-altitude" /></div>`
      : "",
    cfg.fields.includes("fuel")
      ? `<div class="sortie-field"><label>Fuel rem.</label>
           <input type="number" id="wp-fuel-h" min="0" placeholder="hh" style="flex: 0 0 55px;" /> :
           <input type="number" id="wp-fuel-m" min="0" max="59" placeholder="mm" style="flex: 0 0 55px;" />
         </div>`
      : "",
    cfg.fields.includes("ete")
      ? `<div class="sortie-field"><label>ETE</label>
           <input type="number" id="wp-ete-h" min="0" placeholder="hh" style="flex: 0 0 55px;" /> :
           <input type="number" id="wp-ete-m" min="0" max="59" placeholder="mm" style="flex: 0 0 55px;" />
         </div>`
      : "",
    // Engine Start-specific: "destination or search area" (Gerry's exact
    // wording) -- writes to Sortie.assigned_area via PATCH, not a new column;
    // prefilled with whatever's already set (e.g. from a WMIRS import or the
    // Start Sortie form) so re-logging Engine Start doesn't blank it out.
    cfg.fields.includes("destination_area")
      ? `<div class="sortie-field"><label>Dest./Area</label><input type="text" id="wp-destination-area" value="${sortie.assigned_area ?? ""}" /></div>`
      : "",
  ].join("");

  container.innerHTML = `
    <div class="comms-form-header">
      <div class="sortie-summary-row"><span>Mission ${sortie.mission_number ?? "—"}</span><span>${callsign}</span></div>
      <div class="sortie-summary-row"><span>Sortie #${sortie.sortie_number ?? "—"}</span><span>${cfg.label}</span></div>
    </div>
    ${timeEntryFieldHtml("wp", "Time")}
    ${fieldsHtml}
    <textarea id="wp-comments" rows="2" placeholder="${currentCommsType === "pirep" ? "PIREP report" : "Message / notes"}"></textarea>
    ${currentCommsType === "pirep" ? '<div id="pirep-counter" class="pirep-counter">0 / 500</div>' : ""}
    <button class="sortie-action-btn" id="comms-submit-btn">Log ${cfg.label}</button>
  `;
  wireTimeEntryField("wp");
  if (cfg.fields.includes("location")) {
    const methodSelect = document.getElementById("wp-method");
    methodSelect.addEventListener("change", () => updateWaypointLocationInputs(methodSelect.value));
    updateWaypointLocationInputs(methodSelect.value);
  }
  // PIREP: no hard maxlength -- truncating silently mid-sentence is worse than
  // letting the caller see they're over and summarize themselves. The counter
  // just prompts; submitCommsEntry below is what actually stops an over-length
  // submit, with a message explaining why, not a bare backend rejection.
  if (currentCommsType === "pirep") {
    const pirepCounter = document.getElementById("pirep-counter");
    const pirepTextarea = document.getElementById("wp-comments");
    const updatePirepCounter = () => {
      const len = pirepTextarea.value.length;
      pirepCounter.textContent = len > 500 ? `${len} / 500 -- too long, please summarize` : `${len} / 500`;
      pirepCounter.classList.toggle("over-limit", len > 500);
    };
    pirepTextarea.addEventListener("input", updatePirepCounter);
  }
  document.getElementById("comms-submit-btn").addEventListener("click", () => submitCommsEntry(tailNumber, sortie, currentCommsType));
}

function updateWaypointLocationInputs(method) {
  const container = document.getElementById("wp-location-inputs");
  if (method === "mgrs") {
    container.innerHTML = `<div class="sortie-field"><label>MGRS</label><input type="text" id="wp-raw-input" placeholder="16SFJ8362771646" /></div>`;
  } else if (method === "named_point") {
    container.innerHTML = `
      <div class="sortie-field"><label>Place name</label><input type="text" id="wp-raw-input" placeholder="over Smithville" /></div>
      <div class="sortie-field"><label>Lat, Lon</label>
        <input type="number" step="any" id="wp-lat" placeholder="lat" style="flex: 1;" />
        <input type="number" step="any" id="wp-lon" placeholder="lon" style="flex: 1;" />
      </div>`;
  } else {
    container.innerHTML = `
      <div class="sortie-field"><label>Lat, Lon</label>
        <input type="number" step="any" id="wp-lat" placeholder="lat" style="flex: 1;" />
        <input type="number" step="any" id="wp-lon" placeholder="lon" style="flex: 1;" />
      </div>`;
  }
}

// Replaces the old flat addWaypoint() (added 2026-10-26) -- routes through
// either POST /api/aircraft/{tail}/events (kind: "event") or
// POST /api/sorties/{id}/waypoints (kind: "waypoint") depending on the
// quick-select type clicked, per COMMS_TYPES. Deliberately does NOT reset
// currentCommsType on success -- re-renders the same type's form, cleared,
// ready for another same-type entry (e.g. logging several In-Grid calls in a
// row), matching how this panel already resets its own fields on every
// refreshSortieInfo() re-render.
async function submitCommsEntry(tailNumber, sortie, typeKey) {
  const cfg = COMMS_TYPES[typeKey];
  const timeUtc = readTimeEntryField("wp");
  const commentsEl = document.getElementById("wp-comments");
  const messageText = commentsEl ? commentsEl.value.trim() || null : null;
  // PIREP's free text is a separate, DB-bounded column (SortieWaypoint.pirep_report,
  // VARCHAR(500), added 2026-10-26 per Gerry) -- not the shared, unbounded
  // `comments` every other type uses. Same textarea, different destination field.
  const comments = typeKey === "pirep" ? null : messageText;
  const pirepReport = typeKey === "pirep" ? messageText : null;

  // Prompt rather than reject: the DB column is a real VARCHAR(500) constraint,
  // but the caller should hear "please summarize" from us, not a generic 422
  // after they've already hit Submit. Per Gerry 2026-10-26.
  if (typeKey === "pirep" && pirepReport && pirepReport.length > 500) {
    alert(`This PIREP is ${pirepReport.length} characters -- the max is 500. Please summarize/condense it, then log it.`);
    return;
  }

  if (cfg.kind === "sortie-start" || cfg.kind === "sortie-stop") {
    if (cfg.fields.includes("destination_area")) {
      const destinationArea = document.getElementById("wp-destination-area").value.trim();
      if (destinationArea) {
        const patchRes = await fetch(`/api/sorties/${sortie.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ assigned_area: destinationArea }),
        });
        if (!patchRes.ok) {
          const err = await patchRes.json().catch(() => ({}));
          alert(`Could not update destination/area: ${err.detail ?? patchRes.statusText}`);
          return;
        }
      }
    }
    const path = cfg.kind === "sortie-start" ? "start" : "stop";
    const bodyKey = cfg.kind === "sortie-start" ? "engine_start_utc" : "engine_stop_utc";
    const res = await fetch(`/api/sorties/${sortie.id}/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [bodyKey]: timeUtc, logged_by: loggedByName() }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`Could not log ${cfg.label}: ${err.detail ?? res.statusText}`);
      return;
    }
    await refreshSortieInfo(tailNumber);
    await refreshSortieEvents(tailNumber);
    return;
  }

  if (cfg.kind === "event") {
    await logEvent(tailNumber, cfg.eventType, timeUtc, comments); // already refreshes sortie info/events itself
    return;
  }

  // kind === "waypoint" -- every current waypoint-kind COMMS_TYPES entry
  // includes "location" (see the config above), so this is always populated.
  const locationMethod = document.getElementById("wp-method").value;
  const lat = document.getElementById("wp-lat")?.value;
  const lon = document.getElementById("wp-lon")?.value;
  // MGRS's own text field IS the raw_input; lat/lon methods use the coordinates
  // themselves as raw_input (accountability -- preserves exactly what was entered).
  const rawInputField = document.getElementById("wp-raw-input");
  const rawInput = rawInputField ? rawInputField.value.trim() : `${lat},${lon}`;
  if (!rawInput || (locationMethod !== "mgrs" && (!lat || !lon))) {
    alert("Location is required.");
    return;
  }

  const body = {
    waypoint_type: typeKey,
    location_method: locationMethod,
    raw_input: rawInput,
    latitude: lat ? parseFloat(lat) : null,
    longitude: lon ? parseFloat(lon) : null,
    time_utc: timeUtc,
    logged_by: loggedByName(),
    comments,
    pirep_report: pirepReport,
  };
  if (cfg.fields.includes("altitude")) {
    const altitude = document.getElementById("wp-altitude").value;
    body.altitude_ft = altitude ? parseFloat(altitude) : null;
  }
  if (cfg.fields.includes("fuel")) {
    const fuelH = document.getElementById("wp-fuel-h").value;
    const fuelM = document.getElementById("wp-fuel-m").value;
    body.fuel_remaining_hours = fuelH ? parseInt(fuelH, 10) : null;
    body.fuel_remaining_minutes = fuelM ? parseInt(fuelM, 10) : null;
  }
  if (cfg.fields.includes("ete")) {
    const eteH = document.getElementById("wp-ete-h").value;
    const eteM = document.getElementById("wp-ete-m").value;
    body.ete_hours = eteH ? parseInt(eteH, 10) : null;
    body.ete_minutes = eteM ? parseInt(eteM, 10) : null;
  }

  const res = await fetch(`/api/sorties/${sortie.id}/waypoints`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(`Could not log ${cfg.label}: ${err.detail ?? res.statusText}`);
    return;
  }
  await refreshSortieInfo(tailNumber);
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
        ${ev.comments ? `<div class="wp-meta">${ev.comments}</div>` : ""}
        <div class="edit-controls">
          <input type="datetime-local" step="1" />
          <button>Correct time (${displayTimezone})</button>
        </div>`;
      const input = row.querySelector("input");
      input.value = utcToZonedInputValue(new Date(ev.effective_time_utc), displayTimezone);
      row.querySelector("button").addEventListener("click", async () => {
        const newUtc = zonedInputValueToUtc(input.value, displayTimezone).toISOString();
        const res = await fetch(`/api/events/${ev.id}/edits`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ field_name: "event_time_utc", new_value: newUtc, edited_by: loggedByName() }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          alert(`Could not correct time: ${err.detail ?? res.statusText}`);
          return;
        }
        refreshSortieEvents(tailNumber);
      });
      container.appendChild(row);
    }
  } catch (err) {
    container.innerHTML = "<em>Failed to load events.</em>";
    console.error(err);
  }
}

async function logEvent(tailNumber, eventType, eventTimeUtc = null, comments = null) {
  const res = await fetch(`/api/aircraft/${tailNumber}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      event_type: eventType,
      event_time_utc: eventTimeUtc, // null = server-now, same as before this field existed
      logged_by: loggedByName(),
      comments,
    }),
  });
  // Found live 2026-10-26 (adding require_time_logging_permission, app/auth.py):
  // this never checked the response at all, so a now-possible 403 (wrong role)
  // would fail completely silently -- the button would appear to do nothing.
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(`Could not log event: ${err.detail ?? res.statusText}`);
    return;
  }
  refreshSortieEvents(tailNumber);
  // takeoff/landing now show in the "Sortie In Progress" summary (REQUIREMENTS.md
  // 3.3, 2026-09-14) -- refresh that too, not just the raw event log below it, or
  // logging a takeoff/landing silently doesn't update its own summary row until the
  // panel is closed and reopened (found live while verifying this feature).
  refreshSortieInfo(tailNumber);
}

document.getElementById("sortie-close").addEventListener("click", () => {
  document.getElementById("sortie-panel").hidden = true;
  currentSortieTail = null;
  document.getElementById("track-toggle").checked = false;
  setTrackFeature(EMPTY_TRACK);
});

// ── Aircraft track/trail (REQUIREMENTS.md 3.1, added 2026-10-24) -- selectable
// per aircraft via the sortie panel's "Show track" checkbox (per Gerry: "The
// track needs to be selectable by aircraft, with an 8 hour window" -- not
// always-on for every airborne tail, which would clutter the map). The 8-hour
// window itself is a flat server-side cutoff (app/api/sorties.py's
// aircraft_track), not "since takeoff" -- there's no formal airborne-state
// transition tracked server-side to anchor that to instead.
const EMPTY_TRACK = { type: "FeatureCollection", features: [] };

function setTrackFeature(feature) {
  if (!mapStyleReady) {
    map.once("load", () => setTrackFeature(feature));
    return;
  }
  const source = map.getSource("aircraft-track");
  if (source) {
    source.setData(feature);
  } else {
    map.addSource("aircraft-track", { type: "geojson", data: feature });
    map.addLayer({
      id: "aircraft-track-line",
      type: "line",
      source: "aircraft-track",
      // Changed AGAIN 2026-10-25: the burnt-orange (#c2410c) that replaced the
      // original light gold was itself too close to USGS Topo's own highway
      // color -- same problem, different shade of the same red/orange family
      // every basemap here already uses for roads. Magenta has no road,
      // water, or vegetation meaning on any of topo/streets/imagery/hybrid, so
      // it can't blend into any of them the way anything in the red-orange-
      // yellow range keeps doing.
      paint: { "line-color": "#d6007f", "line-width": 2.5 },
    });
  }
}

async function refreshAircraftTrack(tailNumber) {
  try {
    const res = await fetch(withMissionParam(`/api/aircraft/${tailNumber}/track`));
    const points = await res.json();
    if (!Array.isArray(points) || points.length < 2) {
      setTrackFeature(EMPTY_TRACK);
      return;
    }
    setTrackFeature({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: { type: "LineString", coordinates: points.map((p) => [p.longitude, p.latitude]) },
          properties: {},
        },
      ],
    });
  } catch {
    setTrackFeature(EMPTY_TRACK);
  }
}

document.getElementById("track-toggle").addEventListener("change", (e) => {
  if (e.target.checked && currentSortieTail) {
    refreshAircraftTrack(currentSortieTail);
  } else {
    setTrackFeature(EMPTY_TRACK);
  }
});

// ── Air Ops Status Board (REQUIREMENTS.md 3.3, added 2026-10-01) -- a live,
// multi-aircraft digitization of the AOBD's wall board, grounded in the CAWG AOBD
// course: Sortie #/Tail/AC Type/Assigned Area/ATD/ETA/ATA/Last Ops Normal, with
// overdue flagging. Polling refresh (not a new WS channel) since this data doesn't
// change at ADS-B frequency -- matches the project's existing precedent for
// lower-frequency data (e.g. airfields bbox refetch).
const OPS_BOARD_REFRESH_MS = 20000;
let opsBoardInterval = null;

const OPS_BOARD_STATE_LABEL = {
  airborne: "AIRBORNE",
  ground: "ON GROUND",
  "not-reporting": "NOT REPORTING",
  complete: "COMPLETE",
};

async function refreshOpsBoard() {
  try {
    const [boardRes, discoveredRes] = await Promise.all([
      fetch(withMissionParam("/api/ops-board?hours=24")),
      fetch(withMissionParam("/api/discovered")),
    ]);
    const { rows, generated_at } = await boardRes.json();
    renderDiscoveredSection(discoveredRes.ok ? await discoveredRes.json() : []);
    renderOpsBoardTable(rows);
    document.getElementById("ops-board-updated").textContent = `Updated ${fmtInTz(generated_at)}`;
  } catch (err) {
    document.getElementById("ops-board-table").innerHTML = "<em>Failed to load ops board.</em>";
    console.error(err);
  }
}

// Unregistered CAP/PARD aircraft detected in the AO (REQUIREMENTS.md 3.1, added
// 2026-10-14) -- folded into the Ops Board as its own top section per Gerry's
// preference, rather than a separate panel/toast, reusing the board's existing
// poll cadence and the `.missing-flag` visual language instead of inventing new
// styling.
function renderDiscoveredSection(rows) {
  const el = document.getElementById("discovered-section");
  if (!rows.length) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = `
    <div class="discovered-alert">
      <strong class="missing-flag">${rows.length} unregistered CAP/PARD aircraft${aoIsSet ? " in AO" : " (no AO set -- showing all)"}</strong>
      ${rows
        .map((r) => {
          const wingBadge = r.wing_hint
            ? ` <span class="wing-hint" title="Display hint only, from callsign pattern -- not authoritative">(${r.wing_hint})</span>`
            : "";
          return `
        <div class="discovered-row">
          <span>${r.source_label} ${r.callsign} / ${r.tail_number}${wingBadge}</span>
          <button class="discovered-approve" data-id="${r.id}">Approve</button>
          <button class="discovered-dismiss" data-id="${r.id}">Dismiss</button>
        </div>`;
        })
        .join("")}
    </div>`;
  // approveDiscovered/dismissDiscovered already refresh the Ops Board themselves
  // when it's open (see their definitions above) -- this section only renders
  // while it is, so no extra refresh call is needed here.
  el.querySelectorAll(".discovered-approve").forEach((btn) => btn.addEventListener("click", () => approveDiscovered(btn.dataset.id)));
  el.querySelectorAll(".discovered-dismiss").forEach((btn) => btn.addEventListener("click", () => dismissDiscovered(btn.dataset.id)));
}

// Two independent alert conditions (REQUIREMENTS.md 3.3, added 2026-10-14), most
// severe first: `missing` (CAPR 70-10's compound overdue+no-contact trigger,
// flashing red) outranks `no_contact_warning` (soft, orange halo, independent of
// ETA) outranks plain `overdue` (still shown as data, no longer itself an alarm --
// per Gerry, "that's a different issue") outranks the ordinary state ranking.
// Shared by the Ops Board table and the right-column Mission Status view (added
// 2026-10-26) so the two views always agree on triage order.
const OPS_BOARD_STATE_RANK = { airborne: 0, ground: 1, "not-reporting": 2, complete: 3 };
function sortOpsBoardRows(rows) {
  return [...rows].sort((a, b) => {
    if (a.missing !== b.missing) return a.missing ? -1 : 1;
    if (a.no_contact_warning !== b.no_contact_warning) return a.no_contact_warning ? -1 : 1;
    if (a.overdue !== b.overdue) return a.overdue ? -1 : 1;
    if (a.overdue && b.overdue) return b.overdue_minutes - a.overdue_minutes;
    return OPS_BOARD_STATE_RANK[a.state] - OPS_BOARD_STATE_RANK[b.state];
  });
}

function renderOpsBoardTable(rows) {
  const container = document.getElementById("ops-board-table");
  if (rows.length === 0) {
    container.innerHTML = "<em>No sorties in the last 24 hours.</em>";
    return;
  }
  const sorted = sortOpsBoardRows(rows);

  const headers = [
    "Sortie #",
    "Aircraft",
    "Type",
    "Mission #",
    "PIC",
    "Assigned Area",
    "ATD",
    "ETA",
    "ATA",
    "Last Ops Normal",
    "Status",
  ];
  const headerHtml = headers.map((h) => `<th>${h}</th>`).join("");
  const bodyHtml = sorted
    .map((r) => {
      const overdueFlag = r.overdue ? `<span class="overdue-flag">OVERDUE +${r.overdue_minutes}m</span>` : "";
      const alertFlag = r.missing
        ? `<span class="missing-flag">MISSING — NOTIFY NOC</span>`
        : r.no_contact_warning
          ? `<span class="no-contact-flag">NO CONTACT ${r.last_contact_minutes}m</span>`
          : "";
      // Row-level class drives the visual (flashing red halo / slow orange halo) --
      // CSS in index.html. `missing` and `no-contact-warning` are mutually
      // exclusive by construction (missing requires no_contact_warning's condition
      // to already be true at a longer duration), but keep both checks explicit
      // rather than relying on that invariant holding forever.
      const alertClass = r.missing ? "missing" : r.no_contact_warning ? "no-contact-warning" : "";
      return `
        <tr class="${alertClass}" data-tail="${r.tail_number}">
          <td>${r.sortie_number ?? "—"}</td>
          <td>${r.callsign ?? r.tail_number}</td>
          <td>${r.aircraft_type ?? "—"}</td>
          <td>${r.mission_number ?? "—"}</td>
          <td>${r.pic_name ?? "—"}</td>
          <td>${r.assigned_area ?? "—"}</td>
          <td>${r.atd_utc ? fmtInTz(r.atd_utc) : "—"}</td>
          <td>${r.eta_utc ? fmtInTz(r.eta_utc) : "—"} ${overdueFlag}</td>
          <td>${r.ata_utc ? fmtInTz(r.ata_utc) : "—"}</td>
          <td>${r.last_ops_normal_utc ? fmtInTz(r.last_ops_normal_utc) : "—"} ${alertFlag}</td>
          <td><span class="status-badge status-${r.state}">${OPS_BOARD_STATE_LABEL[r.state]}</span></td>
        </tr>`;
    })
    .join("");

  container.innerHTML = `<table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`;
  // Row click opens the existing per-aircraft sortie panel -- the board is a
  // glance/triage view, the sortie panel is still where an AOBD actually logs or
  // corrects something, not duplicated here.
  container.querySelectorAll("tbody tr").forEach((row) => {
    row.addEventListener("click", () => openSortiePanel(row.dataset.tail));
  });
}

document.getElementById("ops-board-toggle").addEventListener("click", () => {
  document.getElementById("ops-board-panel").hidden = false;
  refreshOpsBoard();
  if (!opsBoardInterval) opsBoardInterval = setInterval(refreshOpsBoard, OPS_BOARD_REFRESH_MS);
});
document.getElementById("ops-board-close").addEventListener("click", () => {
  document.getElementById("ops-board-panel").hidden = true;
  clearInterval(opsBoardInterval);
  opsBoardInterval = null;
});

// ── Right-column Aircraft Aloft / Mission Status toggle (added 2026-10-26).
// Mission Status reuses the Ops Board's own data (GET /api/ops-board) -- per
// Gerry's request for the same operational picture without having to open the
// bottom overlay -- rendered as narrow stacked cards instead of a wide table,
// since this column is 190px vs. the Ops Board's full-width overlay. Its sort
// is its own (sortByCapCallsign below), not the Ops Board table's severity-
// first sortOpsBoardRows -- per Gerry, Aircraft Aloft doesn't need sorting at
// all, but Mission Status should be ordered by CAP callsign so COMMS can find
// an aircraft fast when it calls in (rather than COMMS already having
// selected it to make the call).
const PANEL_MODE_STORAGE_KEY = "avtrack_panel_mode";
let panelStatusInterval = null;

// A plain string sort of "CAP535" vs "CAP1012" puts 1012 first ("1" < "5"
// lexicographically) -- wrong for a list meant to be scanned in callsign
// order. Zero-pad the numeric suffix to a fixed width before comparing (same
// trick as sorting zero-padded version strings) so 3-digit callsigns compare
// correctly against 4-digit ones; the displayed callsign itself is untouched.
const CALLSIGN_SORT_RE = /^(CAP|PARD)\s?(\d+)$/i;
function _callsignSortKey(callsign) {
  const m = callsign ? CALLSIGN_SORT_RE.exec(callsign.trim()) : null;
  if (!m) return `~${callsign ?? ""}`; // non-CAP/PARD-shaped callsigns/tails sort after all of them
  return `${m[1].toUpperCase()}${m[2].padStart(4, "0")}`;
}
function sortByCapCallsign(rows) {
  return [...rows].sort((a, b) =>
    _callsignSortKey(a.callsign ?? a.tail_number).localeCompare(_callsignSortKey(b.callsign ?? b.tail_number))
  );
}

function renderMissionStatusPanel(rows) {
  const container = document.getElementById("mission-status-list");
  if (rows.length === 0) {
    container.innerHTML = "<em>No sorties in the last 24 hours.</em>";
    return;
  }
  const sorted = sortByCapCallsign(rows);
  container.innerHTML = sorted
    .map((r) => {
      const alertFlag = r.missing
        ? `<span class="alert">MISSING</span>`
        : r.no_contact_warning
          ? `<span class="alert">NO CONTACT ${r.last_contact_minutes}m</span>`
          : r.overdue
            ? `<span class="alert">OVERDUE +${r.overdue_minutes}m</span>`
            : "";
      return `
        <div class="mission-status-row" data-tail="${r.tail_number}">
          <div class="top-line">
            <span>${r.callsign ?? r.tail_number}${r.sortie_number != null ? ` · #${r.sortie_number}` : ""}</span>
            <span class="status-badge status-${r.state}">${OPS_BOARD_STATE_LABEL[r.state]}</span>
          </div>
          <div class="meta">${r.assigned_area ?? "—"} · ETA ${r.eta_utc ? fmtInTz(r.eta_utc) : "—"}</div>
          <div class="meta">Last Ops Normal ${r.last_ops_normal_utc ? fmtInTz(r.last_ops_normal_utc) : "—"} ${alertFlag}</div>
        </div>`;
    })
    .join("");
  container.querySelectorAll(".mission-status-row").forEach((row) => {
    row.addEventListener("click", () => openSortiePanel(row.dataset.tail));
  });
}

async function refreshMissionStatusPanel() {
  try {
    const res = await fetch(withMissionParam("/api/ops-board?hours=24"));
    const { rows } = await res.json();
    renderMissionStatusPanel(rows);
  } catch (err) {
    document.getElementById("mission-status-list").innerHTML = "<em>Failed to load mission status.</em>";
    console.error(err);
  }
}

function setPanelMode(mode) {
  const aloftBtn = document.getElementById("panel-mode-aloft-btn");
  const statusBtn = document.getElementById("panel-mode-status-btn");
  const aloftList = document.getElementById("aircraft-list");
  const statusList = document.getElementById("mission-status-list");
  aloftBtn.classList.toggle("active", mode === "aloft");
  statusBtn.classList.toggle("active", mode === "status");
  aloftList.hidden = mode !== "aloft";
  statusList.hidden = mode !== "status";

  clearInterval(panelStatusInterval);
  panelStatusInterval = null;
  if (mode === "status") {
    refreshMissionStatusPanel();
    panelStatusInterval = setInterval(refreshMissionStatusPanel, OPS_BOARD_REFRESH_MS);
  }
  try {
    localStorage.setItem(PANEL_MODE_STORAGE_KEY, mode);
  } catch {
    // ignore -- private browsing / storage disabled; mode just won't persist across reloads
  }
}

document.getElementById("panel-mode-aloft-btn").addEventListener("click", () => setPanelMode("aloft"));
document.getElementById("panel-mode-status-btn").addEventListener("click", () => setPanelMode("status"));

let initialPanelMode = "aloft";
try {
  initialPanelMode = localStorage.getItem(PANEL_MODE_STORAGE_KEY) === "status" ? "status" : "aloft";
} catch {
  // ignore -- default to "aloft"
}
setPanelMode(initialPanelMode);

// MGRS precision-box outline (REQUIREMENTS.md 3.2, added 2026-09-13) -- when a
// locate query resolves to a box (truncated-precision MGRS, coarser than ~1m --
// see app/api/locate.py's _precision_box docstring for why a bare grid square
// isn't really "a point"), draw the actual box instead of silently centering on
// one arbitrary corner. bounds is [[lat,lon], SW, SE, NE, NW] as returned by the
// backend -- each corner already independently converted, not a naive rectangle.
function setMgrsBox(bounds) {
  if (!mapStyleReady) {
    map.once("load", () => setMgrsBox(bounds));
    return;
  }
  const feature = {
    type: "Feature",
    geometry: { type: "Polygon", coordinates: [[...bounds, bounds[0]].map(([lat, lon]) => [lon, lat])] },
  };
  const source = map.getSource("mgrs-box");
  if (source) {
    source.setData(feature);
  } else {
    map.addSource("mgrs-box", { type: "geojson", data: feature });
    map.addLayer({
      id: "mgrs-box-fill",
      type: "fill",
      source: "mgrs-box",
      paint: { "fill-color": "#d9534f", "fill-opacity": 0.08 },
    });
    map.addLayer({
      id: "mgrs-box-line",
      type: "line",
      source: "mgrs-box",
      paint: { "line-color": "#d9534f", "line-width": 2, "line-dasharray": [2, 1] },
    });
  }
}

function clearMgrsBox() {
  if (map.getSource("mgrs-box")) {
    map.getSource("mgrs-box").setData({ type: "FeatureCollection", features: [] });
  }
}

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
  const { latitude, longitude, bounds } = await res.json();
  const radiusNm = parseFloat(document.getElementById("locate-radius").value) || 0;
  if (radiusNm > 0) {
    bullseyeCenter = { lat: latitude, lon: longitude };
    setOpsArea([longitude, latitude], radiusNm);
    // Persist to the backend (REQUIREMENTS.md 3.1, added 2026-10-14) so the
    // AO-scoped discovery poller can geofence against it -- additive, doesn't
    // change the draw-only behavior above.
    fetch(withMissionParam("/api/discovered/ao"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ center_latitude: latitude, center_longitude: longitude, radius_nm: radiusNm }),
    })
      .then((r) => {
        if (r.ok) aoIsSet = true;
      })
      .catch((err) => console.error("Failed to persist Area of Operations:", err));
  }
  if (bounds) {
    setMgrsBox(bounds);
    const lons = bounds.map((c) => c[1]);
    const lats = bounds.map((c) => c[0]);
    map.fitBounds(
      [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ],
      { padding: 60 }
    );
    return;
  }
  clearMgrsBox();
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

// ── Rapid callsign/tail lookup (REQUIREMENTS.md 3.3, added 2026-09-13) ──────
// Per Gerry: working comms, a radio call gives callsign first, and the aircraft
// may not have a live position yet (pilot often doesn't call in until minutes
// after engine start, sometimes not until airborne and clear of the field) --
// so this has to work by callsign/tail alone, not by clicking a map icon or
// sidebar row that requires a live position to exist first. openSortiePanel()
// already tolerates no live position (falls back to the tail number for
// display), so the only missing piece was resolving the typed ident to a tail.
async function jumpToAircraft(query) {
  query = query.trim();
  if (!query) return;
  const res = await fetch("/api/aircraft/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ idents: [query] }),
  });
  const matches = await res.json();
  if (matches.length === 0) {
    alert(`No known aircraft matches "${query}" (not tracked yet -- add it via the roster upload first).`);
    return;
  }
  openSortiePanel(matches[0].tail_number);
}
document.getElementById("jump-input").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter") return;
  await jumpToAircraft(e.target.value);
  e.target.value = "";
  e.target.blur(); // give focus back to the map/keyboard shortcuts rather than leaving it in the box
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
  const res = await fetch(withMissionParam("/api/gis/layers"));
  const layers = await res.json();
  gisLayers.clear();
  for (const layer of layers) gisLayers.set(layer.id, layer);
  renderGisLayerList();
  for (const layer of layers) {
    if (!map.getSource(`gis-${layer.id}`)) await addGisLayerToMap(layer.id);
  }
}

function showGisFeaturePopup(e) {
  new maplibregl.Popup()
    .setLngLat(e.lngLat)
    .setHTML(`<pre style="margin:0;font-size:11px;">${JSON.stringify(e.features[0].properties, null, 1)}</pre>`)
    .addTo(map);
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
  // Found live 2026-10-26: Point geometries (e.g. a KML with named reporting
  // points, not just search-area polygons/lines) were stored and counted
  // ("N features") but had no layer to actually draw them -- only fill/line
  // existed, so every uploaded point silently never appeared on the map.
  map.addLayer({
    id: `gis-${layerId}-point`,
    type: "circle",
    source: `gis-${layerId}`,
    filter: ["==", ["geometry-type"], "Point"],
    paint: {
      "circle-radius": 5,
      "circle-color": GIS_LAYER_COLOR,
      "circle-stroke-width": 1.5,
      "circle-stroke-color": "#1a1a1a",
    },
  });
  for (const suffix of ["-fill", "-line", "-point"]) {
    map.on("click", `gis-${layerId}${suffix}`, showGisFeaturePopup);
  }
}

function removeGisLayerFromMap(layerId) {
  for (const suffix of ["-fill", "-line", "-point"]) {
    if (map.getLayer(`gis-${layerId}${suffix}`)) map.removeLayer(`gis-${layerId}${suffix}`);
  }
  if (map.getSource(`gis-${layerId}`)) map.removeSource(`gis-${layerId}`);
}

function clearGisLayers() {
  // Mirrors clearKnownAircraft() -- switching missions must not leave a
  // previous mission's uploaded layers cluttering the map or the list (found
  // live 2026-10-26, same report as the mission-scoping fix above).
  for (const id of gisLayers.keys()) removeGisLayerFromMap(id);
  gisLayers.clear();
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
      await fetch(withMissionParam(`/api/gis/layers/${id}`), { method: "DELETE" });
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
  const res = await fetch(withMissionParam("/api/gis/layers"), { method: "POST", body: formData });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(`Upload failed: ${err.detail || res.statusText}`);
    return;
  }
  input.value = "";
  await refreshGisLayerList();
});

// ── Aviation Chart: NASR NavAids/Fixes, Class Airspace, Obstructions, and the
// zoom-resolving VFR Sectional crossover -- ported from CAP_WxCOP's EWMC/
// Flight Ops Impacts pages (enhanced_weather_map_complete.html /
// weather_impacts.html on r815) per Gerry 2026-11-23: "We need the same logic
// and mapping for AvTrack and Flight Impacts... take it to Zoom 12." AvTrack
// already has its own airfield layer (a different, CAP-specific dataset and
// tiering -- see app/api/airfields.py's own docstring) -- that's untouched;
// this only adds what AvTrack didn't have at all: NavAids, Fixes, Class
// Airspace, Obstructions, and the vector-to-raster crossover itself. Fetched
// cross-origin from the CAP_WxCOP host (same absolute-URL reasoning as
// BASEMAPS.sectional above -- a relative path would resolve against AvTrack's
// own origin and 404); CORS enabled server-side for these specific paths
// 2026-11-23 (see /etc/apache2/sites-enabled/cap_winds.conf and
// conf-available/mapcache.conf).
const CAP_WXCOP_ORIGIN = "https://wxcop.duckdns.org";
const NASR_NAVAIDS_API = `${CAP_WXCOP_ORIGIN}/CAP_WxCOP/api/nasr/navaids`;
const NASR_FIXES_API = `${CAP_WXCOP_ORIGIN}/CAP_WxCOP/api/nasr/fixes`;
const NASR_OBSTRUCTIONS_API = `${CAP_WXCOP_ORIGIN}/CAP_WxCOP/api/nasr/obstructions`;
const CLASS_AIRSPACE_URL = `${CAP_WXCOP_ORIGIN}/CAP_WxCOP/static/geo/class_airspace.geojson`;
const NASR_FIX_MIN_ZOOM = 10;
const OBSTRUCTIONS_MIN_ZOOM = 10;

const NAVAID_COLOR = {
  VOR: "#1565c0",
  "VOR/DME": "#1565c0",
  VORTAC: "#00838f",
  TACAN: "#00838f",
  NDB: "#8e24aa",
  "NDB/DME": "#8e24aa",
  "MARINE NDB": "#8e24aa",
  DME: "#555",
  VOT: "#555",
  "FAN MARKER": "#999",
};

function nasrBoundsParam() {
  const b = map.getBounds();
  return `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
}

// NavAid zoom tiering (per Gerry, 2026-11-23): "Prioritize HIGH VORTAC on 0-3
// and then start bringing in more, including the low altitude NAVAIDs at
// higher zoom levels" -- same density-pacing principle as the airfield tiers
// above (TIER_MIN_ZOOM), driven by FAA's own ALT_CODE field on nasr.navaids
// (backfilled 2026-11-23 from NAV_BASE.csv -- see
// [[project_nasr_schema_table_ownership]] for why that needed a one-time
// ALTER run as postgres). VH/H = high altitude, VL/L = low altitude, T =
// terminal; blank for the navaid types that don't carry one at all
// (NDB/NDB-DME/DME/VOT/FAN MARKER/MARINE NDB), which fall into the lowest,
// most-numerous tier alongside Terminal-class VOR/VORTAC/VOR-DME and TACAN
// (no altitude structure of its own, but a primary aid -- grouped with the
// Terminal tier rather than the bottom one). Applied the same way the
// airfield dot layer is: precomputed per-feature, gated via setFilter on
// every bbox refresh rather than re-fetching per tier (NavAids is a single
// ~1.5k-row nationwide table -- cheap enough to fetch once and filter
// client-side, unlike the ~22k-row airfields table airfieldsRequestId/
// max_tier was built to avoid over-fetching).
function navaidMinZoom(navType, altCode) {
  const alt = (altCode || "").toUpperCase();
  const isVorFamily = navType === "VORTAC" || navType === "VOR/DME" || navType === "VOR";
  if (navType === "VORTAC" && (alt === "H" || alt === "VH")) return 0;
  if (isVorFamily && (alt === "H" || alt === "VH")) return 3;
  if (isVorFamily && (alt === "L" || alt === "VL")) return 6;
  if ((isVorFamily && alt === "T") || navType === "TACAN") return 8;
  return NASR_FIX_MIN_ZOOM; // NDB/NDB-DME/DME/VOT/FAN MARKER/Marine NDB -- full detail only
}

// ── NavAids + Fixes ──────────────────────────────────────────────────────────
let navaidsFixesRequestId = 0;

async function refreshNavaidsFixes() {
  if (!document.getElementById("navaids-toggle").checked) return;
  const requestId = ++navaidsFixesRequestId;
  const bp = nasrBoundsParam();
  const zoom = map.getZoom();
  const statusEl = document.getElementById("navaids-status");

  try {
    const navRes = await fetch(`${NASR_NAVAIDS_API}?bounds=${bp}`);
    const navData = await navRes.json();
    if (requestId !== navaidsFixesRequestId) return;
    const navGeojson = {
      type: "FeatureCollection",
      features: (navData.navaids || []).map((n) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [n.lon, n.lat] },
        properties: {
          color: NAVAID_COLOR[n.nav_type] || "#555",
          label: `${n.nav_id} — ${n.nav_type}${n.name ? " (" + n.name + ")" : ""}${n.freq ? " " + n.freq : ""}`,
          minZoom: navaidMinZoom(n.nav_type, n.alt_code),
        },
      })),
    };
    const navSource = map.getSource("nasr-navaids");
    if (navSource) {
      navSource.setData(navGeojson);
    } else {
      map.addSource("nasr-navaids", { type: "geojson", data: navGeojson });
      map.addLayer({
        id: "nasr-navaids-layer",
        type: "circle",
        source: "nasr-navaids",
        paint: {
          "circle-radius": 6,
          "circle-color": ["get", "color"],
          "circle-opacity": 0.25,
          "circle-stroke-color": ["get", "color"],
          "circle-stroke-width": 2,
        },
      });
      map.on("click", "nasr-navaids-layer", (e) => {
        new maplibregl.Popup().setLngLat(e.lngLat).setHTML(e.features[0].properties.label).addTo(map);
      });
    }
    map.setFilter("nasr-navaids-layer", ["<=", ["get", "minZoom"], zoom]);

    let fixCount = 0;
    if (zoom >= NASR_FIX_MIN_ZOOM) {
      const fixRes = await fetch(`${NASR_FIXES_API}?bounds=${bp}`);
      const fixData = await fixRes.json();
      if (requestId !== navaidsFixesRequestId) return;
      fixCount = fixData.count || 0;
      const fixGeojson = {
        type: "FeatureCollection",
        features: (fixData.fixes || []).map((fx) => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [fx.lon, fx.lat] },
          properties: { label: fx.fix_id + (fx.use_code ? ` (${fx.use_code.trim()})` : "") },
        })),
      };
      const fixSource = map.getSource("nasr-fixes");
      if (fixSource) {
        fixSource.setData(fixGeojson);
      } else {
        map.addSource("nasr-fixes", { type: "geojson", data: fixGeojson });
        map.addLayer({
          id: "nasr-fixes-layer",
          type: "circle",
          source: "nasr-fixes",
          paint: {
            "circle-radius": 3,
            "circle-color": "#aaa",
            "circle-opacity": 0.6,
            "circle-stroke-color": "#777",
            "circle-stroke-width": 1,
          },
        });
        map.on("click", "nasr-fixes-layer", (e) => {
          new maplibregl.Popup().setLngLat(e.lngLat).setHTML(e.features[0].properties.label).addTo(map);
        });
      }
    } else {
      const fixSource = map.getSource("nasr-fixes");
      if (fixSource) fixSource.setData({ type: "FeatureCollection", features: [] });
    }

    if (statusEl) {
      const shownNavaids = navGeojson.features.filter((f) => f.properties.minZoom <= zoom).length;
      const navaidsLabel =
        shownNavaids < navGeojson.features.length ? `${shownNavaids}/${navGeojson.features.length} navaids` : `${shownNavaids} navaids`;
      statusEl.textContent = navaidsLabel + (zoom >= NASR_FIX_MIN_ZOOM ? ` · ${fixCount} fixes` : " · zoom in for fixes");
    }
  } catch (err) {
    console.error("Failed to load NavAids/Fixes", err);
  }
}

function clearNavaidsFixes() {
  for (const id of ["nasr-navaids-layer", "nasr-fixes-layer"]) if (map.getLayer(id)) map.removeLayer(id);
  for (const id of ["nasr-navaids", "nasr-fixes"]) if (map.getSource(id)) map.removeSource(id);
  const statusEl = document.getElementById("navaids-status");
  if (statusEl) statusEl.textContent = "";
}

let navaidsFixesFetchTimer = null;
function scheduleNavaidsFixesRefresh() {
  if (!document.getElementById("navaids-toggle").checked) return;
  clearTimeout(navaidsFixesFetchTimer);
  navaidsFixesFetchTimer = setTimeout(refreshNavaidsFixes, 400);
}

document.getElementById("navaids-toggle").addEventListener("change", (e) => {
  if (e.target.checked) refreshNavaidsFixes();
  else clearNavaidsFixes();
});

// ── Obstructions (FAA Digital Obstacle File) ────────────────────────────────
// Triangle glyphs, matching EWMC/Flight Ops Impacts per Gerry's "triangle or
// diamond" request there (2026-11-22) -- registered once as two addImage()
// bitmaps (lit/unlit) rather than per-feature HTML markers, same convention
// as registerAirfieldTierBadges() above, since this dataset is dense enough
// (656k nationwide, ~200k at the >=200ft AGL floor the API applies by
// default) that per-feature DOM markers would be far more expensive than one
// shared symbol-layer image.
function makeObstructionTriangleImage(color) {
  const w = 20,
    h = 22;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.beginPath();
  ctx.moveTo(w / 2, 0);
  ctx.lineTo(w, h);
  ctx.lineTo(0, h);
  ctx.closePath();
  ctx.globalAlpha = 0.85;
  ctx.fillStyle = color;
  ctx.fill();
  return ctx.getImageData(0, 0, w, h);
}

function registerObstructionIcons() {
  if (!map.hasImage("obstruction-lit")) map.addImage("obstruction-lit", makeObstructionTriangleImage("#ff3030"));
  if (!map.hasImage("obstruction-unlit")) map.addImage("obstruction-unlit", makeObstructionTriangleImage("#a00000"));
}

let obstructionsRequestId = 0;

async function refreshObstructions() {
  if (!document.getElementById("obstructions-toggle").checked) return;
  const statusEl = document.getElementById("obstructions-status");
  const zoom = map.getZoom();
  if (zoom < OBSTRUCTIONS_MIN_ZOOM) {
    const source = map.getSource("nasr-obstructions");
    if (source) source.setData({ type: "FeatureCollection", features: [] });
    if (statusEl) statusEl.textContent = "zoom in to load obstructions...";
    return;
  }
  const requestId = ++obstructionsRequestId;
  const bp = nasrBoundsParam();
  try {
    const res = await fetch(`${NASR_OBSTRUCTIONS_API}?bounds=${bp}`);
    const data = await res.json();
    if (requestId !== obstructionsRequestId) return;
    const geojson = {
      type: "FeatureCollection",
      features: (data.obstructions || []).map((o) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [o.lon, o.lat] },
        properties: {
          lit: !!(o.lighting && o.lighting !== "N"),
          agl_ft: o.agl_ft || 0,
          label: `${o.obstacle_type || "Obstruction"}${o.agl_ft ? " — " + o.agl_ft + "ft AGL" : ""}${o.amsl_ft ? " (" + o.amsl_ft + "ft MSL)" : ""}`,
        },
      })),
    };
    const source = map.getSource("nasr-obstructions");
    if (source) {
      source.setData(geojson);
    } else {
      map.addSource("nasr-obstructions", { type: "geojson", data: geojson });
      map.addLayer({
        id: "nasr-obstructions-layer",
        type: "symbol",
        source: "nasr-obstructions",
        layout: {
          "icon-image": ["case", ["get", "lit"], "obstruction-lit", "obstruction-unlit"],
          "icon-size": ["interpolate", ["linear"], ["get", "agl_ft"], 0, 0.4, 1200, 1.0],
          "icon-allow-overlap": true,
        },
      });
      map.on("click", "nasr-obstructions-layer", (e) => {
        new maplibregl.Popup().setLngLat(e.lngLat).setHTML(e.features[0].properties.label).addTo(map);
      });
    }
    if (statusEl) statusEl.textContent = `${data.count || 0} obstructions ≥200ft AGL`;
  } catch (err) {
    console.error("Failed to load Obstructions", err);
  }
}

function clearObstructions() {
  if (map.getLayer("nasr-obstructions-layer")) map.removeLayer("nasr-obstructions-layer");
  if (map.getSource("nasr-obstructions")) map.removeSource("nasr-obstructions");
  const statusEl = document.getElementById("obstructions-status");
  if (statusEl) statusEl.textContent = "";
}

let obstructionsFetchTimer = null;
function scheduleObstructionsRefresh() {
  if (!document.getElementById("obstructions-toggle").checked) return;
  clearTimeout(obstructionsFetchTimer);
  obstructionsFetchTimer = setTimeout(refreshObstructions, 400);
}

document.getElementById("obstructions-toggle").addEventListener("change", (e) => {
  if (e.target.checked) refreshObstructions();
  else clearObstructions();
});

// ── Class Airspace (B/C/D controlled, E surface) ────────────────────────────
// Static GeoJSON, same file EWMC/Flight Ops Impacts serve (already simplified
// server-side from Esri's raw 542MB export down to 28.5MB -- see
// scripts/update_airspace_geo.py on r815), loaded once and cached in-memory
// like AvTrack's own loadSua()/loadMtr() pattern (if those exist elsewhere in
// this file) rather than refetched on every toggle.
let classAirspaceData = null;
let classAirspaceLoading = false;

function loadClassAirspace(callback) {
  if (classAirspaceData) {
    callback(classAirspaceData);
    return;
  }
  if (classAirspaceLoading) {
    setTimeout(() => loadClassAirspace(callback), 500);
    return;
  }
  classAirspaceLoading = true;
  const statusEl = document.getElementById("airspace-class-status");
  if (statusEl) statusEl.textContent = "Loading…";
  fetch(CLASS_AIRSPACE_URL)
    .then((r) => {
      if (!r.ok) throw new Error("Class Airspace fetch failed: " + r.status);
      return r.json();
    })
    .then((data) => {
      classAirspaceData = data;
      classAirspaceLoading = false;
      if (statusEl) statusEl.textContent = "";
      callback(data);
    })
    .catch((err) => {
      classAirspaceLoading = false;
      if (statusEl) statusEl.textContent = "unavailable";
      console.error("Class Airspace load error:", err);
    });
}

// B/C/D get one shared fill layer (fill-opacity IS a valid data/property
// expression) but three separate line layers, not one -- found live
// 2026-11-23: MapLibre rejects line-dasharray as a data expression ("data
// expressions not supported"), so a single per-class `match` on CLASS can't
// drive the dash pattern; each class's dasharray has to be a plain literal on
// its own layer instead. Also split the fill (originally one combined
// "class-bcd-fill") the same way, since each class now needs its own native
// `minzoom` -- MapLibre's `minzoom` is a per-layer property, not something a
// filter/paint expression can vary by CLASS within one layer.
//
// Zoom floors (per Gerry, 2026-11-23): "Class B airspace at CONUS-level zoom,
// then, progressively, C and D" -- same density-pacing logic as the airfield
// tiers and NavAids above, applied to controlled airspace. Class B (the ~37
// major-hub rings) is sparse enough to show at the map's zoom-3.2 startup
// view; C and D step in a few zoom levels apart so each step reveals
// something new rather than dumping all ~700 D rings in at once. Class E
// (surface extensions at *any* towered field, by far the most numerous) gets
// the latest floor, matching NavAids'/Fixes' "full detail" zoom.
const CLASS_BCD_MIN_ZOOM = { B: 0, C: 4, D: 6 };
const CLASS_E_MIN_ZOOM = 8;
const CLASS_BCD_FILL_IDS = ["class-b-fill", "class-c-fill", "class-d-fill"];
const CLASS_BCD_LINE_IDS = ["class-b-line", "class-c-line", "class-d-line"];
const CLASS_ALL_LAYER_IDS = [...CLASS_BCD_FILL_IDS, ...CLASS_BCD_LINE_IDS, "class-e-line"];

let classAirspaceLayersReady = false;
let classAirspaceLayersBuilding = false;

function classAirspacePopupHtml(p) {
  const cls = (p.CLASS || "").toUpperCase();
  const name = p.NAME || p.IDENT || "";
  const floor = p.LOWER_CODE === "SFC" ? "SFC" : p.LOWER_VAL != null ? `${p.LOWER_VAL} ${p.LOWER_UOM || "ft"}` : "?";
  const ceiling = p.UPPER_VAL != null ? `${p.UPPER_VAL} ${p.UPPER_UOM || "ft"}` : "?";
  const hours = p.WKHR_CODE === "H24" ? "Continuous" : p.WKHR_RMK || "See chart/NOTAMs";
  return `<div style="font-size:13px;min-width:180px;"><strong>${name}</strong><br><span style="color:#555;">Class ${cls}</span><br>Alt: ${floor} – ${ceiling}<br><span style="font-size:11px;">⏱ ${hours}</span></div>`;
}

function ensureClassAirspaceLayers() {
  // Guards a real race (found live 2026-11-23): the class-bcd-toggle and
  // class-e-toggle "change" listeners below both call this, and
  // loadClassAirspace()'s fetch is async -- both calls passed the "source
  // doesn't exist yet" check before either one's callback had actually run,
  // so both tried to addSource the same id ("Source 'class-airspace' already
  // exists"). A synchronous "building" flag closes that window; the simple
  // `if (map.getSource(...)) return` guard alone isn't enough since the
  // source doesn't exist until the fetch resolves.
  if (classAirspaceLayersReady || classAirspaceLayersBuilding) return;
  classAirspaceLayersBuilding = true;
  loadClassAirspace((data) => {
    map.addSource("class-airspace", { type: "geojson", data });
    map.addLayer({
      id: "class-b-fill",
      type: "fill",
      source: "class-airspace",
      filter: ["==", ["get", "CLASS"], "B"],
      minzoom: CLASS_BCD_MIN_ZOOM.B,
      layout: { visibility: "none" },
      paint: { "fill-color": "#0046dc", "fill-opacity": 0.06 },
    });
    map.addLayer({
      id: "class-c-fill",
      type: "fill",
      source: "class-airspace",
      filter: ["==", ["get", "CLASS"], "C"],
      minzoom: CLASS_BCD_MIN_ZOOM.C,
      layout: { visibility: "none" },
      paint: { "fill-color": "#0046dc", "fill-opacity": 0.05 },
    });
    map.addLayer({
      id: "class-d-fill",
      type: "fill",
      source: "class-airspace",
      filter: ["==", ["get", "CLASS"], "D"],
      minzoom: CLASS_BCD_MIN_ZOOM.D,
      layout: { visibility: "none" },
      paint: { "fill-color": "#0046dc", "fill-opacity": 0.04 },
    });
    map.addLayer({
      id: "class-b-line",
      type: "line",
      source: "class-airspace",
      filter: ["==", ["get", "CLASS"], "B"],
      minzoom: CLASS_BCD_MIN_ZOOM.B,
      layout: { visibility: "none" },
      paint: { "line-color": "#0046dc", "line-width": 2, "line-opacity": 0.9 },
    });
    map.addLayer({
      id: "class-c-line",
      type: "line",
      source: "class-airspace",
      filter: ["==", ["get", "CLASS"], "C"],
      minzoom: CLASS_BCD_MIN_ZOOM.C,
      layout: { visibility: "none" },
      paint: { "line-color": "#0046dc", "line-width": 1.5, "line-dasharray": [8, 4], "line-opacity": 0.8 },
    });
    map.addLayer({
      id: "class-d-line",
      type: "line",
      source: "class-airspace",
      filter: ["==", ["get", "CLASS"], "D"],
      minzoom: CLASS_BCD_MIN_ZOOM.D,
      layout: { visibility: "none" },
      paint: { "line-color": "#0046dc", "line-width": 1, "line-dasharray": [4, 4], "line-opacity": 0.7 },
    });
    map.addLayer({
      id: "class-e-line",
      type: "line",
      source: "class-airspace",
      filter: ["==", ["get", "CLASS"], "E"],
      minzoom: CLASS_E_MIN_ZOOM,
      layout: { visibility: "none" },
      paint: { "line-color": "#787800", "line-width": 1, "line-dasharray": [1, 2], "line-opacity": 0.5 },
    });
    for (const id of CLASS_ALL_LAYER_IDS) {
      map.on("click", id, (e) => {
        new maplibregl.Popup().setLngLat(e.lngLat).setHTML(classAirspacePopupHtml(e.features[0].properties)).addTo(map);
      });
    }
    classAirspaceLayersReady = true;
    classAirspaceLayersBuilding = false;
    applyClassAirspaceVisibility();
  });
}

function applyClassAirspaceVisibility() {
  const bcdOn = document.getElementById("class-bcd-toggle").checked;
  const eOn = document.getElementById("class-e-toggle").checked;
  if (map.getLayer("class-b-fill")) {
    for (const id of [...CLASS_BCD_FILL_IDS, ...CLASS_BCD_LINE_IDS]) {
      map.setLayoutProperty(id, "visibility", bcdOn ? "visible" : "none");
    }
  }
  if (map.getLayer("class-e-line")) {
    map.setLayoutProperty("class-e-line", "visibility", eOn ? "visible" : "none");
  }
}

for (const id of ["class-bcd-toggle", "class-e-toggle"]) {
  document.getElementById(id).addEventListener("change", () => {
    ensureClassAirspaceLayers();
    applyClassAirspaceVisibility();
  });
}

// ── Aviation Chart crossover (vector master layer <-> real sectional raster) ─
// Per Gerry 2026-11-23, matching EWMC/Flight Ops Impacts exactly: zoom 12 is
// both where a real viewport-measured crossover felt right there AND where
// the self-hosted sectional raster's own maxNativeZoom tops out, so it only
// engages once it actually has more detail to offer than the vector layer.
const AVIATION_CROSSOVER_ZOOM = 12;
let aviationModeActive = false;

function setAviationOverlays(show) {
  // NavAids deliberately excluded (per Gerry, 2026-11-23): "NAVAIDS does not
  // need to be default checked 'On' on any of the maps" -- it was the single
  // biggest contributor to the full-CONUS density complaint (1513 unfiltered
  // markers), so unlike Class B/C/D/E and Obstructions, it now stays off
  // unless the user explicitly checks it, even in aviation mode.
  for (const id of ["class-bcd-toggle", "class-e-toggle", "obstructions-toggle"]) {
    const el = document.getElementById(id);
    if (el.checked !== show) {
      el.checked = show;
      el.dispatchEvent(new Event("change"));
    }
  }
}

function applyAviationLayer() {
  if (!aviationModeActive) return;
  const wantSectional = map.getZoom() >= AVIATION_CROSSOVER_ZOOM;
  const topoVisible = map.getLayoutProperty("basemap-topo-layer", "visibility") !== "none";
  if (wantSectional && topoVisible) {
    map.setLayoutProperty("basemap-topo-layer", "visibility", "none");
    map.setLayoutProperty("basemap-sectional-layer", "visibility", "visible");
    setAviationOverlays(false);
  } else if (!wantSectional && !topoVisible) {
    map.setLayoutProperty("basemap-sectional-layer", "visibility", "none");
    map.setLayoutProperty("basemap-topo-layer", "visibility", "visible");
    setAviationOverlays(true);
  }
}

// ── Mission roster upload (REQUIREMENTS.md 3.1, added 2026-09-13) ───────────
// Additive-only -- see app/api/aircraft.py upload_roster docstring. Distinct from
// the standing monthly wing-wide import (scripts/import_callsign_tails.py, a CLI
// script, not web-exposed).
document.getElementById("roster-upload-btn").addEventListener("click", async () => {
  const input = document.getElementById("roster-upload-input");
  const status = document.getElementById("roster-upload-status");
  const file = input.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  status.textContent = "Uploading…";
  const res = await fetch(withMissionParam("/api/aircraft/roster"), { method: "POST", body: formData });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    status.textContent = `Failed: ${err.detail || res.statusText}`;
    return;
  }
  const result = await res.json();
  input.value = "";
  status.textContent = `Added/updated ${result.added_or_updated}.`;
  if (result.unusual_suffix_tails.length > 0) {
    status.textContent += ` Note unusual suffix: ${result.unusual_suffix_tails.join(", ")}`;
  }
  await loadKnownAircraft(); // additive -- newly-uploaded tails stop showing as "unlisted"
});

// ── Comms log PDF report (REQUIREMENTS.md 3.3/3.6, added 2026-09-13) ────────
// Per Gerry: end-of-operating-period PDF of every logged entry, showing when it
// was actually *received* (not the reported/backdated event time), with several
// entries received together grouped under one timestamp, and any comments.
// datetime-local (not the rapid HHMM fields used elsewhere) since a period can
// span multiple days -- this is a deliberate occasional action, not rapid-fire
// radio logging, so a native date+time picker is the right tool here.
// Fixed to UTC (added 2026-10-15 per Gerry: "those should be in 24 hr clock, in
// UTC"), deliberately NOT `displayTimezone` -- the generated PDF itself is always
// Zulu (see reports.py), so letting these fields follow whatever convenience
// display zone the rest of the app happens to be set to would let what you type
// drift out of sync with what the official document actually prints. The
// underlying <input type="datetime-local"> value is already `hourCycle: "h23"`
// (24hr) via utcToZonedInputValue -- only the browser's own on-screen rendering
// of that native widget (locale-dependent, outside this app's control) could
// still show it differently.
function defaultReportEndValue() {
  return utcToZonedInputValue(new Date(), "UTC").slice(0, 16); // datetime-local wants no seconds
}
document.getElementById("report-end").value = defaultReportEndValue();

document.getElementById("generate-report-btn").addEventListener("click", () => {
  const startVal = document.getElementById("report-start").value;
  const endVal = document.getElementById("report-end").value || defaultReportEndValue();
  if (!startVal) {
    alert("Start time is required.");
    return;
  }
  const startUtc = zonedInputValueToUtc(`${startVal}:00`, "UTC").toISOString();
  const endUtc = zonedInputValueToUtc(`${endVal}:00`, "UTC").toISOString();
  window.open(`/api/reports/comms-log?start=${encodeURIComponent(startUtc)}&end=${encodeURIComponent(endUtc)}`, "_blank");
});

map.on("load", refreshAirfields);
map.on("moveend", scheduleAirfieldsRefresh); // covers both pan and zoom (zoom-only still fires moveend)
map.on("zoom", updateAirfieldZoomFilter); // instant dot+label tier feedback, ahead of the debounced bbox refetch
map.on("zoom", renderStationPlotMarkers); // instant barb-only/full-plot tier feedback, same reasoning; no-op unless that mode is active
map.on("load", refreshSua);
map.on("moveend", scheduleSuaRefresh);
map.on("load", refreshGisLayerList);
map.on("moveend", scheduleNavaidsFixesRefresh);
map.on("moveend", scheduleObstructionsRefresh);
map.on("zoomend", applyAviationLayer);

// ── Login gate + mission switcher (added 2026-10-16) ───────────────────────
// Everything tracking-related (loadKnownAircraft/loadPersistedOpsArea/
// refreshDiscoveredMarkers/the websocket) now waits for a successful login
// instead of firing unconditionally -- reference/GIS data above (airfields,
// SUA, uploaded layers) stays unauthenticated, matching the backend (only
// the identity/mission-scoped routes require a session).
function initTrackingData() {
  loadKnownAircraft().then(connectWebSocket);
  loadPersistedOpsArea();
  refreshDiscoveredMarkers();
}

function clearKnownAircraft() {
  // Additive-only Sets/Maps (same philosophy as roster upload) never shrink
  // on their own -- switching missions must not leave a previous mission's
  // aircraft looking "known" in this one.
  knownAircraft.clear();
  callsignByTail.clear();
  defaultPicByTail.clear();
  aircraftIdByTail.clear();
  wingHintByTail.clear();
}

function populateMissionSelect() {
  const select = document.getElementById("mission-select");
  const previousValue = currentMissionId || "";
  select.innerHTML = '<option value="">— none (legacy / unscoped) —</option>';
  for (const m of currentMissions) {
    const opt = document.createElement("option");
    opt.value = m.id;
    const label = m.mission_number ? `${m.name} (${m.mission_number})` : m.name;
    opt.textContent = m.closed_at ? `${label} [closed]` : label;
    select.appendChild(opt);
  }
  select.value = currentMissions.some((m) => m.id === previousValue) ? previousValue : "";
  if (select.value !== previousValue) {
    // The URL/persisted mission no longer exists/isn't ours anymore -- fall
    // back to unscoped rather than silently keeping a dead id around.
    setCurrentMissionId(null);
  }
  updateMissionMembersVisibility();
}

// Re-fetches currentMissions from /mine, honoring the "Show closed missions"
// checkbox (item 5, 2026-10-18 plan), then re-renders the select. Used after
// login and after any lifecycle action (leave/close/reopen/join) that could
// change which missions should be listed.
async function refreshMissionList() {
  const includeClosed = document.getElementById("mission-show-closed-toggle").checked;
  const res = await fetch(`/api/missions/mine${includeClosed ? "?include_closed=true" : ""}`);
  if (res.ok) currentMissions = await res.json();
  populateMissionSelect();
}

document.getElementById("mission-show-closed-toggle").addEventListener("change", refreshMissionList);

function updateMissionMembersVisibility() {
  const section = document.getElementById("mission-members-section");
  section.style.display = currentMissionId ? "block" : "none";
  // Added 2026-11-02 per Gerry: the Leave/Close/Reopen controls being gated
  // behind selecting a mission in the dropdown above (rather than, say,
  // visible as soon as a mission exists) wasn't "articulated... in a way I
  // understood" -- he closed a mission's worth of time not knowing this
  // existed. Hint only needed while nothing's selected; once the section
  // above is showing, the hint's job is done.
  document.getElementById("mission-select-hint").hidden = !!currentMissionId;
  if (!currentMissionId) return;
  const mission = currentMissions.find((m) => m.id === currentMissionId);
  const isClosed = !!mission?.closed_at;
  // Leave/Close only make sense on an open mission the viewer can act on;
  // a closed one shows a single Reopen button instead (item 5, 2026-10-18 plan).
  document.getElementById("mission-leave-btn").hidden = isClosed;
  document.getElementById("mission-close-btn").hidden = isClosed;
  document.getElementById("mission-reopen-btn").hidden = !isClosed;
  refreshMissionMembers();
}

async function refreshMissionMembers() {
  const list = document.getElementById("mission-members-list");
  try {
    const res = await fetch(`/api/missions/${currentMissionId}/members`);
    if (!res.ok) {
      list.textContent = "Could not load members.";
      return;
    }
    const members = await res.json();
    list.innerHTML = members.map((m) => `${m.display_name} (${m.cap_id})${m.role ? ` — ${m.role}` : ""}`).join("<br/>");
  } catch (err) {
    console.error("Failed to load mission members", err);
    list.textContent = "Could not load members.";
  }
}

async function switchToMission(missionId) {
  setCurrentMissionId(missionId || null);
  document.getElementById("mission-select").value = currentMissionId || "";
  clearKnownAircraft();
  clearGisLayers();
  updateMissionMembersVisibility();
  await loadKnownAircraft();
  await loadPersistedOpsArea();
  await refreshDiscoveredMarkers();
  await refreshGisLayerList();
  if (!document.getElementById("ops-board-panel").hidden) refreshOpsBoard();
}

document.getElementById("mission-select").addEventListener("change", async (e) => {
  await switchToMission(e.target.value || null);
});

document.getElementById("mission-new-toggle").addEventListener("click", () => {
  const form = document.getElementById("mission-new-form");
  form.hidden = !form.hidden;
});

document.getElementById("mission-new-create").addEventListener("click", async () => {
  const name = document.getElementById("mission-new-name").value.trim();
  if (!name) {
    alert("Mission name is required.");
    return;
  }
  const mission_number = document.getElementById("mission-new-number").value.trim() || null;
  const role = document.getElementById("mission-new-role").value.trim() || null;
  const res = await fetch("/api/missions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, mission_number, role }),
  });
  if (!res.ok) {
    alert("Could not create mission.");
    return;
  }
  const mission = await res.json();
  currentMissions.push(mission);
  document.getElementById("mission-new-name").value = "";
  document.getElementById("mission-new-number").value = "";
  document.getElementById("mission-new-role").value = "";
  document.getElementById("mission-new-form").hidden = true;
  populateMissionSelect();
  await switchToMission(mission.id);
});

document.getElementById("mission-add-member-btn").addEventListener("click", async () => {
  if (!currentMissionId) return;
  const cap_id = document.getElementById("mission-add-cap-id").value.trim();
  const role = document.getElementById("mission-add-role").value.trim() || null;
  const status = document.getElementById("mission-members-status");
  if (!cap_id) return;
  const res = await fetch(`/api/missions/${currentMissionId}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cap_id, role }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    status.textContent = `Failed: ${err.detail || res.statusText}`;
    return;
  }
  status.textContent = "Added.";
  document.getElementById("mission-add-cap-id").value = "";
  document.getElementById("mission-add-role").value = "";
  await refreshMissionMembers();
});

// ── Leave / Close / Reopen (item 5, 2026-10-18 plan) -- three separate,
// distinct actions, not merged. These are the first destructive-feeling
// actions this UI offers, so each uses a plain confirm() first, matching
// this app's existing alert()-only error-display convention (no custom
// modal built just for this).
document.getElementById("mission-leave-btn").addEventListener("click", async () => {
  if (!currentMissionId) return;
  if (!confirm("Leave this mission? You can rejoin later via the join box below.")) return;
  await fetch(`/api/missions/${currentMissionId}/members/me`, { method: "DELETE" });
  await refreshMissionList();
  await switchToMission(null);
});

document.getElementById("mission-close-btn").addEventListener("click", async () => {
  if (!currentMissionId) return;
  if (!confirm("Close this mission? Its history stays intact -- this only hides it from the default mission list.")) return;
  const closedId = currentMissionId;
  const res = await fetch(`/api/missions/${closedId}/close`, { method: "POST" });
  if (!res.ok) {
    alert("Could not close mission.");
    return;
  }
  const showClosed = document.getElementById("mission-show-closed-toggle").checked;
  await refreshMissionList(); // re-fetch so the list/select reflects include_closed correctly
  if (showClosed) {
    // Still visible (the checkbox is on) -- keep it selected, now showing Reopen.
    document.getElementById("mission-select").value = closedId;
    updateMissionMembersVisibility();
  } else {
    // It just dropped out of the default (open-only) list -- fall back to
    // unscoped rather than silently keeping a dead selection/stale data around.
    await switchToMission(null);
  }
});

document.getElementById("mission-reopen-btn").addEventListener("click", async () => {
  if (!currentMissionId) return;
  const res = await fetch(`/api/missions/${currentMissionId}/reopen`, { method: "POST" });
  if (!res.ok) {
    alert("Could not reopen mission.");
    return;
  }
  // Reopened missions are always in the default (open-only) list, so a plain
  // refresh keeps this one selected automatically via populateMissionSelect's
  // existing "keep the current id if it's still in the list" logic.
  await refreshMissionList();
});

// ── Join-a-mission combobox (item 4, 2026-10-18 plan) -- one control serves
// both "browse" and "search by number," per Gerry's answer, rather than two
// separate UI elements. Empty/focus shows the capped default list from
// GET /api/missions; typing re-queries with ?q=.
const missionJoinInput = document.getElementById("mission-join-input");
const missionJoinResults = document.getElementById("mission-join-results");

async function searchJoinableMissions(q) {
  const url = q ? `/api/missions?q=${encodeURIComponent(q)}` : "/api/missions";
  const res = await fetch(url);
  if (!res.ok) return;
  const rows = await res.json();
  if (!rows.length) {
    missionJoinResults.innerHTML = '<div style="padding:4px; font-size:11px; color:#9aa4ad;">No open missions found.</div>';
  } else {
    missionJoinResults.innerHTML = rows
      .map((m) => {
        const label = m.mission_number ? `${m.name} (${m.mission_number})` : m.name;
        return `<div class="mission-join-row" data-id="${m.id}" style="padding:4px 6px; font-size:12px; cursor:pointer;">${label}</div>`;
      })
      .join("");
    missionJoinResults.querySelectorAll(".mission-join-row").forEach((el) => {
      el.addEventListener("click", async () => {
        const res = await fetch(`/api/missions/${el.dataset.id}/join`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        if (!res.ok) {
          alert("Could not join mission.");
          return;
        }
        const mission = await res.json();
        if (!currentMissions.some((m) => m.id === mission.id)) currentMissions.push(mission);
        missionJoinInput.value = "";
        missionJoinResults.hidden = true;
        populateMissionSelect();
        await switchToMission(mission.id);
      });
    });
  }
  missionJoinResults.hidden = false;
}

missionJoinInput.addEventListener("focus", () => searchJoinableMissions(missionJoinInput.value.trim()));
missionJoinInput.addEventListener("input", () => searchJoinableMissions(missionJoinInput.value.trim()));
document.addEventListener("click", (e) => {
  if (!missionJoinResults.contains(e.target) && e.target !== missionJoinInput) missionJoinResults.hidden = true;
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  location.reload();
});

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById("login-error");
  errorEl.textContent = "";
  const body = {
    cap_id: document.getElementById("login-cap-id").value.trim(),
    password: document.getElementById("login-password").value,
    totp_code: document.getElementById("login-totp").value.trim(),
  };
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    errorEl.textContent = "Invalid CAP ID, password, or authenticator code.";
    return;
  }
  document.getElementById("login-password").value = "";
  document.getElementById("login-totp").value = "";
  await checkAuthAndInit();
});

async function checkAuthAndInit() {
  const res = await fetch("/api/auth/me");
  if (!res.ok) {
    document.getElementById("login-overlay").hidden = false;
    document.getElementById("user-badge").hidden = true;
    return;
  }
  const me = await res.json();
  currentUser = me.user;
  currentMissions = me.missions;
  document.getElementById("login-overlay").hidden = true;
  document.getElementById("user-badge").hidden = false;
  document.getElementById("user-badge-name").textContent = currentUser.display_name;
  document.getElementById("admin-requests-link").hidden = !currentUser.is_admin;
  populateMissionSelect();
  initTrackingData();
}

checkAuthAndInit();
