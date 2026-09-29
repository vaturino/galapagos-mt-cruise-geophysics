"""
Check every Viewer3D mesh against the source grid it was built from.

For each dataset in Viewer3D/data/manifest.json, samples up to 3000 random
vertices and looks up the source grid's nearest node at each vertex's OWN
lon/lat:
  - bathymetry meshes: vertex z_m vs the source elevation (compared as |z|,
    so --positive-down builds still match);
  - geophysics drapes: vertex `value` vs the source value grid, AND vertex
    z_m vs the terrain grid it was draped on.
It also repeats the value lookup with the source grid flipped north-south.
A correctly built mesh matches ~100 % as-is and ~0 % flipped; an upside-down
build shows the reverse. This is how two stale upside-down meshes were found
(DRFT04RR and Geophys_Barckhausen_Magnetics, both built from grids written
before fix_mgds_grid.py's old-style-GMT row-order fix).

Read-only. Exits non-zero if any mesh doesn't match. Usage (from scripts/):
    venv/bin/python3 audit_viewer_meshes.py [path/to/Viewer3D/data/]
"""
import glob
import json
import os
import sys

import numpy as np

from build_cesium_mesh import read_grd

R = "../"
B = R + "GMRT_regional/Backscatter_MGDS/"
G = R + "GMRT_regional/Geophysics_MGDS/"
F = R + "FOR_TUSHAR/"
BASEMAP = R + "GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap_clean.grd"
MITTEL_BATH = F + "CUT_bath_clean.tif"

# dataset id -> (source grid(s), section to compare, drape terrain or None)
SOURCES = {
    "GMRT_basemap": ([BASEMAP], "z_m", None),
    "DOA_ETP_MBES_corridor": ([R + "DOA_ETP_MBES/DOA_ETP_MBES_galapagos_corridor_4326_clean.tif"], "z_m", None),
    "MV1007": ([R + "GeoMapApp_ready/MV1007_bathymetry_mosaic.grd"], "z_m", None),
    "GSC_regional": ([B + "GSC_97-86W_Compilation/GSC_97-86W_100m_comp.grd"], "z_m", None),
    "DRFT04RR": ([B + "DRFT04RR_GSC_Bathymetry/galapagos.100m.comb_geomapapp.grd"], "z_m", None),
    "TN188": (sorted(glob.glob(B + "TN188_GSC_Bathymetry_8m/TN188_*_geomapapp.grd")), "z_m", None),
    "Mittelstaedt_Galapagos_Bathy": ([MITTEL_BATH], "z_m", None),
    "Geophys_Barckhausen_Magnetics": ([G + "Barckhausen_CentralAmerica_Magnetics/central_america_mag_geomapapp.grd"], "value", BASEMAP),
    "Geophys_Bassett_ResidualGravity": ([G + "Bassett_CentralAmerica_ResidualGravity/CentAm_Residual_gravity_geomapapp.grd"], "value", BASEMAP),
    "Geophys_SR1806_MBA": ([G + "SR1806_CocosNazca_Gravity/105W95W1S5N_mba.grd"], "value", BASEMAP),
    "Geophys_SR1806_FAA_GlobalTopo": ([G + "SR1806_CocosNazca_Gravity/105W95W1S5N_mba_global_topo_global_FAA.grd"], "value", BASEMAP),
    "Geophys_SR1806_FAA_ShipTopo": ([G + "SR1806_CocosNazca_Gravity/105W95W1S5N_mba_ship_topo_global_FAA.grd"], "value", BASEMAP),
    "Geophys_SR1806_RMBA": ([G + "SR1806_CocosNazca_Gravity/105W95W1S5N_rmba_1k.grd"], "value", BASEMAP),
    "Geophys_SR1806_CrustThickness": ([G + "SR1806_CocosNazca_Gravity/105W95W1S5N_crust_1k.grd"], "value", BASEMAP),
    "Geophys_SR1806_ThermalAnomaly": ([G + "SR1806_CocosNazca_Gravity/105W95W1S5N_thermal_1k.grd"], "value", BASEMAP),
    "Geophys_Mittelstaedt_FreeAir": ([F + "CUT_FA.grd"], "value", MITTEL_BATH),
    "Geophys_Mittelstaedt_MagAnomaly": ([F + "CUT_maganom_1km_blockmed.grd"], "value", MITTEL_BATH),
    "Geophys_Mittelstaedt_Magnetization": ([F + "CUT_magnetization.grd"], "value", MITTEL_BATH),
    "Geophys_Mittelstaedt_RMBA": ([F + "CUT_RMBA.grd"], "value", MITTEL_BATH),
}
PASS_PCT = 99.0
_cache = {}


