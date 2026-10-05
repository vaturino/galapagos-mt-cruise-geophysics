// Offline Cesium bathymetry/backscatter viewer.
// No network calls at runtime: no Ion, no default imagery/terrain, no CDN.
// Supports multiple datasets loaded and shown/hidden independently.

Cesium.Ion.defaultAccessToken = undefined;

const viewer = new Cesium.Viewer("cesiumContainer", {
  imageryProvider: false,
  baseLayerPicker: false,
  terrainProvider: new Cesium.EllipsoidTerrainProvider(),
  geocoder: false,
  homeButton: false,
  sceneModePicker: false,
  navigationHelpButton: false,
  animation: false,
  timeline: false,
  infoBox: false,
  selectionIndicator: false,
  fullscreenButton: true,
  requestRenderMode: true,
  maximumRenderTimeChange: Infinity,
});
viewer.scene.globe.show = false; // we supply our own surface; bare ellipsoid adds nothing
viewer.scene.skyAtmosphere.show = false; // atmosphere glow with no globe behind it just washes the view out white
viewer.scene.sun.show = false;
viewer.scene.moon.show = false;
viewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#050912");

// Mouse/trackpad camera controls are the primary way to look around: left-drag
// rotates, scroll/right-drag zooms, middle-drag (or ctrl+left-drag) tilts.
// The on-screen camera buttons are a secondary, input-device-independent way
// to nudge the view -- but the important thing (see setDatasetVisible and
// rebuildAllLoaded below) is that nothing else in this app fights the camera
// once you've moved it by hand: dataset toggles, colour/exaggeration/lighting
// changes no longer auto-recentre the view. Only the explicit "Reset view"
// button, and turning on the very first dataset when nothing else is visible,
// move the camera on their own.
const ctrl = viewer.scene.screenSpaceCameraController;
ctrl.enableRotate = true;
ctrl.enableZoom = true;
ctrl.enableTilt = true;
ctrl.enableLook = true;

const WGS84_A = 6378137.0;
const WGS84_F = 1.0 / 298.257223563;
const WGS84_E2 = WGS84_F * (2.0 - WGS84_F);

// Vectorised geodetic -> ECEF (WGS84), mirrors build_cesium_mesh.py exactly.
function geodeticToECEF(lonRad, latRad, heightM, outX, outY, outZ) {
  const n = lonRad.length;
  for (let i = 0; i < n; i++) {
    const lat = latRad[i];
    const sinLat = Math.sin(lat);
    const cosLat = Math.cos(lat);
    const nRad = WGS84_A / Math.sqrt(1.0 - WGS84_E2 * sinLat * sinLat);
    const h = heightM[i];
    const lon = lonRad[i];
    outX[i] = (nRad + h) * cosLat * Math.cos(lon);
    outY[i] = (nRad + h) * cosLat * Math.sin(lon);
    outZ[i] = (nRad * (1.0 - WGS84_E2) + h) * sinLat;
  }
}

function readSections(buffer, meta) {
  const out = {};
  for (const s of meta.sections) {
    const Ctor = {
      float64: Float64Array,
      float32: Float32Array,
      uint8: Uint8Array,
      uint32: Uint32Array,
    }[s.dtype];
    out[s.name] = new Ctor(buffer, s.offset, s.count * s.components);
  }
  return out;
}

// Cesium 1.145 targets WebGL2 / GLSL ES 3.00 (#version 300 es is auto-prepended),
// so custom Appearance shaders must use in/out, not attribute/varying. Cesium's
// own preamble already declares `out vec4 out_FragColor` -- assign to it, don't
// redeclare it. An unused `in float batchId;` is required too: Cesium's Primitive
// pipeline always appends pick-pass wrapper code that references it, even with
// a single GeometryInstance and allowPicking left at its default.
const vertexShaderSource = `
in vec3 position3DHigh;
in vec3 position3DLow;
in vec3 normal;
in vec4 color;
in float batchId;

out vec3 v_normalEC;
out vec4 v_color;

void main() {
    vec4 p = czm_computePosition();
    v_normalEC = czm_normal * normal;
    v_color = color;
    gl_Position = czm_modelViewProjectionRelativeToEye * p;
}
`;

function fragmentShaderSource(lightingEnabled) {
  const ambient = lightingEnabled ? 0.35 : 1.0;
  const diffuseScale = lightingEnabled ? 1.0 - ambient : 0.0;
  return `
in vec3 v_normalEC;
in vec4 v_color;

void main() {
    vec3 n = normalize(v_normalEC);
    vec3 lightDir = normalize(czm_sunDirectionEC);
    float diffuse = max(dot(n, lightDir), 0.0);
    float shade = ${ambient.toFixed(3)} + ${diffuseScale.toFixed(3)} * diffuse;
    out_FragColor = vec4(v_color.rgb * shade, v_color.a);
}
`;
}

// ---- global state ----
const state = {
  datasets: {}, // id -> { manifestEntry, meta, metaLoaded, sections, primitive, boundingSphere, loaded, visible, rasterCache }
  order: [],
  exaggeration: 2.0,
  colorMode: "depth",
  lightingEnabled: true,
  camera: { heading: 0, pitch: -35, rangeScale: 2.2 }, // heading/pitch in degrees
  trackPoints: [], // flattened points from every VISIBLE entry in trackLayers -- rebuilt by rebuildVisibleTrackPoints()
  trackLayers: new Map(), // fileName -> { fileName, kind, points: [...], visible }, one entry per loaded CSV
  trackBillboardCollection: null, // Cesium.BillboardCollection -- both "dredge" (circle) and "mt" (diamond) points, drawn as haloed canvas icons
  crossSection: {
    armed: false, // true while waiting for the next surface click to place A or B
    pointA: null, // { lonDeg, latDeg, cartesian }
    pointB: null,
    pointCollection: null, // Cesium.PointPrimitiveCollection, the A/B endpoint markers
    lineEntity: null, // draped polyline entity following the sampled profile
    profile: null, // { distKm, lonDeg, latDeg, depthM, drapedCartesians, totalKm } -- geometry along A-B, or null before both points are picked
    layerProfiles: [], // [{ id, label, kind, units, values: (number|null)[] }, ...] one per currently-visible dataset, same sample indexing as profile.distKm
    layerCanvases: [], // [{ canvas, layerProfile }, ...] -- the currently-rendered per-layer plot canvases, for the composite PNG download
  },
  geoExport: {
    mode: "entire", // "entire" | "select"
    format: "color", // "color" (RGBA picture) | "elevation" (single-band float32 metres)
    drawing: "idle", // "idle" | "armed" (waiting for mouse-down) | "dragging"
    corner1: null, // { lonDeg, latDeg } -- the drag's start corner
    bbox: null, // { west, east, south, north } in degrees, or null
    rectangleEntity: null,
  },
};

function setLoading(visible, text) {
  const el = document.getElementById("loading");
  if (text) document.getElementById("loadingText").textContent = text;
  el.classList.toggle("hidden", !visible);
}

function setProgress(frac) {
  document.getElementById("loadingBarInner").style.width = `${Math.round(frac * 100)}%`;
}

function anyBackscatter() {
  return state.order.some((id) => state.datasets[id].loaded && state.datasets[id].meta.has_backscatter);
}

function updateStats() {
  const box = document.getElementById("statsBox");
  const lines = [];
  let totalV = 0;
  let totalT = 0;
  for (const id of state.order) {
    const d = state.datasets[id];
    if (!d.loaded || !d.visible) continue;
    totalV += d.meta.vertex_count;
    totalT += d.meta.triangle_count;
    const label = d.meta.label || d.manifestEntry.label;
    lines.push(
      `${label}: ${d.meta.vertex_count.toLocaleString()} v, ${resolutionText(d.meta)}, ` +
        `${elevWord(d.meta.z_range_m)} ${d.meta.z_range_m[0].toFixed(0)} to ${d.meta.z_range_m[1].toFixed(0)} m`
    );
  }
  if (lines.length === 0) {
    box.textContent = "No datasets shown -- check a box above.";
    return;
  }
  box.textContent = `${totalV.toLocaleString()} vertices, ${totalT.toLocaleString()} triangles total\n` + lines.join("\n");
}

function elevWord(range) {
  return range[0] < 0 && range[1] > 0 ? "elev" : "depth";
}

// ---- dataset row meta text (resolution etc, shown even before checking) ----
function resolutionText(meta) {
  const shown = Math.round(meta.effective_resolution_m);
  if (meta.native_resolution_m == null) return `~${shown} m resolution`;
  const native = Math.round(meta.native_resolution_m);
  if (Math.abs(shown - native) <= 1) return `~${shown} m resolution (native)`;
  return `~${shown} m shown (native ~${native} m)`;
}

function formatDsMeta(meta, loaded) {
  let s = `${resolutionText(meta)} · ${elevWord(meta.z_range_m)} ${meta.z_range_m[0].toFixed(0)} to ${meta.z_range_m[1].toFixed(0)} m`;
  if (loaded) s += ` · ${meta.vertex_count.toLocaleString()} vertices`;
  return s;
}

function updateDatasetRowMeta(id) {
  const d = state.datasets[id];
  if (!d.meta) return;
  document.querySelectorAll(`[data-dsid="${id}"] .ds-meta`).forEach((el) => {
    el.textContent = formatDsMeta(d.meta, d.loaded);
  });
}

async function prefetchAllMeta() {
  await Promise.all(
    state.order.map(async (id) => {
      const d = state.datasets[id];
      try {
        const meta = await (await fetch(`${d.manifestEntry.path}/meta.json`)).json();
        d.meta = meta;
        d.metaLoaded = true;
        updateDatasetRowMeta(id);
      } catch (err) {
        console.error(`failed to prefetch meta.json for ${id}`, err);
      }
    })
  );
}

// Any dataset built with the absolute-elevation "globe" colormap (currently
// just GMRT_basemap) is, by construction, a background context layer that
// other checked datasets sit on top of. Its own real elevation is close
// enough to a detailed survey's at the same lon/lat that the GPU depth
// buffer can't reliably pick a winner -- the two surfaces are nearly
// coincident in true 3D space, so which one wins flips pixel-by-pixel
// (floating-point depth precision noise), which is exactly the "holey" /
// patchy look reported when a survey is checked on top of the basemap.
// Sinking every basemap-style dataset's rendered depth by a fixed, small
// (compared to ocean depths) real-world offset -- applied to the true
// depth *before* the exaggeration multiply, so it scales the same way as
// everything else and stays proportionally tiny at any exaggeration --
// guarantees any overlapping survey mesh's true elevation is always closer
// to the camera, so it wins the depth test everywhere it has data, with no
// visible effect on the basemap itself (it's already flagged as coarse
// background context, not for reading precise depths).
const BASEMAP_DEPTH_BIAS_M = 120;

function buildGeometry(sections, meta, exaggeration, colorMode) {
  const n = meta.vertex_count;
  const isBasemapLayer = !!(meta.colormap_stops && meta.colormap_stops.domain === "absolute_m");
  const depthBias = isBasemapLayer ? BASEMAP_DEPTH_BIAS_M : 0;

  const ex = new Float64Array(n);
  const ey = new Float64Array(n);
  const ez = new Float64Array(n);
  const hScaled = new Float64Array(n);
  for (let i = 0; i < n; i++) hScaled[i] = (sections.z_m[i] - depthBias) * exaggeration;
  geodeticToECEF(sections.lon_rad, sections.lat_rad, hScaled, ex, ey, ez);

  const positions = new Float64Array(n * 3);
  for (let i = 0; i < n; i++) {
    positions[i * 3] = ex[i];
    positions[i * 3 + 1] = ey[i];
    positions[i * 3 + 2] = ez[i];
  }

  const useBackscatter = colorMode === "backscatter" && sections.color_backscatter;
  const colorSrc = useBackscatter ? sections.color_backscatter : sections.color_depth;

  const geometry = new Cesium.Geometry({
    attributes: {
      position: new Cesium.GeometryAttribute({
        componentDatatype: Cesium.ComponentDatatype.DOUBLE,
        componentsPerAttribute: 3,
        values: positions,
      }),
      normal: new Cesium.GeometryAttribute({
        componentDatatype: Cesium.ComponentDatatype.FLOAT,
        componentsPerAttribute: 3,
        values: sections.normal,
      }),
      color: new Cesium.GeometryAttribute({
        componentDatatype: Cesium.ComponentDatatype.UNSIGNED_BYTE,
        componentsPerAttribute: 4,
        normalize: true,
        values: colorSrc,
      }),
    },
    indices: sections.indices,
    primitiveType: Cesium.PrimitiveType.TRIANGLES,
    boundingSphere: Cesium.BoundingSphere.fromVertices(positions),
  });

  return { geometry, boundingSphere: geometry.boundingSphere };
}

function buildPrimitiveFor(id) {
  const d = state.datasets[id];
  if (d.primitive) {
    viewer.scene.primitives.remove(d.primitive);
    d.primitive = null;
  }
  const { geometry, boundingSphere } = buildGeometry(d.sections, d.meta, state.exaggeration, state.colorMode);

  const appearance = new Cesium.Appearance({
    translucent: false,
    closed: false,
    vertexShaderSource,
    fragmentShaderSource: fragmentShaderSource(state.lightingEnabled),
    renderState: Cesium.Appearance.getDefaultRenderState(false, false, {
      depthTest: { enabled: true },
      cull: { enabled: false },
    }),
  });

  const instance = new Cesium.GeometryInstance({ geometry });
  const primitive = new Cesium.Primitive({
    geometryInstances: instance,
    appearance,
    asynchronous: false,
    compressVertices: false,
  });
  primitive.show = d.visible;

  viewer.scene.primitives.add(primitive);
  d.primitive = primitive;
  d.boundingSphere = boundingSphere;
}

function rebuildAllLoaded() {
  for (const id of state.order) {
    const d = state.datasets[id];
    if (d.loaded) buildPrimitiveFor(id);
  }
  viewer.scene.requestRender();
  updateStats();
  updateLegend();
  placeTrackPoints();
  placePrevDredges(true);
  if (dl.rows.length) dlDraw();
  ship.hCache.clear();
  if (document.getElementById("shipToggle").checked) pollShip();
  if (nativeInit.viewerIds) nativeSuggest();
  updateCrossSection();
  setLoading(false);
}

function visibleBoundingSphere() {
  const spheres = state.order
    .map((id) => state.datasets[id])
    .filter((d) => d.loaded && d.visible && d.boundingSphere)
    .map((d) => d.boundingSphere);
  if (spheres.length === 0) return null;
  return Cesium.BoundingSphere.fromBoundingSpheres(spheres);
}

function flyToVisible(duration) {
  const bs = visibleBoundingSphere();
  if (!bs) return;
  state.camera.range = bs.radius * state.camera.rangeScale;
  viewer.camera.flyToBoundingSphere(bs, {
    duration: duration ?? 0.0,
    offset: new Cesium.HeadingPitchRange(
      Cesium.Math.toRadians(state.camera.heading),
      Cesium.Math.toRadians(state.camera.pitch),
      state.camera.range
    ),
  });
}

async function loadDataset(id) {
  const d = state.datasets[id];
  setLoading(true, `Fetching ${d.manifestEntry.label}…`);
  if (!d.metaLoaded) {
    d.meta = await (await fetch(`${d.manifestEntry.path}/meta.json`)).json();
    d.metaLoaded = true;
  }
  const buf = await (await fetch(`${d.manifestEntry.path}/mesh.bin`)).arrayBuffer();
  d.sections = readSections(buf, d.meta);
  d.loaded = true;

  document.querySelectorAll(`[data-dsid="${id}"] .ds-label`).forEach((el) => {
    el.textContent = d.meta.label || d.manifestEntry.label;
  });
  updateDatasetRowMeta(id);

  document.querySelector('input[name="colorMode"][value="backscatter"]').disabled = !anyBackscatter();

  buildPrimitiveFor(id);
  setLoading(false);
}

async function setDatasetVisible(id, visible) {
  const d = state.datasets[id];
  const anyVisibleBefore = state.order.some((oid) => oid !== id && state.datasets[oid].visible);
  d.visible = visible;
  // a dataset with backscatter has two checkboxes (Bathymetry + Backscatter
  // sections) bound to the same underlying visibility -- keep both in sync.
  document.querySelectorAll(`input[data-id="${id}"]`).forEach((cb) => {
    cb.checked = visible;
  });
  if (visible && !d.loaded) {
    try {
      await loadDataset(id); // loadDataset() clears the loading overlay itself
    } catch (err) {
      // e.g. mesh.bin larger than the browser's ~2 GB ArrayBuffer limit, or meta.json not
      // matching mesh.bin -- say so instead of silently drawing nothing
      console.error(`Failed to load ${id}:`, err);
      d.visible = false;
      document.querySelectorAll(`input[data-id="${id}"]`).forEach((cb) => (cb.checked = false));
      setLoading(true, `Could not load ${d.manifestEntry.label}: ${err.message}`);
      return;
    }
  } else {
    if (d.primitive) d.primitive.show = visible;
    setLoading(false);
  }
  viewer.scene.requestRender();
  updateStats();
  updateLegend();
  placeTrackPoints();
  placePrevDredges(true);
  if (dl.rows.length) dlDraw();
  ship.hCache.clear();
  if (document.getElementById("shipToggle").checked) pollShip();
  if (nativeInit.viewerIds) nativeSuggest();
  updateCrossSection();
  // Only auto-frame when going from "nothing visible" to "something visible" --
  // otherwise this would yank the camera away from wherever you've manually
  // looked every time you check/uncheck a box. Explicit "Reset view" still
  // always reframes.
  if (visible && !anyVisibleBefore) flyToVisible(0.8);
}

// ---- UI: dataset list, grouped into collapsible Bathymetry / Backscatter /
// Geophysics sections. Backscatter isn't a separate mesh -- it's an
// alternate colouring of a bathymetry dataset's own mesh (see the "Colour
// by" radio moved into that section) -- so any dataset flagged
// has_backscatter in the manifest gets a SECOND row rendered into the
// Backscatter section, sharing the same underlying state.datasets[id] entry
// and kept in sync (both checkboxes reflect the same d.visible) via
// data-id-based lookups in setDatasetVisible/updateDatasetRowMeta/loadDataset.
function buildDatasetRows(manifest) {
  const containers = {
    bathymetry: document.getElementById("datasetList-bathymetry"),
    backscatter: document.getElementById("datasetList-backscatter"),
    geophysics: document.getElementById("datasetList-geophysics"),
  };
  Object.values(containers).forEach((c) => {
    if (c) c.innerHTML = "";
  });

  function makeRow(entry, checkedDefault) {
    const row = document.createElement("div");
    row.className = "ds-row";
    row.setAttribute("data-dsid", entry.id);
    row.innerHTML = `
      <label>
        <input type="checkbox" data-id="${entry.id}" ${checkedDefault ? "checked" : ""}>
        <span class="ds-label">${entry.label}</span>
      </label>
      <div class="ds-meta">loading info…</div>
    `;
    row.querySelector("input").addEventListener("change", (e) => {
      setLoading(true, e.target.checked ? "Loading…" : "Updating…");
      setTimeout(() => setDatasetVisible(entry.id, e.target.checked), 10);
    });
    return row;
  }

  const firstId = manifest.datasets.length ? manifest.datasets[0].id : null;

  manifest.datasets.forEach((entry) => {
    state.order.push(entry.id);
    state.datasets[entry.id] = { manifestEntry: entry, loaded: false, metaLoaded: false, visible: false, primitive: null };
  });

  manifest.datasets.forEach((entry) => {
    const category = entry.category || "bathymetry";
    const isFirst = entry.id === firstId;
    const target = containers[category] || containers.bathymetry;
    if (target) target.appendChild(makeRow(entry, isFirst));
    if (entry.has_backscatter && containers.backscatter) {
      containers.backscatter.appendChild(makeRow(entry, isFirst));
    }
  });
}

// ---- UI wiring: global controls ----
document.getElementById("exagSlider").addEventListener("input", (e) => {
  document.getElementById("exagValue").textContent = `${parseFloat(e.target.value).toFixed(1)}×`;
});
document.getElementById("exagSlider").addEventListener("change", (e) => {
  state.exaggeration = parseFloat(e.target.value);
  setLoading(true, "Rebuilding mesh…");
  setTimeout(rebuildAllLoaded, 10);
});

document.querySelectorAll('input[name="colorMode"]').forEach((el) => {
  el.addEventListener("change", (e) => {
    if (!e.target.checked) return;
    state.colorMode = e.target.value;
    setLoading(true, "Recolouring…");
    setTimeout(rebuildAllLoaded, 10);
  });
});

document.getElementById("lightingToggle").addEventListener("change", (e) => {
  state.lightingEnabled = e.target.checked;
  setLoading(true, "Updating shading…");
  setTimeout(rebuildAllLoaded, 10);
});

document.getElementById("resetViewBtn").addEventListener("click", () => flyToVisible(1.0));

// ---- UI wiring: on-screen camera controls (secondary to mouse drag) ----
function nudgeCamera(dHeading, dPitch, zoomFactor) {
  state.camera.heading = (state.camera.heading + (dHeading || 0) + 360) % 360;
  state.camera.pitch = Cesium.Math.clamp(state.camera.pitch + (dPitch || 0), -89, -2);
  if (zoomFactor) {
    const bs = visibleBoundingSphere();
    const base = bs ? bs.radius * state.camera.rangeScale : state.camera.range || 100000;
    state.camera.range = Cesium.Math.clamp((state.camera.range || base) * zoomFactor, 50, base * 20);
  }
  const bs = visibleBoundingSphere();
  if (!bs) return;
  if (!state.camera.range) state.camera.range = bs.radius * state.camera.rangeScale;
  viewer.camera.flyToBoundingSphere(bs, {
    duration: 0.25,
    offset: new Cesium.HeadingPitchRange(
      Cesium.Math.toRadians(state.camera.heading),
      Cesium.Math.toRadians(state.camera.pitch),
      state.camera.range
    ),
  });
}

document.getElementById("rotateLeftBtn").addEventListener("click", () => nudgeCamera(-20, 0));
document.getElementById("rotateRightBtn").addEventListener("click", () => nudgeCamera(20, 0));
document.getElementById("tiltUpBtn").addEventListener("click", () => nudgeCamera(0, 10));
document.getElementById("tiltDownBtn").addEventListener("click", () => nudgeCamera(0, -10));
document.getElementById("zoomInBtn").addEventListener("click", () => nudgeCamera(0, 0, 0.7));
document.getElementById("zoomOutBtn").addEventListener("click", () => nudgeCamera(0, 0, 1.4));

