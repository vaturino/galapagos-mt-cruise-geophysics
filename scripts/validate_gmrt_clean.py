"""
Check a repaired GMRT corridor basemap (fix_gmrt_spikes.py output) against
references independent of the repair itself. Prints PASS/FAIL per check and
exits non-zero if any fail. Read-only.

Checks:
  1. Real summits survive: the grid max within 0.05 deg of each named peak
     is within [-400 m, +100 m] of its documented height (-400 allows for a
     ~245 m cell missing the exact summit; +100 catches a spike on the peak).
  2. Nothing on land is higher than Chimborazo (6263 m), the highest real
     point in this bbox (Sierra Nevada de Santa Marta, 5700 m+, is north of
     10.7N and outside it).
  3. Known artifact sites are gone: land sites against Copernicus GLO-90,
     ocean sites against the DOA-ETP multibeam (MBES) compilation, both
     sampled at the site; |grid - reference| must be < 500 m.
  4. Real steep ocean features the ocean filter must NOT touch: the scarp
     at 8.64N 84.49W (MBES -3395 m) keeps its original value, and the real
     ~2 km-deep closed depression at 3.82N 89.95W (MBES to about -4590 m;
     it contains the bad -6456 m cell) still reaches below -4000 m.
  5. Wolf/Darwin Island patches still hold (max below 300 / 250 m).
  6. Land-wide: no cell differs from Copernicus by > 500 m.
  7. The repair is surgical: < 0.5 % of cells differ from the original.

Usage (from scripts/):
    venv/bin/python3 validate_gmrt_clean.py [path/to/GMRT_corridor_basemap_clean.grd]
"""
import sys

import numpy as np
import rasterio

from netcdf_lite import netcdf_file

RAW = "../GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap.grd"
CLEAN = "../GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap_clean.grd"
COP_REF = "../GMRT_regional/GMRT_Basemap/copernicus_glo90_on_gmrt_grid.tif"
MBES = "../DOA_ETP_MBES/DOA_ETP_MBES_galapagos_corridor_4326_clean.tif"

# name, lat, lon, documented summit height (m)
PEAKS = [
    ("Chimborazo", -1.469, -78.817, 6263),
    ("Cotopaxi", -0.681, -78.436, 5897),
    ("Cayambe", 0.029, -77.986, 5790),
    ("Nevado del Ruiz", 4.892, -75.324, 5321),
    ("Cerro Chirripo", 9.484, -83.489, 3820),
    ("Volcan Baru", 8.808, -82.543, 3474),
]
LAND_MAX_M = 6263 + 100
# name, lat, lon -- located by scanning the raw grid; see fix_gmrt_spikes.py
LAND_SITES = [
    ("Darien spike (raw 7183 m)", 7.728, -77.748),
    ("Esmeraldas spike (raw 4983 m)", 0.370, -79.748),
    ("Tumaco lowland spike (raw 3766 m)", 2.582, -78.151),
    ("Antioquia spike (raw 6570 m)", 7.048, -75.555),
    ("S. Ecuador pit (raw 1024 m)", -4.250, -79.060),
]
OCEAN_SITES = [
    ("ocean spike (raw +1910 m)", 2.766, -80.304),
    ("ocean spike (raw +399 m)", 2.440, -85.505),
    ("ocean pit (raw -6456 m)", 3.816, -89.947),
    ("ocean pit (raw -4835 m)", 2.368, -84.642),
    ("ocean pit (raw -4373 m)", 4.998, -84.956),
]
REAL_OCEAN = ("real scarp (MBES -3395 m)", 8.638, -84.486)
REAL_BASIN = ("real depression (MBES to ~-4590 m)", 3.816, -89.935, -4000.0)
ISLANDS = [("Wolf Island", 1.30, 1.45, -91.90, -91.75, 300.0),
           ("Darwin Island", 1.58, 1.78, -92.10, -91.90, 250.0)]


