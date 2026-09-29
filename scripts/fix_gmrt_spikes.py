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

Two further stages (added 2026-09-29) handle the same class of artifact
across the rest of the corridor, which the Wolf/Darwin patches don't reach.
GMRT's land synthesis here has thousands of bad cells, mostly in Colombia,
Ecuador, and Panama: multi-cell blobs 3-5 km too high (7183 m in the Darien,
where the real terrain is ~1.5 km; 4983 m in the Esmeraldas lowlands, real
~0.7 km; up to 3766 m in the Pacific mangrove lowlands near Tumaco, real
~0-30 m) and some pits 1-2 km too deep (southern Ecuadorian Andes). Most are
not caught by any height cutoff, since real Andean summits reach 6263 m.

  - Land (stage 2): wherever the Copernicus GLO-90 DEM has a tile, compare
    against it (copernicus_glo90_on_gmrt_grid.tif, built by
    fetch_copernicus_reference.py; area-averaged onto the GMRT nodes). On
    land the two agree to a median of -5 m, MAD 10 m, and a 245 m cell's
    point value can't differ from its area average by more than ~200 m even
    on 45 deg slopes. Cells where |GMRT - Copernicus| > LAND_CORE_M (300 m)
    are artifacts (a 500 m cutoff left ~4400 cells at 300-500 m, 84 % of
    them too HIGH -- spike skirts, not symmetric resampling noise); the mask grows
    from them into connected cells with |diff| > LAND_GROW_M (150 m) to take
    in each blob's tapering skirt. Masked cells get Copernicus plus a
    Laplacian-inpainted residual (GMRT - Copernicus) from the unmasked
    neighbours, so the repaired surface has Copernicus's real terrain shape
    and joins GMRT seamlessly at the mask edge. A false positive here costs
    little: that cell just takes an independent 90 m DEM's value.
  - Ocean (stage 3): no independent grid covers the whole ocean, so cells
    outside Copernicus land use a local test for isolated spikes/pits:
    |z - median of 5x5 cells| > OCEAN_CORE_M (1500 m), grown only into
    8-connected neighbours with |resid| > OCEAN_GROW_M (1000 m), then
    Laplacian-inpainted (same as the island patches). Both numbers were
    checked against the DOA-ETP multibeam (MBES) compilation: every cell
    flagged where MBES has data is an artifact (e.g. a +1910 m "island" at
    2.77N 80.30W where MBES has -2400 m; a -6456 m single-cell pit at 3.82N
    89.95W where MBES has -3290 m), while real steep features stay below it
    (-3585 m at 8.64N 84.49W, MBES -3395 m, residual -1105 m). The narrow
    window and tight growth matter: that -6456 m cell sits inside a REAL
    ~2 km-deep closed depression (MBES down to about -4590 m), and a wider
    window with growth into |resid| > 300 m flattened the whole depression.

