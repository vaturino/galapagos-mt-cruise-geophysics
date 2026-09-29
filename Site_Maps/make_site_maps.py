"""
Site maps and coordinate tables for the AT53-04 Galapagos cruise: dredge
sites, MT sites, and both together, numbered in planned order.

Inputs (all in this repository):
  MT_dredging_coords/DredgeSites.csv, MTsites.csv   site, latitude, longitude, depth, status
  GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap_clean.grd   basemap + depth check
  FOR_TUSHAR/CUT_bath_clean.tif                     50 m platform bathymetry, depth check
  Site_Maps/reserves/RMG.shp, RMH.shp               Galapagos / Hermandad marine reserves
                                                    (DPNG shapefiles, WGS-84)

Outputs (Site_Maps/):
  Dredge_Sites_Map.{png,pdf}, MT_Sites_Map.{png,pdf}, Combined_Sites_Map.{png,pdf}
      Bathymetry + sites only. The PDFs add coordinate-table pages after the map.
      Bathymetry: cmocean 'deep' with a depth colour bar; the colour range is the site depths
      on that map +/- 300 m (site_depth_range), so the ramp spans the depths that matter.
  *_Reserves_Map.{png,pdf}
      Same three maps with the Galapagos and Hermandad marine-reserve zones filled
      and labelled, plus an overview inset showing both reserves in full.
  Dredge_Sites_Table.csv, MT_Sites_Table.csv, AT5304_Site_Coordinates.xlsx (both, one sheet each)
      site, lat/lon in decimal degrees, degrees + decimal minutes, and
      degrees-minutes-seconds, listed depth, GMRT and 50 m bathymetry depth
      at the point, reserve zone, distance from the previous site.

"Sequence" is the site number in the planning files; the thin line joins
the sites in that order. Depths are metres, positive down.

Run (needs matplotlib, cartopy, geopandas, rasterio):
    source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env
    cd Site_Maps && python make_site_maps.py
"""
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import rasterio
from shapely.geometry import Point, box

matplotlib.use("Agg")
import cartopy.crs as ccrs
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LightSource

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))
from netcdf_lite import netcdf_file  # noqa: E402

SITES = ROOT / "MT_dredging_coords"
GMRT = ROOT / "GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap_clean.grd"
CUT50 = ROOT / "FOR_TUSHAR/CUT_bath_clean.tif"
RES = HERE / "reserves"
DEPTH_FLAG_M = 300  # listed depth vs bathymetry disagreement worth flagging

DREDGE_C, MT_C, TRACK_C, TRACK_D = "#d7301f", "#ffd92f", "0.15", "#99000d"
PC = ccrs.PlateCarree()


# ---------------------------------------------------------------- data
def ddm(v, pos, neg):
    """Decimal degrees -> degrees and decimal minutes, e.g. 1°56.6885' N.
    Rounds in integer units of 1e-4 minute so 59.99999' can't print as 60.0000'."""
    h = pos if v >= 0 else neg
    u = int(round(abs(v) * 60 * 10_000))
    d, m = divmod(u, 60 * 10_000)
    return f"{d}°{m / 10_000:07.4f}' {h}"


def dms(v, pos, neg):
    """Decimal degrees -> degrees, minutes, seconds, e.g. 1°56'41.31" N (0.01 s ~ 0.3 m)."""
    h = pos if v >= 0 else neg
    u = int(round(abs(v) * 3600 * 100))
    d, r = divmod(u, 3600 * 100)
    m, s = divmod(r, 60 * 100)
    return f"{d}°{m:02d}'{s / 100:05.2f}\" {h}"


def haversine_km(la1, lo1, la2, lo2):
    la1, lo1, la2, lo2 = map(np.radians, (la1, lo1, la2, lo2))
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(a))


def load_gmrt(bbox):
    nc = netcdf_file(str(GMRT), "r", mmap=False)
    lon = np.asarray(nc.variables["lon"][:]).copy()
    lat = np.asarray(nc.variables["lat"][:]).copy()
    i0, i1 = np.searchsorted(lon, bbox[0]), np.searchsorted(lon, bbox[1])
    j0, j1 = np.searchsorted(lat, bbox[2]), np.searchsorted(lat, bbox[3])
    z = np.array(nc.variables["altitude"][j0:j1, i0:i1], dtype=np.float32)
    nc.close()
    return lon[i0:i1], lat[j0:j1], z


