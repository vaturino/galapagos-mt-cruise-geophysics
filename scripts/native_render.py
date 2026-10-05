#!/usr/bin/env python3
"""
native_render.py -- every gridded dataset in this repo, rendered or exported at its
NATIVE resolution (one output pixel per source grid cell, in the source's own CRS).
No downsampling and no reprojection anywhere, unless a WGS-84 copy is explicitly
requested for a projected source (see --wgs84).

Used three ways:
  1. Full-dataset maps:   python native_render.py all  [--out ../Native_Maps]
                          python native_render.py one MV1007_bathymetry
  2. Clip export (ship navigation / GIS), any bbox in lon/lat:
        python native_render.py clip Mittelstaedt_50m -91.3 -91.1 0.6 0.8 --out D07 --nav
  3. As a library (make_dredge_packets.py, the viewer's /export_native endpoint):
        from native_render import DATASETS, load, clip, write_elev_tif, write_color_tif, colorize

Outputs for a dataset or clip <name>:
  <name>_elev_native.tif   float32 elevation (m) for bathymetry, exactly the source numbers, native CRS
                           (NaN = no data); <name>_value_native.tif for backscatter / geophysics;
                           <name>_slope_deg_native.tif = seafloor slope (deg) at the native cell size.
  <name>_color_native.tif  RGB uint8 picture (colour ramp + hillshade for bathymetry), with an
                           internal no-data mask. --nav: baseline strip GeoTIFF (LZW, no tiles,
                           no BigTIFF, no overviews, white no-data) plus .tfw world file and .prj,
                           the most widely readable form for navigation software.
  <name>_map.png           the colour raster placed pixel-for-pixel (figimage, no resampling) with
                           coordinate axes, colour bar, title, and the planned/previous sites.
  <name>_info.json         source file, CRS, grid size and spacing, value range, colour range.

Environment: claude-science-env (needs rasterio, pyproj, cmocean, matplotlib).
    source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env
"""
import argparse
import glob
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

B = "GMRT_regional/Backscatter_MGDS/"
G = "GMRT_regional/Geophysics_MGDS/"
F = "FOR_TUSHAR/"

# id -> source file(s) (repo-relative; globs allowed), CRS, kind, label, colour settings.
# Each file is the finest version of that dataset in the repo (resampled mosaics, e.g.
# GeoMapApp_ready/GMRT_regional/TN188_bathymetry_mosaic.grd at 40.8 m from 7 m tiles,
# are deliberately NOT used). kind: bathy | backscatter | anomaly (diverging, symmetric
# about 0) | sequential.
DATASETS = {
    # ---------------------------------------------------------------- bathymetry
    "GMRT_corridor": dict(paths=["GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap_clean.grd"], epsg=4326,
                          kind="bathy", label="GMRT corridor basemap (spike-repaired), ~245 m", units="m"),
    "DOA_ETP_MBES": dict(paths=["DOA_ETP_MBES/MBES_bathymetric_compilation_V1_2025.tif"], epsg=3395,
                         kind="bathy", label="DOA-ETP MBES compilation V1 2025, 100 m (World Mercator)", units="m"),
    "MV1007_bathymetry": dict(paths=["GeoMapApp_ready/MV1007_bathymetry_mosaic.grd"], epsg=4326,
                              kind="bathy", label="MV1007 (2010) multibeam bathymetry mosaic, 50 m", units="m"),
    "GSC_97-86W_100m": dict(paths=[B + "GSC_97-86W_Compilation/GSC_97-86W_100m_comp.grd"], epsg=4326,
                            kind="bathy", label="GSC 97-86 W compilation, 100 m", units="m"),
    "DRFT04RR_bathymetry_100m": dict(paths=[B + "DRFT04RR_GSC_Bathymetry/galapagos.100m.comb_geomapapp.grd"],
                                     epsg=4326, kind="bathy", label="DRFT04RR GSC bathymetry, 100 m", units="m"),
    "DRFT04RR_86W_bathymetry_10m": dict(paths=[B + "DRFT04RR_GSC_Bathymetry/86w_all_bathy_10m.grd"], epsg=4326,
                                        kind="bathy", label="DRFT04RR 86 W bathymetry, 10 m", units="m"),
    "TN188_8m": dict(paths=[B + "TN188_GSC_Bathymetry_8m/TN188_DSL120A_8mbat.*_geomapapp.grd"], epsg=4326,
                     kind="bathy", label="TN188 DSL-120A bathymetry, ~7 m", units="m"),
    "Mittelstaedt_50m": dict(paths=[F + "CUT_bath_clean.tif"], epsg=4326, kind="bathy",
                             label="Mittelstaedt/Young Galapagos platform bathymetry, 50 m", units="m"),
    "AT5009_multibeam": dict(paths=[B + "AT50-09BC_GalapagosPlatform_Bathymetry/A*_WGS84_*m.grd"], epsg=4326,
                             kind="bathy", label="AT50-09BC Galapagos platform multibeam (15-50 m)", units="m"),
    # ---------------------------------------------------------------- backscatter / sidescan
    "MV1007_backscatter": dict(paths=["GeoMapApp_ready/MV1007_backscatter_mosaic.grd"], epsg=4326,
                               kind="backscatter", label="MV1007 backscatter mosaic", units="amplitude"),
    "DRFT04RR_sidescan": dict(paths=[B + "DRFT04RR_GSC_Backscatter/*.grd", B + "DRFT04RR_GSC_Backscatter/*.asc"],
                              epsg=32615, kind="backscatter", label="DRFT04RR MR1 sidescan (8-16 m, UTM 15N)",
                              units="amplitude"),
    "MGL1106_sidescan_5m": dict(paths=[B + "MGL1106_CostaRica_CRISP/Sidescan_5m.grd"], epsg=4326,
                                kind="backscatter", label="MGL1106 CRISP sidescan, 5 m", units="amplitude"),
    # ---------------------------------------------------------------- geophysics
    "Barckhausen_magnetics": dict(paths=[G + "Barckhausen_CentralAmerica_Magnetics/central_america_mag_geomapapp.grd"],
                                  epsg=4326, kind="anomaly", label="Barckhausen Central America magnetic anomaly",
                                  units="nT"),
    "Bassett_residual_gravity": dict(paths=[G + "Bassett_CentralAmerica_ResidualGravity/CentAm_Residual_gravity_geomapapp.grd"],
                                     epsg=4326, kind="anomaly", label="Bassett Central America residual gravity",
                                     units="mGal"),
    "SR1806_MBA": dict(paths=[G + "SR1806_CocosNazca_Gravity/105W95W1S5N_mba.grd"], epsg=4326, kind="anomaly",
                       label="SR1806 mantle Bouguer anomaly", units="mGal"),
    "SR1806_FAA_GlobalTopo": dict(paths=[G + "SR1806_CocosNazca_Gravity/105W95W1S5N_mba_global_topo_global_FAA.grd"],
                                  epsg=4326, kind="anomaly", label="SR1806 MBA (global topo, global FAA)", units="mGal"),
    "SR1806_FAA_ShipTopo": dict(paths=[G + "SR1806_CocosNazca_Gravity/105W95W1S5N_mba_ship_topo_global_FAA.grd"],
                                epsg=4326, kind="anomaly", label="SR1806 MBA (ship topo, global FAA)", units="mGal"),
    "SR1806_RMBA": dict(paths=[G + "SR1806_CocosNazca_Gravity/105W95W1S5N_rmba_1k.grd"], epsg=4326, kind="anomaly",
                        label="SR1806 residual mantle Bouguer anomaly", units="mGal"),
    "SR1806_CrustThickness": dict(paths=[G + "SR1806_CocosNazca_Gravity/105W95W1S5N_crust_1k.grd"], epsg=4326,
                                  kind="sequential", label="SR1806 crustal thickness", units="km"),
    "SR1806_ThermalAnomaly": dict(paths=[G + "SR1806_CocosNazca_Gravity/105W95W1S5N_thermal_1k.grd"], epsg=4326,
                                  kind="anomaly", label="SR1806 thermal anomaly", units="(as source)"),
    "Mittelstaedt_FreeAir": dict(paths=[F + "CUT_FA.grd"], epsg=4326, kind="anomaly",
                                 label="Mittelstaedt free-air gravity", units="mGal"),
    "Mittelstaedt_MagAnomaly": dict(paths=[F + "CUT_maganom_1km_blockmed.grd"], epsg=4326, kind="anomaly",
                                    label="Mittelstaedt magnetic anomaly (1 km block median)", units="nT"),
    "Mittelstaedt_Magnetization": dict(paths=[F + "CUT_magnetization.grd"], epsg=4326, kind="anomaly",
                                       label="Mittelstaedt crustal magnetization", units="A/m"),
    "Mittelstaedt_RMBA": dict(paths=[F + "CUT_RMBA.grd"], epsg=4326, kind="anomaly",
                              label="Mittelstaedt residual mantle Bouguer anomaly", units="mGal"),
}