// picking: report depth (and backscatter, if the raw value array is present)
const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
handler.setInputAction((movement) => {
  // The export-rectangle drag (see "GeoTIFF export region" below) owns
  // left-clicks while it's armed/dragging -- skip the depth readout so a
  // stray click while positioning the rectangle doesn't also drop a
  // cross-section endpoint or overwrite the pick box.
  if (state.geoExport.drawing !== "idle") return;
  if (Date.now() - (state.geoExport.lastDragEnd || 0) < 400) return; // the click that ends a rectangle drag
  const cartesian = viewer.scene.pickPosition(movement.position);
  const box = document.getElementById("pickBox");
  if (!Cesium.defined(cartesian)) {
    box.textContent = "No surface at that point -- click on the mesh.";
    return;
  }
  const carto = Cesium.Cartographic.fromCartesian(cartesian);
  const lonDeg = Cesium.Math.toDegrees(carto.longitude);
  const latDeg = Cesium.Math.toDegrees(carto.latitude);
  // the GMRT basemap is drawn BASEMAP_DEPTH_BIAS_M deeper than its data (so surveys sit on top of
  // it); add that back when the picked surface is the basemap
  const picked = viewer.scene.pick(movement.position);
  const pickedDs = picked && Object.values(state.datasets).find((d) => d.primitive && d.primitive === picked.primitive);
  const isBase = !!(pickedDs && pickedDs.meta && pickedDs.meta.colormap_stops && pickedDs.meta.colormap_stops.domain === "absolute_m");
  const trueDepth = carto.height / state.exaggeration + (isBase ? BASEMAP_DEPTH_BIAS_M : 0); // elevation, m (negative = below sea level)
  box.textContent = `lon ${lonDeg.toFixed(5)}, lat ${latDeg.toFixed(5)}\ndepth (approx, from picked surface): ${trueDepth.toFixed(0)} m`;

  // Cross-section picking mode (see "Cross-section tool" below) piggybacks
  // on this same click -- the depth readout above still always happens.
  // one tool per click: a dredge line being drawn takes the click, else the cross-section
  if (dl.armed) dlPlacePoint(lonDeg, latDeg, -trueDepth); // DredgeLines.csv depths are positive down
  else if (state.crossSection.armed) placeCrossSectionEndpoint(cartesian);
}, Cesium.ScreenSpaceEventType.LEFT_CLICK);

// =====================================================================
// Legend / colorbars
// =====================================================================

// JS port of build_cesium_mesh.py's globe.cpt sampling (absolute elevation
// domain, hard hinge at sea level).
function sampleGlobeColorJS(elevM, ocean, land) {
  const zc = Math.max(-10000, Math.min(10000, elevM));
  const stops = elevM >= 0 ? land : ocean;
  return [interpStops(zc, stops, 0), interpStops(zc, stops, 1), interpStops(zc, stops, 2)];
}

// JS port of the "depth"/"backscatter" relative ramps: stops are [t,r,g,b]
// with t in [0,1].
function sampleRelativeColorJS(t, stops) {
  const tc = Math.max(0, Math.min(1, t));
  return [interpStops(tc, stops, 0), interpStops(tc, stops, 1), interpStops(tc, stops, 2)];
}

function interpStops(x, stops, channelIdx) {
  if (x <= stops[0][0]) return stops[0][1 + channelIdx];
  const last = stops[stops.length - 1];
  if (x >= last[0]) return last[1 + channelIdx];
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i];
    const b = stops[i + 1];
    if (x >= a[0] && x <= b[0]) {
      const t = b[0] === a[0] ? 0 : (x - a[0]) / (b[0] - a[0]);
      return a[1 + channelIdx] + t * (b[1 + channelIdx] - a[1 + channelIdx]);
    }
  }
  return last[1 + channelIdx];
}

// Generated by sampling the real colour maps (cmocean 3.0.3, cmcrameri, matplotlib 3.10)
// at 17 evenly spaced points. Stops: [t, r, g, b], t in [0,1]; t = 0 is the LOWEST value
// in the colour window (for bathymetry, the deepest water).
const EXTRA_PALETTES = {
  deep: { label: "Deep (cmocean) - dark = deep", stops: [[0.0000,40,26,44], [0.0625,50,37,65], [0.1250,59,49,90], [0.1875,65,61,117], [0.2500,64,76,139], [0.3125,62,94,147], [0.3750,63,110,151], [0.4375,67,126,154], [0.5000,72,142,158], [0.5625,78,158,161], [0.6250,85,174,163], [0.6875,98,191,164], [0.7500,120,206,163], [0.8125,152,218,164], [0.8750,187,230,172], [0.9375,221,242,186], [1.0000,253,254,204]] },
  haline: { label: "Haline (cmocean)", stops: [[0.0000,42,24,108], [0.0625,46,29,147], [0.1250,32,52,162], [0.1875,15,74,152], [0.2500,15,91,144], [0.3125,28,106,140], [0.3750,41,119,137], [0.4375,51,133,136], [0.5000,60,147,135], [0.5625,68,162,132], [0.6250,80,176,126], [0.6875,97,191,116], [0.7500,122,203,103], [0.8125,158,214,92], [0.8750,196,221,101], [0.9375,227,230,126], [1.0000,253,239,154]] },
  ice: { label: "Ice (cmocean) - dark = deep", stops: [[0.0000,4,6,19], [0.0625,19,19,42], [0.1250,33,32,65], [0.1875,46,44,90], [0.2500,56,57,117], [0.3125,62,71,143], [0.3750,63,87,163], [0.4375,62,105,176], [0.5000,66,123,183], [0.5625,75,140,189], [0.6250,88,157,195], [0.6875,104,174,202], [0.7500,123,191,208], [0.8125,147,207,216], [0.8750,177,222,226], [0.9375,207,237,239], [1.0000,234,253,253]] },
  dense: { label: "Dense (cmocean) - dark = deep", stops: [[0.0000,54,14,36], [0.0625,72,18,55], [0.1250,89,23,80], [0.1875,101,33,106], [0.2500,110,45,132], [0.3125,117,60,156], [0.3750,120,77,178], [0.4375,121,94,197], [0.5000,120,113,213], [0.5625,117,132,223], [0.6250,115,151,228], [0.6875,121,169,228], [0.7500,134,185,227], [0.8125,154,200,226], [0.8750,177,214,227], [0.9375,203,228,232], [1.0000,230,241,241]] },
  oslo: { label: "Oslo (Crameri) - dark = deep", stops: [[0.0000,1,1,1], [0.0625,10,18,27], [0.1250,14,30,46], [0.1875,17,43,68], [0.2500,21,57,91], [0.3125,28,72,115], [0.3750,38,87,140], [0.4375,55,103,166], [0.5000,80,123,188], [0.5625,104,140,199], [0.6250,125,153,202], [0.6875,144,165,201], [0.7500,163,177,202], [0.8125,184,191,205], [0.8750,207,210,216], [0.9375,232,233,234], [1.0000,255,255,255]] },
  batlow: { label: "Batlow (Crameri)", stops: [[0.0000,1,25,89], [0.0625,13,49,93], [0.1250,17,67,96], [0.1875,23,82,98], [0.2500,34,96,97], [0.3125,53,106,89], [0.3750,77,115,77], [0.4375,103,123,62], [0.5000,130,130,49], [0.5625,161,138,43], [0.6250,192,144,54], [0.6875,221,149,77], [0.7500,242,157,109], [0.8125,252,169,147], [0.8750,253,180,182], [0.9375,252,192,216], [1.0000,250,204,250]] },
  viridis: { label: "Viridis", stops: [[0.0000,68,1,84], [0.0625,72,24,106], [0.1250,71,45,123], [0.1875,66,64,134], [0.2500,59,82,139], [0.3125,51,99,141], [0.3750,44,114,142], [0.4375,38,130,142], [0.5000,33,145,140], [0.5625,31,160,136], [0.6250,40,174,128], [0.6875,63,188,115], [0.7500,94,201,98], [0.8125,132,212,75], [0.8750,173,220,48], [0.9375,216,226,25], [1.0000,253,231,37]] },
  cividis: { label: "Cividis (colour-blind safe)", stops: [[0.0000,0,34,78], [0.0625,0,46,106], [0.1250,26,56,111], [0.1875,50,67,109], [0.2500,67,78,108], [0.3125,83,90,109], [0.3750,97,101,111], [0.4375,111,112,115], [0.5000,125,124,120], [0.5625,140,136,120], [0.6250,155,148,118], [0.6875,171,160,114], [0.7500,188,174,108], [0.8125,205,187,99], [0.8750,222,201,88], [0.9375,240,216,70], [1.0000,254,232,56]] },
  turbo: { label: "Turbo (high contrast rainbow)", stops: [[0.0000,48,18,59], [0.0625,64,64,162], [0.1250,70,107,227], [0.1875,66,148,255], [0.2500,40,188,235], [0.3125,24,221,194], [0.3750,50,242,152], [0.4375,109,254,98], [0.5000,164,252,60], [0.5625,205,236,52], [0.6250,238,207,58], [0.6875,253,172,52], [0.7500,251,126,33], [0.8125,235,80,14], [0.8750,208,47,5], [0.9375,169,22,1], [1.0000,122,4,3]] },
  spectral: { label: "Spectral (red = deep)", stops: [[0.0000,158,1,66], [0.0625,193,39,74], [0.1250,221,74,76], [0.1875,240,103,68], [0.2500,249,142,82], [0.3125,253,181,103], [0.3750,254,212,129], [0.4375,254,236,159], [0.5000,255,255,190], [0.5625,239,249,166], [0.6250,214,238,155], [0.6875,177,223,163], [0.7500,134,207,165], [0.8125,94,185,169], [0.8750,61,149,184], [0.9375,68,113,178], [1.0000,94,79,162]] },
  slope: { label: "SLOPE (deg) from the mesh - YlOrRd", slope: true, stops: [[0.0000,255,255,204], [0.0625,255,246,182], [0.1250,255,237,160], [0.1875,254,227,139], [0.2500,254,217,118], [0.3125,254,197,97], [0.3750,254,178,76], [0.4375,253,159,68], [0.5000,253,140,60], [0.5625,252,108,51], [0.6250,252,77,42], [0.6875,239,51,35], [0.7500,226,25,28], [0.8125,207,12,33], [0.8750,187,0,38], [0.9375,157,0,38], [1.0000,128,0,38]] },
  greys: { label: "Greyscale - dark = deep", stops: [[0.0000,0,0,0], [0.0625,17,17,17], [0.1250,36,36,36], [0.1875,58,58,58], [0.2500,81,81,81], [0.3125,98,98,98], [0.3750,114,114,114], [0.4375,132,132,132], [0.5000,149,149,149], [0.5625,169,169,169], [0.6250,189,189,189], [0.6875,203,203,203], [0.7500,217,217,217], [0.8125,228,228,228], [0.8750,240,240,240], [0.9375,247,247,247], [1.0000,255,255,255]] },
};

// Land colour for bathymetry layers when an extra (ocean) palette is chosen: those palettes
// are for water depth, so cells above sea level get one flat colour instead of being
// squeezed onto the ocean ramp. Geophysics layers colour by their value, so this never applies.
const PALETTE_LAND_RGB = [184, 173, 140];

// Range a chosen palette spans by default. Geophysics layers: their own value range.
// Bathymetry layers: the water part only (deepest point to sea level, or to the
// shallowest point if the layer has no land), so the whole ramp goes to the seafloor.
// Seafloor slope (deg) per vertex from the mesh's own normals (computed at build time from the
// TRUE, unexaggerated surface): slope = acos(|n . up|), up = the local ellipsoid normal. Smooth
// vertex normals average the adjacent triangles, i.e. a slope over about two cells of the
// displayed mesh (meta.effective_resolution_m). Cached per dataset.
function slopeDegArray(d) {
  if (d.sections.slope_deg) return d.sections.slope_deg;
  const n = d.meta.vertex_count, nv = d.sections.normal, lo = d.sections.lon_rad, la = d.sections.lat_rad;
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const cl = Math.cos(la[i]);
    const dot = nv[i * 3] * cl * Math.cos(lo[i]) + nv[i * 3 + 1] * cl * Math.sin(lo[i]) + nv[i * 3 + 2] * Math.sin(la[i]);
    out[i] = (Math.acos(Math.min(1, Math.abs(dot))) * 180) / Math.PI;
  }
  d.sections.slope_deg = out;
  return out;
}
const isSlopePalette = (d) => !!(d.palette && EXTRA_PALETTES[d.palette] && EXTRA_PALETTES[d.palette].slope);

function paletteDefaultRange(d) {
  if (isSlopePalette(d)) return [0, 40];
  if (d.meta.is_geophysics || d.meta.legend_range) return d.meta.legend_range || d.meta.z_range_m;
  const [lo, hi] = d.meta.z_range_m;
  return lo < 0 ? [lo, Math.min(hi, 0)] : [lo, hi];
}

function paletteStops(d) {
  const p = EXTRA_PALETTES[d.palette];
  if (!p) return null;
  return d.paletteReverse ? p.stops.map((s, i, a) => [s[0], ...a[a.length - 1 - i].slice(1)]) : p.stops;
}

// Switch a loaded layer's "depth" colouring to a named palette ("builtin" = the colours
// baked at build time). Resets the colour window to the palette's default range.
function setDatasetPalette(id, key, reverse) {
  const d = state.datasets[id];
  if (!d) return;
  d.palette = key === "builtin" ? null : key;
  d.paletteReverse = !!reverse;
  d.colorWindow = d.colorWindow || {};
  d.colorWindow.depth = null; // recomputed for the new palette's range
  if (!d.loaded) return;
  if (!d.palette) {
    // back to the baked colours: reload them from the original section
    d.sections.color_depth = d.sections.color_depth_baked || d.sections.color_depth;
    d.rasterCache = {};
    if (d.visible) { buildPrimitiveFor(id); viewer.scene.requestRender(); }
    updateLegend();
    updateCrossSection();
    return;
  }
  if (!d.sections.color_depth_baked) d.sections.color_depth_baked = d.sections.color_depth;
  commitColorWindow(id, "depth", getDepthColorWindow(d));
}

// Which colour source (and its legend metadata) a dataset is actually
// rendered with right now -- mirrors buildGeometry()'s own fallback so the
// legend always matches what's on screen.
function pickColorSource(d) {
  const useBackscatter = state.colorMode === "backscatter" && d.sections && d.sections.color_backscatter;
  return useBackscatter ? d.sections.color_backscatter : d.sections.color_depth;
}

// Same lookup as pickColorMeta, but for an explicitly-named mode rather than
// the 3D mesh's current display mode -- used by the colour-rewindowing code
// below, which needs to resolve "the depth ramp" or "the backscatter ramp"
// specifically, independent of what's currently showing on screen.
function pickColorMetaFor(d, mode) {
  if (mode === "backscatter") {
    return { kind: "backscatter", ramp: d.meta.backscatter_colormap_stops, range: d.meta.backscatter_range, unit: "" };
  }
  // A geophysics layer (build_geophysics_drape.py) colours vertices by a
  // value that isn't the mesh's own elevation, so it carries its own
  // legend_range/legend_units/legend_kind rather than reusing z_range_m
  // (which for those layers is the *terrain* elevation used only for 3D
  // draping, not what the colour ramp is keyed to). Any dataset without
  // these fields falls back to the original depth-in-metres behaviour.
  const unit = d.meta.legend_units != null ? d.meta.legend_units : " m";
  const kind = d.meta.legend_kind || "depth";
  if (d.palette && EXTRA_PALETTES[d.palette]) {
    if (isSlopePalette(d)) {
      const res = d.meta.effective_resolution_m ? `, ${Math.round(d.meta.effective_resolution_m)} m mesh` : "";
      return { kind: `slope${res}`, ramp: { stops: paletteStops(d) }, range: [0, 40], unit: "\u00b0", palette: d.palette };
    }
    return { kind, ramp: { stops: paletteStops(d) }, range: paletteDefaultRange(d), unit, palette: d.palette };
  }
  const range = d.meta.legend_range || d.meta.z_range_m;
  return { kind, ramp: d.meta.colormap_stops, range, unit };
}

function pickColorMeta(d) {
  const useBackscatter = state.colorMode === "backscatter" && d.meta.has_backscatter;
  return pickColorMetaFor(d, useBackscatter ? "backscatter" : "depth");
}

// =====================================================================
// Manual colour re-windowing (draggable "colour stretch")
// =====================================================================
// A dataset's color_depth/color_backscatter mesh.bin section is baked at
// build time from ITS FULL default range (legend_range / z_range_m /
// backscatter_range, or, for the GMRT basemap, the fixed ±10000 m domain
// its ocean/land table covers). Re-windowing narrows that mapping at
// runtime: values outside the chosen [lo, hi] clamp to the ramp's end
// colours and values inside it spread across the full spectrum, exactly
// like dragging the two handles on a colourbar to punch up
// contrast over a sub-range. Two ramp shapes are supported (see
// recolorDatasetForWindow below): a "relative" ramp (a single stops list
// sampled by t in [0,1] -- see sampleRelativeColorJS), used by every
// survey/geophysics/backscatter layer, and the GMRT basemap's "globe" ramp
// (two joined ocean/land tables keyed by absolute elevation, hinged at sea
// level -- see sampleGlobeColorJS), windowed by remapping the picked
// [lo, hi] elevation range onto that ramp's own fixed [-10000, 10000]
// domain before sampling it.
//
// Two independent windows are tracked per dataset -- "depth" (the
// z_m/value-based ramp, i.e. colormap_stops -- this is what the GMRT
// basemap uses too, it just has no separate has_backscatter mode) and
// "backscatter" (MV1007's separate value_backscatter-based ramp) --
// because a dataset with has_backscatter can be displayed, and
// re-windowed, in either mode independently. The cross-section dock always
// colours by the "depth" window regardless of which mode the 3D mesh is
// currently showing, since a profile plots depth/geophysics value, never
// backscatter.
function clamp01range(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function freshColorWindow(range) {
  const [lo, hi] = range;
  return { lo, hi, fullLo: lo, fullHi: hi };
}

// The "depth" window (z_m for bathymetry, the raw geophysics "value" section
// for a geophysics drape) -- this is also what the cross-section profile
// fill always uses, independent of the 3D mesh's current colour mode.
function getDepthColorWindow(d) {
  d.colorWindow = d.colorWindow || {};
  if (!d.colorWindow.depth) {
    const colorMeta = pickColorMetaFor(d, "depth");
    let range;
    if (colorMeta.ramp && colorMeta.ramp.ocean && colorMeta.ramp.land) {
      // Globe ramp: its real domain is whatever its own ocean/land stop
      // tables actually span (the ocean table's first entry to the land
      // table's last), not the dataset's own z_range_m -- so the handle
      // track covers the ramp's full spectrum, matching what
      // drawLegendCanvas below draws into the bar.
      const oceanLo = colorMeta.ramp.ocean[0][0];
      const landHi = colorMeta.ramp.land[colorMeta.ramp.land.length - 1][0];
      range = [oceanLo, landHi];
    } else {
      range = colorMeta.palette ? colorMeta.range : d.meta.legend_range || d.meta.z_range_m;
    }
    d.colorWindow.depth = freshColorWindow(range);
  }
  return d.colorWindow.depth;
}

function getBackscatterColorWindow(d) {
  d.colorWindow = d.colorWindow || {};
  if (!d.colorWindow.backscatter && d.meta.backscatter_range) {
    d.colorWindow.backscatter = freshColorWindow(d.meta.backscatter_range);
  }
  return d.colorWindow.backscatter;
}

// Which window applies to what's actually on the 3D mesh right now, mirrors
// pickColorMeta's own useBackscatter check.
function getActiveColorWindow(d) {
  const useBackscatter = state.colorMode === "backscatter" && d.meta.has_backscatter;
  return useBackscatter
    ? { mode: "backscatter", win: getBackscatterColorWindow(d) }
    : { mode: "depth", win: getDepthColorWindow(d) };
}

// Recomputes color_depth or color_backscatter (whichever `mode` names) from
// the dataset's own raw per-vertex quantity and the current [lo, hi] window,
// replacing that mesh.bin section in place -- buildPrimitiveFor() then picks
// it up exactly like the server-baked original. No-op (returns false) for a
// layer that can't be windowed at all (no relative stops table and no
// ocean/land table, or no raw source for that mode) so callers can skip the
// rebuild.
function roundByte(v) {
  return Math.max(0, Math.min(255, Math.round(v)));
}

function recolorDatasetForWindow(d, mode, win) {
  if (mode === "depth" && d.sections && !d.sections.color_depth_baked) d.sections.color_depth_baked = d.sections.color_depth;
  const colorMeta = pickColorMetaFor(d, mode);
  const isRelative = !!(colorMeta.ramp && colorMeta.ramp.stops);
  const isGlobe = !!(colorMeta.ramp && colorMeta.ramp.ocean && colorMeta.ramp.land);
  if (!isRelative && !isGlobe) return false;
  const srcArr = mode === "backscatter" ? d.sections.value_backscatter
    : isSlopePalette(d) ? slopeDegArray(d) : (d.sections.value || d.sections.z_m);
  if (!srcArr) return false;
  const n = d.meta.vertex_count;
  const span = win.hi - win.lo || 1e-9;
  const out = new Uint8Array(n * 4);
  if (isRelative) {
    // Truncating (not rounding) here is deliberate: it's what makes the
    // un-windowed default reproduce the server-baked color_depth/
    // color_backscatter bit-for-bit (verified against real data) -- this
    // path is already a single division (t, once), so there's no
    // accumulated round-trip error for rounding to guard against, unlike
    // the globe branch below.
    const stops = colorMeta.ramp.stops;
    const landFlat = !!colorMeta.palette && mode === "depth" && !d.meta.is_geophysics && !d.sections.value && !isSlopePalette(d);
    for (let i = 0; i < n; i++) {
      const t = (srcArr[i] - win.lo) / span;
      const rgb = landFlat && srcArr[i] > 0 ? PALETTE_LAND_RGB : sampleRelativeColorJS(t, stops);
      const o = i * 4;
      out[o] = rgb[0] | 0;
      out[o + 1] = rgb[1] | 0;
      out[o + 2] = rgb[2] | 0;
      out[o + 3] = 255;
    }
  } else {
    // Globe ramp: remap the picked [lo, hi] elevation window onto the
    // ramp's own fixed domain (win.fullLo/fullHi -- see getDepthColorWindow)
    // before handing it to sampleGlobeColorJS, so narrowing the handles
    // stretches contrast across just that sub-range of elevation -- which
    // can land entirely in the ocean table, entirely in the land table, or
    // straddle sea level, exactly like dragging the legend's own handles.
    const fullSpan = win.fullHi - win.fullLo || 1e-9;
    const { ocean, land } = colorMeta.ramp;
    // At the untouched full window the remap is a no-op (t==(elev-fullLo)/
    // fullSpan, pseudoElev==fullLo+t*fullSpan --> algebraically elev again)
    // -- skip the round-trip arithmetic entirely rather than let float
    // rounding in that division+multiplication occasionally nudge a value
    // across a stop boundary and shift a channel by 1/255. Not just a
    // micro-optimisation: this is what guarantees the un-windowed default
    // reproduces sampleGlobeColorJS(elev) exactly, byte for byte.
    const isIdentity = win.lo === win.fullLo && win.hi === win.fullHi;
    for (let i = 0; i < n; i++) {
      const elev = srcArr[i];
      const pseudoElev = isIdentity ? elev : win.fullLo + clamp01range((elev - win.lo) / span, 0, 1) * fullSpan;
      const rgb = sampleGlobeColorJS(pseudoElev, ocean, land);
      const o = i * 4;
      out[o] = roundByte(rgb[0]);
      out[o + 1] = roundByte(rgb[1]);
      out[o + 2] = roundByte(rgb[2]);
      out[o + 3] = 255;
    }
  }
  if (mode === "backscatter") d.sections.color_backscatter = out;
  else d.sections.color_depth = out;
  d.rasterCache = {}; // colour raster (buildDatasetRaster, used by GeoTIFF export) is now stale
  return true;
}

// Applied after a handle drag is released: recolour the mesh, refresh the
// legend (so the handles/labels reflect the committed window), and refresh
// an open cross-section (its profile fill depends on the "depth" window).
function commitColorWindow(id, mode, win) {
  const d = state.datasets[id];
  if (!d || !d.loaded) return;
  const changed = recolorDatasetForWindow(d, mode, win);
  if (changed && d.visible) {
    buildPrimitiveFor(id);
    viewer.scene.requestRender();
  }
  updateLegend();
  if (mode === "depth") updateCrossSection();
}

function drawLegendCanvas(canvas, colorMeta) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  const grad = ctx.createLinearGradient(0, 0, w, 0);
  const N = 32;
  const [lo, hi] = colorMeta.range;
  for (let i = 0; i <= N; i++) {
    const t = i / N;
    const val = lo + t * (hi - lo);
    // Structural check (ocean+land stop tables present) rather than a fixed
    // domain-string allowlist: "absolute_m" (globe.cpt, fixed universal
    // domain, background layers) and "absolute_m_survey" (build_cesium_mesh
    // .py's --colormap relief, a dataset's own min/max, still a foreground
    // layer -- see BASEMAP_DEPTH_BIAS_M above) both sample the same way for
    // the legend; only isBasemapLayer's domain === "absolute_m" check above
    // decides the background-layer depth sink, which relief must NOT trigger.
    const rgb =
      colorMeta.ramp.ocean && colorMeta.ramp.land
        ? sampleGlobeColorJS(val, colorMeta.ramp.ocean, colorMeta.ramp.land)
        : sampleRelativeColorJS(t, colorMeta.ramp.stops);
    grad.addColorStop(t, `rgb(${rgb[0] | 0},${rgb[1] | 0},${rgb[2] | 0})`);
  }
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
}

