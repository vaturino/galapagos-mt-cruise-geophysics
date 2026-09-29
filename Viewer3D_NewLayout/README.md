# Viewer3D_NewLayout

An experimental copy of `Viewer3D/`, laid out around a dedicated
profile/colourbar workflow closer to what standalone bathymetry-processing
tools offer. **The original `Viewer3D/` is untouched** -- this folder is a
separate, parallel viewer, not a replacement.

Open it the same way: `python3 run_viewer.py` from inside this folder (or
just serve/open `index.html` the way you already do for `Viewer3D/`).

## What's different from `Viewer3D/`

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

## What's identical

Everything else -- dataset list, exaggeration, lighting, track points,
GeoTIFF export, the underlying data -- works exactly like `Viewer3D/`. The
`cesium/` and `data/` folders aren't duplicated; they're symlinked back to
`Viewer3D/`'s own copies, so this folder adds only a few hundred KB on
disk, not another several GB.

## Why a separate copy

This layout is a genuine change in how the tool is used (bottom dock vs.
sidebar, draggable re-windowing) and hasn't had the same amount of real-use
mileage as `Viewer3D/` yet. Keeping it separate means it can be tried out,
and iterated on further, without any risk to the viewer you already rely
on day to day.
