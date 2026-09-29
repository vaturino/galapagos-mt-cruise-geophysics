"""
Maps of the AT53-04 permit dredge sites together with previous dredges in the region, plus
an MV1007-only map. Bathymetry is coloured with cmocean 'deep' (pale = shallow, dark = deep)
over a hillshade, with a depth colour bar.

Inputs
  MT_dredging_coords/DredgeSites.csv                      permit dredge sites (AT53-04)
  <PREV>/Galápagos samples_lats and longs.xlsx            previous samples: GSC cruises, MV1007,
                                                          PLUME02, SO158 (ID, material, lat, lon, depth)
  <PREV>/MV1007_DredgeLog_167_2358_KL Edited.xls          MV1007 log: on/off-bottom positions,
                                                          depths, recovery, description (D01-D47)
  GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap_clean.grd   ~245 m basemap (local maps)
  GMRT_regional/GMRT_Basemap/GMRT_GSC_wide_high.grd       ~1 km GMRT, 102.5-82.5 W (regional map)

Outputs (Site_Maps/)
  Permit_Dredges_with_Previous_Map.{png,pdf}   permit sites D1-D30 + previous dredges, local
  MV1007_Dredges_Map.{png,pdf}                 MV1007 D01-D47 on/off-bottom tracks
  Previous_Dredges_Regional_Map.{png,pdf}      every previous dredge, 102-83 W, + permit sites
  Previous_Dredges_Compiled.csv                one row per dredge station, all cruises
  Dredge_Glass_Status_Map.{png,pdf}            permit sites + previous dredges coloured by recovery
                                               (glass / no glass / no rock), shape = cruise
  Dredge_Zoom_<A-D>_*.{png,pdf}                four zoomed maps, every site labelled, 50 m
                                               Mittelstaedt bathymetry where available (GMRT elsewhere)
  Dredge_Zooms_All.pdf                         the four zooms in one PDF
  Permit_Sites_Nearest_Previous.csv            per permit site: nearest previous dredge, and nearest
                                               one that recovered glass

Data corrections made here (and listed in the CSV 'note' column):
  * MV1007 D14 off-bottom longitude is logged as 91 deg 0.157' W; the on-bottom longitude is
    91 deg 59.989' W, so it must be 92 deg 0.157' W (0.4 km drag instead of 111 km).
  * ST7-17D-1G (+86.13) and NA063-002 (+85.9) were listed with east longitudes; this region is
    all west. Fixed to -86.13 and -85.9 in the spreadsheet itself on 2026-09-29 (original kept as
    ..._ORIGINAL_before_lon_fix.xlsx); any remaining east longitude is still flipped and noted.
  * MV1007 D05/D06 are in the log but not the spreadsheet; recovery classes come from the log
    description. D23, D34 and D45 recovered no rock ("no rock"), whatever the spreadsheet says.

    source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env
    cd Site_Maps && python make_previous_dredge_maps.py
"""
import re

import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LightSource
from matplotlib.lines import Line2D

import geopandas as gpd
from make_site_maps import (HERE, PC, RES, ROOT, SITES, bathy, colorbar, haversine_km, load_gmrt, place_labels,
                            scalebar, site_depth_range)
from netcdf_lite import netcdf_file

PREV = "/home/tmittal/Dropbox/Work_AI/Permits/9_AT5304_Final_Sites_and_Maps/4_prev_dredges/"
XLSX = PREV + "Galápagos samples_lats and longs.xlsx"
LOG = PREV + "MV1007_DredgeLog_167_2358_KL Edited.xls"
WIDE = ROOT / "GMRT_regional/GMRT_Basemap/GMRT_GSC_wide_high.grd"

