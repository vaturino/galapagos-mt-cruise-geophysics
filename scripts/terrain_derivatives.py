#!/usr/bin/env python3
"""
terrain_derivatives.py

Slope (magnitude + downslope direction) and seafloor roughness from a
bathymetry (or any continuous-field) grid, computed on a metric UTM grid.
Fully offline: needs only numpy, scipy, tifffile (see requirements.txt) and
the vendored netcdf_lite.py -- no GDAL, no pyproj, no internet.

Why UTM: gradients need horizontal distance in the same units as depth.
GMRT / GeoMapApp grids are in lon/lat degrees, where a cell is ~245 m tall
but only ~245*cos(lat) m wide and both are not in the units of z. This script
therefore reprojects (bilinear) onto a regular UTM grid first, then derives
everything there. The UTM forward/inverse transform is implemented here
(Kruger series, WGS84), accurate to sub-millimetre inside a zone.

Inputs   GeoTIFF (single-band, float/int elevation) or GMT/COARDS .grd
         (x/y/z, lon/lat/altitude, or old-style GMT), or ESRI .asc.
         Geographic (EPSG:4326) or already-projected metric CRS.
         RGB/RGBA image GeoTIFFs (e.g. Viewer3D "export" screenshots) are
         rejected: colours are not elevations, so no slope can be derived.

Outputs  one float32 GeoTIFF per product in --outdir (default: ./derived
         next to the input), EPSG code (326xx north / 327xx south) baked in,
         NaN = no data:
           <stem>_elev.tif          resampled elevation on the UTM grid
           <stem>_slope.tif         slope magnitude, degrees from horizontal
           <stem>_aspect.tif        DOWNSLOPE direction, degrees clockwise
                                    from grid north (0=N, 90=E, ...); NaN on
                                    flat cells
           <stem>_dzdE.tif / _dzdN.tif   gradient components (m/m) [--grad]
           <stem>_tri.tif           Terrain Ruggedness Index (Wilson et al.
                                    2007 / gdaldem): mean |z - neighbour|
                                    over the 8 neighbours, metres
           <stem>_rstd_w<W>.tif     detrended roughness: std-dev of the
                                    residual after removing a best-fit plane
                                    in a WxW-cell window, metres (slope-
                                    independent -> the usual "roughness")
           <stem>_vrm_w<W>.tif      Vector Ruggedness Measure (Sappington
                                    et al. 2007), 0 (smooth) .. 1 (rugged),
                                    WxW window

Usage
  python3 terrain_derivatives.py in.tif
  python3 terrain_derivatives.py GMRT.grd --bbox -92.5 -89 -2 1.5 --cell 250
  python3 terrain_derivatives.py in.tif --window-m 500,1500,5000 --zone 15
  python3 terrain_derivatives.py depth_positive.tif --depth-positive

Notes
  * Downslope direction is computed from z as given. GMRT/GeoMapApp store
    elevation (seafloor negative) -> aspect points downhill = towards deeper
    water. If your grid stores depth as POSITIVE numbers, pass --depth-positive
    or the direction is reversed.
  * Windows are in cells (--window-cells, default 3,9,27) or in metres
    (--window-m, rounded to odd cells). Roughness is scale dependent: report
    the window with the numbers, and don't interpret scales below ~3x the
    real data resolution (GMRT is interpolated satellite-derived bathymetry
    away from multibeam swaths).
  * Windows containing any NaN give NaN (complete windows only), so the
    outer (W-1)/2 cells of each grid edge and areas next to data gaps are
    NaN by design. The 3x3 slope/aspect/TRI lose 1 cell at edges.
  * UTM is only accurate near its central meridian. The scale factor is
    ~1.0004 at the CM edge of a zone, ~1.0010 at 3 deg off and >1.005 beyond
    ~6 deg; a warning is printed. For a wide region pick the zone at the
    survey area (--zone) and/or crop with --bbox.
  * --cell sets the output UTM cell size in metres (default: the input's
    native spacing, whichever axis is coarser). Going much coarser than the
    input aliases with bilinear sampling -- smooth first if you need that.
  * Other layers (gravity, magnetics, crustal thickness...) work the same:
    slope = spatial gradient magnitude in <units>/m; use --z-scale to
    convert (e.g. 1e-3 for m->km). The "roughness" is then in the layer's
    own units.
"""
import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------
# UTM (transverse Mercator, WGS84, Kruger series to n^4)
# --------------------------------------------------------------------------
_F = 1 / 298.257223563
_A_EQ = 6378137.0
_K0 = 0.9996
_FE = 500000.0
_N = _F / (2 - _F)
_E = math.sqrt(_F * (2 - _F))
_AA = _A_EQ / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64 + _N**6 / 256)
_ALPHA = (
    _N / 2 - 2 * _N**2 / 3 + 5 * _N**3 / 16 + 41 * _N**4 / 180,
    13 * _N**2 / 48 - 3 * _N**3 / 5 + 557 * _N**4 / 1440,
    61 * _N**3 / 240 - 103 * _N**4 / 140,
    49561 * _N**4 / 161280,
)
_BETA = (
    _N / 2 - 2 * _N**2 / 3 + 37 * _N**3 / 96 - _N**4 / 360,
    _N**2 / 48 + _N**3 / 15 - 437 * _N**4 / 1440,
    17 * _N**3 / 480 - 37 * _N**4 / 840,
    4397 * _N**4 / 161280,
)
_DELTA = (
    2 * _N - 2 * _N**2 / 3 - 2 * _N**3 + 116 * _N**4 / 45,
    7 * _N**2 / 3 - 8 * _N**3 / 5 - 227 * _N**4 / 45,
    56 * _N**3 / 15 - 136 * _N**4 / 35,
    4279 * _N**4 / 630,
)


