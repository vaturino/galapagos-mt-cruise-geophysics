"""
One self-contained data packet per planned dredge (AT53-04), for the bridge, the science party
and the ship's navigation system. Reads MT_dredging_coords/DredgeSites.csv (the permit sites)
and DredgeLines.csv (planned tows; seed/edit with dredge_plan.py or the 3D viewer).

Output: Dredge_Packets/ (in the repo; GeoTIFFs are git-ignored) containing
  README.md                       what every file is, conventions, how to regenerate
  Dredge_Plan_Sheets.pdf          overview page + summary table + one planning sheet per site
  All_Dredge_Lines.gpx / .kml     every tow (route S -> E) and every site/start/end waypoint
  All_Dredge_Lines.csv            the line table with DD / DDM / DMS coordinates
  All_Dredge_Waypoints.csv        one row per waypoint (D07S, D07, D07E ...), nav-import friendly
  D07/ (one folder per site)
    D07_sheet.pdf / .png          map (native-resolution clip of the finest grid, hillshade, contours),
                                  tow line, previous dredges, along- and cross-line depth profiles,
                                  coordinate table, flags
    D07_waypoints.csv / .gpx / .kml
    D07_profile.csv               depth every ~10 m along the tow (and 500 m beyond each end)
    D07_<grid>_elev_native.tif    float32 depth grid clip (6 x 6 km), exactly the source cells
    D07_<grid>_color_native.tif   RGB nav-safe picture of the same (+ .tfw / .prj)
    D07_<backscatter>_*.tif       backscatter clip where a survey covers the site

    source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env
    cd Site_Maps && python make_dredge_packets.py [--sites 7 25 26] [--out ../Dredge_Packets]
"""
import argparse
import datetime as dt
import json
import math
import shutil
import textwrap
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402
from shapely.geometry import Point  # noqa: E402

import dredge_plan as dp  # noqa: E402
from make_site_maps import ddm, dms, reserve_zone  # noqa: E402

nr = dp.nr
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HALF_KM = 3.0          # GeoTIFF clip half-width
MAP_HALF_KM = 2.2      # sheet map half-width
EXTEND_M = 500.0       # profile shown this far beyond each end of the tow
BACKSCATTER = ["MV1007_backscatter", "DRFT04RR_sidescan", "MGL1106_sidescan_5m"]
GLASS_C = {"glass": "#1a9850", "no glass": "#d9d9d9", "no rock": "k"}
WHITE = [pe.withStroke(linewidth=2.6, foreground="w")]
# run-time options (set from the command line in main())
OPTS = dict(slope_baseline=None, bathy_cmap=None, slope_cmap=None)


def fmt3(lat, lon):
    return (f"{lat:.5f}, {lon:.5f}", f"{ddm(lat, 'N', 'S')}  {ddm(lon, 'E', 'W')}",
            f"{dms(lat, 'N', 'S')}  {dms(lon, 'E', 'W')}")


def load_inputs():
    sites = pd.read_csv(dp.SITES)
    lines = dp.load_lines()
    prev = pd.read_csv(HERE / "Previous_Dredges_Compiled.csv")
    mt = pd.read_csv(REPO / "MT_dredging_coords/MTsites.csv")
    rep = pd.read_csv(HERE / "Repeat_Dredge_Sites.csv") if (HERE / "Repeat_Dredge_Sites.csv").exists() else None
    rmg, rmh = gpd.read_file(HERE / "reserves/RMG.shp"), gpd.read_file(HERE / "reserves/RMH.shp")
    return sites, lines, prev, mt, rep, rmg, rmh


def nearest_prev(prev, lat, lon, n=3):
    d = dp.GEOD.inv(np.full(len(prev), lon), np.full(len(prev), lat), prev.lon.values, prev.lat.values)[2] / 1000
    p = prev.assign(dist_km=d).nsmallest(n, "dist_km")
    return p


def profile_m(lat0, lon0, lat1, lon1, g, step=10.0, extend=EXTEND_M):
    """Depths along the geodesic, from `extend` m before start to `extend` m past the end."""
    az, baz, L = dp.GEOD.inv(lon0, lat0, lon1, lat1)
    s = np.arange(-extend, L + extend + step / 2, step)
    lo, la = np.empty_like(s), np.empty_like(s)
    for k, d in enumerate(s):
        if d >= 0:
            lo[k], la[k], _ = dp.GEOD.fwd(lon0, lat0, az, d)
        else:
            lo[k], la[k], _ = dp.GEOD.fwd(lon0, lat0, (az + 180) % 360, -d)
    return s, la, lo, -dp.sample(g, lo, la), L, (az + 360) % 360


def cross_profile(lat, lon, az, g, half=1000.0, step=10.0):
    s = np.arange(-half, half + step / 2, step)
    lo, la = np.empty_like(s), np.empty_like(s)
    for k, d in enumerate(s):
        b = (az + 90) % 360 if d >= 0 else (az + 270) % 360
        lo[k], la[k], _ = dp.GEOD.fwd(lon, lat, b, abs(d))
    return s, -dp.sample(g, lo, la)


# ----------------------------------------------------------------------------- exports
def write_gpx(path, wpts, routes):
    x = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<gpx version="1.1" creator="AT53-04 make_dredge_packets.py" xmlns="http://www.topografix.com/GPX/1/1">']
    for w in wpts:
        x.append(f'<wpt lat="{w["lat"]:.7f}" lon="{w["lon"]:.7f}"><name>{w["name"]}</name>'
                 f'<desc>{w["desc"]}</desc></wpt>')
    for name, desc, pts in routes:
        x.append(f"<rte><name>{name}</name><desc>{desc}</desc>" + "".join(
            f'<rtept lat="{p["lat"]:.7f}" lon="{p["lon"]:.7f}"><name>{p["name"]}</name></rtept>' for p in pts) + "</rte>")
    x.append("</gpx>")
    path.write_text("\n".join(x) + "\n")


