"""
Tests for native_render.py. Run from scripts/:
    source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env
    python -m pytest -q tests/test_native_render.py

What they establish:
  - exported GeoTIFFs hold the exact source numbers at the exact source cell centres
    (no resampling, no half-cell shift), for GeoTIFF, modern-netCDF, old-style-GMT and
    0-360-longitude sources;
  - rows are not flipped (orientation checked against an independent survey);
  - the checks are able to fail (a one-cell shift and a north-south flip are both caught).
"""
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_render as nr  # noqa: E402

R = nr.REPO


def _pixel_centre_check(tif, g, n=400, seed=0):
    """Max |value difference| and max coordinate error (in cells) between tif and Grid g."""
    rng = np.random.default_rng(seed)
    with rasterio.open(tif) as d:
        a = d.read(1)
        t = d.transform
    rows = rng.integers(0, g.z.shape[0], n)
    cols = rng.integers(0, g.z.shape[1], n)
    xs, ys = rasterio.transform.xy(t, rows, cols, offset="center")
    coord_err = max(np.max(np.abs(np.asarray(xs) - g.x[cols])) / abs(g.dx),
                    np.max(np.abs(np.asarray(ys) - g.y[rows])) / abs(g.dy))
    va, vg = a[rows, cols], g.z[rows, cols]
    both = np.isfinite(va) & np.isfinite(vg)
    assert (np.isfinite(va) == np.isfinite(vg)).all(), "no-data pattern changed"
    return float(np.max(np.abs(va[both] - vg[both]))) if both.any() else 0.0, float(coord_err)


def _synthetic():
    x = -91.0 + 0.001 * np.arange(300)
    y = 1.0 - 0.0005 * np.arange(200)
    X, Y = np.meshgrid(x, y)
    z = (-2000 + 500 * np.sin(X * 40) * np.cos(Y * 60)).astype(np.float32)
    z[50:60, 70:90] = np.nan
    return nr.Grid(x, y, z, 4326, str(R / "synthetic"))


def test_synthetic_roundtrip_exact(tmp_path):
    g = _synthetic()
    f = nr.write_elev_tif(g, tmp_path / "s.tif")
    dv, dc = _pixel_centre_check(f, g)
    assert dv == 0.0 and dc < 1e-6, (dv, dc)


def test_check_detects_one_cell_shift(tmp_path):
    """Prove the round-trip check can fail: shift the grid by one cell and it must be caught."""
    g = _synthetic()
    bad = nr.Grid(g.x + g.dx, g.y, g.z, 4326, g.source)
    f = nr.write_elev_tif(bad, tmp_path / "bad.tif")
    _, dc = _pixel_centre_check(f, g)
    assert dc > 0.99


@pytest.mark.parametrize("ds,box", [
    ("Mittelstaedt_50m", (-91.30, -91.10, 0.60, 0.80)),          # GeoTIFF source
    ("MV1007_bathymetry", (-91.30, -91.10, 0.60, 0.80)),         # modern netCDF
    ("GMRT_corridor", (-91.5, -90.5, 0.0, 1.0)),                 # modern netCDF (lon/lat/altitude or x/y/z)
    ("DRFT04RR_86W_bathymetry_10m", (-86.1, -86.0, 0.75, 0.85)), # old-style GMT
])
def test_clip_matches_source_exactly(tmp_path, ds, box):
    g = nr.clip(ds, box)
    assert g is not None
    f = nr.write_elev_tif(g, tmp_path / "c.tif", overviews=False)
    dv, dc = _pixel_centre_check(f, g)
    assert dv == 0.0 and dc < 1e-6, (dv, dc)
    # clip = same numbers as reading the whole source file (window logic is not shifting)
    full = nr.load_ds(ds, nr.source_files(ds)[0])
    i0 = int(np.argmin(np.abs(full.x - g.x[0])))
    j0 = int(np.argmin(np.abs(full.y - g.y[0])))
    assert abs(full.x[i0] - g.x[0]) < 1e-9 and abs(full.y[j0] - g.y[0]) < 1e-9
    sub = full.z[j0:j0 + g.z.shape[0], i0:i0 + g.z.shape[1]]
    assert np.array_equal(np.nan_to_num(sub, nan=-1e30), np.nan_to_num(g.z, nan=-1e30))