# cruise -> (marker, colour, label)
CRUISES = {
    "MV1007": ("o", "#ff7f00", "MV1007 (2010)"),
    "PLUME02": ("D", "#f768a1", "PLUME02"),
    "SO158": ("P", "#00d5ff", "SO158"),
    "TR164": ("s", "#9be564", "TR164"),
    "CTW": ("<", "#ffd92f", "CTW"),
    "DS": ("p", "#e5c494", "DS"),
    "ST7": (">", "#b3b3b3", "ST7"),
    "NA06x": ("h", "#fdbf6f", "NA062/NA063"),
    "NZ": ("v", "#cab2d6", "NZ"),
}
PERMIT_C = "#d7301f"
# longitudes that were missing their minus sign in the spreadsheet (fixed in the .xlsx on 2026-09-29)
SIGN_FIXED = {"ST7-17D-1G": 86.13, "NA063-002": 85.9}


# ------------------------------------------------------------------ data
def cruise_of(sample_id, section):
    if section in ("MV1007", "PLUME02", "SO158"):
        return section
    for pre, c in (("TR164", "TR164"), ("CTW", "CTW"), ("DS-", "DS"), ("ST7", "ST7"), ("NZ", "NZ"), ("NA06", "NA06x")):
        if sample_id.startswith(pre):
            return c
    raise ValueError(f"unknown cruise for {sample_id}")


def load_spreadsheet():
    x = pd.read_excel(XLSX, header=None)
    rows, section = [], None
    for r in x.itertuples(index=False):
        sid = r[0]
        if isinstance(sid, str) and pd.isna(r[2]) and pd.isna(r[1]):
            section = {"Galápagos Spredaing Center": "GSC", "MV1007 Cruise": "MV1007", "PLUME02 Cruise": "PLUME02",
                       "SO158 Cruise": "SO158"}.get(sid.strip(), sid.strip())
            continue
        if section is None or section.startswith("Proposed") or pd.isna(r[2]) or sid == "SAMPLE ID":
            continue
        rows.append(dict(sample=str(sid).strip(), material=str(r[1]).strip(), lat=float(r[2]), lon=float(r[3]),
                         depth=r[4], source=r[5] if isinstance(r[5], str) else "", section=section))
    return pd.DataFrame(rows)


def dm(d, m, h):
    v = float(d) + float(m) / 60.0
    return -v if str(h).strip() in ("S", "W") else v


def load_mv1007_log():
    x = pd.read_excel(LOG, header=None).iloc[4:51]
    out = []
    for r in x.values:
        did = str(r[0]).strip()
        off_lon_deg = r[20]
        note = ""
        if did == "D14" and int(r[20]) == 91 and abs(float(r[21]) - 0.157) < 1e-6:
            off_lon_deg, note = 92, "off-bottom lon logged 91 0.157'W, corrected to 92 0.157'W"
        desc = f"{r[2]}; {r[3]}"
        if re.search(r"no rock|sediment only|no igneous", desc, re.I):
            rec = "no rock"
        elif re.search(r"glass", desc, re.I):
            rec = "glass"
        else:
            rec = "no glass"
        out.append(dict(station=did, lat=dm(r[8], r[9], r[10]), lon=dm(r[11], r[12], r[13]),
                        off_lat=dm(r[17], r[18], r[19]), off_lon=dm(off_lon_deg, r[21], r[22]),
                        depth=int(r[7]), off_depth=int(r[16]), location=str(r[1]).strip(),
                        recovery=str(r[2]).strip(), description=str(r[3]).strip(), glass=rec, note=note))
    return pd.DataFrame(out)