def gmrt_at(lon, lat, z, la, lo):
    return z[np.abs(lat[:, None] - la[None]).argmin(0), np.abs(lon[:, None] - lo[None]).argmin(0)]


def reserve_zone(pts, rmg, rmh):
    zones = []
    for p in pts:
        z = [r.Categoria for r in rmh.itertuples() if r.geometry.contains(p)]
        if z:
            zones.append("Hermandad: " + "; ".join(z))
        elif rmg.geometry.iloc[0].contains(p):
            zones.append("Galapagos MR")
        else:
            zones.append("outside both reserves")
    return zones


def build_table(kind, rmg, rmh, glon, glat, gz):
    df = pd.read_csv(SITES / ("DredgeSites.csv" if kind == "dredge" else "MTsites.csv"))
    df = df.sort_values("site").reset_index(drop=True)
    if not (df.site.values == np.arange(1, len(df) + 1)).all():
        raise ValueError(f"{kind}: site numbers are not a gapless 1..N sequence")
    prefix = "D" if kind == "dredge" else "MT"
    out = pd.DataFrame({
        "site_id": [f"{prefix}{s:02d}" for s in df.site],
        "sequence": df.site,
        "lat_dd": df.latitude.round(5),
        "lon_dd": df.longitude.round(5),
        "lat_ddm": [ddm(v, "N", "S") for v in df.latitude],
        "lon_ddm": [ddm(v, "E", "W") for v in df.longitude],
        "lat_dms": [dms(v, "N", "S") for v in df.latitude],
        "lon_dms": [dms(v, "E", "W") for v in df.longitude],
        "depth_listed_m": df.depth,
        "depth_gmrt_m": (-gmrt_at(glon, glat, gz, df.latitude.values, df.longitude.values)).round(0),
    })
    with rasterio.open(CUT50) as ds:
        c = np.array([v[0] for v in ds.sample(zip(df.longitude, df.latitude))], dtype=float)
    c[(np.abs(c) > 1e4) | ~np.isfinite(c)] = np.nan
    out["depth_50m_m"] = (-c).round(0)
    ref = out.depth_50m_m.fillna(out.depth_gmrt_m)
    out["depth_flag"] = np.where((out.depth_listed_m - ref).abs() > DEPTH_FLAG_M,
                                 "listed depth differs from bathymetry by " +
                                 (out.depth_listed_m - ref).round(0).astype(int).astype(str) + " m", "")
    out["reserve_zone"] = reserve_zone([Point(x, y) for x, y in zip(df.longitude, df.latitude)], rmg, rmh)
    leg = haversine_km(df.latitude.shift(), df.longitude.shift(), df.latitude, df.longitude)
    out["leg_from_prev_km"] = leg.round(1)
    out["cumulative_km"] = leg.fillna(0).cumsum().round(1)
    out["status"] = df.status
    return out


# ---------------------------------------------------------------- drawing
def site_depth_range(*depths, margin=300.0, step=250.0, pct=None):
    """Colour range for the bathymetry: the depths of the sites on the map plus a margin,
    rounded to `step`, so the whole colour ramp spans the depths that matter here
    (shallower/deeper seafloor saturates to the end colours). pct=(lo, hi) uses those
    percentiles of the site depths instead of min/max, so a few outlying sites (e.g. a
    400 m summit dredge among 2000 m ones) don't flatten the contrast for the rest."""
    d = np.concatenate([np.abs(np.asarray(x, float)) for x in depths])
    d = d[np.isfinite(d)]
    lo, hi = (d.min(), d.max()) if pct is None else np.percentile(d, pct)
    return (max(0.0, np.floor((lo - margin) / step) * step), np.ceil((hi + margin) / step) * step)