def write_kml(path, title, wpts, routes):
    x = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
         f"<name>{title}</name>",
         '<Style id="tow"><LineStyle><color>ff1a8cff</color><width>4</width></LineStyle></Style>',
         '<Style id="S"><IconStyle><color>ff4fd12b</color></IconStyle></Style>',
         '<Style id="E"><IconStyle><color>ff3b3bff</color></IconStyle></Style>',
         '<Style id="site"><IconStyle><color>ff1f30d7</color></IconStyle></Style>']
    for name, desc, pts in routes:
        c = " ".join(f'{p["lon"]:.7f},{p["lat"]:.7f},0' for p in pts)
        x.append(f"<Placemark><name>{name}</name><description>{desc}</description><styleUrl>#tow</styleUrl>"
                 f"<LineString><coordinates>{c}</coordinates></LineString></Placemark>")
    for w in wpts:
        x.append(f'<Placemark><name>{w["name"]}</name><description>{w["desc"]}</description>'
                 f'<styleUrl>#{w["style"]}</styleUrl><Point><coordinates>{w["lon"]:.7f},{w["lat"]:.7f},0</coordinates>'
                 f"</Point></Placemark>")
    x.append("</Document></kml>")
    path.write_text("\n".join(x) + "\n")


def waypoints_for(sid, s, ln):
    out = []
    for key, lat, lon, depth, what, style in (
            ("S", ln.start_lat, ln.start_lon, ln.start_depth_m, "start of tow", "S"),
            ("", s.latitude, s.longitude, s.depth, "permit site", "site"),
            ("E", ln.end_lat, ln.end_lon, ln.end_depth_m, "end of tow", "E")):
        if not (np.isfinite(lat) and np.isfinite(lon)):
            continue
        dd, dm_, ds = fmt3(lat, lon)
        out.append(dict(name=f"{sid}{key}", lat=float(lat), lon=float(lon), lat_ddm=ddm(lat, "N", "S"),
                        lon_ddm=ddm(lon, "E", "W"), lat_dms=dms(lat, "N", "S"), lon_dms=dms(lon, "E", "W"),
                        depth_m=int(round(depth)) if np.isfinite(depth) else "", style=style,
                        desc=f"{sid} {what}, {int(round(depth)) if np.isfinite(depth) else '?'} m"))
    return out


# ----------------------------------------------------------------------------- sheet
def ddm_formatter(pos, neg):
    def f(v, _):
        h = pos if v >= 0 else neg
        a = abs(v)
        d = int(a)
        return f"{d}°{(a - d) * 60:05.2f}'{h}"
    return f


def _display(grid, kind):
    """Grid -> (rgb, extent, X, Y, Z, vrange) in lon/lat for plotting. Geographic grids are shown cell
    for cell; projected ones (DOA-ETP Mercator) are reprojected for display only."""
    gd = grid if grid.geographic else nr.to_wgs84(grid, kind)
    rgb, _, vr = nr.colorize(gd, kind, cmap=OPTS[f"{kind}_cmap"])
    ext = [gd.x[0] - gd.dx / 2, gd.x[-1] + gd.dx / 2, gd.y[-1] - gd.dy / 2, gd.y[0] + gd.dy / 2]
    X, Y = np.meshgrid(gd.x, gd.y)
    return rgb, ext, X, Y, gd.z, vr


