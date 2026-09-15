#!/usr/bin/env python3
"""Quick header/shape inspector for GMRT COARDS grids, using mmap so it
doesn't pull the full altitude array into RAM just to report dimensions."""
import sys
from netcdf_lite import netcdf_file

for path in sys.argv[1:]:
    nc = netcdf_file(path, "r", mmap=True)
    lon = nc.variables["lon"]
    lat = nc.variables["lat"]
    alt = nc.variables["altitude"]
    lon_vals = lon[:]
    lat_vals = lat[:]
    nx, ny = len(lon_vals), len(lat_vals)
    dx = (float(lon_vals[-1]) - float(lon_vals[0])) / (nx - 1)
    dy = (float(lat_vals[-1]) - float(lat_vals[0])) / (ny - 1)
    print(f"{path}")
    print(f"  shape (ny,nx) = {alt.shape}, nx={nx}, ny={ny}")
    print(f"  lon range {lon_vals[0]:.5f} .. {lon_vals[-1]:.5f}, dx={dx:.6f} deg (~{dx*111000:.1f} m)")
    print(f"  lat range {lat_vals[0]:.5f} .. {lat_vals[-1]:.5f}, dy={dy:.6f} deg (~{dy*111000:.1f} m)")
    print(f"  approx cells = {nx*ny:,}  (~{nx*ny*4/1e6:.1f} MB as float32)")
    nc.close()
