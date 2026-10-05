# Viewer3D_NewLayout

The primary 3D viewer for this project going forward. It started as an
experimental layout variant of `Viewer3D/` and has now replaced it as the
one to use day to day -- see "About `Viewer3D/`" below for what that means
in practice.

Open it the same way as before: `python3 run_viewer.py` from inside this
folder.

## What's different from the original `Viewer3D/` layout

- **Cross-section profiles moved out of the sidebar.** Pick two points the
  same way (the "Pick 2 points" / "Clear" buttons are still in the left
  panel), but the profiles themselves now open in a big panel across the
  **bottom of the screen**, one large chart per checked layer, instead of
  the narrow sidebar strip.
- **Spectrum-coloured profiles.** Each profile's fill is coloured straight
  from that dataset's own colour ramp (the same one its 3D mesh and legend
  use), banded by value along the line -- not a single flat line colour.
- **Manual colour re-windowing on the legend.** Every re-windowable legend
  colourbar (anything except the GMRT basemap, which uses a fixed
  land/ocean elevation table rather than a single stretch) now has two
  draggable handles. Dragging them narrows the colour mapping to a
  sub-range of the data -- values outside the handles clamp to the ramp's
  end colours, values inside spread across the full spectrum -- which
  re-tints the 3D mesh, the legend, and any open cross-section profile for
  that layer as soon as you release a handle. "Reset range" under each
  colourbar puts it back to the full data range. This also means a GeoTIFF
  colour export picks up whatever window is currently set, since it's
  drawn from the same re-tinted mesh colours.
- **Track points: multi-file upload, one toggleable row per file, shaped by
  site type.** The "Track points" panel's file picker takes more than one
  CSV at once (hold Ctrl/Cmd, or drag-select, in the file dialog) -- select
  `MT_dredging_coords/MTsites.csv` and `DredgeSites.csv` together, for
  example, and both show up. Each loaded file gets its own row (file name,
  point count, type) with a checkbox, in the same style as the dataset
  layer list -- toggle a file on/off without re-uploading it. Loading a new
  file name adds it alongside whatever's already loaded; loading the same
  file name again replaces just that one entry, keeping the others as they
  were. Marker **shape** comes from the file name, not a column: a name
  containing "dredge" plots as a plain circle, a name containing "mt" (e.g.
  `MTsites.csv`) plots as a diamond; anything else defaults to a circle.
  Marker **colour** comes from each row's `status` and `site` columns: a
  `done` point (or anything else/blank) is always flat grey; a `to do`
  point is coloured by its own `site` number along a ramp -- dredge runs
  white -> vivid green, MT runs yellow -> red -- normalised against the
  min/max `site` actually present in that file. (`TRACK_ORDER_RAMPS` in
  app.js is where these live; a few other colour combinations were tried
  first and are worth a look if either of these stops working for the
  actual data.) Every marker also has a 3px white halo behind its
  dark-rimmed fill for extra contrast against the basemap. The legend
  shows both ramps with the loaded min/max site range. Hovering (or
  clicking, e.g. on a trackpad) a point shows its label -- "MT acquisition
  point *n*" or "Dredge point *n*" -- using its
  `site` number.

## What's identical

Everything else -- dataset list, exaggeration, lighting, GeoTIFF export,
the underlying data -- works the same as it always has. The `cesium/` and
`data/` folders aren't duplicated; they're symlinked to `Viewer3D/`'s own
copies (see below), so this folder is a few hundred KB on disk, not
another several GB.

## About `Viewer3D/`

The original, sidebar-only layout still exists as a local-only folder --
it's no longer tracked in this GitHub repository, but it isn't deleted:
it's still sitting on the shared drive exactly as before, and this
folder's `cesium/` (vendored CesiumJS build) and `data/` (built meshes)
are still symlinks pointing at it, so removing it from GitHub changes
nothing about how `Viewer3D_NewLayout/` runs. It's kept locally as a
reference/fallback, not because anything still depends on its own
`app.js`/`index.html`/`style.css` -- those are no longer maintained.
Someone who clones this repository fresh from GitHub will only see this
folder, not `Viewer3D/`.

## Additions on the `TM_version` branch (2026-10-04)

**Opens in Chrome.** `run_viewer.py` opens the viewer in Google Chrome (a normal
install, or the Flatpak `com.google.Chrome`), and falls back to the default
browser only if Chrome isn't found.