def map_panel(ax, sid, s, ln, rgb, ext, X, Y, Zbathy, prev, mt, sites):
    """Raster + bathymetry contours + rings + previous dredges + MT + permit sites + tow, on ax.
    Returns (contour step, bold step, n levels)."""
    lat, lon = float(s.latitude), float(s.longitude)
    lo0, lo1, la0, la1 = dp.box_around(lat, lon, MAP_HALF_KM)
    ax.imshow(rgb, extent=ext, interpolation="nearest", zorder=0)
    Z = Zbathy
    relief = np.nanmax(Z) - np.nanmin(Z) if np.isfinite(Z).any() else 0
    step = 25 if relief < 300 else 50 if relief < 800 else 100
    mstep = {25: 100, 50: 250, 100: 500}[step]
    lv = np.arange(np.floor(np.nanmin(Z) / step) * step, np.nanmax(Z) + step, step) if relief else []
    if len(lv) > 1:
        cs = ax.contour(X, Y, Z, levels=lv, colors="k", linewidths=0.35, alpha=0.55, zorder=1)
        mstep = {25: 100, 50: 250, 100: 500}[step]
        major = [v for v in lv if v % mstep == 0] or lv[::4]
        ax.contour(X, Y, Z, levels=major, colors="k", linewidths=0.9, alpha=0.7, zorder=1)
        ax.clabel(cs, levels=major, fmt=lambda v: f"{-v:.0f}", fontsize=7, inline=True)
    ax.set_xlim(lo0, lo1)
    ax.set_ylim(la0, la1)
    ax.set_aspect(1 / math.cos(math.radians(lat)))
    ax.xaxis.set_major_formatter(ddm_formatter("E", "W"))
    ax.yaxis.set_major_formatter(ddm_formatter("N", "S"))
    ax.tick_params(labelsize=8)
    ax.grid(color="w", lw=0.4, alpha=0.5)
    # bearing ticks every 30 deg (true) on the 1 km ring, and the across-tow profile line
    for b_ in range(0, 360, 30):
        x_a, y_a, _ = dp.GEOD.fwd(lon, lat, b_, 920)
        x_b, y_b, _ = dp.GEOD.fwd(lon, lat, b_, 1080)
        x_t, y_t, _ = dp.GEOD.fwd(lon, lat, b_, 1230)
        ax.plot([x_a, x_b], [y_a, y_b], color="w", lw=1.4, zorder=2, path_effects=[pe.withStroke(linewidth=2.6, foreground="k")])
        ax.text(x_t, y_t, f"{b_:03d}°", color="w", fontsize=7.5, ha="center", va="center", zorder=2,
                fontweight="bold", path_effects=[pe.withStroke(linewidth=2.2, foreground="k")])
    if np.isfinite(ln.start_lat) and np.isfinite(ln.end_lat):
        az_ = (dp.GEOD.inv(ln.start_lon, ln.start_lat, ln.end_lon, ln.end_lat)[0] + 360) % 360
        ends = [dp.GEOD.fwd(lon, lat, (az_ + 270) % 360, 1000)[:2], dp.GEOD.fwd(lon, lat, (az_ + 90) % 360, 1000)[:2]]
        ax.plot([ends[0][0], ends[1][0]], [ends[0][1], ends[1][1]], color="#3b5998", lw=1.6, ls=(0, (6, 3)), zorder=3,
                path_effects=[pe.Stroke(linewidth=3, foreground="w"), pe.Normal()])
        for (ex, ey), bb in zip(ends, ((az_ + 270) % 360, (az_ + 90) % 360)):
            ax.annotate(f"across {bb:.0f}°", (ex, ey), xytext=(0, -11), textcoords="offset points", ha="center",
                        fontsize=7.5, color="#1d3461", zorder=6, path_effects=WHITE)
    # range rings
    for r_m, lab in ((500, "500 m"), (1000, "1 km")):
        b = np.linspace(0, 360, 181)
        rl = [dp.GEOD.fwd(lon, lat, a, r_m)[:2] for a in b]
        ax.plot([p[0] for p in rl], [p[1] for p in rl], color="w", lw=0.8, ls=(0, (4, 3)), zorder=2)
        ax.text(rl[45][0], rl[45][1], lab, color="w", fontsize=7, zorder=2, path_effects=[pe.withStroke(linewidth=2, foreground="k")])
    # previous dredges, MT, other permit sites
    pv = prev[prev.lon.between(lo0, lo1) & prev.lat.between(la0, la1)]
    for q in pv.itertuples():
        if np.isfinite(q.off_lat) and np.isfinite(q.off_lon):
            ax.plot([q.lon, q.off_lon], [q.lat, q.off_lat], color="k", lw=2.0, zorder=3,
                    path_effects=[pe.Stroke(linewidth=3.6, foreground="w"), pe.Normal()])
        ax.scatter([q.lon], [q.lat], s=110 if q.glass != "no rock" else 120, marker="X" if q.glass == "no rock" else "o",
                   c=GLASS_C[q.glass], edgecolors="k" if q.glass != "no rock" else "w", linewidths=1.1, zorder=4)
        ax.annotate(f"{'MV ' if q.cruise == 'MV1007' else ''}{q.station} {int(q.depth)} m\n{q.glass}", (q.lon, q.lat),
                    xytext=(7, -14), textcoords="offset points", fontsize=7.5, zorder=6, path_effects=WHITE)
    m = mt[mt.longitude.between(lo0, lo1) & mt.latitude.between(la0, la1)]
    ax.scatter(m.longitude, m.latitude, s=70, marker="D", c="#ffd400", edgecolors="k", zorder=4)
    for q in m.itertuples():
        ax.annotate(f"MT{q.site}", (q.longitude, q.latitude), xytext=(6, 4), textcoords="offset points", fontsize=7.5,
                    path_effects=WHITE, zorder=6)
    o = sites[sites.longitude.between(lo0, lo1) & sites.latitude.between(la0, la1) & (sites.site != s.site)]
    ax.scatter(o.longitude, o.latitude, s=120, marker="^", c="#d7301f", edgecolors="k", zorder=5)
    for q in o.itertuples():
        ax.annotate(f"D{q.site}", (q.longitude, q.latitude), xytext=(7, 4), textcoords="offset points", fontsize=8,
                    color="#67000d", fontweight="bold", path_effects=WHITE, zorder=6)
    # the tow
    has_line = np.isfinite(ln.start_lat) and np.isfinite(ln.end_lat)
    if has_line:
        ax.plot([ln.start_lon, ln.end_lon], [ln.start_lat, ln.end_lat], color="#ff2d6f", lw=3.2, zorder=7,
                solid_capstyle="round", path_effects=[pe.Stroke(linewidth=5.5, foreground="w"), pe.Normal()])
        mlo, mla = ln.start_lon + 0.72 * (ln.end_lon - ln.start_lon), ln.start_lat + 0.72 * (ln.end_lat - ln.start_lat)  # arrowhead past the site
        ax.annotate("", (mlo + 0.12 * (ln.end_lon - ln.start_lon), mla + 0.12 * (ln.end_lat - ln.start_lat)), (mlo, mla),
                    zorder=7, arrowprops=dict(arrowstyle="-|>,head_length=1.1,head_width=0.6", lw=0, color="#ff2d6f",
                                              shrinkA=0, shrinkB=0))
        for (la_, lo_, c, k) in ((ln.start_lat, ln.start_lon, "#2bd14f", "S"), (ln.end_lat, ln.end_lon, "#ff3b3b", "E")):
            ax.scatter([lo_], [la_], s=90, c=c, edgecolors="k", linewidths=1.2, zorder=8)
            ax.annotate(f"{sid}{k}", (lo_, la_), xytext=(8, 6), textcoords="offset points", fontsize=10,
                        fontweight="bold", path_effects=WHITE, zorder=9)
    ax.scatter([lon], [lat], s=330, marker="^", c="#d7301f", edgecolors="k", linewidths=1.6, zorder=8)
    ax.annotate(f"{sid} ({int(s.depth)} m)", (lon, lat), xytext=(10, -18), textcoords="offset points", fontsize=11,
                fontweight="bold", color="#67000d", path_effects=WHITE, zorder=9)
    # scale bar + north arrow
    x0, y0 = lo0 + 0.06 * (lo1 - lo0), la0 + 0.05 * (la1 - la0)
    x1 = dp.GEOD.fwd(x0, y0, 90, 1000)[0]
    ax.plot([x0, x1], [y0, y0], color="k", lw=4, zorder=9, path_effects=[pe.Stroke(linewidth=7, foreground="w"), pe.Normal()])
    ax.text((x0 + x1) / 2, y0 + 0.012 * (la1 - la0), "1 km", ha="center", fontsize=9, zorder=9, path_effects=WHITE)
    ax.annotate("N", (lo1 - 0.05 * (lo1 - lo0), la1 - 0.04 * (la1 - la0)),
                (lo1 - 0.05 * (lo1 - lo0), la1 - 0.13 * (la1 - la0)), ha="center", fontsize=12, fontweight="bold",
                arrowprops=dict(arrowstyle="-|>", lw=2, color="k"), zorder=9, path_effects=WHITE)
    return step, mstep, len(lv)