// Builds one legend row. For a re-windowable ramp (colorMeta.ramp.stops --
// anything except the GMRT basemap's absolute ocean/land table) this adds
// the draggable dual handles: the canvas always shows the
// FULL spectrum across the layer's full data range, the two handles mark
// the current re-window [lo, hi] that's actually mapped to colour right
// now, and the dark strips outside them show what's currently clamped flat
// to the end colours.
function buildLegendRow(id, d, colorMeta) {
  const unit = colorMeta.unit != null ? colorMeta.unit : (colorMeta.kind === "backscatter" ? "" : " m");
  // Windowable covers both ramp shapes: a relative stops list (every
  // survey/geophysics/backscatter layer) and the globe ramp's ocean/land
  // table (the GMRT basemap) -- see recolorDatasetForWindow.
  const windowable = !!(colorMeta.ramp && (colorMeta.ramp.stops || (colorMeta.ramp.ocean && colorMeta.ramp.land)));
  const { mode, win } = windowable ? getActiveColorWindow(d) : { mode: null, win: null };
  // Once windowable, the bar and its end labels always show the ramp's FULL
  // spectrum (win.fullLo/fullHi) -- the same bounds the handle track uses --
  // rather than colorMeta.range, which for the globe ramp is only this
  // dataset's own z_range_m, not the ramp's real (much wider) domain.
  const displayRange = windowable ? [win.fullLo, win.fullHi] : colorMeta.range;

  const row = document.createElement("div");
  row.className = "legend-row";

  const title = document.createElement("div");
  title.className = "legend-title";
  title.textContent = `${d.meta.label || d.manifestEntry.label} — ${colorMeta.kind}`;
  row.appendChild(title);

  if (colorMeta.kind !== "backscatter") {
    const palRow = document.createElement("div");
    palRow.className = "legend-palette-row";
    const sel = document.createElement("select");
    sel.className = "legend-palette";
    sel.title = "Colour map for this layer";
    const opts = [["builtin", "Colour map: as built"], ...Object.entries(EXTRA_PALETTES).map(([k, p]) => [k, p.label])];
    for (const [k, lab] of opts) {
      const o = document.createElement("option");
      o.value = k;
      o.textContent = lab;
      sel.appendChild(o);
    }
    sel.value = d.palette || "builtin";
    const revLab = document.createElement("label");
    revLab.className = "legend-palette-rev";
    const rev = document.createElement("input");
    rev.type = "checkbox";
    rev.checked = !!d.paletteReverse;
    rev.disabled = !d.palette;
    revLab.appendChild(rev);
    revLab.appendChild(document.createTextNode(" reverse"));
    sel.addEventListener("change", () => setDatasetPalette(id, sel.value, rev.checked));
    rev.addEventListener("change", () => setDatasetPalette(id, sel.value, rev.checked));
    palRow.appendChild(sel);
    palRow.appendChild(revLab);
    row.appendChild(palRow);
    if (colorMeta.palette && !isSlopePalette(d) && !d.meta.is_geophysics && !d.sections.value && d.meta.z_range_m[1] > 0) {
      const note = document.createElement("div");
      note.className = "legend-palette-note";
      note.innerHTML = `<span class="legend-land-swatch"></span> land (above sea level)`;
      row.appendChild(note);
    }
  }

  const track = document.createElement("div");
  track.className = "legend-track";
  const canvas = document.createElement("canvas");
  canvas.width = 232;
  canvas.height = 14;
  canvas.className = "legend-bar";
  drawLegendCanvas(canvas, windowable ? { ...colorMeta, range: displayRange } : colorMeta);
  track.appendChild(canvas);
  row.appendChild(track);

  const labels = document.createElement("div");
  labels.className = "legend-labels";
  labels.innerHTML = `<span>${displayRange[0].toFixed(1)}${unit}</span><span>${displayRange[1].toFixed(1)}${unit}</span>`;
  row.appendChild(labels);

  if (!windowable || !win) return row;

  // ---- draggable re-window handles ----
  const dimLeft = document.createElement("div");
  dimLeft.className = "legend-dim";
  const dimRight = document.createElement("div");
  dimRight.className = "legend-dim";
  const handleLo = document.createElement("div");
  handleLo.className = "legend-handle";
  handleLo.tabIndex = 0;
  const handleHi = document.createElement("div");
  handleHi.className = "legend-handle";
  handleHi.tabIndex = 0;
  track.appendChild(dimLeft);
  track.appendChild(dimRight);
  track.appendChild(handleLo);
  track.appendChild(handleHi);

  const windowLabels = document.createElement("div");
  windowLabels.className = "legend-window-labels";
  const loLabel = document.createElement("span");
  const hiLabel = document.createElement("span");
  windowLabels.appendChild(loLabel);
  windowLabels.appendChild(hiLabel);
  row.appendChild(windowLabels);

  const resetRow = document.createElement("div");
  resetRow.className = "legend-reset-row";
  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "legend-reset-btn";
  resetBtn.textContent = "Reset range";
  resetRow.appendChild(resetBtn);
  row.appendChild(resetRow);

  const fullSpan = win.fullHi - win.fullLo || 1e-9;
  const pct = (v) => `${(clamp01range((v - win.fullLo) / fullSpan, 0, 1) * 100).toFixed(3)}%`;

  function paint() {
    const loPct = clamp01range((win.lo - win.fullLo) / fullSpan, 0, 1);
    const hiPct = clamp01range((win.hi - win.fullLo) / fullSpan, 0, 1);
    handleLo.style.left = pct(win.lo);
    handleHi.style.left = pct(win.hi);
    dimLeft.style.left = "0%";
    dimLeft.style.width = `${(loPct * 100).toFixed(3)}%`;
    dimRight.style.left = `${(hiPct * 100).toFixed(3)}%`;
    dimRight.style.width = `${((1 - hiPct) * 100).toFixed(3)}%`;
    loLabel.textContent = `${win.lo.toFixed(1)}${unit}`;
    hiLabel.textContent = `${win.hi.toFixed(1)}${unit}`;
    const atDefault = Math.abs(win.lo - win.fullLo) < 1e-6 && Math.abs(win.hi - win.fullHi) < 1e-6;
    resetBtn.disabled = atDefault;
  }
  paint();

  const EPS = fullSpan * 0.001;
  function dragHandle(which) {
    return (e) => {
      e.preventDefault();
      const el = which === "lo" ? handleLo : handleHi;
      el.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const rect = track.getBoundingClientRect();
        const t = clamp01range((ev.clientX - rect.left) / rect.width, 0, 1);
        let v = win.fullLo + t * fullSpan;
        if (which === "lo") v = Math.min(v, win.hi - EPS);
        else v = Math.max(v, win.lo + EPS);
        win[which] = v;
        paint();
      };
      const up = () => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
        commitColorWindow(id, mode, win);
      };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    };
  }
  handleLo.addEventListener("pointerdown", dragHandle("lo"));
  handleHi.addEventListener("pointerdown", dragHandle("hi"));
  resetBtn.addEventListener("click", () => {
    win.lo = win.fullLo;
    win.hi = win.fullHi;
    paint();
    commitColorWindow(id, mode, win);
  });

  return row;
}

function updateLegend() {
  const container = document.getElementById("legendList");
  if (!container) return;
  container.innerHTML = "";
  let any = false;
  for (const id of state.order) {
    const d = state.datasets[id];
    if (!d.loaded || !d.visible || !d.meta) continue;
    const colorMeta = pickColorMeta(d);
    if (!colorMeta.ramp || !colorMeta.range) continue;
    any = true;
    container.appendChild(buildLegendRow(id, d, colorMeta));
  }
  if (!any) {
    container.innerHTML = '<div class="legend-empty">No datasets shown.</div>';
  }
}

// =====================================================================
// Track points (CSV upload, placed on the current seafloor surface)
// =====================================================================

// Colour: "done" is always the same flat grey (reads as complete/inactive
// at a glance, regardless of type). "to do" is coloured by SEQUENCE -- each
// type gets its own light-to-dark ramp, one hue family per type so dredge
// and MT never share a colour, and position within the ramp comes directly
// from the CSV's own "site" column (not row order or a recomputed index).
// Blue vs. orange is a safe pairing for colour-vision deficiency too, since
// the two also separate by lightness, not hue alone.
const TRACK_DONE_HEX = "#9aa7b2"; // grey -- completed, or status missing/unrecognised
const TRACK_ORDER_RAMPS = {
  dredge: { stops: ["#ffffff", "#00701a"] }, // white -> deep green: low site number -> high
  mt: { stops: ["#fff200", "#d0021b"] }, // yellow -> red: low site number -> high
};