def central_meridian(zone):
    return (zone - 1) * 6 - 180 + 3


def zone_from_lon(lon):
    lon = ((lon + 180.0) % 360.0) - 180.0
    return int(min(60, max(1, math.floor((lon + 180.0) / 6.0) + 1)))


def ll_to_utm(lon, lat, zone, south):
    lam = np.radians(np.asarray(lon, dtype=np.float64) - central_meridian(zone))
    phi = np.radians(np.asarray(lat, dtype=np.float64))
    t = np.sinh(np.arctanh(np.sin(phi)) - _E * np.arctanh(_E * np.sin(phi)))
    xi0 = np.arctan2(t, np.cos(lam))
    eta0 = np.arcsinh(np.sin(lam) / np.hypot(t, np.cos(lam)))
    xi, eta = xi0.copy(), eta0.copy()
    for j, a in enumerate(_ALPHA, start=1):
        xi += a * np.sin(2 * j * xi0) * np.cosh(2 * j * eta0)
        eta += a * np.cos(2 * j * xi0) * np.sinh(2 * j * eta0)
    return _FE + _K0 * _AA * eta, _K0 * _AA * xi + (1e7 if south else 0.0)


def utm_to_ll(e, n, zone, south):
    xi = (np.asarray(n, dtype=np.float64) - (1e7 if south else 0.0)) / (_K0 * _AA)
    eta = (np.asarray(e, dtype=np.float64) - _FE) / (_K0 * _AA)
    xi0, eta0 = xi.copy(), eta.copy()
    for j, b in enumerate(_BETA, start=1):
        xi0 -= b * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
        eta0 -= b * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
    chi = np.arcsin(np.sin(xi0) / np.cosh(eta0))
    phi = chi.copy()
    for j, d in enumerate(_DELTA, start=1):
        phi += d * np.sin(2 * j * chi)
    lam = np.arctan2(np.sinh(eta0), np.cos(xi0))
    return np.degrees(lam) + central_meridian(zone), np.degrees(phi)