def bathy(ax, glon, glat, gz, extent, vrange=None):
    """Depth coloured with cmocean 'deep' (pale = shallow, dark = deep) over a hillshade, 500 m
    isobaths, land grey-brown. vrange defaults to the 2-98th percentile of ocean depth in view.
    Returns (cmap, norm) for colorbar()."""
    import cmocean
    from matplotlib.colors import Normalize
    ax.set_extent(extent, crs=PC)
    depth = -gz
    ocean = depth > 0
    if vrange is None:
        lo, hi = np.nanpercentile(depth[ocean], [2, 98])
        vrange = (np.floor(lo / 250) * 250, np.ceil(hi / 250) * 250)
    cmap, norm = cmocean.cm.deep, Normalize(*vrange)
    dx = 111_320 * np.cos(np.radians(glat.mean())) * (glon[1] - glon[0])
    dy = 111_320 * (glat[1] - glat[0])
    hs = LightSource(azdeg=315, altdeg=40).hillshade(np.nan_to_num(gz, nan=-3000), vert_exag=3, dx=dx, dy=dy)
    rgb = cmap(norm(np.clip(depth, *vrange)))[..., :3]
    rgb[~ocean] = np.array([0.72, 0.68, 0.60])
    rgb = rgb * (0.6 + 0.4 * hs[..., None])
    ax.imshow(np.clip(rgb, 0, 1), origin="lower", extent=[glon[0], glon[-1], glat[0], glat[-1]], transform=PC,
              interpolation="bilinear", zorder=0)
    ax.contour(glon, glat, gz, levels=np.arange(-5000, 0, 500), colors="k", linewidths=0.25, alpha=0.35,
               transform=PC, zorder=1)
    ax.contour(glon, glat, gz, levels=[0], colors="0.15", linewidths=0.6, transform=PC, zorder=1)
    gl = ax.gridlines(draw_labels=True, linewidth=0.3, color="0.3", alpha=0.5, linestyle=":")
    gl.top_labels = gl.right_labels = False
    gl.xlabel_style = gl.ylabel_style = {"size": 8}
    return cmap, norm


def colorbar(fig, ax, cmap, norm):
    """Vertical depth colour bar just right of the map axes, shallow at the top."""
    bb = ax.get_position()
    cax = fig.add_axes([bb.x1 + 0.008, bb.y0 + 0.1 * bb.height, 0.012, 0.8 * bb.height])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), cax=cax, extend="both")
    cb.set_label("Water depth (m)", fontsize=9)
    cb.ax.invert_yaxis()
    cb.ax.tick_params(labelsize=8)


ZONES = [  # (key, label, fill, edge, linestyle, alpha)
    ("GMR", "Galapagos Marine Reserve", "#00a651", "#00843d", "--", 0.09),
    ("RMH_NT", "Hermandad MR: No-Take zone", "#e7298a", "#ce1256", "-.", 0.22),
    ("RMH_RF", "Hermandad MR: Responsible-Fishing zone", "#807dba", "#54278f", ":", 0.18),
]


def zone_geoms(rmg, rmh):
    return {"GMR": list(rmg.geometry),
            "RMH_NT": [g for g, c in zip(rmh.geometry, rmh.Categoria) if c == "Zona No Take"],
            "RMH_RF": [g for g, c in zip(rmh.geometry, rmh.Categoria) if c != "Zona No Take"]}


def reserves(ax, rmg, rmh, extent, site_ll):
    """Filled, outlined and labelled reserve zones. Each label goes at the point inside the
    zone (within the map frame) farthest from any site."""
    from shapely.ops import unary_union
    w, h = extent[1] - extent[0], extent[3] - extent[2]
    # keep labels well inside the frame (they are ~0.3 deg wide) and off the scale bar / north arrow
    frame = box(extent[0] + 0.14 * w, extent[2] + 0.05 * h, extent[1] - 0.14 * w, extent[3] - 0.06 * h).difference(
        box(extent[1] - 0.25 * w, extent[3] - 0.3 * h, extent[1], extent[3]))
    geoms = zone_geoms(rmg, rmh)
    gx, gy = np.meshgrid(np.linspace(extent[0], extent[1], 90), np.linspace(extent[2], extent[3], 90))
    for key, lab, fc, ec, ls, a in ZONES:
        g = geoms[key]
        ax.add_geometries(g, PC, facecolor=fc, alpha=a, edgecolor="none", zorder=2)
        ax.add_geometries(g, PC, facecolor="none", edgecolor=ec, linewidth=1.6, linestyle=ls, zorder=2)
        inside = unary_union(g).intersection(frame)
        if inside.is_empty or inside.area < 0.02:
            continue
        cand = [(x, y) for x, y in zip(gx.ravel(), gy.ravel()) if inside.contains(Point(x, y))]
        if not cand:
            continue
        c = np.array(cand)
        d = np.min(np.hypot((c[:, None, 0] - site_ll[None, :, 0]) * np.cos(np.radians(1)),
                            c[:, None, 1] - site_ll[None, :, 1]), axis=1)
        x, y = c[np.argmax(d)]
        ax.text(x, y, lab.replace(": ", ":\n"), transform=PC, ha="center", va="center", fontsize=8.5,
                fontweight="bold", color=ec, style="italic", zorder=6,
                path_effects=[pe.withStroke(linewidth=3, foreground="w")])


