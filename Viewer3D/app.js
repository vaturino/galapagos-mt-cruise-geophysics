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
  trackPoints: [], // { lonDeg, latDeg, label, cartesian }
  trackPointCollection: null,
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
      `${label}: ${d.meta.vertex_count.toLocaleString()} v, ~${Math.round(d.meta.effective_resolution_m)} m posts, ` +
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
function formatDsMeta(meta, loaded) {
  let s = `~${Math.round(meta.effective_resolution_m)} m resolution · ${elevWord(meta.z_range_m)} ${meta.z_range_m[0].toFixed(0)} to ${meta.z_range_m[1].toFixed(0)} m`;
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
    await loadDataset(id); // loadDataset() clears the loading overlay itself
  } else {
    if (d.primitive) d.primitive.show = visible;
    setLoading(false);
  }
  viewer.scene.requestRender();
  updateStats();
  updateLegend();
  placeTrackPoints();
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
  const cartesian = viewer.scene.pickPosition(movement.position);
  const box = document.getElementById("pickBox");
  if (!Cesium.defined(cartesian)) {
    box.textContent = "No surface at that point -- click on the mesh.";
    return;
  }
  const carto = Cesium.Cartographic.fromCartesian(cartesian);
  const lonDeg = Cesium.Math.toDegrees(carto.longitude);
  const latDeg = Cesium.Math.toDegrees(carto.latitude);
  const trueDepth = carto.height / state.exaggeration;
  box.textContent = `lon ${lonDeg.toFixed(5)}, lat ${latDeg.toFixed(5)}\ndepth (approx, from picked surface): ${trueDepth.toFixed(0)} m`;
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

// Which colour source (and its legend metadata) a dataset is actually
// rendered with right now -- mirrors buildGeometry()'s own fallback so the
// legend always matches what's on screen.
function pickColorSource(d) {
  const useBackscatter = state.colorMode === "backscatter" && d.sections && d.sections.color_backscatter;
  return useBackscatter ? d.sections.color_backscatter : d.sections.color_depth;
}

function pickColorMeta(d) {
  const useBackscatter = state.colorMode === "backscatter" && d.meta.has_backscatter;
  if (useBackscatter) {
    return { kind: "backscatter", ramp: d.meta.backscatter_colormap_stops, range: d.meta.backscatter_range, unit: "" };
  }
  // A geophysics layer (build_geophysics_drape.py) colours vertices by a
  // value that isn't the mesh's own elevation, so it carries its own
  // legend_range/legend_units/legend_kind rather than reusing z_range_m
  // (which for those layers is the *terrain* elevation used only for 3D
  // draping, not what the colour ramp is keyed to). Any dataset without
  // these fields falls back to the original depth-in-metres behaviour.
  const range = d.meta.legend_range || d.meta.z_range_m;
  const unit = d.meta.legend_units != null ? d.meta.legend_units : " m";
  const kind = d.meta.legend_kind || "depth";
  return { kind, ramp: d.meta.colormap_stops, range, unit };
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
    const rgb =
      colorMeta.ramp.domain === "absolute_m"
        ? sampleGlobeColorJS(val, colorMeta.ramp.ocean, colorMeta.ramp.land)
        : sampleRelativeColorJS(t, colorMeta.ramp.stops);
    grad.addColorStop(t, `rgb(${rgb[0] | 0},${rgb[1] | 0},${rgb[2] | 0})`);
  }
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
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

    const row = document.createElement("div");
    row.className = "legend-row";

    const title = document.createElement("div");
    title.className = "legend-title";
    title.textContent = `${d.meta.label || d.manifestEntry.label} — ${colorMeta.kind}`;

    const canvas = document.createElement("canvas");
    canvas.width = 232;
    canvas.height = 14;
    canvas.className = "legend-bar";
    drawLegendCanvas(canvas, colorMeta);

    const labels = document.createElement("div");
    labels.className = "legend-labels";
    const unit = colorMeta.unit != null ? colorMeta.unit : (colorMeta.kind === "backscatter" ? "" : " m");
    labels.innerHTML = `<span>${colorMeta.range[0].toFixed(1)}${unit}</span><span>${colorMeta.range[1].toFixed(1)}${unit}</span>`;

    row.appendChild(title);
    row.appendChild(canvas);
    row.appendChild(labels);
    container.appendChild(row);
  }
  if (!any) {
    container.innerHTML = '<div class="legend-empty">No datasets shown.</div>';
  }
}

// =====================================================================
// Track points (CSV upload, placed on the current seafloor surface)
// =====================================================================