def test_tn188_0_360_original_equals_geomapapp_copy():
    a = nr.load(R / nr.B / "TN188_GSC_Bathymetry_8m/TN188_DSL120A_8mbat.01.grd", 4326)
    b = nr.load(R / nr.B / "TN188_GSC_Bathymetry_8m/TN188_DSL120A_8mbat.01_geomapapp.grd", 4326)
    assert np.abs(a.x - b.x).max() < 0.02 * abs(b.dx) and np.abs(a.y - b.y).max() < 0.02 * abs(b.dy)
    m = np.isfinite(a.z) & np.isfinite(b.z)
    assert m.mean() > 0.01 and np.allclose(a.z[m], b.z[m], atol=1e-3)  # tile is ~3 % covered (one swath)
    assert not (np.isfinite(a.z[::-1]) & np.isfinite(b.z)).any()  # a N-S flip would not line up


def _corr_on_common(g_fine, g_ref):
    """Correlation of g_fine sampled at g_ref's cell centres (nearest), and the same with g_fine flipped N-S."""
    ix = np.clip(np.searchsorted(g_fine.x, g_ref.x), 0, g_fine.x.size - 1)
    iy = np.clip(np.searchsorted(-g_fine.y, -g_ref.y), 0, g_fine.y.size - 1)
    a = g_fine.z[np.ix_(iy, ix)]
    af = g_fine.z[::-1][np.ix_(iy, ix)]
    out = []
    for s in (a, af):
        m = np.isfinite(s) & np.isfinite(g_ref.z)
        out.append(np.corrcoef(s[m], g_ref.z[m])[0, 1] if m.sum() > 100 else np.nan)
    return out


def test_old_style_at5009_orientation_vs_independent_survey():
    box = (-90.85, -90.60, 0.20, 0.50)  # Pinta rift, AT50-09 20 m vs Mittelstaedt 50 m
    at = nr.load(R / nr.B / "AT50-09BC_GalapagosPlatform_Bathymetry/AT5009_MB_PintaRift_WGS84_20m.grd", 4326,
                 nr.lonlat_bbox_to_crs(box, 4326))
    ref = nr.clip("Mittelstaedt_50m", box)
    c, cf = _corr_on_common(at, ref)
    assert c > 0.95 and (np.isnan(cf) or cf < c - 0.3), (c, cf)