def compile_previous():
    s = load_spreadsheet()
    log = load_mv1007_log()
    # MV1007: log is authoritative for positions; check spreadsheet agrees, and use its glass class
    mv_s = s[s.section == "MV1007"].set_index("sample")
    for r in log.itertuples():
        if r.station in mv_s.index:
            q = mv_s.loc[r.station]
            dist_m = haversine_km(q.lat, q.lon, r.lat, r.lon) * 1000
            if dist_m > 5:
                raise ValueError(f"MV1007 {r.station}: spreadsheet and log differ by {dist_m:.0f} m")
            sheet_glass = "glass" if q.material == "with glass" else "no glass"
            if r.glass != "no rock" and sheet_glass != r.glass:
                raise ValueError(f"MV1007 {r.station}: spreadsheet says {q.material}, log description says {r.glass}")
    mv = log.assign(cruise="MV1007", samples=log.station, source="MV1007 dredge log (on-bottom position)",
                    note=[n or ("not in spreadsheet; class from log description" if st in ("D05", "D06") else "")
                          for n, st in zip(log.note, log.station)])
    others = s[s.section != "MV1007"].copy()
    others["cruise"] = [cruise_of(a, b) for a, b in zip(others["sample"], others.section)]
    others["note"] = [f"lon listed as +{SIGN_FIXED[x]} (east) in the original spreadsheet; corrected to west 2026-09-29"
                      if x in SIGN_FIXED else "" for x in others["sample"]]
    east = others.lon > 0
    others.loc[east, "note"] = "listed lon +" + others.loc[east, "lon"].astype(str) + " (east); plotted as west"
    others.loc[east, "lon"] = -others.loc[east, "lon"]
    # one row per dredge station = same cruise and position
    st = (others.groupby(["cruise", "lat", "lon"], sort=False)
          .agg(samples=("sample", lambda v: ", ".join(v)), depth=("depth", "first"),
               glass=("material", lambda v: "glass" if (v == "glass").any() else "no glass"),
               source=("source", lambda v: "; ".join(sorted({a for a in v if a}))),
               note=("note", lambda v: "; ".join(sorted({a for a in v if a}))))
          .reset_index())
    st["station"] = st.samples.str.split(",").str[0].str.replace(r"-\d+G?$|G$|A$|Ag$", "", regex=True)
    cols = ["cruise", "station", "samples", "lat", "lon", "depth", "glass", "source", "note"]
    allp = pd.concat([mv.assign(samples=mv.station)[cols + ["off_lat", "off_lon", "location", "recovery", "description"]],
                      st[cols]], ignore_index=True)
    return allp, mv


# ------------------------------------------------------------------ drawing
def load_grid(path, bbox):
    nc = netcdf_file(str(path), "r", mmap=False)
    lon = np.asarray(nc.variables["lon"][:]).copy()
    lat = np.asarray(nc.variables["lat"][:]).copy()
    i0, i1 = np.searchsorted(lon, bbox[0]), np.searchsorted(lon, bbox[1])
    j0, j1 = np.searchsorted(lat, bbox[2]), np.searchsorted(lat, bbox[3])
    z = np.array(nc.variables["altitude"][j0:j1, i0:i1], dtype=np.float32)
    nc.close()
    return lon[i0:i1], lat[j0:j1], z


def reserve_outlines(ax):
    rmg, rmh = gpd.read_file(RES / "RMG.shp"), gpd.read_file(RES / "RMH.shp")
    ax.add_geometries(rmg.geometry, PC, facecolor="none", edgecolor="#00a651", lw=1.1, ls="--", zorder=2)
    ax.add_geometries(rmh.geometry, PC, facecolor="none", edgecolor="#e7298a", lw=0.9, ls="-.", zorder=2)


def draw_previous(ax, p, size=38, zorder=4):
    for c, g in p.groupby("cruise"):
        mk, col, _ = CRUISES[c]
        gg = g[g.glass == "glass"]
        ax.scatter(gg.lon, gg.lat, s=size, marker=mk, c=col, edgecolors="k", linewidths=0.7, transform=PC, zorder=zorder)
        gn = g[g.glass == "no glass"]
        ax.scatter(gn.lon, gn.lat, s=size, marker=mk, facecolors="white", edgecolors=col, linewidths=1.6,
                   transform=PC, zorder=zorder)
        gx = g[g.glass == "no rock"]
        ax.scatter(gx.lon, gx.lat, s=size * 0.9, marker="x", c=col, linewidths=1.6, transform=PC, zorder=zorder)


def legend_handles(cruises_present, permit=True):
    h = []
    if permit:
        h.append(Line2D([], [], marker="^", ls="", mfc=PERMIT_C, mec="k", ms=10, label="AT53-04 permit dredge site (D1-D30)"))
    for c in CRUISES:
        if c in cruises_present:
            mk, col, lab = CRUISES[c]
            h.append(Line2D([], [], marker=mk, ls="", mfc=col, mec="k", ms=7, label=lab))
    h += [Line2D([], [], marker="o", ls="", mfc="0.45", mec="k", ms=7, label="filled = glass recovered"),
          Line2D([], [], marker="o", ls="", mfc="white", mec="0.45", mew=1.6, ms=7, label="open = no glass"),
          Line2D([], [], marker="x", ls="", color="0.45", mew=1.6, ms=7, label="x = no rock recovered")]
    return h


