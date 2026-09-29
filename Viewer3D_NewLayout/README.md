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
- **Track points: multi-file upload, shaped by site type.** The "Track
  points" panel's file picker now takes more than one CSV at once (hold
  Ctrl/Cmd, or drag-select, in the file dialog) -- select
  `MT_dredging_coords/MTsites.csv` and `DredgeSites.csv` together, for
  example, and both show up at the same time. Marker **shape** comes from
  the file name, not a column: a file name containing "dredge" plots as a
  plain circle, a file name containing "mt" (as a whole word, e.g.
  `MTsites.csv`) plots as a diamond; anything else defaults to a circle.
  Marker **colour** comes from each row's `status` column: `to do` is
  orange, `done` (or anything else/blank) is grey. Selecting a new set of
  files replaces whatever was loaded before, same as the original
  single-file behaviour.

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
