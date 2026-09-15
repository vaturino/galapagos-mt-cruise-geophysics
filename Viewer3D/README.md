# Viewer3D

An offline, self-contained 3D viewer for the bathymetry/backscatter grids in
`GeoMapApp_ready/` (extracted locally, not included in this repository — see
the root `README.md`'s Data section) and `GMRT_regional/`, plus a regional
GMRT basemap layer for context, built on
[CesiumJS](https://cesium.com/platform/cesiumjs/) (vendored locally in
`cesium/` — nothing here calls out to Cesium Ion or any other network
service, so it runs with no internet, on any machine).

## Opening it

```bash
cd Viewer3D
python3 run_viewer.py
```

This starts a local server (`127.0.0.1`, a free port picked automatically)
and opens the viewer in the default browser. `run_viewer.py` needs nothing
but a stock Python 3 — no pip install, no virtual environment, no internet.
A local server is required (rather than just opening `index.html` directly)
because browsers block the relative-path Worker/Asset loads Cesium needs
when a page is opened as a bare `file://` URL; `run_viewer.py` exists solely
to get around that.

Stop it with Ctrl+C in the terminal it's running in.

## What you can do in it

- **Datasets panel** — grouped into three collapsible sections (Bathymetry,
  Backscatter, Geophysics; click a section's header to expand/collapse it,
  Bathymetry starts open). One checkbox per dataset (from
  `data/manifest.json`), and any number can be ticked on at once, across
  sections. Backscatter isn't a separate mesh — it's an alternate colouring
  of a bathymetry dataset's own mesh (see "Colour by depth or backscatter"
  below) — so a dataset flagged `has_backscatter` in the manifest (currently
  just `MV1007`) gets a second checkbox rendered into the Backscatter
  section, bound to the *same* underlying visibility as its Bathymetry-section
  checkbox: checking either one checks both. The "Colour by" radio buttons
  live inside the Backscatter section, since that's the control that
  actually makes backscatter colouring show up. Each row shows its
  resolution, elevation/depth range, and vertex count as soon as the page
  loads (fetched in the background) — no need to check a dataset on just to
  see how coarse or fine it is. Each mesh is actually loaded the first time
  it's checked (that's the "Loading..." pause), then just shown/hidden after
  that — unchecking doesn't re-fetch. The camera only auto-flies to frame
  things the *first* time a dataset goes from nothing checked to something
  checked; toggling datasets on/off after that leaves the view exactly where
  it was, so it never yanks the camera out from under a manual rotate. The
  stats box at the bottom lists vertex/triangle counts and depth range per
  visible dataset.
- **Colour by depth or backscatter** (radio buttons) — both are baked in
  per-vertex at build time, so switching is instant, and applies to every
  loaded dataset at once. A dataset with no backscatter grid (most of them —
  see "Available datasets" below) just stays depth-coloured in backscatter
  mode rather than going blank, so mixing a backscatter survey with
  depth-only ones works fine.
- **Legend** — a colour-bar row per visible, loaded dataset, matching
  whichever channel (depth or backscatter) is actually on screen for that
  dataset, with its real min/max value labels. `GMRT_basemap`'s legend
  covers the full −10000..+10000 m absolute elevation range (see "Basemap
  vs. survey colouring"); every survey's legend covers that survey's own
  min/max. A geophysics layer's legend shows its own physical units (nT,
  mGal, km, or °C) and value range instead of a depth in metres — see
  "Geophysics layers" below.
- **Vertical exaggeration** slider (1×–30×) — recomputes true,
  curvature-correct vertex positions (not a flat local-plane approximation;
  see "Why exaggeration recomputes" below) for every loaded dataset, so it's
  accurate at any survey scale, but a rebuild after releasing the slider
  takes a second or two on a large mesh (longer with several datasets
  loaded at once).
- **Relief shading** toggle — Cesium's own per-fragment Lambertian lighting
  against real mesh normals and the current sun direction. This is what
  gives the "hillshade" look; no separate hillshade image is baked or
  needed.