def figure_for(extent, width=11.0, band=1.25):
    mh = width * 0.86 * (extent[3] - extent[2]) / (extent[1] - extent[0])
    H = mh + band + 0.45
    fig = plt.figure(figsize=(width, H))
    ax = fig.add_axes([0.06, band / H, 0.86, mh / H], projection=PC)
    return fig, ax, H


def save(fig, name):
    fig.savefig(HERE / f"{name}.png", dpi=200)
    with PdfPages(HERE / f"{name}.pdf") as pdf:
        pdf.savefig(fig)
    plt.close(fig)


def pad_extent(lon, lat, px=0.15, py=0.12):
    return [lon.min() - px, lon.max() + px + 0.08, lat.min() - py, lat.max() + py + 0.05]


# ------------------------------------------------------------------ maps
def map_permit_with_previous(permit, prev):
    ext = pad_extent(permit.longitude, permit.latitude)
    inside = prev[(prev.lon.between(ext[0], ext[1])) & (prev.lat.between(ext[2], ext[3]))]
    glon, glat, gz = load_gmrt((ext[0] - 0.05, ext[1] + 0.05, ext[2] - 0.05, ext[3] + 0.05))
    fig, ax, H = figure_for(ext, band=1.55)
    cmap, norm = bathy(ax, glon, glat, gz, ext, vrange=site_depth_range(permit.depth, inside.depth, pct=(5, 95)))
    reserve_outlines(ax)
    ax.plot(permit.longitude, permit.latitude, color=PERMIT_C, lw=0.9, alpha=0.8, transform=PC, zorder=3)
    draw_previous(ax, inside)
    ax.scatter(permit.longitude, permit.latitude, s=85, marker="^", c=PERMIT_C, edgecolors="k", linewidths=0.9,
               transform=PC, zorder=6)
    fig.canvas.draw()
    tr = PC._as_mpl_transform(ax)
    xy = tr.transform(np.column_stack([permit.longitude, permit.latitude]))
    obst = np.vstack([xy, tr.transform(np.column_stack([inside.lon, inside.lat]))])
    place_labels(ax, [(x, y, f"D{s}", 8, "#67000d") for (x, y), s in zip(xy, permit.site)], obst, fig.dpi)
    scalebar(ax, ext)
    colorbar(fig, ax, cmap, norm)
    ax.set_title(f"AT53-04 permit dredge sites (D1-D30, planned order) and {len(inside)} previous dredges in the area",
                 fontsize=11.5, fontweight="bold", loc="left")
    h = legend_handles(set(inside.cruise)) + [
        Line2D([], [], color="#00a651", ls="--", lw=1.1, label="Galapagos Marine Reserve"),
        Line2D([], [], color="#e7298a", ls="-.", lw=0.9, label="Hermandad MR"),
        Line2D([], [], color="k", lw=0.4, alpha=0.5, label="500 m isobaths")]
    fig.legend(handles=h, loc="upper left", bbox_to_anchor=(0.06, (1.55 - 0.35) / H), ncol=4, fontsize=7.5, frameon=False)
    save(fig, "Permit_Dredges_with_Previous_Map")
    return inside