validate_gmrt_clean.py checks the result against both references.
"""
import numpy as np
import rasterio
from netcdf_lite import netcdf_file
from scipy import ndimage

SRC = "../GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap.grd"
DST = "../GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap_clean.grd"
COP_REF = "../GMRT_regional/GMRT_Basemap/copernicus_glo90_on_gmrt_grid.tif"

PATCHES = [
    # name, lat0, lat1, lon0, lon1, cap_m, dilation_iters
    ("Wolf Island",   1.30, 1.45, -91.90, -91.75, 300.0, 2),
    ("Darwin Island", 1.58, 1.78, -92.10, -91.90, 250.0, 2),
]

LAND_CORE_M = 300.0    # |GMRT - Copernicus| that marks a cell as bad
LAND_GROW_M = 150.0    # connected cells above this join the bad blob
OCEAN_CORE_M = 1500.0  # |z - 5x5 median| that marks an ocean cell as bad
OCEAN_GROW_M = 1000.0  # adjacent cells above this join it (keeps real basins out)
MEDIAN_WIN = 5         # ~1.2 km at this grid's ~245 m spacing
BOX_MARGIN = 4         # cells of context around each blob for inpainting


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


def grow_mask(core, grow, domain, dilate):
    """core cells plus every 8-connected cell of (grow | core), inside domain, dilated `dilate`."""
    lab, _ = ndimage.label((grow | core) & domain, structure=np.ones((3, 3)))
    keep = np.unique(lab[core])
    keep = keep[keep > 0]
    mask = np.isin(lab, keep)
    if dilate:
        mask = ndimage.binary_dilation(mask, iterations=dilate)
    return mask & domain


def inpaint_blobs(field, mask):
    """Laplacian-inpaint `field` over `mask`, one bounding box per blob (in place)."""
    lab, n = ndimage.label(mask)
    ny, nx = field.shape
    for k, sl in enumerate(ndimage.find_objects(lab), 1):
        j0 = max(sl[0].start - BOX_MARGIN, 0); j1 = min(sl[0].stop + BOX_MARGIN, ny)
        i0 = max(sl[1].start - BOX_MARGIN, 0); i1 = min(sl[1].stop + BOX_MARGIN, nx)
        m = mask[j0:j1, i0:i1]  # includes neighbouring blobs in the box: all unknown
        field[j0:j1, i0:i1][m] = inpaint(field[j0:j1, i0:i1], m, iters=600)[m]
    return n


def fix_land(z, ref):
    """Stage 2: Copernicus-guided repair of land cells. Returns (mask, stats)."""
    domain = np.isfinite(ref) & np.isfinite(z) & ((z > 0) | (ref > 0.5))
    resid = np.where(domain, z - ref, 0.0)
    absr = np.abs(resid)
    core = domain & (absr > LAND_CORE_M)
    mask = grow_mask(core, absr > LAND_GROW_M, domain, dilate=1)
    stats = dict(core=int(core.sum()), cells=int(mask.sum()),
                 hi=int((core & (resid > 0)).sum()), lo=int((core & (resid < 0)).sum()),
                 worst_hi=float(resid[core].max()) if core.any() else 0.0,
                 worst_lo=float(resid[core].min()) if core.any() else 0.0)
    r = resid.copy()
    stats["blobs"] = inpaint_blobs(r, mask)
    z[mask] = ref[mask] + r[mask]
    return mask, stats


def fix_ocean(z, land_domain):
    """Stage 3: local-median repair of ocean cells outside Copernicus land. Returns (mask, stats)."""
    domain = ~land_domain & np.isfinite(z)
    med = ndimage.median_filter(np.where(np.isfinite(z), z, 0.0).astype(np.float32), size=MEDIAN_WIN)
    resid = np.where(domain, z - med, 0.0)
    absr = np.abs(resid)
    core = domain & (absr > OCEAN_CORE_M)
    mask = grow_mask(core, absr > OCEAN_GROW_M, domain, dilate=0)
    stats = dict(core=int(core.sum()), cells=int(mask.sum()),
                 hi=int((core & (resid > 0)).sum()), lo=int((core & (resid < 0)).sum()))
    stats["blobs"] = inpaint_blobs(z, mask)
    return mask, stats


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

    print(f"Reading Copernicus reference {COP_REF} (build it with fetch_copernicus_reference.py)...")
    with rasterio.open(COP_REF) as ds:
        ref = ds.read(1)[::-1].astype(np.float64)  # north-up -> south->north rows, like the grd
    if ref.shape != z_full.shape:
        raise RuntimeError(f"reference shape {ref.shape} != grid shape {z_full.shape} -- rebuild it for this grid")

    land_mask, s = fix_land(z_full, ref)
    total_patched += s["cells"]
    print(f"Land (vs Copernicus): {s['core']} cells off by >{LAND_CORE_M:.0f} m "
          f"({s['hi']} too high, worst +{s['worst_hi']:.0f} m; {s['lo']} too low, worst {s['worst_lo']:.0f} m); "
          f"patched {s['cells']} cells in {s['blobs']} blobs")

    land_domain = np.isfinite(ref) & ((ref > 0.5) | (z_full > 0))
    _, s = fix_ocean(z_full, land_domain)
    total_patched += s["cells"]
    print(f"Ocean (vs {MEDIAN_WIN}x{MEDIAN_WIN} median): {s['core']} cells off by >{OCEAN_CORE_M:.0f} m "
          f"({s['hi']} spikes, {s['lo']} pits); patched {s['cells']} cells in {s['blobs']} blobs")

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
            "Laplacian-inpainted values; land cells off from Copernicus GLO-90 by >500 m replaced with "
            "Copernicus plus an inpainted residual; ocean cells off from their 5x5 median by >1500 m "
            "inpainted (see fix_gmrt_spikes.py). Original file: GMRT_corridor_basemap.grd."
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