def load_mesh(d):
    m = json.load(open(os.path.join(d, "meta.json")))
    b = open(os.path.join(d, "mesh.bin"), "rb").read()
    return m, {s["name"]: np.frombuffer(b[s["offset"]:s["offset"] + s["length"]], dtype=s["dtype"]).reshape(s["count"], -1)
               for s in m["sections"]}


def grid(path):
    if path not in _cache:
        x, y, z, h = read_grd(path)
        x, y, z = np.asarray(x, float), np.asarray(y, float), np.asarray(z[:], np.float64)
        h.close()
        if y[0] > y[-1]:
            y, z = y[::-1], z[::-1]
        _cache[path] = (x, y, z)
    return _cache[path]


def sample(paths, lo, la, flip):
    """(n_grids, n_vertices) nearest-node values; NaN where a grid doesn't cover the vertex."""
    outs = []
    for p in paths:
        x, y, z = grid(p)
        out = np.full(lo.size, np.nan)
        inb = (lo >= x.min() - 1e-9) & (lo <= x.max() + 1e-9) & (la >= y.min() - 1e-9) & (la <= y.max() + 1e-9)
        i = np.clip(np.searchsorted(x, lo[inb]), 1, x.size - 1)
        i -= (lo[inb] - x[i - 1]) < (x[i] - lo[inb])
        j = np.clip(np.searchsorted(y, la[inb]), 1, y.size - 1)
        j -= (la[inb] - y[j - 1]) < (y[j] - la[inb])
        out[inb] = z[y.size - 1 - j if flip else j, i]
        outs.append(out)
    return np.array(outs)


def main():
    data = sys.argv[1] if len(sys.argv) > 1 else R + "Viewer3D/data/"
    ids = [e["id"] for e in json.load(open(os.path.join(data, "manifest.json")))["datasets"]] \
        if os.path.exists(os.path.join(data, "manifest.json")) else sorted(os.listdir(data))
    rng = np.random.default_rng(0)
    bad = 0
    print(f"{'dataset':36s} {'section':7s} {'as-is':>7s} {'N-S flipped':>12s} {'drape terrain':>14s}")
    for did in ids:
        if did not in SOURCES:
            print(f"{did:36s} -- no source registered in audit_viewer_meshes.SOURCES")
            bad += 1
            continue
        paths, sec, terrain = SOURCES[did]
        missing = [p for p in paths + ([terrain] if terrain else []) if not os.path.exists(p)]
        if not paths or missing:
            print(f"{did:36s} -- source missing: {missing or 'none found'}")
            bad += 1
            continue
        m, s = load_mesh(os.path.join(data, did))
        k = rng.choice(m["vertex_count"], min(3000, m["vertex_count"]), replace=False)
        lo = np.degrees(s["lon_rad"].ravel()[k])
        la = np.degrees(s["lat_rad"].ravel()[k])
        v = np.abs(s[sec].ravel()[k].astype(float))
        match = lambda a: np.isclose(v[None, :], np.abs(a), rtol=1e-4, atol=0.05).any(axis=0)
        a0, a1 = sample(paths, lo, la, False), sample(paths, lo, la, True)
        ok = np.isfinite(a0).any(axis=0)
        r0, r1 = match(a0)[ok].mean() * 100, match(a1)[ok].mean() * 100
        rt, tcol = 100.0, ""
        if terrain:
            zt = sample([terrain], lo, la, False)[0]
            inside = np.isfinite(zt)  # vertices beyond the terrain bbox drape on its edge by design
            rt = np.isclose(s["z_m"].ravel()[k][inside], zt[inside], atol=0.5).mean() * 100
            tcol = f"{rt:.1f}%"
        fail = r0 < PASS_PCT or rt < PASS_PCT
        bad += fail
        print(f"{did:36s} {sec:7s} {r0:6.1f}% {r1:11.1f}% {tcol:>14s}{'   <-- MISMATCH' if fail else ''}")
    print(f"\n{'ALL MESHES MATCH THEIR SOURCES' if not bad else f'{bad} DATASET(S) FAILED'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