**No stale layers.** The server sends `Cache-Control: no-store` and never answers
"304 Not Modified". Otherwise, after a layer is rebuilt or restored, the browser
can keep using an old `meta.json` with the new `mesh.bin`. That was seen here: a
44.5 M-vertex meta read against an 11.1 M-vertex mesh, giving
`RangeError: Invalid typed array length`, and the layer silently didn't draw. If
a layer still fails to load, the viewer now shows the error in the loading
message and unticks the layer.

**Colour maps per layer.** Each legend row has a colour-map menu and a
**reverse** box:
- "as built" keeps the layer's original colours.
- The others are Deep, Haline, Ice and Dense (cmocean); Oslo and Batlow
  (Crameri); Viridis; Cividis; Turbo; Spectral; Greyscale. They were sampled from
  the real colour maps (17 stops each).
- For bathymetry, the chosen palette spans the water only (deepest point to sea
  level). Land is drawn in one flat tan, shown with a swatch in the legend.
- The drag handles, cross-section profiles and the colour GeoTIFF export all
  follow the chosen palette.

**Ship position (optional).** Start with `python3 run_viewer.py --ship-feed`, then
tick "Show ship position" in the panel.
- **Listening:** the server listens for NMEA on UDP **55000** (GPS) and
  **55001** (heading), the ports `nc -ul 55000` / `nc -ul 55001` read. They can
  be changed with `--gps-port` / `--heading-port`.
- **Sentences used:** position from GGA, RMC and GLL; course and speed over
  ground from RMC and VTG; heading from HDT, THS and HDG (HDG is magnetic and is
  labelled "M"). Checksums are checked when present.
- **Display:** the page polls `/ship.json` once a minute. It draws a ship arrow,
  a label (position in degrees and decimal minutes, heading or course, speed,
  fix age) and a 1-point-per-minute track. "Go to ship" flies the camera there.
- **No heading feed:** the arrow follows course over ground and the label says
  "COG … (no heading feed)".
- **Stale fix:** a fix older than 5 min turns the arrow grey and is labelled
  STALE.
- **Diagnostics:** `/ship.json` lists the sentence types received on each port.
  On 2026-10-04 the GPS port carried `$GPGGA`, `$GPRMC`, `$GPVTG` and `$GPZDA`,
  and nothing arrived on 55001.
- **Ports in use:** only one program can normally listen on a UDP port. Close
  any `nc -ul` on those ports first.

**Mittelstaedt bathymetry at native 50 m, as 4 tiles.** A single native 50 m
mesh of the whole platform is 2.85 GB (44.5 M vertices). Chrome can't hold more
than about 2 GB in one array (2.0 GB allocates, 2.2 GB fails), so that file can
never load. The platform is instead split 2 × 2, with a one-cell overlap so
there are no gaps:
- layers `Mittelstaedt_50m_NW`, `_NE`, `_SW`, `_SE`
- each about 0.67 GB and 11.1 M vertices, `--colormap relief`
- each with its own colour scale and legend row
- all four load together

The old ~100 m `Mittelstaedt_Galapagos_Bathy` layer is still there. To rebuild:

```bash
# 1. crop (repo venv): writes FOR_TUSHAR/tiles_50m/CUT_bath_clean_{NW,NE,SW,SE}.tif
#    from FOR_TUSHAR/CUT_bath_clean.tif (2x2 split at the grid's middle row/column, +1 cell overlap)
# 2. build each tile
cd scripts
for t in NW NE SW SE; do
  venv/bin/python3 build_cesium_mesh.py --bathy ../FOR_TUSHAR/tiles_50m/CUT_bath_clean_$t.tif \
    --out-dir ../Viewer3D/data/Mittelstaedt_50m_$t --stride 1 --colormap relief \
    --label "Mittelstaedt/Young bathymetry, 50 m native - $t tile"
done
# 3. add the four ids to Viewer3D/data/manifest.json (category "bathymetry")
```

Keep any single layer's `mesh.bin` well under 2 GB. In practice, aim for about
15 M vertices (about 1 GB) or less.

### Previous dredges, planned sites and ship direction (2026-10-04)

**Previous dredges.** Tick "Previous dredges (earlier cruises)". These are the
104 stations from 9 cruises in `Site_Maps/Previous_Dredges_Compiled.csv`, drawn
with the same symbols as the previous-dredge maps in `Site_Maps/`:
- **Shape = cruise:**
  - MV1007 circle
  - TR164 square
  - SO158 plus
  - PLUME02 diamond
  - CTW and ST7 triangles
  - DS pentagon
  - NA062/063 hexagon
  - NZ down-triangle
