"""
Repair the Darwin Island bad-data spike in FOR_TUSHAR/CUT_bath.grd (the
Mittelstaedt-group Galapagos platform bathymetry compilation), writing a
corrected GeoTIFF copy alongside the untouched original.

Method: the same one used for the (independent) Wolf Island/Darwin Island
spikes in GMRT_corridor_basemap.grd (see fix_gmrt_spikes.py) -- this is a
DIFFERENT dataset, different provenance (Maddie Young's MS thesis processing
chain, not GMRT's GridServer synthesis), but turned out to have the SAME
TWO spikes at essentially the same places: Darwin Island's documented peak
is 165 m, but this grid reached 6874 m there; Wolf Island's (a small island
near Darwin -- NOT the ~1707 m Wolf Volcano on Isabela, easy to conflate by
name) documented peak is 253 m, but this grid reached 1904.9 m there. A scan
of the full 44.5M-cell grid found every cell above 2000 m confined to the
Darwin cluster (lon ~-92.00/-92.04, lat ~1.63-1.69) and, after patching
Darwin alone, the new max (1904.9 m) sat exactly at Wolf Island's location
(lon ~-91.82, lat ~1.38) -- nothing else in this Galapagos-platform-only
crop comes anywhere close: the real N. Isabela / Wolf VOLCANO area tops out
at 1674 m here, matching its documented ~1707 m peak almost exactly. That
makes per-island height caps safe for THIS dataset specifically -- its bbox
is the Galapagos platform only, no mainland/Andes the way the wider GMRT
corridor has, which is what made a single global cutoff unsafe there.

mask = dilate(z > cap) + fill_holes per island, caps matching the ones
already validated for the GMRT fix (Wolf Island 300 m, Darwin 250 m --
comfortably above each island's documented peak, well below either spike's
multi-hundred-to-multi-thousand-metre floor). Masked cells are replaced by
iterative Laplacian (mean-of-4-neighbours) inpainting seeded from a
nearest-valid-neighbour fill, scoped to a small crop around each island (not
the whole grid -- cheaper, and keeps each patch tightly local like the GMRT
fix).

Writes CUT_bath_clean.tif (GeoTIFF, EPSG:4326, float32) -- the original
CUT_bath.grd (GMT NetCDF4/HDF5) is left untouched. GeoTIFF rather than
another NetCDF4 .grd because this project can only *read* NetCDF4/HDF5 (via
rasterio/GDAL) -- see build_cesium_mesh.py's read_grd() -- not write it;
GeoTIFF write is already a working, validated path (crop_reproject_doa_etp.py).
"""
import numpy as np
import rasterio
from scipy import ndimage

SRC = "../FOR_TUSHAR/CUT_bath.grd"
DST = "../FOR_TUSHAR/CUT_bath_clean.tif"

# name, lat0, lat1, lon0, lon1, cap_m, dilation_iters
PATCHES = [
    ("Wolf Island", 1.30, 1.45, -91.90, -91.75, 300.0, 2),
    ("Darwin Island", 1.58, 1.78, -92.10, -91.90, 250.0, 2),
]


def make_mask(sub, cap, dil_iter):
    raw = sub > cap
    mask = ndimage.binary_dilation(raw, iterations=dil_iter)
    mask = ndimage.binary_fill_holes(mask)
    return mask


def inpaint(sub, mask, iters=1200):
    out = sub.copy()
    ind = ndimage.distance_transform_edt(mask, return_distances=False, return_indices=True)
    out[mask] = sub[tuple(ind[:, mask])]
    valid = ~mask
    for _ in range(iters):
        up = np.roll(out, 1, axis=0); up[0, :] = out[0, :]
        down = np.roll(out, -1, axis=0); down[-1, :] = out[-1, :]
        left = np.roll(out, 1, axis=1); left[:, 0] = out[:, 0]
        right = np.roll(out, -1, axis=1); right[:, -1] = out[:, -1]
        avg = (up + down + left + right) / 4.0
        out = np.where(valid, sub, avg)
    return out


def main():
    print("Reading source grid (full band)...")
    with rasterio.open(SRC) as src:
        z = src.read(1).astype(np.float64)  # (ny, nx)
        transform = src.transform
    print(f"  loaded, shape {z.shape}, range [{np.nanmin(z):.1f}, {np.nanmax(z):.1f}]")

    ny, nx = z.shape
    lon = transform.c + (np.arange(nx) + 0.5) * transform.a
    lat = transform.f + (np.arange(ny) + 0.5) * transform.e  # descending (north -> south)

    total_patched = 0
    for name, lat0, lat1, lon0, lon1, cap, dil in PATCHES:
        lat_in = (lat >= lat0) & (lat <= lat1)
        lon_in = (lon >= lon0) & (lon <= lon1)
        j_idx = np.where(lat_in)[0]
        i_idx = np.where(lon_in)[0]
        j0, j1 = int(j_idx.min()), int(j_idx.max()) + 1
        i0, i1 = int(i_idx.min()), int(i_idx.max()) + 1

        sub = z[j0:j1, i0:i1].copy()
        mask = make_mask(sub, cap, dil)
        leftover = int(((sub > cap) & (~mask)).sum())
        if leftover:
            raise RuntimeError(f"{name}: {leftover} cells above cap left unmasked -- widen crop/cap before proceeding")
        before_max = float(sub[mask].max()) if mask.any() else float("nan")
        repaired = inpaint(sub, mask, iters=1200)
        z[j0:j1, i0:i1][mask] = repaired[mask]
        n = int(mask.sum())
        total_patched += n
        print(f"{name}: patched {n} cells, lat[{lat0},{lat1}] lon[{lon0},{lon1}], "
              f"max before={before_max:.1f} m -> max after={repaired[mask].max():.1f} m")

    print(f"Total cells patched: {total_patched}")
    print(f"new grid range: [{np.nanmin(z):.1f}, {np.nanmax(z):.1f}]")

    profile = {
        "driver": "GTiff",
        "height": ny,
        "width": nx,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform,
        "nodata": np.nan,
        "compress": "deflate",
        "predictor": 3,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with rasterio.open(DST, "w", **profile) as dst:
        dst.write(z.astype(np.float32), 1)
    print("wrote", DST)


if __name__ == "__main__":
    main()