def overview_inset(fig, rect, extent, rmg, rmh, olon, olat, oz, site_ll):
    """Small locator map: both reserves in full, the islands, and this map's frame."""
    ov = [-94.5, -86.3, -2.6, 4.3]
    iax = fig.add_axes(rect, projection=PC)
    iax.set_extent(ov, crs=PC)
    iax.set_facecolor("#dcebf5")
    iax.contourf(olon, olat, oz, levels=[0, 1e4], colors=["#b7ab8b"], transform=PC, zorder=1)
    geoms = zone_geoms(rmg, rmh)
    for key, lab, fc, ec, ls, a in ZONES:
        iax.add_geometries(geoms[key], PC, facecolor=fc, alpha=a + 0.1, edgecolor=ec, linewidth=0.8,
                           linestyle=ls, zorder=2)
    iax.scatter(site_ll[:, 0], site_ll[:, 1], s=2, c="k", transform=PC, zorder=3)
    iax.add_geometries([box(extent[0], extent[2], extent[1], extent[3])], PC, facecolor="none", edgecolor="k",
                       linewidth=1.2, zorder=4)
    gl = iax.gridlines(draw_labels=True, linewidth=0.3, color="0.4", alpha=0.5, linestyle=":")
    gl.top_labels = gl.right_labels = False
    gl.xlabel_style = gl.ylabel_style = {"size": 6}
    iax.set_title("Overview: both reserves in full (box = main map)", fontsize=7.5, pad=3)


def scalebar(ax, extent, km=25, corner="ur"):
    """Scale bar + north arrow, in the upper-right ('ur', default) or lower-left ('ll') corner."""
    w, h = extent[1] - extent[0], extent[3] - extent[2]
    dlo = km / (111.32 * np.cos(np.radians(extent[3])))
    if corner == "ll":
        lo1, la0, nx, ny = extent[0] + 0.04 * w + dlo, extent[2] + 0.08 * h, 0.03, 0.12
    else:
        lo1, la0, nx, ny = extent[1] - 0.04 * w, extent[3] - 0.05 * h, 0.975, 0.77
    ax.plot([lo1 - dlo, lo1], [la0, la0], color="k", lw=3, transform=PC, zorder=6,
            path_effects=[pe.Stroke(linewidth=5, foreground="w"), pe.Normal()])
    ax.text(lo1 - dlo / 2, la0 - 0.012 * h, f"{km} km", ha="center", va="top", fontsize=8, transform=PC,
            zorder=6, path_effects=[pe.withStroke(linewidth=2.5, foreground="w")])
    ax.annotate("N", xy=(nx, ny + 0.07), xytext=(nx, ny), xycoords="axes fraction", ha="center",
                fontsize=10, fontweight="bold", zorder=6, arrowprops=dict(arrowstyle="-|>", color="k", lw=1.5),
                path_effects=[pe.withStroke(linewidth=2.5, foreground="w")])


def draw_sites(ax, t, kind):
    """Track in planned order (with direction arrows) and site markers."""
    lon, lat = t.lon_dd.values, t.lat_dd.values
    tc = TRACK_D if kind == "dredge" else TRACK_C
    ax.plot(lon, lat, color=tc, lw=0.9, alpha=0.85, transform=PC, zorder=3)
    tr = PC._as_mpl_transform(ax)
    for k in range(len(lon) - 1):  # direction arrow at each leg midpoint
        mx, my = (lon[k] + lon[k + 1]) / 2, (lat[k] + lat[k + 1]) / 2
        ax.annotate("", xy=(mx + (lon[k + 1] - lon[k]) * 0.08, my + (lat[k + 1] - lat[k]) * 0.08), xytext=(mx, my),
                    xycoords=tr, arrowprops=dict(arrowstyle="-|>", color=tc, lw=0.6, mutation_scale=7), zorder=3)
    if kind == "dredge":
        ax.scatter(lon, lat, s=70, marker="^", c=DREDGE_C, edgecolors="k", linewidths=0.8, transform=PC, zorder=5)
    else:
        ax.scatter(lon, lat, s=34, marker="D", c=MT_C, edgecolors="k", linewidths=0.6, transform=PC, zorder=4)