# --------------------------------------------------------------------------
# Readers -> Source (north-up; pixel-centre coordinates)
# --------------------------------------------------------------------------
class Source:
    """z[i, j]: i=0 is the northernmost row. Pixel [i,j] centre is at
    (x0 + j*dx, y0 - i*dy), dx, dy > 0. `geographic` => x/y are lon/lat deg."""

    def __init__(self, z, x0, y0, dx, dy, geographic, epsg, nodata, name):
        self.z, self.x0, self.y0, self.dx, self.dy = z, x0, y0, dx, dy
        self.geographic, self.epsg, self.nodata, self.name = geographic, epsg, nodata, name

    @property
    def shape(self):
        return self.z.shape

    def extent(self):
        nh, nw = self.shape
        return (self.x0, self.x0 + (nw - 1) * self.dx,
                self.y0 - (nh - 1) * self.dy, self.y0)  # xmin xmax ymin ymax

    def crop(self, bbox):
        """bbox = (xmin, xmax, ymin, ymax) in the source's own coordinates."""
        nh, nw = self.shape
        xmin, xmax, ymin, ymax = bbox
        if self.geographic and self.x0 >= 0 and xmin < 0:  # 0-360 source
            xmin, xmax = xmin % 360, xmax % 360
        c0 = max(0, int(math.floor((xmin - self.x0) / self.dx)))
        c1 = min(nw, int(math.ceil((xmax - self.x0) / self.dx)) + 1)
        r0 = max(0, int(math.floor((self.y0 - ymax) / self.dy)))
        r1 = min(nh, int(math.ceil((self.y0 - ymin) / self.dy)) + 1)
        if c1 - c0 < 5 or r1 - r0 < 5:
            raise SystemExit(f"--bbox {bbox} does not overlap the grid "
                             f"(grid extent {self.extent()}) by at least 5 cells")
        self.z = self.z[r0:r1, c0:c1]
        self.x0 += c0 * self.dx
        self.y0 -= r0 * self.dy


def _parse_geokeys(page):
    keys = {}
    tag = page.tags.get("GeoKeyDirectoryTag")
    if tag is not None:
        v = list(tag.value)
        for k in range(int(v[3])):
            key, loc, count, val = v[4 + 4 * k: 8 + 4 * k]
            if loc == 0:
                keys[int(key)] = int(val)
    return keys


def read_geotiff(path, input_epsg=None):
    import tifffile
    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        spp = page.samplesperpixel
        dt = np.dtype(page.dtype)
        if spp != 1 or dt.kind not in "fiu" or (dt.kind == "u" and dt.itemsize == 1):
            raise SystemExit(
                f"{path}:\n  this GeoTIFF is {spp}-band {dt} "
                f"({getattr(page.photometric, 'name', page.photometric)}) -- a rendered colour image, "
                "not elevation data.\n  Slope/roughness need real z values (a float or "
                "int16 GeoTIFF/.grd of depth or elevation). Export the\n  data grid "
                "itself instead of a screenshot, or point this script at the .grd/.tif "
                "the image was rendered from.")
        if page.tags.get("ModelTransformationTag") is not None:
            raise SystemExit(f"{path}: rotated/sheared georeferencing (ModelTransformationTag) not supported")
        scale = page.tags["ModelPixelScaleTag"].value
        tie = page.tags["ModelTiepointTag"].value
        keys = _parse_geokeys(page)
        nod = page.tags.get("GDAL_NODATA")
        nodata = float(str(nod.value).strip("\x00 ")) if nod is not None else None
        try:
            z = tifffile.memmap(path)
            if z.ndim != 2:
                raise ValueError
        except Exception:
            z = page.asarray()
    dx, dy = float(scale[0]), float(scale[1])
    i, j, _, X, Y, _ = tie[:6]
    if keys.get(1025, 1) == 2:  # PixelIsPoint: tiepoint is a pixel centre
        x0, y0 = X - i * dx, Y + j * dy
    else:  # PixelIsArea: tiepoint is a pixel corner
        x0, y0 = X - i * dx + dx / 2, Y + j * dy - dy / 2
    epsg = keys.get(3072)
    if epsg in (None, 32767):
        epsg = None
    geographic = (keys.get(2048) is not None and epsg is None) or keys.get(1024) == 2
    if input_epsg:
        epsg, geographic = (None, True) if input_epsg == 4326 else (input_epsg, False)
    elif not geographic and epsg is None:
        # no usable CRS keys: guess from coordinate magnitude, same rule as fix_mgds_grid.py
        if abs(x0) <= 360.5 and abs(y0) <= 90.5:
            geographic = True
        else:
            raise SystemExit(f"{path}: projected coordinates but no EPSG code in the file; pass --input-epsg")
    return Source(z, x0, y0, dx, dy, geographic, epsg, nodata, os.path.basename(path))