def map_mv1007(mv):
    lons = np.concatenate([mv.lon, mv.off_lon])
    lats = np.concatenate([mv.lat, mv.off_lat])
    ext = pad_extent(lons, lats, 0.12, 0.1)
    glon, glat, gz = load_gmrt((ext[0] - 0.05, ext[1] + 0.05, ext[2] - 0.05, ext[3] + 0.05))
    fig, ax, H = figure_for(ext, band=1.1)
    cmap, norm = bathy(ax, glon, glat, gz, ext, vrange=site_depth_range(mv.depth, mv.off_depth, pct=(5, 95)))
    for r in mv.itertuples():
        ax.plot([r.lon, r.off_lon], [r.lat, r.off_lat], color="#ff7f00", lw=2.2, transform=PC, zorder=3,
                path_effects=[pe.Stroke(linewidth=3.6, foreground="k"), pe.Normal()])
    draw_previous(ax, mv.assign(cruise="MV1007"), size=46)
    fig.canvas.draw()
    tr = PC._as_mpl_transform(ax)
    xy = tr.transform(np.column_stack([mv.lon, mv.lat]))
    place_labels(ax, [(x, y, s, 7.5, "k") for (x, y), s in zip(xy, mv.station)], xy, fig.dpi)
    scalebar(ax, ext, km=10)
    colorbar(fig, ax, cmap, norm)
    n = mv.glass.value_counts()
    ax.set_title(f"MV1007 (2010) dredges D01-D47: {n.get('glass', 0)} with glass, {n.get('no glass', 0)} without, "
                 f"{n.get('no rock', 0)} no rock", fontsize=11.5, fontweight="bold", loc="left")
    h = [Line2D([], [], marker="o", ls="", mfc="#ff7f00", mec="k", ms=8, label="glass recovered"),
         Line2D([], [], marker="o", ls="", mfc="white", mec="#ff7f00", mew=1.6, ms=8, label="no glass"),
         Line2D([], [], marker="x", ls="", color="#ff7f00", mew=1.6, ms=8, label="no rock recovered"),
         Line2D([], [], color="#ff7f00", lw=2.2, path_effects=[pe.Stroke(linewidth=3.6, foreground="k"), pe.Normal()],
                label="dredge track, on-bottom (marker) to off-bottom"),
         Line2D([], [], color="k", lw=0.4, alpha=0.5, label="500 m isobaths")]
    fig.legend(handles=h, loc="upper left", bbox_to_anchor=(0.06, (1.1 - 0.35) / H), ncol=3, fontsize=8, frameon=False)
    fig.text(0.06, 0.012, "Positions from MV1007_DredgeLog_167_2358_KL Edited.xls (D14 off-bottom longitude corrected, "
             "see Previous_Dredges_Compiled.csv). Glass class from the sample spreadsheet; D05/D06 from the log.",
             fontsize=7, color="0.3")
    save(fig, "MV1007_Dredges_Map")


def map_regional(prev, permit):
    ext = [-102.2, -82.55, -0.4, 3.4]
    glon, glat, gz = load_grid(WIDE, (ext[0] - 0.1, ext[1] + 0.1, ext[2] - 0.1, ext[3] + 0.1))
    fig, ax, H = figure_for(ext, width=15.0, band=1.35)
    cmap, norm = bathy(ax, glon, glat, gz, ext, vrange=(1000, 3500))
    reserve_outlines(ax)
    draw_previous(ax, prev, size=40)
    ax.scatter(permit.longitude, permit.latitude, s=45, marker="^", c=PERMIT_C, edgecolors="k", linewidths=0.6,
               transform=PC, zorder=6)
    scalebar(ax, ext, km=100, corner="ll")
    colorbar(fig, ax, cmap, norm)
    ax.set_title(f"Previous dredges by cruise ({len(prev)} stations) and AT53-04 permit dredge sites (red)",
                 fontsize=12, fontweight="bold", loc="left")
    h = legend_handles(set(prev.cruise)) + [Line2D([], [], color="#00a651", ls="--", lw=1.1, label="Galapagos MR"),
                                            Line2D([], [], color="#e7298a", ls="-.", lw=0.9, label="Hermandad MR")]
    fig.legend(handles=h, loc="upper left", bbox_to_anchor=(0.06, (1.35 - 0.35) / H), ncol=6, fontsize=8, frameon=False)
    fig.text(0.06, 0.012, "Basemap: GMRT (~1 km). ST7-17D-1G and NA063-002 longitudes corrected from east to west "
             "(sign typo in the sample spreadsheet; see Previous_Dredges_Compiled.csv).", fontsize=7.5, color="0.3")
    save(fig, "Previous_Dredges_Regional_Map")


