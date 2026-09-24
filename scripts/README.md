# scripts

Python tooling for extracting, converting, validating, and meshing the
datasets referenced in `DATA_MANIFEST.md`. Every script takes its
input/output paths as command-line arguments (or resolves local imports
relative to this folder) — nothing is hardcoded to a particular machine or
mount point.

## One-time setup (per machine)

Most of these scripts need numpy, scipy, and tifffile, installed into a
local virtual environment rather than relying on whatever happens to be
installed globally:

```bash
cd scripts
./setup_env.sh          # Windows: setup_env.bat
```

This creates a `venv/` folder here from the pinned versions in
`requirements.txt`. It needs internet access once, to fetch the packages
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

## What each script does

- **`extract_geomapapp_layers.py`** — pulls just the bathymetry/backscatter
  `.grd`/`.tif` members out of a large cruise data-transfer archive, without
  extracting unrelated nav-plot/metadata files. Stdlib only, no environment
  needed.
- **`netcdf_lite.py`** — vendored pure-Python/numpy NetCDF3 reader/writer
  (trimmed from SciPy), imported by most scripts below to read/write
  GMT-style `.grd` files without a GMT install. Not run directly.
- **`mosaic_geomapapp_grids.py`** — merges many per-survey-line grids into
  one combined mosaic grid, so a viewer can load a single file instead of
  hundreds of per-line ones. Needs numpy.
- **`inspect_gmrt.py`** — prints shape/extent/resolution for one or more
  GMRT-style grids (`lon`/`lat`/`altitude` variables), read-only, mmap-based
  so it doesn't load the full array into memory. Needs numpy.
- **`validate_gmrt.py`** — checks a GMRT-style grid's computed min/max
  against its header `actual_range` and counts NaNs. Needs numpy.
- **`grd_to_float32.py`** — downcasts a GMRT grid's `altitude` variable from
  float64 to float32 in place (roughly halves file size). Needs numpy.
- **`gmrt_to_xyz.py`** — renames a GMRT grid's variables from
  `lon`/`lat`/`altitude` to the classic GMT `x`/`y`/`z` convention that
  GeoMapApp's grid reader requires (no value changes). Fixes GeoMapApp's
  "header min/max values are valid numbers and not NaN" error when the real
  cause is variable naming, not corruption. Needs numpy.
- **`validate_xyz.py`** — same check as `validate_gmrt.py`, for grids
  already in `x`/`y`/`z` convention (run after `gmrt_to_xyz.py`). Needs
  numpy.
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
- **`fix_gmrt_spikes.py`** — one-off repair for the Wolf/Darwin Island
  bad-data spikes baked into GMRT's own synthesis of
  `GMRT_corridor_basemap.grd` (mask + Laplacian inpaint, tightly scoped to
  each island); writes a `_clean.grd` copy, leaves the original untouched.
  Re-run if that grid is ever re-fetched from GMRT. See
  `Viewer3D/README.md`'s "Known data-quality issues" for the full story.
  Needs numpy, scipy.
- **`fix_mittelstaedt_bath_spikes.py`** — same problem, independently, in a
  completely different dataset: `FOR_TUSHAR/CUT_bath.grd` (the
  Mittelstaedt-group Galapagos platform compilation) has its own Wolf
  Island/Darwin Island spikes, unrelated in provenance to GMRT's. Same
  mask-dilate-Laplacian-inpaint method, scoped per island; writes
  `CUT_bath_clean.tif` (GeoTIFF, not another `.grd` — this project can only
  *read* NetCDF4/HDF5, not write it). See `Viewer3D/README.md`'s "Known
  data-quality issues". Needs numpy, scipy, rasterio.
- **`crop_reproject_doa_etp.py`** — crops a GeoTIFF to a lon/lat bounding
  box and reprojects it to EPSG:4326 via a memory-safe streaming
  `WarpedVRT` read (never materialises the full array) — written for the
  DOA-ETP regional MBES compilation (EPSG:3395 World Mercator) but the
  pattern generalizes to any projected-CRS GeoTIFF; change `SRC`/`DST`/the
  bbox. See `Viewer3D/README.md`'s "Reading other formats". Needs
  rasterio, pyproj.
