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

        needs_resample = abs(fdx - dx) / dx > 0.01 or abs(fdy - dy) / dy > 0.01
        if needs_resample:
            # nearest-neighbour resample this file's own footprint onto the master spacing
            out_nx = int(round((fxmax - fxmin) / dx)) + 1
            out_ny = int(round((fymax - fymin) / dy)) + 1
            col_idx = np.clip(np.round(np.arange(out_nx) * dx / fdx).astype(int), 0, fnx - 1)
            row_idx = np.clip(np.round(np.arange(out_ny) * dy / fdy).astype(int), 0, fny - 1)
            z = z[np.ix_(row_idx, col_idx)]
            fnx, fny = out_nx, out_ny
            print(f"  (resampled {os.path.basename(f)} from {fdx:.6f}/{fdy:.6f} deg "
                  f"to master spacing {dx:.6f}/{dy:.6f} deg)")

        ix0 = int(round((fxmin - xmin) / dx))
        iy0 = int(round((fymin - ymin) / dy))
        sub = master[iy0:iy0 + fny, ix0:ix0 + fnx]
        mask = ~np.isnan(z)
        sub[mask] = z[mask]
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
