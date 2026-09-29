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
  trackPoints: [], // { lonDeg, latDeg, label, status, kind, cartesian }
  trackPointCollection: null, // Cesium.PointPrimitiveCollection -- "dredge" points (plain circles)
  trackBillboardCollection: null, // Cesium.BillboardCollection -- "mt" points (diamond icon, no other shape option on a PointPrimitive)
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
    await loadDataset(id); // loadDataset() clears the loading overlay itself
  } else {
    if (d.primitive) d.primitive.show = visible;
    setLoading(false);
  }
  viewer.scene.requestRender();
  updateStats();
  updateLegend();
  placeTrackPoints();
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

  // Cross-section picking mode (see "Cross-section tool" below) piggybacks
  // on this same click -- the depth readout above still always happens.
  if (state.crossSection.armed) {
    placeCrossSectionEndpoint(cartesian);
  }
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
  const range = d.meta.legend_range || d.meta.z_range_m;
  const unit = d.meta.legend_units != null ? d.meta.legend_units : " m";
  const kind = d.meta.legend_kind || "depth";
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
      range = d.meta.legend_range || d.meta.z_range_m;
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
  const colorMeta = pickColorMetaFor(d, mode);
  const isRelative = !!(colorMeta.ramp && colorMeta.ramp.stops);
  const isGlobe = !!(colorMeta.ramp && colorMeta.ramp.ocean && colorMeta.ramp.land);
  if (!isRelative && !isGlobe) return false;
  const srcArr = mode === "backscatter" ? d.sections.value_backscatter : (d.sections.value || d.sections.z_m);
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
    for (let i = 0; i < n; i++) {
      const t = (srcArr[i] - win.lo) / span;
      const rgb = sampleRelativeColorJS(t, stops);
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

// Status -> colour: just two states, "to do" (still outstanding) and "done"
// (grey, deliberately -- greyed-out reads as complete/inactive at a glance).
// One validated slot from the standard categorical theme for "to do"; an
// unrecognised status value reads the same as "done" rather than getting a
// third colour, since "not one of the two we track" and "done" both mean
// "not something to act on right now". Keys are lower-cased for matching
// against the CSV.
const TRACK_STATUS_COLORS = [
  { key: "to do", hex: "#eb6834" }, // orange -- still outstanding
  { key: "done", hex: "#9aa7b2" }, // grey -- completed
];
const TRACK_STATUS_FALLBACK_HEX = "#9aa7b2"; // same grey as "done" -- missing/unrecognised status
const TRACK_STATUS_LOOKUP = new Map(TRACK_STATUS_COLORS.map((s) => [s.key, s.hex]));

function trackStatusColor(status) {
  if (!status) return TRACK_STATUS_FALLBACK_HEX;
  return TRACK_STATUS_LOOKUP.get(status.trim().toLowerCase()) || TRACK_STATUS_FALLBACK_HEX;
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
  if (/(^|[^a-z])mt([^a-z]|$)/.test(name)) return "mt";
  return "dredge"; // unrecognised filename -- default to the plain circle marker
}

// Cesium's PointPrimitiveCollection (used for "dredge" points, below) only
// ever renders a circle -- there's no shape option on it. A genuinely
// different "MT" marker needs a Billboard with a hand-drawn icon instead.
// Drawn once per status colour and cached (a handful of colours total, not
// one canvas per point).
const trackDiamondIconCache = new Map();
function getTrackDiamondIcon(hex) {
  if (trackDiamondIconCache.has(hex)) return trackDiamondIconCache.get(hex);
  const size = 20;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const mid = size / 2;
  const r = size / 2 - 2;
  ctx.beginPath();
  ctx.moveTo(mid, mid - r);
  ctx.lineTo(mid + r, mid);
  ctx.lineTo(mid, mid + r);
  ctx.lineTo(mid - r, mid);
  ctx.closePath();
  ctx.fillStyle = hex;
  ctx.fill();
  // dark outline, matching the circle markers' outline treatment
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = "#0b0b0b";
  ctx.stroke();
  trackDiamondIconCache.set(hex, canvas);
  return canvas;
}

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function buildTrackLegend() {
  const container = document.getElementById("trackLegend");
  if (!container) return;
  container.innerHTML = "";

  const statusHeading = document.createElement("div");
  statusHeading.className = "track-legend-heading";
  statusHeading.textContent = "Status";
  container.appendChild(statusHeading);
  for (const { key, hex } of TRACK_STATUS_COLORS) {
    const row = document.createElement("div");
    row.className = "track-legend-row";
    const swatch = document.createElement("span");
    swatch.className = "track-legend-swatch";
    swatch.style.background = hex;
    const label = document.createElement("span");
    label.textContent = key.replace(/^\w/, (c) => c.toUpperCase());
    row.appendChild(swatch);
    row.appendChild(label);
    container.appendChild(row);
  }

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
  let labelIdx = header.findIndex((h) => ["name", "label", "id", "station"].some((k) => h.includes(k)));
  let statusIdx = header.findIndex((h) => ["status", "task", "activity"].some((k) => h.includes(k)));
  let startRow = 1;
  let warning = null;
  if (latIdx === -1 || lonIdx === -1) {
    latIdx = 0;
    lonIdx = 1;
    labelIdx = -1;
    statusIdx = -1;
    startRow = 0;
    warning = 'No lat/lon header recognised -- assumed column 1 = latitude, column 2 = longitude.';
  }

  const points = [];
  const unrecognisedStatuses = new Set();
  for (let i = startRow; i < lines.length; i++) {
    const cols = splitLine(lines[i]);
    const lat = parseFloat(cols[latIdx]);
    const lon = parseFloat(cols[lonIdx]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
    const label = labelIdx >= 0 && cols[labelIdx] ? cols[labelIdx] : `pt${points.length + 1}`;
    const status = statusIdx >= 0 && cols[statusIdx] ? cols[statusIdx] : null;
    if (status && !TRACK_STATUS_LOOKUP.has(status.trim().toLowerCase())) unrecognisedStatuses.add(status);
    points.push({ latDeg: lat, lonDeg: lon, label, status, kind });
  }
  if (unrecognisedStatuses.size > 0) {
    const extra = `Status value(s) not recognised (shown in grey, same as "done"): ${[...unrecognisedStatuses].slice(0, 4).join(", ")}${unrecognisedStatuses.size > 4 ? ", …" : ""}. Expected "to do" or "done".`;
    warning = warning ? `${warning} ${extra}` : extra;
  }
  return { points, warning };
}

function placeTrackPoints() {
  if (!state.trackPointCollection) {
    state.trackPointCollection = viewer.scene.primitives.add(new Cesium.PointPrimitiveCollection());
  }
  if (!state.trackBillboardCollection) {
    state.trackBillboardCollection = viewer.scene.primitives.add(new Cesium.BillboardCollection());
  }
  state.trackPointCollection.removeAll();
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
    const hex = trackStatusColor(pt.status);
    if (pt.kind === "mt") {
      state.trackBillboardCollection.add({
        position: pt.cartesian,
        image: getTrackDiamondIcon(hex),
        width: 14,
        height: 14,
      });
    } else {
      state.trackPointCollection.add({
        position: pt.cartesian,
        color: Cesium.Color.fromCssColorString(hex),
        // a dark outline (rather than white) keeps every status colour legible
        // against both the pale land palette and the bright shallow-water blue
        // in the basemap underneath -- white washed out on the lightest
        // terrain colours during testing.
        outlineColor: Cesium.Color.fromCssColorString("#0b0b0b"),
        outlineWidth: 1.5,
        pixelSize: 9,
      });
    }
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
  const files = Array.from(e.target.files || []);
  if (files.length === 0) return;
  const statusEl = document.getElementById("trackStatus");
  const allPoints = [];
  const perFileNotes = [];
  for (const file of files) {
    const text = await file.text();
    const kind = detectTrackKind(file.name);
    const { points, warning } = parseTrackCSV(text, kind);
    allPoints.push(...points);
    perFileNotes.push(warning ? `${file.name}: ${warning}` : `${file.name}: ${points.length} point(s) (${kind}).`);
  }
  // Selecting a new set of files replaces whatever was loaded before --
  // select every CSV you want visible together in one file-picker dialog.
  state.trackPoints = allPoints;
  if (statusEl) statusEl.textContent = perFileNotes.join(" ");
  placeTrackPoints();
});

document.getElementById("clearTrackBtn").addEventListener("click", () => {
  state.trackPoints = [];
  if (state.trackPointCollection) state.trackPointCollection.removeAll();
  if (state.trackBillboardCollection) state.trackBillboardCollection.removeAll();
  document.getElementById("trackStatus").textContent = "";
  document.getElementById("trackCsvInput").value = "";
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
  const canSpectrum = !!(colorMeta && colorMeta.ramp && colorMeta.ramp.stops);
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
  restoreCameraControlsAfterDrag();
  const b = state.geoExport.bbox;
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
      const [pr, pg, pb] = hexToRgb(trackStatusColor(pt.status));
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

// ---- dataset manifest ----
async function init() {
  buildTrackLegend();
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
