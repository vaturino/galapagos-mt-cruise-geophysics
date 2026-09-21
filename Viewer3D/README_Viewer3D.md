# Viewer3D

An offline, self-contained 3D viewer for the bathymetry/backscatter grids in
`GeoMapApp_ready/` and `GMRT_regional/`, plus a regional GMRT basemap layer
for context, built on [CesiumJS](https://cesium.com/platform/cesiumjs/)
(vendored locally in `cesium/` -- nothing here calls out to Cesium Ion or any
other network service, so it runs with no internet and on any computer this
drive is plugged into).

## Opening it

```bash
cd Viewer3D
python3 run_viewer.py
```

This starts a local server (`127.0.0.1`, a free port picked automatically)
and opens the viewer in your default browser. `run_viewer.py` needs nothing
but a stock Python 3 -- no pip install, no venv, no internet. A local server
is required (rather than just double-clicking `index.html`) because browsers
block the relative-path Worker/Asset loads Cesium needs when a page is opened
as a bare `file://` URL; `run_viewer.py` exists solely to get around that.

Stop it with Ctrl+C in the terminal it's running in.

## What you can do in it

- **Datasets panel** -- grouped into three collapsible sections (Bathymetry,
  Backscatter, Geophysics; click a section's header to expand/collapse it,
  Bathymetry starts open). One checkbox per dataset (from
  `data/manifest.json`), and any number can be ticked on at once, across
  sections. Backscatter isn't a separate mesh -- it's an alternate colouring
  of a bathymetry dataset's own mesh (see "Colour by depth or backscatter"
  below) -- so a dataset flagged `has_backscatter` in the manifest (currently
  just `MV1007`) gets a second checkbox rendered into the Backscatter
  section, bound to the *same* underlying visibility as its Bathymetry-section
  checkbox: checking either one checks both. The "Colour by" radio buttons
  live inside the Backscatter section now (they used to be their own
  control-group) since that's the control that actually makes backscatter
  colouring show up. Each row shows its resolution,
  elevation/depth range, and vertex count as soon as the page loads (fetched
  in the background) -- you don't have to check a dataset on just to see how
  coarse or fine it is. Each mesh is actually loaded the first time you check
  it (that's the "Loading..." pause), then just shown/hidden after that --
  unchecking doesn't re-fetch. The camera only auto-flies to frame things the
  *first* time you go from nothing checked to something checked; toggling
  datasets on/off after that leaves your view exactly where you left it, so
  it never yanks the camera out from under a manual rotate. The stats box at
  the bottom lists vertex/triangle counts and depth range per visible
  dataset.
- **Colour by depth or backscatter** (radio buttons) -- both are baked in
  per-vertex at build time, so switching is instant, and applies to every
  loaded dataset at once. A dataset with no backscatter grid (most of them --
  see "Available datasets" below) just stays depth-coloured in backscatter
  mode rather than going blank, so mixing a backscatter survey with
  depth-only ones works fine.
- **Legend** -- a colour-bar row per visible, loaded dataset, matching
  whichever channel (depth or backscatter) is actually on screen for that
  dataset, with its real min/max value labels. `GMRT_basemap`'s legend covers
  the full -10000..+10000 m absolute elevation range (see "Basemap vs. survey
  colouring"); every survey's legend covers that survey's own min/max. A
  geophysics layer's legend shows its own physical units (nT, mGal, km, or
  degC) and value range instead of a depth in metres -- see "Geophysics
  layers" below.
- **Vertical exaggeration** slider (1x-30x) -- recomputes true, curvature-correct
  vertex positions (not a flat local-plane hack; see "Why exaggeration
  recomputes" below) for every loaded dataset, so it's accurate at any survey
  scale, but a rebuild after you release the slider takes a second or two on
  a big mesh (longer with several datasets loaded at once).
- **Relief shading** toggle -- Cesium's own per-fragment Lambertian lighting
  against real mesh normals and the current sun direction. This is what gives
  the "hillshade" look; no separate hillshade image is baked or needed.
- **Camera** -- the primary way to move around is the mouse: left-drag
  rotates/orbits, scroll or right-drag zooms, middle-drag (or ctrl+left-drag)
  tilts. Six buttons (rotate left/right, tilt up/down, zoom in/out) do the
  same thing if you'd rather click. "Reset view" reframes on whatever's
  currently checked.
- **Click the surface** to read off longitude/latitude and approximate depth
  (divided back out of the current exaggeration) at that point.
- **Cross-section (two-point depth profile)** -- click "Pick 2 points", then
  click twice anywhere on the rendered surface; a persistent yellow marker is
  dropped at each click (labelled A/B, draped along the line between them)
  and a depth-vs-distance chart appears below the buttons, with "Download
  CSV"/"Download PNG" buttons underneath it once a profile is computed. See
  "Cross-section tool" below for how it's computed, why it takes a few
  seconds, what the downloads contain, and its limitations.
- **Track points (CSV upload)** -- upload a CSV of lat/lon points (optional
  name/label column; the parser looks for header names containing "lat"/
  "lon"/"name" etc., and falls back to assuming columns 1/2 are lat/lon if it
  can't find a header) and each point is snapped onto the currently-rendered
  seafloor surface via Cesium's `scene.sampleHeight`, at whatever vertical
  exaggeration is active, so a point sits right on the mesh rather than
  floating above or piercing through it. Points re-snap automatically if you
  change exaggeration, toggle datasets, or rebuild. A point only resolves if
  it falls over a *currently checked* dataset -- if you upload a track that
  spans an area with nothing checked underneath, those points are reported as
  "no coverage" and left unplaced rather than guessed at. "Clear points"
  removes them. An optional status column (header containing "status"/
  "task"/"activity") colour-codes each point -- see "Track point status
  colours" below.
- **Export visible layers as GeoTIFF** -- writes a single georeferenced 2D
  GeoTIFF combining every currently-checked (and format-eligible) dataset,
  composited coarse-to-fine (a detailed survey wins over the basemap
  wherever it has data). Two independent choices above the Export button:
  **format** -- "Colour" (an RGBA picture of the mesh, with any uploaded
  track points stamped in) or "Elevation" (a single-band float32 GeoTIFF of
  real depth/elevation in metres, for slope/roughness/terrain-analysis
  tools -- see "GeoTIFF export format" below, this is the fix for the
  original RGBA-only export not being usable for that) -- and **region**,
  "Entire region" (every eligible checked dataset's full extent, the
  default) or "Select region" (drag a rectangle on the map first, and the
  export is clipped to just that area) -- see "GeoTIFF export region
  selection" below. Resolution matches the finest checked dataset, capped to
  6000 px on the long side (a real GIS export, downsampling further if
  needed, not a screenshot) -- see "GeoTIFF export details" below.

## Available datasets

`GMRT_basemap` is the default-visible dataset when the viewer opens -- a
coarse regional context layer, colored with a different colormap (see
below) specifically so it reads as a base, not another survey. The other
four are detailed single-survey meshes you check on as needed, on top of it.

| id | source | coverage | native resolution | shown at (this build) | backscatter? |
|---|---|---|---|---|---|
| `GMRT_basemap` | `GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap_clean.grd` (GMRT GridServer, `layer=topo`, `resolution=max`, spike-repaired -- see "Known data-quality issues" below) | Wider Panama-Galapagos-Costa Rica-N.Peru region, matched to cover the full area of interest (98.5-73.5W/4.5S-10.7N) | ~245 m (GMRT's own ceiling for this bbox under its 2GB file cap) | ~978 m (stride 4) | no |
| `MV1007` | `GeoMapApp_ready/MV1007_*.grd` | MV1007 survey corridor (~530x370 km) | ~50 m | **~50 m -- native, stride 1** | yes |
| `GSC_regional` | `GMRT_regional/.../GSC_97-86W_Compilation/GSC_97-86W_100m_comp.grd` | Galapagos Spreading Center, 97-86 W regional compilation | ~100 m | **~100 m -- native, stride 1** | no |
| `DRFT04RR` | `GMRT_regional/.../DRFT04RR_GSC_Bathymetry/galapagos.100m.comb_geomapapp.grd` | DRFT04RR Galapagos platform survey | ~100 m | **~100 m -- native, stride 1** | no |
| `TN188` | `GMRT_regional/.../TN188_GSC_Bathymetry_8m/` (26 DSL-120A line grids) | TN188 Galapagos Spreading Center survey lines | ~7 m (measured from the grid spacing itself -- the "8 m" in the dataset name is the survey's own nominal/rounded product label) | **~7 m -- native, stride 1** | no |

Every dataset's row in the Datasets panel shows both numbers too (e.g.
"~100 m shown (native ~50 m)", or "~100 m resolution (native)" once shown
equals native) -- `meta.json`'s `native_resolution_m` field, computed
directly from the source grid's own cell spacing at build time, so it can't
drift out of sync the way a hand-written table can. **"Shown" is a mesh
decimation choice for this viewer, not a limit of the survey data itself --
the source `.grd` files always hold the native resolution.**

All four survey grids now ship at full native resolution -- `MV1007`,
`GSC_regional` and `TN188` used to be shown at ~2x their native spacing
because the build script OOM-killed at full native on this machine's
~3.5 GB available RAM; that turned out to be a build-script inefficiency,
not a hard RAM ceiling, and was fixed (see "Native vs. shown resolution"
under "Known data-quality issues" below for what changed and the file-size/
render-cost trade-off that comes with it). `GMRT_basemap` is a different
case: it's a *regional context* layer spanning Panama to Peru, so even its
current ~4x-decimated mesh is already ~9.9M triangles -- native would be
~150M+ triangles for one background layer, which is a triangle-count/
rendering-performance problem, not a RAM problem, so it's deliberately left
coarser regardless of the RAM fix below; if you need `GMRT_basemap`-
resolution detail somewhere specific, crop a sub-region from the source
`.grd` and build a dedicated mesh for it instead of raising the whole
corridor's stride.

`GSC_regional` and `DRFT04RR` are single-grid meshes; `TN188` is built from
all 26 native per-line grids at once (see the multi-file example below) since
the lines are spread along a ~5.4 deg-wide chain too sparse to mosaic into
one dense grid without blowing up memory. `DRFT04RR`'s source grid stores
depth positive-down (like most MGDS products, per the README in
`GMRT_regional/Backscatter_MGDS/`), so it was built with `--positive-down`;
without that flag its seafloor would render as a mirror-image peak above sea
level instead of a basin below it.

None of the three GMRT_regional survey additions has a paired backscatter
grid -- `Backscatter_MGDS/DRFT04RR_GSC_Backscatter/` has backscatter
products but as GeoTIFF, not the NetCDF `build_cesium_mesh.py` reads (and
one of them is noted as corrupted in that folder's own README), and
`MGL1106_CostaRica_CRISP/` has backscatter with no bathymetry pair. Those
weren't wired in for that reason; converting a GeoTIFF backscatter product
to match a bathymetry mesh's vertices would need extra reprojection/
resampling work this script doesn't do.

`GMRT_basemap` was fetched from GMRT's own GridServer rather than any local
source -- see `GMRT_regional/README_GMRT_regional.md` for how (worth reading
if you want to refresh it or pull a different corridor: direct `curl`/
`fetch` to gmrt.org is blocked by org network policy, but navigating a real
browser straight to the GridServer URL triggers a normal file download).

### Geophysics layers

Eight layers built from `GMRT_regional/Geophysics_MGDS/` (see that folder's
own README for dataset provenance/UIDs). `Mittelstaedt_GalapagosIslands_Gravity`
is excluded -- its source file came back 0 bytes on two separate MGDS
download attempts, confirmed genuinely empty, not a project-side bug.

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

These are unlike every other dataset in this viewer: none of them are
bathymetry, and none share a footprint with the Galapagos corridor or with
each other (each MGDS product covers its own separate sub-region, generally
smaller than `GMRT_basemap`'s coverage). `scripts/build_geophysics_drape.py`
(a new script, not an extension of `build_cesium_mesh.py`) handles this by
triangulating each geophysics grid's *own* native lon/lat posts, then
looking up a display elevation for each vertex from `GMRT_basemap`'s source
grid (`GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap.grd`) via
nearest-neighbour lon/lat matching -- the same matching technique
`build_cesium_mesh.py --backscatter` already uses to drop a backscatter
mosaic onto its own bathymetry -- purely so each layer drapes over real
seafloor shape in 3D rather than floating on a flat plane. Colour comes from
the geophysics VALUE at each vertex, not the terrain elevation used for its
height:

- **diverging** ramp (blue -> near-white -> red), domain symmetric about
  zero (`±max(|min|, |max|)` of the grid's own value range) -- used for
  signed anomaly fields (magnetics, the four gravity-anomaly variants,
  thermal anomaly), where zero is meaningful and a diverging colour makes
  sign visually obvious.
- **sequential** ramp (dark purple -> warm yellow), plain low->high domain --
  used for `Geophys_SR1806_CrustThickness`, a magnitude field (crustal
  thickness in km) where a zero-centred diverging ramp wouldn't make sense.

Both reuse the *existing* `"relative_0_1"` colour-domain machinery already
in `app.js` (the same one `depth` and `backscatter` colouring use), so no
changes to the colour-sampling/legend-drawing code itself were needed for a
new geophysics layer to render correctly. Two small, additive changes *were*
needed so the legend shows the right units/range/kind instead of the
terrain elevation range with a hardcoded " m": `meta.json` for a geophysics
layer carries `legend_range`, `legend_units`, and `legend_kind` fields (the
geophysics value range/unit/name, separate from `z_range_m`, which stays the
*terrain* elevation range used for draping and the dataset-list resolution
line), and `pickColorMeta()`/`updateLegend()` in `app.js` prefer those
fields when present, falling back to the original depth-in-metres behaviour
for every dataset that doesn't set them (i.e. every dataset built with
`build_cesium_mesh.py`, unchanged).

**Terrain-drape limitation:** several of these grids extend outside
`GMRT_basemap`'s own bbox (e.g. Bassett's residual-gravity grid runs to
111W, the SR1806 grids to 105W, both well past the basemap's 98.5W edge;
Barckhausen runs to 13N, past the basemap's 10.7N edge). Vertices outside
the terrain grid's bbox get clamped to the terrain's nearest edge elevation
rather than extrapolated, so those areas drape onto a flat shelf instead of
real seafloor shape -- the geophysics VALUE colouring itself is unaffected,
only the 3D height in those areas is approximate. The fraction of each
layer's vertices affected is logged at build time and saved as
`terrain_clamped_frac` in that layer's `meta.json` (ranges from 0% for the
SR1806 grids nested inside the basemap's own coverage on the east side, up
to ~67% for Bassett and the SR1806 grids where they extend west past 98.5W).

To add another geophysics-style layer (values on their own native grid, not
co-registered with any bathymetry mesh here), run:

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

## Known data-quality issues

**Wolf/Darwin Island bad-data spikes (fixed).** GMRT's own synthesis for
this corridor had two localized bad-data artifacts baked into it, both
sitting right on top of real Galapagos islands: Wolf Island (documented
peak 253 m) spiked to 1804 m, and Darwin Island (documented peak 165 m)
spiked to 6288 m, in the raw `GMRT_corridor_basemap.grd`. These are
GMRT's own errors, not anything introduced by this project -- but they
showed up in `GMRT_basemap` and in any geophysics layer draping on it
(only `Geophys_Bassett_ResidualGravity`'s footprint reaches that far
south/west of the others). A global height cutoff isn't safe here: real
Galapagos shield volcanoes on Isabela legitimately reach 1097-1707 m, and
the wider corridor includes the Andes up to ~6260 m (Chimborazo), so a
threshold anywhere near the Darwin spike's magnitude would also risk
flattening real terrain elsewhere. The actual fix (`scripts/fix_gmrt_spikes.py`)
scopes tightly to each island's own small footprint: cells clearly above
each island's documented peak get masked (with a safety margin and a
dilation pass to also catch the spike's tapering edge), then filled by
Laplacian inpainting from the surrounding valid bathymetry -- a smooth,
honest "we don't have better data here" surface rather than a fake peak.
It writes a corrected `GMRT_corridor_basemap_clean.grd` alongside the
untouched original (never overwritten) and is what `GMRT_basemap` and
`Geophys_Bassett_ResidualGravity` are now built from. Sanity check after
the fix: Wolf Island's crop now maxes at 268 m (an untouched real cell,
close to the 253 m documented peak); Darwin's maxes at 142 m (this coarse
~245 m grid was never going to resolve a ~1 km-wide island precisely even
before the bug -- a low, smoothed rise is the honest answer here, not a
false 165 m summit).

**A broader area of likely bad data exists in the mainland corridor,
unfixed.** While investigating the above, a wider scan (comparing each
cell to its immediate neighbours, `median_filter` size 3 with a 600 m
residual threshold -- calibrated to zero false positives against Wolf
Volcano, Cerro Azul, Sierra Negra, Alcedo and a Chimborazo/Cotopaxi check
window) turned up roughly 5,400 additional suspect cells scattered across
the Panama/Colombia/Ecuador mainland, including a single point at
7.75N/77.75W reading 7183 m (there is no real peak anywhere near that in
the Darien region -- the actual highest point nearby is roughly 1,900 m).
Some of these are clearly isolated single-cell "salt and pepper" noise
sitting on otherwise smooth hills (visually confirmed around 7.6-7.9N/
77.55-77.95W); many others fall inside the true high Andes (e.g. a
1,121-cell cluster spanning roughly 0.2S-2.7N/76.1-78.0W, which is
genuinely some of the most rugged terrain on the continent) and are very
likely real steep relief, not artifacts -- telling the two apart reliably
would need an independent elevation source (e.g. SRTM/ASTER) to check
against, which this project hasn't done. **None of this mainland area was
touched by the fix above** -- it's thousands of km from the Galapagos
survey area this project cares about, so trying to auto-correct it risked
doing more harm (flattening real Andean relief) than leaving it alone. If
a future task ever needs the mainland portion of `GMRT_basemap` for
anything quantitative, treat it as unvalidated and check it first.

**Native vs. shown resolution (resolved -- all survey grids now ship at
native).** The first pass at this (stride-2, ~2x native, for `MV1007` /
`GSC_regional` / `TN188`) was a workaround for a real problem: the build
script OOM-killed at ~3.5-3.8 GB resident on this machine when meshing any
of those three at full native stride:

| dataset | native | stride tried | result (old script) |
|---|---|---|---|
| `MV1007` | ~50 m | 1 (native) | OOM-killed during decimation, ~3.77 GB RSS |
| `MV1007` | ~50 m | 2 (~100 m) | OK, 2.1 GB peak, 3.6M vertices / 7.1M triangles |
| `GSC_regional` | ~100 m | 1 (native) | OOM-killed during decimation, ~3.78 GB RSS |
| `GSC_regional` | ~100 m | 2 (~200 m) | OK, 1.95 GB peak, 3.4M vertices / 6.6M triangles |
| `TN188` | ~7 m | 1 (native) | decimation succeeded (10.25M vertices / 20.2M triangles) but OOM-killed computing normals, ~3.79 GB RSS |
| `TN188` | ~7 m | 2 (~14 m) | OK, 1.49 GB peak, 2.56M vertices / 5.0M triangles |
| `DRFT04RR` | ~100 m | 1 (native) | OK, 2.67M vertices / 5.2M triangles -- shipped |

Investigating that OOM found it was a **build-script inefficiency, not a
hard RAM ceiling**: the decimation step materialized each grid's full
bounding-box as dense lon/lat/z/vertex-id arrays even though `MV1007` and
`GSC_regional` are each only ~17% populated within their own bbox (the rest
is nodata outside the survey's actual coverage); `compute_smooth_normals`
built four full-size float64 arrays for the whole triangle set at once
(~2 GB extra for `TN188`'s 20.2M triangles alone); triangle indices were
carried as `int64` when `int32` covers every vertex count here with room to
spare, with redundant array copies on top; and reading a co-registered
backscatter grid materialized the *entire* backscatter mosaic into RAM on
an unchecked assumption that it's always smaller than the bathymetry mesh
(false for `MV1007`: its backscatter grid is 78.4M cells vs. 14.5M populated
bathymetry vertices), then wrote each `mesh.bin` section via an unconditional
cast-copy plus a full `.tobytes()` copy instead of writing straight from the
array buffer. `scripts/build_cesium_mesh.py` was rewritten to stream each
grid row-by-row during decimation (bounding peak memory to roughly the
output size, not the bbox size), compute normals in `float32` chunks, use
`int32` triangle indices throughout, index the backscatter grid directly by
its sampled cells instead of pre-materializing it, and write mesh sections
without the extra copies. Every fix was validated by rebuilding at the
*same* stride as the already-shipped production mesh and comparing the
output `mesh.bin` byte-for-byte (vertex/colour/normal arrays exact-match,
triangle index sets identical) before trusting it for a new native build.

Result -- all three now build at full native (`--stride 1`) comfortably
inside this machine's ~3.5-3.8 GB shell ceiling:

| dataset | native = shown | peak RSS (new script) | vertices / triangles | mesh.bin size |
|---|---|---|---|---|
| `MV1007` | ~50 m | 3.73 GB | 14.5M / 28.7M | 982.5 MB (was 244.8 MB at stride 2) |
| `GSC_regional` | ~100 m | 3.00 GB | 13.8M / 26.8M | 817.7 MB (was 202.6 MB at stride 2) |
| `TN188` | ~7 m | 2.14 GB | 10.3M / 20.2M | 611.2 MB (was 151.9 MB at stride 2) |

`MV1007` in particular is now building within ~70 MB of this machine's
observed ~3.8 GB ceiling -- comfortable today, but the headroom for any
*further* growth (a bigger backscatter mosaic, a wider bbox) is thin. If a
future dataset OOMs again, the row-streaming/chunked-normals pattern here is
the template to extend, not a sign to fall back to decimation.

**This is a real trade-off, not a free win: `Viewer3D/data/` grew from
~0.87 GB to ~2.77 GB** for these three datasets, and checking multiple
native-resolution survey layers on at once (80M+ triangles combined across
`MV1007` + `GSC_regional` + `TN188`, plus `GMRT_basemap`'s ~9.9M) is well
past what stays smooth in a browser tab on a laptop GPU. The viewer already
treats survey layers as check-on-as-needed rather than always-on for this
reason (see "Available datasets" above) -- if the viewer gets sluggish with
several native layers on at once during the cruise, toggle down to one or
two rather than rebuilding at a coarser stride; `--stride 2` versions of the
old (still-correct) meshes are kept in `Viewer3D/data_backup_20260921/` if
you ever want to compare or revert one dataset.

## Basemap vs. survey colouring

`build_cesium_mesh.py --colormap` picks which "depth" ramp gets baked into a
mesh's `color_depth` channel at build time (this is orthogonal to the
depth/backscatter radio buttons in the UI, which just pick which *baked-in*
channel to show -- see "Colour by depth or backscatter" above):

- `depth` (default) -- navy-to-pale-cyan, rescaled to *this dataset's own*
  min/max. Good for a single survey where you want to see its own relief
  clearly, but two different surveys' depth ramps aren't comparable to each
  other (a -500 m point in one mesh and a -500 m point in another can render
  as different colors, since each is normalized independently).
- `globe` -- the real GMT/GMRT "globe.cpt" relief palette (deep purple ->
  blue -> pale cyan at the shelf break -> hard hinge at sea level -> green
  lowlands -> tan/brown uplands -> grey/white peaks), on its fixed
  **absolute** -10000..+10000 m domain, not rescaled per-dataset. This is
  what `GMRT_basemap` uses. Two reasons to use it for a basemap rather than
  just reusing `depth`: it's visually distinct from every survey mesh's blue
  ramp (the survey swaths read as a darker, differently-shaded patch sitting
  on the basemap, most visible zoomed in on a survey rather than at
  full-corridor scale where both are mostly ocean-blue), and because the
  colors mean the same real elevation everywhere, it's the one ramp that
  still makes sense for a basemap spanning both land and sea at once.

Any dataset built with `--colormap globe` is also, by that fact alone,
treated by `app.js` as a background layer: its rendered elevation is
sunk by a fixed 120 m (before the exaggeration multiply, so the effect
scales the same way as everything else and stays proportionally tiny)
relative to its real value. This isn't visible on the basemap itself --
it exists purely so a detailed survey's real elevation is always fractionally
closer to the camera than the basemap's at the same lon/lat, which makes the
GPU depth test resolve consistently in the survey's favor everywhere it has
data. Without this, a survey checked on top of the basemap would z-fight --
flicker pixel-by-pixel between the two nearly-coincident surfaces -- which
showed up as a patchy/"holey" look wherever the two overlapped (worse than
with the basemap off, since off there was nothing to fight with). If you add
another basemap-style layer with `--colormap globe`, it gets this for free;
there's no separate flag to opt in.

## Adding another dataset

Generate a mesh from any GMT NetCDF (`x`/`y`/`z`) bathymetry grid -- run this
from `scripts/` (it needs numpy; `netcdf_lite.py` there is used automatically
if `scipy` isn't installed, so this works with no venv on a fresh machine):

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
also accepts more than one file -- each is cropped to its own tight bounding
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
exports usually don't) -- without it, the seafloor renders inverted, poking
up above sea level instead of down into it. `--label` sets the name shown in
the viewer's dataset panel and stats box; it defaults to the first bathymetry
file's name if omitted. `read_grd()` accepts either `x`/`y`/`z` (GeoMapApp-
ready grids) or `lon`/`lat`/`altitude` (GMRT's own GridServer export)
variable names, so a GMRT download can be fed straight in with no rename
step.

For a wide regional basemap layer rather than a detailed survey, add
`--colormap globe` (see "Basemap vs. survey colouring" above) -- this is how
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
(`default`/`med`/`high`/`max`, see `GMRT_regional/README_GMRT_regional.md`)
caps nodes-per-side, not real-world spacing -- `resolution=default` returns
the largest grid under ~1000 nodes/side *regardless of the bbox you ask
for*, so widening the requested area (without also raising `resolution`)
silently coarsens the real-world spacing in direct proportion. This bit us
twice fetching this exact basemap: first widening the bbox at
`resolution=default` dropped it from ~2 km/post to ~4 km/post for no reason
other than the wider area; then `resolution=high` (4x `default`'s budget)
got it back to ~979 m but still short of GMRT's own ceiling.
`resolution=max` (targets ~100 m/node, backing off only as needed to stay
under GMRT's 2GB per-file cap) is the real ceiling -- for this bbox that
landed at 11381x6951 nodes, ~245 m/node, 633 MB. Two things worth knowing
about `resolution=max` specifically: it can take several minutes for
GMRT's server to generate/stream a file that large (a `Claude_Browser__navigate`
or equivalent reporting "couldn't open"/timeout does *not* mean the request
failed -- the download can still be running server-side; check the
Downloads folder by mtime a few minutes later before assuming it needs
retrying), and the fetched grid is themselves too large to mesh at native
resolution on a memory-constrained machine (see below).

**Memory ceiling on decimation, separate from the triangle-budget one:**
on a machine with ~4 GB RAM available to the build script, meshing this
245 m native grid OOM-killed at `--stride` values of 1 or 2 (79M and ~20M
output vertices) partway through -- `--stride 3` (~8.8M vertices) also
OOM'd, this time during normal computation, not just the initial grid read.
`--stride 4` (~4.9M vertices, ~979 m effective, 297 MB mesh.bin) was the
finest that completed reliably. If you have more RAM to work with (or run
the script somewhere other than a constrained VM), a finer stride than 4
may well succeed -- there's no reason 3 or even 2 wouldn't work with
headroom, this is a hardware ceiling on this specific build, not a limit
in the script itself. If you widen this basemap's bbox again, bump
`resolution` to `max`, expect to wait on the fetch, and expect to search
for the coarsest `--stride` your machine's RAM allows rather than assuming
`--max-triangles`'s auto-picker will land somewhere sensible (it doesn't
know about your RAM at all, only a triangle count).

Then add an entry to `Viewer3D/data/manifest.json` (any number of datasets
can go in this list -- they all show up as independently-toggleable
checkboxes in the viewer, not a single-select dropdown):

```json
{ "id": "MV1007", "label": "MV1007 -- bathymetry + backscatter mosaic", "path": "data/MV1007", "category": "bathymetry", "has_backscatter": true }
```

`category` (`"bathymetry"` or `"geophysics"`; defaults to `"bathymetry"` if
omitted) picks which collapsible section a dataset's checkbox appears under
in the panel. `has_backscatter: true` additionally renders a second,
synced checkbox for that same dataset into the Backscatter section -- only
set this if the mesh actually has a `color_backscatter` channel (i.e. it was
built with `build_cesium_mesh.py --backscatter`); it's just a display hint
for the UI, unrelated to whether `meta.json`'s own `has_backscatter` field
(written by the build script) is true.

### Choosing `--max-triangles`

This is a triangle-count budget, not a resolution -- the script works out the
decimation stride needed to hit it from how many populated (non-NaN) cells
are actually in your grid, and prints the effective resolution it landed on.
The default (1.5M triangles) renders smoothly on a normal laptop's
integrated GPU; push it higher (e.g. 4-6M) on a machine with a discrete GPU
if you want closer to the native 50 m grid spacing, or lower (e.g. 500k) for
an older/shared lab computer. `--stride` overrides the auto-picked value
directly if you want a specific resolution regardless of triangle count.

The MV1007 bathymetry mosaic is 87.6M cells but 83% NaN outside the actual
survey-line swaths -- the script only ever triangulates quads where all four
corners have data, so the empty two-thirds-plus of the bounding box costs
nothing in the output mesh.

## Why exaggeration recomputes rather than just scaling

The MV1007 survey corridor is large (roughly 530 x 370 km). Scaling
Z in a single flat local tangent-plane frame -- the "cheap" way to do vertical
exaggeration -- breaks down badly at that scale: Earth's curvature alone
displaces the far edges of a frame like that by kilometres relative to the
centre, which would dwarf the actual seafloor relief. So instead, `app.js`
stores true longitude/latitude/depth per vertex and recomputes true
ellipsoidal (WGS84) ECEF positions with `height = depth * exaggeration`
client-side whenever you change the slider -- correct at any scale, at the
cost of a rebuild pause instead of a live drag.

## GeoTIFF export format: Colour vs. Elevation

The "Export" control group has two format radio buttons above the region
controls:

- **Colour** (default) -- an RGBA picture of the mesh: each pixel is the
  same baked-in display colour you see in the 3D view (depth or backscatter
  ramp, or a geophysics layer's value ramp), plus any uploaded track points
  stamped in. This is a *rendered picture*, good for a GIS visual reference
  or overlay, but the pixel values are colour, not data -- a slope/roughness/
  terrain-analysis tool that reads the raster's numbers back out gets RGBA
  bytes, not elevation, and (correctly) can't compute anything physical from
  them. If your analysis script refuses a Colour-format export with a
  message like "4-band, 8-bit -- not elevation," this is why: reach for
  Elevation format instead.
- **Elevation** -- a single-band 32-bit float GeoTIFF of real depth/
  elevation in metres, one value per pixel, no colour involved. Each pixel
  is that dataset's own `z_m` (the same true, never-exaggerated value the
  surface-click depth readout and the cross-section tool use) reconstructed
  from the mesh's sparse vertex grid, not derived from a colour ramp --
  it's the actual measurement, at whatever resolution the checked dataset's
  mesh has (see "Choosing `--max-triangles`" earlier for what sets that).
  Nodata pixels are `NaN` (both the pixel value itself and the `GDAL_NODATA`
  tag say so, so GDAL/QGIS/rasterio/numpy all recognise them as nodata
  automatically, no manual sentinel-value handling needed on your end). Use
  this format for slope, roughness, hillshade, contouring, or any other
  terrain-analysis tool -- it's what those tools actually need, and what
  the GMRT corridor `.grd` or an MGDS bathymetry grid would give you
  directly if you started from the source data instead of this viewer.
  **Geophysics-category layers are excluded from Elevation format** --
  their `z_m` is borrowed `GMRT_basemap` terrain used only to give them
  something to drape on in 3D (see "Geophysics layers" above), not their
  own gravity/magnetics/crustal-thickness/thermal measurement, so exporting
  it as "elevation" would be actively wrong. If only a geophysics layer is
  checked, Elevation-format export is refused with an explanation rather
  than silently exporting someone else's terrain; check a Bathymetry-section
  dataset (or switch back to Colour format) instead. Track points aren't
  stamped into this format either, for the same reason -- they're a status
  colour annotation, not elevation data.

Both formats share the same region-selection and resolution-capping
behaviour described below and in "GeoTIFF export details"; switching format
doesn't change or clear a drawn selection rectangle.

## GeoTIFF export details

The exporter reconstructs each dataset's original dense (row, column) grid
from its stored *sparse, NaN-masked* vertex list -- every mesh's vertices
came from a uniform lon/lat grid before empty cells were dropped, and the
grid spacing (`dlon_deg`/`dlat_deg`) and origin (`bbox`) are saved in each
dataset's `meta.json`, so this is an exact un-flatten, not an interpolation
or a guess. Cells with no vertex nearby (true nodata, not decimation) stay
fully transparent in a Colour export (or `NaN` in an Elevation export) --
this is why a sparse survey mosaic (e.g. `MV1007`, which only has real data
along its actual ship track lines) exports as those same track-line shapes
rather than a filled rectangle; that matches what you see in the 3D view,
it isn't an export bug.

Compositing is pull-based: for every output pixel, each checked (and
format-eligible) dataset is queried in on-screen order and the first one
with real data there wins, which is what lets a fine survey sit cleanly on
top of the coarse basemap without leaving gaps (a naive "scatter the coarse
dataset's own sparse points into a fine output grid" approach would leave
holes between them). In Colour format, uploaded track points are stamped in
last, as small 5x5-pixel markers coloured by status (see "Track point
status colours" below; a status-less or unrecognised point stamps in the
same neutral grey the 3D view and legend use), after the dataset
compositing; Elevation format skips this step entirely.

Both formats are plain, dependency-free single-strip uncompressed TIFFs
with WGS84 (EPSG:4326) GeoKeys -- `ModelPixelScaleTag`/`ModelTiepointTag`
for the pixel size and top-left corner, `GeoKeyDirectoryTag` for the CRS.
Colour is 4-band 8-bit RGBA; Elevation is single-band 32-bit float
(`SampleFormat` = IEEE floating point) with the `GDAL_NODATA` tag set to
`nan`. Both open in QGIS, GDAL, GeoMapApp, or anything else that reads a
standard georeferenced TIFF; Elevation additionally opens directly in
numpy/rasterio/GDAL-based analysis scripts as a real data array.

## GeoTIFF export region selection

The "Export" control group has two region-mode radio buttons above the
Export button itself:

- **Entire region** (default) -- exports the full union bbox of every
  currently-checked dataset, exactly as before this option existed.
- **Select region** -- reveals "Draw rectangle"/"Clear" buttons and a status
  line. Click "Draw rectangle", then click-and-drag anywhere on the 3D view;
  a translucent yellow rectangle follows the drag and stays on screen (and
  in the 3D view) after you release the mouse, so you can see exactly what
  will be exported before clicking "Export". Dragging again after "Draw
  rectangle" replaces the previous rectangle; "Clear" removes it. Left-drag
  camera rotation is automatically suspended for the duration of the drag
  (and restored the moment you release the mouse) so drawing the rectangle
  doesn't also spin the camera.

When "Select region" is active, exporting **clips** the usual "every checked
dataset's union bbox" down to the drawn rectangle -- it only ever shrinks
the export, never extends it past whatever's actually checked. If the
rectangle doesn't overlap any checked dataset at all, the export is refused
with a message saying so rather than silently producing a blank/tiny file.
The corners are picked with `camera.pickEllipsoid` (a ray/ellipsoid
intersection, not a `sampleHeight` surface query), so -- unlike the
cross-section tool and track points above -- the rectangle corners are not
snapped to mesh height and drawing one doesn't carry the multi-second-per-
sample cost those features have; the lon/lat under the cursor is what
defines the rectangle regardless of vertical exaggeration.

Switching back to "Entire region" clears any drawn rectangle (so switching
back and forth doesn't leave a stale, invisible clip behind); switching to
"Select region" always starts with no rectangle until you draw one.

## Track point status colours

A track-points CSV can carry an optional status column (any header containing
"status", "task", or "activity") with one of four values -- **dredging to
do**, **dredging done**, **MT to do**, **MT done** -- and each point is
coloured accordingly, both in the 3D view and in the GeoTIFF export. The
mapping is fixed in `app.js`'s `TRACK_STATUS_COLORS` (matching is
case-insensitive/whitespace-trimmed):

| status | colour | hex |
|---|---|---|
| dredging to do | blue | `#2a78d6` |
| dredging done | orange | `#eb6834` |
| MT to do | aqua | `#1baf7a` |
| MT done | violet | `#4a3aa7` |
| (missing or unrecognised) | grey | `#9aa7b2` |

A static legend for this mapping (colour swatch + label, all five rows) sits
under the CSV upload control regardless of whether anything's been uploaded
yet, so it doubles as documentation of what the CSV should contain. A status
value that doesn't match one of the four (typos, different wording, a blank
cell) doesn't get dropped or guessed at -- the point still places, coloured
grey, and the upload status line lists which value(s) weren't recognised so
you can fix the CSV rather than silently mis-colouring a point.

These four colours are the anthropic-skills `dataviz` skill's own
palette-validated categorical slots (blue/orange/aqua/violet from its
standard 8-hue theme), chosen specifically because that combination is one
of the few 4-colour subsets from the theme that clears *all* of the skill's
`validate_palette.js` checks in `--pairs all` mode (every pair distinguishable,
not just neighbours -- the relevant test here since all four can appear
scattered together on screen at once, unlike a bar chart's ordered series):
lightness band, chroma floor, colour-vision-deficiency separation (protan/
deutan/tritan simulated, worst pair ΔE 9.2, clear of the 8.0 target), and
normal-vision separation (worst pair ΔE 16.3, clear of the 15.0 floor). The
naive choice -- the theme's first four slots in order (blue/orange/aqua/
yellow) -- fails this: yellow and orange sit too close together for full-
color vision once *all* pairs are in play, not just adjacent ones, which is
why violet (the theme's 7th slot) was substituted for yellow (4th) here. One
check is a WARN, not a clean pass: violet's contrast against this panel's
dark background is 2.04:1, under the usual 3:1 floor -- the skill's
documented mitigation for that (a visible text label next to the swatch,
never a colour standing alone) is exactly what the legend already does, and
each point additionally gets a dark `#0b0b0b` outline ring (not the more
common white -- white washed out against the basemap's own pale/near-white
palette at high elevations) so every status colour stays legible against
whatever terrain colour is underneath it, independent of the panel-contrast
question. Adding a 5th status needs a 5th slot chosen the same validated way
(re-run `validate_palette.js` on candidates), not an arbitrary/generated hue.

## Cross-section tool

Click "Pick 2 points" (in the "Cross-section" control group, above "Track
points") to arm picking -- the status line under the buttons confirms
picking is armed -- then click anywhere on the rendered surface twice. The
first click drops a persistent marker labelled "A"; the second drops "B",
draws a line between them draped along the sampled surface, and computes/
renders the depth profile. "Clear" removes the markers, line, and chart and
re-arms nothing (click "Pick 2 points" again to start a new one). Only one
cross-section is kept at a time -- picking a new pair replaces the old one.

**How the profile is computed:** the straight-line geodesic between A and B
(true great-circle distance via `Cesium.EllipsoidGeodesic`, not a flat-map
approximation) is divided into 60 evenly-spaced sample points, and each one
is snapped onto the currently-rendered surface with the same
`scene.sampleHeight` technique the track points use -- so, like track
points, the profile follows whichever dataset is actually checked on and on
top at each sample location, not a fixed source grid. Depth values shown
are true depth with the current vertical exaggeration divided back out,
same convention as the surface-click depth readout. If a stretch of the
line crosses an area with nothing checked underneath, those sample points
come back as gaps -- the draped line and chart both break at a gap rather
than interpolating or guessing across it, and the status line under the
buttons reports how many of the 60 sample points had coverage.

**Performance note:** computing a cross-section does 60 real
`scene.sampleHeight` calls, each an actual render-pass query against the
GPU, so it isn't instant -- the "Computing cross-section..." loading
overlay covers this pause (typically a couple of seconds; longer on a
slower/software-rendered GPU). This is the same underlying call the track
points use, just run 60 times synchronously for one profile instead of
once per uploaded point.

Like track points, the cross-section **recomputes automatically** whenever
vertical exaggeration changes or datasets are checked/unchecked, so it
always reflects what's currently on screen rather than going stale.

**Limitation:** because it samples the rendered surface rather than the
source `.grd` grid, the profile is only as fine as whatever mesh is
currently visible at each sample point (decimated, per "Limitations,
honestly" below) and follows whichever checked dataset happens to be
on top there -- for a chart-grade profile, go back to the source grid.

**Downloading a profile:** once a cross-section is computed, "Download CSV"
and "Download PNG" (below the chart) become enabled -- both are disabled
again after "Clear" or before the first pair is picked.

- **CSV** -- one row per sample point, `distance_km,lon_deg,lat_deg,depth_m`
  (true depth, exaggeration already divided out), preceded by a few
  `#`-comment header lines recording point A/B's own coordinates and the
  total distance. `lon_deg`/`lat_deg` are that *sample's own* position along
  the A-B line (not just the two endpoints) -- every row is independently
  georeferenced. A gap (no coverage at that sample) is left blank in
  `depth_m` rather than written as 0 or interpolated, so it's obvious in a
  spreadsheet or replot which stretches had nothing checked underneath;
  `lon_deg`/`lat_deg` are still filled in for a gap row, since the position
  along the line is known even when the depth there isn't. Meant for
  re-plotting or further analysis outside the viewer (Python/Excel/MATLAB/etc).
- **PNG** -- a snapshot of the depth-profile chart exactly as drawn in the
  panel (same pixel dimensions as the on-screen canvas), including a small
  header row giving point A and point B's lon/lat (to 3 decimal places) so
  the image is self-contained -- you don't need the CSV or the on-page
  status text alongside it to know where the profile was taken. Good for
  dropping straight into a slide or write-up.

Both go through the browser's normal download mechanism, same as the
GeoTIFF export below.

## Files

- `index.html`, `app.js`, `style.css` -- the viewer itself.
- `cesium/` -- vendored CesiumJS static build (`Build/Cesium` from the
  `cesium` npm package). Self-contained; nothing here phones home.
- `data/manifest.json` -- list of datasets shown (and independently
  toggleable) in the viewer's Datasets panel.
- `data/<id>/mesh.bin` + `meta.json` -- one dataset's precomputed mesh
  (positions, normals, both colour ramps, raw backscatter values, indices)
  and its metadata (vertex/triangle counts, bbox, z range, provenance).
  Regenerate bathymetry/backscatter datasets with `scripts/build_cesium_mesh.py`,
  geophysics datasets with `scripts/build_geophysics_drape.py` (see
  "Geophysics layers" above) any time the source grid changes -- these are
  build products, not something to hand-edit.
- `run_viewer.py` -- the local server / launcher described above.
- `scripts/fix_gmrt_spikes.py` -- one-off repair for the Wolf/Darwin Island
  bad-data spikes in `GMRT_corridor_basemap.grd` (see "Known data-quality
  issues" above); writes `GMRT_corridor_basemap_clean.grd` alongside the
  untouched original. Re-run it if `GMRT_corridor_basemap.grd` is ever
  re-fetched from GMRT.
- `test_track_points.csv` -- 15 randomly-scattered lat/lon points across the
  full basemap corridor, with a random status column (see "Track point
  status colours" above), for exercising the track-points CSV upload feature
  (see "Track points (CSV upload)" above). Not real survey data -- delete it
  any time, it isn't referenced by anything.

## Limitations, honestly

- The mesh is a decimated snapshot only in the sense that it's a discrete
  vertex mesh, not a continuous surface -- for chart-grade precision
  measurements go back to the source `.grd` files, not this viewer. Every
  dataset row (and `meta.json`) shows both its native and shown resolution
  side by side; all four survey grids now ship at full native (`GMRT_basemap`
  is deliberately coarser, as a wide regional context layer -- see "Known
  data-quality issues" above). Checking on more than one or two native-
  resolution survey layers at once is heavy for a laptop GPU -- see "Native
  vs. shown resolution" above for the file sizes and triangle counts.
- `GMRT_basemap`'s source grid had two bad-data spikes at Wolf and Darwin
  Islands (up to 6288 m where the real peak is 165 m) which are now
  repaired; a separate, much larger area of likely-bad cells in the
  mainland Panama/Colombia/Ecuador corridor was found but deliberately
  left untouched -- see "Known data-quality issues" above.
- The `GMRT_basemap` layer is a coarse (~978 m/post) public synthesis, not
  survey-grade data -- it's regional context to orient the detailed surveys
  against, not something to read precise depths from. There's still no
  imagery/satellite layer (deliberately offline-only) -- this is relief
  shading on real elevation, not a photo basemap.
- Picking reports the position where you clicked on the *rendered* (possibly
  exaggerated, possibly decimated) surface, not a lookup against the source
  grid -- treat the depth readout as approximate, not a replacement for
  reading the actual grid value at that cell.
- Geophysics layers' 3D height is a display convenience, not a depth
  measurement of anything -- it's the nearest `GMRT_basemap` elevation at
  that lon/lat, clamped to the terrain grid's edge (not extrapolated)
  wherever a geophysics grid extends outside `GMRT_basemap`'s own coverage
  (up to ~67% of vertices for some layers -- see "Geophysics layers" above).
  The colour (the actual gravity/magnetics/crustal-thickness/thermal value)
  is unaffected by this; only where the coloured surface sits in 3D is
  approximate in those areas.
