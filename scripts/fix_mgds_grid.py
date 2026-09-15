#!/usr/bin/env python3
"""
fix_mgds_grid.py

Makes an MGDS-downloaded grid GeoMapApp-ready, regardless of which of the
three formats MGDS happens to hand you:

  1. Old-style GMT netCDF ("classic surface" format: x_range/y_range/
     z_range/spacing/dimension/z, no CF conventions -- what MATLAB's
     write_gmt and some older GMT pipelines produce).
  2. Modern COARDS x/y/z netCDF, but with longitude in 0-360 convention
     instead of -180..180 (GeoMapApp displays this fine most of the time,
     but it's inconsistent with every other grid in this project, so we
     normalize it).
  3. ESRI ASCII grid (.asc: ncols/nrows/xllcenter-or-xllcorner/yllcenter-or-
     yllcorner/cellsize/nodata_value header, then the z values).

For each input, this script looks at the actual coordinate magnitudes to
decide whether the grid is already in geographic degrees or in a projected
CRS (e.g. UTM, which shows up as eastings/northings in the hundreds of
thousands to millions):

  - Geographic  -> write a modern COARDS/CF x/y/z .grd (longitude forced to
    -180..180), the same convention as the working MV1007/MGL1106 grids.
    GeoMapApp opens this directly, no further fixes needed.
  - Projected   -> write a GeoTIFF with an EPSG code baked into the file
    (same approach already used for the TN188 sidescan samples). Pass the
    EPSG explicitly with --epsg; the script does not guess a projected CRS.

Usage:
    python3 fix_mgds_grid.py input.grd output.grd        # geographic input
    python3 fix_mgds_grid.py input.grd output.tif --epsg 32615   # projected input
    python3 fix_mgds_grid.py input.asc output.tif --epsg 32615   # ESRI ASCII input
"""
import argparse
import sys

import numpy as np
from netcdf_lite import netcdf_file

GEOGRAPHIC_LIMIT = 360.5  # if both coordinate ranges fall within +/- this, treat as lon/lat degrees


def read_old_style_grd(path):
    nc = netcdf_file(path, "r", mmap=True)
    x_range = np.array(nc.variables["x_range"][:], dtype=np.float64)
    y_range = np.array(nc.variables["y_range"][:], dtype=np.float64)
    dimension = nc.variables["dimension"][:]
    nx, ny = int(dimension[0]), int(dimension[1])
    zvar = nc.variables["z"]
    node_offset = int(zvar._attributes.get("node_offset", 0))
    z = np.array(zvar[:], dtype=np.float32).reshape(ny, nx)
    fill = zvar._attributes.get("_FillValue")
    nc.close()

    if node_offset == 0:
        x = np.linspace(x_range[0], x_range[1], nx)
        y = np.linspace(y_range[0], y_range[1], ny)
    else:
        dx = (x_range[1] - x_range[0]) / nx
        dy = (y_range[1] - y_range[0]) / ny
        x = x_range[0] + dx * (np.arange(nx) + 0.5)
        y = y_range[0] + dy * (np.arange(ny) + 0.5)

    if fill is not None:
        z = np.where(z == np.float32(fill), np.nan, z)
    return x, y, z


def read_modern_grd(path):
    nc = netcdf_file(path, "r", mmap=True)
    x = np.array(nc.variables["x"][:], dtype=np.float64)
    y = np.array(nc.variables["y"][:], dtype=np.float64)
    z = np.array(nc.variables["z"][:], dtype=np.float32)
    nc.close()
    return x, y, z


def read_esri_ascii(path):
    header = {}
    with open(path, "r") as f:
        while True:
            pos = f.tell()
            line = f.readline()
            parts = line.split()
            if len(parts) == 2 and parts[0].lower() in (
                "ncols", "nrows", "xllcorner", "yllcorner", "xllcenter",
                "yllcenter", "cellsize", "nodata_value", "dx", "dy",
            ):
                try:
                    header[parts[0].lower()] = float(parts[1])
                except ValueError:
                    header[parts[0].lower()] = parts[1]
            else:
                f.seek(pos)
                break
        data = np.loadtxt(f, dtype=np.float32).reshape(
            int(header["nrows"]), int(header["ncols"])
        )

    ncols, nrows = int(header["ncols"]), int(header["nrows"])
    cellsize = header.get("cellsize", header.get("dx"))
    if "xllcenter" in header:
        x0 = header["xllcenter"]
    else:
        x0 = header["xllcorner"] + cellsize / 2.0
    if "yllcenter" in header:
        y0 = header["yllcenter"]
    else:
        y0 = header["yllcorner"] + cellsize / 2.0

    x = x0 + cellsize * np.arange(ncols)
    y = y0 + cellsize * np.arange(nrows)  # row 0 of `data` is the TOP row (northmost) by ESRI convention
    z = data
    nodata = header.get("nodata_value")
    if nodata is not None:
        z = np.where(z == np.float32(nodata), np.nan, z)
    # ESRI ASCII lists rows north-to-south; flip so y is ascending south-to-north
    z = np.flipud(z)
    return x, y, z


