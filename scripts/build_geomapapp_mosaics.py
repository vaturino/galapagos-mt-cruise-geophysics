#!/usr/bin/env python3
"""
build_geomapapp_mosaics.py

Builds single-file, GeoMapApp-ready mosaics for every dataset folder under
GMRT_regional/, so each folder can be imported as ONE grid instead of many
tiles. Also makes sure every individual raw grid in those folders has a
correctly-georeferenced GeoMapApp twin (reusing fix_mgds_grid.py's readers,
NOT rasterio/GDAL -- GDAL's netCDF driver silently fails to auto-detect the
x/y coordinate variables in these files (they lack CF `units` attributes),
which looked like a real georeferencing bug during investigation but isn't:
fix_mgds_grid.py's own reader (var-name based, no CF-units dependency) reads
correct real-world coordinates from every one of them).

Output goes to GeoMapApp_ready/GMRT_regional/<same subfolder layout>/, not
GMRT_regional/ itself, so the originals are untouched.

Geographic (.grd) mosaics are written as classic NetCDF3 COARDS x/y/z grids
(via netcdf_lite, matching every other working .grd in this project and
mosaic_geomapapp_grids.py's own output format).
"""
import os
import sys
import glob
import json

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fix_mgds_grid as fx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "GMRT_regional")
DST = os.path.join(ROOT, "GeoMapApp_ready", "GMRT_regional")

LOG = open(os.path.join(ROOT, "scripts", "build_geomapapp_mosaics.log"), "a")


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg)
    LOG.write(msg + "\n")
    LOG.flush()


def normalize_lon(x):
    x = np.array(x, dtype=np.float64)
    if x.max() > 180.0:
        x = x - 360.0
    return x


def read_geo_grid(path):
    """Read any raw .grd via fix_mgds_grid, normalize 0-360 longitude if needed."""
    x, y, z = fx.load_any(path)
    x = normalize_lon(x)
    return x, y, z


def write_geomapapp_twin(path, out_path):
    x, y, z = read_geo_grid(path)
    info = fx.write_modern_grd(x, y, z, out_path)
    return info


def mosaic_geo_grids(files, out_path, max_cells=180_000_000):
    """Merge a list of raw geographic .grd files into one COARDS .grd.
    Master spacing = the COARSEST spacing among the inputs (keeps the
    mosaic a manageable single file -- full native resolution for any one
    tile is still available in its own per-tile twin). Files are placed
    coarsest-first, finest-last, so finer tiles win overlaps."""
    metas = []
    for f in files:
        x, y, z = read_geo_grid(f)
        # mean spacing across the whole axis -- more robust than x[1]-x[0],
        # which can be a noisy/rounded outlier on some of these grids
        dx = abs(float(x[-1] - x[0]) / (len(x) - 1)) if len(x) > 1 else 1.0
        dy = abs(float(y[-1] - y[0]) / (len(y) - 1)) if len(y) > 1 else 1.0
        metas.append({"file": f, "x": x, "y": y, "z": z, "dx": dx, "dy": dy})

    dx = max(m["dx"] for m in metas)
    dy = max(m["dy"] for m in metas)
    xmin = min(float(m["x"].min()) for m in metas)
    xmax = max(float(m["x"].max()) for m in metas)
    ymin = min(float(m["y"].min()) for m in metas)
    ymax = max(float(m["y"].max()) for m in metas)

    nx = int(round((xmax - xmin) / dx)) + 1
    ny = int(round((ymax - ymin) / dy)) + 1
    while nx * ny > max_cells:
        dx *= 2
        dy *= 2
        nx = int(round((xmax - xmin) / dx)) + 1
        ny = int(round((ymax - ymin) / dy)) + 1
    log(f"  master grid {nx} x {ny} = {nx*ny:,} cells, spacing {dx:.6f} x {dy:.6f} deg "
        f"(~{nx*ny*4/1e6:.0f} MB)")

    master = np.full((ny, nx), np.nan, dtype=np.float32)

    # coarser files first, finer files last (win overlaps)
    metas.sort(key=lambda m: -(m["dx"] * m["dy"]))

    for m in metas:
        x, y, z = m["x"], m["y"], m["z"]
        fdx = abs(float(x[1] - x[0])) if len(x) > 1 else dx
        fdy = abs(float(y[1] - y[0])) if len(y) > 1 else dy

        c0 = int(np.clip(round((float(x[0]) - xmin) / dx), 0, nx - 1))
        c1 = int(np.clip(round((float(x[-1]) - xmin) / dx), 0, nx - 1))
        r0 = int(np.clip(round((float(y[0]) - ymin) / dy), 0, ny - 1))
        r1 = int(np.clip(round((float(y[-1]) - ymin) / dy), 0, ny - 1))
        if c1 <= c0 or r1 <= r0:
            log(f"    skipping {os.path.basename(m['file'])}: degenerate overlap with master grid")
            continue
        ncols, nrows = c1 - c0 + 1, r1 - r0 + 1

        master_x_slice = xmin + (c0 + np.arange(ncols)) * dx
        master_y_slice = ymin + (r0 + np.arange(nrows)) * dy
        src_col = np.clip(np.round((master_x_slice - float(x[0])) / fdx).astype(np.int64), 0, len(x) - 1)
        src_row = np.clip(np.round((master_y_slice - float(y[0])) / fdy).astype(np.int64), 0, len(y) - 1)

        sub_z = z[np.ix_(src_row, src_col)]
        target = master[r0:r0 + nrows, c0:c0 + ncols]
        mask = np.isfinite(sub_z)
        target[mask] = sub_z[mask]
        log(f"    placed {os.path.basename(m['file'])} ({z.shape[1]}x{z.shape[0]} native -> "
            f"{ncols}x{nrows} at master spacing, {int(mask.sum()):,} cells)")
        del z, sub_z

    xvals = xmin + np.arange(nx) * dx
    yvals = ymin + np.arange(ny) * dy
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    info = fx.write_modern_grd(xvals, yvals, master, out_path)
    return info