def read_grid(path, input_epsg=None):
    from netcdf_lite import netcdf_file
    nc = netcdf_file(path, "r", mmap=True)
    names = set(nc.variables.keys())
    triples = [("lon", "lat", "altitude"), ("x", "y", "z"), ("lon", "lat", "z"), ("x", "y", "altitude")]
    hit = next((t for t in triples if set(t) <= names), None)
    if hit is None:
        nc.close()
        from fix_mgds_grid import load_any  # old-style GMT / ESRI ascii
        x, y, z = load_any(path)
        nodata = None
    else:
        x = np.asarray(nc.variables[hit[0]][:], dtype=np.float64)
        y = np.asarray(nc.variables[hit[1]][:], dtype=np.float64)
        var = nc.variables[hit[2]]
        z = var[:]
        att = getattr(var, "_attributes", {}) or {}
        nodata = att.get("_FillValue", att.get("missing_value"))
        nodata = None if nodata is None else float(np.asarray(nodata).ravel()[0])
    dx = float((x[-1] - x[0]) / (len(x) - 1))
    dy = float((y[-1] - y[0]) / (len(y) - 1))
    if dx < 0:
        x, z, dx = x[::-1], z[:, ::-1], -dx
    if dy > 0:  # y ascending -> flip to north-up
        z = z[::-1]
        y0 = float(y[-1])
    else:
        y0, dy = float(y[0]), -dy
    geographic = abs(float(x[0])) <= 360.5 and abs(y0) <= 90.5
    epsg = None
    if input_epsg:
        geographic, epsg = (True, None) if input_epsg == 4326 else (False, input_epsg)
    elif not geographic:
        raise SystemExit(f"{path}: projected coordinates in a .grd carry no EPSG; pass --input-epsg (e.g. 32615)")
    return Source(z, float(x[0]), y0, abs(dx), abs(dy), geographic, epsg, nodata, os.path.basename(path))


def read_source(path, input_epsg=None):
    low = path.lower()
    if low.endswith((".tif", ".tiff")):
        return read_geotiff(path, input_epsg)
    if low.endswith((".grd", ".nc", ".asc")):
        return read_grid(path, input_epsg)
    raise SystemExit(f"{path}: unsupported extension (use .tif/.tiff/.grd/.nc/.asc)")


# --------------------------------------------------------------------------
# Reproject onto a regular UTM grid
# --------------------------------------------------------------------------
def plan_grid(src, zone_arg, cell_arg, max_cells):
    xmin, xmax, ymin, ymax = src.extent()
    if src.geographic:
        lonc = ((xmin + xmax) / 2 + 180) % 360 - 180
        latc = (ymin + ymax) / 2
        zone = zone_arg or zone_from_lon(lonc)
        south = latc < 0
        native = max(src.dx * 111320.0 * math.cos(math.radians(latc)), src.dy * 110574.0)
        cell = cell_arg or float(max(1, round(native)))
        # bounding box in UTM of the whole lon/lat rectangle (sample the edges)
        t = np.linspace(0, 1, 400)
        lons = np.concatenate([xmin + (xmax - xmin) * t, xmin + (xmax - xmin) * t,
                               np.full_like(t, xmin), np.full_like(t, xmax)])
        lats = np.concatenate([np.full_like(t, ymin), np.full_like(t, ymax),
                               ymin + (ymax - ymin) * t, ymin + (ymax - ymin) * t])
        lons = ((lons + 180) % 360) - 180
        e, n = ll_to_utm(lons, lats, zone, south)
        offcm = np.abs(((lons - central_meridian(zone) + 180) % 360) - 180).max()
        epsg = (32700 if south else 32600) + zone
        # keep only the part of the UTM bounding box that is inside the source: shrink to inscribed
        # extent is unnecessary; outside-of-source cells simply come out NaN.
        emin, emax, nmin, nmax = e.min(), e.max(), n.min(), n.max()
        info = dict(zone=zone, south=south, epsg=epsg, offcm=offcm, native=native)
    else:
        zone = south = None
        epsg = src.epsg
        cell = cell_arg or float(src.dx)
        emin, emax, nmin, nmax = xmin - src.dx / 2, xmax + src.dx / 2, ymin - src.dy / 2, ymax + src.dy / 2
        if epsg and 32601 <= epsg <= 32660:
            zone, south = epsg - 32600, False
        elif epsg and 32701 <= epsg <= 32760:
            zone, south = epsg - 32700, True
        info = dict(zone=zone, south=south, epsg=epsg, offcm=None, native=src.dx)
        if abs(src.dx - src.dy) / src.dx > 0.01:
            print(f"  warning: non-square cells ({src.dx} x {src.dy}); output uses {cell} m squares")
    emin = math.floor(emin / cell) * cell
    nmax = math.ceil(nmax / cell) * cell
    nx = int(math.ceil((emax - emin) / cell))
    ny = int(math.ceil((nmax - nmin) / cell))
    if nx * ny > max_cells:
        raise SystemExit(f"UTM grid would be {nx} x {ny} = {nx*ny:,} cells (> --max-cells {max_cells:,}). "
                         "Use --bbox to crop and/or a larger --cell.")
    return dict(cell=cell, emin=emin, nmax=nmax, nx=nx, ny=ny, **info)