- **`segy_inspect.py`** — reads a SEGY seismic file's headers (trace count,
  sample rate, record length, shot-point coordinate range) without loading
  trace data; can export the shot-point track as CSV. Cataloging tool only
  — SEGY is traces along a track, not a grid, so it can't become a
  bathymetry mesh. Needs segyio.
- **`ascii_xyz_to_grd.py`** — converts a plain ASCII lon/lat/value text
  file (`.txt`/`.txt.gz`) to a NetCDF `.grd` if the points form a regular
  grid (then `build_cesium_mesh.py` reads it directly), or to a clean CSV
  if they don't (e.g. a gravimeter/magnetometer log along a ship track —
  scattered, not a grid, and this script detects and respects that rather
  than forcing one). No extra dependency beyond `netcdf_lite.py`.
- **`gmt_grd_to_geotiff.py`** — converts an old-style GMT grid
  (`x_range`/`y_range`/`z_range`, no CF conventions) to a standard GeoTIFF
  with an EPSG code baked in. Needed only for an old MATLAB-`write_gmt`
  style file in a projected/UTM CRS. Needs numpy, scipy, tifffile.
- **`fix_mgds_grid.py`** — general-purpose fixer for grids pulled from
  MGDS: handles old-style GMT grids, modern grids with 0–360° longitude,
  and ESRI ASCII (`.asc`) grids; auto-detects geographic vs. projected
  coordinates; writes either a GeoMapApp-ready `.grd` (geographic) or a
  GeoTIFF with `--epsg <code>` baked in (projected/UTM). Produced every
  `*_geomapapp.grd`/`.tif` file under `GMRT_regional/`. Prefer this over
  `gmt_grd_to_geotiff.py` or `gmrt_to_xyz.py` for any new MGDS pull. Needs
  numpy, scipy, tifffile.

- **`terrain_derivatives.py`** — slope magnitude, downslope direction
  (azimuth clockwise from north), and seafloor roughness (Wilson TRI,
  plane-detrended std-dev at several window sizes, Vector Ruggedness Measure)
  from a bathymetry/elevation GeoTIFF or `.grd`. Reprojects lon/lat input
  onto a metric UTM grid first (own pure-numpy UTM, no pyproj/GDAL), then
  writes one float32 GeoTIFF per product with the UTM EPSG baked in, into a
  `derived/` folder next to the input. Refuses RGB/RGBA image GeoTIFFs (e.g.
  Viewer3D screenshot exports) since colours are not elevations. Use `--bbox`
  and `--zone` for a survey area (UTM scale error grows past ~6 deg from the
  zone's central meridian; the script warns). Handles the full 245 m GMRT
  corridor grid in ~1 min / 2.4 GB RAM. `--help` for all options. Needs
  numpy, scipy, tifffile.

## Suggested order, starting from scratch

Most of these are independent, run-as-needed utilities, not a fixed
pipeline — but a reasonable order when building everything up from source
data is:

1. `./setup_env.sh` (once per machine) + `source venv/bin/activate`
2. `extract_geomapapp_layers.py` against the cruise's own data transfer, if
   working with that dataset (see the root `README.md`'s Data section)
3. *(optional)* `mosaic_geomapapp_grids.py` on the extracted per-line
   folders, for one combined grid instead of one file per survey line
4. For any new MGDS pull: `fix_mgds_grid.py` on each downloaded grid
5. For any new GMRT pull: `grd_to_float32.py` then `gmrt_to_xyz.py`, then
   `validate_xyz.py` to confirm
6. `inspect_gmrt.py` / `validate_gmrt.py` / `validate_xyz.py` are read-only
   diagnostics — run any of them any time, on any matching grid
7. `build_cesium_mesh.py` / `build_geophysics_drape.py` to add a dataset to
   the 3D viewer (see `Viewer3D/README.md`)

Every script supports `--help` (the older mmap-based inspector/validator
scripts just take a list of file paths as plain arguments) — run with no
arguments first if unsure of the exact flags.
