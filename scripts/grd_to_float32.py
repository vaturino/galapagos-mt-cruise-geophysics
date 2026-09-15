#!/usr/bin/env python3
"""
grd_to_float32.py

GMRT's GridServer COARDS grids store the altitude/z variable as float64
(double), which is heavier than needed for elevation data and appears to be
what makes GeoMapApp choke on larger tiles ("header min/max values are valid
numbers and not NaN" -- a generic GMT-grid-reader error that in practice also
fires on things GeoMapApp's Java heap can't handle, not just truly invalid
headers). The MV1007 grids that import fine use float32. This converts a
GMRT COARDS grid's altitude variable from float64 to float32 in place
(lon/lat stay float64 -- those are tiny), which roughly halves file size and
matches the known-working format.

Usage:
    python3 grd_to_float32.py input.grd output.grd
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
    z = np.array(alt[:], dtype=np.float32)  # this is the one big copy+cast

    global_attrs = dict(nc_in._attributes)
    lon_attrs = dict(lon._attributes)
    lat_attrs = dict(lat._attributes)
    alt_attrs = dict(alt._attributes)
    nc_in.close()

    zmin = float(np.nanmin(z))
    zmax = float(np.nanmax(z))

    out = netcdf_file(out_path, "w")
    for k, v in global_attrs.items():
        setattr(out, k, v)
    setattr(out, "history",
            (global_attrs.get("history", b"").decode("latin1", "ignore")
             if isinstance(global_attrs.get("history"), (bytes, bytearray))
             else str(global_attrs.get("history", ""))) +
            "\nConverted altitude float64->float32 with grd_to_float32.py")

    out.createDimension("lon", len(lon_vals))
    out.createDimension("lat", len(lat_vals))

    lonv = out.createVariable("lon", "d", ("lon",))
    lonv[:] = lon_vals
    for k, v in lon_attrs.items():
        setattr(lonv, k, v)
    lonv.actual_range = np.array([lon_vals[0], lon_vals[-1]], dtype=np.float64)

    latv = out.createVariable("lat", "d", ("lat",))
    latv[:] = lat_vals
    for k, v in lat_attrs.items():
        setattr(latv, k, v)
    latv.actual_range = np.array([lat_vals[0], lat_vals[-1]], dtype=np.float64)

    altv = out.createVariable("altitude", "f", ("lat", "lon"))
    altv[:] = z
    for k, v in alt_attrs.items():
        if k in ("_FillValue", "actual_range"):
            continue
        setattr(altv, k, v)
    altv._FillValue = np.float32(np.nan)
    altv.actual_range = np.array([zmin, zmax], dtype=np.float64)

    out.close()
    return {"nx": len(lon_vals), "ny": len(lat_vals), "zmin": zmin, "zmax": zmax}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: grd_to_float32.py input.grd output.grd")
    info = convert(sys.argv[1], sys.argv[2])
    print(info)
