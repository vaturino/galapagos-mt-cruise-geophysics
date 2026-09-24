"""
Crop the DOA-ETP regional MBES compilation (EPSG:3395 World Mercator, 100 m,
415 MB) to the Galapagos / Panama-Galapagos-Costa Rica corridor and
reproject to EPSG:4326 (lon/lat), so it matches every other grid this
project's viewer reads.

Streams the reprojection in row-blocks via a WarpedVRT rather than
materialising the (~1.4 GB) full cropped array and a same-size destination
array at once -- a first attempt doing that OOM-killed at ~3.8 GB. Peak
memory here is bounded by one row-block, not the whole raster.

The requested crop bounds are clamped to the VRT's own actual bounds before
computing the pixel window -- an earlier version didn't do this and the
window silently extended ~10-15 columns past the VRT's valid extent on the
west/east edges (LON_MIN was ~0.008 deg past the source's real coverage),
producing a window wider than the source raster itself and contaminating
those edge columns with whatever GDAL fills for an out-of-range read. Caught
by comparing a freshly-read chunk against the written file and finding a
width mismatch, not by any error -- GDAL didn't raise on the bad window.
"""
import numpy as np
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
from rasterio.enums import Resampling

SRC = "../DOA_ETP_MBES/MBES_bathymetric_compilation_V1_2025.tif"
DST = "../DOA_ETP_MBES/DOA_ETP_MBES_galapagos_corridor_4326_clean.tif"

# Same lat bounds as GMRT_basemap's corridor; lon left at ~the source's own
# native extent (source only spans ~-95.34 to -77.01 lon, already narrower
# than GMRT_basemap's -98.5/-73.5) -- i.e. crop to the intersection of the
# two corridors. Clamped to the VRT's actual bounds below regardless.
LON_MIN, LON_MAX = -95.35, -77.00
LAT_MIN, LAT_MAX = -4.5, 10.7
ROW_CHUNK = 1000


def main():
    with rasterio.open(SRC) as src:
        print("source crs", src.crs, "shape", src.shape, "res", src.res, flush=True)
        with WarpedVRT(src, crs="EPSG:4326", resampling=Resampling.bilinear) as vrt:
            print("vrt shape", vrt.shape, "res", vrt.res, "bounds", vrt.bounds, flush=True)

            lon_min = max(LON_MIN, vrt.bounds.left)
            lon_max = min(LON_MAX, vrt.bounds.right)
            lat_min = max(LAT_MIN, vrt.bounds.bottom)
            lat_max = min(LAT_MAX, vrt.bounds.top)
            print(f"clamped request to vrt bounds: lon [{lon_min}, {lon_max}] "
                  f"lat [{lat_min}, {lat_max}]", flush=True)

            win = vrt.window(lon_min, lat_min, lon_max, lat_max)
            win = win.round_lengths().round_offsets()
            # belt-and-suspenders: intersect with the VRT's own pixel extent so a
            # window can never be requested outside [0, vrt.width) x [0, vrt.height)
            col0 = max(0, int(win.col_off))
            row0 = max(0, int(win.row_off))
            col1 = min(vrt.width, int(win.col_off) + int(win.width))
            row1 = min(vrt.height, int(win.row_off) + int(win.height))
            assert col1 > col0 and row1 > row0, "crop window is empty after clamping"
            width, height = col1 - col0, row1 - row0
            win = Window(col0, row0, width, height)
            out_transform = vrt.window_transform(win)
            print("final pixel window", win, "-> shape", (height, width),
                  f"(vrt is {vrt.height} x {vrt.width}, so window must fit inside that)",
                  flush=True)
            assert col0 >= 0 and row0 >= 0 and col1 <= vrt.width and row1 <= vrt.height

            profile = {
                "driver": "GTiff",
                "height": height,
                "width": width,
                "count": 1,
                "dtype": "float32",
                "crs": "EPSG:4326",
                "transform": out_transform,
                "nodata": src.nodata,
                "compress": "deflate",
                "predictor": 3,
                "tiled": True,
                "blockxsize": 256,
                "blockysize": 256,
            }
            populated = 0
            vmin, vmax = np.inf, -np.inf
            with rasterio.open(DST, "w", **profile) as dst:
                for r0 in range(0, height, ROW_CHUNK):
                    r1 = min(height, r0 + ROW_CHUNK)
                    src_win = Window(col0, row0 + r0, width, r1 - r0)
                    block = vrt.read(1, window=src_win)
                    assert block.shape == (r1 - r0, width), (
                        f"read block shape {block.shape} != expected {(r1-r0, width)} "
                        f"-- window went out of VRT bounds"
                    )
                    dst.write(block, 1, window=Window(0, r0, width, r1 - r0))
                    finite = np.isfinite(block) & (block < 1e30)
                    if finite.any():
                        populated += int(finite.sum())
                        vmin = min(vmin, float(block[finite].min()))
                        vmax = max(vmax, float(block[finite].max()))
                    if r0 % 5000 == 0:
                        print(f"  row {r0}/{height}", flush=True)
            print("populated cells", populated, "/", height * width,
                  f"({100*populated/(height*width):.1f}%)", flush=True)
            print("value range", vmin, vmax, flush=True)
        print("wrote", DST, flush=True)


if __name__ == "__main__":
    main()
