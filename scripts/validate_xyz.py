#!/usr/bin/env python3
import sys
import numpy as np
from netcdf_lite import netcdf_file

for path in sys.argv[1:]:
    nc = netcdf_file(path, "r", mmap=True)
    print(path)
    print("  vars:", list(nc.variables.keys()))
    z = nc.variables["z"]
    print("  z dtype:", z.data.dtype, "shape:", z.shape)
    zz = z[:]
    zmin = float(np.nanmin(zz))
    zmax = float(np.nanmax(zz))
    print("  computed min/max:", zmin, zmax)
    print("  header actual_range:", z._attributes.get("actual_range"))
    nc.close()
