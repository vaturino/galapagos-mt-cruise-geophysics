"""
Highlight the permit dredge sites that nearly repeat a poor MV1007 dredge (no glass or no rock
within REPEAT_KM), for deciding targets before the cruise. One detailed panel per flagged
site plus a locator panel.

For each flagged site the panel shows the permit site and its planned depth, the MV1007
dredge track it repeats with its logged recovery and description, every other previous
dredge within the panel by recovery class, and the distance to the nearest previous dredge
that recovered glass. Bathymetry: 50 m Mittelstaedt/Young where available, GMRT elsewhere.

Outputs (Site_Maps/): Repeat_Dredge_Sites_Map.{png,pdf}, Repeat_Dredge_Sites.csv

    source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env
    cd Site_Maps && python make_repeat_site_map.py
"""
import textwrap

import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D

from make_previous_dredge_maps import (GLASS_C, PERMIT_C, bathy, colorbar, compile_previous, draw_previous_glass,
                                       overlay_cut50, short_id)
from make_site_maps import HERE, PC, SITES, haversine_km, load_gmrt, place_labels

REPEAT_KM = 2.0     # permit site within this distance of a poor MV1007 dredge -> flagged
HALF_DEG = 0.055    # panel half-width (~6 km)


def seg_dist_km(la, lo, a_la, a_lo, b_la, b_lo):
    """Distance from a point to the on->off-bottom segment (local flat approximation)."""
    k = np.cos(np.radians(la)) * 111.32
    p = np.array([(lo - a_lo) * k, (la - a_la) * 111.32])
    v = np.array([(b_lo - a_lo) * k, (b_la - a_la) * 111.32])
    t = 0.0 if not v.any() else np.clip(p @ v / (v @ v), 0, 1)
    return float(np.hypot(*(p - t * v)))


def flagged_sites(permit, prev):
    mv = prev[(prev.cruise == "MV1007") & (prev.glass != "glass")]
    glass = prev[prev.glass == "glass"]
    rows = []
    for r in permit.itertuples():
        d = haversine_km(r.latitude, r.longitude, mv.lat.values, mv.lon.values)
        k = int(np.argmin(d))
        if d[k] > REPEAT_KM:
            continue
        m = mv.iloc[k]
        dg = haversine_km(r.latitude, r.longitude, glass.lat.values, glass.lon.values)
        g = glass.iloc[int(np.argmin(dg))]
        rows.append(dict(permit_site=f"D{r.site}", lat=r.latitude, lon=r.longitude, depth_m=int(r.depth),
                         mv1007=m.station, mv_class=m.glass, mv_depth_m=int(m.depth),
                         dist_onbottom_km=round(d[k], 2),
                         dist_track_km=round(seg_dist_km(r.latitude, r.longitude, m.lat, m.lon, m.off_lat, m.off_lon), 2),
                         mv_location=m.location, mv_recovery=m.recovery, mv_description=m.description,
                         nearest_glass=f"{g.cruise} {g.station}", nearest_glass_km=round(float(dg.min()), 1),
                         nearest_glass_lat=g.lat, nearest_glass_lon=g.lon))
    return pd.DataFrame(rows)


