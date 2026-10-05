#!/usr/bin/env python3
"""
mosaic_geomapapp_grids.py

Merge many per-survey-line GMT/COARDS NetCDF grids (the "*.geo.grd" files
in GeoMapApp_ready/Bathymetry or GeoMapApp_ready/Backscatter) into ONE grid
covering the whole survey, so you can import a single file into GeoMapApp
instead of one-at-a-time.

Uses only numpy + the bundled `netcdf_lite.py` (a vendored copy of SciPy's
pure-Python NetCDF3 reader/writer) -- no internet, no pip install, no GMT
installation needed.

Usage:
    # Bathymetry (defaults already set for this dataset's naming):
    python3 mosaic_geomapapp_grids.py \\
        --input-dir GeoMapApp_ready/Bathymetry \\
        --output GeoMapApp_ready/MV1007_bathymetry_mosaic.grd \\
        --suffix bty.50m.geo.grd bty.75m.geo.grd

    # Backscatter:
    python3 mosaic_geomapapp_grids.py \\
        --input-dir GeoMapApp_ready/Backscatter \\
        --output GeoMapApp_ready/MV1007_backscatter_mosaic.grd \\
        --suffix ss.gmap.geo.grd

    # Check size/coverage first without writing anything:
    python3 mosaic_geomapapp_grids.py --input-dir ... --output ... --suffix ... --dry-run

Each immediate subfolder of --input-dir is treated as one survey line; the
first suffix in --suffix that exists in that folder is used (so you can
list a preferred suffix first and fall back to a secondary one for lines
that don't have it, e.g. 50m falling back to 75m).

Where two lines overlap, whichever is processed later wins for the
overlapping cells (only where its data is non-NaN) -- lines are processed
in alphabetical folder order.
"""
import argparse
import glob
import os
import sys

import numpy as np
from netcdf_lite import netcdf_file