def draw_sheet(sid, s, ln, g, ds, cell, prev, mt, sites, rep_row, zone, out_png, out_slope_png, pdfs):
    lat, lon = float(s.latitude), float(s.longitude)
    fig = plt.figure(figsize=(16.54, 11.69))  # A3 landscape
    ax = fig.add_axes([0.04, 0.17, 0.53, 0.73])
    lo0, lo1, la0, la1 = dp.box_around(lat, lon, MAP_HALF_KM)
    sub = nr.clip(ds, (lo0, lo1, la0, la1), pad_cells=2) or g
    rgb, ext, X, Y, Z, vr = _display(sub, "bathy")
    step, mstep, nlv = map_panel(ax, sid, s, ln, rgb, ext, X, Y, Z, prev, mt, sites)
    has_line = np.isfinite(ln.start_lat) and np.isfinite(ln.end_lat)
    cax = fig.add_axes([0.08, 0.105, 0.45, 0.014])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=Normalize(*vr), cmap=nr._cmap("bathy", OPTS["bathy_cmap"])), cax=cax, orientation="horizontal",
                      extend="both")
    cb.set_label(f"elevation (m); contours every {step} m (bold every {mstep if nlv > 1 else '-'} m, labelled as depth)", fontsize=8)
    cb.ax.tick_params(labelsize=8)
    sg = nr.slope_grid(g, OPTS["slope_baseline"])  # slope on the grid's own cells, for the profiles
    base = sg.meta["slope_baseline_m"]  # central-difference width actually used (2k cells)

    # profiles
    flags = []
    if has_line:
        d, pla, plo, pz, L, az = profile_m(ln.start_lat, ln.start_lon, ln.end_lat, ln.end_lon, g)
        a1 = fig.add_axes([0.615, 0.725, 0.36, 0.175])
        a1.axvspan(d[0], 0, color="0.92")
        a1.axvspan(L, d[-1], color="0.92")
        a1.plot(d, pz, color="k", lw=1.3)
        a1.plot([0, L], [ln.start_depth_m, ln.end_depth_m], "o", color="none")
        a1.scatter([0], [np.interp(0, d, pz)], c="#2bd14f", edgecolors="k", zorder=5, s=60)
        a1.scatter([L], [np.interp(L, d, pz)], c="#ff3b3b", edgecolors="k", zorder=5, s=60)
        # where the site (triangle) projects onto the tow: distance from S along the line, and offset
        az_ss, _, d_ss = dp.GEOD.inv(ln.start_lon, ln.start_lat, lon, lat)
        ds_site = d_ss * math.cos(math.radians(az_ss - az))
        off_site = abs(d_ss * math.sin(math.radians(az_ss - az)))
        a1.scatter([ds_site], [np.interp(ds_site, d, pz)], marker="^", s=130, c="#d7301f", edgecolors="k", zorder=6)
        a1.annotate(f"{sid}" + (f" ({off_site:.0f} m off line)" if off_site > 25 else ""), (ds_site, np.interp(ds_site, d, pz)),
                    xytext=(0, -16), textcoords="offset points", ha="center", fontsize=8, color="#67000d", fontweight="bold")
        a1.invert_yaxis()
        a1.tick_params(labelbottom=False)
        a1.set_ylabel("depth (m)", fontsize=8)
        a1.set_title(f"Along-tow profile {sid}S → {sid}E: {L:.0f} m at {az:.1f}° T "
                     f"(climb {ln.start_depth_m - ln.end_depth_m:.0f} m; vertical exaggerated)", fontsize=9, loc="left")
        a1.tick_params(labelsize=8)
        a1.grid(alpha=0.3)
        # slope along the tow: terrain slope (steepest, from the slope grid, native cells) and the
        # signed gradient along the tow direction over one grid cell (+ = climbing towards E)
        terr = dp.sample(sg, plo, pla)
        h = base / 2  # same width as the terrain slope's central difference
        grad = np.degrees(np.arctan((np.interp(d - h, d, pz) - np.interp(d + h, d, pz)) / base))
        grad[(d - h < d[0]) | (d + h > d[-1])] = np.nan
        a1s = fig.add_axes([0.615, 0.585, 0.36, 0.13], sharex=a1)
        a1s.axvspan(d[0], 0, color="0.92")
        a1s.axvspan(L, d[-1], color="0.92")
        a1s.fill_between(d, 0, terr, color="#fdae61", alpha=0.75, lw=0, label=f"terrain slope: steepest direction, over {base:.0f} m")
        a1s.plot(d, grad, color="k", lw=1.0, label=f"slope along the tow direction, over {base:.0f} m (+ = climbing)")
        a1s.axhline(0, color="0.4", lw=0.6)
        for lvl in (20, 30):
            a1s.axhline(lvl, color="#b2182b", lw=0.6, ls=":")
        a1s.set_ylabel("slope (°)", fontsize=8)
        a1s.set_xlabel(f"distance along tow from {sid}S (m); grey = {EXTEND_M:.0f} m beyond each end", fontsize=8)
        a1s.tick_params(labelsize=8)
        a1s.grid(alpha=0.3)
        a1s.legend(fontsize=6.5, loc="upper left", ncol=2, framealpha=0.85)
        on = (d >= 0) & (d <= L)
        tmean, tmax = float(np.nanmean(terr[on])), float(np.nanmax(terr[on]))
        frac20 = float(np.nanmean(terr[on] > 20) * 100)
        cd, cz = cross_profile(lat, lon, az, g)
        a1.axvline(ds_site, color="#d7301f", lw=0.8, ls="--")
        a1s.axvline(ds_site, color="#d7301f", lw=0.8, ls="--")
        a2 = fig.add_axes([0.615, 0.425, 0.36, 0.11])
        a2.plot(cd, cz, color="#3b5998", lw=1.3)
        a2.axvline(0, color="#d7301f", lw=1, ls="--")
        for xx, lab, ha in ((cd[0], f"{(az + 270) % 360:.0f}\u00b0 T side", "left"), (cd[-1], f"{(az + 90) % 360:.0f}\u00b0 T side", "right")):
            a2.text(xx, 0.04, lab, transform=a2.get_xaxis_transform(), ha=ha, fontsize=7.5, color="0.3")
        a2.invert_yaxis()
        a2.set_xlabel(f"distance across tow through {sid} (m; + = {((az + 90) % 360):.0f}° T)", fontsize=8)
        a2.set_ylabel("depth (m)", fontsize=8)
        a2.set_title("Profile ACROSS the tow (perpendicular, through the site): ridge crest vs flank, room to drift",
                     fontsize=9, loc="left")
        a2.tick_params(labelsize=8)
        a2.grid(alpha=0.3)
        prof = pd.DataFrame(dict(distance_from_start_m=d.round(1), lat=pla.round(7), lon=plo.round(7),
                                 depth_m=np.round(pz, 1), terrain_slope_deg=np.round(terr, 1),
                                 along_tow_gradient_deg=np.round(grad, 1), on_tow=on))
    else:
        prof = None
        flags.append("NO TOW LINE PLANNED")
    # table
    if isinstance(ln.note, str) and ln.note:
        flags.append(ln.note)
    if rep_row is not None:
        flags.append(f"REPEAT SITE: {rep_row.dist_onbottom_km:.1f} km from MV1007 {rep_row.mv1007} "
                     f"({rep_row.mv_class}: {rep_row.mv_description})")
    gd = float(-dp.sample(g, lon, lat)[0])
    if np.isfinite(gd) and abs(gd - s.depth) > 150:
        flags.append(f"grid depth at site {gd:.0f} m vs permit {s.depth:.0f} m")
    rows = [("Site (permit)", lat, lon, s.depth)]
    if has_line:
        rows = [(f"{sid}S start", ln.start_lat, ln.start_lon, ln.start_depth_m), rows[0],
                (f"{sid}E end", ln.end_lat, ln.end_lon, ln.end_depth_m)]
    t = [f"{sid}  planned dredge  ({'hand-edited' if str(ln.source).lower() == 'manual' else 'auto first guess'} line)",
         "", f"{'':13s} {'decimal deg':20s} {'deg + dec. min':28s} {'deg min sec':30s} depth"]
    for name, la_, lo_, de in rows:
        a, b, c = fmt3(la_, lo_)
        t.append(f"{name:13s} {a:20s} {b:28s} {c:30s} {de:5.0f} m")
    t.append("")
    if has_line:
        t += [f"Tow: {L:.0f} m at {az:.1f}° T (reciprocal {(az + 180) % 360:.1f}°), "
              f"{ln.start_depth_m:.0f} → {ln.end_depth_m:.0f} m, "
              f"end-to-end gradient {ln.mean_slope_deg:.1f}°",
              f"Terrain slope on the tow (over {base:.0f} m): mean {tmean:.1f}°, max {tmax:.1f}°, "
              f"{frac20:.0f} % of the tow steeper than 20°"]
    t += [f"Grid: {ds}, {cell:.0f} m cells ({nr.DATASETS[ds]['label']})", f"Reserve zone: {zone}", "",
          "Nearest previous dredges:"]
    for q in nearest_prev(prev, lat, lon).itertuples():
        t.append(f"  {q.cruise} {q.station}: {q.dist_km:.1f} km, {int(q.depth)} m, {q.glass}"
                 + (f" ({q.description})" if isinstance(q.description, str) and q.description else ""))
    if flags:
        t += ["", "FLAGS:"] + ["  " + w for f_ in flags for w in textwrap.wrap(f_, 110)]
    t += ["", "WGS-84. Depths from the grid named above (planning only): confirm on the ship's own multibeam before deploying."]
    fig.text(0.585, 0.355, "\n".join(t), fontsize=6.9, family="monospace", va="top")
    fig.suptitle(f"AT53-04 dredge plan: {sid}  ({lat:.5f}, {lon:.5f}; permit depth {s.depth:.0f} m)", x=0.04, ha="left",
                 fontsize=15, fontweight="bold", y=0.965)
    shown = (f"native {cell:.0f} m cells of {ds} (no resampling)" if g.geographic else
             f"{ds} ({cell:.0f} m cells, EPSG:{g.epsg}) reprojected to lon/lat for this sheet only; the GeoTIFFs are native")
    fig.text(0.04, 0.938, textwrap.fill(f"Map: {shown}, hillshade from NW. "
             "Triangle = permit site; arrow = planned tow (green S → red E); circles = previous dredges "
             "(green glass, grey no glass, X no rock; black line = MV1007 tow); diamonds = MT sites; "
             "dashed rings 500 m / 1 km.", 190), fontsize=8.5, va="top")
    fig.text(0.99, 0.01, f"generated {dt.date.today().isoformat()} by Site_Maps/make_dredge_packets.py", ha="right",
             fontsize=7, color="0.4")
    fig.savefig(out_png, dpi=150)
    for p_ in pdfs:
        p_.savefig(fig)
    plt.close(fig)

    # ---- page 2: slope map at the grid's own cell size
    fig = plt.figure(figsize=(16.54, 11.69))
    ax = fig.add_axes([0.04, 0.08, 0.62, 0.84])
    ssub = nr.slope_grid(sub, OPTS["slope_baseline"])
    srgb, sext, _, _, _, svr = _display(ssub, "slope")
    map_panel(ax, sid, s, ln, srgb, sext, X, Y, Z, prev, mt, sites)
    cax = fig.add_axes([0.70, 0.55, 0.015, 0.33])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=Normalize(*svr), cmap=nr._cmap("slope", OPTS["slope_cmap"])), cax=cax, extend="max")
    cb.set_label("seafloor slope (°), steepest direction", fontsize=9)
    cb.ax.tick_params(labelsize=8)
    v = ssub.z[np.isfinite(ssub.z)]
    txt = [f"{sid} slope map", "",
           f"Slope of {ds} on its own {cell:.0f} m cells, in the",
           "steepest direction, from central differences over",
           f"{base:.0f} m ({base / cell:.0f} cells), true metric spacing.", "",
           f"Map area: median {np.median(v):.1f}°, 90th pct {np.percentile(v, 90):.1f}°, max {v.max():.1f}°"]
    if has_line:
        txt += [f"On the tow: mean {tmean:.1f}°, max {tmax:.1f}°,", f"{frac20:.0f} % steeper than 20°"]
    txt += ["", "A grid cell is an average: real slopes at the", "scale of a dredge (metres) are steeper and",
            f"rougher than a {cell:.0f} m grid can show.", "",
            "Contours: bathymetry (labelled as depth).", "Arrow: planned tow, green S → red E."]
    fig.text(0.70, 0.49, "\n".join(txt), fontsize=9, va="top")
    fig.suptitle(f"AT53-04 dredge plan: {sid} - seafloor slope", x=0.04, ha="left", fontsize=15, fontweight="bold", y=0.965)
    fig.savefig(out_slope_png, dpi=150)
    for p_ in pdfs:
        p_.savefig(fig)
    plt.close(fig)
    info = dict(base=base, step=step, mstep=mstep, has_line=has_line)
    if has_line:
        info.update(L=L, az=az, ds_site=ds_site, off_site=off_site, tmean=tmean, tmax=tmax, frac20=frac20)
    return prof, flags, info


