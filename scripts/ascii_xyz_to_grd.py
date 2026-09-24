#!/usr/bin/env python3
"""
Convert a plain ASCII lon/lat/value text file (whitespace or comma
delimited, 3 columns, optionally gzipped) into either:

  - a NetCDF .grd (x/y/z, readable by build_cesium_mesh.py /
    build_geophysics_drape.py unchanged) if the points fall on a regular
    grid, or
  - a plain CSV with the original columns if they don't -- e.g. gravimeter/
    magnetometer readings logged along a ship track are a 1D sequence of
    points, not a grid, and trying to force them onto one would be wrong.
    (Viewer3D's existing CSV track-point upload takes name/lat/lon/status
    columns for a categorical status, not a continuous value like mGal or
    nT -- this script does NOT try to shoehorn a numeric value into that
    field. It just gives you a clean CSV; adding a real continuous-value
    track overlay to the viewer is a separate, not-yet-done feature.)

Usage:
  python3 ascii_xyz_to_grd.py FLAMINGO_FAA_xyg.txt.gz --out flamingo_faa.csv
  python3 ascii_xyz_to_grd.py some_gridded_product.xyz --out some_grid.grd
"""
import argparse
import gzip
import sys
from pathlib import Path

import numpy as np


def _open_text(path):
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, "rt")
    return open(path, "r")


def load_xyz(path):
    xs, ys, vs = [], [], []
    with _open_text(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            if len(parts) < 3:
                continue
            try:
                x, y, v = float(parts[0]), float(parts[1]), float(parts[2])
            except ValueError:
                continue
            xs.append(x)
            ys.append(y)
            vs.append(v)
    return np.array(xs), np.array(ys), np.array(vs)


def is_regular_grid(x, y, tol=1e-6):
    """Check whether (x, y) pairs form a regular grid: every unique x paired
    with every unique y, evenly spaced in both axes."""
    ux = np.unique(np.round(x, 8))
    uy = np.unique(np.round(y, 8))
    if len(ux) < 2 or len(uy) < 2:
        return False, None, None
    if len(ux) * len(uy) != len(x):
        return False, None, None
    dx = np.diff(ux)
    dy = np.diff(uy)
    if not (np.allclose(dx, dx[0], atol=tol) and np.allclose(dy, dy[0], atol=tol)):
        return False, None, None
    return True, ux, uy


def write_grd(path, x, y, z):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from netcdf_lite import netcdf_file

    out = netcdf_file(path, "w")
    out.createDimension("y", len(y))
    out.createDimension("x", len(x))
    vx = out.createVariable("x", "f8", ("x",))
    vx[:] = x
    vy = out.createVariable("y", "f8", ("y",))
    vy[:] = y
    vz = out.createVariable("z", "f8", ("y", "x"))
    vz[:] = z
    out.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="ASCII lon/lat/value file, .txt or .txt.gz")
    ap.add_argument("--out", required=True, help="output path: .grd for a regular grid, .csv otherwise")
    args = ap.parse_args()

    x, y, v = load_xyz(args.input)
    print(f"read {len(x):,} points, x range [{x.min():.4f}, {x.max():.4f}], "
          f"y range [{y.min():.4f}, {y.max():.4f}], value range [{v.min():.4f}, {v.max():.4f}]")

    regular, ux, uy = is_regular_grid(x, y)
    if regular:
        print(f"regular grid detected: {len(ux)} x {len(uy)}")
        # pivot scattered (x, y, v) triplets onto the (uy, ux) grid
        xi = np.searchsorted(ux, np.round(x, 8))
        yi = np.searchsorted(uy, np.round(y, 8))
        z = np.full((len(uy), len(ux)), np.nan)
        z[yi, xi] = v
        out_path = args.out if args.out.endswith(".grd") else args.out + ".grd"
        write_grd(out_path, ux, uy, z)
        print(f"wrote {out_path} (x/y/z NetCDF grid -- read_grd() picks this up directly)")
    else:
        print("NOT a regular grid -- this is scattered/track-line data, not something "
              "a mesh grid can represent. Writing a plain CSV instead.")
        out_path = args.out if args.out.endswith(".csv") else args.out + ".csv"
        with open(out_path, "w") as f:
            f.write("lon,lat,value\n")
            for xi, yi, vi in zip(x, y, v):
                f.write(f"{xi},{yi},{vi}\n")
        print(f"wrote {out_path} ({len(x):,} rows, lon/lat/value columns)")


if __name__ == "__main__":
    main()