def find_line_file(line_dir, suffixes):
    for suf in suffixes:
        matches = sorted(glob.glob(os.path.join(line_dir, f"*{suf}")))
        if matches:
            return matches[0]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input-dir", required=True, help="Folder containing one subfolder per survey line")
    ap.add_argument("--output", required=True, help="Output mosaic .grd path")
    ap.add_argument("--suffix", nargs="+", required=True,
                     help="Priority list of filename suffixes to look for in each line folder, "
                          "e.g. --suffix bty.50m.geo.grd bty.75m.geo.grd")
    ap.add_argument("--dry-run", action="store_true", help="Only report coverage/size, write nothing")
    args = ap.parse_args()

    line_dirs = sorted(d for d in glob.glob(os.path.join(args.input_dir, "*")) if os.path.isdir(d))
    if not line_dirs:
        sys.exit(f"No subfolders found under {args.input_dir}")

    files = []
    for d in line_dirs:
        f = find_line_file(d, args.suffix)
        if f:
            files.append(f)
        else:
            print(f"WARNING: no file matching {args.suffix} in {d}, skipping this line", file=sys.stderr)

    print(f"Found {len(files)} files to mosaic out of {len(line_dirs)} line folders")
    if not files:
        sys.exit("Nothing to do.")

    # Pass 1: headers only, to work out the overall extent and a common spacing
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    dx = dy = None
    file_info = []
    for f in files:
        nc = netcdf_file(f, "r", mmap=False)
        x = nc.variables["x"][:]
        y = nc.variables["y"][:]
        fxmin, fxmax = float(x[0]), float(x[-1])
        fymin, fymax = float(y[0]), float(y[-1])
        fdx = (fxmax - fxmin) / (len(x) - 1)
        fdy = (fymax - fymin) / (len(y) - 1)
        if dx is None:
            dx, dy = fdx, fdy
        elif abs(fdx - dx) / dx > 0.01 or abs(fdy - dy) / dy > 0.01:
            print(f"WARNING: {f} spacing ({fdx:.6f},{fdy:.6f}) differs >1% from first file's "
                  f"({dx:.6f},{dy:.6f}) -- it will still be placed using the master spacing, "
                  f"which may misalign it slightly.", file=sys.stderr)
        xmin, xmax = min(xmin, fxmin), max(xmax, fxmax)
        ymin, ymax = min(ymin, fymin), max(ymax, fymax)
        file_info.append((f, fxmin, fymin, fxmax, fymax, len(x), len(y), fdx, fdy))
        nc.close()

    nx = int(round((xmax - xmin) / dx)) + 1
    ny = int(round((ymax - ymin) / dy)) + 1
    est_mb = nx * ny * 4 / 1e6
    print(f"Master grid: {nx} x {ny} = {nx*ny:,} cells (~{est_mb:.0f} MB as float32)")
    print(f"Extent: lon {xmin:.5f} to {xmax:.5f}, lat {ymin:.5f} to {ymax:.5f}, spacing {dx:.6f} x {dy:.6f} deg")

    if args.dry_run:
        print("Dry run only -- nothing written.")
        return

    master = np.full((ny, nx), np.nan, dtype=np.float32)

    for f, fxmin, fymin, fxmax, fymax, fnx, fny, fdx, fdy in file_info:
        nc = netcdf_file(f, "r", mmap=False)
        z = np.array(nc.variables["z"][:], dtype=np.float32)
        nc.close()

        # Place by COORDINATES, never by index: every master cell inside this file's footprint takes
        # the file's nearest cell, located from the file's OWN origin and spacing. (The previous
        # version pasted tiles at the master spacing with a rounded origin; with tile spacings
        # differing by up to 0.26 % that drifted tiles by up to several cells -- the MV1007 mosaic
        # was displaced 30-95 m east of its own source tiles.) Error is now <= half a source cell.
        j0 = max(0, int(np.ceil((fxmin - fdx / 2 - xmin) / dx)))
        j1 = min(nx - 1, int(np.floor((fxmax + fdx / 2 - xmin) / dx)))
        i0 = max(0, int(np.ceil((fymin - fdy / 2 - ymin) / dy)))
        i1 = min(ny - 1, int(np.floor((fymax + fdy / 2 - ymin) / dy)))
        col = np.round((xmin + np.arange(j0, j1 + 1) * dx - fxmin) / fdx).astype(int)
        row = np.round((ymin + np.arange(i0, i1 + 1) * dy - fymin) / fdy).astype(int)
        okc = (col >= 0) & (col < fnx)
        okr = (row >= 0) & (row < fny)
        vals = z[np.ix_(row[okr], col[okc])]
        rows_m, cols_m = np.arange(i0, i1 + 1)[okr], np.arange(j0, j1 + 1)[okc]
        sub = master[np.ix_(rows_m, cols_m)]
        mask = ~np.isnan(vals)
        sub[mask] = vals[mask]
        master[np.ix_(rows_m, cols_m)] = sub
        ix0, iy0 = j0, i0
        print(f"  placed {os.path.basename(f)} at col {ix0}, row {iy0} ({fnx}x{fny})")

    zmin = float(np.nanmin(master))
    zmax = float(np.nanmax(master))

    xvals = xmin + np.arange(nx) * dx
    yvals = ymin + np.arange(ny) * dy

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    out = netcdf_file(args.output, "w")
    out.Conventions = "COARDS/CF-1.0"
    out.title = "Mosaic built by mosaic_geomapapp_grids.py"
    out.history = f"Merged {len(files)} line grids from {args.input_dir}"
    out.createDimension("x", nx)
    out.createDimension("y", ny)
    xv = out.createVariable("x", "d", ("x",))
    xv[:] = xvals
    xv.long_name = "Longitude"
    xv.actual_range = np.array([xvals[0], xvals[-1]], dtype=np.float64)
    yv = out.createVariable("y", "d", ("y",))
    yv[:] = yvals
    yv.long_name = "Latitude"
    yv.actual_range = np.array([yvals[0], yvals[-1]], dtype=np.float64)
    zv = out.createVariable("z", "f", ("y", "x"))
    zv[:] = master
    zv.actual_range = np.array([zmin, zmax], dtype=np.float64)
    zv.long_name = "z"
    zv._FillValue = np.float32(np.nan)
    out.close()

    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
