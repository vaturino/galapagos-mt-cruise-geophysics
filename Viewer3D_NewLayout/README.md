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