def place_labels(ax, items, obstacles_xy, dpi):
    """Greedy label placement. items: list of (x_disp, y_disp, text, fontsize, color).
    Tries 16 offsets around each point; picks the one whose text box overlaps least with
    site markers and labels already placed (ties -> closest)."""
    px = dpi / 72.0
    obst = [(x - 5 * px, y - 5 * px, x + 5 * px, y + 5 * px) for x, y in obstacles_xy]
    placed = []
    ang = np.radians(np.arange(0, 360, 45))
    cands = [(r * np.cos(a), r * np.sin(a)) for r in (9, 15) for a in ang]
    tr = ax.transData.inverted()
    fb = ax.get_window_extent()
    frame = (fb.x0, fb.y0, fb.x1, fb.y1)

    def overlap(b, boxes):
        return sum(max(0, min(b[2], o[2]) - max(b[0], o[0])) * max(0, min(b[3], o[3]) - max(b[1], o[1]))
                   for o in boxes)

    for x, y, txt, fs, col in items:
        wbox, hbox = 0.62 * fs * len(txt) * px + 2 * px, 1.0 * fs * px + 2 * px
        best = None
        for dxp, dyp in cands:
            cx, cy = x + dxp * px, y + dyp * px
            b = (cx - wbox / 2, cy - hbox / 2, cx + wbox / 2, cy + hbox / 2)
            outside = (b[2] - b[0]) * (b[3] - b[1]) - overlap(b, [frame])  # part of the box off the map
            cost = overlap(b, placed) * 3 + overlap(b, obst) + outside * 10 + np.hypot(dxp, dyp) * 0.5
            if best is None or cost < best[0]:
                best = (cost, cx, cy, b)
        _, cx, cy, b = best
        placed.append(b)
        dx, dy = tr.transform((cx, cy))
        ax.text(dx, dy, txt, ha="center", va="center", fontsize=fs, fontweight="bold", color=col, zorder=7,
                path_effects=[pe.withStroke(linewidth=2.2, foreground="w")])


def legend(fig, kinds, with_reserves, anchor=(0.52, 0.03), loc="lower center", ncol=4):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    h = []
    if "dredge" in kinds:
        h.append(Line2D([], [], marker="^", ls="", mfc=DREDGE_C, mec="k", ms=9, label="Dredge site (D1 = first)"))
        h.append(Line2D([], [], color=TRACK_D, lw=0.9, marker=">", ms=4, label="Dredge planned order"))
    if "mt" in kinds:
        h.append(Line2D([], [], marker="D", ls="", mfc=MT_C, mec="k", ms=6, label="MT site (1 = first)"))
        h.append(Line2D([], [], color=TRACK_C, lw=0.9, marker=">", ms=4, label="MT planned order"))
    if with_reserves:
        h += [Patch(facecolor=fc, alpha=a + 0.1, edgecolor=ec, ls=ls, lw=1.4, label=lab)
              for _, lab, fc, ec, ls, a in ZONES]
    h.append(Line2D([], [], color="k", lw=0.4, alpha=0.5, label="500 m isobaths"))
    fig.legend(handles=h, loc=loc, bbox_to_anchor=anchor, ncol=ncol, fontsize=7.5, frameon=False)