def main():
    prev, _ = compile_previous()
    permit = pd.read_csv(SITES / "DredgeSites.csv")
    fl = flagged_sites(permit, prev)
    fl.to_csv(HERE / "Repeat_Dredge_Sites.csv", index=False)
    print(fl[["permit_site", "mv1007", "mv_class", "dist_onbottom_km", "dist_track_km", "depth_m", "mv_depth_m",
              "nearest_glass", "nearest_glass_km"]].to_string(index=False))

    n = len(fl)
    fig = plt.figure(figsize=(17, 7.8))
    W, GAP, X0 = 0.205, 0.045, 0.045
    axes = [fig.add_axes([X0 + i * (W + GAP), 0.36, W, 0.54], projection=PC) for i in range(n)]
    loc = fig.add_axes([X0 + n * (W + GAP), 0.36, 0.19, 0.54], projection=PC)
    for ax, r in zip(axes, fl.itertuples()):
        ext = [r.lon - HALF_DEG, r.lon + HALF_DEG, r.lat - HALF_DEG, r.lat + HALF_DEG]
        glon, glat, gz = load_gmrt((ext[0] - 0.02, ext[1] + 0.02, ext[2] - 0.02, ext[3] + 0.02))
        z0 = -gz[(glat[:, None] >= ext[2]) & (glat[:, None] <= ext[3]) & np.ones_like(gz, bool)]
        cmap, norm = bathy(ax, glon, glat, gz, ext, vrange=(np.floor(np.nanpercentile(z0, 1) / 100) * 100,
                                                          np.ceil(np.nanpercentile(z0, 99) / 100) * 100))
        overlay_cut50(ax, cmap, norm, ext)
        inside = prev[prev.lon.between(ext[0], ext[1]) & prev.lat.between(ext[2], ext[3])]
        for q in inside[inside.cruise == "MV1007"].itertuples():
            ax.plot([q.lon, q.off_lon], [q.lat, q.off_lat], color="k", lw=2.2, transform=PC, zorder=3.5,
                    path_effects=[pe.Stroke(linewidth=3.8, foreground="w"), pe.Normal()])
        # link to the repeated MV1007 dredge, with distance
        m = prev[(prev.cruise == "MV1007") & (prev.station == r.mv1007)].iloc[0]
        ax.plot([r.lon, m.lon], [r.lat, m.lat], color="#ffff33", lw=1.6, ls="--", transform=PC, zorder=4)
        ax.scatter([r.lon], [r.lat], s=260, marker="^", c=PERMIT_C, edgecolors="k", linewidths=1.3, transform=PC,
                   zorder=6)
        ax.scatter([r.lon], [r.lat], s=520, marker="o", facecolors="none", edgecolors="#ffff33", linewidths=2.0,
                   transform=PC, zorder=5.5)
        draw_previous_glass(ax, inside, size=110, zorder=7)
        others = permit[permit.longitude.between(ext[0], ext[1]) & permit.latitude.between(ext[2], ext[3])
                        & (permit.site != int(r.permit_site[1:]))]
        ax.scatter(others.longitude, others.latitude, s=150, marker="^", c=PERMIT_C, edgecolors="k", transform=PC,
                   zorder=6)
        fig.canvas.draw()
        tr = PC._as_mpl_transform(ax)
        pts = [(r.lon, r.lat, f"{r.permit_site} ({r.depth_m} m)", 10, "#67000d")]
        pts += [(o.longitude, o.latitude, f"D{o.site}", 9, "#67000d") for o in others.itertuples()]
        pts += [(q.lon, q.lat, f"{short_id(q)} ({int(q.depth)} m)", 8, "k") for q in inside.itertuples()]
        xy = tr.transform(np.array([(a, b) for a, b, *_ in pts]))
        place_labels(ax, [(x, y, t, fs, c) for (x, y), (_, _, t, fs, c) in zip(xy, pts)], xy, fig.dpi)
        # 2 km scale bar
        dlo = 2.0 / (111.32 * np.cos(np.radians(r.lat)))
        x0, y0 = ext[0] + 0.006, ext[2] + 0.008
        ax.plot([x0, x0 + dlo], [y0, y0], color="k", lw=3, transform=PC, zorder=8,
                path_effects=[pe.Stroke(linewidth=5, foreground="w"), pe.Normal()])
        ax.text(x0 + dlo / 2, y0 + 0.004, "2 km", ha="center", fontsize=8, transform=PC, zorder=8,
                path_effects=[pe.withStroke(linewidth=2.5, foreground="w")])
        cls = {"no glass": "NO GLASS", "no rock": "NO ROCK"}[r.mv_class]
        ax.set_title(f"{r.permit_site}: {r.dist_onbottom_km:.1f} km from MV1007 {r.mv1007} ({cls})"
                     + (f"\n{r.dist_track_km:.2f} km from its dredge track" if r.dist_track_km < r.dist_onbottom_km - 0.05 else ""),
                     fontsize=11, fontweight="bold", loc="left", color="#67000d")
        info = (f"MV1007 {r.mv1007}: {r.mv_location}. Recovery: {r.mv_recovery}; {r.mv_description}. "
                f"Depth {r.mv_depth_m} m (permit site {r.depth_m} m). "
                f"Nearest glass-bearing dredge: {r.nearest_glass}, {r.nearest_glass_km:.1f} km.")
        bb = ax.get_position()
        fig.text(bb.x0, bb.y0 - 0.125, "\n".join(textwrap.wrap(info, 55)), fontsize=8.3, va="top")
        cb_ax = fig.add_axes([bb.x0 + 0.02, bb.y0 - 0.06, bb.width - 0.04, 0.012])
        cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), cax=cb_ax, orientation="horizontal",
                          extend="both")
        cb.ax.tick_params(labelsize=7)
        cb.set_label("depth (m)", fontsize=7, labelpad=1)
    # locator
    lext = [permit.longitude.min() - 0.15, permit.longitude.max() + 0.15,
            permit.latitude.min() - 0.12, permit.latitude.max() + 0.12]
    glon, glat, gz = load_gmrt((lext[0] - 0.05, lext[1] + 0.05, lext[2] - 0.05, lext[3] + 0.05))
    bathy(loc, glon, glat, gz, lext)
    loc.plot(permit.longitude, permit.latitude, color=PERMIT_C, lw=0.8, transform=PC, zorder=3)
    loc.scatter(permit.longitude, permit.latitude, s=22, marker="^", c=PERMIT_C, edgecolors="k", linewidths=0.4,
                transform=PC, zorder=4)
    for r in fl.itertuples():
        loc.scatter([r.lon], [r.lat], s=260, marker="o", facecolors="none", edgecolors="#ffff33", linewidths=2.2,
                    transform=PC, zorder=5)
        loc.text(r.lon + 0.06, r.lat, r.permit_site, transform=PC, fontsize=9, fontweight="bold", color="#67000d",
                 va="center", zorder=6, path_effects=[pe.withStroke(linewidth=2.5, foreground="w")])
    loc.set_title("Where they are (all 30 permit sites)", fontsize=10, loc="left")
    fig.suptitle(f"Permit dredge sites within {REPEAT_KM:.0f} km of an MV1007 dredge that recovered no glass or no rock",
                 fontsize=13, fontweight="bold", x=0.03, ha="left", y=0.985)
    h = [Line2D([], [], marker="^", ls="", mfc=PERMIT_C, mec="k", ms=11, label="AT53-04 permit site (flagged: yellow ring)"),
         Line2D([], [], marker="o", ls="", mfc=GLASS_C["glass"], mec="k", ms=9, label="previous dredge: glass"),
         Line2D([], [], marker="o", ls="", mfc=GLASS_C["no glass"], mec="k", ms=9, label="previous dredge: no glass"),
         Line2D([], [], marker="X", ls="", mfc="k", mec="w", ms=9, label="previous dredge: no rock"),
         Line2D([], [], color="k", lw=2.2, label="MV1007 dredge track (on- to off-bottom)"),
         Line2D([], [], color="#ffff33", lw=1.6, ls="--", label="link to the repeated MV1007 dredge (distance in title)")]
    fig.legend(handles=h, loc="lower left", bbox_to_anchor=(0.03, 0.005), ncol=6, fontsize=8, frameon=False)
    fig.savefig(HERE / "Repeat_Dredge_Sites_Map.png", dpi=200)
    with PdfPages(HERE / "Repeat_Dredge_Sites_Map.pdf") as pdf:
        pdf.savefig(fig)
    plt.close(fig)


if __name__ == "__main__":
    main()