SITE_README = """# {sid}: what the plots and files show

Sheet: `{tag}_sheet.pdf` (page 1 = plan, page 2 = slope map). The same pages as images: `{tag}_sheet.png`, `{tag}_slope_map.png`.

Parameters for this site:

| | |
|---|---|
| Bathymetry grid | `{ds}`: {label} |
| Grid cell size | {cell:.0f} m (the map shows these cells as they are, no resampling) |
| Slope baseline | {base:.0f} m = central difference over {k} cell(s) each side (`--slope-baseline`; default and finest = 2 cells) |
| Contours | every {step} m, bold every {mstep} m, labels = depth |
| Planned tow | {towline} |
| Reserve zone | {zone} |
| Line source | {source} |

## Page 1, map (left)
- **Colours:** the bathymetry grid cells, with hillshade lit from the NW. Colour scale below the map.
- **Red triangle:** the permit site ({sid}).
- **Pink line + arrow:** the planned tow, from **{sid}S** (green, start) to **{sid}E** (red, end).
- **Dashed blue line:** where the "across the tow" profile is taken: 1 km each side of the site, perpendicular to the tow. Its ends are labelled with their true bearings.
- **White dashed rings:** 500 m and 1 km from the site. The tick marks and labels on the 1 km ring are true bearings every 30 deg (000 = north).
- **Circles:** previous dredges (green = glass, grey = no glass, X = no rock). A black line is an MV1007 tow (on- to off-bottom).
- **Yellow diamonds:** MT sites. **Small triangles:** other permit dredge sites.

## Page 1, along-tow profile (top right)
- Depth sampled every 10 m along the planned tow (a geodesic) from {sid}S to {sid}E.
- Extended 500 m beyond each end (grey bands), to show what is just past the ends.
- The **triangle** marks where the site falls along the line{site_note}.
- The vertical scale is exaggerated.

## Page 1, slope along the tow (below the profile)
- **Orange fill, terrain slope:** the slope of the seafloor in its steepest direction at each point on the line, whatever that direction is. It comes from the grid by central differences over {base:.0f} m in E-W and N-S: slope = atan(sqrt(dz/dx^2 + dz/dy^2)).
- **Black line, slope along the tow:** only the component in the tow direction, over the same {base:.0f} m. Positive = climbing towards {sid}E.
- It is at most the terrain slope. Where the black line sits well below the orange, the tow cuts across the slope rather than straight up it.
- **Dotted lines:** 20 and 30 deg.
- Slopes from a {cell:.0f} m grid are averages. Real slopes at dredge scale (metres) are steeper and rougher.

## Page 1, profile across the tow (middle right)
- Depth along the dashed blue line: 2 km perpendicular to the tow, through the site.
- High in the middle = the tow runs along a ridge or crest. One-sided = the tow runs along a flank.
- Shows how much the depth changes if the dredge drifts sideways.

## Page 1, table (bottom right)
- **Positions** (WGS-84) of start, site and end in decimal degrees, degrees + decimal minutes, and DMS.
- **Depths:** the start/end depths come from the grid. The site depth is the permit value.
- **Tow:** length and bearing (geodesic, true), end-to-end gradient, terrain-slope statistics along the tow, grid used, reserve zone, the three nearest previous dredges.
- **FLAGS:**
  - `FLAT`: even the best direction climbs less than 3 deg.
  - `direction from azimuth search`: the line is not straight up the local slope. Seeded lines are the 1 km line through the site that climbs most without cresting and running down the far side.
  - `REPEAT SITE`: within 2 km of an MV1007 dredge that got no glass or no rock.
  - Grid-vs-permit depth differences over 150 m.

## Page 2, slope map
- The terrain slope (as above, over {base:.0f} m) for the whole map area, 0-40 deg (pale = flat, dark red = steep, darker than the top colour = steeper than 40 deg).
- Same tow, rings, bearings and symbols as page 1. Steep bands are scarps and fault or flow fronts.

## Files
- `{tag}_waypoints.csv / .gpx / .kml`: {sid}S, {sid} (site), {sid}E, and the route S -> E.
- `{tag}_profile.csv`: every 10 m along the tow: distance from S, lat, lon, depth, terrain slope, slope along the tow, `on_tow`.
- `{tag}_{ds}_elev_native.tif`: float32 elevation, the grid's own cells (no resampling), 6 x 6 km.
- `{tag}_{ds}_color_native.tif`: RGB nav-safe GeoTIFF (+ `.tfw` / `.prj`).
- `{tag}_{ds}_slope_deg_native.tif`, `_slope_color_native.tif`: slope (deg) on the same cells.
- `*_backscatter_*` / `*_sidescan_*`: backscatter clips, where a survey covers the site.
- `*_wgs84.tif`: lon/lat copies of grids that are natively projected (DOA-ETP is World Mercator).
- `{tag}_info.json`: everything above, machine-readable.

Regenerate: `cd Site_Maps && python make_dredge_packets.py --sites {site}`
(options: `--slope-baseline 200`, `--bathy-cmap cmo.topo`, `--slope-cmap magma_r`).
"""


