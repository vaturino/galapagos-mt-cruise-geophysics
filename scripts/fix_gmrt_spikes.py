"""
Repair the Wolf Island / Darwin Island bad-data spikes in
GMRT_corridor_basemap.grd, writing a corrected copy alongside the
original (which is left untouched).

Method (see project notes / chat log for the full investigation):
  - Both spikes are smooth, multi-cell "bad patch" artifacts baked into
    GMRT's own synthesis (not introduced by anything in this project) --
    NOT single-pixel noise, and NOT explainable as real terrain: Wolf
    Island's documented peak is 253 m (Wikipedia) but the grid reaches
    1804 m there; Darwin Island's documented peak is 165 m but the grid
    reaches 6288 m there. The bad cells sit right next to genuine,
    plausible island terrain (e.g. an untouched 268 m cell survives one
    row away from Wolf's spike), so a single global height threshold is
    unsafe (real Galapagos shield volcanoes on Isabela reach 1097-1707 m,
    and the wider corridor includes the Andes up to ~6260 m) -- the fix
    is scoped tightly to the two islands' own small footprints.
  - mask = dilate(z > cap) + fill_holes, cap chosen well above each
    island's documented peak (Wolf 300 m, Darwin 250 m). Verified zero
    leftover unmasked cells above cap in a generously wide crop around
    each island.
  - masked cells are replaced by iterative Laplacian (mean-of-4-neighbors)
    inpainting seeded from a nearest-valid-neighbor fill -- i.e. a smooth
    surface consistent with the surrounding valid bathymetry/topography,
    which is the best-effort honest answer at this grid's ~245 m native
    resolution (both islands are only ~1-2 km across, i.e. a handful of
    cells, so this coarse product was never going to resolve their true
    summits precisely even before the bug).
"""
import numpy as np
from netcdf_lite import netcdf_file
from scipy import ndimage

SRC = "../GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap.grd"
DST = "../GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap_clean.grd"

PATCHES = [
    # name, lat0, lat1, lon0, lon1, cap_m, dilation_iters
    ("Wolf Island",   1.30, 1.45, -91.90, -91.75, 300.0, 2),
    ("Darwin Island", 1.58, 1.78, -92.10, -91.90, 250.0, 2),
]


def crop_idx(lat, lon, lat0, lat1, lon0, lon1):
    j0 = np.searchsorted(lat, lat0)
    j1 = np.searchsorted(lat, lat1)
    i0 = np.searchsorted(lon, lon0)
    i1 = np.searchsorted(lon, lon1)
    return j0, j1, i0, i1


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
    print("Reading source grid...")
    nc = netcdf_file(SRC, "r", mmap=True)
    v = nc.variables
    lon = np.asarray(v["lon"][:]).copy()
    lat = np.asarray(v["lat"][:]).copy()
    z_attrs = dict(v["altitude"]._attributes)
    lon_attrs = dict(v["lon"]._attributes)
    lat_attrs = dict(v["lat"]._attributes)
    global_attrs = dict(nc._attributes)

    print("Loading full altitude array into memory (float64, ~%.0f MB)..." %
          (lat.size * lon.size * 8 / 1e6))
    z_full = np.array(v["altitude"][:], dtype=np.float64)
    print("  loaded, shape", z_full.shape)

    total_patched = 0
    for name, lat0, lat1, lon0, lon1, cap, dil in PATCHES:
        j0, j1, i0, i1 = crop_idx(lat, lon, lat0, lat1, lon0, lon1)
        sub = z_full[j0:j1, i0:i1].astype(np.float32)
        mask = make_mask(sub, cap, dil)
        leftover = ((sub > cap) & (~mask)).sum()
        if leftover:
            raise RuntimeError(f"{name}: {leftover} cells above cap left unmasked -- widen crop/cap before proceeding")
        before_max = sub[mask].max() if mask.any() else float("nan")
        repaired = inpaint(sub, mask, iters=1200)
        z_full[j0:j1, i0:i1][mask] = repaired[mask].astype(np.float64)
        n = int(mask.sum())
        total_patched += n
        print(f"{name}: patched {n} cells, lat[{lat0},{lat1}] lon[{lon0},{lon1}], "
              f"max before={before_max:.1f} m -> max after={repaired[mask].max():.1f} m")

    print(f"Total cells patched: {total_patched} out of {z_full.size} ({100*total_patched/z_full.size:.5f}%)")

    new_range = np.array([np.nanmin(z_full), np.nanmax(z_full)], dtype=">f8")
    print("New actual_range for altitude:", new_range, " (was", z_attrs.get("actual_range"), ")")

    print("Writing corrected grid to", DST)
    out = netcdf_file(DST, "w")
    for k, val in global_attrs.items():
        setattr(out, k, val)
    setattr(out, "history",
            (global_attrs.get("history", b"").decode() if isinstance(global_attrs.get("history"), bytes) else str(global_attrs.get("history", ""))) +
            "\nPatched by Viewer3D project: Wolf/Darwin Island bad-data spikes replaced with local "
            "Laplacian-inpainted values (see fix_gmrt_spikes.py). Original file: GMRT_corridor_basemap.grd."
            )

    out.createDimension("lat", lat.size)
    out.createDimension("lon", lon.size)

    v_lat = out.createVariable("lat", "f8", ("lat",))
    v_lat[:] = lat
    for k, val in lat_attrs.items():
        setattr(v_lat, k, val)

    v_lon = out.createVariable("lon", "f8", ("lon",))
    v_lon[:] = lon
    for k, val in lon_attrs.items():
        setattr(v_lon, k, val)

    v_alt = out.createVariable("altitude", "f8", ("lat", "lon"))
    v_alt[:] = z_full
    for k, val in z_attrs.items():
        if k == "actual_range":
            setattr(v_alt, k, new_range)
        else:
            setattr(v_alt, k, val)

    out.close()
    print("Done.")


if __name__ == "__main__":
    main()
