"""
Build an independent land-elevation reference for GMRT_corridor_basemap.grd:
the Copernicus GLO-90 DEM (90 m, ESA/Airbus, public, no login), area-averaged
onto the GMRT grid's own nodes. fix_gmrt_spikes.py uses it to find and repair
GMRT's bad land cells; validate_gmrt_clean.py uses it to check the result.

Tiles are read straight from the public AWS Open Data bucket
(https://registry.opendata.aws/copernicus-dem/) over HTTPS via GDAL's
/vsicurl/ -- only the overview levels needed for ~245 m output are fetched,
so the whole corridor takes about a minute. A 1x1 deg tile that doesn't exist
in the bucket is all-ocean; those cells stay NaN. Copernicus stores open
water as 0 m.

Needs internet once. Output is a float32 GeoTIFF on exactly the GMRT node
grid (north-up; NaN = no Copernicus tile), so every later step is offline.

Usage (from scripts/):
    venv/bin/python3 fetch_copernicus_reference.py
    venv/bin/python3 fetch_copernicus_reference.py --grid <grd> --out <tif>
"""
import argparse
import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

from netcdf_lite import netcdf_file

GRID = "../GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap.grd"
OUT = "../GMRT_regional/GMRT_Basemap/copernicus_glo90_on_gmrt_grid.tif"
BUCKET = "https://copernicus-dem-90m.s3.amazonaws.com"


def tile_url(la, lo):
    ns = f"N{la:02d}" if la >= 0 else f"S{-la:02d}"
    ew = f"W{-lo:03d}" if lo < 0 else f"E{lo:03d}"
    name = f"Copernicus_DSM_COG_30_{ns}_00_{ew}_00_DEM"
    return f"/vsicurl/{BUCKET}/{name}/{name}.tif"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid", default=GRID, help="GMRT grid whose nodes define the output grid")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    nc = netcdf_file(args.grid, "r", mmap=False)
    lon = np.asarray(nc.variables["lon"][:]).copy()
    lat = np.asarray(nc.variables["lat"][:]).copy()
    nc.close()
    dlon, dlat = lon[1] - lon[0], lat[1] - lat[0]
    ref = np.full((lat.size, lon.size), np.nan, np.float32)  # rows south->north, like the grd

    def one_tile(tile):
        la, lo = tile
        j0, j1 = np.searchsorted(lat, la), np.searchsorted(lat, la + 1)
        i0, i1 = np.searchsorted(lon, lo), np.searchsorted(lon, lo + 1)
        if j1 <= j0 or i1 <= i0:
            return None
        env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif")
        try:
            with rasterio.Env(**env), rasterio.open(tile_url(la, lo)) as ds:
                dst = np.full((j1 - j0, i1 - i0), np.nan, np.float32)
                # destination cells centred on the GMRT nodes lon[i0:i1], lat[j0:j1]
                tr = from_origin(lon[i0] - dlon / 2, lat[j1 - 1] + dlat / 2, dlon, dlat)
                reproject(rasterio.band(ds, 1), dst, dst_transform=tr, dst_crs="EPSG:4326",
                          resampling=Resampling.average, dst_nodata=np.nan)
                return j0, j1, i0, i1, dst[::-1]  # north-up -> south->north rows
        except rasterio.errors.RasterioIOError:
            return None  # no such tile: all ocean

    tiles = [(la, lo) for la in range(math.floor(lat[0]), math.ceil(lat[-1]))
             for lo in range(math.floor(lon[0]), math.ceil(lon[-1]))]
    found = 0
    with ThreadPoolExecutor(16) as ex:
        for r in ex.map(one_tile, tiles):
            if r is not None:
                j0, j1, i0, i1, a = r
                ref[j0:j1, i0:i1] = a
                found += 1
    print(f"Copernicus tiles found: {found} of {len(tiles)} 1x1 deg tiles in the grid's bbox")
    print(f"Cells with a reference value: {np.isfinite(ref).sum():,} of {ref.size:,}")

    profile = dict(driver="GTiff", dtype="float32", count=1, width=lon.size, height=lat.size,
                   crs="EPSG:4326", nodata=np.nan, compress="deflate", tiled=True,
                   transform=from_origin(lon[0] - dlon / 2, lat[-1] + dlat / 2, dlon, dlat))
    with rasterio.open(args.out, "w", **profile) as dst:
        dst.write(ref[::-1], 1)
        dst.update_tags(source="Copernicus GLO-90 DEM (AWS Open Data), Resampling.average onto GMRT nodes",
                        grid=str(args.grid))
    print("Wrote", args.out)


if __name__ == "__main__":
    main()