def overview_page(pdf, sites, lines):
    fig = plt.figure(figsize=(16.54, 11.69))
    fig.suptitle("AT53-04 dredge plan: all sites", x=0.04, ha="left", fontsize=16, fontweight="bold", y=0.97)
    cols = ["site", "start (DDM)", "end (DDM)", "S→E depth m", "km", "az °T", "slope°", "grid", "line", "flags"]
    data = []
    for s in sites.itertuples():
        ln = lines[lines.site == s.site]
        if not len(ln):
            data.append([f"D{s.site}"] + ["-"] * 8 + ["no line"])
            continue
        ln = ln.iloc[0]
        data.append([f"D{s.site}", f"{ddm(ln.start_lat, 'N', 'S')} {ddm(ln.start_lon, 'E', 'W')}",
                     f"{ddm(ln.end_lat, 'N', 'S')} {ddm(ln.end_lon, 'E', 'W')}",
                     f"{ln.start_depth_m:.0f}→{ln.end_depth_m:.0f}", f"{ln.length_m / 1000:.2f}",
                     f"{ln.azimuth_deg:.0f}", f"{ln.mean_slope_deg:.1f}", ln.grid,
                     "hand" if str(ln.source).lower() == "manual" else "auto",
                     textwrap.shorten(str(ln.note) if isinstance(ln.note, str) else "", 60)])
    ax = fig.add_axes([0.03, 0.05, 0.94, 0.86])
    ax.axis("off")
    tb = ax.table(cellText=data, colLabels=cols, loc="upper left", cellLoc="left",
                  colWidths=[0.04, 0.17, 0.17, 0.08, 0.04, 0.05, 0.05, 0.12, 0.04, 0.24])
    tb.auto_set_font_size(False)
    tb.set_fontsize(7.5)
    tb.scale(1, 1.25)
    for (r, c), cell in tb.get_celld().items():
        if r == 0:
            cell.set_facecolor("#dfe6ee")
        elif "FLAT" in data[r - 1][-1] or "REPEAT" in data[r - 1][-1]:
            cell.set_facecolor("#fff4cc")
    pdf.savefig(fig)
    plt.close(fig)


