#!/usr/bin/env python3
"""
gmt_grd_to_geotiff.py

Convert an old-style GMT "surface" NetCDF grid (the format written by
write_gmt in MATLAB: variables x_range/y_range/z_range/spacing/dimension/z,
no CF conventions, no embedded CRS) into a standard, unambiguous GeoTIFF
with an explicit EPSG code baked in.

Why: QGIS reported "Invalid Data Source" for the raw .grd files. This old
GMT grid variant isn't reliably picked up by every GDAL build (the generic
netCDF driver doesn't recognize its non-CF layout, and the dedicated GMT
driver isn't in every GDAL build/order of driver registration). Converting
to GeoTIFF sidesteps the driver-detection problem entirely.

Usage:
    python3 gmt_grd_to_geotiff.py input.grd output.tif --epsg 32615
"""
import argparse
import numpy as np
import tifffile
from scipy.io import netcdf_file


def convert(in_path, out_path, epsg):
    nc = netcdf_file(in_path, "r", mmap=False)
    x_range = nc.variables["x_range"][:].copy()
    y_range = nc.variables["y_range"][:].copy()
    spacing = nc.variables["spacing"][:].copy()
    dimension = nc.variables["dimension"][:].copy()
    zvar = nc.variables["z"]
    node_offset = int(zvar._attributes.get("node_offset", 0))
    scale_factor = float(zvar._attributes.get("scale_factor", 1.0))
    add_offset = float(zvar._attributes.get("add_offset", 0.0))
    zdata = zvar[:].copy().astype(np.float32)
    nc.close()

    nx, ny = int(dimension[0]), int(dimension[1])
    if zdata.size != nx * ny:
        raise ValueError(f"{in_path}: z size {zdata.size} != nx*ny {nx*ny}")

    if scale_factor != 1.0 or add_offset != 0.0:
        zdata = zdata * scale_factor + add_offset

    grid = zdata.reshape(ny, nx)  # GMT stores south-to-north row order
    grid = np.flipud(grid)        # flip to north-up for a standard raster

    px_w, px_h = float(spacing[0]), float(spacing[1])
    if node_offset == 0:
        # gridline registration: x_range/y_range are node centers
        origin_x = float(x_range[0]) - px_w / 2.0
        origin_y = float(y_range[1]) + px_h / 2.0
    else:
        # pixel registration: x_range/y_range are already cell edges
        origin_x = float(x_range[0])
        origin_y = float(y_range[1])

    # GeoTIFF GeoKeys for a plain projected CRS by EPSG code
    geo_key_directory = (
        1, 1, 0, 4,       # version, revision, minor revision, num keys
        1024, 0, 1, 1,    # GTModelTypeGeoKey = 1 (Projected)
        1025, 0, 1, 1,    # GTRasterTypeGeoKey = 1 (PixelIsArea)
        3072, 0, 1, epsg, # ProjectedCSTypeGeoKey = EPSG code
        1026, 0, 1, 0,    # GTCitationGeoKey placeholder (unused, 0 count would be invalid so keep minimal)
    )
    # Simplify: GDAL/QGIS only strictly need ModelType, RasterType, ProjectedCSType
    geo_key_directory = (
        1, 1, 0, 3,
        1024, 0, 1, 1,
        1025, 0, 1, 1,
        3072, 0, 1, epsg,
    )

    extratags = [
        (33550, "d", 3, (px_w, px_h, 0.0), False),           # ModelPixelScaleTag
        (33922, "d", 6, (0.0, 0.0, 0.0, origin_x, origin_y, 0.0), False),  # ModelTiepointTag
        (34735, "H", len(geo_key_directory), geo_key_directory, False),   # GeoKeyDirectoryTag
        (42113, "s", 3, "nan", False),                        # GDAL_NODATA
    ]

    tifffile.imwrite(
        out_path,
        grid,
        dtype=np.float32,
        photometric="minisblack",
        extratags=extratags,
    )
    return {
        "shape": grid.shape,
        "nx": nx, "ny": ny,
        "origin": (origin_x, origin_y),
        "pixel_size": (px_w, px_h),
        "nan_count": int(np.isnan(grid).sum()),
        "min": float(np.nanmin(grid)),
        "max": float(np.nanmax(grid)),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--epsg", type=int, required=True)
    args = ap.parse_args()
    info = convert(args.input, args.output, args.epsg)
    print(info)