GLASS_C = {"glass": "#1a9850", "no glass": "#d9d9d9", "no rock": "k"}
CUT50 = ROOT / "FOR_TUSHAR/CUT_bath_clean.tif"
ZOOMS = [  # name, permit sites included, title
    ("A_South_WDL", range(1, 7), "South Wolf-Darwin lineament (D1-D6)"),
    ("B_Wolf_Darwin_NW", range(7, 17), "Wolf-Darwin to the NW (D7-D16)"),
    ("C_Central_Lineament", range(17, 21), "Central lineament (D17-D20)"),
    ("D_East_Lineament", range(21, 31), "Eastern lineament (D21-D30)"),
]


def short_id(r):
    return f"MV-{r.station}" if r.cruise == "MV1007" else r.station


def draw_previous_glass(ax, p, size=46, zorder=4):
    """Marker shape = cruise, fill colour = recovery class."""
    for r in p.itertuples():
        mk = "X" if r.glass == "no rock" else CRUISES[r.cruise][0]
        ax.scatter([r.lon], [r.lat], s=size * (0.9 if r.glass == "no rock" else 1), marker=mk,
                   c=GLASS_C[r.glass], edgecolors="k" if r.glass != "no rock" else "w",
                   linewidths=0.8 if r.glass != "no rock" else 0.6, transform=PC, zorder=zorder)


def glass_handles(cruises_present):
    h = [Line2D([], [], marker="^", ls="", mfc=PERMIT_C, mec="k", ms=10, label="AT53-04 permit dredge site"),
         Line2D([], [], marker="o", ls="", mfc=GLASS_C["glass"], mec="k", ms=8, label="previous dredge: glass recovered"),
         Line2D([], [], marker="o", ls="", mfc=GLASS_C["no glass"], mec="k", ms=8, label="previous dredge: no glass"),
         Line2D([], [], marker="X", ls="", mfc="k", mec="w", ms=8, label="previous dredge: no rock")]
    for c in CRUISES:
        if c in cruises_present:
            h.append(Line2D([], [], marker=CRUISES[c][0], ls="", mfc="white", mec="k", ms=7,
                            label=f"shape: {CRUISES[c][2]}"))
    return h


def overlay_cut50(ax, cmap, norm, extent):
    """Overlay the 50 m Mittelstaedt/Young grid (where it has data) on the GMRT basemap."""
    import rasterio
    from rasterio.windows import from_bounds
    with rasterio.open(CUT50) as ds:
        b = ds.bounds
        x0, x1 = max(extent[0], b.left), min(extent[1], b.right)
        y0, y1 = max(extent[2], b.bottom), min(extent[3], b.top)
        if x0 >= x1 or y0 >= y1:
            return False
        w = from_bounds(x0, y0, x1, y1, ds.transform)
        z = ds.read(1, window=w).astype(np.float32)
        wt = ds.window_transform(w)
    z[(np.abs(z) > 1e4)] = np.nan
    if not np.isfinite(z).any():
        return False
    dx, dy = abs(wt.a) * 111_320 * np.cos(np.radians((y0 + y1) / 2)), abs(wt.e) * 111_320
    hs = LightSource(azdeg=315, altdeg=40).hillshade(np.nan_to_num(z, nan=np.nanmedian(z)), vert_exag=3, dx=dx, dy=dy)
    depth = -z
    rgba = cmap(norm(np.clip(depth, norm.vmin, norm.vmax)))
    rgba[..., :3] *= (0.6 + 0.4 * hs[..., None])
    rgba[depth <= 0, :3] = np.array([0.72, 0.68, 0.60]) * (0.6 + 0.4 * hs[depth <= 0, None])
    rgba[..., 3] = np.isfinite(z)
    ext_img = [wt.c, wt.c + wt.a * z.shape[1], wt.f + wt.e * z.shape[0], wt.f]
    ax.imshow(np.clip(rgba, 0, 1), extent=ext_img, origin="upper", transform=PC, interpolation="bilinear", zorder=0.5)
    return True


