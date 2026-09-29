"""
Compare the permit dredge sites (the table in the submitted DPNG proposal,
FORMATO_PROPUESTA_INVESTIGACION = Combined_Cruise_Sites_Dredge.csv = MT_dredging_coords/DredgeSites.csv
since 2026-09-29) with the alternative list that DredgeSites.csv held briefly on 2026-09-29, now
MT_dredging_coords/DredgeSites_Sep29_alternative.csv (NOT used).
Writes Dredge_Permit_vs_Sep29_Comparison.png and Dredge_Permit_vs_Sep29_Comparison.csv.

    source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env
    cd Site_Maps && python compare_dredge_lists.py
"""
import geopandas as gpd
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

from make_site_maps import HERE, PC, RES, SITES, basemap, haversine_km, load_gmrt, reserves, scalebar

PERMIT_CSV = "/home/tmittal/Dropbox/Work_AI/Permits/1_Dredging/3_sites/Combined_Cruise_Sites_Dredge.csv"


def main():
    perm = pd.read_csv(PERMIT_CSV).rename(columns={"Latitude": "lat", "Longitude": "lon", "Depth_m": "depth"})
    perm["depth"] = perm.depth.abs()
    new = pd.read_csv(SITES / "DredgeSites_Sep29_alternative.csv").rename(columns={"latitude": "lat", "longitude": "lon"})
    d = haversine_km(new.lat.values[:, None], new.lon.values[:, None], perm.lat.values[None], perm.lon.values[None])
    near = d.argmin(1)
    out = pd.DataFrame({"sep29_site": [f"D{s}" for s in new.site], "sep29_lat": new.lat, "sep29_lon": new.lon,
                        "nearest_permit_site": [f"P{k + 1}" for k in near],
                        "permit_lat": perm.lat.values[near].round(5), "permit_lon": perm.lon.values[near].round(5),
                        "distance_km": d.min(1).round(1)})
    out.to_csv(HERE / "Dredge_Permit_vs_Sep29_Comparison.csv", index=False)

    rmg, rmh = gpd.read_file(RES / "RMG.shp"), gpd.read_file(RES / "RMH.shp")
    ext = [-92.40, -90.55, 0.45, 2.22]
    glon, glat, gz = load_gmrt((ext[0] - 0.1, ext[1] + 0.1, ext[2] - 0.1, ext[3] + 0.1))
    W = 11.0
    mh = W * 0.9 * (ext[3] - ext[2]) / (ext[1] - ext[0])
    H = mh + 1.4
    fig = plt.figure(figsize=(W, H))
    ax = fig.add_axes([0.07, 1.0 / H, 0.9, mh / H], projection=PC)
    basemap(ax, glon, glat, gz, ext)
    site_ll = np.vstack([np.column_stack([perm.lon, perm.lat]), np.column_stack([new.lon, new.lat])])
    reserves(ax, rmg, rmh, ext, site_ll)
    for k in range(len(new)):  # link each Sep-29 site to its nearest permit site
        ax.plot([new.lon[k], perm.lon[near[k]]], [new.lat[k], perm.lat[near[k]]], color="w", lw=0.9, ls=":",
                transform=PC, zorder=4)
    ax.scatter(perm.lon, perm.lat, s=90, marker="o", c="#1a9850", edgecolors="k", linewidths=0.9, transform=PC, zorder=5)
    ax.scatter(new.lon, new.lat, s=70, marker="^", c="#d7301f", edgecolors="k", linewidths=0.8, transform=PC, zorder=5)
    halo = [pe.withStroke(linewidth=2.3, foreground="w")]
    for i, r in perm.iterrows():
        ax.annotate(f"P{i + 1}", (r.lon, r.lat), xycoords=PC._as_mpl_transform(ax), xytext=(-9, 6),
                    textcoords="offset points", fontsize=7, fontweight="bold", color="#00441b", ha="right",
                    zorder=7, path_effects=halo)
    for i, r in new.iterrows():
        ax.annotate(f"D{int(r.site)}", (r.lon, r.lat), xycoords=PC._as_mpl_transform(ax), xytext=(8, -8),
                    textcoords="offset points", fontsize=7, fontweight="bold", color="#67000d",
                    zorder=7, path_effects=halo)
    scalebar(ax, ext)
    dmin = d.min(1)
    ax.set_title("Dredge sites: PERMIT list (green, P1-P30, USED) vs 29-Sep alternative (red, NOT used)\n"
                 f"No red site is within 2 km of a permit site; nearest-permit-site distance median "
                 f"{np.median(dmin):.0f} km, range {dmin.min():.0f}-{dmin.max():.0f} km",
                 fontsize=11, fontweight="bold", loc="left")
    h = [Line2D([], [], marker="o", ls="", mfc="#1a9850", mec="k", ms=9,
                label="Permit sites: DPNG proposal table = Permits/Combined_Cruise_Sites_Dredge.csv"),
         Line2D([], [], marker="^", ls="", mfc="#d7301f", mec="k", ms=9,
                label="DredgeSites_Sep29_alternative.csv (not used)"),
         Line2D([], [], color="0.4", ls=":", lw=1, label="Each red site -> its nearest permit site"),
         Line2D([], [], color="#00843d", ls="--", lw=1.4, label="Galapagos Marine Reserve"),
         Line2D([], [], color="#54278f", ls=":", lw=1.4, label="Hermandad MR (No-Take pink, Resp.-Fishing purple)")]
    fig.legend(handles=h, loc="lower left", bbox_to_anchor=(0.07, 0.02), ncol=2, fontsize=7.5, frameon=False)
    fig.savefig(HERE / "Dredge_Permit_vs_Sep29_Comparison.png", dpi=200)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