- **Colour by recovery:** green = glass, grey = no glass, X = no rock.
- **Colour by cruise:** filled = glass, open = no glass, X = no rock.
- **Filters:** untick any recovery class or cruise to hide it. The row counts
  match the CSV (65 glass, 36 no glass, 3 no rock).
- **Extras:**
  - MV1007 on- to off-bottom dredge tracks (black lines)
  - optional labels (station and depth), hidden beyond about 250 km camera distance
  - hover or click for cruise, station, position, depth, location,
    recovery, description and any correction note
- **Dark halo:** previous dredges have a dark halo; planned sites have a white
  halo.
- **Depth placement:** markers sit at the logged on-bottom depth times the
  vertical exaggeration, not at the sampled surface. Sampling costs about
  0.1 s per point on an integrated GPU.
  - On the ~1 km GMRT basemap the rendered surface is a median 56 m off the
    logged depths (range −504 to +232 m, n=24), because the coarse grid smooths
    steep edifices.
  - Markers are never hidden by the surface.

**AT53-04 planned sites in one click.** "Load AT53-04 dredge + MT sites" loads
`MT_dredging_coords/DredgeSites.csv` and `MTsites.csv` without the file picker.
Planned-site markers are now drawn on top of the seafloor instead of being
half-hidden by it.

`run_viewer.py` serves these three CSVs at `/sites/<file name>`. Only the files
in its `SITE_FILES` list are served, so open the viewer through
`run_viewer.py`.

**Ship direction lines.** The ship marker now has lines showing 30 min ahead
at the current speed over ground (minimum 2 km):
- solid pink = true heading, when the heading feed (UDP 55001) is present
- dashed white = course over ground

With both shown, the angle between them is the crab/drift angle. The pink line
behind the ship is its track, one point per minute.

**Fixes.**
- New lines (ship track, direction lines, dredge tracks) now draw as soon as
  they're built. Before, they only appeared after the next camera move.
- `run_viewer.py` no longer drops the connection on a 404. The log filter
  assumed a string and crashed on the error code.

### Dredge lines, native-resolution GeoTIFF export, slope colouring (2026-10-04)

**Dredge lines (planning).** Tick "Dredge lines (planning)". The layer shows
`MT_dredging_coords/DredgeLines.csv`, one planned on-bottom tow per permit
site, drawn as an arrow from start (green) to end (red) with a label giving
depths, length and bearing.
- **Line colours:** orange = auto first guess, yellow = auto but flat
  (direction weakly constrained), magenta = drawn or edited by hand.
- **Draw line:** pick a site, then click the start and the end on the
  surface. Length and bearing are geodesic on WGS-84 (Cesium
  `EllipsoidGeodesic`; the server's pyproj recomputation agrees to 0.1 m and
  0.1°).
- **Reverse:** swaps start and end.
- **Save to repo:** POSTs the CSV to `run_viewer.py`, which keeps the old
  file in `MT_dredging_coords/backups/`, writes the new one, and runs
  `Site_Maps/dredge_plan.py refresh` to recompute depths and slopes from the
  finest grid.
- **CSV / GPX / KML:** download the current lines; GPX has routes `DnnS -> DnnE`.
- **Seeds:** `Site_Maps/dredge_plan.py seed`. For the per-dredge packets
  (sheets, waypoints, nav GeoTIFFs), run `Site_Maps/make_dredge_packets.py`.

**Native-resolution GeoTIFF (Export panel).** Pick a dataset, then export
the selected region (or the current view). `run_viewer.py /export_native`
runs `scripts/native_render.py clip`, which cuts the box straight out of the
original grid file: 1 pixel = 1 grid cell, the grid's own CRS, no
resampling. The download is a zip containing:
- elevation (float32)
- a colour GeoTIFF
- slope (degrees, float32, plus a colour version)
- a map PNG and an info JSON

Notes:
- "Navigation-safe" (default) writes classic strip TIFFs (LZW, RGB, no
  alpha, no BigTIFF) with `.tfw` and `.prj` files, the most widely readable
  form for chart and navigation software.
- If you picked a colour map for that layer in the legend, the colour
  GeoTIFF uses the same map (viewer palette names map to cmocean,
  cmcrameri and matplotlib names with matching orientation).
- The export needs a Python with numpy, rasterio and pyproj. The server
  defaults to `~/miniforge3/envs/claude-science-env/bin/python`; override
  with `--python`.