# viewer dataset id (Viewer3D/data/manifest.json) -> DATASETS id, for the viewer's native export
VIEWER_IDS = {
    "GMRT_basemap": "GMRT_corridor", "DOA_ETP_MBES_corridor": "DOA_ETP_MBES", "MV1007": "MV1007_bathymetry",
    "GSC_regional": "GSC_97-86W_100m", "DRFT04RR": "DRFT04RR_bathymetry_100m", "TN188": "TN188_8m",
    "Mittelstaedt_Galapagos_Bathy": "Mittelstaedt_50m", "Mittelstaedt_50m_NW": "Mittelstaedt_50m",
    "Mittelstaedt_50m_NE": "Mittelstaedt_50m", "Mittelstaedt_50m_SW": "Mittelstaedt_50m",
    "Mittelstaedt_50m_SE": "Mittelstaedt_50m",
    "Geophys_Barckhausen_Magnetics": "Barckhausen_magnetics", "Geophys_Bassett_ResidualGravity": "Bassett_residual_gravity",
    "Geophys_SR1806_MBA": "SR1806_MBA", "Geophys_SR1806_FAA_GlobalTopo": "SR1806_FAA_GlobalTopo",
    "Geophys_SR1806_FAA_ShipTopo": "SR1806_FAA_ShipTopo", "Geophys_SR1806_RMBA": "SR1806_RMBA",
    "Geophys_SR1806_CrustThickness": "SR1806_CrustThickness", "Geophys_SR1806_ThermalAnomaly": "SR1806_ThermalAnomaly",
    "Geophys_Mittelstaedt_FreeAir": "Mittelstaedt_FreeAir", "Geophys_Mittelstaedt_MagAnomaly": "Mittelstaedt_MagAnomaly",
    "Geophys_Mittelstaedt_Magnetization": "Mittelstaedt_Magnetization", "Geophys_Mittelstaedt_RMBA": "Mittelstaedt_RMBA",
}


def source_files(ds_id):
    out = []
    for p in DATASETS[ds_id]["paths"]:
        out += sorted(glob.glob(str(REPO / p)))
    return out


# ============================================================================ reading
@dataclass
class Grid:
    """Node-registered grid. x ascending (cell centres), y DESCENDING (row 0 = north/top),
    z float32 (ny, nx) in image order. epsg = CRS of x/y."""
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    epsg: int
    source: str
    meta: dict = field(default_factory=dict)

    @property
    def dx(self):
        return float(self.x[1] - self.x[0]) if self.x.size > 1 else float("nan")

    @property
    def dy(self):  # positive
        return float(self.y[0] - self.y[1]) if self.y.size > 1 else float("nan")

    @property
    def transform(self):
        from rasterio.transform import from_origin
        return from_origin(self.x[0] - self.dx / 2, self.y[0] + self.dy / 2, self.dx, self.dy)

    @property
    def geographic(self):
        return self.epsg == 4326

    def cell_size_m(self):
        """(dx, dy) in metres at the grid centre."""
        if not self.geographic:
            return abs(self.dx), abs(self.dy)
        lat = math.radians(float(np.mean(self.y)))
        return abs(self.dx) * 111320.0 * math.cos(lat), abs(self.dy) * 110574.0


def _regular(v, name, path):
    """Check uniform spacing and return the exactly regular axis (least-squares line through the
    stored coordinates). Coordinates stored as float32 (e.g. the TN188 originals at 265 deg E)
    carry rounding of up to ~0.12 cell; anything over half a cell is a genuinely irregular axis."""
    if v.size < 3:
        return v
    i = np.arange(v.size, dtype=np.float64)
    b, a = np.polyfit(i, v, 1)
    if np.abs(v - (a + b * i)).max() > 0.5 * abs(b):
        raise ValueError(f"{path}: {name} spacing is not uniform -- cannot georeference as a plain grid")
    return a + b * i