README = """# AT53-04 dredge packets

Generated {date} by `Site_Maps/make_dredge_packets.py` from
`MT_dredging_coords/DredgeSites.csv` (the 30 permit dredge sites) and
`MT_dredging_coords/DredgeLines.csv` (the planned tows).

**Planning only.** Positions are WGS-84. Depths come from existing compilation grids (named per
site). Confirm on the ship's own multibeam before deploying.

## Contents

| File | What |
|---|---|
| `Dredge_Plan_Sheets.pdf` | Summary table of all tows (flagged rows shaded), then one A3 sheet per site |
| `All_Dredge_Lines.gpx` | Every tow as a route `DnnS -> DnnE`, plus waypoints `DnnS`, `Dnn` (permit site), `DnnE` |
| `All_Dredge_Lines.kml` | The same for Google Earth / most chart plotters |
| `All_Dredge_Lines.csv` | Line table: start/site/end in decimal degrees, degrees-decimal-minutes and DMS; length, azimuth, depths, slopes, grid, flags |
| `All_Dredge_Waypoints.csv` | One row per waypoint (name, lat, lon, DDM, DMS, depth): the simplest import for a nav system |
| `Dnn/Dnn_sheet.pdf` | Two pages: (1) the planning sheet, (2) the seafloor slope map |
| `Dnn/Dnn_sheet.png`, `Dnn_slope_map.png` | The same two pages as images |
| `Dnn/Dnn_waypoints.csv/.gpx/.kml` | That site's three waypoints and its route |
| `Dnn/Dnn_profile.csv` | Every 10 m along the tow, extended {extend:.0f} m beyond each end: depth, terrain slope (steepest, from the grid cells), gradient along the tow over one cell (+ = climbing), `on_tow` flag |
| `Dnn/Dnn_<grid>_elev_native.tif` | Float32 depth grid (m, elevation: negative = below sea level), {box:.0f} x {box:.0f} km around the site, the source grid's own cells (no resampling) |
| `Dnn/Dnn_<grid>_color_native.tif` | RGB picture of the same with hillshade, as a navigation-safe GeoTIFF (classic TIFF, strips, LZW, no alpha; white = no data), plus `.tfw` world file and `.prj` |
| `Dnn/Dnn_<grid>_slope_deg_native.tif` | Float32 seafloor slope (degrees) on the same cells: central differences between neighbouring cells, true metric spacing, no smoothing |
| `Dnn/Dnn_<grid>_slope_color_native.tif` | Nav-safe RGB picture of the slope (0-40 deg, pale = flat, dark red = steep) + `.tfw` / `.prj` |
| `Dnn/Dnn_<backscatter>_value_native.tif`, `_color_native.tif` | The same for a backscatter/sidescan survey where one covers the site (bright = strong return, often fresh lava) |

## The sheet

- **Map:** native grid cells, hillshade, depth contours, the tow, previous dredges, MT sites, and rings at 500 m and 1 km.
- **Along-tow profile:** depth from start to end, plus {extend:.0f} m beyond each end (grey).
- **Slope along the tow:** orange = terrain slope (the steepest direction at each point, from the grid cells). Black = gradient along the tow direction over one grid cell (+ = climbing). The black line can never exceed the orange.
- **Profile across the tow:** depth along a 2 km line perpendicular to the tow, through the site. It shows whether the tow runs along a ridge crest (high in the middle) or across a flank (one-sided), and how much room there is to drift sideways before the terrain changes.
- **Slope map (page 2):** where the steep scarps are, relative to the planned tow.

Grid slopes are cell averages. At dredge scale (metres) real slopes are steeper and rougher than a 50-100 m grid shows.

## Conventions

- **Waypoint names:** `D07S` = start of tow, `D07` = permit site, `D07E` = end of tow.
- **Tow direction** is start to end. Seeded lines run upslope (deep to shallow) through the site, 1 km long.
- **Azimuth** is the geodesic bearing start to end, degrees true. Lengths are geodesic on WGS-84.
- **Depth** is positive down in tables and profiles. The elevation GeoTIFFs keep the source sign (negative below sea level).
- **Line labels:** `auto` = first guess from `dredge_plan.py` (upslope through the site, from a plane fit over max(250 m, 2.5 cells)). `hand` = edited in the 3D viewer or by hand.
- **FLAT flag:** the fitted slope is under 3 deg, so the direction is arbitrary. Choose it on the sheet.
- **REPEAT SITE flag:** within 2 km of an MV1007 dredge that got no glass or no rock (see `Repeat_Dredge_Sites_NOTE.md`).

## Regenerate

```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env
cd galapagos-mt-cruise-geophysics/Site_Maps
python dredge_plan.py seed            # only adds lines for sites without one; never touches hand-edited lines
python make_dredge_packets.py         # all sites  (or: --sites 7 25 26)
```

To edit a line, use the 3D viewer: Dredge lines, pick a site, then **Draw line** and **Save to repo**.
Or edit `DredgeLines.csv` and run `python dredge_plan.py refresh`. Then rerun this script.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sites", nargs="*", type=int, default=None)
    ap.add_argument("--out", default=str(REPO / "Dredge_Packets"))
    ap.add_argument("--slope-baseline", type=float, default=None,
                    help="slope central-difference width in m (default: 2 grid cells, the finest)")
    ap.add_argument("--bathy-cmap", default=None, help="bathymetry colour map (default cmocean deep); any matplotlib, cmo.*, cmc.* name")
    ap.add_argument("--slope-cmap", default=None, help="slope colour map (default YlOrRd)")
    a = ap.parse_args()
    OPTS.update(slope_baseline=a.slope_baseline, bathy_cmap=a.bathy_cmap, slope_cmap=a.slope_cmap)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sites, lines, prev, mt, rep, rmg, rmh = load_inputs()
    todo = sites if a.sites is None else sites[sites.site.isin(a.sites)]
    all_w, all_r, all_rows = [], [], []
    with PdfPages(out / "Dredge_Plan_Sheets.pdf") as pdf:
        overview_page(pdf, sites, lines)
        for s in todo.itertuples():
            sid, tag = f"D{s.site}", f"D{s.site:02d}"
            d = out / tag
            if d.exists():
                shutil.rmtree(d)
            d.mkdir()
            lrow = lines[lines.site == s.site]
            ln = lrow.iloc[0] if len(lrow) else pd.Series({c: np.nan for c in dp.COLUMNS})
            req = [(ln.start_lat, ln.start_lon), (ln.end_lat, ln.end_lon)] if np.isfinite(ln.start_lat) else []
            bg = dp.best_grid(s.latitude, s.longitude, half_km=HALF_KM, require=req) or \
                dp.best_grid(s.latitude, s.longitude, half_km=HALF_KM)
            ds, cell, g = bg
            zone = reserve_zone([Point(s.longitude, s.latitude)], rmg, rmh)[0]
            rep_row = None
            if rep is not None and (rep.permit_site == sid).any():
                rep_row = rep[rep.permit_site == sid].iloc[0]
            with PdfPages(d / f"{tag}_sheet.pdf") as p1:  # 2 pages: plan + slope map
                prof, flags, sinfo = draw_sheet(sid, s, ln, g, ds, cell, prev, mt, sites, rep_row, zone,
                                         d / f"{tag}_sheet.png", d / f"{tag}_slope_map.png", [pdf, p1])
            if prof is not None:
                prof.to_csv(d / f"{tag}_profile.csv", index=False)
            w = waypoints_for(sid, s, ln)
            pd.DataFrame(w).drop(columns=["style", "desc"]).to_csv(d / f"{tag}_waypoints.csv", index=False)
            route = [] if not np.isfinite(ln.start_lat) else [(sid, f"{ln.length_m:.0f} m at {ln.azimuth_deg:.1f} deg T, "
                                                                     f"{ln.start_depth_m:.0f} -> {ln.end_depth_m:.0f} m",
                                                                     [w[0], w[-1]])]
            write_gpx(d / f"{tag}_waypoints.gpx", w, route)
            write_kml(d / f"{tag}_waypoints.kml", f"AT53-04 {sid}", w, route)
            all_w += w
            all_r += route
            # native-resolution GeoTIFF clips (nav-safe colour + float32 elevation)
            box = dp.box_around(s.latitude, s.longitude, HALF_KM)
            gclip = nr.clip(ds, box)
            nr.render_grid(gclip, "bathy", f"{tag}_{ds}", d, nr.DATASETS[ds]["label"], "m", png=False, nav=True,
                           wgs84=not gclip.geographic, sites=False, cmap=OPTS["bathy_cmap"])
            nr.render_slope(gclip, f"{tag}_{ds}", d, nr.DATASETS[ds]["label"], png=False, nav=True,
                            wgs84=not gclip.geographic, sites=False, baseline_m=OPTS["slope_baseline"],
                            cmap=OPTS["slope_cmap"])
            towline = (f"{sinfo['L']:.0f} m at {sinfo['az']:.1f} deg T, {ln.start_depth_m:.0f} -> {ln.end_depth_m:.0f} m"
                       if sinfo["has_line"] else "none")
            site_note = ""
            if sinfo["has_line"]:
                site_note = f" ({sinfo['ds_site']:.0f} m from {sid}S" + (
                    f", {sinfo['off_site']:.0f} m off the line)" if sinfo["off_site"] > 25 else ")")
            (d / f"{tag}_README.md").write_text(SITE_README.format(
                sid=sid, tag=tag, site=s.site, ds=ds, label=nr.DATASETS[ds]["label"], cell=cell, base=sinfo["base"],
                k=int(round(sinfo["base"] / cell / 2)), step=sinfo["step"], mstep=sinfo["mstep"], towline=towline,
                zone=zone, source="hand-edited" if str(ln.source).lower() == "manual" else "auto first guess",
                site_note=site_note))
            for bs in BACKSCATTER:
                bc = nr.clip(bs, box)
                if bc is not None and np.isfinite(bc.z).mean() > 0.2:
                    nr.render_grid(bc, "backscatter", f"{tag}_{bs}", d, nr.DATASETS[bs]["label"], "amplitude",
                                   png=False, nav=True, wgs84=not bc.geographic, sites=False)
            for f in d.glob("*_info.json"):  # keep the packet tidy: one combined info file
                f.unlink()
            row = dict(site=sid)
            for k, (la_, lo_) in (("start", (ln.start_lat, ln.start_lon)), ("site", (s.latitude, s.longitude)),
                                  ("end", (ln.end_lat, ln.end_lon))):
                if np.isfinite(la_):
                    row.update({f"{k}_lat_dd": round(la_, 6), f"{k}_lon_dd": round(lo_, 6),
                                f"{k}_lat_ddm": ddm(la_, "N", "S"), f"{k}_lon_ddm": ddm(lo_, "E", "W"),
                                f"{k}_lat_dms": dms(la_, "N", "S"), f"{k}_lon_dms": dms(lo_, "E", "W")})
            row.update(permit_depth_m=s.depth, start_depth_m=ln.start_depth_m, end_depth_m=ln.end_depth_m,
                       length_m=ln.length_m, azimuth_deg=ln.azimuth_deg, mean_slope_deg=ln.mean_slope_deg,
                       max_slope_deg=ln.max_slope_deg, grid=ds, grid_cell_m=round(cell, 1), line_source=ln.source,
                       reserve_zone=zone, flags=" | ".join(flags))
            all_rows.append(row)
            json.dump(row, open(d / f"{tag}_info.json", "w"), indent=1, default=str)
            print(f"{tag}: {ds} ({cell:.0f} m), {len(list(d.iterdir()))} files" + (f"  FLAGS: {' | '.join(flags)}" if flags else ""),
                  flush=True)
    if a.sites is None:
        write_gpx(out / "All_Dredge_Lines.gpx", all_w, all_r)
        write_kml(out / "All_Dredge_Lines.kml", "AT53-04 dredge lines", all_w, all_r)
        pd.DataFrame(all_rows).to_csv(out / "All_Dredge_Lines.csv", index=False)
        pd.DataFrame(all_w).drop(columns=["style", "desc"]).to_csv(out / "All_Dredge_Waypoints.csv", index=False)
        (out / "README.md").write_text(README.format(date=dt.date.today().isoformat(), extend=EXTEND_M, box=2 * HALF_KM))
    print(f"done: {out}")


if __name__ == "__main__":
    main()