def main():
    os.makedirs(DST, exist_ok=True)
    manifest = {}

    # ---- 1. single-file / already-good folders: just write a clean twin ----
    singles = [
        "Backscatter_MGDS/GSC_97-86W_Compilation/GSC_97-86W_100m_comp.grd",
        "Backscatter_MGDS/MGL1106_CostaRica_CRISP/Sidescan_5m.grd",
        "GMRT_Basemap/GMRT_corridor_basemap_clean.grd",
        "Geophysics_MGDS/Barckhausen_CentralAmerica_Magnetics/central_america_mag.grd",
        "Geophysics_MGDS/Bassett_CentralAmerica_ResidualGravity/CentAm_Residual_gravity.grd",
        "Geophysics_MGDS/SR1806_CocosNazca_Gravity/105W95W1S5N_crust_1k.grd",
        "Geophysics_MGDS/SR1806_CocosNazca_Gravity/105W95W1S5N_mba.grd",
        "Geophysics_MGDS/SR1806_CocosNazca_Gravity/105W95W1S5N_mba_global_topo_global_FAA.grd",
        "Geophysics_MGDS/SR1806_CocosNazca_Gravity/105W95W1S5N_mba_ship_topo_global_FAA.grd",
        "Geophysics_MGDS/SR1806_CocosNazca_Gravity/105W95W1S5N_rmba_1k.grd",
        "Geophysics_MGDS/SR1806_CocosNazca_Gravity/105W95W1S5N_thermal_1k.grd",
    ]
    for rel in singles:
        src = os.path.join(SRC, rel)
        out = os.path.join(DST, rel)
        if not os.path.exists(src):
            log("MISSING", src)
            continue
        log("single:", rel)
        try:
            info = write_geomapapp_twin(src, out)
            manifest[rel] = {"type": "single", "out": os.path.relpath(out, ROOT), **{k: v for k, v in info.items() if k != "z"}}
            log("  ->", out, info["nx"], "x", info["ny"], "z", info["zmin"], info["zmax"])
        except Exception as e:
            log("  ERROR", repr(e))

    LOG.flush()

    # ---- 2. real multi-tile mosaics (geographic) ----
    geo_mosaics = {
        "TN188_bathymetry_mosaic": sorted(glob.glob(os.path.join(SRC, "Backscatter_MGDS/TN188_GSC_Bathymetry_8m/TN188_DSL120A_8mbat.*.grd"))),
        "AT50-09BC_GalapagosPlatform_bathymetry_mosaic": sorted(f for f in glob.glob(os.path.join(SRC, "Backscatter_MGDS/AT50-09BC_GalapagosPlatform_Bathymetry/*.grd")) if "_geomapapp" not in f),
    }
    for name, files in geo_mosaics.items():
        files = [f for f in files if "_geomapapp" not in f]
        if not files:
            log("MOSAIC", name, "no files found, skipping")
            continue
        log(f"mosaic: {name} ({len(files)} tiles)")
        out = os.path.join(DST, "_mosaics", name + ".grd")
        try:
            info = mosaic_geo_grids(files, out)
            manifest[name] = {"type": "mosaic", "n_tiles": len(files), "out": os.path.relpath(out, ROOT), **{k: v for k, v in info.items() if k != "z"}}
            log("  ->", out, info["nx"], "x", info["ny"], "z", info["zmin"], info["zmax"])
        except Exception as e:
            log("  ERROR", repr(e))
        LOG.flush()

    # ---- 3. DRFT04RR_GSC_Bathymetry: use the best existing compilation as the mosaic ----
    log("mosaic: DRFT04RR_GSC_Bathymetry (using existing 10m compilation)")
    try:
        src = os.path.join(SRC, "Backscatter_MGDS/DRFT04RR_GSC_Bathymetry/86w_all_bathy_10m.grd")
        out = os.path.join(DST, "_mosaics", "DRFT04RR_GSC_bathymetry_compilation_10m.grd")
        info = write_geomapapp_twin(src, out)
        manifest["DRFT04RR_GSC_Bathymetry"] = {"type": "existing_compilation", "out": os.path.relpath(out, ROOT), **{k: v for k, v in info.items() if k != "z"}}
        log("  ->", out, info["nx"], "x", info["ny"], "z", info["zmin"], info["zmax"])
    except Exception as e:
        log("  ERROR", repr(e))
    LOG.flush()

    with open(os.path.join(ROOT, "scripts", "build_geomapapp_mosaics_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)

    log("DONE PHASE 1")


if __name__ == "__main__":
    main()
