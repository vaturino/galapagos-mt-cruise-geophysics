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