function lerpHex(hexA, hexB, t) {
  const [ar, ag, ab] = hexToRgb(hexA);
  const [br, bg, bb] = hexToRgb(hexB);
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const b = Math.round(ab + (bb - ab) * t);
  const toHex = (n) => Math.max(0, Math.min(255, n)).toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

// Walks a >=2-stop ramp as a piecewise gradient (evenly spaced segments) --
// a 2-stop ramp is just the 1-segment case, so this covers both dredge and
// the 3-stop MT ramp with one code path.
function multiStopHex(stops, t) {
  if (stops.length === 1) return stops[0];
  const segments = stops.length - 1;
  const scaled = Math.min(segments - 1e-9, Math.max(0, t * segments));
  const i = Math.floor(scaled);
  return lerpHex(stops[i], stops[i + 1], scaled - i);
}

// pt carries siteNum plus that CSV's own minOrder/maxOrder (see parseTrackCSV) --
// denormalised onto every point at parse time so this function needs nothing
// beyond the point itself.
function trackPointColor(pt) {
  const status = pt.status ? pt.status.trim().toLowerCase() : "";
  if (status !== "to do") return TRACK_DONE_HEX; // "done", blank, or unrecognised
  const ramp = TRACK_ORDER_RAMPS[pt.kind] || TRACK_ORDER_RAMPS.dredge;
  if (!Number.isFinite(pt.siteNum) || pt.minOrder == null || pt.maxOrder == null || pt.maxOrder === pt.minOrder) {
    return ramp.stops[ramp.stops.length - 1]; // no usable "site" number to place this on the ramp -- just use the family's darkest colour
  }
  const t = Math.min(1, Math.max(0, (pt.siteNum - pt.minOrder) / (pt.maxOrder - pt.minOrder)));
  return multiStopHex(ramp.stops, t);
}

// Marker SHAPE is decided by the uploaded file's own name, not a CSV column
// -- site lists are one file per site type (MTsites.csv, DredgeSites.csv,
// as they're actually named in MT_dredging_coords/ on the shared drive), so
// the filename is the reliable signal. "dredge" is checked first since it's
// an unambiguous word; "mt" is matched as a whole word/token (not just any
// filename containing the letters "mt") to avoid false positives.
function detectTrackKind(filename) {
  const name = filename.toLowerCase();
  if (name.includes("dredge")) return "dredge";
  // Plain substring, not a word-boundary match -- real file names here are
  // "MTsites.csv" / "MT_....csv", i.e. "mt" immediately followed by more
  // letters, which a stricter \bmt\b-style check would (and did) miss.
  if (name.includes("mt")) return "mt";
  return "dredge"; // unrecognised filename -- default to the plain circle marker
}

// Both marker shapes are hand-drawn canvas icons (not a Cesium
// PointPrimitive's built-in circle) so "dredge"/"mt" can each get a genuine
// shape AND the same halo treatment: a white ring behind a colour-filled,
// dark-rimmed shape. The white ring is what keeps a point visible however
// the basemap underneath is coloured -- a flat colour + dark outline alone
// (the previous approach) washed out against ocean blues close to the
// marker's own hue. Drawn once per shape+colour combination and cached.
const trackIconCache = new Map();
function getTrackIcon(shape, hex) {
  const key = `${shape}:${hex}`;
  if (trackIconCache.has(key)) return trackIconCache.get(key);
  const size = 20;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const mid = size / 2;
  const rOuter = size / 2 - 1; // white halo
  const rInner = rOuter - 3; // coloured fill, inset inside the halo

  const pathFor = (r) => {
    ctx.beginPath();
    if (shape === "diamond") {
      ctx.moveTo(mid, mid - r);
      ctx.lineTo(mid + r, mid);
      ctx.lineTo(mid, mid + r);
      ctx.lineTo(mid - r, mid);
      ctx.closePath();
    } else {
      ctx.arc(mid, mid, r, 0, Math.PI * 2);
    }
  };

  pathFor(rOuter);
  ctx.fillStyle = "#ffffff";
  ctx.fill();

  pathFor(rInner);
  ctx.fillStyle = hex;
  ctx.fill();
  ctx.lineWidth = 1.2;
  ctx.strokeStyle = "#0b0b0b";
  ctx.stroke();

  trackIconCache.set(key, canvas);
  return canvas;
}

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function rebuildVisibleTrackPoints() {
  const pts = [];
  for (const layer of state.trackLayers.values()) {
    if (!layer.visible) continue;
    pts.push(...layer.points);
  }
  state.trackPoints = pts;
}

// One row per loaded CSV, same look as the dataset checklist (.ds-row/.ds-label/.ds-meta)
// -- a checkbox toggles that file's points on/off without re-uploading it.
function buildTrackLayerRows() {
  const container = document.getElementById("trackLayerList");
  if (!container) return;
  container.innerHTML = "";
  for (const layer of state.trackLayers.values()) {
    const row = document.createElement("div");
    row.className = "ds-row";
    const kindLabel = layer.kind === "mt" ? "MT \u2014 diamond" : "Dredge \u2014 circle";
    row.innerHTML = `
      <label>
        <input type="checkbox" ${layer.visible ? "checked" : ""}>
        <span class="ds-label">${layer.fileName}</span>
      </label>
      <div class="ds-meta">${layer.points.length} point(s) \u2014 ${kindLabel}</div>
    `;
    row.querySelector("input").addEventListener("change", (e) => {
      layer.visible = e.target.checked;
      rebuildVisibleTrackPoints();
      placeTrackPoints();
    });
    container.appendChild(row);
  }
}

// Loaded (not necessarily visible) min/max "site" number for one kind,
// across every currently loaded file of that kind -- drives the gradient
// legend's range label, e.g. "MT (site 1\u21925)".
function getTrackKindOrderRange(kind) {
  let min = null;
  let max = null;
  for (const layer of state.trackLayers.values()) {
    if (layer.kind !== kind) continue;
    for (const p of layer.points) {
      if (!Number.isFinite(p.siteNum)) continue;
      if (min === null || p.siteNum < min) min = p.siteNum;
      if (max === null || p.siteNum > max) max = p.siteNum;
    }
  }
  return { min, max };
}

function buildTrackLegend() {
  const container = document.getElementById("trackLegend");
  if (!container) return;
  container.innerHTML = "";

  const todoHeading = document.createElement("div");
  todoHeading.className = "track-legend-heading";
  todoHeading.textContent = "To do (colour = site order)";
  container.appendChild(todoHeading);
  const gradientRows = [
    { kind: "dredge", gradientClass: "gradient-dredge", label: "Dredge" },
    { kind: "mt", gradientClass: "gradient-mt", label: "MT" },
  ];
  for (const { kind, gradientClass, label } of gradientRows) {
    const { min, max } = getTrackKindOrderRange(kind);
    const row = document.createElement("div");
    row.className = "track-legend-row";
    const swatch = document.createElement("span");
    swatch.className = `track-legend-swatch track-legend-swatch--${gradientClass}`;
    const labelEl = document.createElement("span");
    labelEl.textContent = min !== null && max !== null ? `${label} (site ${min}\u2192${max})` : `${label} (none loaded)`;
    row.appendChild(swatch);
    row.appendChild(labelEl);
    container.appendChild(row);
  }

  const doneHeading = document.createElement("div");
  doneHeading.className = "track-legend-heading";
  doneHeading.textContent = "Done";
  container.appendChild(doneHeading);
  const doneRow = document.createElement("div");
  doneRow.className = "track-legend-row";
  const doneSwatch = document.createElement("span");
  doneSwatch.className = "track-legend-swatch";
  doneSwatch.style.background = TRACK_DONE_HEX;
  const doneLabel = document.createElement("span");
  doneLabel.textContent = "Done (any type)";
  doneRow.appendChild(doneSwatch);
  doneRow.appendChild(doneLabel);
  container.appendChild(doneRow);

  const typeHeading = document.createElement("div");
  typeHeading.className = "track-legend-heading";
  typeHeading.textContent = "Type (from file name)";
  container.appendChild(typeHeading);
  const shapeRows = [
    { shapeClass: "circle", label: "Dredge \u2014 circle" },
    { shapeClass: "diamond", label: "MT \u2014 diamond" },
  ];
  for (const { shapeClass, label } of shapeRows) {
    const row = document.createElement("div");
    row.className = "track-legend-row";
    const swatch = document.createElement("span");
    swatch.className = `track-legend-swatch track-legend-swatch--${shapeClass}`;
    const labelEl = document.createElement("span");
    labelEl.textContent = label;
    row.appendChild(swatch);
    row.appendChild(labelEl);
    container.appendChild(row);
  }
}

// "-0.7029", "0.7029 S", "0 42.17 S", "0°42.172' S", "0°42'10.3\"S" -> signed decimal degrees, or NaN.
// Hemisphere letters must match the axis (N/S for latitude, E/W for longitude).
function parseCoord(raw, isLat) {
  if (raw == null) return NaN;
  let t = String(raw).trim().toUpperCase();
  if (!t) return NaN;
  let sign = 1;
  const hm = t.match(/[NSEW]/g);
  if (hm) {
    if (hm.length > 1) return NaN;
    const hh = hm[0];
    if (isLat ? !"NS".includes(hh) : !"EW".includes(hh)) return NaN;
    if (hh === "S" || hh === "W") sign = -1;
    t = t.replace(/[NSEW]/, " ");
  }
  const nums = t.replace(/[°º'’"″′:]/g, " ").trim().split(/\s+/).filter(Boolean);
  if (!nums.length || nums.length > 3 || !nums.every((x) => /^[-+]?\d+(\.\d+)?$/.test(x))) return NaN;
  const [d, m = "0", sec = "0"] = nums;
  if (nums.length > 1 && (+m >= 60 || +sec >= 60 || +m < 0 || +sec < 0)) return NaN;
  let v = Math.abs(+d) + (+m) / 60 + (+sec) / 3600;
  if (String(d).trim().startsWith("-")) sign = -sign;
  v *= sign;
  return Math.abs(v) <= (isLat ? 90 : 180) ? v : NaN;
}

function parseTrackCSV(text, kind) {
  const lines = text
    .split(/\r\n|\n|\r/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
  if (lines.length === 0) return { points: [], warning: "Empty file." };

  const splitLine = (l) => l.split(",").map((c) => c.trim().replace(/^"|"$/g, ""));
  const header = splitLine(lines[0]).map((h) => h.toLowerCase());
  let latIdx = header.findIndex((h) => h.includes("lat"));
  let lonIdx = header.findIndex((h) => h.includes("lon"));
  let labelIdx = header.findIndex((h) => ["name", "label", "id", "station", "site"].some((k) => h.includes(k)));
  let statusIdx = header.findIndex((h) => ["status", "task", "activity"].some((k) => h.includes(k)));
  // Colour-by-order needs the CSV's own "site" number specifically, not
  // just whatever ends up as the display label -- kept separate from
  // labelIdx above even though "site" also satisfies that search.
  let orderIdx = header.findIndex((h) => h.includes("site"));
  let startRow = 1;
  let warning = null;
  if (latIdx === -1 || lonIdx === -1) {
    latIdx = 0;
    lonIdx = 1;
    labelIdx = -1;
    statusIdx = -1;
    orderIdx = -1;
    startRow = 0;
    warning = 'No lat/lon header recognised -- assumed column 1 = latitude, column 2 = longitude.';
  }

  const points = [];
  let skipped = 0;
  const unrecognisedStatuses = new Set();
  const RECOGNISED_STATUSES = new Set(["to do", "done"]);
  for (let i = startRow; i < lines.length; i++) {
    const cols = splitLine(lines[i]);
    const lat = parseCoord(cols[latIdx], true);
    const lon = parseCoord(cols[lonIdx], false);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) { skipped += 1; continue; }
    const label = labelIdx >= 0 && cols[labelIdx] ? cols[labelIdx] : `pt${points.length + 1}`;
    const siteNum = orderIdx >= 0 ? parseFloat(cols[orderIdx]) : NaN;
    const status = statusIdx >= 0 && cols[statusIdx] ? cols[statusIdx] : null;
    if (status && !RECOGNISED_STATUSES.has(status.trim().toLowerCase())) unrecognisedStatuses.add(status);
    points.push({ latDeg: lat, lonDeg: lon, label, status, kind, siteNum });
  }
  // Denormalise this file's own min/max "site" number onto every point so
  // trackPointColor() and the legend can place things on the ramp without
  // re-scanning the file -- normalised per file, not across every CSV loaded.
  const siteNums = points.map((p) => p.siteNum).filter(Number.isFinite);
  const minOrder = siteNums.length > 0 ? Math.min(...siteNums) : null;
  const maxOrder = siteNums.length > 0 ? Math.max(...siteNums) : null;
  for (const p of points) {
    p.minOrder = minOrder;
    p.maxOrder = maxOrder;
  }
  if (skipped > 0) {
    const extra = `${skipped} row(s) skipped: latitude/longitude not readable (use decimal degrees, or deg min with N/S/E/W).`;
    warning = warning ? `${warning} ${extra}` : extra;
  }
  if (unrecognisedStatuses.size > 0) {
    const extra = `Status value(s) not recognised (shown in grey, same as "done"): ${[...unrecognisedStatuses].slice(0, 4).join(", ")}${unrecognisedStatuses.size > 4 ? ", …" : ""}. Expected "to do" or "done".`;
    warning = warning ? `${warning} ${extra}` : extra;
  }
  return { points, warning };
}

function placeTrackPoints() {
  if (!state.trackBillboardCollection) {
    state.trackBillboardCollection = viewer.scene.primitives.add(new Cesium.BillboardCollection());
  }
  state.trackBillboardCollection.removeAll();
  const statusEl = document.getElementById("trackStatus");
  if (state.trackPoints.length === 0) {
    if (statusEl) statusEl.textContent = "";
    viewer.scene.requestRender();
    return;
  }
  if (viewer.scene.sampleHeightSupported === false) {
    if (statusEl) statusEl.textContent = "This browser/GPU doesn't support surface height sampling here -- can't place points.";
    return;
  }

  let placed = 0;
  const missing = [];
  for (const pt of state.trackPoints) {
    const carto = Cesium.Cartographic.fromDegrees(pt.lonDeg, pt.latDeg, 0.0);
    let height;
    try {
      height = viewer.scene.sampleHeight(carto);
    } catch (e) {
      height = undefined;
    }
    if (height === undefined || !Number.isFinite(height)) {
      pt.cartesian = null;
      missing.push(pt.label);
      continue;
    }
    carto.height = height;
    pt.cartesian = Cesium.Cartographic.toCartesian(carto);
    const hex = trackPointColor(pt);
    const shape = pt.kind === "mt" ? "diamond" : "circle";
    state.trackBillboardCollection.add({
      position: pt.cartesian,
      image: getTrackIcon(shape, hex),
      width: 16,
      height: 16,
      id: pt,
      disableDepthTestDistance: Number.POSITIVE_INFINITY, // never half-hidden by the seafloor it sits on
    });
    placed += 1;
  }
  viewer.scene.requestRender();

  if (statusEl) {
    let text = `${placed} / ${state.trackPoints.length} point(s) placed on the surface.`;
    if (missing.length > 0) {
      text += ` No coverage yet (check the dataset underneath) for: ${missing.slice(0, 6).join(", ")}${missing.length > 6 ? ", …" : ""}`;
    }
    statusEl.textContent = text;
  }
}

// pt objects (see parseTrackCSV) are attached as the `id` of each billboard
// / point primitive in placeTrackPoints() above, so scene.pick() hands them
// straight back.
function trackTooltipLabel(pt) {
  const num = Number.isFinite(pt.siteNum) ? pt.siteNum : pt.label;
  return pt.kind === "mt" ? `MT acquisition point ${num}` : `Dredge point ${num}`;
}
function showTrackTooltip(pt, windowPosition) {
  const el = document.getElementById("trackTooltip");
  if (!el) return;
  el.textContent = pt.prevDredge ? prevTooltipText(pt) : trackTooltipLabel(pt);
  el.style.left = `${windowPosition.x + 14}px`;
  el.style.top = `${windowPosition.y + 14}px`;
  el.style.display = "block";
}
function hideTrackTooltip() {
  const el = document.getElementById("trackTooltip");
  if (el) el.style.display = "none";
}
function pickTrackPoint(windowPosition) {
  const picked = viewer.scene.pick(windowPosition);
  if (Cesium.defined(picked) && picked.id && typeof picked.id === "object" &&
      (("kind" in picked.id && "latDeg" in picked.id) || picked.id.prevDredge)) {
    return picked.id;
  }
  return null;
}
const trackPickHandler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
trackPickHandler.setInputAction((movement) => {
  const pt = pickTrackPoint(movement.endPosition);
  if (pt) showTrackTooltip(pt, movement.endPosition);
  else hideTrackTooltip();
}, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
trackPickHandler.setInputAction((movement) => {
  const pt = pickTrackPoint(movement.position);
  if (pt) showTrackTooltip(pt, movement.position);
}, Cesium.ScreenSpaceEventType.LEFT_CLICK);

document.getElementById("trackCsvInput").addEventListener("change", async (e) => {
  const files = Array.from(e.target.files || []);
  if (files.length === 0) return;
  const named = [];
  for (const file of files) named.push([file.name, await file.text()]);
  e.target.value = ""; // allow re-selecting the same file name later to reload it
  addTrackCsvs(named);
});

// [[fileName, csvText], ...] -> track layers (shared by the file picker and the
// "Load AT53-04 sites" button).
function addTrackCsvs(named) {
  const statusEl = document.getElementById("trackStatus");
  const perFileNotes = [];
  for (const [name, text] of named) {
    const kind = detectTrackKind(name);
    const { points, warning } = parseTrackCSV(text, kind);
    // Keyed by file name: loading a new file name ADDS a new toggleable
    // entry alongside whatever's already loaded (e.g. MT then Dredge later
    // still shows both); re-loading the same file name replaces just that
    // one entry rather than duplicating it.
    state.trackLayers.set(name, { fileName: name, kind, points, visible: true });
    perFileNotes.push(warning ? `${name}: ${warning}` : `${name}: ${points.length} point(s) (${kind}).`);
  }
  buildTrackLayerRows();
  buildTrackLegend();
  rebuildVisibleTrackPoints();
  if (statusEl) statusEl.textContent = perFileNotes.join(" ");
  placeTrackPoints();
}

// The planned AT53-04 site files (MT_dredging_coords/), served by run_viewer.py at /sites/.
document.getElementById("loadPlanSitesBtn").addEventListener("click", async () => {
  const statusEl = document.getElementById("trackStatus");
  try {
    const named = [];
    for (const name of ["DredgeSites.csv", "MTsites.csv"]) {
      const r = await fetch(`sites/${name}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
      named.push([name, await r.text()]);
    }
    addTrackCsvs(named);
  } catch (err) {
    statusEl.textContent = `Could not load the site files (${err.message}). Start the viewer with run_viewer.py, or pick the CSVs by hand.`;
  }
});

document.getElementById("clearTrackBtn").addEventListener("click", () => {
  state.trackLayers.clear();
  state.trackPoints = [];
  if (state.trackBillboardCollection) state.trackBillboardCollection.removeAll();
  document.getElementById("trackStatus").textContent = "";
  document.getElementById("trackCsvInput").value = "";
  buildTrackLayerRows();
  buildTrackLegend();
  hideTrackTooltip();
  viewer.scene.requestRender();
});

// =====================================================================
// Cross-section tool -- click two points on the rendered surface, keep both
// markers on screen connected by a line draped along the sampled profile
// between them, and plot true depth vs. along-track distance. Reuses the
// same viewer.scene.sampleHeight() technique as track points (see above),
// so the profile follows whatever's actually on top on screen right now
// (a detailed survey wins over the basemap wherever both are checked),
// and (like the "click the surface" depth readout) divides the current
// vertical exaggeration back out for the plotted/reported depth values --
// the *3D line* stays at the exaggerated height so it visually hugs the
// mesh, but every number shown (chart axis, summary, status text) is true
// depth.
// =====================================================================

// Profile resolution along the geodesic. Each sample is a synchronous
// viewer.scene.sampleHeight() call (the same primitive-depth-probe technique
// placeTrackPoints() uses) -- on real GPU hardware this is fast enough for a
// button click to feel responsive, but it is NOT cheap (an offscreen render
// per sample), so this stays a moderate count rather than one-per-pixel; the
// "Computing..." loading overlay (see computeAndRenderCrossSection's caller)
// covers the pause on slower machines.
const CROSS_SECTION_SAMPLES = 60;
const CROSS_SECTION_COLOR = "#ffd400"; // bright yellow -- distinct from every dataset colour ramp, the legend hues, and the track-point status palette

function ensureCrossSectionCollections() {
  const cs = state.crossSection;
  if (!cs.pointCollection) cs.pointCollection = viewer.scene.primitives.add(new Cesium.PointPrimitiveCollection());
  if (!cs.lineCollection) cs.lineCollection = viewer.scene.primitives.add(new Cesium.PolylineCollection());
  if (!cs.labelCollection) cs.labelCollection = viewer.scene.primitives.add(new Cesium.LabelCollection());
}

function setCrossSectionStatus(text) {
  const el = document.getElementById("crossSectionStatus");
  if (el) el.textContent = text;
}

function armCrossSectionPicking() {
  clearCrossSection(); // re-arming always starts a fresh pair, even mid-pick
  ensureCrossSectionCollections();
  state.crossSection.armed = true;
  if (typeof dl !== "undefined" && dl.armed) { dl.armed = false; dl.first = null; setDlStatus("Line drawing cancelled (cross-section picking started)."); }
  setCrossSectionStatus("Click a first point on the surface for the cross-section.");
}

function clearCrossSection() {
  const cs = state.crossSection;
  cs.armed = false;
  cs.pointA = null;
  cs.pointB = null;
  cs.profile = null;
  cs.layerProfiles = [];
  cs.layerCanvases = [];
  if (cs.pointCollection) cs.pointCollection.removeAll();
  if (cs.lineCollection) cs.lineCollection.removeAll();
  if (cs.labelCollection) cs.labelCollection.removeAll();
  setCrossSectionStatus("");
  const plotsEl = document.getElementById("crossSectionPlots");
  if (plotsEl) plotsEl.innerHTML = "";
  setCrossSectionDownloadsEnabled(false);
  setCrossSectionDockVisible(false);
  viewer.scene.requestRender();
}

// Shows/hides the full-width bottom dock the profiles render into (see
// index.html) -- opened once a cross-section is actually computed, closed by
// Clear or the dock's own close button (both route through clearCrossSection).
function setCrossSectionDockVisible(visible) {
  const dock = document.getElementById("crossSectionDock");
  if (dock) dock.classList.toggle("hidden", !visible);
}

function setCrossSectionDownloadsEnabled(enabled) {
  const csvBtn = document.getElementById("crossSectionDownloadCsvBtn");
  const pngBtn = document.getElementById("crossSectionDownloadPngBtn");
  if (csvBtn) csvBtn.disabled = !enabled;
  if (pngBtn) pngBtn.disabled = !enabled;
}

// Shared with the GeoTIFF exporter's own download trigger -- create an
// object URL, click a throwaway <a download>, then revoke it once the
// browser's had time to start the save.
function triggerBrowserDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function csvSafe(s) {
  return String(s).replace(/[,\n"]/g, " ").trim();
}

// distance_km,lon_deg,lat_deg,<layer 1>,<layer 2>,... -- one row per sample
// point, including that sample's own lon/lat along the A-B geodesic (not
// just the two endpoints, which are also recorded separately in the header
// comments for convenience). One column per dataset that was checked at
// compute time, each sampled independently via its own reconstructed grid
// (see computeLayerProfiles) -- NOT the single topmost-surface reading the
// old single-plot version used, so e.g. a bathymetry column and a
// magnetic-anomaly column can both be populated for the same row. A gap
// (that layer has no coverage at that sample) leaves the cell blank rather
// than 0 or an interpolated guess, so it's visually obvious in a
// spreadsheet/plot which stretches had no coverage -- lon_deg/lat_deg are
// still filled in for a gap row, since the position along the line is known
// even when a given layer's value there isn't.
function downloadCrossSectionCSV() {
  const cs = state.crossSection;
  const p = cs.profile;
  const layers = cs.layerProfiles || [];
  if (!p || !cs.pointA || !cs.pointB || layers.length === 0) return;
  const header = [
    "distance_km",
    "lon_deg",
    "lat_deg",
    ...layers.map((lp) => csvSafe(`${lp.label} (${lp.units || lp.kind})`)),
  ];
  const lines = [
    "# Viewer3D cross-section profile -- one column per layer that was checked when this was computed",
    `# Point A: lon ${cs.pointA.lonDeg.toFixed(6)}, lat ${cs.pointA.latDeg.toFixed(6)}`,
    `# Point B: lon ${cs.pointB.lonDeg.toFixed(6)}, lat ${cs.pointB.latDeg.toFixed(6)}`,
    `# Total distance: ${p.totalKm.toFixed(3)} km`,
    "# lon_deg/lat_deg are each sample's own position along the A-B line; each layer column is that dataset's own value there (its native units, not exaggerated); blank = no coverage at that sample",
    header.join(","),
  ];
  for (let i = 0; i < p.distKm.length; i++) {
    const row = [p.distKm[i].toFixed(4), p.lonDeg[i].toFixed(6), p.latDeg[i].toFixed(6)];
    for (const lp of layers) {
      const v = lp.values[i];
      row.push(v == null ? "" : v.toFixed(4));
    }
    lines.push(row.join(","));
  }
  const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv" });
  triggerBrowserDownload(blob, `viewer3d_cross_section_${Date.now()}.csv`);
}

// Stacks every currently-rendered per-layer canvas into one tall PNG (in
// visible order, top to bottom) so "one button" still gives one file even
// though the panel can now show several plots at once.
function downloadCrossSectionPNG() {
  const cs = state.crossSection;
  const canvases = (cs.layerCanvases || []).map((lc) => lc.canvas);
  if (!cs.profile || canvases.length === 0) return;
  const gap = 6;
  const w = Math.max(...canvases.map((c) => c.width));
  const h = canvases.reduce((sum, c) => sum + c.height, 0) + gap * Math.max(0, canvases.length - 1);
  const composite = document.createElement("canvas");
  composite.width = w;
  composite.height = h;
  const ctx = composite.getContext("2d");
  ctx.fillStyle = "#10161f";
  ctx.fillRect(0, 0, w, h);
  let y = 0;
  for (const c of canvases) {
    ctx.drawImage(c, 0, y);
    y += c.height + gap;
  }
  composite.toBlob((blob) => {
    if (blob) triggerBrowserDownload(blob, `viewer3d_cross_section_${Date.now()}.png`);
  }, "image/png");
}

function addCrossSectionMarker(point, label) {
  const cs = state.crossSection;
  cs.pointCollection.add({
    position: point.cartesian,
    color: Cesium.Color.fromCssColorString(CROSS_SECTION_COLOR),
    outlineColor: Cesium.Color.fromCssColorString("#0b0b0b"),
    outlineWidth: 2,
    pixelSize: 11,
  });
  cs.labelCollection.add({
    position: point.cartesian,
    text: label,
    font: "bold 13px -apple-system, sans-serif",
    fillColor: Cesium.Color.fromCssColorString("#0b0b0b"),
    outlineColor: Cesium.Color.WHITE,
    outlineWidth: 3,
    style: Cesium.LabelStyle.FILL_AND_OUTLINE,
    verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
    pixelOffset: new Cesium.Cartesian2(0, -13),
  });
}

// Re-sample A/B onto whatever's now on top (dataset toggled, exaggeration
// changed) -- same idea as track points re-snapping. Point order in
// pointCollection/labelCollection always matches [A, B] since both are only
// ever rebuilt together via clearCrossSection()+addCrossSectionMarker().
function repositionCrossSectionMarkers() {
  const cs = state.crossSection;
  if (!cs.pointCollection) return;
  [cs.pointA, cs.pointB].forEach((pt, i) => {
    if (!pt) return;
    const carto = Cesium.Cartographic.fromDegrees(pt.lonDeg, pt.latDeg, 0.0);
    let height;
    try {
      height = viewer.scene.sampleHeight(carto);
    } catch (e) {
      height = undefined;
    }
    if (height === undefined || !Number.isFinite(height)) return;
    carto.height = height;
    pt.cartesian = Cesium.Cartographic.toCartesian(carto);
    if (cs.pointCollection.length > i) cs.pointCollection.get(i).position = pt.cartesian;
    if (cs.labelCollection && cs.labelCollection.length > i) cs.labelCollection.get(i).position = pt.cartesian;
  });
}

// Sample true depth (and a draped, exaggerated-height position for the 3D
// line) at CROSS_SECTION_SAMPLES+1 evenly-spaced points along the geodesic
// between A and B. A sample with nothing checked underneath it comes back
// as null in depthM/drapedCartesians -- the chart and 3D line both skip
// across those gaps rather than interpolating through them.
function sampleCrossSectionProfile(pointA, pointB) {
  const geodesic = new Cesium.EllipsoidGeodesic(
    Cesium.Cartographic.fromDegrees(pointA.lonDeg, pointA.latDeg),
    Cesium.Cartographic.fromDegrees(pointB.lonDeg, pointB.latDeg)
  );
  const totalM = geodesic.surfaceDistance;
  const n = CROSS_SECTION_SAMPLES;
  const distKm = [];
  const lonDeg = [];
  const latDeg = [];
  const depthM = [];
  const drapedCartesians = [];
  for (let i = 0; i <= n; i++) {
    const d = (totalM * i) / n;
    const carto = i === 0 ? Cesium.Cartographic.fromDegrees(pointA.lonDeg, pointA.latDeg)
      : i === n ? Cesium.Cartographic.fromDegrees(pointB.lonDeg, pointB.latDeg)
      : geodesic.interpolateUsingSurfaceDistance(d);
    // The sample's own lon/lat along the geodesic -- recorded regardless of
    // whether sampleHeight below succeeds, since the position along A-B is
    // defined either way (only the depth is unknown at a coverage gap).
    lonDeg.push(Cesium.Math.toDegrees(carto.longitude));
    latDeg.push(Cesium.Math.toDegrees(carto.latitude));
    let height;
    try {
      height = viewer.scene.sampleHeight(carto);
    } catch (e) {
      height = undefined;
    }
    distKm.push(d / 1000);
    if (height === undefined || !Number.isFinite(height)) {
      depthM.push(null);
      drapedCartesians.push(null);
    } else {
      depthM.push(height / state.exaggeration);
      const c = Cesium.Cartographic.clone(carto);
      c.height = height;
      drapedCartesians.push(Cesium.Cartographic.toCartesian(c));
    }
  }
  return { distKm, lonDeg, latDeg, depthM, drapedCartesians, totalKm: totalM / 1000 };
}

// Draws ONE layer's profile into its own canvas in the bottom dock:
// geometryProfile is the shared A-B sampling (distance/lon/lat, same for
// every layer); layerProfile is that one dataset's own values at those same
// sample indices (see computeLayerProfiles). Kept deliberately
// self-contained (title + endpoint coords baked into the picture) so each
// canvas -- and the stacked composite downloadCrossSectionPNG() builds from
// them -- reads on its own without the on-page status text alongside it.
//
// Spectrum fill: the area under the curve is painted one
// vertical band per sample, each band's colour drawn straight from that
// dataset's OWN colour ramp (meta.colormap_stops) at that sample's value,
// using the same "depth" re-window (see getDepthColorWindow) that's
// currently punched into its legend colourbar -- so narrowing the handles
// there visibly re-saturates the bands here too. A layer with no relative
// ramp (only the GMRT basemap, which isn't geophysics/backscatter data
// anyway) falls back to a flat line, same as the original single-colour plot.
// "Nice" round tick values (1/2/5 x 10^n step) spanning [min, max], the same
// approach d3/matplotlib use for readable axes -- picks a step so there are
// roughly `targetCount` ticks landing on round numbers, not one per sample.
function niceTicks(min, max, targetCount) {
  if (!(max > min)) return { ticks: [min], step: 1 };
  const range = max - min;
  const rough = range / Math.max(1, targetCount);
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const ticks = [];
  const start = Math.ceil(min / step) * step;
  for (let v = start; v <= max + step * 1e-6; v += step) {
    ticks.push(Math.round(v / step) * step); // snap off float drift
  }
  return { ticks, step };
}

function formatTick(v, step) {
  const decimals = step >= 1 ? 0 : Math.max(0, Math.ceil(-Math.log10(step)));
  return v.toFixed(decimals);
}

function drawLayerCrossSectionCanvas(canvas, geometryProfile, layerProfile) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#10161f";
  ctx.fillRect(0, 0, w, h);

  ctx.fillStyle = "#e8ecf1";
  ctx.font = "bold 12px -apple-system, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(layerProfile.label, 8, 16);

  const validVals = layerProfile.values.filter((v) => v != null);
  if (validVals.length < 2) {
    ctx.fillStyle = "#7f8b97";
    ctx.font = "11px -apple-system, sans-serif";
    ctx.fillText("No coverage along this line.", 8, h / 2 + 8);
    return;
  }

  const padL = 54, padR = 12, padT = 30, padB = 30;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const vMin = Math.min(...validVals);
  const vMax = Math.max(...validVals);
  const vRange = Math.max(1e-6, vMax - vMin);
  const totalM = Math.max(1e-9, geometryProfile.totalKm * 1000); // distance axis in metres, not km

  const xAt = (km) => padL + ((km * 1000) / totalM) * plotW;
  const yAt = (v) => padT + (1 - (v - vMin) / vRange) * plotH;
  const baseY = padT + plotH;

  // Coordinate header -- lon/lat of the two picked endpoints.
  const cs = state.crossSection;
  if (cs.pointA && cs.pointB) {
    ctx.fillStyle = "#b9c2cc";
    ctx.font = "10px -apple-system, sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(`A ${cs.pointA.lonDeg.toFixed(2)},${cs.pointA.latDeg.toFixed(2)} -> B ${cs.pointB.lonDeg.toFixed(2)},${cs.pointB.latDeg.toFixed(2)}`, w - padR, 16);
    ctx.textAlign = "left";
  }

  // ---- axes: dashed gridlines + tick labels, distance in metres (x) and
  // value in the layer's own units (y) -- drawn before the fill/curve so
  // they read as a backdrop, matching a dedicated profile-view layering. ----
  ctx.save();
  ctx.setLineDash([3, 3]);
  ctx.strokeStyle = "rgba(255,255,255,0.14)";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#9aa7b2";
  ctx.font = "9px -apple-system, sans-serif";

  const { ticks: yTicks, step: yStep } = niceTicks(vMin, vMax, 5);
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (const v of yTicks) {
    const y = Math.round(yAt(v)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(w - padR, y);
    ctx.stroke();
    ctx.fillText(formatTick(v, yStep), padL - 5, y);
  }

  const { ticks: xTicks, step: xStep } = niceTicks(0, totalM, 5);
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (const m of xTicks) {
    const x = Math.round(padL + (m / totalM) * plotW) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, padT);
    ctx.lineTo(x, baseY);
    ctx.stroke();
    ctx.fillText(formatTick(m, xStep), x, baseY + 4);
  }
  ctx.restore();
  ctx.textBaseline = "alphabetic";

  // plot border, solid, on top of the dashed grid
  ctx.strokeStyle = "rgba(255,255,255,0.3)";
  ctx.lineWidth = 1;
  ctx.strokeRect(padL + 0.5, padT + 0.5, plotW - 1, plotH - 1);

  // ---- spectrum-coloured fill, one vertical band per sample ----
  const d = state.datasets[layerProfile.id];
  const colorMeta = d ? pickColorMetaFor(d, "depth") : null;
  const canSpectrum = !!(colorMeta && colorMeta.ramp && colorMeta.ramp.stops) && !(d && isSlopePalette(d));
  const win = canSpectrum ? getDepthColorWindow(d) : null;
  const winSpan = canSpectrum ? win.hi - win.lo || 1e-9 : 1;

  if (canSpectrum) {
    const n = geometryProfile.distKm.length;
    for (let i = 0; i < n; i++) {
      const v = layerProfile.values[i];
      if (v == null) continue;
      const xC = xAt(geometryProfile.distKm[i]);
      const xPrev = i > 0 && layerProfile.values[i - 1] != null ? xAt(geometryProfile.distKm[i - 1]) : xC;
      const xNext = i < n - 1 && layerProfile.values[i + 1] != null ? xAt(geometryProfile.distKm[i + 1]) : xC;
      const x0 = (xPrev + xC) / 2;
      const x1 = (xC + xNext) / 2;
      const t = (v - win.lo) / winSpan;
      const rgb = sampleRelativeColorJS(t, colorMeta.ramp.stops);
      ctx.fillStyle = `rgb(${rgb[0] | 0},${rgb[1] | 0},${rgb[2] | 0})`;
      const y = yAt(v);
      ctx.fillRect(x0, y, Math.max(1, x1 - x0), baseY - y);
    }
  }

  // zero gridline, only if the profile actually straddles it (depth/anomaly
  // fields alike -- meaningful for both)
  if (vMin < 0 && vMax > 0) {
    ctx.strokeStyle = "rgba(255,255,255,0.25)";
    ctx.lineWidth = 1;
    const y0 = Math.round(yAt(0)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(padL, y0);
    ctx.lineTo(w - padR, y0);
    ctx.stroke();
  }

  // Outline the curve on top of the fill (or, if this layer has no ramp to
  // colour by, this is the whole profile -- same flat look as before).
  ctx.strokeStyle = canSpectrum ? "rgba(255,255,255,0.85)" : CROSS_SECTION_COLOR;
  ctx.lineWidth = canSpectrum ? 1.2 : 1.8;
  ctx.beginPath();
  let drawing = false;
  for (let i = 0; i < geometryProfile.distKm.length; i++) {
    const v = layerProfile.values[i];
    if (v == null) {
      drawing = false;
      continue;
    }
    const x = xAt(geometryProfile.distKm[i]);
    const y = yAt(v);
    if (!drawing) {
      ctx.moveTo(x, y);
      drawing = true;
    } else {
      ctx.lineTo(x, y);
    }
  }
  ctx.stroke();

  // A/B endpoint markers -- thin dashed guide lines in the same yellow as
  // the picked points in the 3D view, with a bold white-on-black label at
  // the top of each, so it's obvious at a glance which end of the plot is
  // which without cross-referencing the lon/lat header above.
  ctx.save();
  ctx.strokeStyle = CROSS_SECTION_COLOR;
  ctx.lineWidth = 1;
  ctx.setLineDash([2, 2]);
  ctx.beginPath();
  ctx.moveTo(padL, padT);
  ctx.lineTo(padL, baseY);
  ctx.moveTo(w - padR, padT);
  ctx.lineTo(w - padR, baseY);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.font = "bold 11px -apple-system, sans-serif";
  ctx.lineWidth = 3;
  ctx.strokeStyle = "#0b0b0b";
  ctx.fillStyle = "#ffffff";
  ctx.textBaseline = "top";
  ctx.textAlign = "left";
  ctx.strokeText("A", padL + 3, padT + 2);
  ctx.fillText("A", padL + 3, padT + 2);
  ctx.textAlign = "right";
  ctx.strokeText("B", w - padR - 3, padT + 2);
  ctx.fillText("B", w - padR - 3, padT + 2);
  ctx.restore();
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";

  // Axis captions -- the tick numbers above already give the values, these
  // just name what they're values OF (units for y, "distance (m)" for x,
  // matching this layout's own metres-based distance axis).
  const units = layerProfile.units ? ` (${layerProfile.units})` : "";
  ctx.fillStyle = "#7f8b97";
  ctx.font = "9px -apple-system, sans-serif";
  ctx.save();
  ctx.textAlign = "left";
  ctx.translate(2, padT + plotH / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText(`value${units}`, -plotH / 2, 9);
  ctx.restore();
  ctx.textAlign = "right";
  ctx.fillText("distance (m)", w - padR, h - 4);
  ctx.textAlign = "left";
}

// Samples every currently checked+loaded dataset's OWN value grid at the
// shared set of A-B sample points, via the exact same nearest-cell lookup
// exportGeoTiff() already uses to composite multiple dataset rasters --
// this is what makes "one plot per visible layer" possible at all.
// viewer.scene.sampleHeight (used only for the 3D draped guide-line below)
// can ever return just the topmost rendered surface at a point, so it could
// never tell an overlapping bathymetry depth and magnetic-anomaly value
// apart; reading each dataset's own reconstructed grid directly sidesteps
// that entirely, and is exact (not interpolated), same as
// buildDatasetElevationRaster/buildDatasetValueRaster already are.
function computeLayerProfiles(lonDeg, latDeg) {
  const layers = [];
  for (const id of state.order) {
    const d = state.datasets[id];
    if (!d.loaded || !d.visible || !d.sections || !d.meta) continue;
    const raster = buildDatasetValueRaster(d);
    const meta = d.meta;
    const isGeo = !!meta.is_geophysics;
    const label = meta.label || d.manifestEntry.label;
    const kind = isGeo ? meta.legend_kind : elevWord(meta.z_range_m);
    const units = (isGeo ? meta.legend_units || "" : "m").trim();
    const values = new Array(lonDeg.length);
    for (let i = 0; i < lonDeg.length; i++) {
      const col = Math.round((lonDeg[i] - raster.west) / raster.dlon);
      const row = Math.round((raster.north - latDeg[i]) / raster.dlat);
      if (col < 0 || col >= raster.nCols || row < 0 || row >= raster.nRows) {
        values[i] = null;
        continue;
      }
      const v = raster.val[row * raster.nCols + col];
      values[i] = Number.isFinite(v) ? v : null;
    }
    layers.push({ id, label, kind, units, values });
  }
  return layers;
}

// Rebuilds the #crossSectionPlots panel from scratch: one small canvas per
// currently-visible layer, stacked top to bottom in dataset order. Called
// every time the profile is (re)computed, including on a visibility toggle
// (see updateCrossSection) so the set of plots always matches what's
// actually checked right now.
// Rebuilds the bottom dock's plot strip from scratch: one big canvas per
// currently-visible layer, laid out left to right (wrapping if the panel
// gets narrow or many layers are checked at once) -- see
// .cross-section-plots-dock in style.css. Each plot is sized well beyond
// what the old sidebar canvas (232x108) could fit, per the "bigger,
// underneath the pic, not in a side bar" request.
const CROSS_SECTION_DOCK_CANVAS_W = 460;
const CROSS_SECTION_DOCK_CANVAS_H = 230;

function renderCrossSectionPlots(geometryProfile, layerProfiles) {
  const cs = state.crossSection;
  const container = document.getElementById("crossSectionPlots");
  if (!container) return;
  container.innerHTML = "";
  cs.layerCanvases = [];
  if (layerProfiles.length === 0) {
    const hint = document.createElement("div");
    hint.className = "hint-text";
    hint.textContent = "No datasets checked -- check a dataset in the panel to see its profile here.";
    container.appendChild(hint);
    return;
  }
  for (const lp of layerProfiles) {
    const wrap = document.createElement("div");
    wrap.className = "cross-section-plot-dock";
    const canvas = document.createElement("canvas");
    canvas.width = CROSS_SECTION_DOCK_CANVAS_W;
    canvas.height = CROSS_SECTION_DOCK_CANVAS_H;
    canvas.className = "cross-section-canvas-dock";
    wrap.appendChild(canvas);
    container.appendChild(wrap);
    drawLayerCrossSectionCanvas(canvas, geometryProfile, lp);
    cs.layerCanvases.push({ canvas, layerProfile: lp });
  }
}

function computeAndRenderCrossSection() {
  const cs = state.crossSection;
  if (!cs.pointA || !cs.pointB) return;
  if (viewer.scene.sampleHeightSupported === false) {
    setCrossSectionStatus("This browser/GPU doesn't support surface height sampling here -- can't compute a cross-section.");
    return;
  }
  const profile = sampleCrossSectionProfile(cs.pointA, cs.pointB);
  cs.profile = profile;
  cs.layerProfiles = computeLayerProfiles(profile.lonDeg, profile.latDeg);

  cs.lineCollection.removeAll();
  let seg = [];
  const flushSeg = () => {
    if (seg.length > 1) {
      cs.lineCollection.add({
        positions: seg,
        width: 3,
        material: Cesium.Material.fromType("Color", { color: Cesium.Color.fromCssColorString(CROSS_SECTION_COLOR) }),
      });
    }
    seg = [];
  };
  for (const c of profile.drapedCartesians) {
    if (c) seg.push(c);
    else flushSeg();
  }
  flushSeg();

  const layerCount = cs.layerProfiles.length;
  const anyCoverage = cs.layerProfiles.some((lp) => lp.values.some((v) => v != null));
  setCrossSectionStatus(
    layerCount === 0
      ? `Cross-section: ${profile.totalKm.toFixed(2)} km. No datasets checked -- check one above to plot it. Click "Pick 2 points" to redo.`
      : `Cross-section: ${profile.totalKm.toFixed(2)} km across ${layerCount} checked layer${layerCount === 1 ? "" : "s"}, one plot each below. Click "Pick 2 points" to redo.`
  );
  renderCrossSectionPlots(profile, cs.layerProfiles);
  setCrossSectionDownloadsEnabled(anyCoverage);
  setCrossSectionDockVisible(true);
  viewer.scene.requestRender();
}

// Called from the same places placeTrackPoints() is -- rebuildAllLoaded()
// (exaggeration/lighting/colour-mode changes) and setDatasetVisible()
// (checking/unchecking a dataset) -- so an existing cross-section stays
// correct rather than silently going stale.
function updateCrossSection() {
  const cs = state.crossSection;
  if (!cs.pointA && !cs.pointB) return;
  repositionCrossSectionMarkers();
  if (cs.pointA && cs.pointB) computeAndRenderCrossSection();
  viewer.scene.requestRender();
}

function placeCrossSectionEndpoint(cartesian) {
  const cs = state.crossSection;
  ensureCrossSectionCollections();
  const carto = Cesium.Cartographic.fromCartesian(cartesian);
  const point = {
    lonDeg: Cesium.Math.toDegrees(carto.longitude),
    latDeg: Cesium.Math.toDegrees(carto.latitude),
    cartesian,
  };
  if (!cs.pointA) {
    cs.pointA = point;
    addCrossSectionMarker(point, "A");
    setCrossSectionStatus("Point A set. Click a second point on the surface.");
  } else {
    cs.pointB = point;
    addCrossSectionMarker(point, "B");
    cs.armed = false;
    // computeAndRenderCrossSection() does CROSS_SECTION_SAMPLES synchronous
    // sampleHeight() calls -- each one an offscreen render, not free -- so
    // show the loading overlay and defer a tick to let it actually paint
    // before the heavy synchronous loop runs, same pattern as the
    // exaggeration slider / colour-mode / lighting toggles above.
    setLoading(true, "Computing cross-section…");
    setTimeout(() => {
      computeAndRenderCrossSection();
      setLoading(false);
    }, 10);
  }
  viewer.scene.requestRender();
}

document.getElementById("crossSectionPickBtn").addEventListener("click", () => {
  armCrossSectionPicking();
});
document.getElementById("crossSectionClearBtn").addEventListener("click", () => {
  clearCrossSection();
});
document.getElementById("crossSectionDownloadCsvBtn").addEventListener("click", () => {
  downloadCrossSectionCSV();
});
document.getElementById("crossSectionDownloadPngBtn").addEventListener("click", () => {
  downloadCrossSectionPNG();
});
document.getElementById("crossSectionDockCloseBtn").addEventListener("click", () => {
  clearCrossSection();
});

// =====================================================================
// GeoTIFF export (composited from every checked dataset's own vertex grid,
// not a screen capture -- so it's a true equirectangular raster, not an
// oblique 3D snapshot)
// =====================================================================

const MAX_EXPORT_DIM = 6000;

// =====================================================================
// GeoTIFF export region -- "entire region" (the union bbox of every checked
// dataset, the original/default behaviour) vs. "select region" (drag a
// rectangle on the map; the export is clipped to that rectangle intersected
// with the checked datasets' own coverage). The rectangle corners are
// picked with camera.pickEllipsoid rather than scene.sampleHeight -- it's a
// cheap ray/ellipsoid intersection with no render pass, so it stays smooth
// during a live drag (sampleHeight costs whole seconds per call here -- see
// the cross-section tool above -- which would make dragging unusable).
// =====================================================================

function ensureExportRectangleEntity() {
  const ge = state.geoExport;
  if (!ge.rectangleEntity) {
    ge.rectangleEntity = viewer.entities.add({
      show: false,
      rectangle: {
        coordinates: new Cesium.CallbackProperty(() => {
          const b = state.geoExport.bbox;
          return b ? Cesium.Rectangle.fromDegrees(b.west, b.south, b.east, b.north) : undefined;
        }, false),
        material: Cesium.Color.fromCssColorString("#ffd400").withAlpha(0.15),
        outline: true,
        outlineColor: Cesium.Color.fromCssColorString("#ffd400"),
        outlineWidth: 2,
        height: 0,
      },
    });
  }
  return ge.rectangleEntity;
}

function setExportRegionStatus(text) {
  const el = document.getElementById("exportRegionStatus");
  if (el) el.textContent = text;
}

function pickLonLatFromWindowPosition(windowPosition) {
  const ellipsoid = viewer.scene.globe.ellipsoid;
  const cartesian = viewer.camera.pickEllipsoid(windowPosition, ellipsoid);
  if (!cartesian) return null;
  const carto = ellipsoid.cartesianToCartographic(cartesian);
  return { lonDeg: Cesium.Math.toDegrees(carto.longitude), latDeg: Cesium.Math.toDegrees(carto.latitude) };
}

function updateExportBBoxFromCorners(c1, c2) {
  state.geoExport.bbox = {
    west: Math.min(c1.lonDeg, c2.lonDeg),
    east: Math.max(c1.lonDeg, c2.lonDeg),
    south: Math.min(c1.latDeg, c2.latDeg),
    north: Math.max(c1.latDeg, c2.latDeg),
  };
  viewer.scene.requestRender();
}

function armExportRectangleDrawing() {
  const ge = state.geoExport;
  ge.drawing = "armed";
  ge.corner1 = null;
  ge.bbox = null;
  const entity = ensureExportRectangleEntity();
  entity.show = false;
  setExportRegionStatus("Drag on the map to draw the export rectangle.");
  viewer.scene.requestRender();
}

function clearExportRectangle() {
  const ge = state.geoExport;
  ge.drawing = "idle";
  ge.corner1 = null;
  ge.bbox = null;
  if (ge.rectangleEntity) ge.rectangleEntity.show = false;
  restoreCameraControlsAfterDrag();
  setExportRegionStatus(ge.mode === "select" ? "Drag on the map to draw the export rectangle." : "");
  viewer.scene.requestRender();
}

// The rectangle drag needs exclusive use of the mouse, so the normal
// left-drag-to-rotate camera behaviour is suspended for the duration of one
// drag and restored as soon as it ends (mouse-up) or is cancelled.
function suspendCameraControlsForDrag() {
  ctrl.enableRotate = false;
  ctrl.enableTranslate = false;
  ctrl.enableTilt = false;
  ctrl.enableZoom = false;
}
function restoreCameraControlsAfterDrag() {
  ctrl.enableRotate = true;
  ctrl.enableTranslate = true;
  ctrl.enableTilt = true;
  ctrl.enableZoom = true;
}

function setExportMode(mode) {
  const ge = state.geoExport;
  ge.mode = mode;
  const controlsEl = document.getElementById("exportRegionControls");
  const statusEl = document.getElementById("exportRegionStatus");
  const show = mode === "select";
  if (controlsEl) controlsEl.style.display = show ? "flex" : "none";
  if (statusEl) statusEl.style.display = show ? "block" : "none";
  if (!show) clearExportRectangle();
  else setExportRegionStatus(ge.bbox ? statusEl.textContent : "Drag on the map to draw the export rectangle.");
}

handler.setInputAction((movement) => {
  if (state.geoExport.drawing !== "armed") return;
  const p = pickLonLatFromWindowPosition(movement.position);
  if (!p) return;
  state.geoExport.corner1 = p;
  state.geoExport.drawing = "dragging";
  suspendCameraControlsForDrag();
  const entity = ensureExportRectangleEntity();
  entity.show = true;
  updateExportBBoxFromCorners(p, p);
  setExportRegionStatus("Drag to size the rectangle, release the mouse to finish.");
}, Cesium.ScreenSpaceEventType.LEFT_DOWN);

handler.setInputAction((movement) => {
  if (state.geoExport.drawing !== "dragging") return;
  const p = pickLonLatFromWindowPosition(movement.endPosition);
  if (!p) return;
  updateExportBBoxFromCorners(state.geoExport.corner1, p);
}, Cesium.ScreenSpaceEventType.MOUSE_MOVE);

handler.setInputAction((movement) => {
  if (state.geoExport.drawing !== "dragging") return;
  const p = pickLonLatFromWindowPosition(movement.position);
  if (p) updateExportBBoxFromCorners(state.geoExport.corner1, p);
  state.geoExport.drawing = "idle";
  state.geoExport.lastDragEnd = Date.now();
  restoreCameraControlsAfterDrag();
  let b = state.geoExport.bbox;
  if (b && ((b.east - b.west) < 1e-4 || (b.north - b.south) < 1e-4)) { // a click, not a drag
    state.geoExport.bbox = b = null;
    if (state.geoExport.rectangleEntity) state.geoExport.rectangleEntity.show = false;
  }
  setExportRegionStatus(
    b
      ? `Rectangle: ${b.west.toFixed(3)}, ${b.south.toFixed(3)} to ${b.east.toFixed(3)}, ${b.north.toFixed(3)} (lon, lat). "Export" will clip to this area; "Draw rectangle" to redo.`
      : "Couldn't place that rectangle -- try dragging again."
  );
}, Cesium.ScreenSpaceEventType.LEFT_UP);

document.querySelectorAll('input[name="exportRegionMode"]').forEach((el) => {
  el.addEventListener("change", (e) => setExportMode(e.target.value));
});
document.querySelectorAll('input[name="exportFormat"]').forEach((el) => {
  el.addEventListener("change", (e) => {
    state.geoExport.format = e.target.value;
  });
});
document.getElementById("exportDrawRectBtn").addEventListener("click", () => {
  armExportRectangleDrawing();
});
document.getElementById("exportClearRectBtn").addEventListener("click", () => {
  clearExportRectangle();
});

// Reconstruct a dense (row,col) RGBA grid for one dataset from its flat,
// NaN-masked vertex list -- the vertices came from a uniform lon/lat grid
// (dlon_deg/dlat_deg apart, from meta.json) before sparse-masking, so this
// is an exact un-flatten, not an interpolation.
function buildDatasetRaster(d) {
  const key = state.colorMode;
  d.rasterCache = d.rasterCache || {};
  if (d.rasterCache[key]) return d.rasterCache[key];

  const meta = d.meta;
  const west = meta.bbox.lon_min;
  const north = meta.bbox.lat_max;
  const dlon = meta.dlon_deg;
  const dlat = meta.dlat_deg;
  const nCols = Math.max(1, Math.round((meta.bbox.lon_max - meta.bbox.lon_min) / dlon) + 1);
  const nRows = Math.max(1, Math.round((meta.bbox.lat_max - meta.bbox.lat_min) / dlat) + 1);
  const rgba = new Uint8ClampedArray(nRows * nCols * 4); // alpha 0 everywhere = nodata

  const colorSrc = pickColorSource(d);
  const lonRad = d.sections.lon_rad;
  const latRad = d.sections.lat_rad;
  const n = meta.vertex_count;
  const RAD2DEG = 180 / Math.PI;
  for (let i = 0; i < n; i++) {
    const lonDeg = lonRad[i] * RAD2DEG;
    const latDeg = latRad[i] * RAD2DEG;
    const col = Math.round((lonDeg - west) / dlon);
    const row = Math.round((north - latDeg) / dlat);
    if (col < 0 || col >= nCols || row < 0 || row >= nRows) continue;
    const outIdx = (row * nCols + col) * 4;
    const srcIdx = i * 4;
    rgba[outIdx] = colorSrc[srcIdx];
    rgba[outIdx + 1] = colorSrc[srcIdx + 1];
    rgba[outIdx + 2] = colorSrc[srcIdx + 2];
    rgba[outIdx + 3] = 255;
  }
  const result = { west, north, dlon, dlat, nCols, nRows, rgba };
  d.rasterCache[key] = result;
  return result;
}

// Same un-flatten as buildDatasetRaster above, but writing each vertex's own
// z_m (true elevation/depth in metres, never exaggerated -- see the
// "GeoTIFF export format" section of the README) instead of its baked-in
// display colour. This is what makes the "Elevation" export format usable
// for real terrain analysis (slope, roughness, etc.) rather than just a
// coloured picture of the mesh -- see exportGeoTiff() below for why the
// RGBA export alone can't be used for that.
function buildDatasetElevationRaster(d) {
  const key = "elevation";
  d.rasterCache = d.rasterCache || {};
  if (d.rasterCache[key]) return d.rasterCache[key];

  const meta = d.meta;
  const west = meta.bbox.lon_min;
  const north = meta.bbox.lat_max;
  const dlon = meta.dlon_deg;
  const dlat = meta.dlat_deg;
  const nCols = Math.max(1, Math.round((meta.bbox.lon_max - meta.bbox.lon_min) / dlon) + 1);
  const nRows = Math.max(1, Math.round((meta.bbox.lat_max - meta.bbox.lat_min) / dlat) + 1);
  const elev = new Float32Array(nRows * nCols).fill(NaN); // NaN everywhere = nodata

  const lonRad = d.sections.lon_rad;
  const latRad = d.sections.lat_rad;
  const zM = d.sections.z_m;
  const n = meta.vertex_count;
  const RAD2DEG = 180 / Math.PI;
  for (let i = 0; i < n; i++) {
    const lonDeg = lonRad[i] * RAD2DEG;
    const latDeg = latRad[i] * RAD2DEG;
    const col = Math.round((lonDeg - west) / dlon);
    const row = Math.round((north - latDeg) / dlat);
    if (col < 0 || col >= nCols || row < 0 || row >= nRows) continue;
    elev[row * nCols + col] = zM[i];
  }
  const result = { west, north, dlon, dlat, nCols, nRows, elev };
  d.rasterCache[key] = result;
  return result;
}

// Same un-flatten again, but for the cross-section tool: writes each
// vertex's own geophysics VALUE (nT, mGal, km, degC -- whatever the layer's
// "value" mesh.bin section holds, see build_geophysics_drape.py) for a
// geophysics dataset, or its z_m (true depth/elevation, never exaggerated)
// for a bathymetry-type dataset that has no separate "value" section --
// z_m already *is* the plottable quantity there. Either way this is the
// dataset's own real number, not its baked-in display colour, so
// computeLayerProfiles() can chart it directly.
function buildDatasetValueRaster(d) {
  const key = "value";
  d.rasterCache = d.rasterCache || {};
  if (d.rasterCache[key]) return d.rasterCache[key];

  const meta = d.meta;
  const west = meta.bbox.lon_min;
  const north = meta.bbox.lat_max;
  const dlon = meta.dlon_deg;
  const dlat = meta.dlat_deg;
  const nCols = Math.max(1, Math.round((meta.bbox.lon_max - meta.bbox.lon_min) / dlon) + 1);
  const nRows = Math.max(1, Math.round((meta.bbox.lat_max - meta.bbox.lat_min) / dlat) + 1);
  const val = new Float32Array(nRows * nCols).fill(NaN); // NaN everywhere = nodata

  const lonRad = d.sections.lon_rad;
  const latRad = d.sections.lat_rad;
  const srcArr = d.sections.value || d.sections.z_m;
  const n = meta.vertex_count;
  const RAD2DEG = 180 / Math.PI;
  for (let i = 0; i < n; i++) {
    const lonDeg = lonRad[i] * RAD2DEG;
    const latDeg = latRad[i] * RAD2DEG;
    const col = Math.round((lonDeg - west) / dlon);
    const row = Math.round((north - latDeg) / dlat);
    if (col < 0 || col >= nCols || row < 0 || row >= nRows) continue;
    val[row * nCols + col] = srcArr[i];
  }
  const result = { west, north, dlon, dlat, nCols, nRows, val };
  d.rasterCache[key] = result;
  return result;
}

// ---- GeoTIFF writers ----
// Two minimal but valid GeoTIFF writers sharing the same tag-encoding
// helpers and IFD assembly: writeGeoTIFFRGBA (uncompressed 4-band RGBA --
// the original "Colour" export format, a rendered picture of the mesh) and
// writeGeoTIFFFloat32 (uncompressed single-band 32-bit float -- the
// "Elevation" format, real metres per pixel, NaN for nodata). Both are
// single-strip, WGS84 geographic CRS via GeoKeys. No external library --
// TIFF is a plain, well-documented binary format and this only needs a
// handful of tags to be readable by GDAL/QGIS/GeoMapApp (and, for the float
// format, by numpy/rasterio-based analysis scripts).
const TIFF_TYPE_SHORT = 3;
const TIFF_TYPE_LONG = 4;
const TIFF_TYPE_ASCII = 2;
const TIFF_TYPE_DOUBLE = 12;

function tiffU16Array(vals) {
  const b = new Uint8Array(vals.length * 2);
  const dv = new DataView(b.buffer);
  vals.forEach((v, i) => dv.setUint16(i * 2, v, true));
  return b;
}
function tiffU32Array(vals) {
  const b = new Uint8Array(vals.length * 4);
  const dv = new DataView(b.buffer);
  vals.forEach((v, i) => dv.setUint32(i * 4, v, true));
  return b;
}
function tiffF64Array(vals) {
  const b = new Uint8Array(vals.length * 8);
  const dv = new DataView(b.buffer);
  vals.forEach((v, i) => dv.setFloat64(i * 8, v, true));
  return b;
}
// Little-endian IEEE-754 float32 pixel bytes, built explicitly via DataView
// rather than reading a Float32Array's raw buffer -- guarantees correct
// byte order regardless of host endianness, matching the explicit-LE style
// already used for every tag value below.
function tiffF32LEBytes(vals) {
  const b = new Uint8Array(vals.length * 4);
  const dv = new DataView(b.buffer);
  for (let i = 0; i < vals.length; i++) dv.setFloat32(i * 4, vals[i], true);
  return b;
}
function tiffAsciiBytes(str) {
  const withNull = `${str}\0`;
  const b = new Uint8Array(withNull.length);
  for (let i = 0; i < withNull.length; i++) b[i] = withNull.charCodeAt(i);
  return b;
}

// The geo-referencing tags are identical for both formats: pixel scale +
// tiepoint (pixel (0,0) -> the raster's own west/north corner) and a
// GeoKeyDirectory declaring plain WGS84 geographic coordinates (EPSG:4326).
function tiffGeoReferencingEntries(west, north, dlonDeg, dlatDeg) {
  const geoKeys = [
    1, 1, 0, 3,
    1024, 0, 1, 2, // GTModelTypeGeoKey = 2 (Geographic)
    1025, 0, 1, 1, // GTRasterTypeGeoKey = 1 (RasterPixelIsArea)
    2048, 0, 1, 4326, // GeographicTypeGeoKey = EPSG:4326 (WGS84)
  ];
  return [
    { tag: 33550, type: TIFF_TYPE_DOUBLE, count: 3, bytes: tiffF64Array([dlonDeg, dlatDeg, 0]) }, // ModelPixelScaleTag
    { tag: 33922, type: TIFF_TYPE_DOUBLE, count: 6, bytes: tiffF64Array([0, 0, 0, west, north, 0]) }, // ModelTiepointTag
    { tag: 34735, type: TIFF_TYPE_SHORT, count: geoKeys.length, bytes: tiffU16Array(geoKeys) }, // GeoKeyDirectoryTag
  ];
}

// Assemble a single-strip TIFF from raw pixel bytes + a list of {tag, type,
// count, bytes} entries (format-specific tags + the shared geo-referencing
// ones above). Shared by both writers below -- byte-length-based inline-vs-
// external placement (<=4 bytes inline in the IFD entry, otherwise written
// after it) is generic TIFF IFD mechanics, independent of pixel format.
function assembleTIFF(pixelBytes, entries) {
  const imageDataOffset = 8; // right after the 8-byte header
  const imageDataSize = pixelBytes.length;

  const sortedEntries = entries.slice().sort((a, b) => a.tag - b.tag); // TIFF requires ascending tag order
  const numEntries = sortedEntries.length;
  const ifdOffset = imageDataOffset + imageDataSize;
  const ifdSize = 2 + numEntries * 12 + 4;
  let externalOffset = ifdOffset + ifdSize;
  for (const e of sortedEntries) {
    if (e.bytes.length > 4) {
      if (externalOffset % 2 !== 0) externalOffset += 1; // tag values must start on a word boundary
      e.offset = externalOffset;
      externalOffset += e.bytes.length;
    }
  }
  const totalSize = externalOffset;

  const buf = new ArrayBuffer(totalSize);
  const dv = new DataView(buf);
  const bytes = new Uint8Array(buf);

  dv.setUint8(0, 0x49);
  dv.setUint8(1, 0x49); // 'II' little-endian
  dv.setUint16(2, 42, true);
  dv.setUint32(4, ifdOffset, true);

  bytes.set(pixelBytes, imageDataOffset);

  let p = ifdOffset;
  dv.setUint16(p, numEntries, true);
  p += 2;
  for (const e of sortedEntries) {
    dv.setUint16(p, e.tag, true);
    p += 2;
    dv.setUint16(p, e.type, true);
    p += 2;
    dv.setUint32(p, e.count, true);
    p += 4;
    if (e.bytes.length <= 4) {
      bytes.set(e.bytes, p);
    } else {
      dv.setUint32(p, e.offset, true);
      bytes.set(e.bytes, e.offset);
    }
    p += 4;
  }
  dv.setUint32(p, 0, true); // no next IFD

  return new Blob([buf], { type: "image/tiff" });
}

function writeGeoTIFFRGBA(width, height, rgba, west, north, dlonDeg, dlatDeg) {
  const imageDataOffset = 8;
  const imageDataSize = rgba.length;
  const entries = [
    { tag: 256, type: TIFF_TYPE_LONG, count: 1, bytes: tiffU32Array([width]) }, // ImageWidth
    { tag: 257, type: TIFF_TYPE_LONG, count: 1, bytes: tiffU32Array([height]) }, // ImageLength
    { tag: 258, type: TIFF_TYPE_SHORT, count: 4, bytes: tiffU16Array([8, 8, 8, 8]) }, // BitsPerSample
    { tag: 259, type: TIFF_TYPE_SHORT, count: 1, bytes: tiffU16Array([1]) }, // Compression = none
    { tag: 262, type: TIFF_TYPE_SHORT, count: 1, bytes: tiffU16Array([2]) }, // PhotometricInterpretation = RGB
    { tag: 273, type: TIFF_TYPE_LONG, count: 1, bytes: tiffU32Array([imageDataOffset]) }, // StripOffsets
    { tag: 277, type: TIFF_TYPE_SHORT, count: 1, bytes: tiffU16Array([4]) }, // SamplesPerPixel (RGBA)
    { tag: 278, type: TIFF_TYPE_LONG, count: 1, bytes: tiffU32Array([height]) }, // RowsPerStrip (single strip)
    { tag: 279, type: TIFF_TYPE_LONG, count: 1, bytes: tiffU32Array([imageDataSize]) }, // StripByteCounts
    { tag: 284, type: TIFF_TYPE_SHORT, count: 1, bytes: tiffU16Array([1]) }, // PlanarConfiguration = chunky
    { tag: 338, type: TIFF_TYPE_SHORT, count: 1, bytes: tiffU16Array([2]) }, // ExtraSamples = unassociated alpha
    ...tiffGeoReferencingEntries(west, north, dlonDeg, dlatDeg),
  ];
  return assembleTIFF(rgba, entries);
}

// Single-band 32-bit IEEE float GeoTIFF -- real elevation/depth values in
// metres, not a colour ramp, so it's directly usable by terrain-analysis
// tools (slope, roughness, hillshade, contouring, etc). NaN marks nodata;
// GDAL, QGIS, and numpy/rasterio all treat a float NaN pixel as nodata
// automatically, and the GDAL_NODATA tag (42113, GDAL's private ASCII tag
// for this) spells that out explicitly for tools that check it rather than
// inferring it from NaN.
function writeGeoTIFFFloat32(width, height, elevData, west, north, dlonDeg, dlatDeg) {
  const imageDataOffset = 8;
  const pixelBytes = tiffF32LEBytes(elevData);
  const imageDataSize = pixelBytes.length;
  const noDataBytes = tiffAsciiBytes("nan");
  const entries = [
    { tag: 256, type: TIFF_TYPE_LONG, count: 1, bytes: tiffU32Array([width]) }, // ImageWidth
    { tag: 257, type: TIFF_TYPE_LONG, count: 1, bytes: tiffU32Array([height]) }, // ImageLength
    { tag: 258, type: TIFF_TYPE_SHORT, count: 1, bytes: tiffU16Array([32]) }, // BitsPerSample
    { tag: 259, type: TIFF_TYPE_SHORT, count: 1, bytes: tiffU16Array([1]) }, // Compression = none
    { tag: 262, type: TIFF_TYPE_SHORT, count: 1, bytes: tiffU16Array([1]) }, // PhotometricInterpretation = BlackIsZero
    { tag: 273, type: TIFF_TYPE_LONG, count: 1, bytes: tiffU32Array([imageDataOffset]) }, // StripOffsets
    { tag: 277, type: TIFF_TYPE_SHORT, count: 1, bytes: tiffU16Array([1]) }, // SamplesPerPixel (single band)
    { tag: 278, type: TIFF_TYPE_LONG, count: 1, bytes: tiffU32Array([height]) }, // RowsPerStrip (single strip)
    { tag: 279, type: TIFF_TYPE_LONG, count: 1, bytes: tiffU32Array([imageDataSize]) }, // StripByteCounts
    { tag: 284, type: TIFF_TYPE_SHORT, count: 1, bytes: tiffU16Array([1]) }, // PlanarConfiguration = chunky
    { tag: 339, type: TIFF_TYPE_SHORT, count: 1, bytes: tiffU16Array([3]) }, // SampleFormat = IEEE floating point
    { tag: 42113, type: TIFF_TYPE_ASCII, count: noDataBytes.length, bytes: noDataBytes }, // GDAL_NODATA
    ...tiffGeoReferencingEntries(west, north, dlonDeg, dlatDeg),
  ];
  return assembleTIFF(pixelBytes, entries);
}

async function exportGeoTiff() {
  const statusEl = document.getElementById("exportStatus");
  const allVisible = state.order.map((id) => state.datasets[id]).filter((d) => d.loaded && d.visible);
  if (allVisible.length === 0) {
    statusEl.textContent = "No datasets are checked -- nothing to export.";
    return;
  }

  const elevationFormat = state.geoExport.format === "elevation";
  // Geophysics layers drape on borrowed GMRT terrain purely so they have
  // *something* to sit on in 3D (see "Geophysics layers" in the README) --
  // their z_m is that borrowed terrain, not their own measurement (their
  // real quantity -- nT/mGal/km/degC -- only exists baked into the display
  // colour). Exporting that borrowed terrain as if it were the layer's own
  // elevation would be actively misleading, so geophysics layers are
  // dropped from the elevation format entirely; the colour format is
  // unaffected and still includes every checked dataset as before.
  const sourceDatasets = elevationFormat
    ? allVisible.filter((d) => (d.manifestEntry.category || "bathymetry") !== "geophysics")
    : allVisible;
  if (elevationFormat && sourceDatasets.length === 0) {
    statusEl.textContent =
      'Elevation export needs a Bathymetry-section dataset checked -- geophysics layers drape on borrowed terrain, not their own elevation data, so they\'re excluded from this format. Check a bathymetry dataset, or switch back to "Colour" format.';
    return;
  }

  const selectMode = state.geoExport.mode === "select";
  if (selectMode && !state.geoExport.bbox) {
    statusEl.textContent = 'Region mode is "Select region" but no rectangle has been drawn yet -- click "Draw rectangle" first.';
    return;
  }

  setLoading(true, elevationFormat ? "Building elevation GeoTIFF…" : "Building GeoTIFF…");
  await new Promise((r) => setTimeout(r, 20)); // let the loading text paint first

  let west = Infinity;
  let east = -Infinity;
  let south = Infinity;
  let north = -Infinity;
  let finestDlon = Infinity;
  let finestDlat = Infinity;
  for (const d of sourceDatasets) {
    const b = d.meta.bbox;
    west = Math.min(west, b.lon_min);
    east = Math.max(east, b.lon_max);
    south = Math.min(south, b.lat_min);
    north = Math.max(north, b.lat_max);
    finestDlon = Math.min(finestDlon, d.meta.dlon_deg);
    finestDlat = Math.min(finestDlat, d.meta.dlat_deg);
  }

  // "Select region" clips the export sources' own union bbox down to the
  // drawn rectangle -- it can only shrink the export, never extend it past
  // what's actually loaded/checked (and eligible for the chosen format).
  if (selectMode) {
    const r = state.geoExport.bbox;
    west = Math.max(west, r.west);
    east = Math.min(east, r.east);
    south = Math.max(south, r.south);
    north = Math.min(north, r.north);
    if (west >= east || south >= north) {
      setLoading(false);
      statusEl.textContent = "The drawn rectangle doesn't overlap any checked (and format-eligible) dataset -- draw it over the visible terrain.";
      return;
    }
  }

  let outDlon = finestDlon;
  let outDlat = finestDlat;
  let outW = Math.max(1, Math.round((east - west) / outDlon) + 1);
  let outH = Math.max(1, Math.round((north - south) / outDlat) + 1);
  const scale = Math.max(1, outW / MAX_EXPORT_DIM, outH / MAX_EXPORT_DIM);
  if (scale > 1) {
    outDlon *= scale;
    outDlat *= scale;
    outW = Math.max(1, Math.round((east - west) / outDlon) + 1);
    outH = Math.max(1, Math.round((north - south) / outDlat) + 1);
  }

  let blob;
  let stamped = 0;

  if (elevationFormat) {
    // Real metres per pixel, composited the same coarse-to-fine, pull-based
    // way as the colour export (first source with real data wins) -- just
    // taking each source's raw z_m instead of its baked-in display colour.
    const rasters = sourceDatasets.map((d) => buildDatasetElevationRaster(d));
    const outElev = new Float32Array(outW * outH).fill(NaN);
    for (let outRow = 0; outRow < outH; outRow++) {
      const lat = north - outRow * outDlat;
      for (let outCol = 0; outCol < outW; outCol++) {
        const lon = west + outCol * outDlon;
        const outIdx = outRow * outW + outCol;
        for (let k = 0; k < rasters.length; k++) {
          const r = rasters[k];
          const col = Math.round((lon - r.west) / r.dlon);
          const row = Math.round((r.north - lat) / r.dlat);
          if (col < 0 || col >= r.nCols || row < 0 || row >= r.nRows) continue;
          const v = r.elev[row * r.nCols + col];
          if (!Number.isFinite(v)) continue;
          outElev[outIdx] = v;
        }
      }
    }
    blob = writeGeoTIFFFloat32(outW, outH, outElev, west, north, outDlon, outDlat);
  } else {
    const rasters = sourceDatasets.map((d) => buildDatasetRaster(d));
    const outRGBA = new Uint8ClampedArray(outW * outH * 4);

    for (let outRow = 0; outRow < outH; outRow++) {
      const lat = north - outRow * outDlat;
      for (let outCol = 0; outCol < outW; outCol++) {
        const lon = west + outCol * outDlon;
        const outIdx = (outRow * outW + outCol) * 4;
        // coarse -> fine (state.order / visible order): later datasets overwrite,
        // so a detailed survey wins over the basemap wherever it has data.
        for (let k = 0; k < rasters.length; k++) {
          const r = rasters[k];
          const col = Math.round((lon - r.west) / r.dlon);
          const row = Math.round((r.north - lat) / r.dlat);
          if (col < 0 || col >= r.nCols || row < 0 || row >= r.nRows) continue;
          const srcIdx = (row * r.nCols + col) * 4;
          if (r.rgba[srcIdx + 3] === 0) continue;
          outRGBA[outIdx] = r.rgba[srcIdx];
          outRGBA[outIdx + 1] = r.rgba[srcIdx + 1];
          outRGBA[outIdx + 2] = r.rgba[srcIdx + 2];
          outRGBA[outIdx + 3] = 255;
        }
      }
    }

    // Track points are a visual annotation (status colour), not elevation
    // data, so they're only stamped into the colour export.
    for (const pt of state.trackPoints) {
      if (!pt.cartesian) continue;
      const outCol = Math.round((pt.lonDeg - west) / outDlon);
      const outRow = Math.round((north - pt.latDeg) / outDlat);
      // Outside the (possibly select-region-clipped) export bbox entirely --
      // don't count it as stamped, matching what the raster actually shows.
      if (outCol < -2 || outCol >= outW + 2 || outRow < -2 || outRow >= outH + 2) continue;
      const [pr, pg, pb] = hexToRgb(trackPointColor(pt));
      for (let dr = -2; dr <= 2; dr++) {
        for (let dc = -2; dc <= 2; dc++) {
          const rr = outRow + dr;
          const cc = outCol + dc;
          if (rr < 0 || rr >= outH || cc < 0 || cc >= outW) continue;
          const idx = (rr * outW + cc) * 4;
          outRGBA[idx] = pr;
          outRGBA[idx + 1] = pg;
          outRGBA[idx + 2] = pb;
          outRGBA[idx + 3] = 255;
        }
      }
      stamped += 1;
    }

    blob = writeGeoTIFFRGBA(outW, outH, outRGBA, west, north, outDlon, outDlat);
  }

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = elevationFormat ? `viewer3d_export_elevation_${Date.now()}.tif` : `viewer3d_export_color_${Date.now()}.tif`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 30000);

  setLoading(false);
  if (elevationFormat) {
    statusEl.textContent =
      `Exported ${outW}×${outH} px elevation GeoTIFF (~${(outDlon * 111320).toFixed(0)} m/px, single-band float32 metres, NaN = nodata), ${sourceDatasets.length} layer(s)` +
      (selectMode ? ", selected region" : "") +
      (state.trackPoints.length > 0 ? " (track points not included -- colour-only annotation)" : "") +
      `. Saved via your browser's download.`;
  } else {
    statusEl.textContent =
      `Exported ${outW}×${outH} px (~${(outDlon * 111320).toFixed(0)} m/px), ${sourceDatasets.length} layer(s)` +
      (selectMode ? ", selected region" : "") +
      (stamped > 0 ? `, ${stamped} track point(s) stamped in` : "") +
      `. Saved via your browser's download.`;
  }
}

document.getElementById("exportGeoTiffBtn").addEventListener("click", () => {
  exportGeoTiff().catch((err) => {
    console.error(err);
    document.getElementById("exportStatus").textContent = `Export failed: ${err.message}`;
    setLoading(false);
  });
});

// =====================================================================
// Ship position (optional live GPS + heading feed via run_viewer.py --ship-feed)
// =====================================================================
// run_viewer.py listens on UDP (GPS 55000, heading 55001), parses NMEA and serves the latest
// fix at ship.json. This polls it once a minute -- deliberately slow -- only while the
// "Show ship position" box is ticked.
const SHIP_POLL_MS = 60 * 1000;
const SHIP_STALE_S = 5 * 60;
const SHIP_MIN_SOG_KN = 1.0; // below this, course over ground is meaningless (drifting / on station)
const ship = { timer: null, entity: null, trackEntity: null, iconCache: {}, lastGood: 0, last: null, hCache: new Map(), gen: 0 };

// Height (m, exaggerated scene units) of the displayed seafloor under a point, so the ship and its
// lines sit on the surface the user is looking at, not at sea level above it (which shifts them by
// kilometres in a tilted view). Cached per rounded position; cleared when the surface changes.
function seafloorHeight(lon, lat) {
  const key = `${lon.toFixed(4)},${lat.toFixed(4)}`;
  if (ship.hCache.has(key)) return ship.hCache.get(key);
  let h;
  try { h = viewer.scene.sampleHeight(Cesium.Cartographic.fromDegrees(lon, lat)); } catch (e) { h = undefined; }
  h = Number.isFinite(h) ? h : 0;
  if (ship.hCache.size > 5000) ship.hCache.clear();
  ship.hCache.set(key, h);
  return h;
}
function shipPos(lon, lat) {
  return Cesium.Cartesian3.fromDegrees(lon, lat, seafloorHeight(lon, lat) + 30 * state.exaggeration);
}

function shipIcon(stale) {
  const key = stale ? "stale" : "live";
  if (ship.iconCache[key]) return ship.iconCache[key];
  const c = document.createElement("canvas");
  c.width = 40;
  c.height = 40;
  const g = c.getContext("2d");
  g.translate(20, 20);
  g.beginPath(); // arrow pointing "up" (= heading 0 / north before rotation)
  g.moveTo(0, -17);
  g.lineTo(11, 14);
  g.lineTo(0, 7);
  g.lineTo(-11, 14);
  g.closePath();
  g.fillStyle = stale ? "#9aa7b2" : "#ff2d6f";
  g.strokeStyle = "#ffffff";
  g.lineWidth = 3;
  g.stroke();
  g.fill();
  ship.iconCache[key] = c;
  return c;
}

function fmtDegMin(v, pos, neg) {
  const u = Math.round(Math.abs(v) * 60 * 1000); // thousandths of a minute: 59.9996' never prints as 60.000'
  const d = Math.floor(u / 60000);
  return `${d}°${((u - d * 60000) / 1000).toFixed(3).padStart(6, "0")}' ${v >= 0 ? pos : neg}`;
}

function setShipStatus(text) {
  document.getElementById("shipStatus").textContent = text;
}

// point `km` along great-circle bearing `deg` (clockwise from north) from (lat, lon)
// Entity polylines are built asynchronously (web workers) and the viewer only redraws on
// request (requestRenderMode), so a new line would stay invisible until the next camera
// move. The async build itself only advances during a render, so keep requesting a render
// every frame (the clock ticks every frame even when nothing is drawn) until every entity
// is built, then one final render to draw it.
let entityRenderPending = false;
function renderSoon() {
  entityRenderPending = true;
  viewer.scene.requestRender();
}
viewer.clock.onTick.addEventListener(() => {
  if (!entityRenderPending) return;
  if (viewer.dataSourceDisplay.ready) entityRenderPending = false;
  viewer.scene.requestRender();
});

function destinationDeg(lat, lon, deg, km) {
  const R = 6371.0088, d = km / R, b = Cesium.Math.toRadians(deg);
  const p1 = Cesium.Math.toRadians(lat), l1 = Cesium.Math.toRadians(lon);
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [Cesium.Math.toDegrees(p2), Cesium.Math.toDegrees(l2)];
}

function setShipVector(key, deg, p, km, material) {
  if (deg == null) {
    if (ship[key]) viewer.entities.remove(ship[key]);
    ship[key] = null;
    return;
  }
  const [la, lo] = destinationDeg(p.lat, p.lon, deg, km);
  const positions = [shipPos(p.lon, p.lat), shipPos(lo, la)];
  if (!ship[key]) {
    ship[key] = viewer.entities.add({ polyline: { positions, width: 3, material, depthFailMaterial: material } });
  } else {
    ship[key].polyline.positions = positions;
  }
}

function clearShip() {
  if (ship.entity) viewer.entities.remove(ship.entity);
  if (ship.trackEntity) viewer.entities.remove(ship.trackEntity);
  for (const k of ["hdgEntity", "cogEntity"]) if (ship[k]) viewer.entities.remove(ship[k]);
  ship.entity = ship.trackEntity = ship.hdgEntity = ship.cogEntity = null;
  viewer.scene.requestRender();
}

// Mark what is on screen as stale (grey icon, label says so) -- used when polling fails, so a frozen
// marker can never look live.
function markShipStale(reason) {
  if (!ship.entity) return;
  const ageMin = ship.lastGood ? (Date.now() - ship.lastGood) / 60000 : NaN;
  ship.entity.billboard.image = shipIcon(true);
  const base = ship.last ? ship.last.split("\n")[0] : "Ship";
  ship.entity.label.text = `${base}\nNO UPDATE for ${Number.isFinite(ageMin) ? ageMin.toFixed(0) : "?"} min (${reason}) -- STALE`;
  ship.entity.label.fillColor = Cesium.Color.fromCssColorString("#c8d0d8");
  viewer.scene.requestRender();
}

async function pollShip() {
  const gen = ship.gen;
  let s;
  try {
    const r = await fetch("ship.json", { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    s = await r.json();
  } catch (err) {
    if (gen !== ship.gen || !document.getElementById("shipToggle").checked) return;
    setShipStatus(`Could not reach the viewer server (${err.message}). Showing the last fix as STALE.`);
    markShipStale(err.message);
    return;
  }
  if (gen !== ship.gen || !document.getElementById("shipToggle").checked) return; // toggled off meanwhile
  s.counts = s.counts || {};
  if (!s.enabled) {
    clearShip();
    setShipStatus("Ship feed is off. Start the viewer with:  python3 run_viewer.py --ship-feed");
    return;
  }
  const errs = Object.values(s.errors || {});
  if (!s.position) {
    clearShip();
    setShipStatus((errs.length ? errs.join("; ") + ". " : "") +
      `No position received yet (GPS lines ${s.counts.gps_lines}, heading lines ${s.counts.heading_lines}, other/ignored ${s.counts.ignored}). Next check in 1 min.`);
    return;
  }
  const p = s.position;
  // a real heading (HDT/THS/HDG) younger than 5 min wins; otherwise course over ground, but only if it
  // is itself fresh and the ship is actually moving (COG at < 1 kn is noise)
  const h = s.heading && s.heading.age_s <= SHIP_STALE_S ? s.heading : null;
  const mRaw = s.motion;
  const m = mRaw && mRaw.age_s <= SHIP_STALE_S && mRaw.sog_kn != null && mRaw.sog_kn >= SHIP_MIN_SOG_KN ? mRaw : null;
  const stale = p.age_s > SHIP_STALE_S;
  ship.lastGood = Date.now();
  const where = shipPos(p.lon, p.lat);
  const sogTxt = mRaw && mRaw.sog_kn != null && mRaw.age_s <= SHIP_STALE_S ? `, ${mRaw.sog_kn.toFixed(1)} kn` : "";
  const why = s.heading ? "heading stale" : "no heading feed";
  const hdgTxt = h ? `heading ${h.deg.toFixed(1)}° ${h.true ? "T" : "M (magnetic, not corrected)"}${sogTxt}`
    : m ? `COG ${m.cog.toFixed(1)}° T${sogTxt} (${why})`
      : `direction unknown (${why}${mRaw ? "; speed < 1 kn or COG stale" : ""})${sogTxt}`;
  const label = `Ship  ${fmtDegMin(p.lat, "N", "S")}  ${fmtDegMin(p.lon, "E", "W")}\n${hdgTxt}   fix ${Math.round(p.age_s)} s old${stale ? " (STALE)" : ""}`;
  if (!ship.entity) {
    ship.entity = viewer.entities.add({
      position: where,
      billboard: {
        image: shipIcon(stale),
        width: 30,
        height: 30,
        alignedAxis: Cesium.Cartesian3.UNIT_Z, // rotation measured on the map (from north)
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
      label: {
        text: label,
        font: "bold 13px sans-serif",
        fillColor: Cesium.Color.WHITE,
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 3,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        pixelOffset: new Cesium.Cartesian2(22, -18),
        horizontalOrigin: Cesium.HorizontalOrigin.LEFT,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
    });
  }
  ship.entity.position = where;
  ship.entity.billboard.image = shipIcon(stale || dirIsNull(h, m));
  ship.entity.label.fillColor = stale ? Cesium.Color.fromCssColorString("#c8d0d8") : Cesium.Color.WHITE;
  ship.last = label;
  // Cesium billboard rotation is counter-clockwise in radians; heading is clockwise from north
  const dirDeg = h ? h.deg : m ? m.cog : null;
  ship.entity.billboard.rotation = dirDeg != null ? -Cesium.Math.toRadians(dirDeg) : 0;
  ship.entity.label.text = label;
  // Direction vectors, 30 min ahead at the current speed (min 2 km): heading solid, COG dashed.
  // Both shown when both exist, so crab/drift angle is visible.
  const aheadKm = Math.max(2, (m && m.sog_kn != null ? m.sog_kn : 0) * 1.852 * 0.5);
  setShipVector("hdgEntity", h ? h.deg : null, p, aheadKm, new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString("#ff2d6f")));
  setShipVector("cogEntity", m ? m.cog : null, p, aheadKm,
    new Cesium.PolylineDashMaterialProperty({ color: Cesium.Color.WHITE, dashLength: 12 }));
  const pts = (s.track || []).map(([lon, lat]) => shipPos(lon, lat));
  if (pts.length >= 2) {
    if (!ship.trackEntity) {
      ship.trackEntity = viewer.entities.add({
        polyline: { positions: pts, width: 2, material: Cesium.Color.fromCssColorString("#ff2d6f").withAlpha(0.8),
                    depthFailMaterial: Cesium.Color.fromCssColorString("#ff2d6f").withAlpha(0.8) },
      });
    } else {
      ship.trackEntity.polyline.positions = pts;
    }
  }
  renderSoon();
  setShipStatus(`Fix from ${p.sentence}${p.utc ? " at " + p.utc + " UTC" : ""}, ${Math.round(p.age_s)} s old` +
    (h ? `; heading from ${h.sentence}` : m ? `; ${why}, arrow = course over ground (${m.sentence})`
      : `; ${why}, no usable course: arrow shown grey, pointing north`) +
    `. Lines show 30 min ahead (${aheadKm.toFixed(1)} km): solid pink = heading, dashed white = course over ground.` +
    ` Track: ${(s.track || []).length} pts (1/min). Next update in 1 min.`);
}

function dirIsNull(h, m) {
  return !h && !m;
}

document.getElementById("shipToggle").addEventListener("change", (e) => {
  if (ship.timer) clearInterval(ship.timer);
  ship.timer = null;
  ship.gen += 1; // any poll still in flight is now ignored
  if (e.target.checked) {
    setShipStatus("Checking for the ship feed...");
    pollShip();
    ship.timer = setInterval(pollShip, SHIP_POLL_MS);
  } else {
    clearShip();
    setShipStatus("");
  }
});

document.getElementById("shipFlyBtn").addEventListener("click", () => {
  if (!ship.entity) return;
  const c = Cesium.Cartographic.fromCartesian(ship.entity.position.getValue(Cesium.JulianDate.now()));
  viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromRadians(c.longitude, c.latitude, 60000), duration: 1.0 });
});

// =====================================================================
// Previous dredges (Site_Maps/Previous_Dredges_Compiled.csv, served at sites/)
// =====================================================================
// Same symbols as Site_Maps/make_previous_dredge_maps.py: shape = cruise; "recovery"
// colouring = green glass / light grey no glass / black X no rock (Dredge_Glass_Status_Map),
// "cruise" colouring = cruise colour, filled = glass, open = no glass, X = no rock
// (Permit_Dredges_with_Previous_Map). MV1007 on- to off-bottom tracks drawn as lines.
const PREV_CRUISES = {
  MV1007: { shape: "o", color: "#ff7f00", label: "MV1007 (2010)" },
  PLUME02: { shape: "D", color: "#f768a1", label: "PLUME02" },
  SO158: { shape: "P", color: "#00d5ff", label: "SO158" },
  TR164: { shape: "s", color: "#9be564", label: "TR164" },
  CTW: { shape: "<", color: "#ffd92f", label: "CTW" },
  DS: { shape: "p", color: "#e5c494", label: "DS" },
  ST7: { shape: ">", color: "#b3b3b3", label: "ST7" },
  NA06x: { shape: "h", color: "#fdbf6f", label: "NA062/NA063" },
  NZ: { shape: "v", color: "#cab2d6", label: "NZ" },
};
const PREV_GLASS_C = { glass: "#1a9850", "no glass": "#d9d9d9", "no rock": "#000000" };
const PREV_CLASSES = ["glass", "no glass", "no rock"];
const prev = {
  rows: null, colorBy: "glass", labels: false, tracks: true,
  cruiseOn: {}, classOn: { glass: true, "no glass": true, "no rock": true },
  billboards: null, labelCollection: null, trackEntities: [], iconCache: new Map(),
};

// quote-aware CSV (descriptions contain commas)
function parseCsvRows(text) {
  text = text.replace(/^\uFEFF/, "");
  const rows = [];
  let row = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') q = false;
      else cell += ch;
    } else if (ch === '"') q = true;
    else if (ch === ",") { row.push(cell); cell = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      if (row.some((c) => c !== "")) rows.push(row);
      row = [];
    } else cell += ch;
  }
  row.push(cell);
  if (row.some((c) => c !== "")) rows.push(row);
  const head = rows.shift();
  return rows.map((r) => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ""])));
}

function prevShapePath(g, shape, r) {
  const poly = (n, rot) => {
    for (let k = 0; k < n; k++) {
      const a = rot + (2 * Math.PI * k) / n;
      k ? g.lineTo(r * Math.sin(a), -r * Math.cos(a)) : g.moveTo(r * Math.sin(a), -r * Math.cos(a));
    }
    g.closePath();
  };
  g.beginPath();
  if (shape === "o") g.arc(0, 0, r, 0, 2 * Math.PI);
  else if (shape === "s") g.rect(-r * 0.85, -r * 0.85, 1.7 * r, 1.7 * r);
  else if (shape === "D") poly(4, 0);
  else if (shape === "v") poly(3, Math.PI);
  else if (shape === "<") poly(3, -Math.PI / 2);
  else if (shape === ">") poly(3, Math.PI / 2);
  else if (shape === "p") poly(5, 0);
  else if (shape === "h") poly(6, 0);
  else if (shape === "P") { // filled plus
    const a = r * 0.38;
    g.moveTo(-a, -r); g.lineTo(a, -r); g.lineTo(a, -a); g.lineTo(r, -a); g.lineTo(r, a); g.lineTo(a, a);
    g.lineTo(a, r); g.lineTo(-a, r); g.lineTo(-a, a); g.lineTo(-r, a); g.lineTo(-r, -a); g.lineTo(-a, -a);
    g.closePath();
  }
}

// One canvas per (cruise shape, recovery class, colour mode).
function prevIcon(cruise, cls, colorBy) {
  const c = PREV_CRUISES[cruise] || { shape: "o", color: "#ffffff" };
  const key = `${c.shape}|${c.color}|${cls}|${colorBy}`;
  if (prev.iconCache.has(key)) return prev.iconCache.get(key);
  const S = 24, cv = document.createElement("canvas");
  cv.width = cv.height = S;
  const g = cv.getContext("2d");
  g.translate(S / 2, S / 2);
  g.lineJoin = "round";
  if (cls === "no rock") { // X, black with white edge (recovery) or cruise colour (cruise)
    const r = 7;
    g.lineCap = "round";
    for (const [w, col] of [[6, colorBy === "glass" ? "#ffffff" : "#0b0b0b"], [3, colorBy === "glass" ? "#000000" : c.color]]) {
      g.lineWidth = w;
      g.strokeStyle = col;
      g.beginPath(); g.moveTo(-r, -r); g.lineTo(r, r); g.moveTo(r, -r); g.lineTo(-r, r); g.stroke();
    }
  } else {
    prevShapePath(g, c.shape, 10.5); // dark halo: tells previous dredges apart from the white-haloed planned sites
    g.fillStyle = "#151515";
    g.fill();
    prevShapePath(g, c.shape, 7.5);
    if (colorBy === "glass" || cls === "glass") {
      g.fillStyle = colorBy === "glass" ? PREV_GLASS_C[cls] : c.color;
      g.fill();
    } else { // open symbol in the cruise colour
      g.fillStyle = "#ffffff";
      g.fill();
      g.lineWidth = 3;
      g.strokeStyle = c.color;
      g.stroke();
    }
  }
  prev.iconCache.set(key, cv);
  return cv;
}

function setPrevStatus(text) {
  document.getElementById("prevStatus").textContent = text;
}

async function loadPrevDredges() {
  const r = await fetch("sites/Previous_Dredges_Compiled.csv", { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  prev.rows = parseCsvRows(await r.text()).map((d) => ({
    ...d,
    prevDredge: true,
    lat: parseFloat(d.lat), lon: parseFloat(d.lon), depth: parseFloat(d.depth),
    off_lat: parseFloat(d.off_lat), off_lon: parseFloat(d.off_lon),
  })).filter((d) => Number.isFinite(d.lat) && Number.isFinite(d.lon));
  for (const d of prev.rows) if (!(d.cruise in prev.cruiseOn)) prev.cruiseOn[d.cruise] = true;
  buildPrevFilters();
}

function buildPrevFilters() {
  const el = document.getElementById("prevFilters");
  el.innerHTML = "";
  const addRow = (icon, text, checked, onChange) => {
    const row = document.createElement("label");
    row.className = "track-legend-row prev-row";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = checked;
    cb.addEventListener("change", (e) => onChange(e.target.checked));
    const img = document.createElement("img");
    img.src = icon.toDataURL();
    img.className = "prev-swatch";
    const span = document.createElement("span");
    span.textContent = text;
    row.append(cb, img, span);
    el.appendChild(row);
  };
  const count = (f) => prev.rows.filter(f).length;
  const head = (t) => { const h = document.createElement("div"); h.className = "prev-head"; h.textContent = t; el.appendChild(h); };
  head("Recovery");
  for (const cls of PREV_CLASSES) {
    addRow(prevIcon("MV1007", cls, prev.colorBy), `${cls} (${count((d) => d.glass === cls)})`, prev.classOn[cls],
      (on) => { prev.classOn[cls] = on; placePrevDredges(); });
  }
  head("Cruise (shape)");
  for (const cruise of Object.keys(prev.cruiseOn)) {
    const n = count((d) => d.cruise === cruise);
    const ng = count((d) => d.cruise === cruise && d.glass === "glass");
    addRow(prevIcon(cruise, "glass", prev.colorBy), `${PREV_CRUISES[cruise]?.label || cruise}: ${n} (${ng} glass)`,
      prev.cruiseOn[cruise], (on) => { prev.cruiseOn[cruise] = on; placePrevDredges(); });
  }
}

// Stations are drawn at their logged on-bottom depth x the vertical exaggeration (same
// scaling as the meshes), not by sampleHeight(): that is one offscreen render per point
// (~0.1 s each on an integrated GPU, 10+ s for all 104). On the ~1 km GMRT basemap the
// rendered surface is a median 56 m off the logged depths (-504 to +232 m, n=24; the coarse
// grid smooths steep edifices), less on the detailed surveys. The markers ignore depth
// testing, so they stay visible either way.
// Only a station with no logged depth falls back to sampling the visible surface.
function prevSurfaceCartesian(lat, lon, depth) {
  const carto = Cesium.Cartographic.fromDegrees(lon, lat, 0);
  if (Number.isFinite(depth)) {
    carto.height = -depth * state.exaggeration;
    return { cartesian: Cesium.Cartographic.toCartesian(carto), onSurface: true };
  }
  let h;
  try { h = viewer.scene.sampleHeight(carto); } catch (e) { h = undefined; }
  const onSurface = h !== undefined && Number.isFinite(h);
  carto.height = onSurface ? h : 0;
  return { cartesian: Cesium.Cartographic.toCartesian(carto), onSurface };
}

function clearPrevDredges() {
  if (prev.billboards) prev.billboards.removeAll();
  if (prev.labelCollection) prev.labelCollection.removeAll();
  for (const e of prev.trackEntities) viewer.entities.remove(e);
  prev.trackEntities = [];
  viewer.scene.requestRender();
}

// resample=true when the surface changed (dataset toggled, exaggeration...). Each
// sampleHeight() is an offscreen render, so heights are cached per station and filter /
// colour / label clicks reuse them.
function placePrevDredges(resample = false) {
  if (resample && prev.rows) for (const d of prev.rows) d.carto = null;
  clearPrevDredges();
  if (!document.getElementById("prevToggle").checked || !prev.rows) return;
  if (!prev.billboards) prev.billboards = viewer.scene.primitives.add(new Cesium.BillboardCollection());
  if (!prev.labelCollection) prev.labelCollection = viewer.scene.primitives.add(new Cesium.LabelCollection());
  const shown = prev.rows.filter((d) => prev.cruiseOn[d.cruise] && prev.classOn[d.glass]);
  let off = 0;
  for (const d of shown) {
    if (!d.carto) {
      const s = prevSurfaceCartesian(d.lat, d.lon, d.depth);
      d.carto = s;
      d.onSurface = s.onSurface;
      // off-bottom end drawn at the on-bottom height (tracks are <~2 km; saves a sample)
      if (Number.isFinite(d.off_lat) && Number.isFinite(d.off_lon)) {
        const h = Cesium.Cartographic.fromCartesian(s.cartesian).height;
        d.carto.offCartesian = Cesium.Cartesian3.fromDegrees(d.off_lon, d.off_lat, h);
      }
    }
    const cartesian = d.carto.cartesian;
    if (!d.onSurface) off += 1;
    prev.billboards.add({
      position: cartesian, image: prevIcon(d.cruise, d.glass, prev.colorBy), width: 18, height: 18, id: d,
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
    });
    if (prev.labels) {
      prev.labelCollection.add({
        position: cartesian,
        text: `${d.cruise === "MV1007" ? "MV " : ""}${d.station}${Number.isFinite(d.depth) ? ` ${Math.round(d.depth)} m` : ""}`,
        font: "12px sans-serif", fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.BLACK, outlineWidth: 3,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cesium.Cartesian2(11, -9),
        horizontalOrigin: Cesium.HorizontalOrigin.LEFT, disableDepthTestDistance: Number.POSITIVE_INFINITY,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 250000), // hide when zoomed far out
      });
    }
    if (prev.tracks && d.carto.offCartesian) {
      prev.trackEntities.push(viewer.entities.add({
        polyline: {
          positions: [cartesian, d.carto.offCartesian], width: 3,
          material: Cesium.Color.fromCssColorString("#111111"),
          depthFailMaterial: Cesium.Color.fromCssColorString("#111111").withAlpha(0.6),
        },
      }));
    }
  }
  renderSoon();
  setPrevStatus(`${shown.length} of ${prev.rows.length} stations shown` +
    (off ? `; ${off} have no logged depth and no surface under them (drawn at sea level)` : "") +
    ". Hover or click a symbol for details.");
}

function prevTooltipText(d) {
  const lines = [`${d.cruise} ${d.station}: ${d.glass.toUpperCase()}`,
    `${fmtDegMin(d.lat, "N", "S")}  ${fmtDegMin(d.lon, "E", "W")}` +
    (Number.isFinite(d.depth) ? `   ${Math.round(d.depth)} m` : "")];
  if (d.location) lines.push(d.location);
  if (d.recovery) lines.push(`Recovery: ${d.recovery}`);
  if (d.description) lines.push(d.description);
  if (d.samples && d.samples !== d.station) lines.push(`Samples: ${d.samples}`);
  if (d.note) lines.push(`Note: ${d.note}`);
  if (d.source) lines.push(`Source: ${d.source}`);
  if (d.onSurface === false) lines.push("(no logged depth and no surface here: drawn at sea level)");
  return lines.join("\n");
}

document.getElementById("prevToggle").addEventListener("change", async (e) => {
  if (!e.target.checked) { clearPrevDredges(); setPrevStatus(""); return; }
  if (!prev.rows) {
    setPrevStatus("Loading previous dredges...");
    try {
      await loadPrevDredges();
    } catch (err) {
      setPrevStatus(`Could not load sites/Previous_Dredges_Compiled.csv (${err.message}). Start the viewer with run_viewer.py.`);
      e.target.checked = false;
      return;
    }
  }
  placePrevDredges();
});
for (const el of document.querySelectorAll('input[name="prevColor"]')) {
  el.addEventListener("change", (e) => {
    prev.colorBy = e.target.value;
    if (prev.rows) { buildPrevFilters(); placePrevDredges(); }
  });
}
document.getElementById("prevLabels").addEventListener("change", (e) => { prev.labels = e.target.checked; placePrevDredges(); });
document.getElementById("prevTracks").addEventListener("change", (e) => { prev.tracks = e.target.checked; placePrevDredges(); });

// =====================================================================
// Dredge lines (planning): MT_dredging_coords/DredgeLines.csv, one on-bottom track per site
// =====================================================================
const DL_COLS = ["site", "start_lat", "start_lon", "end_lat", "end_lon", "length_m", "azimuth_deg", "start_depth_m",
  "end_depth_m", "site_depth_m", "mean_slope_deg", "max_slope_deg", "grid", "grid_cell_m", "source", "note"];
const dl = { rows: [], entities: [], armed: false, first: null, dirty: false };

function setDlStatus(t) { document.getElementById("dlStatus").textContent = t; }
const dlNum = (v) => (v === "" || v == null ? NaN : Number(v));

async function dlLoad() {
  const r = await fetch("sites/DredgeLines.csv", { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status} (no DredgeLines.csv yet? run Site_Maps/dredge_plan.py seed)`);
  dlSetRows(parseCsvRows(await r.text()));
}

function dlSetRows(rows) {
  dl.rows = rows.map((r) => Object.fromEntries(DL_COLS.map((c) => [c, r[c] ?? ""])));
  const sel = document.getElementById("dlSite");
  const keep = sel.value;
  sel.innerHTML = dl.rows.map((r) => `<option value="${r.site}">D${r.site}</option>`).join("");
  if (keep) sel.value = keep;
  dlDraw();
  dlList();
}

function dlColor(r) {
  if (String(r.source).toLowerCase() === "manual") return "#ff3cf0";
  return /FLAT/.test(r.note) ? "#ffe14d" : "#ff8c1a";
}

function dlCartesian(lon, lat, depth) {
  return Cesium.Cartesian3.fromDegrees(lon, lat, Number.isFinite(depth) ? -depth * state.exaggeration : 0);
}

function dlClear() {
  for (const e of dl.entities) viewer.entities.remove(e);
  dl.entities = [];
}

function dlDraw() {
  dlClear();
  if (!document.getElementById("dlToggle").checked) { viewer.scene.requestRender(); return; }
  const selSite = document.getElementById("dlSite").value;
  for (const r of dl.rows) {
    const [la0, lo0, la1, lo1] = [r.start_lat, r.start_lon, r.end_lat, r.end_lon].map(dlNum);
    if (![la0, lo0, la1, lo1].every(Number.isFinite)) continue;
    const a = dlCartesian(lo0, la0, dlNum(r.start_depth_m));
    const b = dlCartesian(lo1, la1, dlNum(r.end_depth_m));
    const col = Cesium.Color.fromCssColorString(dlColor(r));
    const wide = String(r.site) === selSite ? 16 : 11;
    const mat = new Cesium.PolylineArrowMaterialProperty(col);
    dl.entities.push(viewer.entities.add({ polyline: { positions: [a, b], width: wide, material: mat, depthFailMaterial: mat } }));
    for (const [p, c] of [[a, "#2bd14f"], [b, "#ff3b3b"]]) {
      dl.entities.push(viewer.entities.add({
        position: p,
        point: { pixelSize: 8, color: Cesium.Color.fromCssColorString(c), outlineColor: Cesium.Color.BLACK,
                 outlineWidth: 1.5, disableDepthTestDistance: Number.POSITIVE_INFINITY },
      }));
    }
    const len = dlNum(r.length_m), az = dlNum(r.azimuth_deg);
    const d0 = dlNum(r.start_depth_m), d1 = dlNum(r.end_depth_m);
    dl.entities.push(viewer.entities.add({
      position: Cesium.Cartesian3.midpoint(a, b, new Cesium.Cartesian3()),
      label: {
        text: `D${r.site}  ${Number.isFinite(d0) ? Math.round(d0) : "?"}→${Number.isFinite(d1) ? Math.round(d1) : "?"} m` +
          `  ${Number.isFinite(len) ? (len / 1000).toFixed(2) : "?"} km  ${Number.isFinite(az) ? az.toFixed(0) : "?"}°`,
        font: "bold 12px sans-serif", fillColor: col, outlineColor: Cesium.Color.BLACK, outlineWidth: 3,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cesium.Cartesian2(10, -10),
        horizontalOrigin: Cesium.HorizontalOrigin.LEFT, disableDepthTestDistance: Number.POSITIVE_INFINITY,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 300000),
      },
    }));
  }
  renderSoon();
}

function dlList() {
  const el = document.getElementById("dlList");
  const f = (v, d = 0) => (Number.isFinite(dlNum(v)) ? dlNum(v).toFixed(d) : "");
  el.innerHTML = `<table><tr><th>site</th><th>start→end m</th><th>km</th><th>az</th><th>slope</th><th></th></tr>` +
    dl.rows.map((r) => `<tr data-site="${r.site}" title="${(r.note || "").replace(/"/g, "'")} [${r.grid} ${f(r.grid_cell_m)} m]">` +
      `<td>D${r.site}</td><td>${f(r.start_depth_m)}→${f(r.end_depth_m)}</td><td>${(dlNum(r.length_m) / 1000).toFixed(2)}</td>` +
      `<td>${f(r.azimuth_deg)}°</td><td>${f(r.mean_slope_deg, 1)}°</td>` +
      `<td style="color:${dlColor(r)}">${String(r.source).toLowerCase() === "manual" ? "hand" : /FLAT/.test(r.note) ? "flat" : "auto"}</td></tr>`).join("") +
    "</table>";
  for (const tr of el.querySelectorAll("tr[data-site]")) {
    tr.addEventListener("click", () => { document.getElementById("dlSite").value = tr.dataset.site; dlDraw(); dlFly(); });
  }
}

function dlSelected() {
  return dl.rows.find((r) => String(r.site) === document.getElementById("dlSite").value);
}

function dlFly() {
  const r = dlSelected();
  if (!r) return;
  const lat = (dlNum(r.start_lat) + dlNum(r.end_lat)) / 2, lon = (dlNum(r.start_lon) + dlNum(r.end_lon)) / 2;
  viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(lon, lat, 9000), duration: 1.0 });
}