def resample_to_utm(src, g):
    from scipy.ndimage import map_coordinates
    nh, nw = src.shape
    out = np.full((g["ny"], g["nx"]), np.nan, dtype=np.float32)
    E = g["emin"] + (np.arange(g["nx"]) + 0.5) * g["cell"]
    rows_per = max(1, int(3e6 // g["nx"]))
    for r0 in range(0, g["ny"], rows_per):
        r1 = min(g["ny"], r0 + rows_per)
        N = g["nmax"] - (np.arange(r0, r1) + 0.5) * g["cell"]
        EE, NN = np.meshgrid(E, N)
        if src.geographic:
            lon, lat = utm_to_ll(EE, NN, g["zone"], g["south"])
            if src.x0 >= 0:  # 0-360 source
                lon = lon % 360.0
            elif src.x0 + (nw - 1) * src.dx <= 180:
                lon = ((lon + 180.0) % 360.0) - 180.0
            col, row = (lon - src.x0) / src.dx, (src.y0 - lat) / src.dy
        else:
            col, row = (EE - src.x0) / src.dx, (src.y0 - NN) / src.dy
        inside = (col >= 0) & (col <= nw - 1) & (row >= 0) & (row <= nh - 1)
        if not inside.any():
            continue
        c0, c1 = int(math.floor(col[inside].min())), int(math.ceil(col[inside].max())) + 2
        q0, q1 = int(math.floor(row[inside].min())), int(math.ceil(row[inside].max())) + 2
        sub = np.array(src.z[q0:q1, c0:c1], dtype=np.float32)
        if src.nodata is not None:
            sub[sub == np.float32(src.nodata)] = np.nan
        sub[np.abs(sub) > 1e30] = np.nan
        vals = map_coordinates(sub, [row - q0, col - c0], order=1, mode="constant", cval=np.nan)
        vals[~inside] = np.nan
        out[r0:r1] = vals
    return out


# --------------------------------------------------------------------------
# Derivatives (operate on a tile with halo rows; outer ring / short windows -> NaN)
# --------------------------------------------------------------------------
def _box(a, W):
    from scipy.ndimage import uniform_filter
    return uniform_filter(a, size=W, mode="constant", cval=0.0) * (W * W)


def gradients(z, h):
    """Horn 3x3 gradients (m/m): dz/dEast, dz/dNorth; NaN on the outer ring."""
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, hh, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdE = ((c + 2 * f + i) - (a + 2 * d + g)) / (8.0 * h)
    dzdN = -((g + 2 * hh + i) - (a + 2 * b + c)) / (8.0 * h)  # rows run southwards
    pad = lambda arr: np.pad(arr, 1, constant_values=np.nan)
    return pad(dzdE), pad(dzdN)


def tri_wilson(z):
    c = z[1:-1, 1:-1]
    s = np.zeros_like(c)
    for di in (0, 1, 2):
        for dj in (0, 1, 2):
            if di == 1 and dj == 1:
                continue
            s += np.abs(z[di:z.shape[0] - 2 + di, dj:z.shape[1] - 2 + dj] - c)
    return np.pad(s / 8.0, 1, constant_values=np.nan)


def rough_std_detrended(z, W):
    """Std-dev of residuals after least-squares plane fit in each WxW window."""
    from scipy.ndimage import correlate1d, uniform_filter1d
    r = (W - 1) // 2
    valid = np.isfinite(z)
    z0 = np.where(valid, z.astype(np.float64), 0.0)
    m = z0[valid].mean() if valid.any() else 0.0
    z0 = np.where(valid, z0 - m, 0.0)
    N = float(W * W)
    dj = np.arange(-r, r + 1, dtype=np.float64)
    s1, s2 = _box(z0, W), _box(z0 * z0, W)
    sxz = correlate1d(uniform_filter1d(z0, W, axis=0, mode="constant") * W, dj, axis=1, mode="constant")
    syz = correlate1d(uniform_filter1d(z0, W, axis=1, mode="constant") * W, dj, axis=0, mode="constant")
    sxx = W * float((dj ** 2).sum())
    ss_res = (s2 - s1 * s1 / N) - sxz ** 2 / sxx - syz ** 2 / sxx
    out = np.sqrt(np.maximum(ss_res, 0.0) / max(N - 3.0, 1.0))
    out[_box(valid.astype(np.float64), W) < N - 0.5] = np.nan
    return out.astype(np.float32)


def vrm(dzdE, dzdN, W):
    """Sappington et al. (2007): 1 - |sum of unit surface normals| / N."""
    valid = np.isfinite(dzdE) & np.isfinite(dzdN)
    gE, gN = np.where(valid, dzdE, 0.0).astype(np.float64), np.where(valid, dzdN, 0.0).astype(np.float64)
    nrm = np.sqrt(gE * gE + gN * gN + 1.0)
    sx, sy, sz = _box(-gE / nrm * valid, W), _box(-gN / nrm * valid, W), _box(valid / nrm, W)
    N = float(W * W)
    out = 1.0 - np.sqrt(sx * sx + sy * sy + sz * sz) / N
    out[_box(valid.astype(np.float64), W) < N - 0.5] = np.nan
    return np.maximum(out, 0.0).astype(np.float32)


def tile_products(zt, h, windows, want):
    res = {}
    dzdE, dzdN = gradients(zt, h)
    if "slope" in want:
        res["slope"] = np.degrees(np.arctan(np.hypot(dzdE, dzdN))).astype(np.float32)
    if "aspect" in want:
        az = np.degrees(np.arctan2(-dzdE, -dzdN)) % 360.0
        az[np.hypot(dzdE, dzdN) < 1e-9] = np.nan
        res["aspect"] = az.astype(np.float32)
    if "grad" in want:
        res["dzdE"], res["dzdN"] = dzdE.astype(np.float32), dzdN.astype(np.float32)
    if "tri" in want:
        res["tri"] = tri_wilson(zt).astype(np.float32)
    for W in windows:
        if "rstd" in want:
            res[f"rstd_w{W}"] = rough_std_detrended(zt, W)
        if "vrm" in want:
            res[f"vrm_w{W}"] = vrm(dzdE, dzdN, W)
    return res


# --------------------------------------------------------------------------
# GeoTIFF output
# --------------------------------------------------------------------------
def _tags(g):
    keys = (1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, int(g["epsg"] or 32767))
    return [(33550, "d", 3, (g["cell"], g["cell"], 0.0), False),
            (33922, "d", 6, (0.0, 0.0, 0.0, g["emin"], g["nmax"], 0.0), False),
            (34735, "H", len(keys), keys, False),
            (42113, "s", 3, "nan", False)]


def open_output(path, g):
    import tifffile
    try:
        return tifffile.memmap(path, shape=(g["ny"], g["nx"]), dtype=np.float32,
                               photometric="minisblack", extratags=_tags(g))
    except Exception:
        return np.full((g["ny"], g["nx"]), np.nan, dtype=np.float32)


def close_output(arr, path, g):
    import tifffile
    if isinstance(arr, np.memmap):
        arr.flush()
    else:
        tifffile.imwrite(path, arr, photometric="minisblack", extratags=_tags(g))


# --------------------------------------------------------------------------
def odd(n):
    n = int(round(n))
    n = max(3, n)
    return n if n % 2 else n + 1


def stat_line(a):
    v = a[np.isfinite(a)]
    if v.size == 0:
        return "no valid cells"
    p = np.percentile(v[:: max(1, v.size // 2_000_000)], [1, 50, 99])
    return f"min {v.min():.4g}  p1 {p[0]:.4g}  median {p[1]:.4g}  p99 {p[2]:.4g}  max {v.max():.4g}"


def process(path, args):
    print(f"\n== {path}")
    src = read_source(path, args.input_epsg)
    if args.bbox:
        src.crop(args.bbox)
    nh, nw = src.shape
    print(f"  input: {nw} x {nh} cells, {'lon/lat' if src.geographic else 'EPSG:%s' % src.epsg}, "
          f"spacing {src.dx:g} x {src.dy:g}{' deg' if src.geographic else ' m'}")
    g = plan_grid(src, args.zone, args.cell, args.max_cells)
    if g["zone"]:
        print(f"  UTM zone {g['zone']}{'S' if g['south'] else 'N'} (EPSG:{g['epsg']}), cell {g['cell']:g} m, "
              f"grid {g['nx']} x {g['ny']} = {g['nx']*g['ny']:,} cells")
    else:
        print(f"  projected EPSG:{g['epsg']} (assumed metres), cell {g['cell']:g} m, grid {g['nx']} x {g['ny']}")
    if g.get("offcm") and g["offcm"] > 6:
        print(f"  WARNING: data reach {g['offcm']:.1f} deg from zone {g['zone']}'s central meridian; UTM scale "
              f"error is ~{100*(_K0*(1+(math.radians(g['offcm']))**2/2)-1):.1f}% there, which biases slopes by the "
              "same fraction. Crop with --bbox or pick --zone nearer the survey.")

    Z = resample_to_utm(src, g)
    if args.depth_positive:
        Z *= -1.0
    if args.z_scale != 1.0:
        Z *= np.float32(args.z_scale)
    print(f"  elevation: {stat_line(Z)}")

    windows = sorted({odd(m / g["cell"]) for m in args.window_m} if args.window_m
                     else {odd(c) for c in args.window_cells})
    if "rstd" in args.products or "vrm" in args.products:
        print("  roughness windows: " + ", ".join(f"{w}x{w} cells (~{w*g['cell']:g} m)" for w in windows))
    halo = max(1, (max(windows) - 1) // 2 + 1) if windows else 1

    stem = os.path.splitext(os.path.basename(path))[0]
    outdir = args.outdir or os.path.join(os.path.dirname(os.path.abspath(path)), "derived")
    os.makedirs(outdir, exist_ok=True)
    outs, paths = {}, {}

    def get_out(name):
        if name not in outs:
            paths[name] = os.path.join(outdir, f"{stem}_{name}.tif")
            outs[name] = open_output(paths[name], g)
        return outs[name]

    if "elev" in args.products:
        get_out("elev")[:] = Z
    ny, nx = Z.shape
    tile_rows = max(2 * halo + 8, int(3e6 // nx))
    for r0 in range(0, ny, tile_rows):
        r1 = min(ny, r0 + tile_rows)
        a, b = max(0, r0 - halo), min(ny, r1 + halo)
        res = tile_products(Z[a:b], g["cell"], windows, set(args.products))
        for k, v in res.items():
            get_out(k)[r0:r1] = v[r0 - a: r1 - a]
    for name, arr in outs.items():
        print(f"  {name:12s} {stat_line(arr)}")
        close_output(arr, paths[name], g)
    print(f"  wrote {len(outs)} GeoTIFFs -> {outdir}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help=".tif/.tiff/.grd/.nc/.asc elevation or depth grid(s)")
    ap.add_argument("-o", "--outdir", help="output folder (default: 'derived' next to each input)")
    ap.add_argument("--zone", type=int, help="UTM zone 1-60 (default: zone of the grid centre)")
    ap.add_argument("--cell", type=float, help="output UTM cell size in metres (default: native spacing)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("XMIN", "XMAX", "YMIN", "YMAX"),
                    help="crop first, in the input's own coordinates (lon lon lat lat for lon/lat grids)")
    ap.add_argument("--window-cells", type=lambda s: [int(v) for v in s.split(",")], default=[3, 9, 27],
                    help="roughness window sizes in cells, comma list (default 3,9,27)")
    ap.add_argument("--window-m", type=lambda s: [float(v) for v in s.split(",")],
                    help="roughness window sizes in metres, comma list (overrides --window-cells)")
    ap.add_argument("--products", type=lambda s: s.split(","), default=["elev", "slope", "aspect", "tri", "rstd", "vrm"],
                    help="subset of: elev,slope,aspect,grad,tri,rstd,vrm")
    ap.add_argument("--depth-positive", action="store_true", help="input stores depth as positive numbers")
    ap.add_argument("--z-scale", type=float, default=1.0, help="multiply z by this (units to metres, etc.)")
    ap.add_argument("--input-epsg", type=int, help="override/declare the input CRS (4326 or a metric EPSG)")
    ap.add_argument("--max-cells", type=int, default=250_000_000)
    args = ap.parse_args()
    bad = set(args.products) - {"elev", "slope", "aspect", "grad", "tri", "rstd", "vrm"}
    if bad:
        ap.error(f"unknown products {sorted(bad)}")
    for p in args.inputs:
        process(p, args)


if __name__ == "__main__":
    main()