- The old "Export visible layers as GeoTIFF" button is unchanged. It
  rasterises the displayed (possibly decimated) meshes.

**Slope colouring.** Each layer's colour-map menu now has "SLOPE (deg) from
the mesh".
- **How it's computed:** slope = acos(|n · up|) per vertex, from the mesh
  normals (built from the true, unexaggerated surface), shown on a fixed
  0–40° YlOrRd scale with the usual drag handles.
- **Scale:** vertex normals average the neighbouring triangles, so this is
  the slope over about two cells of the displayed mesh. Checked on the
  stride-2 Mittelstaedt layer (100 m vertices): against
  `native_render.slope_grid` over 200 m, r = 0.990 and median |difference|
  = 0.09° (3000 random vertices); over 100 m or 400 m the agreement is
  worse (r = 0.91, 0.95).
- **Finer slopes:** load the 50 m tiles for slope over about 100 m, or use
  the native export, which uses the source grid itself.

**Fixed:** a box outside a grid's coverage now returns "no data in that
box" instead of failing.

### Operations hardening (2026-10-04 review)

The viewer and server went through an adversarial review before the cruise. What changed for
whoever runs it at sea:

**Ship feed (`run_viewer.py --ship-feed`).**
- **Sentence checks:**
  - Only whole, checksummed NMEA sentences are accepted. A sentence cut off or split across UDP packets is ignored, where before it could produce a false position such as 1°N 8°E.
  - Anything before the `$` (logger timestamps, tag blocks) is stripped.
  - `--allow-no-checksum` accepts feeds that don't send `*hh`.
- **Value checks:**
  - Values must be finite and in range; NaN or inf used to freeze the display.
  - Minutes must be under 60, and (0, 0) is rejected.
  - Degrees are split at the decimal point, so a talker that drops leading zeros still parses correctly.
- **Fix quality:**
  - GGA quality must be 1–6; 0 (no fix), 7 (manual) and 8 (simulator) are rejected.
  - RMC/GLL must be valid (`A`), and RMC/GLL/VTG with mode N or S are rejected, as are THS S and V.
  - HDG is corrected to true heading when it carries variation; otherwise it's labelled magnetic.
- **Plain-text input:** plain `lat lon` or `heading` text lines need `--plain-feed`, and must be exactly those numbers.
- **Port conflicts:** a second program on the same UDP port now makes the viewer report "cannot listen on UDP …" instead of silently losing the feed. Stop `nc -ul` or the other viewer first.
- **Ages:** ages use the monotonic clock, so a laptop clock step doesn't corrupt them.
- **Tests:** `python -m pytest tests/test_nmea.py`, 35 tests. Every failure case from the review is included, and the old parser fails 29 of them.

**Ship display.**
- **Outages:** if the server stops answering, the marker turns grey with "NO UPDATE for N min … STALE". A frozen marker can no longer look live.
- **Placement:** the marker, direction lines and track are drawn on the displayed seafloor under the ship, not at sea level. At sea level they appeared kilometres off in tilted views.
- **Course fallback:** course over ground is used for the arrow only if it's under 5 min old and the ship is moving at 1 kn or more. Otherwise the arrow is grey and the label says why ("heading stale" or "no heading feed").

**Dredge lines.**
- **Depths:** hand-drawn lines now store positive-down depths, so the label and GPX read right before saving.
- **Drawing safeguards:**
  - the site is locked when **Draw line** is pressed
  - a zero-length line is refused
  - drawing a line and picking a cross-section can't both catch one click
- **Saving:** saves are serialised and written atomically, and every backup gets its own name in `MT_dredging_coords/backups/`, never overwritten. Validation rejects out-of-range coordinates and duplicate sites. If the depth refresh fails, the page says so: "saved, but the depth refresh FAILED".
- **Waypoint names:** GPX/KML names match the packets (`D07S`, `D07`, `D07E`).

**Server.**
- Saves and native exports are refused unless the request comes from the viewer itself. A web page on another site can't trigger them.
- Request bodies are limited to 5 MB.
- Native exports run one at a time, are limited to 2° on a side, and accept only known dataset names.

**Elsewhere.**
- Depth read-outs on the GMRT basemap now remove its 120 m display offset.
- "Colour map: as built" restores the original colours even after dragging the colour range.
- The track-CSV loader understands N/S/E/W and degrees-minutes(-seconds), and reports any rows it can't read instead of silently misplacing them.