// geodesic length (m) and initial bearing (deg from true N) on the WGS-84 ellipsoid
function dlGeodesic(lat0, lon0, lat1, lon1) {
  const g = new Cesium.EllipsoidGeodesic(Cesium.Cartographic.fromDegrees(lon0, lat0), Cesium.Cartographic.fromDegrees(lon1, lat1));
  return { length: g.surfaceDistance, azimuth: (Cesium.Math.toDegrees(g.startHeading) + 360) % 360 };
}

function dlPlacePoint(lon, lat, depth) {
  const r = dl.rows.find((x) => String(x.site) === String(dl.site)); // the site chosen when Draw was pressed
  if (!r) { dl.armed = false; return; }
  if (!dl.first) {
    dl.first = { lon, lat, depth };
    setDlStatus(`D${r.site}: start set (${lat.toFixed(5)}, ${lon.toFixed(5)}, ~${Math.round(depth)} m). Now click the END of the tow.`);
    return;
  }
  const s = dl.first, g = dlGeodesic(s.lat, s.lon, lat, lon);
  if (!(g.length >= 20)) {
    setDlStatus(`D${r.site}: end is only ${g.length.toFixed(0)} m from the start -- click the END somewhere else.`);
    return;
  }
  Object.assign(r, {
    start_lat: s.lat.toFixed(6), start_lon: s.lon.toFixed(6), end_lat: lat.toFixed(6), end_lon: lon.toFixed(6),
    length_m: g.length.toFixed(1), azimuth_deg: g.azimuth.toFixed(1), start_depth_m: Math.round(s.depth),
    end_depth_m: Math.round(depth), mean_slope_deg: (Math.atan(Math.abs(depth - s.depth) / g.length) * 180 / Math.PI).toFixed(1),
    max_slope_deg: "", source: "manual", note: "drawn in viewer (depths from the picked surface until saved)",
  });
  dl.armed = false;
  dl.first = null;
  dl.dirty = true;
  setDlStatus(`D${r.site}: new line ${(g.length / 1000).toFixed(2)} km at ${g.azimuth.toFixed(0)}°. Not saved yet -- "Save to repo" to keep it.`);
  dlDraw();
  dlList();
}