def _window(x_asc, y_asc, bbox_xy, pad_cells=0):
    """Index slices of the cells whose centres fall inside bbox_xy=(xmin,xmax,ymin,ymax)."""
    if bbox_xy is None:
        return slice(0, x_asc.size), slice(0, y_asc.size)
    x0, x1, y0, y1 = bbox_xy
    i0 = max(0, int(np.searchsorted(x_asc, x0, "left")) - pad_cells)
    i1 = min(x_asc.size, int(np.searchsorted(x_asc, x1, "right")) + pad_cells)
    j0 = max(0, int(np.searchsorted(y_asc, y0, "left")) - pad_cells)
    j1 = min(y_asc.size, int(np.searchsorted(y_asc, y1, "right")) + pad_cells)
    return slice(i0, i1), slice(j0, j1)


def load(path, epsg, bbox_xy=None, pad_cells=0):
    """Read a grid (or only the window covering bbox_xy, in the grid's own CRS) into a Grid."""
    path = str(path)
    low = path.lower()
    with open(path, "rb") as fh:
        hdf5 = fh.read(8) == b"\x89HDF\r\n\x1a\n"  # GMT>=6 NetCDF-4 '.grd' (the FOR_TUSHAR grids)
    if low.endswith((".tif", ".tiff")) or hdf5:  # GDAL presents both north-up with a correct transform
        import rasterio
        from rasterio.windows import Window
        with rasterio.open(path) as s:
            t = s.transform
            if abs(t.b) > 1e-12 or abs(t.d) > 1e-12:
                raise ValueError(f"{path}: rotated raster not supported")
            src_epsg = s.crs.to_epsg() if s.crs else epsg
            x = t.c + (np.arange(s.width) + 0.5) * t.a
            y_top = t.f + (np.arange(s.height) + 0.5) * t.e  # descending
            ix, iy = _window(x, y_top[::-1], bbox_xy, pad_cells)
            # iy indexes the ascending copy; convert to top-down rows
            r0, r1 = s.height - iy.stop, s.height - iy.start
            z = s.read(1, window=Window(ix.start, r0, ix.stop - ix.start, r1 - r0)).astype(np.float32)
            if s.nodata is not None and not np.isnan(s.nodata):
                z[z == np.float32(s.nodata)] = np.nan
            return Grid(x[ix].copy(), y_top[r0:r1].copy(), z, src_epsg, path)
    # NetCDF: old-style GMT (x_range/...), ESRI ascii, or modern x/y/z | lon/lat/altitude
    if low.endswith(".asc"):
        from fix_mgds_grid import load_any
        xa, ya, za = load_any(path)
    else:
        from netcdf_lite import netcdf_file
        nc = netcdf_file(path, "r", mmap=True)
        names = set(nc.variables)
        if {"x_range", "y_range", "dimension"}.issubset(names):
            nc.close()
            from fix_mgds_grid import load_any
            xa, ya, za = load_any(path)  # x, y ascending, z rows match y
        else:
            xn, yn, zn = ("x", "y", "z") if "x" in names else ("lon", "lat", "altitude")
            xa = np.array(nc.variables[xn][:], dtype=np.float64)
            ya = np.array(nc.variables[yn][:], dtype=np.float64)
            zv = nc.variables[zn]
            if xa[0] > xa[-1]:
                raise ValueError(f"{path}: descending x not supported")
            if epsg == 4326 and xa.max() > 180:  # 0-360 longitudes (TN188 originals)
                xa = xa - 360.0
            flip_y = ya[0] > ya[-1]
            if flip_y:
                ya = ya[::-1]
            ix, iy = _window(xa, ya, bbox_xy, pad_cells)
            if flip_y:
                rows = slice(zv.shape[0] - iy.stop, zv.shape[0] - iy.start)
                za = np.array(zv[rows, ix], dtype=np.float32)[::-1]
            else:
                za = np.array(zv[iy, ix], dtype=np.float32)
            fill = getattr(zv, "_attributes", {}).get("_FillValue")
            sf = float(getattr(zv, "_attributes", {}).get("scale_factor", 1.0))
            ao = float(getattr(zv, "_attributes", {}).get("add_offset", 0.0))
            if fill is not None:
                za[za == np.float32(fill)] = np.nan
            if sf != 1.0 or ao != 0.0:
                za = za * sf + ao
            xa, ya = xa[ix], ya[iy]
            nc.close()
            bbox_xy = None  # window already applied
    xa = np.asarray(xa, np.float64)
    ya = np.asarray(ya, np.float64)
    za = np.asarray(za, np.float32)
    if epsg == 4326 and xa.max() > 180:  # 0-360 longitudes (TN188 originals)
        xa = xa - 360.0
    if bbox_xy is not None:
        ix, iy = _window(xa, ya, bbox_xy, pad_cells)
        xa, ya, za = xa[ix], ya[iy], za[iy, ix]
    xa = _regular(xa, "x", path)
    ya = _regular(ya, "y", path)
    return Grid(xa, ya[::-1].copy(), np.ascontiguousarray(za[::-1]), epsg, path)


def lonlat_bbox_to_crs(bbox_ll, epsg):
    """(lon0, lon1, lat0, lat1) -> (x0, x1, y0, y1) in epsg, densified so the box is covered."""
    if epsg == 4326:
        return bbox_ll
    from pyproj import Transformer
    tr = Transformer.from_crs(4326, epsg, always_xy=True)
    lo = np.linspace(bbox_ll[0], bbox_ll[1], 21)
    la = np.linspace(bbox_ll[2], bbox_ll[3], 21)
    LO, LA = np.meshgrid(lo, la)
    X, Y = tr.transform(LO.ravel(), LA.ravel())
    return float(np.min(X)), float(np.max(X)), float(np.min(Y)), float(np.max(Y))


def clip(ds_id, bbox_ll, pad_cells=1):
    """Native-resolution clip of dataset ds_id over a lon/lat box. Returns the Grid from the
    first source file with finite data in the box (multi-file datasets: the finest such file),
    or None if no file covers it."""
    spec = DATASETS[ds_id]
    best = None
    for p in source_files(ds_id):
        try:
            g = load(p, spec["epsg"], lonlat_bbox_to_crs(bbox_ll, spec["epsg"]), pad_cells)
        except (ValueError, IndexError):
            continue
        if min(g.z.shape) < 3 or not np.isfinite(g.z).any():  # box outside (or only touching) this grid
            continue
        cov = float(np.isfinite(g.z).mean())
        key = (cov > 0.5, -g.cell_size_m()[0], cov)
        if best is None or key > best[0]:
            best = (key, g)
    return best[1] if best else None


