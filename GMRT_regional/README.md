# GMRT_regional

Data for the wider cruise-transit corridor (Balboa → Galápagos → Golfito)
beyond the MV1007 survey area itself: individual-cruise bathymetry,
backscatter, gravity, and magnetics products from the Marine Geoscience Data
System (MGDS), plus a regional basemap grid from the Global Multi-Resolution
Topography (GMRT) synthesis.

## Contents

| Path | Contents |
|---|---|
| `Backscatter_MGDS/` | Individual-cruise bathymetry and backscatter grids, finer than GMRT's blended synthesis. See [`Backscatter_MGDS/README.md`](Backscatter_MGDS/README.md). |
| `Geophysics_MGDS/` | Gravity, magnetics, and seismic products for the corridor. See [`Geophysics_MGDS/README.md`](Geophysics_MGDS/README.md). |
| `GMRT_Basemap/` | A single regional GMRT topography grid, used as the 3D viewer's background layer (see `Viewer3D/README.md`). |

## Why GMRT itself isn't the primary data source here

GeoMapApp's own base map already serves GMRT dynamically at native
resolution for whatever area is on screen, so a separately downloaded GMRT
grid adds nothing when working in GeoMapApp directly. The individual-cruise
MGDS data in `Backscatter_MGDS/` and `Geophysics_MGDS/` is finer than GMRT's
blended synthesis and is the actual value-add of this folder.

The one exception is `GMRT_Basemap/GMRT_corridor_basemap.grd`: the offline 3D
viewer in `Viewer3D/` has no live basemap service to fall back on, so it
needs its own local copy of GMRT for context. See `Viewer3D/README.md` for
how that grid was fetched and how to refresh it for a different area.

To fetch a GMRT grid for a given bounding box directly (GridServer accepts
either `north`/`south`/`east`/`west` or the equivalent `minlatitude`/
`maxlatitude`/`minlongitude`/`maxlongitude` names):

```
https://www.gmrt.org/services/GridServer?north=<N>&south=<S>&east=<E>&west=<W>&layer=topo&format=coards&resolution=<default|med|high|max>
```

**Exact URL used for `GMRT_Basemap/GMRT_corridor_basemap.grd`** (reproduces
that file exactly, given GMRT's synthesis doesn't change underneath a fixed
version):

```
https://www.gmrt.org/services/GridServer?north=10.7&south=-4.5&east=-73.5&west=-98.5&layer=topo&format=coards&resolution=max
```

This is bbox 98.5–73.5°W / 4.5°S–10.7°N (the wider Panama–Galápagos–Costa
Rica–N. Peru mapping area, not just the narrower cruise-transit corridor),
`layer=topo` (GEBCO-filled, no NaN gaps on land), `resolution=max`. It
resolved to 11381×6951 nodes, ~245 m/node, 633 MB — see [GridServer's own
documentation](https://www.gmrt.org/services/gridserverinfo.php) for the
full parameter reference.

`resolution` caps nodes-per-side, not real-world spacing: `default` returns
the largest grid under ~1000 nodes/side regardless of the requested area, so
widening the bounding box without also raising `resolution` silently
coarsens the output. `max` targets ~100 m/node, backing off only as needed to
stay under GMRT's 2 GB per-file cap, and can take several minutes to
generate for a large area — a browser reporting "couldn't open" or a
timeout does not mean the request failed; the file can still be generating
server-side.

## Import gotcha

GMRT's COARDS output names its variables `lon`/`lat`/`altitude`. GeoMapApp's
GMT-based grid reader requires the classic GMT names `x`/`y`/`z`
specifically — a `lon`/`lat`/`altitude` file is rejected with a generic
"header min/max values are valid numbers and not NaN" error that looks like
data corruption but isn't. Use `scripts/gmrt_to_xyz.py` to rename the
variables (no value changes) before importing, and `scripts/validate_xyz.py`
to double-check the result.