function dlCsv() {
  const q = (v) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  return DL_COLS.join(",") + "\n" + dl.rows.map((r) => DL_COLS.map((c) => q(r[c] ?? "")).join(",")).join("\n") + "\n";
}

function dlDownload(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

const xmlEsc = (s) => String(s).replace(/[<>&"']/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" }[c]));

const dlTag = (site) => `D${String(site).padStart(2, "0")}`;

function dlGpx() {
  const pts = (r) => [["S", r.start_lat, r.start_lon, r.start_depth_m], ["E", r.end_lat, r.end_lon, r.end_depth_m]];
  return `<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="AT53-04 viewer" xmlns="http://www.topografix.com/GPX/1/1">\n` +
    dl.rows.map((r) => pts(r).map(([k, la, lo, d]) => `<wpt lat="${la}" lon="${lo}"><name>${dlTag(r.site)}${k}</name><desc>${k === "S" ? "start" : "end"} of tow, ${d} m</desc></wpt>`).join("\n")).join("\n") + "\n" +
    dl.rows.map((r) => `<rte><name>${dlTag(r.site)}</name><desc>${xmlEsc(`${r.length_m} m at ${r.azimuth_deg} deg, ${r.start_depth_m}->${r.end_depth_m} m. ${r.note || ""}`)}</desc>` +
      pts(r).map(([k, la, lo, d]) => `<rtept lat="${la}" lon="${lo}"><name>${dlTag(r.site)}${k}</name></rtept>`).join("") + "</rte>").join("\n") +
    "\n</gpx>\n";
}