# ============================================================================ colour
def color_range(z, kind):
    v = z[np.isfinite(z)]
    if v.size == 0:
        return 0.0, 1.0
    if kind == "anomaly":
        a = float(np.percentile(np.abs(v), 98))
        return -a, a
    if kind == "slope":  # fixed scale so slope maps compare across sites and grids
        return 0.0, 40.0
    if kind == "bathy":
        sea = v[v < 0]
        lo = float(np.percentile(sea, 0.5)) if sea.size else float(v.min())
        hi = float(np.percentile(sea, 99.5)) if sea.size else float(v.max())
        return lo, min(hi, 0.0) if sea.size else hi
    return float(np.percentile(v, 1)), float(np.percentile(v, 99))


def _cmap(kind, name=None):
    """Colour map for a kind, or any named map: matplotlib names ('viridis', 'turbo'...), cmocean
    ('cmo.deep', 'cmo.haline'...), or cmcrameri ('cmc.batlow', 'cmc.oslo'...) if installed.
    A trailing '_r' reverses it."""
    import cmocean  # noqa: F401  (registers the cmo.* names)
    import matplotlib.pyplot as plt
    if name:
        if name.startswith("cmc."):
            import cmcrameri.cm as cmc
            m = getattr(cmc, name[4:].removesuffix("_r"))
            return m.reversed() if name.endswith("_r") else m
        return plt.get_cmap(name)
    return {"bathy": cmocean.cm.deep_r, "backscatter": plt.get_cmap("gray"),
            "anomaly": plt.get_cmap("RdBu_r"), "sequential": plt.get_cmap("viridis"),
            "slope": plt.get_cmap("YlOrRd")}[kind]


LAND_CMAP_STOPS = [(0.0, (0.35, 0.55, 0.30)), (0.25, (0.62, 0.70, 0.42)), (0.6, (0.80, 0.70, 0.50)),
                   (1.0, (0.97, 0.95, 0.92))]


def hillshade(g, azimuth=315.0, altitude=45.0, vexag=2.0, block=2048):
    """Lambertian hillshade in [0,1] from true metric slopes (per-row longitude scale for
    geographic grids). Processed in row blocks with a 1-row halo to bound memory."""
    ny, nx = g.z.shape
    out = np.empty((ny, nx), np.float32)
    az, alt = math.radians(360.0 - azimuth + 90.0), math.radians(altitude)
    dxm_const, dym = g.cell_size_m()
    for r0 in range(0, ny, block):
        r1 = min(ny, r0 + block)
        a0, a1 = max(0, r0 - 1), min(ny, r1 + 1)
        zb = g.z[a0:a1].astype(np.float32) * vexag
        if g.geographic:
            dxm = (abs(g.dx) * 111320.0 * np.cos(np.radians(g.y[a0:a1])))[:, None].astype(np.float32)
        else:
            dxm = np.float32(dxm_const)
        gy, gx = np.gradient(zb)
        gx = gx / dxm
        gy = -gy / np.float32(dym)  # rows run north->south
        slope = np.pi / 2 - np.arctan(np.hypot(gx, gy))
        aspect = np.arctan2(-gx, gy)
        hs = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
        out[r0:r1] = np.clip(hs, 0, 1)[r0 - a0:r0 - a0 + (r1 - r0)]
    return out


def _ground_dx_per_row(g, rows):
    """True east-west cell size (m) for the given rows: geographic -> dlon * 111320 cos(lat);
    World Mercator (3395) -> dx * cos(lat) (Mercator scale factor 1/cos lat); other projected
    CRSs (UTM) -> dx (scale factor within 0.04 %)."""
    y = g.y[rows]
    if g.geographic:
        return abs(g.dx) * 111320.0 * np.cos(np.radians(y))
    if g.epsg == 3395:
        lat = 2 * np.arctan(np.exp(y / 6378137.0)) - np.pi / 2  # spherical inverse, adequate for the scale factor
        return abs(g.dx) * np.cos(lat)
    return np.full(y.shape, abs(g.dx))


def slope_half_cells(g, baseline_m=None):
    """Half-width k (cells) of the central difference for a requested baseline (m). The baseline
    actually used is 2k cells; the finest possible is k = 1 (two cells)."""
    if not baseline_m:
        return 1
    return max(1, int(round(baseline_m / (2.0 * g.cell_size_m()[0]))))


def slope_grid(g, baseline_m=None, block=2048):
    """Seafloor slope (degrees) in the steepest direction (gradient magnitude), on the grid's own
    cells. Central differences over 2k cells, (z[i+k] - z[i-k]) / (2k * cell), in x and y, with true
    metric spacing per row. k comes from baseline_m (default k = 1: the neighbouring cells, the
    finest the grid supports). NaN where a needed neighbour is no-data or off the grid edge."""
    k = slope_half_cells(g, baseline_m)
    ny, nx = g.z.shape
    out = np.full((ny, nx), np.nan, np.float32)
    dym_geo = g.cell_size_m()[1]
    for r0 in range(0, ny, block):
        r1 = min(ny, r0 + block)
        a0, a1 = max(0, r0 - k), min(ny, r1 + k)
        zb = g.z[a0:a1].astype(np.float32)
        dxm = _ground_dx_per_row(g, slice(a0, a1)).astype(np.float32)[:, None]
        dym = np.full_like(dxm, dym_geo) if g.geographic else dxm  # projected grids here have square cells
        gx = np.full_like(zb, np.nan)
        gy = np.full_like(zb, np.nan)
        if zb.shape[1] > 2 * k:
            gx[:, k:-k] = (zb[:, 2 * k:] - zb[:, :-2 * k]) / (2 * k * dxm)
        if zb.shape[0] > 2 * k:  # rows run north -> south: north minus south
            gy[k:-k, :] = (zb[:-2 * k, :] - zb[2 * k:, :]) / (2 * k * dym[k:-k])
        s = np.degrees(np.arctan(np.hypot(gx, gy)))
        out[r0:r1] = s[r0 - a0:r0 - a0 + (r1 - r0)]
    base = 2 * k * g.cell_size_m()[0]
    return Grid(g.x, g.y, out, g.epsg, g.source,
                dict(g.meta, derived="slope_deg", slope_baseline_m=round(base, 1), slope_half_cells=k))