def test_drft_sidescan_utm15n_lands_on_drft_bathymetry():
    """EPSG:32615 for the MR1 sidescan: the swath centre must fall inside the DRFT04RR survey."""
    from pyproj import Transformer
    p = nr.source_files("DRFT04RR_sidescan")[0]
    g = nr.load(p, 32615)
    j, i = np.argwhere(np.isfinite(g.z))[len(np.argwhere(np.isfinite(g.z))) // 2]
    lon, lat = Transformer.from_crs(32615, 4326, always_xy=True).transform(g.x[i], g.y[j])
    assert -93.5 < lon < -88.5 and -3 < lat < 3, (lon, lat)
    b = nr.clip("GSC_97-86W_100m", (lon - 0.05, lon + 0.05, lat - 0.05, lat + 0.05))
    assert b is not None and np.isfinite(b.z).mean() > 0.5


def test_nav_tif_has_world_file_and_is_classic(tmp_path):
    g = nr.clip("Mittelstaedt_50m", (-91.25, -91.20, 0.65, 0.70))
    rgb, valid, _ = nr.colorize(g, "bathy")
    f = nr.write_color_tif(g, rgb, valid, tmp_path / "n.tif", nav=True)
    assert (tmp_path / "n.tfw").exists() and (tmp_path / "n.prj").exists()
    with open(f, "rb") as fh:
        assert fh.read(4) in (b"II*\x00", b"MM\x00*")  # classic TIFF, not BigTIFF
    with rasterio.open(f) as d:
        assert d.count == 3 and d.dtypes[0] == "uint8" and d.crs.to_epsg() == 4326
        assert d.width == g.z.shape[1] and d.height == g.z.shape[0]
    tfw = [float(v) for v in open(tmp_path / "n.tfw").read().split()]
    assert abs(tfw[4] - g.x[0]) < 1e-9 and abs(tfw[5] - g.y[0]) < 1e-9  # world file = pixel centre


def test_hdf5_grd_orientation_free_air_tracks_bathymetry():
    """NetCDF-4 (HDF5) .grd via GDAL: free-air gravity correlates with bathymetry at these
    wavelengths, so the as-read correlation must be clearly positive and beat the N-S flipped one."""
    fa = nr.load(R / "FOR_TUSHAR/CUT_FA.grd", 4326)
    ref = nr.load(R / "FOR_TUSHAR/CUT_bath_clean.tif", 4326)
    c, cf = _corr_on_common(ref, fa)  # bathymetry sampled at the FA cell centres
    assert c > 0.5 and c > cf + 0.3, (c, cf)


def _tilted_plane(lat0, slope_deg=10.0, dip_az=60.0, n=120, cell_deg=0.0005):
    """Exact plane z = tan(slope) * (distance along dip_az) on a lon/lat grid, using the same
    local metric (111320 cos(lat) m/deg E-W, 110574 m/deg N-S) the code uses."""
    x = -91.0 + cell_deg * np.arange(n)
    y = lat0 + cell_deg * (n - 1) - cell_deg * np.arange(n)  # descending
    X, Y = np.meshgrid(x, y)
    e = (X - x[0]) * 111320.0 * np.cos(np.radians(Y))
    nn = (Y - y[-1]) * 110574.0
    t = np.tan(np.radians(slope_deg))
    z = t * (e * np.sin(np.radians(dip_az)) + nn * np.cos(np.radians(dip_az)))
    return nr.Grid(x, y, z.astype(np.float64).astype(np.float32), 4326, "synthetic-plane")


@pytest.mark.parametrize("lat0", [1.0, 60.0])
def test_slope_of_exact_plane(lat0):
    g = _tilted_plane(lat0)
    s = nr.slope_grid(g).z[2:-2, 2:-2]
    assert abs(float(np.median(s)) - 10.0) < 0.05 and float(np.abs(s - 10.0).max()) < 0.3, (np.median(s), np.abs(s - 10).max())


def test_slope_check_fails_without_cos_lat():
    """Prove the plane test can fail: a version that forgets cos(lat) is far off at 60 N."""
    g = _tilted_plane(60.0)
    gy, gx = np.gradient(g.z.astype(np.float64))
    naive = np.degrees(np.arctan(np.hypot(gx / (abs(g.dx) * 111320.0), gy / (abs(g.dy) * 110574.0))))
    assert abs(float(np.median(naive[2:-2, 2:-2])) - 10.0) > 1.0


def test_slope_mercator_ground_scale():
    """EPSG:3395 cells are dx Mercator metres = dx cos(lat) ground metres; a 10 deg plane in
    ground metres must come back as 10 deg."""
    lat = 2.0
    k = np.cos(np.radians(lat))
    n, d = 100, 100.0
    x = -1.0e7 + d * np.arange(n)
    y0 = 6378137.0 * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
    y = y0 + d * (n - 1) - d * np.arange(n)
    X, Y = np.meshgrid(x, y)
    z = (np.tan(np.radians(10.0)) * (X - x[0]) * k).astype(np.float32)  # E-W tilt, ground metres
    s = nr.slope_grid(nr.Grid(x, y, z, 3395, "synthetic-merc")).z[2:-2, 2:-2]
    assert abs(float(np.median(s)) - 10.0) < 0.05, float(np.median(s))


def test_slope_plane_independent_of_baseline():
    g = _tilted_plane(1.0)
    for base in (None, 200.0, 400.0):
        s = nr.slope_grid(g, base).z
        assert abs(float(np.nanmedian(s)) - 10.0) < 0.05, (base, np.nanmedian(s))


@pytest.mark.parametrize("k", [1, 2, 4])
def test_slope_baseline_matches_central_difference_transfer_function(k):
    """z = A sin(2 pi e / lam) (E-W only, metric e). A central difference over +-k cells returns
    dz/de * sinc factor  sin(2 pi k d / lam) / (2 pi k d / lam). Compare the max tan(slope)."""
    lat0, n, cell_deg = 1.0, 400, 0.0005
    x = -91.0 + cell_deg * np.arange(n)
    y = lat0 + cell_deg * 9 - cell_deg * np.arange(10)
    X, Y = np.meshgrid(x, y)
    d = cell_deg * 111320.0 * np.cos(np.radians(lat0))  # cell width (m) at lat0 (rows differ by <1e-5)
    e = (X - x[0]) * 111320.0 * np.cos(np.radians(Y))
    A, lam = 50.0, 20 * d
    z = (A * np.sin(2 * np.pi * e / lam)).astype(np.float64)
    g = nr.Grid(x, y, z.astype(np.float32), 4326, "synthetic-sine")
    s = nr.slope_grid(g, baseline_m=2 * k * d).z
    assert nr.slope_half_cells(g, 2 * k * d) == k
    got = float(np.nanmax(np.tan(np.radians(s[3:-3]))))
    w = 2 * np.pi * k * d / lam
    want = A * 2 * np.pi / lam * np.sin(w) / w
    assert abs(got - want) / want < 0.01, (k, got, want)


# ---------------------------------------------------------------- 2026-10-04 review regressions
def test_drft_bathymetry_is_elevation_and_matches_gsc():
    """DRFT04RR grids store positive depth; after load_ds they must agree in SIGN with GSC."""
    for ds, box in (("DRFT04RR_bathymetry_100m", (-91.2, -90.9, -0.2, 0.1)),
                    ("DRFT04RR_86W_bathymetry_10m", (-86.1, -86.0, 0.75, 0.85))):
        g = nr.clip(ds, box)
        ref = nr.clip("GSC_97-86W_100m", box)
        assert np.nanmedian(g.z) < 0, ds
        c, _ = _corr_on_common(g, ref)
        assert c > 0.9, (ds, c)


def test_bathy_sign_guard_raises_on_positive_depth():
    g = nr.load(nr.source_files("DRFT04RR_bathymetry_100m")[0], 4326)
    assert np.nanmedian(g.z) > 0  # raw file is depth
    spec = dict(nr.DATASETS["DRFT04RR_bathymetry_100m"])
    nr.DATASETS["_tmp_wrong"] = dict(spec, positive_down=False)
    try:
        with pytest.raises(ValueError):
            nr.load_ds("_tmp_wrong", nr.source_files("DRFT04RR_bathymetry_100m")[0])
    finally:
        del nr.DATASETS["_tmp_wrong"]


def test_at5009_inf_nodata_removed_and_no_fake_cliffs(tmp_path):
    box = (-90.85, -90.60, 0.20, 0.50)
    g = nr.load(nr.REPO / nr.B / "AT50-09BC_GalapagosPlatform_Bathymetry/AT5009_MB_PintaRift_WGS84_20m.grd", 4326,
                nr.lonlat_bbox_to_crs(box, 4326))
    assert not np.isinf(g.z).any() and np.isnan(g.z).any()
    s = nr.slope_grid(g).z
    assert np.nanmax(s) < 89.0 and np.nanmean(s > 60) < 1e-3
    f = nr.write_elev_tif(g, tmp_path / "a.tif", overviews=False)
    with rasterio.open(f) as d:
        assert not np.isinf(d.read(1)).any()


def test_clip_prefers_grid_covering_the_requested_box():
    """A small fine tile overlapping a corner must not win over a grid covering the whole box."""
    box = (-90.0, -89.6, -0.65, -0.40)  # CoralCroissant 15 m covers 17 % of this box, the 50 m grids ~36 %
    g = nr.clip("AT5009_multibeam", box)
    assert "CoralCroissant" not in g.source and g.meta["box_coverage"] > 0.3, (g.source, g.meta)


def test_hillshade_lit_from_northwest():
    """Synthetic cone: the NW flank must be the brightest, SE the darkest."""
    n = 201
    x = -91.0 + 0.0005 * np.arange(n)
    y = 1.0 + 0.0005 * (n - 1) - 0.0005 * np.arange(n)
    X, Y = np.meshgrid(x, y)
    e = (X - x[n // 2]) * 111320.0
    nn = (Y - y[n // 2]) * 110574.0
    z = (-2000 + 800 * np.exp(-(e ** 2 + nn ** 2) / (2 * 2000.0 ** 2))).astype(np.float32)
    hs = nr.hillshade(nr.Grid(x, y, z, 4326, "cone"))
    c, o = n // 2, 40
    nw, ne, sw, se = hs[c - o, c - o], hs[c - o, c + o], hs[c + o, c - o], hs[c + o, c + o]
    assert nw > max(ne, sw) > se, (nw, ne, sw, se)


def test_regular_rejects_missing_column():
    v = np.arange(100, dtype=float) * 0.0005
    v = np.delete(v, 50)
    with pytest.raises(ValueError):
        nr._regular(v, "x", "synthetic")


def test_slope_nan_at_nodata_inside_hole():
    g = _tilted_plane(1.0)
    g.z[60, 60] = np.nan
    s = nr.slope_grid(g).z
    assert np.isnan(s[60, 60])


def test_nav_elevation_tif_is_simple(tmp_path):
    g = nr.clip("Mittelstaedt_50m", (-91.25, -91.20, 0.65, 0.70))
    f = nr.write_elev_tif(g, tmp_path / "e.tif", nav=True)
    assert (tmp_path / "e.tfw").exists() and (tmp_path / "e.prj").exists()
    with rasterio.open(f) as d:
        assert d.nodata == nr.NAV_NODATA and d.profile.get("tiled") is False
        assert d.profile.get("compress", "").lower() == "lzw" and np.isfinite(d.read(1)).all()