def table_pages(pdf, t, title, rows_per_page=34):
    short = {"Galapagos MR": "GMR", "outside both reserves": "outside both",
             "Hermandad: Zona No Take": "Hermandad No-Take",
             "Hermandad: Zona de Pesca Responsable": "Hermandad Resp. Fishing"}
    t = t.assign(zone_short=t.reserve_zone.map(lambda z: short.get(z, z)))
    cols = ["site_id", "lat_dd", "lon_dd", "lat_ddm", "lon_ddm", "lat_dms", "lon_dms", "depth_listed_m",
            "depth_50m_m", "depth_gmrt_m", "leg_from_prev_km", "cumulative_km", "zone_short"]
    head = ["Site", "Lat\n(decimal °)", "Lon\n(decimal °)", "Lat\n(° decimal ')", "Lon\n(° decimal ')",
            "Lat\n(° ' \")", "Lon\n(° ' \")", "Depth\nlisted", "Depth\n50 m grid", "Depth\nGMRT",
            "Leg\n(km)", "Cum.\n(km)", "Reserve zone"]
    for p0 in range(0, len(t), rows_per_page):
        chunk = t.iloc[p0:p0 + rows_per_page]
        fig = plt.figure(figsize=(11, 8.5))
        fig.suptitle(f"{title} (rows {p0 + 1}-{p0 + len(chunk)} of {len(t)})", fontsize=11, y=0.975)
        ax = fig.add_axes([0.02, 0.06, 0.96, 0.87]); ax.axis("off")
        cells = [[("" if pd.isna(v) else (f"{v:.5f}" if c in ("lat_dd", "lon_dd") else
                  (f"{v:.0f}" if c.startswith("depth") else (f"{v:.1f}" if c.endswith("km") else str(v)))))
                  for c, v in zip(cols, r)] for r in chunk[cols].itertuples(index=False)]
        tb = ax.table(cellText=cells, colLabels=head, loc="upper center", cellLoc="center",
                      colWidths=[0.045, 0.065, 0.07, 0.085, 0.09, 0.085, 0.09, 0.05, 0.055, 0.05, 0.04, 0.045, 0.13])
        tb.auto_set_font_size(False); tb.set_fontsize(6.8); tb.scale(1, 1.06)
        for (r, c), cell in tb.get_celld().items():
            cell.set_linewidth(0.3)
            if r == 0:
                cell.set_facecolor("#e0e0e0"); cell.set_height(cell.get_height() * 1.6)
            elif chunk.depth_flag.iloc[r - 1] and c == 7:
                cell.set_facecolor("#fdd0a2")
        fig.text(0.02, 0.015, "WGS-84; depths in metres, positive down. 'Depth 50 m grid' = Mittelstaedt/Young platform "
                 "compilation (blank outside its coverage); 'GMRT' = spike-repaired GMRT basemap (~245 m).\n"
                 f"Orange = listed depth differs from bathymetry by > {DEPTH_FLAG_M} m. Leg = great-circle distance "
                 "from the previous site in the sequence; Cum. = running total.", fontsize=7, va="bottom")
        pdf.savefig(fig); plt.close(fig)


def make_map(name, title, tables, extent, glon, glat, gz, rmg, rmh, fonts, with_reserves=False, overview=None):
    W, AXW = 11.0, 0.85  # leaves room for the depth colour bar
    map_h = W * AXW * (extent[3] - extent[2]) / (extent[1] - extent[0])  # PlateCarree: 1 deg = 1 deg
    band = 3.3 if with_reserves else 1.0  # inches below the map: legend (+ overview inset)
    H = map_h + band + 0.35
    fig = plt.figure(figsize=(W, H))
    ax = fig.add_axes([0.07, band / H, AXW, map_h / H], projection=PC)
    cmap, norm = bathy(ax, glon, glat, gz, extent,
                       vrange=site_depth_range(*[t.depth_listed_m for t in tables.values()]))
    colorbar(fig, ax, cmap, norm)
    site_ll = np.vstack([t[["lon_dd", "lat_dd"]].values for t in tables.values()])
    if with_reserves:
        reserves(ax, rmg, rmh, extent, site_ll)
    for kind, t in tables.items():
        draw_sites(ax, t, kind)
    fig.canvas.draw()  # fix the axes transforms before placing labels in display space
    xy = {kind: PC._as_mpl_transform(ax).transform(np.column_stack([t.lon_dd.values, t.lat_dd.values]))
          for kind, t in tables.items()}
    items = []
    for kind in ("dredge", "mt"):  # dredge labels first: fewer, and they get first pick
        if kind in tables:
            t = tables[kind]
            for (x, y), s in zip(xy[kind], t.sequence):
                items.append((x, y, f"D{s}" if kind == "dredge" else f"{s}", fonts[kind],
                              "#67000d" if kind == "dredge" else "k"))
    place_labels(ax, items, np.vstack(list(xy.values())), fig.dpi)
    scalebar(ax, extent)
    ax.set_title(title, fontsize=12, fontweight="bold", loc="left")
    if with_reserves:
        legend(fig, list(tables), True, anchor=(0.07, (band - 0.45) / H), loc="upper left", ncol=2)
        ih = 2.45  # inset height in inches; overview aspect is 8.2 deg x 6.9 deg
        iw = ih * 8.2 / 6.9
        overview_inset(fig, [0.93 - iw / W, 0.4 / H, iw / W, ih / H], extent, rmg, rmh, *overview, site_ll)
    else:
        legend(fig, list(tables), False, anchor=(0.52, 0.3 / H))
    fig.text(0.07, 0.012, "Basemap: GMRT synthesis (Ryan et al., 2009), spike-repaired (scripts/fix_gmrt_spikes.py). "
             + ("Reserves: DPNG shapefiles (RMG, RMH). " if with_reserves else "") +
             "Sites: MT_dredging_coords/*.csv. WGS-84.", fontsize=7, color="0.3")
    fig.savefig(HERE / f"{name}.png", dpi=200)
    with PdfPages(HERE / f"{name}.pdf") as pdf:
        pdf.savefig(fig)
        for kind, t in tables.items():
            table_pages(pdf, t, f"{'Dredge' if kind == 'dredge' else 'MT'} sites: coordinates and sequence")
    plt.close(fig)