def colorize(g, kind, vrange=None, shade=True, cmap=None):
    """-> (rgb uint8 (ny,nx,3), valid mask (ny,nx) bool, (vmin, vmax))."""
    from matplotlib.colors import LinearSegmentedColormap
    z = g.z
    valid = np.isfinite(z)
    vmin, vmax = vrange or color_range(z, kind)
    cmap = _cmap(kind, cmap)
    rgb = np.empty(z.shape + (3,), np.uint8)
    land_cmap = LinearSegmentedColormap.from_list("land", LAND_CMAP_STOPS)
    zmax = float(np.nanmax(z)) if valid.any() else 1.0
    hs = hillshade(g) if (shade and kind == "bathy") else None
    for r0 in range(0, z.shape[0], 2048):
        r1 = min(z.shape[0], r0 + 2048)
        zb = z[r0:r1]
        t = np.clip((zb - vmin) / (vmax - vmin if vmax > vmin else 1.0), 0, 1)
        c = cmap(np.nan_to_num(t))[..., :3]
        if kind == "bathy" and zmax > 0:
            land = zb > 0
            if land.any():
                c[land] = land_cmap(np.clip(zb[land] / zmax, 0, 1))[..., :3]
        if hs is not None:
            h = hs[r0:r1][..., None]
            c = c * (0.45 + 0.55 * np.nan_to_num(h, nan=0.75) / 0.75).clip(0, 1.25)
        c = np.clip(c, 0, 1)
        c[~valid[r0:r1]] = 1.0  # white no-data
        rgb[r0:r1] = (c * 255 + 0.5).astype(np.uint8)
    return rgb, valid, (float(vmin), float(vmax))


# ============================================================================ writing
def _crs(epsg):
    from rasterio.crs import CRS
    return CRS.from_epsg(epsg)


def write_elev_tif(g, path, overviews=True):
    import rasterio
    from rasterio.enums import Resampling
    big = g.z.nbytes > 3.5e9
    prof = dict(driver="GTiff", width=g.z.shape[1], height=g.z.shape[0], count=1, dtype="float32",
                crs=_crs(g.epsg), transform=g.transform, nodata=np.nan, compress="deflate", predictor=3,
                tiled=True, blockxsize=512, blockysize=512, BIGTIFF="YES" if big else "IF_SAFER")
    with rasterio.open(path, "w", **prof) as d:
        d.write(g.z, 1)
        d.update_tags(SOURCE=os.path.relpath(g.source, REPO), NOTE="native grid values, no resampling")
        if overviews and min(g.z.shape) > 1024:
            d.build_overviews([2, 4, 8, 16, 32], Resampling.average)
    return path


def write_color_tif(g, rgb, valid, path, nav=False):
    """RGB GeoTIFF at native resolution. nav=True -> baseline strip TIFF (LZW, no tiling, no
    overviews, white no-data, no mask band) + .tfw + .prj: the most compatible form."""
    import rasterio
    from rasterio.enums import Resampling
    h, w = rgb.shape[:2]
    if nav:
        if rgb.nbytes > 3.9e9:
            raise ValueError("clip too large for a classic (non-Big) TIFF; use a smaller box")
        prof = dict(driver="GTiff", width=w, height=h, count=3, dtype="uint8", crs=_crs(g.epsg),
                    transform=g.transform, compress="lzw", tiled=False, photometric="RGB", BIGTIFF="NO")
    else:
        prof = dict(driver="GTiff", width=w, height=h, count=3, dtype="uint8", crs=_crs(g.epsg),
                    transform=g.transform, compress="deflate", predictor=2, tiled=True, blockxsize=512,
                    blockysize=512, photometric="RGB", BIGTIFF="IF_SAFER")
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        with rasterio.open(path, "w", **prof) as d:
            for b in range(3):
                d.write(rgb[..., b], b + 1)
            d.update_tags(SOURCE=os.path.relpath(g.source, REPO), NOTE="native resolution, 1 pixel = 1 grid cell")
            if not nav:
                d.write_mask(valid.astype(np.uint8) * 255)
                if min(h, w) > 1024:
                    d.build_overviews([2, 4, 8, 16, 32], Resampling.average)
    if nav:
        t = g.transform
        with open(os.path.splitext(path)[0] + ".tfw", "w") as f:  # world file: pixel-centre origin
            f.write(f"{t.a:.12f}\n{t.d:.12f}\n{t.b:.12f}\n{t.e:.12f}\n{t.c + t.a / 2:.12f}\n{t.f + t.e / 2:.12f}\n")
        with open(os.path.splitext(path)[0] + ".prj", "w") as f:
            f.write(_crs(g.epsg).to_wkt(version="WKT1_ESRI"))
    return path


