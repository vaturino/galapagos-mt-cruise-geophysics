#!/usr/bin/env python3
"""
gmrt_to_xyz.py

GMRT's GridServer COARDS output names its variables lon/lat/altitude. GeoMapApp's
grid importer (GMT-based) apparently only recognizes the classic GMT netCDF grid
variable names x/y/z -- confirmed by comparing against the known-good MV1007
*.geo.grd files (MB-System mbgrid output), which use x/y/z and import fine, versus
GMRT's lon/lat/altitude, which GeoMapApp rejects with a generic
"header min/max values are valid numbers and not NaN" error regardless of dtype.

This renames lon->x, lat->y, altitude->z (keeping z as float32, x/y as float64,
matching the proven-working MV1007 format exactly) without changing any data
values.

Usage:
    python3 gmrt_to_xyz.py input.grd output.grd
"""
import sys
import numpy as np
from netcdf_lite import netcdf_file


def convert(in_path, out_path):
    nc_in = netcdf_file(in_path, "r", mmap=True)
    lon = nc_in.variables["lon"]
    lat = nc_in.variables["lat"]
    alt = nc_in.variables["altitude"]

    lon_vals = np.array(lon[:], dtype=np.float64)
    lat_vals = np.array(lat[:], dtype=np.float64)
    z = np.array(alt[:], dtype=np.float32)
    nc_in.close()

    zmin = float(np.nanmin(z))
    zmax = float(np.nanmax(z))

    out = netcdf_file(out_path, "w")
    out.Conventions = "COARDS/CF-1.0"
    out.title = "Topography Grid"
    out.description = "Converted from GMRT GridServer output (lon/lat/altitude) to GMT x/y/z convention by gmrt_to_xyz.py"
    out.GMT_version = "4.5.2 [64-bit]"

    out.createDimension("x", len(lon_vals))
    out.createDimension("y", len(lat_vals))

    xv = out.createVariable("x", "d", ("x",))
    xv[:] = lon_vals
    xv.long_name = "Longitude"
    xv.actual_range = np.array([lon_vals[0], lon_vals[-1]], dtype=np.float64)

    yv = out.createVariable("y", "d", ("y",))
    yv[:] = lat_vals
    yv.long_name = "Latitude"
    yv.actual_range = np.array([lat_vals[0], lat_vals[-1]], dtype=np.float64)

    zv = out.createVariable("z", "f", ("y", "x"))
    zv[:] = z
    zv.long_name = "Topography (m)"
    zv._FillValue = np.float32(np.nan)
    zv.actual_range = np.array([zmin, zmax], dtype=np.float64)

    out.close()
    return {"nx": len(lon_vals), "ny": len(lat_vals), "zmin": zmin, "zmax": zmax}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: gmrt_to_xyz.py input.grd output.grd")
    info = convert(sys.argv[1], sys.argv[2])
    print(info)