def site_extent(*tables, pad_lon=0.15, pad_lat=0.13):
    """Map frame = bounding box of all sites plus a margin (room for labels, scale bar, north arrow)."""
    lon = np.concatenate([t.lon_dd.values for t in tables])
    lat = np.concatenate([t.lat_dd.values for t in tables])
    return [round(lon.min() - pad_lon, 2), round(lon.max() + pad_lon + 0.1, 2),
            round(lat.min() - pad_lat, 2), round(lat.max() + pad_lat + 0.05, 2)]


def main():
    rmg, rmh = gpd.read_file(RES / "RMG.shp"), gpd.read_file(RES / "RMH.shp")
    glon, glat, gz = load_gmrt((-93.2, -90.0, 0.0, 2.8))
    td = build_table("dredge", rmg, rmh, glon, glat, gz)
    tm = build_table("mt", rmg, rmh, glon, glat, gz)
    for t, f in ((td, "Dredge_Sites_Table.csv"), (tm, "MT_Sites_Table.csv")):
        t.to_csv(HERE / f, index=False)
    with pd.ExcelWriter(HERE / "AT5304_Site_Coordinates.xlsx", engine="openpyxl") as xw:
        for t, sh in ((td, "Dredge"), (tm, "MT")):
            t.to_excel(xw, sheet_name=sh, index=False)
            ws = xw.sheets[sh]
            ws.freeze_panes = "B2"
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = max(9, min(40, max(len(str(c.value or "")) for c in col) + 2))

    olon, olat, oz = load_gmrt((-94.6, -86.2, -2.7, 4.4))
    overview = (olon[::6], olat[::6], oz[::6, ::6])
    maps = [  # name, title, tables, extent, label font sizes
        ("Dredge_Sites_Map", "AT53-04 dredge sites (30), numbered in planned order",
         {"dredge": td}, site_extent(td), {"dredge": 8}),
        ("MT_Sites_Map", "AT53-04 MT sites (90), numbered in planned order",
         {"mt": tm}, site_extent(tm), {"mt": 6.5}),
        ("Combined_Sites_Map", "AT53-04 dredge (D1-D30) and MT (1-90) sites",
         {"mt": tm, "dredge": td}, site_extent(tm, td), {"mt": 6, "dredge": 7}),
    ]
    for name, title, tabs, ext, fonts in maps:
        make_map(name, title, tabs, ext, glon, glat, gz, rmg, rmh, fonts)
        make_map(name.replace("_Map", "_Reserves_Map"), title + ", with marine reserves", tabs, ext,
                 glon, glat, gz, rmg, rmh, fonts, with_reserves=True, overview=overview)

    for name, t in (("Dredge", td), ("MT", tm)):
        print(f"{name}: {len(t)} sites, sequence track {t.cumulative_km.iloc[-1]:.1f} km; "
              f"reserve zones {t.reserve_zone.value_counts().to_dict()}")
        fl = t[t.depth_flag != ""]
        for r in fl.itertuples():
            print(f"   FLAG {r.site_id}: listed {r.depth_listed_m} m, 50 m grid {r.depth_50m_m}, GMRT {r.depth_gmrt_m} -> {r.depth_flag}")


if __name__ == "__main__":
    main()