def to_wgs84(g, kind):
    """Projected grid -> EPSG:4326 at a cell size no coarser than the source (nearest for values
    would alias; bilinear keeps the surface). Only used when explicitly requested."""
    from rasterio.warp import calculate_default_transform, reproject, Resampling
    src_t = g.transform
    w, h = g.z.shape[1], g.z.shape[0]
    left, top = src_t.c, src_t.f
    right, bottom = left + w * src_t.a, top + h * src_t.e
    dst_t, dw, dh = calculate_default_transform(_crs(g.epsg), _crs(4326), w, h, left, bottom, right, top)
    # force square degree pixels no larger than the finest source spacing
    res = min(abs(dst_t.a), abs(dst_t.e))
    dst_t, dw, dh = calculate_default_transform(_crs(g.epsg), _crs(4326), w, h, left, bottom, right, top,
                                                resolution=res)
    out = np.full((dh, dw), np.nan, np.float32)
    reproject(g.z, out, src_transform=src_t, src_crs=_crs(g.epsg), dst_transform=dst_t, dst_crs=_crs(4326),
              resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
    x = dst_t.c + (np.arange(dw) + 0.5) * dst_t.a
    y = dst_t.f + (np.arange(dh) + 0.5) * dst_t.e
    return Grid(x, y, out, 4326, g.source, dict(g.meta, reprojected_from=g.epsg))


# ============================================================================ annotated PNG
def _sites():
    import pandas as pd
    out = {}
    for name, f in (("dredge", "MT_dredging_coords/DredgeSites.csv"), ("mt", "MT_dredging_coords/MTsites.csv"),
                    ("prev", "Site_Maps/Previous_Dredges_Compiled.csv"), ("lines", "MT_dredging_coords/DredgeLines.csv")):
        p = REPO / f
        if p.exists():
            out[name] = pd.read_csv(p)
    return out


def map_png(g, rgb, vrange, kind, title, units, path, sites=True, dpi=100, cmap=None):
    """Raster placed with figimage (pixel-exact, no resampling), axes + colour bar around it."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize
    import textwrap
    h, w = rgb.shape[:2]
    fs = float(np.clip(max(w, h) / 220.0, 11, 48))
    dxm, dym = g.cell_size_m()
    L, R, Bm = int(fs * 9), int(fs * 12), int(fs * 7)
    W = w + L + R
    head = (f"{title}\n{w} x {h} cells, native spacing {dxm:.1f} x {dym:.1f} m, 1 pixel = 1 cell. "
            f"Source: {os.path.relpath(g.source, REPO)}")
    chars = max(30, int(W / (fs * 0.62 * dpi / 72)))
    head = "\n".join(textwrap.fill(s, chars) for s in head.split("\n"))
    T = int(fs * dpi / 72 * 1.35 * (head.count("\n") + 1) + fs * 2)
    H = h + T + Bm
    if max(W, H) >= 65000:
        raise ValueError(f"{path}: {W}x{H} px exceeds the PNG renderer's 65k limit")
    fig = plt.figure(figsize=(W / dpi, H / dpi), dpi=dpi)
    fig.figimage(rgb, xo=L, yo=Bm, origin="upper", zorder=0)
    ax = fig.add_axes([L / W, Bm / H, w / W, h / H])
    ax.set_zorder(2)
    ax.patch.set_alpha(0)
    x0, x1 = g.x[0] - g.dx / 2, g.x[-1] + g.dx / 2
    y1, y0 = g.y[0] + g.dy / 2, g.y[-1] - g.dy / 2
    k = 1.0 if g.geographic else 1e-3
    ax.set_xlim(x0 * k, x1 * k)
    ax.set_ylim(y0 * k, y1 * k)
    ax.tick_params(labelsize=fs, length=fs * 0.6, width=max(1, fs / 12))
    for s in ax.spines.values():
        s.set_linewidth(max(1, fs / 12))
    if g.geographic:
        ax.set_xlabel("longitude (deg)", fontsize=fs)
        ax.set_ylabel("latitude (deg)", fontsize=fs)
    else:
        ax.set_xlabel(f"easting (km, EPSG:{g.epsg})", fontsize=fs)
        ax.set_ylabel(f"northing (km, EPSG:{g.epsg})", fontsize=fs)
    fig.text(fs / W, 1 - fs / H, head, fontsize=fs, va="top", ha="left")
    if sites:
        _overlay_sites(ax, g, k, fs)
    cax = fig.add_axes([(L + w + fs * 2.5) / W, (Bm + h * 0.15) / H, fs * 1.4 / W, h * 0.7 / H])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=Normalize(*vrange), cmap=_cmap(kind, cmap)), cax=cax,
                      extend="both")
    cb.ax.tick_params(labelsize=fs)
    cb.set_label({"bathy": "elevation (m)"}.get(kind, units), fontsize=fs)
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


def _overlay_sites(ax, g, k, fs):
    s = _sites()
    tr = None
    if not g.geographic:
        from pyproj import Transformer
        tr = Transformer.from_crs(4326, g.epsg, always_xy=True)
    x0, x1 = sorted(ax.get_xlim())
    y0, y1 = sorted(ax.get_ylim())

    def xy(lon, lat):
        lon, lat = np.asarray(lon, float), np.asarray(lat, float)
        if tr is None:
            return lon * k, lat * k
        X, Y = tr.transform(lon, lat)
        return np.asarray(X) * k, np.asarray(Y) * k

    def inside(X, Y):
        return (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1)

    ms = fs * 0.9
    if "prev" in s:
        p = s["prev"]
        X, Y = xy(p.lon, p.lat)
        m = inside(X, Y)
        col = p.glass.map({"glass": "#1a9850", "no glass": "#d9d9d9", "no rock": "k"}).values
        ax.scatter(X[m], Y[m], s=ms ** 2, c=col[m], edgecolors="k", linewidths=fs / 15, zorder=5, marker="o")
    if "mt" in s:
        X, Y = xy(s["mt"].longitude, s["mt"].latitude)
        m = inside(X, Y)
        ax.scatter(X[m], Y[m], s=ms ** 2, c="#ffd400", edgecolors="k", marker="D", linewidths=fs / 15, zorder=6)
        for xx, yy, n in zip(X[m], Y[m], s["mt"].site[m]):
            ax.annotate(f"MT{int(n)}", (xx, yy), xytext=(fs * 0.6, fs * 0.6), textcoords="offset points",
                        fontsize=fs * 0.75, zorder=7, color="k",
                        bbox=dict(boxstyle="round,pad=0.15", fc="w", ec="none", alpha=0.7))
    if "lines" in s:
        ln = s["lines"]
        Xa, Ya = xy(ln.start_lon, ln.start_lat)
        Xb, Yb = xy(ln.end_lon, ln.end_lat)
        for xa, ya, xb, yb in zip(Xa, Ya, Xb, Yb):
            if inside(np.array([xa, xb]), np.array([ya, yb])).any():
                ax.annotate("", (xb, yb), (xa, ya), zorder=7,
                            arrowprops=dict(arrowstyle="-|>", lw=fs / 8, color="#ff2d6f", mutation_scale=fs * 1.6))
    if "dredge" in s:
        X, Y = xy(s["dredge"].longitude, s["dredge"].latitude)
        m = inside(X, Y)
        ax.scatter(X[m], Y[m], s=(ms * 1.4) ** 2, c="#d7301f", edgecolors="k", marker="^", linewidths=fs / 12,
                   zorder=8)
        for xx, yy, n in zip(X[m], Y[m], s["dredge"].site[m]):
            ax.annotate(f"D{int(n)}", (xx, yy), xytext=(fs * 0.7, -fs * 1.2), textcoords="offset points",
                        fontsize=fs * 0.9, fontweight="bold", color="#67000d", zorder=9,
                        bbox=dict(boxstyle="round,pad=0.15", fc="w", ec="none", alpha=0.75))


# ============================================================================ drivers
def render_grid(g, kind, name, out_dir, title, units, png=True, nav=False, wgs84=False, sites=True, value_tag=None,
                cmap=None):
    value_tag = value_tag or ("elev" if kind == "bathy" else "deg" if kind == "slope" else "value")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rgb, valid, vr = colorize(g, kind, cmap=cmap)
    files = [write_elev_tif(g, out_dir / f"{name}_{value_tag}_native.tif", overviews=not nav),
             write_color_tif(g, rgb, valid, out_dir / f"{name}_color_native.tif", nav=nav)]
    if png:
        files.append(map_png(g, rgb, vr, kind, title, units, out_dir / f"{name}_map.png", sites=sites, cmap=cmap))
    if wgs84 and not g.geographic:
        gw = to_wgs84(g, kind)
        rgbw, vw, _ = colorize(gw, kind, vrange=vr, cmap=cmap)
        files.append(write_elev_tif(gw, out_dir / f"{name}_{value_tag}_wgs84.tif", overviews=not nav))
        files.append(write_color_tif(gw, rgbw, vw, out_dir / f"{name}_color_wgs84.tif", nav=nav))
    v = g.z[np.isfinite(g.z)]
    dxm, dym = g.cell_size_m()
    info = dict(name=name, title=title, source=os.path.relpath(g.source, REPO), epsg=g.epsg,
                width=int(g.z.shape[1]), height=int(g.z.shape[0]), dx=g.dx, dy=g.dy, dx_m=dxm, dy_m=dym,
                bounds=dict(west=float(g.x[0] - g.dx / 2), east=float(g.x[-1] + g.dx / 2),
                            south=float(g.y[-1] - g.dy / 2), north=float(g.y[0] + g.dy / 2)),
                value_min=float(v.min()) if v.size else None, value_max=float(v.max()) if v.size else None,
                valid_fraction=float(valid.mean()), color_range=vr, kind=kind, units=units,
                files=[os.path.basename(str(f)) for f in files], seconds=round(time.time() - t0, 1))
    with open(out_dir / f"{name}_info.json", "w") as f:
        json.dump(info, f, indent=1)
    return info


def render_slope(g, stem, out_dir, title, png=True, nav=False, wgs84=False, sites=True, baseline_m=None, cmap=None):
    """Slope (deg) products on the grid's own cells: <stem>_slope_deg_native.tif (float32),
    <stem>_slope_color_native.tif, <stem>_slope_map.png. baseline_m: central-difference width
    (default = 2 cells, the finest); a wider baseline gives the slope over that distance."""
    sg = slope_grid(g, baseline_m)
    b = sg.meta["slope_baseline_m"]
    return render_grid(sg, "slope", f"{stem}_slope", out_dir, f"Slope of {title} (over {b:.0f} m)", "slope (degrees)",
                       png=png, nav=nav, wgs84=wgs84, sites=sites, value_tag="deg", cmap=cmap)


def render_dataset(ds_id, out_root, png=True, wgs84=True, slope=True, only_slope=False, baseline_m=None, cmap=None):
    spec = DATASETS[ds_id]
    paths = source_files(ds_id)
    if not paths:
        raise FileNotFoundError(f"{ds_id}: no source files match {spec['paths']}")
    infos = []
    for p in paths:
        g = load(p, spec["epsg"])
        stem = ds_id if len(paths) == 1 else f"{ds_id}__{Path(p).stem.replace('_geomapapp', '')}"
        title = spec["label"] + ("" if len(paths) == 1 else f"  [{Path(p).name}]")
        if not only_slope:
            infos.append(render_grid(g, spec["kind"], stem, Path(out_root) / ds_id, title, spec["units"], png=png,
                                     wgs84=wgs84, cmap=cmap))
            print(f"  {stem}: {infos[-1]['width']}x{infos[-1]['height']} in {infos[-1]['seconds']} s", flush=True)
        if slope and spec["kind"] == "bathy":
            si = render_slope(g, stem, Path(out_root) / ds_id, title, png=png, wgs84=wgs84, baseline_m=baseline_m)
            print(f"  {stem}_slope: {si['width']}x{si['height']} in {si['seconds']} s", flush=True)
            infos.append(si)
        del g
    idx = Path(out_root) / ds_id / ("slope_index.json" if only_slope else "index.json")
    with open(idx, "w") as f:
        json.dump(infos, f, indent=1)
    return infos


TOP_README = """# Native_Maps: every dataset at native resolution

Generated by `scripts/native_render.py` ({date}). **1 output pixel = 1 cell of the source grid**,
in the source's own coordinate system: no downsampling, no resampling, no reprojection. The one
exception is the `*_wgs84.tif` copies made next to natively projected grids.

{n} products, {cells:.2f} billion cells, from {nds} datasets. Folder = dataset; table below.

## Files in each folder

| File | What |
|---|---|
| `<name>_elev_native.tif` | Bathymetry: float32 elevation (m, negative below sea level), exactly the source cells. NaN = no data. Tiled, DEFLATE, internal overviews (the overviews are only for fast zoomed-out display; the full-resolution data is the base level) |
| `<name>_value_native.tif` | Backscatter / geophysics: float32 values, units as listed below |
| `<name>_color_native.tif` | RGB picture of the same cells (bathymetry has hillshade lit from the NW), with an internal no-data mask |
| `<name>_map.png` | The picture placed pixel for pixel (no resampling), with axes, colour bar, title, and the planned dredge sites (red triangles), MT sites (yellow diamonds), dredge lines (pink arrows) and previous dredges (circles: green glass, grey no glass, black no rock). Very large: open in an image viewer and zoom |
| `<name>_slope_deg_native.tif` | Bathymetry only: seafloor slope (degrees), steepest direction, on the same cells. Central differences over 2 cells (the finest possible), true metric spacing. Change the width with `python native_render.py slope --baseline 200` |
| `<name>_slope_color_native.tif`, `<name>_slope_map.png` | The slope as a picture (0-40 deg, YlOrRd: pale = flat, dark red = steep) |
| `*_wgs84.tif` | Lon/lat copy of a natively projected grid (DOA-ETP = World Mercator, DRFT04RR sidescan = UTM 15N), at a cell size no coarser than the source (bilinear) |
| `<name>_info.json`, `index.json`, `slope_index.json` | Source file, CRS, grid size, cell size, bounds, value range, colour range |

The colour maps are cmocean `deep` for bathymetry, grey for backscatter, RdBu_r (symmetric about 0) for anomalies, viridis for crustal thickness, and YlOrRd for slope. Change them with `--cmap NAME` (any matplotlib, `cmo.*` or `cmc.*` name; `_r` reverses). For a box at native resolution with the colour map picked in the 3D viewer's legend, use the viewer's **Export -> Native-resolution GeoTIFF**.

## Regenerate

```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env
cd scripts
python native_render.py all  --out ../Native_Maps      # every dataset (~10 min, ~7 GB)
python native_render.py slope --out ../Native_Maps     # slope maps for the bathymetry datasets
python native_render.py clip Mittelstaedt_50m -91.3 -91.1 0.6 0.8 --out my_box --nav --slope   # any box
python native_render.py readme --out ../Native_Maps    # this file
```

## Contents

| Dataset folder | Grid | Size (cells) | Cell (m) | CRS | Source |
|---|---|---|---|---|---|
{rows}
"""

DS_README = """# {ds}

{label}. Rendered by `scripts/native_render.py` at native resolution (1 pixel = 1 grid cell).
See `../README.md` for what each file type is.

| Product | Cells | Cell size | CRS | Value range | Colour range | Source |
|---|---|---|---|---|---|---|
{rows}
"""


def write_readmes(out):
    import datetime
    out = Path(out)
    rows, n, cells, dss = [], 0, 0, 0
    for ds in DATASETS:
        infos = []
        for f in ("index.json", "slope_index.json"):
            if (out / ds / f).exists():
                infos += json.load(open(out / ds / f))
        if not infos:
            continue
        dss += 1
        drows = []
        for i in infos:
            n += 1
            cells += i["width"] * i["height"]
            vr = f"{i['value_min']:.4g} to {i['value_max']:.4g}" if i["value_min"] is not None else "-"
            drows.append(f"| `{i['name']}` | {i['width']} x {i['height']} | {i['dx_m']:.1f} x {i['dy_m']:.1f} m | "
                         f"EPSG:{i['epsg']} | {vr} {i['units']} | {i['color_range'][0]:.4g} to {i['color_range'][1]:.4g} | "
                         f"`{i['source']}` |")
            if i["kind"] != "slope":
                rows.append(f"| [`{ds}`]({ds}/README.md) | `{i['name']}` | {i['width']} x {i['height']} | {i['dx_m']:.1f} | "
                            f"EPSG:{i['epsg']} | `{i['source']}` |")
        (out / ds / "README.md").write_text(DS_README.format(ds=ds, label=DATASETS[ds]["label"], rows="\n".join(drows)))
    (out / "README.md").write_text(TOP_README.format(date=datetime.date.today().isoformat(), n=n, cells=cells / 1e9,
                                                      nds=dss, rows="\n".join(rows)))
    return out / "README.md"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("all", help="render every dataset")
    a.add_argument("--out", default=str(REPO / "Native_Maps"))
    a.add_argument("--no-png", action="store_true")
    a.add_argument("--skip", nargs="*", default=[])
    a.add_argument("--skip-done", action="store_true", help="skip datasets whose index.json already exists")
    o = sub.add_parser("one", help="render one dataset")
    o.add_argument("dataset", choices=sorted(DATASETS))
    o.add_argument("--out", default=str(REPO / "Native_Maps"))
    o.add_argument("--no-png", action="store_true")
    c = sub.add_parser("clip", help="native-resolution clip over a lon/lat box")
    c.add_argument("dataset", choices=sorted(DATASETS))
    c.add_argument("west", type=float)
    c.add_argument("east", type=float)
    c.add_argument("south", type=float)
    c.add_argument("north", type=float)
    c.add_argument("--out", required=True, help="output folder")
    c.add_argument("--name", default=None)
    c.add_argument("--nav", action="store_true", help="navigation-safe GeoTIFF (+ .tfw/.prj)")
    c.add_argument("--wgs84", action="store_true", help="also write an EPSG:4326 copy of a projected source")
    sl = sub.add_parser("slope", help="slope maps (deg, native cell size) for bathymetry datasets")
    sl.add_argument("datasets", nargs="*", help="default: every bathymetry dataset")
    sl.add_argument("--out", default=str(REPO / "Native_Maps"))
    sl.add_argument("--no-png", action="store_true")
    sl.add_argument("--baseline", type=float, default=None, help="slope baseline in m (default: 2 grid cells)")
    for p_ in (a, o, sl):
        p_.add_argument("--cmap", default=None, help="colour map name (matplotlib, cmo.*, cmc.*; _r reverses)")
    c.add_argument("--slope", action="store_true", help="also write slope products (bathymetry only)")
    c.add_argument("--slope-baseline", type=float, default=None, help="slope baseline in m (default: 2 grid cells)")
    c.add_argument("--cmap", default=None, help="colour map name (matplotlib, cmo.*, cmc.*; _r reverses)")
    rd = sub.add_parser("readme", help="write README.md files for a Native_Maps folder from its index files")
    rd.add_argument("--out", default=str(REPO / "Native_Maps"))
    ls = sub.add_parser("list", help="list datasets and their source files")
    ls.add_argument("--json", action="store_true", help="machine-readable (used by the viewer server)")
    args = ap.parse_args()

    if args.cmd == "list":
        if args.json:
            print(json.dumps(dict(datasets=[dict(id=k, label=v["label"], epsg=v["epsg"], kind=v["kind"],
                                                 files=len(source_files(k))) for k, v in DATASETS.items()],
                                  viewer_ids=VIEWER_IDS)))
            return
        for k, v in DATASETS.items():
            fs = source_files(k)
            print(f"{k:30s} EPSG:{v['epsg']:<6d} {v['kind']:12s} {len(fs):3d} file(s)  {v['label']}")
        return
    if args.cmd == "clip":
        g = clip(args.dataset, (args.west, args.east, args.south, args.north))
        if g is None:
            sys.exit(f"{args.dataset}: no data in that box")
        spec = DATASETS[args.dataset]
        info = render_grid(g, spec["kind"], args.name or f"{args.dataset}_clip", args.out, spec["label"],
                           spec["units"], nav=args.nav, wgs84=args.wgs84, cmap=args.cmap)
        if args.slope and spec["kind"] == "bathy":
            render_slope(g, args.name or f"{args.dataset}_clip", args.out, spec["label"], nav=args.nav,
                         wgs84=args.wgs84, baseline_m=args.slope_baseline)
        print(json.dumps(info, indent=1))
        return
    if args.cmd == "readme":
        print(write_readmes(args.out))
        return
    if args.cmd == "slope":
        for ds in args.datasets or [k for k, v in DATASETS.items() if v["kind"] == "bathy"]:
            print(f"{ds} slope ...", flush=True)
            render_dataset(ds, args.out, png=not args.no_png, only_slope=True, baseline_m=args.baseline, cmap=args.cmap)
        return
    ids = sorted(DATASETS) if args.cmd == "all" else [args.dataset]
    for ds in ids:
        if args.cmd == "all" and (ds in args.skip or (args.skip_done and (Path(args.out) / ds / "index.json").exists())):
            continue
        print(f"{ds} ...", flush=True)
        render_dataset(ds, args.out, png=not args.no_png, cmap=args.cmap)
    # top-level index = every dataset rendered so far (not just this run)
    summary = []
    for f in sorted(Path(args.out).glob("*/index.json")) + sorted(Path(args.out).glob("*/slope_index.json")):
        summary += json.load(open(f))
    with open(Path(args.out) / "index.json", "w") as f:
        json.dump(summary, f, indent=1)


if __name__ == "__main__":
    main()