function dlKml() {
  return `<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>AT53-04 dredge lines</name>\n` +
    `<Style id="l"><LineStyle><color>ff6f2dff</color><width>4</width></LineStyle></Style>\n` +
    dl.rows.map((r) => `<Placemark><name>${dlTag(r.site)}</name><description>${xmlEsc(`${r.length_m} m at ${r.azimuth_deg} deg, ${r.start_depth_m}->${r.end_depth_m} m (${r.source}). ${r.note || ""}`)}</description>` +
      `<styleUrl>#l</styleUrl><LineString><coordinates>${r.start_lon},${r.start_lat},0 ${r.end_lon},${r.end_lat},0</coordinates></LineString></Placemark>\n` +
      `<Placemark><name>${dlTag(r.site)}S</name><Point><coordinates>${r.start_lon},${r.start_lat},0</coordinates></Point></Placemark>\n` +
      `<Placemark><name>${dlTag(r.site)}E</name><Point><coordinates>${r.end_lon},${r.end_lat},0</coordinates></Point></Placemark>`).join("\n") +
    "\n</Document></kml>\n";
}

document.getElementById("dlToggle").addEventListener("change", async (e) => {
  if (e.target.checked && !dl.rows.length) {
    try { await dlLoad(); setDlStatus(`${dl.rows.length} lines loaded.`); } catch (err) { setDlStatus(`Could not load: ${err.message}`); }
  }
  dlDraw();
});
document.getElementById("dlSite").addEventListener("change", () => dlDraw());
document.getElementById("dlFlyBtn").addEventListener("click", dlFly);
document.getElementById("dlDrawBtn").addEventListener("click", () => {
  const r = dlSelected();
  if (!r) { setDlStatus("Tick the layer and pick a site first."); return; }
  dl.armed = true;
  dl.first = null;
  dl.site = r.site;
  if (state.crossSection.armed) { state.crossSection.armed = false; setCrossSectionStatus("Cross-section picking cancelled (drawing a dredge line)."); }
  setDlStatus(`D${r.site}: click the START of the tow on the surface (deep end, usually), then the END.`);
});
document.getElementById("dlReverseBtn").addEventListener("click", () => {
  const r = dlSelected();
  if (!r) return;
  [r.start_lat, r.end_lat] = [r.end_lat, r.start_lat];
  [r.start_lon, r.end_lon] = [r.end_lon, r.start_lon];
  [r.start_depth_m, r.end_depth_m] = [r.end_depth_m, r.start_depth_m];
  const gr = dlGeodesic(dlNum(r.start_lat), dlNum(r.start_lon), dlNum(r.end_lat), dlNum(r.end_lon));
  r.azimuth_deg = Number.isFinite(gr.azimuth) ? gr.azimuth.toFixed(1) : "";
  r.source = "manual";
  dl.dirty = true;
  setDlStatus(`D${r.site} reversed. Not saved yet.`);
  dlDraw();
  dlList();
});
document.getElementById("dlSaveBtn").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  if (btn.disabled) return;
  btn.disabled = true; // one save at a time (the server also serialises saves)
  setDlStatus("Saving and recomputing depths from the grids...");
  try {
    const r = await fetch("sites/DredgeLines.csv", { method: "POST", body: dlCsv(), headers: { "Content-Type": "text/csv" } });
    const text = await r.text();
    if (!r.ok) throw new Error(text);
    dlSetRows(parseCsvRows(text));
    dl.dirty = false;
    setDlStatus(`MT_dredging_coords/DredgeLines.csv ${r.headers.get("X-Save-Note") || "saved"}.`);
  } catch (err) {
    setDlStatus(`Not saved: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
});
document.getElementById("dlCsvBtn").addEventListener("click", () => dlDownload("DredgeLines.csv", dlCsv(), "text/csv"));
document.getElementById("dlGpxBtn").addEventListener("click", () => dlDownload("DredgeLines.gpx", dlGpx(), "application/gpx+xml"));
document.getElementById("dlKmlBtn").addEventListener("click", () => dlDownload("DredgeLines.kml", dlKml(), "application/vnd.google-earth.kml+xml"));
window.addEventListener("beforeunload", (e) => { if (dl.dirty) { e.preventDefault(); e.returnValue = ""; } });

// =====================================================================
// Native-resolution GeoTIFF export (run_viewer.py /export_native -> scripts/native_render.py)
// =====================================================================
async function nativeInit() {
  const sel = document.getElementById("nativeDs");
  try {
    const j = await (await fetch("native_datasets", { cache: "no-store" })).json();
    if (j.error) throw new Error(j.error);
    sel.innerHTML = j.datasets.map((d) => `<option value="${d.id}">${d.label} (EPSG:${d.epsg})</option>`).join("");
    nativeInit.viewerIds = j.viewer_ids;
  } catch (err) {
    sel.innerHTML = "<option value=''>unavailable</option>";
    document.getElementById("nativeStatus").textContent = `Native export unavailable: ${err.message}`;
  }
}

// default the dropdown to the finest checked layer that has a native source
function nativeSuggest() {
  if (nativeSuggest.userChose) return; // never override what the user picked
  const ids = nativeInit.viewerIds || {};
  const vis = state.order.filter((id) => state.datasets[id].visible && ids[id]);
  if (!vis.length) return;
  const best = vis.reduce((a, b) => ((state.datasets[a].meta?.native_resolution_m ?? 1e9) <=
    (state.datasets[b].meta?.native_resolution_m ?? 1e9) ? a : b));
  document.getElementById("nativeDs").value = ids[best];
}

// viewer palette key -> matplotlib/cmocean/cmcrameri name with the same orientation
// (endpoints checked: viewer stop t=0 == colormap(0))
const PALETTE_TO_CMAP = { deep: "cmo.deep_r", haline: "cmo.haline", ice: "cmo.ice", dense: "cmo.dense_r", oslo: "cmc.oslo",
  batlow: "cmc.batlow", viridis: "viridis", cividis: "cividis", turbo: "turbo", spectral: "Spectral", greys: "Greys_r", slope: "YlOrRd" };

// the colour map chosen in the legend for a viewer layer that shows this native dataset (if any)
function nativeCmapFor(ds) {
  const ids = nativeInit.viewerIds || {};
  const vid = state.order.find((id) => ids[id] === ds && state.datasets[id].palette && state.datasets[id].visible) ||
    state.order.find((id) => ids[id] === ds && state.datasets[id].palette);
  if (!vid) return { cmap: "", slope: false };
  const d = state.datasets[vid];
  let name = PALETTE_TO_CMAP[d.palette] || "";
  if (name && d.paletteReverse) name = name.endsWith("_r") ? name.slice(0, -2) : `${name}_r`;
  return { cmap: name, slope: isSlopePalette(d), from: vid };
}

document.getElementById("nativeDs").addEventListener("change", () => { nativeSuggest.userChose = true; });

document.getElementById("nativeExportBtn").addEventListener("click", async () => {
  const st = document.getElementById("nativeStatus");
  const ds = document.getElementById("nativeDs").value;
  if (!ds) return;
  let b = state.geoExport.mode === "select" ? state.geoExport.bbox : null;
  let what = "selected region";
  if (!b) {
    const r = viewer.camera.computeViewRectangle();
    if (!r) { st.textContent = "Can't work out the current view; draw a region instead."; return; }
    b = { west: Cesium.Math.toDegrees(r.west), east: Cesium.Math.toDegrees(r.east),
          south: Cesium.Math.toDegrees(r.south), north: Cesium.Math.toDegrees(r.north) };
    what = "current view";
  }
  if (b.east - b.west > 2 || b.north - b.south > 2 || !(b.east > b.west)) {
    st.textContent = `The ${what} is ${(b.east - b.west).toFixed(2)} x ${(b.north - b.south).toFixed(2)} deg; the limit is 2 deg on a side. ` +
      "Zoom in, or use Select region + Draw rectangle. Whole datasets are already in Native_Maps/.";
    return;
  }
  const pal = nativeCmapFor(ds);
  const q = new URLSearchParams({ ds, w: b.west.toFixed(6), e: b.east.toFixed(6), s: b.south.toFixed(6), n: b.north.toFixed(6),
    nav: document.getElementById("nativeNav").checked ? "1" : "0", wgs84: document.getElementById("nativeWgs84").checked ? "1" : "0",
    slope: document.getElementById("nativeSlope").checked || pal.slope ? "1" : "0" });
  if (pal.cmap && !pal.slope) q.set("cmap", pal.cmap);
  st.textContent = (pal.cmap ? `Colour map ${pal.cmap} (from the legend of ${pal.from}). ` : "") +
    `Cutting ${ds} at native resolution over the ${what} (${b.south.toFixed(3)}..${b.north.toFixed(3)} N, ${b.west.toFixed(3)}..${b.east.toFixed(3)} E)...`;
  const t0 = performance.now();
  try {
    const r = await fetch(`export_native?${q}`);
    if (!r.ok) throw new Error(await r.text());
    const blob = await r.blob();
    const name = (r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || "native_export.zip";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    st.textContent = `Downloaded ${name} (${(blob.size / 1e6).toFixed(1)} MB) in ${((performance.now() - t0) / 1000).toFixed(0)} s.`;
  } catch (err) {
    st.textContent = `Export failed: ${err.message}`;
  }
});

// ---- dataset manifest ----
async function init() {
  buildTrackLegend();
  nativeInit().then(nativeSuggest);
  setLoading(true, "Loading dataset list…");
  const manifest = await (await fetch("data/manifest.json")).json();
  buildDatasetRows(manifest);
  prefetchAllMeta(); // fire and forget -- fills in resolution text as each arrives
  if (manifest.datasets.length > 0) {
    await setDatasetVisible(manifest.datasets[0].id, true);
  } else {
    setLoading(true, "No datasets found in data/manifest.json");
  }
}

init().catch((err) => {
  console.error(err);
  setLoading(true, `Failed to load: ${err.message}`);
});