def map_glass_status(permit, prev):
    ext = pad_extent(permit.longitude, permit.latitude)
    inside = prev[(prev.lon.between(ext[0], ext[1])) & (prev.lat.between(ext[2], ext[3]))]
    glon, glat, gz = load_gmrt((ext[0] - 0.05, ext[1] + 0.05, ext[2] - 0.05, ext[3] + 0.05))
    fig, ax, H = figure_for(ext, band=1.55)
    cmap, norm = bathy(ax, glon, glat, gz, ext, vrange=site_depth_range(permit.depth, inside.depth, pct=(5, 95)))
    reserve_outlines(ax)
    ax.plot(permit.longitude, permit.latitude, color=PERMIT_C, lw=0.9, alpha=0.8, transform=PC, zorder=3)
    draw_previous_glass(ax, inside)
    ax.scatter(permit.longitude, permit.latitude, s=85, marker="^", c=PERMIT_C, edgecolors="k", linewidths=0.9,
               transform=PC, zorder=6)
    fig.canvas.draw()
    tr = PC._as_mpl_transform(ax)
    xy = tr.transform(np.column_stack([permit.longitude, permit.latitude]))
    obst = np.vstack([xy, tr.transform(np.column_stack([inside.lon, inside.lat]))])
    place_labels(ax, [(x, y, f"D{s}", 8, "#67000d") for (x, y), s in zip(xy, permit.site)], obst, fig.dpi)
    scalebar(ax, ext)
    colorbar(fig, ax, cmap, norm)
    n = inside.glass.value_counts()
    ax.set_title(f"Permit dredge sites vs previous dredges by recovery: {n.get('glass', 0)} with glass, "
                 f"{n.get('no glass', 0)} without, {n.get('no rock', 0)} no rock", fontsize=11.5, fontweight="bold",
                 loc="left")
    fig.legend(handles=glass_handles(set(inside.cruise)), loc="upper left", bbox_to_anchor=(0.06, (1.55 - 0.35) / H),
               ncol=4, fontsize=7.5, frameon=False)
    save(fig, "Dredge_Glass_Status_Map")