def load_any(path):
    if path.lower().endswith(".asc"):
        return read_esri_ascii(path)
    nc = netcdf_file(path, "r", mmap=True)
    varnames = set(nc.variables.keys())
    nc.close()
    if {"x_range", "y_range", "dimension"}.issubset(varnames):
        return read_old_style_grd(path)
    if {"x", "y", "z"}.issubset(varnames):
        return read_modern_grd(path)
    raise ValueError(f"{path}: unrecognized grid variables {sorted(varnames)}")


def write_modern_grd(x, y, z, out_path):
    zmin = float(np.nanmin(z))
    zmax = float(np.nanmax(z))
    out = netcdf_file(out_path, "w")
    out.Conventions = "COARDS/CF-1.0"
    out.title = "Topography Grid"
    out.description = "Normalized to GMT x/y/z convention by fix_mgds_grid.py"
    out.GMT_version = "4.5.2 [64-bit]"
    out.createDimension("x", len(x))
    out.createDimension("y", len(y))
    xv = out.createVariable("x", "d", ("x",))
    xv[:] = x
    xv.long_name = "Longitude"
    xv.actual_range = np.array([x[0], x[-1]], dtype=np.float64)
    yv = out.createVariable("y", "d", ("y",))
    yv[:] = y
    yv.long_name = "Latitude"
    yv.actual_range = np.array([y[0], y[-1]], dtype=np.float64)
    zv = out.createVariable("z", "f", ("y", "x"))
    zv[:] = z
    zv.long_name = "z"
    zv._FillValue = np.float32(np.nan)
    zv.actual_range = np.array([zmin, zmax], dtype=np.float64)
    out.close()
    return {"nx": len(x), "ny": len(y), "zmin": zmin, "zmax": zmax}


def write_geotiff(x, y, z, out_path, epsg):
    import tifffile

    px_w = float(x[1] - x[0])
    px_h = float(y[1] - y[0])
    # north-up: row 0 of the output raster must be the northmost row
    if py_ascending := (px_h > 0):
        z_out = np.flipud(z)
        origin_y = float(y[-1]) + px_h / 2.0
    else:
        z_out = z
        origin_y = float(y[0]) - px_h / 2.0
    origin_x = float(x[0]) - px_w / 2.0

    geo_key_directory = (
        1, 1, 0, 3,
        1024, 0, 1, 1,
        1025, 0, 1, 1,
        3072, 0, 1, epsg,
    )
    extratags = [
        (33550, "d", 3, (abs(px_w), abs(px_h), 0.0), False),
        (33922, "d", 6, (0.0, 0.0, 0.0, origin_x, origin_y, 0.0), False),
        (34735, "H", len(geo_key_directory), geo_key_directory, False),
        (42113, "s", 3, "nan", False),
    ]
    tifffile.imwrite(
        out_path, z_out.astype(np.float32), dtype=np.float32,
        photometric="minisblack", extratags=extratags,
    )
    return {
        "shape": z_out.shape, "origin": (origin_x, origin_y),
        "pixel_size": (px_w, px_h),
        "zmin": float(np.nanmin(z_out)), "zmax": float(np.nanmax(z_out)),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--epsg", type=int, default=None,
                     help="Required if the input turns out to be in a projected CRS (e.g. UTM)")
    args = ap.parse_args()

    x, y, z = load_any(args.input)

    is_geographic = (
        np.nanmax(np.abs(x)) <= GEOGRAPHIC_LIMIT and np.nanmax(np.abs(y)) <= 90.5
    )

    if is_geographic:
        x = np.where(x > 180, x - 360, x)
        if not np.all(np.diff(x) > 0):
            order = np.argsort(x)
            x = x[order]
            z = z[:, order]
        info = write_modern_grd(x, y, z, args.output)
        print(f"Wrote geographic grid {args.output}: {info}")
    else:
        if args.epsg is None:
            sys.exit(
                f"{args.input}: coordinates look projected (x range "
                f"{x.min():.1f}..{x.max():.1f}, y range {y.min():.1f}..{y.max():.1f}) "
                f"-- pass --epsg <code> (e.g. 32615 for UTM zone 15N/WGS84)"
            )
        info = write_geotiff(x, y, z, args.output, args.epsg)
        print(f"Wrote GeoTIFF {args.output} (EPSG:{args.epsg}): {info}")


if __name__ == "__main__":
    main()