- **Camera** — the primary way to move around is the mouse: left-drag
  rotates/orbits, scroll or right-drag zooms, middle-drag (or ctrl+left-drag)
  tilts. Six buttons (rotate left/right, tilt up/down, zoom in/out) do the
  same thing for click-based navigation. "Reset view" reframes on whatever's
  currently checked.
- **Click the surface** to read off longitude/latitude and approximate depth
  (divided back out of the current exaggeration) at that point.
- **Track points (CSV upload)** — upload a CSV of lat/lon points (optional
  name/label column; the parser looks for header names containing "lat"/
  "lon"/"name" etc., and falls back to assuming columns 1/2 are lat/lon if
  it can't find a header) and each point is snapped onto the
  currently-rendered seafloor surface via Cesium's `scene.sampleHeight`, at
  whatever vertical exaggeration is active, so a point sits right on the
  mesh rather than floating above or piercing through it. Points re-snap
  automatically when exaggeration changes, datasets are toggled, or the mesh
  rebuilds. A point only resolves if it falls over a *currently checked*
  dataset — a track that spans an area with nothing checked underneath
  reports those points as "no coverage" and leaves them unplaced rather than
  guessing. "Clear points" removes them.
- **Export visible layers as GeoTIFF** — writes a single georeferenced 2D
  GeoTIFF (WGS84 / EPSG:4326, RGBA) combining every currently-checked
  dataset, composited coarse-to-fine (a detailed survey wins over the
  basemap wherever it has data), plus any uploaded track points stamped in
  as small magenta markers. Resolution matches the finest checked dataset,
  capped to 6000 px on the long side (a real GIS export, downsampling
  further if needed, not a screenshot) — see "GeoTIFF export details"
  below.

## Available datasets

`GMRT_basemap` is the default-visible dataset when the viewer opens — a
coarse regional context layer, coloured with a different colormap (see
below) specifically so it reads as a base, not another survey. The other
four are detailed single-survey meshes checked on as needed, on top of it.

| id | source | coverage | resolution | backscatter? |
|---|---|---|---|---|
| `GMRT_basemap` | `GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap.grd` (GMRT GridServer, `layer=topo`, `resolution=max`) | Wider Panama-Galapagos-Costa Rica-N.Peru region, matched to cover the full area of interest (98.5-73.5W/4.5S-10.7N) | ~978 m (decimated from ~245 m native — GMRT's own ceiling for this bbox under its 2GB file cap) | no |
| `MV1007`\* | `GeoMapApp_ready/MV1007_*.grd` | MV1007 survey corridor (~530x370 km) | ~200 m (decimated) | yes |
| `GSC_regional` | `GMRT_regional/.../GSC_97-86W_Compilation/GSC_97-86W_100m_comp.grd` | Galapagos Spreading Center, 97-86 W regional compilation | ~600 m (decimated from 100 m) | no |
| `DRFT04RR` | `GMRT_regional/.../DRFT04RR_GSC_Bathymetry/galapagos.100m.comb_geomapapp.grd` | DRFT04RR Galapagos platform survey | ~200 m (decimated from 100 m) | no |
| `TN188` | `GMRT_regional/.../TN188_GSC_Bathymetry_8m/` (26 DSL-120A line grids) | TN188 Galapagos Spreading Center survey lines | ~27 m (decimated from 8 m) | no |

\* `MV1007`'s source grids live in `GeoMapApp_ready/`, which is extracted
locally from the cruise's own data transfer and is not included in this
repository (see the root `README.md`'s Data section) — this mesh can only
be rebuilt on a machine that has extracted that data locally.

`GSC_regional` and `DRFT04RR` are single-grid meshes; `TN188` is built from
all 26 native per-line grids at once (see the multi-file example below)
since the lines are spread along a ~5.4°-wide chain too sparse to mosaic
into one dense grid without excessive memory use. `DRFT04RR`'s source grid
stores depth positive-down (like most MGDS products — see
`GMRT_regional/Backscatter_MGDS/README.md`), so it was built with
`--positive-down`; without that flag its seafloor renders as a mirror-image
peak above sea level instead of a basin below it.

None of the three `GMRT_regional` survey additions has a paired backscatter
grid: `Backscatter_MGDS/DRFT04RR_GSC_Backscatter/` has backscatter products
but as GeoTIFF, not the NetCDF `build_cesium_mesh.py` reads (and one of them
is corrupted — see that folder's own README), and
`MGL1106_CostaRica_CRISP/` has backscatter with no bathymetry pair. Wiring a
GeoTIFF backscatter product into a bathymetry mesh's own vertices would need
extra reprojection/resampling work this script doesn't currently do.

`GMRT_basemap` was fetched from GMRT's own GridServer rather than any local
source — see `GMRT_regional/README.md` for how (worth reading before
refreshing it or pulling a different corridor: direct `curl`/`fetch` to
gmrt.org is blocked by some network policies, but navigating a real browser
straight to the GridServer URL triggers a normal file download).

### Geophysics layers

Eight layers built from `GMRT_regional/Geophysics_MGDS/` (see that folder's
own README for dataset provenance/UIDs). `Mittelstaedt_GalapagosIslands_Gravity`
is excluded — its source file came back 0 bytes on two separate MGDS
download attempts, confirmed genuinely empty on MGDS's end.

| id | source grid | legend units | ramp |
|---|---|---|---|
| `Geophys_Barckhausen_Magnetics` | `Barckhausen_CentralAmerica_Magnetics/central_america_mag_geomapapp.grd` | nT | diverging |
| `Geophys_Bassett_ResidualGravity` | `Bassett_CentralAmerica_ResidualGravity/CentAm_Residual_gravity_geomapapp.grd` | mGal | diverging |
| `Geophys_SR1806_MBA` | `SR1806_CocosNazca_Gravity/105W95W1S5N_mba.grd` | mGal | diverging |
| `Geophys_SR1806_FAA_GlobalTopo` | `.../105W95W1S5N_mba_global_topo_global_FAA.grd` | mGal | diverging |
| `Geophys_SR1806_FAA_ShipTopo` | `.../105W95W1S5N_mba_ship_topo_global_FAA.grd` | mGal | diverging |
| `Geophys_SR1806_RMBA` | `.../105W95W1S5N_rmba_1k.grd` | mGal | diverging |
| `Geophys_SR1806_CrustThickness` | `.../105W95W1S5N_crust_1k.grd` | km | sequential |
| `Geophys_SR1806_ThermalAnomaly` | `.../105W95W1S5N_thermal_1k.grd` | degC | diverging |

These are unlike every other dataset in this viewer: none are bathymetry,
and none share a footprint with the Galápagos corridor or with each other
(each MGDS product covers its own separate sub-region, generally smaller
than `GMRT_basemap`'s coverage — none of them, in fact, cover the Galápagos
platform itself; see `GMRT_regional/Geophysics_MGDS/README.md`).
`scripts/build_geophysics_drape.py` (a separate script from
`build_cesium_mesh.py`) handles this by triangulating each geophysics
grid's *own* native lon/lat posts, then looking up a display elevation for
each vertex from `GMRT_basemap`'s source grid
(`GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap.grd`) via
nearest-neighbour lon/lat matching — the same technique
`build_cesium_mesh.py --backscatter` uses to drop a backscatter mosaic onto
its own bathymetry — purely so each layer drapes over real seafloor shape
in 3D rather than floating on a flat plane. Colour comes from the
geophysics VALUE at each vertex, not the terrain elevation used for its
height:

- **diverging** ramp (blue → near-white → red), domain symmetric about zero
  (`±max(|min|, |max|)` of the grid's own value range) — used for signed
  anomaly fields (magnetics, the four gravity-anomaly variants, thermal
  anomaly), where zero is meaningful and a diverging colour makes sign
  visually obvious.
- **sequential** ramp (dark purple → warm yellow), plain low→high domain —
  used for `Geophys_SR1806_CrustThickness`, a magnitude field (crustal
  thickness in km) where a zero-centred diverging ramp wouldn't make sense.

Both reuse the existing `"relative_0_1"` colour-domain machinery already in
`app.js` (the same one `depth` and `backscatter` colouring use), so no
changes to the colour-sampling/legend-drawing code itself were needed for a
new geophysics layer to render correctly. Two small, additive fields make
the legend show the right units/range/kind instead of the terrain elevation
range with a hardcoded " m": `meta.json` for a geophysics layer carries
`legend_range`, `legend_units`, and `legend_kind` fields (the geophysics
value range/unit/name, separate from `z_range_m`, which stays the *terrain*
elevation range used for draping and the dataset-list resolution line), and
`pickColorMeta()`/`updateLegend()` in `app.js` prefer those fields when
present, falling back to the original depth-in-metres behaviour for every
dataset built with `build_cesium_mesh.py` (unchanged).

**Terrain-drape limitation:** several of these grids extend outside
`GMRT_basemap`'s own bbox (e.g. Bassett's residual-gravity grid runs to
111W, the SR1806 grids to 105W, both well past the basemap's 98.5W edge;
Barckhausen runs to 13N, past the basemap's 10.7N edge). Vertices outside
the terrain grid's bbox get clamped to the terrain's nearest edge elevation
rather than extrapolated, so those areas drape onto a flat shelf instead of
real seafloor shape — the geophysics VALUE colouring itself is unaffected,
only the 3D height in those areas is approximate. The fraction of each
layer's vertices affected is logged at build time and saved as
`terrain_clamped_frac` in that layer's `meta.json` (ranges from 0% for the
SR1806 grids nested inside the basemap's own coverage on the east side, up
to ~67% for Bassett and the SR1806 grids where they extend west past 98.5W).

To add another geophysics-style layer (values on their own native grid, not
co-registered with any bathymetry mesh here):

```bash
cd scripts
python3 build_geophysics_drape.py \
    --value ../GMRT_regional/Geophysics_MGDS/Barckhausen_CentralAmerica_Magnetics/central_america_mag_geomapapp.grd \
    --terrain ../GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap.grd \
    --out-dir ../Viewer3D/data/Geophys_Barckhausen_Magnetics \
    --label "Barckhausen magnetics (Central America)" \
    --legend-kind magnetics --units nT --ramp diverging
```

then add a manifest entry with `"category": "geophysics"` (see "Adding
another dataset" below for the manifest schema).

## Basemap vs. survey colouring

`build_cesium_mesh.py --colormap` picks which "depth" ramp gets baked into a
mesh's `color_depth` channel at build time (this is orthogonal to the
depth/backscatter radio buttons in the UI, which just pick which *baked-in*
channel to show — see "Colour by depth or backscatter" above):

- `depth` (default) — navy-to-pale-cyan, rescaled to *this dataset's own*
  min/max. Good for a single survey where its own relief should read
  clearly, but two different surveys' depth ramps aren't comparable to each
  other (a -500 m point in one mesh and a -500 m point in another can render
  as different colours, since each is normalised independently).
- `globe` — the real GMT/GMRT "globe.cpt" relief palette (deep purple →
  blue → pale cyan at the shelf break → hard hinge at sea level → green
  lowlands → tan/brown uplands → grey/white peaks), on its fixed
  **absolute** −10000..+10000 m domain, not rescaled per-dataset. This is
  what `GMRT_basemap` uses: it's visually distinct from every survey mesh's
  blue ramp (survey swaths read as a darker, differently-shaded patch
  sitting on the basemap), and because the colours mean the same real
  elevation everywhere, it's the one ramp that still makes sense for a
  basemap spanning both land and sea at once.

Any dataset built with `--colormap globe` is also, by that fact alone,
treated by `app.js` as a background layer: its rendered elevation is sunk
by a fixed 120 m (before the exaggeration multiply, so the effect scales
the same way as everything else and stays proportionally tiny) relative to
its real value. This isn't visible on the basemap itself — it exists purely
so a detailed survey's real elevation is always fractionally closer to the
camera than the basemap's at the same lon/lat, which makes the GPU depth
test resolve consistently in the survey's favour everywhere it has data.
Without this, a survey checked on top of the basemap would z-fight —
flicker pixel-by-pixel between the two nearly-coincident surfaces — which
shows up as a patchy/"holey" look wherever the two overlap. Any other
basemap-style layer built with `--colormap globe` gets this for free;
there's no separate flag to opt in.

## Adding another dataset

Generate a mesh from any GMT NetCDF (`x`/`y`/`z`) bathymetry grid — run from
`scripts/` (needs numpy; `netcdf_lite.py` is used automatically if `scipy`
isn't installed, so this works with no virtual environment on a fresh
machine):

```bash
cd scripts
python3 build_cesium_mesh.py \
    --bathy ../GeoMapApp_ready/MV1007_bathymetry_mosaic.grd \
    --backscatter ../GeoMapApp_ready/MV1007_backscatter_mosaic.grd \
    --out-dir ../Viewer3D/data/MV1007 \
    --max-triangles 1500000 \
    --label "MV1007 -- bathymetry + backscatter mosaic"
```

`--backscatter` is optional (skip it for a bathymetry-only mesh). `--bathy`
also accepts more than one file — each is cropped to its own tight bounding
box and decimated independently, then concatenated, so this works even for
tiles too spatially spread out to mosaic into one dense grid (that's how
`TN188` was built, from its 26 separate line grids):

```bash
python3 build_cesium_mesh.py \
    --bathy ../GMRT_regional/Backscatter_MGDS/TN188_GSC_Bathymetry_8m/*_geomapapp.grd \
    --out-dir ../Viewer3D/data/TN188 \
    --max-triangles 1200000 \
    --label "TN188 Galapagos Spreading Center (8 m, DSL-120A)"
```

Add `--positive-down` if the source grid stores depth as a positive number
(check the survey's own README; MGDS-sourced grids often do this, GeoMapApp
exports usually don't) — without it, the seafloor renders inverted, poking
up above sea level instead of down into it. `--label` sets the name shown
in the viewer's dataset panel and stats box; it defaults to the first
bathymetry file's name if omitted. `read_grd()` accepts either `x`/`y`/`z`
(GeoMapApp-ready grids) or `lon`/`lat`/`altitude` (GMRT's own GridServer
export) variable names, so a GMRT download can be fed straight in with no
rename step.

For a wide regional basemap layer rather than a detailed survey, add
`--colormap globe` (see "Basemap vs. survey colouring" above) — this is how
`GMRT_basemap` was built:

```bash
python3 build_cesium_mesh.py \
    --bathy ../GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap.grd \
    --out-dir ../Viewer3D/data/GMRT_basemap \
    --stride 4 \
    --colormap globe \
    --label "GMRT regional basemap (Panama-Galapagos-Costa Rica corridor)"
```

**GridServer resolution gotcha:** GMRT's GridServer `resolution` keyword
(`default`/`med`/`high`/`max`, see `GMRT_regional/README.md`) caps
nodes-per-side, not real-world spacing — `resolution=default` returns the
largest grid under ~1000 nodes/side *regardless of the bbox requested*, so
widening the requested area (without also raising `resolution`) silently
coarsens the real-world spacing in direct proportion. Fetching this exact
basemap hit that twice: first widening the bbox at `resolution=default`
dropped it from ~2 km/post to ~4 km/post for no reason other than the wider
area; then `resolution=high` (4x `default`'s budget) recovered ~979 m but
still short of GMRT's own ceiling. `resolution=max` (targets ~100 m/node,
backing off only as needed to stay under GMRT's 2GB per-file cap) is the
real ceiling — for this bbox that landed at 11381x6951 nodes, ~245 m/node,
633 MB. Two things worth knowing about `resolution=max` specifically: it
can take several minutes for GMRT's server to generate/stream a file that
large (a browser navigation reporting "couldn't open"/timeout does *not*
mean the request failed — the download can still be running server-side;
check the downloads folder by modification time a few minutes later before
retrying), and the fetched grid is itself too large to mesh at native
resolution on a memory-constrained machine (see below).

**Memory ceiling on decimation, separate from the triangle-budget one:** on
a machine with ~4 GB RAM available to the build script, meshing this 245 m
native grid ran out of memory at `--stride` values of 1 or 2 (79M and ~20M
output vertices) partway through — `--stride 3` (~8.8M vertices) also ran
out of memory, this time during normal computation, not just the initial
grid read. `--stride 4` (~4.9M vertices, ~979 m effective, 297 MB
`mesh.bin`) was the finest that completed reliably. On a machine with more
available RAM, a finer stride than 4 may well succeed — there's no reason 3
or even 2 wouldn't work with headroom; this is a hardware ceiling on that
particular build, not a limit in the script itself. When widening this
basemap's bbox again, use `resolution=max`, expect to wait on the fetch, and
expect to search for the coarsest `--stride` the available RAM allows
rather than assuming `--max-triangles`'s auto-picker will land somewhere
sensible (it doesn't account for available RAM, only a triangle count).

Then add an entry to `Viewer3D/data/manifest.json` (any number of datasets
can go in this list — they all show up as independently-toggleable
checkboxes in the viewer, not a single-select dropdown):

```json
{ "id": "MV1007", "label": "MV1007 -- bathymetry + backscatter mosaic", "path": "data/MV1007", "category": "bathymetry", "has_backscatter": true }
```

`category` (`"bathymetry"` or `"geophysics"`; defaults to `"bathymetry"` if
omitted) picks which collapsible section a dataset's checkbox appears under
in the panel. `has_backscatter: true` additionally renders a second, synced
checkbox for that same dataset into the Backscatter section — only set this
if the mesh actually has a `color_backscatter` channel (i.e. it was built
with `build_cesium_mesh.py --backscatter`); it's a display hint for the UI,
unrelated to whether `meta.json`'s own `has_backscatter` field (written by
the build script) is true.

### Choosing `--max-triangles`

This is a triangle-count budget, not a resolution — the script works out
the decimation stride needed to hit it from how many populated (non-NaN)
cells are actually in the grid, and prints the effective resolution it
landed on. The default (1.5M triangles) renders smoothly on a normal
laptop's integrated GPU; push it higher (e.g. 4-6M) on a machine with a
discrete GPU for closer-to-native resolution, or lower (e.g. 500k) for an
older or shared machine. `--stride` overrides the auto-picked value
directly for a specific resolution regardless of triangle count.

The MV1007 bathymetry mosaic is 87.6M cells but 83% NaN outside the actual
survey-line swaths — the script only ever triangulates quads where all four
corners have data, so the empty two-thirds-plus of the bounding box costs
nothing in the output mesh.

## Why exaggeration recomputes rather than just scaling

The MV1007 survey corridor is large (roughly 530 x 370 km). Scaling Z in a
single flat local tangent-plane frame — the "cheap" way to do vertical
exaggeration — breaks down badly at that scale: Earth's curvature alone
displaces the far edges of a frame like that by kilometres relative to the
centre, which would dwarf the actual seafloor relief. Instead, `app.js`
stores true longitude/latitude/depth per vertex and recomputes true
ellipsoidal (WGS84) ECEF positions with `height = depth * exaggeration`
client-side whenever the slider changes — correct at any scale, at the cost
of a rebuild pause instead of a live drag.

## GeoTIFF export details

The exporter reconstructs each dataset's original dense (row, column) grid
from its stored *sparse, NaN-masked* vertex list — every mesh's vertices
came from a uniform lon/lat grid before empty cells were dropped, and the
grid spacing (`dlon_deg`/`dlat_deg`) and origin (`bbox`) are saved in each
dataset's `meta.json`, so this is an exact un-flatten, not an interpolation
or a guess. Cells with no vertex nearby (true nodata, not decimation) stay
fully transparent in the output raster — this is why a sparse survey mosaic
(e.g. `MV1007`, which only has real data along its actual ship track lines)
exports as those same track-line shapes rather than a filled rectangle;
that matches what appears in the 3D view, it isn't an export bug.

Compositing is pull-based: for every output pixel, each checked dataset is
queried in on-screen order and the first one with real data there wins,
which is what lets a fine survey sit cleanly on top of the coarse basemap
without leaving gaps (a naive "scatter the coarse dataset's own sparse
points into a fine output grid" approach would leave holes between them).
Uploaded track points are stamped in last, as small magenta 5x5-pixel
markers, after the dataset compositing.

The output is a plain, dependency-free single-strip uncompressed RGBA TIFF
with WGS84 (EPSG:4326) GeoKeys — `ModelPixelScaleTag`/`ModelTiepointTag` for
the pixel size and top-left corner, `GeoKeyDirectoryTag` for the CRS. It
opens in QGIS, GDAL, GeoMapApp, or anything else that reads a standard
georeferenced TIFF.

## Files

- `index.html`, `app.js`, `style.css` — the viewer itself.
- `cesium/` — vendored CesiumJS static build (`Build/Cesium` from the
  `cesium` npm package). Self-contained; nothing here phones home. Not
  committed to this repository (see the root `README.md`'s Data section);
  fetch it from the `cesium` npm package and copy `Build/Cesium` here to
  reproduce.
- `data/manifest.json` — list of datasets shown (and independently
  toggleable) in the viewer's Datasets panel. Not committed to this
  repository; regenerate per the "Adding another dataset" / "Geophysics
  layers" sections above.
- `data/<id>/mesh.bin` + `meta.json` — one dataset's precomputed mesh
  (positions, normals, both colour ramps, raw backscatter values, indices)
  and its metadata (vertex/triangle counts, bbox, z range, provenance).
  Regenerate bathymetry/backscatter datasets with
  `scripts/build_cesium_mesh.py`, geophysics datasets with
  `scripts/build_geophysics_drape.py` (see "Geophysics layers" above) any
  time the source grid changes — these are build products, not something to
  hand-edit, and are not committed to this repository.
- `run_viewer.py` — the local server/launcher described above.
- `test_track_points.csv` — 15 randomly scattered lat/lon points across the
  full basemap corridor, for exercising the track-points CSV upload feature
  (see "Track points (CSV upload)" above). Not real survey data — safe to
  delete, it isn't referenced by anything.

## Limitations

- The mesh is a decimated snapshot, not the full-resolution grid — for
  chart-grade precision measurements, use the source `.grd` files, not this
  viewer.
- The `GMRT_basemap` layer is a coarse (~978 m/post) public synthesis, not
  survey-grade data — it's regional context to orient the detailed surveys
  against, not something to read precise depths from. There's no
  imagery/satellite layer (deliberately offline-only) — this is relief
  shading on real elevation, not a photo basemap.
- Picking reports the position where the surface was clicked on the
  *rendered* (possibly exaggerated, possibly decimated) surface, not a
  lookup against the source grid — treat the depth readout as approximate,
  not a replacement for reading the actual grid value at that cell.
- Geophysics layers' 3D height is a display convenience, not a depth
  measurement of anything — it's the nearest `GMRT_basemap` elevation at
  that lon/lat, clamped to the terrain grid's edge (not extrapolated)
  wherever a geophysics grid extends outside `GMRT_basemap`'s own coverage
  (up to ~67% of vertices for some layers — see "Geophysics layers" above).
  The colour (the actual gravity/magnetics/crustal-thickness/thermal value)
  is unaffected by this; only where the coloured surface sits in 3D is
  approximate in those areas.