def map_zooms(permit, prev):
    pages = []
    for name, rng, title in ZOOMS:
        sel = permit[permit.site.isin(list(rng))]
        ext = pad_extent(sel.longitude, sel.latitude, 0.07, 0.06)
        # expand (never shrink) the shorter side so width/height stays between 1.0 and 1.5
        w, h = ext[1] - ext[0], ext[3] - ext[2]
        if w < 1.0 * h:
            d = (1.0 * h - w) / 2; ext = [ext[0] - d, ext[1] + d, ext[2], ext[3]]
        elif w > 1.5 * h:
            d = (w / 1.5 - h) / 2; ext = [ext[0], ext[1], ext[2] - d, ext[3] + d]
        assert (sel.longitude.between(ext[0], ext[1]) & sel.latitude.between(ext[2], ext[3])).all()
        inside = prev[(prev.lon.between(ext[0], ext[1])) & (prev.lat.between(ext[2], ext[3]))]
        oth = permit[(permit.longitude.between(ext[0], ext[1])) & (permit.latitude.between(ext[2], ext[3]))]
        glon, glat, gz = load_gmrt((ext[0] - 0.05, ext[1] + 0.05, ext[2] - 0.05, ext[3] + 0.05))
        fig, ax, H = figure_for(ext, width=10.0, band=1.4)
        cmap, norm = bathy(ax, glon, glat, gz, ext, vrange=site_depth_range(oth.depth, inside.depth))
        has50 = overlay_cut50(ax, cmap, norm, ext)
        reserve_outlines(ax)
        ax.plot(permit.longitude, permit.latitude, color=PERMIT_C, lw=1.0, alpha=0.8, transform=PC, zorder=3)
        mvi = inside[inside.cruise == "MV1007"]
        for r in mvi.itertuples():
            if pd.notna(r.off_lon):
                ax.plot([r.lon, r.off_lon], [r.lat, r.off_lat], color="k", lw=1.6, transform=PC, zorder=3.5)
        ax.scatter(oth.longitude, oth.latitude, s=120, marker="^", c=PERMIT_C, edgecolors="k", linewidths=1.0,
                   transform=PC, zorder=5)
        draw_previous_glass(ax, inside, size=70, zorder=6)  # on top, so a co-located old dredge stays visible
        fig.canvas.draw()
        tr = PC._as_mpl_transform(ax)
        pxy = tr.transform(np.column_stack([oth.longitude, oth.latitude]))
        qxy = tr.transform(np.column_stack([inside.lon, inside.lat])) if len(inside) else np.zeros((0, 2))
        items = [(x, y, f"D{s}", 9.5, "#67000d") for (x, y), s in zip(pxy, oth.site)]
        items += [(x, y, short_id(r), 7, "k") for (x, y), r in zip(qxy, inside.itertuples())]
        place_labels(ax, items, np.vstack([pxy, qxy]), fig.dpi)
        km = 10 if (ext[1] - ext[0]) < 0.8 else 25
        scalebar(ax, ext, km=km)
        colorbar(fig, ax, cmap, norm)
        ax.set_title(f"Zoom {title}: permit sites and {len(inside)} previous dredges", fontsize=11.5,
                     fontweight="bold", loc="left")
        h = glass_handles(set(inside.cruise)) + [Line2D([], [], color="k", lw=1.6, label="MV1007 dredge track")]
        fig.legend(handles=h, loc="upper left", bbox_to_anchor=(0.06, (1.4 - 0.3) / H), ncol=4, fontsize=7.5,
                   frameon=False)
        fig.text(0.06, 0.012, ("Bathymetry: 50 m Mittelstaedt/Young compilation where available, GMRT (~245 m) "
                               "elsewhere.\n" if has50 else "Bathymetry: GMRT (~245 m).\n") +
                 "Previous-dredge labels: MV- = MV1007; DR = SO158; PL02 = PLUME02; TR164/CTW = GSC cruises.",
                 fontsize=7, color="0.3")
        fig.savefig(HERE / f"Dredge_Zoom_{name}.png", dpi=200)
        with PdfPages(HERE / f"Dredge_Zoom_{name}.pdf") as pdf:
            pdf.savefig(fig)
        pages.append(fig)
    with PdfPages(HERE / "Dredge_Zooms_All.pdf") as pdf:
        for f in pages:
            pdf.savefig(f)
    for f in pages:
        plt.close(f)


def nearest_table(permit, prev):
    rows = []
    for r in permit.itertuples():
        d = haversine_km(r.latitude, r.longitude, prev.lat.values, prev.lon.values)
        k = int(np.argmin(d))
        g = prev.glass.values == "glass"
        kg = int(np.flatnonzero(g)[np.argmin(d[g])])
        rows.append(dict(permit_site=f"D{r.site}", lat=r.latitude, lon=r.longitude, depth_m=r.depth,
                         nearest_prev=f"{prev.cruise.iloc[k]} {prev.station.iloc[k]}", nearest_prev_km=round(d[k], 1),
                         nearest_prev_class=prev.glass.iloc[k],
                         nearest_glass_prev=f"{prev.cruise.iloc[kg]} {prev.station.iloc[kg]}",
                         nearest_glass_prev_km=round(d[kg], 1)))
    t = pd.DataFrame(rows)
    t.to_csv(HERE / "Permit_Sites_Nearest_Previous.csv", index=False)
    return t


def main():
    prev, mv = compile_previous()
    prev.to_csv(HERE / "Previous_Dredges_Compiled.csv", index=False)
    permit = pd.read_csv(SITES / "DredgeSites.csv")
    inside = map_permit_with_previous(permit, prev)
    map_mv1007(mv)
    map_regional(prev, permit)
    map_glass_status(permit, prev)
    map_zooms(permit, prev)
    nt = nearest_table(permit, prev)
    print(nt.to_string(index=False))
    print(f"{len(prev)} previous dredge stations; by cruise and class:")
    print(prev.groupby(["cruise", "glass"]).size().unstack(fill_value=0).to_string())
    print(f"previous stations inside the permit-site map: {len(inside)}")
    print("notes:", prev.loc[prev.note != "", ["cruise", "station", "note"]].to_string(index=False))


if __name__ == "__main__":
    main()