function parseTrackCSV(text) {
  const lines = text
    .split(/\r\n|\n|\r/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
  if (lines.length === 0) return { points: [], warning: "Empty file." };

  const splitLine = (l) => l.split(",").map((c) => c.trim().replace(/^"|"$/g, ""));
  const header = splitLine(lines[0]).map((h) => h.toLowerCase());
  let latIdx = header.findIndex((h) => h.includes("lat"));
  let lonIdx = header.findIndex((h) => h.includes("lon"));
  let labelIdx = header.findIndex((h) => ["name", "label", "id", "station"].some((k) => h.includes(k)));
  let startRow = 1;
  let warning = null;
  if (latIdx === -1 || lonIdx === -1) {
    latIdx = 0;
    lonIdx = 1;
    labelIdx = -1;
    startRow = 0;
    warning = 'No lat/lon header recognised -- assumed column 1 = latitude, column 2 = longitude.';
  }

  const points = [];
  for (let i = startRow; i < lines.length; i++) {
    const cols = splitLine(lines[i]);
    const lat = parseFloat(cols[latIdx]);
    const lon = parseFloat(cols[lonIdx]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
    const label = labelIdx >= 0 && cols[labelIdx] ? cols[labelIdx] : `pt${points.length + 1}`;
    points.push({ latDeg: lat, lonDeg: lon, label });
  }
  return { points, warning };
}

function placeTrackPoints() {
  if (!state.trackPointCollection) {
    state.trackPointCollection = viewer.scene.primitives.add(new Cesium.PointPrimitiveCollection());
  }
  state.trackPointCollection.removeAll();
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
    state.trackPointCollection.add({
      position: pt.cartesian,
      color: Cesium.Color.fromCssColorString("#ff2fd6"),
      outlineColor: Cesium.Color.WHITE,
      outlineWidth: 1.5,
      pixelSize: 9,
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

document.getElementById("trackCsvInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const text = await file.text();
  const { points, warning } = parseTrackCSV(text);
  state.trackPoints = points;
  const statusEl = document.getElementById("trackStatus");
  statusEl.textContent = warning ? `${warning} (${points.length} point(s) parsed)` : `${points.length} point(s) loaded from ${file.name}.`;
  placeTrackPoints();
});

document.getElementById("clearTrackBtn").addEventListener("click", () => {
  state.trackPoints = [];
  if (state.trackPointCollection) state.trackPointCollection.removeAll();
  document.getElementById("trackStatus").textContent = "";
  document.getElementById("trackCsvInput").value = "";
  viewer.scene.requestRender();
});

// =====================================================================
// GeoTIFF export (composited from every checked dataset's own vertex grid,
// not a screen capture -- so it's a true equirectangular raster, not an
// oblique 3D snapshot)
// =====================================================================

const MAX_EXPORT_DIM = 6000;

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

// Minimal but valid GeoTIFF writer: uncompressed RGBA, single strip,
// WGS84 geographic CRS via GeoKeys. No external library -- TIFF is a
// plain, well-documented binary format and this only needs a handful of
// tags to be readable by GDAL/QGIS/GeoMapApp.
function writeGeoTIFF(width, height, rgba, west, north, dlonDeg, dlatDeg) {
  const TYPE_SHORT = 3;
  const TYPE_LONG = 4;
  const TYPE_DOUBLE = 12;

  function u16arr(vals) {
    const b = new Uint8Array(vals.length * 2);
    const dv = new DataView(b.buffer);
    vals.forEach((v, i) => dv.setUint16(i * 2, v, true));
    return b;
  }
  function u32arr(vals) {
    const b = new Uint8Array(vals.length * 4);
    const dv = new DataView(b.buffer);
    vals.forEach((v, i) => dv.setUint32(i * 4, v, true));
    return b;
  }
  function f64arr(vals) {
    const b = new Uint8Array(vals.length * 8);
    const dv = new DataView(b.buffer);
    vals.forEach((v, i) => dv.setFloat64(i * 8, v, true));
    return b;
  }

  const imageDataOffset = 8; // right after the 8-byte header
  const imageDataSize = rgba.length;

  const entries = [];
  function addEntry(tag, type, count, bytes) {
    entries.push({ tag, type, count, bytes });
  }

  addEntry(256, TYPE_LONG, 1, u32arr([width])); // ImageWidth
  addEntry(257, TYPE_LONG, 1, u32arr([height])); // ImageLength
  addEntry(258, TYPE_SHORT, 4, u16arr([8, 8, 8, 8])); // BitsPerSample
  addEntry(259, TYPE_SHORT, 1, u16arr([1])); // Compression = none
  addEntry(262, TYPE_SHORT, 1, u16arr([2])); // PhotometricInterpretation = RGB
  addEntry(273, TYPE_LONG, 1, u32arr([imageDataOffset])); // StripOffsets
  addEntry(277, TYPE_SHORT, 1, u16arr([4])); // SamplesPerPixel (RGBA)
  addEntry(278, TYPE_LONG, 1, u32arr([height])); // RowsPerStrip (single strip)
  addEntry(279, TYPE_LONG, 1, u32arr([imageDataSize])); // StripByteCounts
  addEntry(284, TYPE_SHORT, 1, u16arr([1])); // PlanarConfiguration = chunky
  addEntry(338, TYPE_SHORT, 1, u16arr([2])); // ExtraSamples = unassociated alpha
  addEntry(33550, TYPE_DOUBLE, 3, f64arr([dlonDeg, dlatDeg, 0])); // ModelPixelScaleTag
  addEntry(33922, TYPE_DOUBLE, 6, f64arr([0, 0, 0, west, north, 0])); // ModelTiepointTag (pixel 0,0 -> west,north)

  // GeoKeyDirectory: header [KeyDirVersion, KeyRevision, MinorRevision, NumKeys]
  // then one [KeyID, TIFFTagLocation, Count, Value] group per key. Location 0
  // + Count 1 means the value is inlined in the last field.
  const geoKeys = [
    1, 1, 0, 3,
    1024, 0, 1, 2, // GTModelTypeGeoKey = 2 (Geographic)
    1025, 0, 1, 1, // GTRasterTypeGeoKey = 1 (RasterPixelIsArea)
    2048, 0, 1, 4326, // GeographicTypeGeoKey = EPSG:4326 (WGS84)
  ];
  addEntry(34735, TYPE_SHORT, geoKeys.length, u16arr(geoKeys));

  entries.sort((a, b) => a.tag - b.tag); // TIFF requires ascending tag order

  const numEntries = entries.length;
  const ifdOffset = imageDataOffset + imageDataSize;
  const ifdSize = 2 + numEntries * 12 + 4;
  let externalOffset = ifdOffset + ifdSize;
  for (const e of entries) {
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

  bytes.set(rgba, imageDataOffset);

  let p = ifdOffset;
  dv.setUint16(p, numEntries, true);
  p += 2;
  for (const e of entries) {
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

async function exportGeoTiff() {
  const statusEl = document.getElementById("exportStatus");
  const visible = state.order.map((id) => state.datasets[id]).filter((d) => d.loaded && d.visible);
  if (visible.length === 0) {
    statusEl.textContent = "No datasets are checked -- nothing to export.";
    return;
  }

  setLoading(true, "Building GeoTIFF…");
  await new Promise((r) => setTimeout(r, 20)); // let the loading text paint first

  let west = Infinity;
  let east = -Infinity;
  let south = Infinity;
  let north = -Infinity;
  let finestDlon = Infinity;
  let finestDlat = Infinity;
  for (const d of visible) {
    const b = d.meta.bbox;
    west = Math.min(west, b.lon_min);
    east = Math.max(east, b.lon_max);
    south = Math.min(south, b.lat_min);
    north = Math.max(north, b.lat_max);
    finestDlon = Math.min(finestDlon, d.meta.dlon_deg);
    finestDlat = Math.min(finestDlat, d.meta.dlat_deg);
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

  const rasters = visible.map((d) => buildDatasetRaster(d));
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

  let stamped = 0;
  for (const pt of state.trackPoints) {
    if (!pt.cartesian) continue;
    const outCol = Math.round((pt.lonDeg - west) / outDlon);
    const outRow = Math.round((north - pt.latDeg) / outDlat);
    for (let dr = -2; dr <= 2; dr++) {
      for (let dc = -2; dc <= 2; dc++) {
        const rr = outRow + dr;
        const cc = outCol + dc;
        if (rr < 0 || rr >= outH || cc < 0 || cc >= outW) continue;
        const idx = (rr * outW + cc) * 4;
        outRGBA[idx] = 255;
        outRGBA[idx + 1] = 0;
        outRGBA[idx + 2] = 230;
        outRGBA[idx + 3] = 255;
      }
    }
    stamped += 1;
  }

  const blob = writeGeoTIFF(outW, outH, outRGBA, west, north, outDlon, outDlat);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `viewer3d_export_${Date.now()}.tif`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 30000);

  setLoading(false);
  statusEl.textContent =
    `Exported ${outW}×${outH} px (~${(outDlon * 111320).toFixed(0)} m/px), ${visible.length} layer(s)` +
    (stamped > 0 ? `, ${stamped} track point(s) stamped in` : "") +
    `. Saved via your browser's download.`;
}

document.getElementById("exportGeoTiffBtn").addEventListener("click", () => {
  exportGeoTiff().catch((err) => {
    console.error(err);
    document.getElementById("exportStatus").textContent = `Export failed: ${err.message}`;
    setLoading(false);
  });
});

// ---- dataset manifest ----
async function init() {
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