def load(path):
    nc = netcdf_file(path, "r", mmap=False)
    lon = np.asarray(nc.variables["lon"][:]).copy()
    lat = np.asarray(nc.variables["lat"][:]).copy()
    z = np.array(nc.variables["altitude"][:], dtype=np.float32)
    nc.close()
    return lon, lat, z


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else CLEAN
    print("Validating", path)
    lon, lat, z = load(path)
    _, _, z0 = load(RAW)
    with rasterio.open(COP_REF) as ds:
        ref = ds.read(1)[::-1]
    mbes = rasterio.open(MBES)
    ij = lambda la, lo: (int(np.argmin(abs(lat - la))), int(np.argmin(abs(lon - lo))))
    fails = 0

    def check(ok, msg):
        nonlocal fails
        fails += not ok
        print(("  PASS  " if ok else "  FAIL  ") + msg)

    print("1. Real summits survive")
    for name, la, lo, h in PEAKS:
        j, i = ij(la, lo); r = int(round(0.05 / (lat[1] - lat[0])))
        m = float(np.nanmax(z[j - r:j + r + 1, i - r:i + r + 1]))
        check(-400 <= m - h <= 100, f"{name}: grid max {m:.0f} m vs documented {h} m ({m - h:+.0f})")

    print("2. Land maximum")
    j, i = np.unravel_index(np.nanargmax(z), z.shape)
    check(z[j, i] <= LAND_MAX_M, f"grid max {z[j, i]:.0f} m at {lat[j]:.3f},{lon[i]:.3f} (limit {LAND_MAX_M} m)")

    print("3. Known artifact sites repaired")
    for name, la, lo in LAND_SITES:
        j, i = ij(la, lo)
        check(abs(z[j, i] - ref[j, i]) < 500, f"{name}: grid {z[j, i]:.0f} m, Copernicus {ref[j, i]:.0f} m")
    for name, la, lo in OCEAN_SITES:
        j, i = ij(la, lo)
        mb = float(list(mbes.sample([(lon[i], lat[j])]))[0][0])
        check(abs(z[j, i] - mb) < 500, f"{name}: grid {z[j, i]:.0f} m, MBES {mb:.0f} m")

    print("4. Real ocean feature untouched")
    name, la, lo = REAL_OCEAN
    j, i = ij(la, lo)
    check(z[j, i] == z0[j, i], f"{name}: grid {z[j, i]:.0f} m, original {z0[j, i]:.0f} m")
    name, la, lo, deepest = REAL_BASIN
    j, i = ij(la, lo)
    m = float(np.nanmin(z[j - 4:j + 5, i - 4:i + 5]))
    check(m <= deepest, f"{name}: deepest cell within ~1 km {m:.0f} m (must be <= {deepest:.0f} m)")

    print("5. Wolf/Darwin patches hold")
    for name, a, b, c, d, cap in ISLANDS:
        sub = z[np.searchsorted(lat, a):np.searchsorted(lat, b), np.searchsorted(lon, c):np.searchsorted(lon, d)]
        check(np.nanmax(sub) <= cap, f"{name}: max {np.nanmax(sub):.0f} m (cap {cap:.0f} m)")

    print("6. Land-wide agreement with Copernicus")
    dom = np.isfinite(ref) & np.isfinite(z) & ((z > 0) | (ref > 0.5))
    dd = (z - ref)[dom]
    n_bad = int((abs(dd) > 500).sum())
    check(n_bad == 0, f"{n_bad} land cells off by >500 m; median {np.median(dd):+.1f} m, "
                      f"99.9th pct |diff| {np.percentile(abs(dd), 99.9):.0f} m, max |diff| {abs(dd).max():.0f} m")

    print("7. Repair is surgical")
    changed = int((z != z0).sum() - (np.isnan(z) & np.isnan(z0)).sum())
    check(changed < 0.005 * z.size, f"{changed:,} of {z.size:,} cells changed ({100 * changed / z.size:.3f} %)")

    print(f"\n{'ALL CHECKS PASSED' if fails == 0 else f'{fails} CHECK(S) FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
