# scripts

Python tooling for extracting, converting, validating, and meshing the
datasets referenced in `DATA_MANIFEST.md`. Every script takes its
input/output paths as command-line arguments or resolves paths relative to
this repo. Exceptions: `build_geomapapp_mosaics.py` (called out below), and the
one-off repair scripts `fix_gmrt_spikes.py`, `fix_mittelstaedt_bath_spikes.py`
and `crop_reproject_doa_etp.py`, which have fixed repo-relative inputs/outputs
and no options (running them with `--help` RUNS them).

## One-time setup (per machine)

Most of these scripts need numpy, scipy, and tifffile, installed into a
local virtual environment rather than relying on whatever happens to be
installed globally:

```bash
cd scripts
./setup_env.sh          # Windows: setup_env.bat
```

This creates a `venv/` folder here from `requirements.txt` (minimum versions,
not pinned; `pip freeze` the working venv before a cruise if you need to
reproduce it exactly). It needs internet access once, to fetch the packages
from PyPI; every script runs fully offline after that. `venv/` is tied to
the OS/architecture it was built on and is not committed to the repository
— run `setup_env.sh`/`setup_env.bat` once per machine.

**Run scripts as `venv/bin/python3 script.py` (`venv\Scripts\python.exe` on
Windows) — don't `source venv/bin/activate` first.** `activate` sets
`$VIRTUAL_ENV`/`$PATH` from a literal absolute path baked in at the moment
the venv was *created*; that's fine only if you're activating it from the
exact same absolute-path context that created it. If that ever differs —
confirmed to actually happen with this project's own remote-desktop tooling,
where a venv created through a mounted/bridged view of this drive gets
activated later from a different mount context, or vice versa — `source
activate` silently prepends a *nonexistent* directory to `$PATH`. There's no
error: `python3`/`pip` just fall through to whatever's next on `$PATH`
(usually the system Python), the shell prompt still shows `(venv)`, and
nothing looks wrong until a script tries to `import rasterio` (or anything
else not already on that fallback Python) and fails — which, offline, is
undebuggable and looks like a missing/broken install rather than what it
actually is. `venv/bin/python3 script.py` sidesteps this whole class of bug:
Python's own venv detection resolves site-packages from wherever `python3`
was actually invoked, not from any baked config, so it works correctly
regardless of what absolute path the venv happens to remember from creation
time. Same reasoning for installing packages later — prefer `venv/bin/
python3 -m pip install ...` over a bare `pip install ...`, even inside an
activated shell (this bit us directly while adding `rasterio`/`pyproj`/
`segyio` this round: `pip install --user ...` outside the venv, and even
`pip install ...` inside a *stale-activated* shell, both landed packages
somewhere other than `venv/`'s own `site-packages` without any error saying
so — re-running via `venv/bin/python3 -m pip install -r requirements.txt`
is what actually put them where they'll persist).

`extract_geomapapp_layers.py` needs nothing extra — it's stdlib-only and
works with no environment and no internet at all.

## Quick start: three common jobs

If setup worked and you're just unsure what to actually run: these scripts
are **not** one fixed pipeline you run start-to-finish — they're a toolbox,
and which ones you need depends on what you're trying to do. Pick the job
below that matches, and run only that numbered list.

**Every command below runs through the venv** — `venv/bin/python3
<script>.py` (`venv\Scripts\python.exe` on Windows), not a bare `python3`,
and not after `source venv/bin/activate` first. See "One-time setup" above
for why `source activate` specifically is the one thing to avoid. The two
exceptions, called out where they come up, are `extract_geomapapp_layers.py`
and `run_viewer.py` — both stdlib-only, no venv needed at all.

### Job A — I just want to open the 3D viewer and look at the data

Nothing to run. The viewer's data is already built.

1. `cd Viewer3D_NewLayout` (the old `Viewer3D/` layout's launcher is no
   longer in the repo; that folder now only holds the shared `cesium/` and
   `data/`)
2. `python3 run_viewer.py` — opens it in Chrome at `localhost`
   (`--ship-feed` to show the ship's live position)

Full usage: [`Viewer3D_NewLayout/README.md`](../Viewer3D_NewLayout/README.md);
dataset reference: [`Viewer3D/README.md`](../Viewer3D/README.md).

### Job B — I have a new/updated grid and want it importable in GeoMapApp

This is the most common reason to touch these scripts: a raw downloaded
grid (from MGDS, GMRT, or anywhere else) usually needs converting before
GeoMapApp will open it — see `DATA_MANIFEST.md` and
[`GMRT_regional/README.md`](../GMRT_regional/README.md) for why.

1. Look at the grid's own variables/format first — this decides which
   script you need next:
   - Came from **MGDS** (old-style GMT `x_range`/`y_range`/`z_range`, modern
     `x`/`y`/`z` with 0–360° longitude, or ESRI ASCII `.asc`) →
     `venv/bin/python3 fix_mgds_grid.py`. This is the general-purpose fixer; prefer it
     over the two narrower/older scripts below for any *new* pull.
   - Came from **GMRT's GridServer** (`lon`/`lat`/`altitude` variables) →
     `venv/bin/python3 gmrt_to_xyz.py` (rename only, no value changes), or
     `venv/bin/python3 grd_to_float32.py` first if you also want to roughly halve the
     file size (downcast float64 → float32) before renaming.
   - An **old-style GMT grid in a projected/UTM CRS** (rare — only needed
     for old MATLAB-`write_gmt`-style files) → `venv/bin/python3 gmt_grd_to_geotiff.py`.
   - A **plain ASCII lon/lat/value text file** → `venv/bin/python3 ascii_xyz_to_grd.py`
     (writes a `.grd` if the points form a regular grid, otherwise a clean
     CSV, e.g. for a ship-track gravimeter/magnetometer log).
2. *(optional)* Merging many per-line/per-tile files from one survey into a
   single importable grid → `venv/bin/python3 build_geomapapp_mosaics.py` for a
   `GMRT_regional/`-style dataset folder (see its own caveat below — it's a
   template more than a drop-in tool at this point), or the older
   `venv/bin/python3 mosaic_geomapapp_grids.py` for a folder of per-survey-line grids.
3. Confirm it worked: `venv/bin/python3 inspect_gmrt.py` / `venv/bin/python3
   validate_gmrt.py` (before renaming) or `venv/bin/python3 validate_xyz.py`
   (after) — read-only, safe to run on
   anything, any time.
4. Open the output `.grd`/`.tif` in GeoMapApp. If it still won't import,
   check the variable names are `x`/`y`/`z` specifically — GeoMapApp's
   classic-grid reader rejects anything else with a generic "header
   min/max ... not NaN" error that looks like corruption but isn't.

### Job C — I want to add a new dataset as a layer in the 3D viewer

1. Get the source grid/GeoTIFF ready first:
   - If it's a **projected CRS GeoTIFF** (e.g. UTM), reproject to EPSG:4326
     first — `venv/bin/python3 crop_reproject_doa_etp.py` (change `SRC`/`DST`/the bbox;
     the pattern generalizes beyond its original DOA-ETP use case).
   - If it's a **bathymetry grid with known bad-data spikes** (this has come
     up twice already, in two unrelated datasets — see "Known data-quality
     issues" in [`Viewer3D/README.md`](../Viewer3D/README.md)) → run the
     relevant fixer first: `venv/bin/python3 fix_gmrt_spikes.py` (after
     `fetch_copernicus_reference.py`) or
     `venv/bin/python3 fix_mittelstaedt_bath_spikes.py`. Only relevant to those two specific
     files unless a new dataset turns out to have the same problem.
   - Otherwise a `.grd` (classic NetCDF3 *or* GMT's newer NetCDF4/HDF5
     variant — auto-detected, no flag needed) or an EPSG:4326 GeoTIFF can go
     straight into the next step.
2. Build the mesh:
   - **Bathymetry/backscatter** → `venv/bin/python3 build_cesium_mesh.py`
   - **Geophysics** (gravity, magnetics, etc., draped over existing terrain)
     → `venv/bin/python3 build_geophysics_drape.py`
   - Both write the layer folder into `Viewer3D/data/`. They do **not** add it
     to `Viewer3D/data/manifest.json`: add an entry (`id`, `path`, `label`,
     `category`) by hand, or the viewer won't list it. Then add the layer's
     source to `SOURCES` in `audit_viewer_meshes.py` and run the audit. Full
     flag reference in [`Viewer3D/README.md`](../Viewer3D/README.md).
3. Open the viewer (Job A) and confirm the new layer shows up, at the right
   place and the right way up — cross-check against a dataset you already
   trust if anything looks off (see `Viewer3D/README.md`'s "Known
   data-quality issues" for how past orientation bugs were actually caught).

### Starting completely from scratch (no local data at all)

Do Job B's step 1 once per raw file you pull, as you pull it — there's no
single "run everything" script, because which fixer a given file needs
depends on where it came from. `extract_geomapapp_layers.py` is the one
exception worth running first if you're working from the cruise's own HMRG
data transfer archive (see the root `README.md`'s "Data" section) — it
pulls out just the bathymetry/backscatter members without extracting
everything else in the archive, and needs no environment at all.

## What each script does

Grouped by the job it belongs to above, not alphabetically.

### Setup & environment
- **`extract_geomapapp_layers.py`** — pulls just the bathymetry/backscatter
  `.grd`/`.tif` members out of a large cruise data-transfer archive, without
  extracting unrelated nav-plot/metadata files. Stdlib only, no environment
  needed.
- **`netcdf_lite.py`** — vendored pure-Python/numpy NetCDF3 reader/writer
  (trimmed from SciPy), imported by most scripts below to read/write
  GMT-style `.grd` files without a GMT install. Not run directly.

### Getting a grid GeoMapApp-ready (Job B)
- **`fix_mgds_grid.py`** — general-purpose fixer for grids pulled from
  MGDS: handles old-style GMT grids, modern grids with 0–360° longitude,
  and ESRI ASCII (`.asc`) grids; auto-detects geographic vs. projected
  coordinates; writes either a GeoMapApp-ready `.grd` (geographic) or a
  GeoTIFF with `--epsg <code>` baked in (projected/UTM). Prefer this over
  `gmt_grd_to_geotiff.py` or `gmrt_to_xyz.py` for any new MGDS pull. Needs
  numpy, scipy, tifffile.
- **`gmrt_to_xyz.py`** — renames a GMRT grid's variables from
  `lon`/`lat`/`altitude` to the classic GMT `x`/`y`/`z` convention that
  GeoMapApp's grid reader requires (no value changes). Fixes GeoMapApp's
  "header min/max values are valid numbers and not NaN" error when the real
  cause is variable naming, not corruption. Needs numpy.
- **`grd_to_float32.py`** — downcasts a GMRT grid's `altitude` variable from
  float64 to float32 in place (roughly halves file size). Needs numpy.
- **`gmt_grd_to_geotiff.py`** — converts an old-style GMT grid
  (`x_range`/`y_range`/`z_range`, no CF conventions) to a standard GeoTIFF
  with an EPSG code baked in. Needed only for an old MATLAB-`write_gmt`
  style file in a projected/UTM CRS. Needs numpy, scipy, tifffile.
- **`ascii_xyz_to_grd.py`** — converts a plain ASCII lon/lat/value text
  file (`.txt`/`.txt.gz`) to a NetCDF `.grd` if the points form a regular
  grid (then `build_cesium_mesh.py` reads it directly), or to a clean CSV
  if they don't (e.g. a gravimeter/magnetometer log along a ship track —
  scattered, not a grid, and this script detects and respects that rather
  than forcing one). No extra dependency beyond `netcdf_lite.py`.
- **`mosaic_geomapapp_grids.py`** — merges many per-survey-line grids into
  one combined mosaic grid, so a viewer can load a single file instead of
  hundreds of per-line ones. Needs numpy.
- **`build_geomapapp_mosaics.py`** — merges every dataset folder under
  `GMRT_regional/` into a single-file, GeoMapApp-ready mosaic, reusing
  `fix_mgds_grid.py`'s readers (not rasterio/GDAL — GDAL's netCDF driver
  silently fails to auto-detect these files' coordinate variables, which
  looks like a georeferencing bug but isn't). **Caveat:** `main()` is
  hardcoded to this project's current `GMRT_regional/` folder layout and
  file list, not a general CLI tool — and it predates two things the
  GeoMapApp-ready output actually needs now: a flat one-file-per-dataset
  layout (no subfolders) and the Mittelstaedt/DRFT04RR-backscatter
  datasets, which need extra steps this script doesn't do (HDF5 grid
  reading via `rasterio`, UTM→geographic reprojection). Treat it as a
  worked template — its two reusable functions,
  `mosaic_geo_grids()`/`write_geomapapp_twin()`, are the part worth
  importing into a short ad hoc script for anything it doesn't already
  cover, rather than editing `main()` itself. Needs numpy, scipy.

### Adding a layer to the 3D viewer (Job C)
- **`build_cesium_mesh.py`** — turns one or more bathymetry `.grd` or `.tif`
  files (plus an optional co-registered backscatter `.grd` — GeoTIFF
  backscatter isn't supported yet) into the decimated binary mesh + JSON
  metadata `Viewer3D/app.js` loads. See `Viewer3D/README.md` for full usage
  and how to pick `--max-triangles`. A GeoTIFF input must already be
  EPSG:4326 (lon/lat) — reproject with `crop_reproject_doa_etp.py` first if
  not. `.grd` inputs can be either classic NetCDF3 or GMT's newer NetCDF4/HDF5
  variant (same extension, different format under the hood, common from
  GMT≥6) — `read_grd()` sniffs the file's own magic bytes to tell them apart
  automatically, no flag needed. Needs numpy (falls back to `netcdf_lite.py`
  if scipy isn't installed); needs rasterio too, but only for the GeoTIFF and
  NetCDF4/HDF5 `.grd` paths.
- **`build_geophysics_drape.py`** — triangulates a geophysics grid (gravity,
  magnetics, etc.) on its own native coordinates, drapes it over a terrain
  grid's elevation for 3D display, and writes the mesh/metadata format
  `Viewer3D/app.js` expects. See `Viewer3D/README.md` for usage. Needs
  numpy.
- **`crop_reproject_doa_etp.py`** — crops a GeoTIFF to a lon/lat bounding
  box and reprojects it to EPSG:4326 via a memory-safe streaming
  `WarpedVRT` read (never materialises the full array) — written for the
  DOA-ETP regional MBES compilation (EPSG:3395 World Mercator) but the
  pattern generalizes to any projected-CRS GeoTIFF; change `SRC`/`DST`/the
  bbox. See `Viewer3D/README.md`'s "Reading other formats". Needs
  rasterio, pyproj.
- **`fetch_copernicus_reference.py`** — builds
  `GMRT_regional/GMRT_Basemap/copernicus_glo90_on_gmrt_grid.tif`: the
  Copernicus GLO-90 DEM (public AWS bucket, no login), area-averaged onto
  `GMRT_corridor_basemap.grd`'s own nodes, used as an independent land
  reference by the next two scripts. Needs internet once (~1 min); run it
  before `fix_gmrt_spikes.py`. Needs numpy, rasterio.
- **`fix_gmrt_spikes.py`** — repairs the bad-data spikes and pits baked
  into GMRT's own synthesis of `GMRT_corridor_basemap.grd`: the Wolf/Darwin
  Island patches, then every land cell more than 300 m off Copernicus
  (replaced with Copernicus plus an inpainted residual), then isolated
  ocean cells more than 1500 m off their 5x5 median (inpainted). Writes a
  `_clean.grd` copy, leaves the original untouched. Re-run if that grid is
  ever re-fetched from GMRT, then rebuild `GMRT_basemap` and the eight
  GMRT-draped geophysics layers. See `Viewer3D/README.md`'s "Known
  data-quality issues" for the full story. Needs numpy, scipy, rasterio.
- **`validate_gmrt_clean.py`** — pass/fail checks on `fix_gmrt_spikes.py`'s
  output against references independent of the repair: documented summit
  heights, known artifact sites against Copernicus and the DOA-ETP
  multibeam, a real ocean depression that must survive, the Wolf/Darwin
  caps, and the fraction of cells changed. Exits non-zero on any failure.
  Needs numpy, rasterio.
- **`fix_mittelstaedt_bath_spikes.py`** — same problem, independently, in a
  completely different dataset: `FOR_TUSHAR/CUT_bath.grd` (the
  Mittelstaedt-group Galapagos platform compilation) has its own Wolf
  Island/Darwin Island spikes, unrelated in provenance to GMRT's. Same
  mask-dilate-Laplacian-inpaint method, scoped per island; writes
  `CUT_bath_clean.tif` (GeoTIFF, not another `.grd` — this project can only
  *read* NetCDF4/HDF5, not write it). See `Viewer3D/README.md`'s "Known
  data-quality issues". Needs numpy, scipy, rasterio.
- **`terrain_derivatives.py`** — slope magnitude, downslope direction
  (azimuth clockwise from north), and seafloor roughness (Wilson TRI,
  plane-detrended std-dev at several window sizes, Vector Ruggedness Measure)
  from a bathymetry/elevation GeoTIFF or `.grd`. Reprojects lon/lat input
  onto a metric UTM grid first (own pure-numpy UTM, no pyproj/GDAL), then
  writes one float32 GeoTIFF per product with the UTM EPSG baked in, into a
  `derived/` folder next to the input. Refuses RGB/RGBA image GeoTIFFs (e.g.
  Viewer3D screenshot exports) since colours are not elevations. Use `--bbox`
  and `--zone` for a survey area (UTM scale error grows past ~6 deg from the
  zone's central meridian; the script warns). `--products` picks which
  outputs to write (default: all of them) — e.g. `--products elev,slope`
  for just absolute height + slope. Handles the full 245 m GMRT corridor
  grid in ~1 min / 2.4 GB RAM. `--help` for all options. Needs numpy, scipy,
  tifffile, and (only for a GeoTIFF written with a compressed
  floating-point predictor, e.g. `rasterio`'s default float32 deflate
  output) `imagecodecs` — without it, `tifffile` raises `ValueError:
  <PREDICTOR.FLOATINGPOINT: 3> requires the 'imagecodecs' package` on
  otherwise-valid input; `imagecodecs` is in `requirements.txt` so
  `setup_env.sh` already covers it.

### Diagnostics (safe to run any time, on any matching grid)
- **`inspect_gmrt.py`** — prints shape/extent/resolution for one or more
  GMRT-style grids (`lon`/`lat`/`altitude` variables), read-only, mmap-based
  so it doesn't load the full array into memory. Needs numpy.
- **`validate_gmrt.py`** — checks a GMRT-style grid's computed min/max
  against its header `actual_range` and counts NaNs. Needs numpy.
- **`validate_xyz.py`** — same check as `validate_gmrt.py`, for grids
  already in `x`/`y`/`z` convention (run after `gmrt_to_xyz.py`). Needs
  numpy.
- **`audit_viewer_meshes.py`** — checks every `Viewer3D/data/` mesh against
  the source grid it was built from, at each vertex's own lon/lat (values,
  and drape terrain for geophysics layers), both as stored and flipped
  north-south, so an upside-down or stale build shows up immediately. Run
  it after rebuilding any layer; exits non-zero on a mismatch. A new
  dataset needs its source added to `SOURCES` at the top. Needs numpy
  (plus rasterio for GeoTIFF/NetCDF4 sources).

### Native-resolution maps, GeoTIFF exports and slope
- **`native_render.py`** — every gridded dataset in the repo, rendered or
  cut out at its native resolution: 1 output pixel = 1 source cell, in the
  source's own CRS (no downsampling, no reprojection).
  - **Registry:** `DATASETS` at the top names 24 datasets (81 files). It
    holds the finest version of each and deliberately skips the resampled
    `GeoMapApp_ready/GMRT_regional/*_mosaic.grd` copies. For example,
    TN188 there is 40.8 m but 7 m here, and AT50-09 is 49.6 m vs 15-30 m.
  - **Reads:** GeoTIFF, classic and NetCDF-4 GMT grids (old-style
    `x_range` too), ESRI ASCII, and 0-360 longitudes.
  - **Writes:** float32 value GeoTIFFs, RGB GeoTIFFs (`--nav`: classic
    strip TIFF + `.tfw`/`.prj` for ship navigation software), a pixel-exact
    annotated PNG and an info JSON. For bathymetry it also writes seafloor
    slope (degrees; central differences over `--baseline` metres, default
    2 cells).
  - **Commands:** `all`, `one`, `clip <dataset> W E S N`, `slope`,
    `readme`, `list [--json]`.
  - **Used by:** the 3D viewer's "Native-resolution GeoTIFF" export and
    `Site_Maps/make_dredge_packets.py`.
  - **Environment:** `claude-science-env` (rasterio, pyproj, cmocean,
    matplotlib).
  - **Tests:** `python -m pytest tests/test_native_render.py`, 19 tests:
    - exact value and cell-centre round trips for each input format
    - orientation against independent grids
    - a tilted plane at 1°N and 60°N, and in Mercator
    - the central-difference transfer function for several baselines
    - checks that fail on a one-cell shift or a missing cos(lat).

### Other formats
- **`segy_inspect.py`** — reads a SEGY seismic file's headers (trace count,
  sample rate, record length, shot-point coordinate range) without loading
  trace data; can export the shot-point track as CSV. Cataloging tool only
  — SEGY is traces along a track, not a grid, so it can't become a
  bathymetry mesh. Needs segyio.

Scripts with options support `--help`. Not all do: the older mmap-based
inspector/validator scripts take a plain list of file paths, and the
one-off repair scripts (`fix_gmrt_spikes.py`, `fix_mittelstaedt_bath_spikes.py`,
`crop_reproject_doa_etp.py`, `build_geomapapp_mosaics.py`) take no arguments at
all and RUN when started, rewriting their outputs. Read a script's docstring
before running it.
