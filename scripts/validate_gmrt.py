#!/usr/bin/env python3
import sys
import numpy as np
from netcdf_lite import netcdf_file

for path in sys.argv[1:]:
    nc = netcdf_file(path, "r", mmap=True)
    alt = nc.variables["altitude"]
    z = alt[:]
    zmin = float(np.nanmin(z))
    zmax = float(np.nanmax(z))
    nan_count = int(np.isnan(z).sum()) if np.issubdtype(z.dtype, np.floating) else 0
    ar = alt._attributes.get("actual_range")
    print(f"{path}")
    print(f"  computed min/max: {zmin:.2f} / {zmax:.2f}  nan_count={nan_count}/{z.size}")
    print(f"  header actual_range attr: {ar}")
    nc.close()
