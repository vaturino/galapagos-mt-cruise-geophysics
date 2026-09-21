#!/usr/bin/env python3
"""
build_geophysics_drape.py -- turn a non-bathymetry geophysics grid (gravity,
magnetics, crustal thickness, thermal anomaly -- anything on its own native
lon/lat grid where the "z" values are NOT elevation) into the same
mesh.bin/meta.json format Viewer3D/app.js already loads, so it shows up as
just another toggleable layer alongside the bathymetry/backscatter datasets.

Why this can't just reuse build_cesium_mesh.py directly: that script always
triangulates a bathymetry grid and colours it by ITS OWN z values (depth) or
a co-registered backscatter grid sampled onto the same posts. None of the
GMRT_regional/Geophysics_MGDS grids are co-registered with the Galapagos
bathymetry -- each covers its own separate footprint, generally not even the
full GMRT_basemap corridor -- so there's no "parent" bathymetry grid to
triangulate here. Instead this script:

  1. triangulates the geophysics grid's OWN native (lon, lat) posts (reusing
     build_cesium_mesh.decimate_one_grid, which crops to the grid's tight
     finite-data bbox and decimates -- the "z" it returns is, for a
     geophysics file, the geophysics VALUE, not a depth);
  2. looks up a DISPLAY elevation for each of those vertices from a separate
     reference terrain grid (the GMRT regional basemap, by default) via
     nearest-neighbour lon/lat lookup -- the same matching technique
     build_cesium_mesh.py already uses to drop a backscatter mosaic onto its
     own bathymetry -- purely so the layer drapes over real seafloor shape
     instead of floating on a flat plane;
  3. colours each vertex by the geophysics VALUE (not the terrain height)
     using either a diverging ramp (blue-white-red, symmetric about zero --
     appropriate for anomaly fields: magnetics, gravity anomalies, thermal
     anomaly) or a sequential ramp (plain low->high -- appropriate for a
     magnitude field like crustal thickness), both written with
     "domain": "relative_0_1" so the *existing* app.js legend/colour-sampling
     code renders them with no changes to that machinery. (Two small,
     additive app.js changes were still needed so the legend shows the
     correct units/range/kind for a geophysics layer instead of the terrain
     elevation range with a hardcoded "m" -- see README_Viewer3D.md.)

LIMITATION: several of these geophysics grids extend outside the GMRT
regional basemap's own coverage (e.g. Bassett's residual-gravity grid runs
west to 111W, SR1806's grids run west to 105W, both well past the basemap's
98.5W edge). Vertices outside the terrain grid's bbox get the terrain's
nearest EDGE elevation (clamped, not extrapolated) -- those areas will drape
onto a flat shelf at the edge elevation rather than real seafloor shape.
The geophysics VALUE colouring itself is unaffected; only the 3D height is
approximate in those areas. This is noted per-layer in meta.json
("terrain_clamped_frac") and summarised in README_Viewer3D.md.

Usage:
  python3 build_geophysics_drape.py \\
      --value GMRT_regional/Geophysics_MGDS/Barckhausen_CentralAmerica_Magnetics/central_america_mag_geomapapp.grd \\
      --terrain GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap.grd \\
      --out-dir Viewer3D/data/Geophys_Barckhausen_Magnetics \\
      --label "Barckhausen magnetics (Central America)" \\
      --legend-kind magnetics --units nT --ramp diverging
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_cesium_mesh import (  # noqa: E402
    read_grd,
    tight_bbox,
    scan_populated,
    choose_stride,
    decimate_one_grid,
    geodetic_to_ecef,
    compute_smooth_normals,
    apply_colormap,
    log,
)


def build_diverging_colormap():
    # symmetric about t=0.5 (value=0): blue (negative) -> near-white (zero)
    # -> red (positive). Distinct hue family from the depth/backscatter/globe
    # ramps so a geophysics layer never reads as "more bathymetry".
    stops = np.array(
        [
            [0.00, 30, 60, 160],
            [0.25, 100, 140, 210],
            [0.50, 245, 245, 240],
            [0.75, 214, 120, 60],
            [1.00, 150, 30, 20],
        ],
        dtype=np.float64,
    )
    return stops


def build_sequential_colormap():
    # plain low(dark purple) -> high(warm yellow) magnitude ramp -- for
    # fields that aren't signed anomalies (e.g. crustal thickness).
    stops = np.array(
        [
            [0.00, 40, 10, 80],
            [0.25, 90, 40, 130],
            [0.50, 180, 70, 100],
            [0.75, 230, 130, 60],
            [1.00, 250, 220, 90],
        ],
        dtype=np.float64,
    )
    return stops


def sample_terrain(lon_v_deg, lat_v_deg, terrain_path):
    """Nearest-neighbour elevation lookup via point-wise gather on the
    (memmapped) terrain grid -- only touches the queried cells, never
    materialises the whole grid. Returns (elev_m, clamped_frac)."""
    tx, ty, tz, tnc = read_grd(terrain_path)
    t_dx = tx[1] - tx[0]
    t_dy = ty[1] - ty[0]
    tny, tnx = tz.shape
    raw_col = np.round((lon_v_deg - tx[0]) / t_dx).astype(np.int64)
    raw_row = np.round((lat_v_deg - ty[0]) / t_dy).astype(np.int64)
    col_idx = np.clip(raw_col, 0, tnx - 1)
    row_idx = np.clip(raw_row, 0, tny - 1)
    clamped = (raw_col != col_idx) | (raw_row != row_idx)
    elev = np.asarray(tz[row_idx, col_idx]).astype(np.float64)
    tnc.close()
    return elev, float(clamped.mean())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--value", required=True, help="geophysics .grd whose z/altitude values are the quantity to colour by (not elevation)")
    ap.add_argument("--terrain", required=True, help="reference bathymetry/topography .grd used only to look up a display elevation for draping")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--max-triangles", type=int, default=600_000,
                     help="target triangle budget after decimation (default 600k -- geophysics grids are usually much smaller than the bathymetry basemap)")
    ap.add_argument("--stride", type=int, help="override auto-picked decimation stride")
    ap.add_argument("--label", required=True, help="display label in the viewer's dataset list")
    ap.add_argument("--legend-kind", required=True, help="short word shown in the legend title, e.g. 'magnetics', 'gravity', 'crustal thickness'")
    ap.add_argument("--units", required=True, help="unit string shown on the legend range labels, e.g. 'nT', 'mGal', 'km', degC")
    ap.add_argument("--ramp", choices=["diverging", "sequential"], required=True,
                     help="'diverging': blue-white-red, symmetric about zero -- for signed anomaly fields. "
                          "'sequential': dark-to-bright low->high -- for magnitude fields (e.g. crustal thickness).")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"scanning {Path(args.value).name} for coverage/stride")
    x, y, z, nc = read_grd(args.value)
    r0, r1, c0, c1 = tight_bbox(z)
    populated, vmin_raw, vmax_raw = scan_populated(z, r0, r1, c0, c1)
    dx_deg = abs(x[1] - x[0])
    log(f"  {len(x)}x{len(y)} grid, {populated:,} populated cells, value range [{vmin_raw:.2f}, {vmax_raw:.2f}]")
    nc.close()

    stride = args.stride or choose_stride(populated, args.max_triangles)
    eff_res_m = dx_deg * stride * 111320
    log(f"  stride={stride} -> effective resolution ~{eff_res_m:.0f} m (native was ~{dx_deg*111320:.0f} m)")

    log("decimating + triangulating geophysics grid's own footprint")
    lon_v, lat_v, value_v, tris, _, _ = decimate_one_grid(args.value, stride, positive_down=False)
    n_vert = lon_v.shape[0]
    log(f"  {n_vert:,} vertices, {tris.shape[0]:,} triangles")
    if n_vert == 0:
        log("  no surviving vertices at this stride -- aborting, nothing written")
        return

    lon_deg = np.degrees(lon_v)
    lat_deg = np.degrees(lat_v)

    log(f"sampling terrain elevation from {Path(args.terrain).name} (nearest-neighbour)")
    terrain_z, clamped_frac = sample_terrain(lon_deg, lat_deg, args.terrain)
    if clamped_frac > 0:
        log(f"  NOTE: {clamped_frac*100:.1f}% of vertices fall outside the terrain grid's bbox -- "
            f"those drape onto the terrain's nearest edge elevation, not real seafloor shape there")

    # true ECEF (unexaggerated) using the DISPLAY (terrain) elevation, for
    # normals + RTC center -- exactly like build_cesium_mesh.py's main(), but
    # z here is terrain_z, not value_v.
    ex, ey, ez = geodetic_to_ecef(lon_v, lat_v, terrain_z)
    ecef = np.stack([ex, ey, ez], axis=1)

    log("  computing smooth normals")
    normals = compute_smooth_normals(ecef, tris)
    radial = ecef / np.linalg.norm(ecef, axis=1, keepdims=True)
    mean_dot = float(np.mean(np.sum(normals * radial, axis=1)))
    if mean_dot < 0:
        log(f"  normals point inward (mean dot {mean_dot:.3f}) -- flipping winding + normals")
        normals = -normals
        tris = tris[:, [0, 2, 1]]

    vmin, vmax = float(np.min(value_v)), float(np.max(value_v))
    log(f"  building colour ramp ({args.ramp}), value range [{vmin:.2f}, {vmax:.2f}] {args.units}")
    if args.ramp == "diverging":
        vhalf = max(abs(vmin), abs(vmax))
        cmin, cmax = -vhalf, vhalf
        stops = build_diverging_colormap()
    else:
        cmin, cmax = vmin, vmax
        stops = build_sequential_colormap()
    value_rgb = apply_colormap(value_v.astype(np.float64), cmin, cmax, stops)
    color_value = np.concatenate([value_rgb, np.full((n_vert, 1), 255, dtype=np.uint8)], axis=1)
    colormap_stops_meta = {"domain": "relative_0_1", "stops": stops.tolist()}

    mesh_path = out_dir / "mesh.bin"
    sections = []

    def write_section(f, name, arr):
        arr = np.ascontiguousarray(arr)
        offset = f.tell()
        f.write(arr.tobytes())
        sections.append({
            "name": name,
            "offset": offset,
            "length": arr.nbytes,
            "dtype": str(arr.dtype),
            "count": int(arr.shape[0]) if arr.ndim else 1,
            "components": int(arr.shape[1]) if arr.ndim > 1 else 1,
        })

    with open(mesh_path, "wb") as f:
        write_section(f, "lon_rad", lon_v.astype(np.float64))
        write_section(f, "lat_rad", lat_v.astype(np.float64))
        write_section(f, "z_m", terrain_z.astype(np.float32))
        write_section(f, "normal", normals.astype(np.float32))
        write_section(f, "color_depth", color_value.astype(np.uint8))
        write_section(f, "indices", tris.astype(np.uint32))

    meta = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": args.label,
        "source_value": Path(args.value).name,
        "source_terrain": Path(args.terrain).name,
        "vertex_count": n_vert,
        "triangle_count": int(tris.shape[0]),
        "stride": stride,
        "effective_resolution_m": eff_res_m,
        "native_resolution_m": dx_deg * 111320,
        "rtc_center_ecef": ecef.mean(axis=0).tolist(),
        "bbox": {
            "lon_min": math.degrees(float(lon_v.min())),
            "lon_max": math.degrees(float(lon_v.max())),
            "lat_min": math.degrees(float(lat_v.min())),
            "lat_max": math.degrees(float(lat_v.max())),
        },
        "z_range_m": [float(terrain_z.min()), float(terrain_z.max())],
        "terrain_clamped_frac": clamped_frac,
        "colormap": f"geophysics_{args.ramp}",
        "colormap_stops": colormap_stops_meta,
        "backscatter_range": None,
        "backscatter_colormap_stops": None,
        "has_backscatter": False,
        "is_geophysics": True,
        "legend_kind": args.legend_kind,
        "legend_units": f" {args.units}",
        "legend_range": [vmin, vmax] if args.ramp == "sequential" else [cmin, cmax],
        "value_range": [vmin, vmax],
        "sections": sections,
    }
    with open(out_dir / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    log(f"wrote {mesh_path} ({mesh_path.stat().st_size/1e6:.1f} MB) + meta.json")
    log("done")


if __name__ == "__main__":
    main()
