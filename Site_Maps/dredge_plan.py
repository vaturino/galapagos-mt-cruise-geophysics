"""
Dredge-line planning: one planned on-bottom dredge track per permit site.

MT_dredging_coords/DredgeLines.csv holds one row per site:
    site, start_lat, start_lon, end_lat, end_lon, length_m, azimuth_deg,
    start_depth_m, end_depth_m, site_depth_m, mean_slope_deg, max_slope_deg, grid, grid_cell_m,
    source, note
source = "auto" (seeded here) or "manual" (drawn/edited in the viewer, or typed in). Depths are
positive down, from the finest bathymetry grid that covers the site (`grid`); azimuth is the
geodesic bearing start -> end, clockwise from true north.

Seeding rule (first guess only; review every line):
  - the line is LENGTH_M long and centred on the site, running from deep (start) to shallow
    (end), i.e. the usual "tow upslope" track;
  - its direction is the azimuth (5 deg steps) maximising climb - 2 x overshoot, where overshoot
    is how far the end lies below the shallowest point on the line (so a line that crests the
    edifice and runs down the far side loses). The local upslope direction (LSQ plane within
    max(250 m, 2.5 cells)) breaks ties; the note says when the search departs from it by >20 deg;
  - if even the best line climbs at under FLAT_DEG the row is flagged FLAT.

    source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env
    cd Site_Maps
    python dredge_plan.py seed            # add auto lines for sites that have none
    python dredge_plan.py seed --reseed   # also regenerate existing AUTO lines (manual ones are kept)
    python dredge_plan.py show            # print the table
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Geod

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "scripts"))
import native_render as nr  # noqa: E402

SITES = REPO / "MT_dredging_coords" / "DredgeSites.csv"
LINES = REPO / "MT_dredging_coords" / "DredgeLines.csv"
GEOD = Geod(ellps="WGS84")
LENGTH_M = 1000.0
FLAT_DEG = 3.0
COLUMNS = ["site", "start_lat", "start_lon", "end_lat", "end_lon", "length_m", "azimuth_deg", "start_depth_m",
           "end_depth_m", "site_depth_m", "mean_slope_deg", "max_slope_deg", "grid", "grid_cell_m", "source", "note"]
# bathymetry datasets, any order: best_grid() picks the finest one with data at the point
BATHY = ["AT5009_multibeam", "TN188_8m", "DRFT04RR_86W_bathymetry_10m", "Mittelstaedt_50m", "MV1007_bathymetry",
         "GSC_97-86W_100m", "DRFT04RR_bathymetry_100m", "DOA_ETP_MBES", "GMRT_corridor"]


def box_around(lat, lon, half_km):
    dlat = half_km / 110.574
    dlon = half_km / (111.320 * math.cos(math.radians(lat)))
    return lon - dlon, lon + dlon, lat - dlat, lat + dlat


def sample(g, lon, lat):
    """Bilinear sample of Grid g at lon/lat arrays (NaN outside / next to no-data)."""
    lon, lat = np.atleast_1d(np.asarray(lon, float)), np.atleast_1d(np.asarray(lat, float))
    if g.geographic:
        X, Y = lon, lat
    else:
        from pyproj import Transformer
        X, Y = Transformer.from_crs(4326, g.epsg, always_xy=True).transform(lon, lat)
    fx = (np.asarray(X) - g.x[0]) / g.dx
    fy = (g.y[0] - np.asarray(Y)) / g.dy
    i0, j0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
    out = np.full(fx.shape, np.nan)
    ok = (i0 >= 0) & (j0 >= 0) & (i0 + 1 < g.z.shape[1]) & (j0 + 1 < g.z.shape[0])
    tx, ty = fx[ok] - i0[ok], fy[ok] - j0[ok]
    a, b = g.z[j0[ok], i0[ok]], g.z[j0[ok], i0[ok] + 1]
    c, d = g.z[j0[ok] + 1, i0[ok]], g.z[j0[ok] + 1, i0[ok] + 1]
    out[ok] = (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty
    return out


def best_grid(lat, lon, half_km=2.5, require=None):
    """Finest bathymetry Grid with data at (lat, lon) and >= 50 % coverage of the box.
    `require` = list of extra (lat, lon) points that must also have data (e.g. line ends)."""
    pts = [(lat, lon)] + list(require or [])
    best = None
    for ds in BATHY:
        g = nr.clip(ds, box_around(lat, lon, half_km), pad_cells=2)
        if g is None or np.isfinite(g.z).mean() < 0.5:
            continue
        if not np.isfinite(sample(g, [p[1] for p in pts], [p[0] for p in pts])).all():
            continue
        cell = g.cell_size_m()[0]
        if best is None or cell < best[1] - 1e-6:
            best = (ds, cell, g)
    return best  # (dataset id, cell m, Grid) or None


def plane_fit(g, lat, lon, radius_m):
    """Upslope azimuth (deg from N) and slope (deg) of the LSQ plane within radius_m."""
    lo0, lo1, la0, la1 = box_around(lat, lon, radius_m / 1000.0)
    if not g.geographic:
        from pyproj import Transformer
        tr = Transformer.from_crs(g.epsg, 4326, always_xy=True)
    jj = np.where((g.y >= min(la0, la1) - abs(g.dy)) & (g.y <= max(la0, la1) + abs(g.dy)))[0] if g.geographic \
        else np.arange(g.z.shape[0])
    ii = np.where((g.x >= lo0 - abs(g.dx)) & (g.x <= lo1 + abs(g.dx)))[0] if g.geographic else np.arange(g.z.shape[1])
    X, Y = np.meshgrid(g.x[ii], g.y[jj])
    Z = g.z[np.ix_(jj, ii)]
    if not g.geographic:
        X, Y = tr.transform(X, Y)
    e = (X - lon) * 111320.0 * math.cos(math.radians(lat))
    n = (Y - lat) * 110574.0
    m = np.isfinite(Z) & (np.hypot(e, n) <= radius_m)
    if m.sum() < 6:
        return float("nan"), float("nan")
    A = np.c_[e[m], n[m], np.ones(m.sum())]
    (ge, gn, _), *_ = np.linalg.lstsq(A, Z[m].astype(float), rcond=None)
    az = (math.degrees(math.atan2(ge, gn)) + 360.0) % 360.0  # direction of increasing elevation
    return az, math.degrees(math.atan(math.hypot(ge, gn)))


def line_stats(lat0, lon0, lat1, lon1, g, n=101):
    """Geodesic profile start->end on Grid g: distances (m), depths (m, +down), and summary."""
    az, _, length = GEOD.inv(lon0, lat0, lon1, lat1)
    pts = GEOD.npts(lon0, lat0, lon1, lat1, n - 2)
    lons = np.r_[lon0, [p[0] for p in pts], lon1]
    lats = np.r_[lat0, [p[1] for p in pts], lat1]
    dist = np.linspace(0, length, n)
    depth = -sample(g, lons, lats)
    slope = np.degrees(np.arctan(np.abs(np.diff(depth)) / np.diff(dist)))
    return dict(lons=lons, lats=lats, dist=dist, depth=depth, azimuth=(az + 360) % 360, length=length,
                mean_slope=float(np.degrees(np.arctan(abs(depth[-1] - depth[0]) / length))) if length else np.nan,
                max_slope=float(np.nanmax(slope)) if np.isfinite(slope).any() else np.nan)


def best_azimuth(g, lat, lon, length_m, az_plane, step_deg=5.0):
    """Tow direction for a line of length_m centred on the site. Scores every azimuth by
        climb - 2 * overshoot
    where climb = start depth - end depth and overshoot = end depth - shallowest depth along the
    line (a line that crests the edifice and runs down the far side is penalised). The local
    plane-fit direction wins ties, so on a clean slope the answer is just 'straight upslope'."""
    best = None
    for az in np.arange(0, 360, step_deg):
        lo0, la0, _ = GEOD.fwd(lon, lat, (az + 180) % 360, length_m / 2)
        lo1, la1, _ = GEOD.fwd(lon, lat, az, length_m / 2)
        st = line_stats(la0, lo0, la1, lo1, g, n=41)
        dep = st["depth"]
        if not np.isfinite(dep).all():
            continue
        climb = dep[0] - dep[-1]
        overshoot = dep[-1] - dep.min()
        near_plane = abs(((az - az_plane + 180) % 360) - 180) if np.isfinite(az_plane) else 180
        score = climb - 2 * overshoot - 1e-3 * near_plane
        if best is None or score > best[0]:
            best = (score, az, climb, overshoot)
    if best is None:
        return az_plane, ""
    _, az, climb, overshoot = best
    dev = abs(((az - az_plane + 180) % 360) - 180) if np.isfinite(az_plane) else 0
    how = f"direction from azimuth search, {dev:.0f} deg off local upslope" if dev > 20 else ""
    return float(az), how


def seed_line(site, lat, lon, depth_listed, length_m=LENGTH_M):
    bg = best_grid(lat, lon)
    if bg is None:
        return dict(site=site, source="auto", note="no bathymetry grid covers this site")
    ds, cell, g = bg
    az_up, slope = plane_fit(g, lat, lon, max(250.0, 2.5 * cell))
    note = []
    if not np.isfinite(az_up):
        return dict(site=site, grid=ds, grid_cell_m=round(cell, 1), source="auto", note="plane fit failed")
    az, how = best_azimuth(g, lat, lon, length_m, az_up)
    lon0, lat0, _ = GEOD.fwd(lon, lat, (az + 180) % 360, length_m / 2)  # deep end
    lon1, lat1, _ = GEOD.fwd(lon, lat, az, length_m / 2)  # shallow end
    # use a grid that covers both ends (finest such)
    bg2 = best_grid(lat, lon, require=[(lat0, lon0), (lat1, lon1)])
    if bg2 is not None:
        ds, cell, g = bg2
    st = line_stats(lat0, lon0, lat1, lon1, g)
    if st["mean_slope"] < FLAT_DEG:
        note.append(f"FLAT: best line climbs only {st['depth'][0] - st['depth'][-1]:.0f} m over {st['length']:.0f} m "
                    f"({st['mean_slope']:.1f} deg); direction is weakly constrained, choose by hand")
    if how:
        note.append(how)
    sd = float(-sample(g, lon, lat)[0])
    if np.isfinite(sd) and abs(sd - depth_listed) > 150:
        note.append(f"grid depth at site {sd:.0f} m vs listed {depth_listed:.0f} m")
    return dict(site=site, start_lat=round(lat0, 6), start_lon=round(lon0, 6), end_lat=round(lat1, 6),
                end_lon=round(lon1, 6), length_m=round(st["length"], 1), azimuth_deg=round(st["azimuth"], 1),
                start_depth_m=round(float(st["depth"][0]), 0), end_depth_m=round(float(st["depth"][-1]), 0),
                site_depth_m=round(sd, 0), mean_slope_deg=round(st["mean_slope"], 1),
                max_slope_deg=round(st["max_slope"], 1), grid=ds, grid_cell_m=round(cell, 1), source="auto",
                note="; ".join(note))


def recompute(row):
    """Refresh lengths/azimuth/depths for a (possibly hand-edited) row from its endpoints."""
    lat0, lon0, lat1, lon1 = row.start_lat, row.start_lon, row.end_lat, row.end_lon
    mid_lon, mid_lat, _ = GEOD.fwd(lon0, lat0, GEOD.inv(lon0, lat0, lon1, lat1)[0],
                                   GEOD.inv(lon0, lat0, lon1, lat1)[2] / 2)
    bg = best_grid(mid_lat, mid_lon, require=[(lat0, lon0), (lat1, lon1)])
    if bg is None:
        return row
    ds, cell, g = bg
    st = line_stats(lat0, lon0, lat1, lon1, g)
    row = row.copy()
    row["length_m"], row["azimuth_deg"] = round(st["length"], 1), round(st["azimuth"], 1)
    row["start_depth_m"], row["end_depth_m"] = round(float(st["depth"][0]), 0), round(float(st["depth"][-1]), 0)
    row["mean_slope_deg"], row["max_slope_deg"] = round(st["mean_slope"], 1), round(st["max_slope"], 1)
    row["grid"], row["grid_cell_m"] = ds, round(cell, 1)
    if "until saved" in str(row.get("note", "")):
        row["note"] = "drawn in viewer"
    return row


def load_lines():
    if LINES.exists():
        df = pd.read_csv(LINES)
        for c in COLUMNS:
            if c not in df:
                df[c] = np.nan
        return df[COLUMNS]
    return pd.DataFrame(columns=COLUMNS)


def seed(reseed=False, length_m=LENGTH_M):
    sites = pd.read_csv(SITES)
    old = load_lines()
    keep = old[old.source.astype(str).str.lower() == "manual"] if reseed else old
    rows = [r for _, r in keep.iterrows()]
    have = set(keep.site.astype(int)) if len(keep) else set()
    for s in sites.itertuples():
        if int(s.site) in have:
            continue
        rows.append(pd.Series(seed_line(int(s.site), s.latitude, s.longitude, float(s.depth), length_m)))
        r = rows[-1]
        print(f"D{int(s.site):<3d} {r.get('grid', '-'):28s} az {r.get('azimuth_deg', float('nan')):6.1f}  "
              f"{r.get('start_depth_m', float('nan')):5.0f} -> {r.get('end_depth_m', float('nan')):5.0f} m  "
              f"{r.get('note', '')}", flush=True)
    df = pd.DataFrame(rows)[COLUMNS].sort_values("site").reset_index(drop=True)
    df["site"] = df.site.astype(int)
    df.to_csv(LINES, index=False)
    print(f"wrote {LINES} ({len(df)} lines)")
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["seed", "show", "refresh"])
    ap.add_argument("--reseed", action="store_true", help="regenerate existing auto lines too")
    ap.add_argument("--length", type=float, default=LENGTH_M, help="seeded line length, m (default 1000)")
    a = ap.parse_args()
    if a.cmd == "seed":
        seed(a.reseed, a.length)
    elif a.cmd == "refresh":  # recompute derived columns of every row from its endpoints
        df = load_lines()
        df = pd.DataFrame([recompute(r) if np.isfinite(r.start_lat) else r for _, r in df.iterrows()])
        df.to_csv(LINES, index=False)
        print(df.to_string(index=False))
    else:
        print(load_lines().to_string(index=False))
