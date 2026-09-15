#!/usr/bin/env python3
"""
build_cesium_mesh.py -- turn a GMT NetCDF (x/y/z) bathymetry grid, plus an
optional co-registered backscatter grid, into the binary mesh + JSON
metadata that Viewer3D/app.js loads offline in Cesium.

Depends only on numpy + scipy.io.netcdf_file (scipy only for the reader --
GeoMapApp_ready/../scripts/netcdf_lite.py is a vendored, dependency-free
drop-in with an identical netcdf_file class if scipy isn't available on the
target machine; swap the import below for that if needed). No GDAL, no
GMT, no matplotlib -- everything (cropping, decimation, normals, both
colour ramps) is plain numpy.

Output (written to --out-dir):
  mesh.bin   -- packed binary: lon_rad, lat_rad, z_m, normals(xyz),
                color_depth(rgba), color_backscatter(rgba) [optional],
                indices -- section byte ranges are in meta.json
  meta.json  -- vertex/index counts, section layout, RTC center, bbox,
                z range, stride/resolution actually used, provenance

Usage:
  python3 build_cesium_mesh.py \
      --bathy MV1007_bathymetry_mosaic.grd \
      --backscatter MV1007_backscatter_mosaic.grd \
      --out-dir Viewer3D/data/MV1007 \
      --max-triangles 1500000
"""
import argparse
import json
import math
import struct
import sys
import time
from pathlib import Path

import numpy as np

try:
    from scipy.io import netcdf_file
except ImportError:  # pragma: no cover - fallback for machines without scipy
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from netcdf_lite import netcdf_file  # type: ignore

WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_grd(path):
    nc = netcdf_file(path, "r", mmap=True)
    # Modern COARDS-style x/y/z is the common case (GeoMapApp-ready grids,
    # most MGDS products after fix_mgds_grid.py). GMRT's own GridServer
    # export instead names these lon/lat/altitude -- accept either rather
    # than requiring a separate rename step for every GMRT pull.
    if "x" in nc.variables:
        x_name, y_name, z_name = "x", "y", "z"
    elif "lon" in nc.variables:
        x_name, y_name, z_name = "lon", "lat", "altitude"
    else:
        raise ValueError(
            f"{path}: no x/y/z or lon/lat/altitude variables found -- got {list(nc.variables.keys())}"
        )
    x = np.array(nc.variables[x_name].data[:], dtype=np.float64)
    y = np.array(nc.variables[y_name].data[:], dtype=np.float64)
    z = nc.variables[z_name].data  # (ny, nx), stays memmapped
    return x, y, z, nc


def tight_bbox(z, chunk=500):
    """Row/col index range that actually contains finite data."""
    ny, nx = z.shape
    row_has = np.zeros(ny, dtype=bool)
    col_has = np.zeros(nx, dtype=bool)
    for r0 in range(0, ny, chunk):
        r1 = min(ny, r0 + chunk)
        block = np.asarray(z[r0:r1, :])
        finite = np.isfinite(block)
        row_has[r0:r1] = finite.any(axis=1)
        col_has |= finite.any(axis=0)
    rows = np.where(row_has)[0]
    cols = np.where(col_has)[0]
    return rows.min(), rows.max(), cols.min(), cols.max()


def geodetic_to_ecef(lon_rad, lat_rad, h_m):
    sin_lat = np.sin(lat_rad)
    cos_lat = np.cos(lat_rad)
    n = WGS84_A / np.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + h_m) * cos_lat * np.cos(lon_rad)
    y = (n + h_m) * cos_lat * np.sin(lon_rad)
    zz = (n * (1.0 - WGS84_E2) + h_m) * sin_lat
    return x, y, zz


def choose_stride(populated_cells, max_triangles):
    """Two triangles per surviving quad -> solve for stride."""
    target_quads = max(1, max_triangles // 2)
    stride = math.sqrt(populated_cells / target_quads)
    return max(1, int(round(stride)))


def build_depth_colormap():
    # perceptually-ish ordered, linear stops, deep(navy) -> shallow(pale cyan)
    stops = np.array(
        [
            [0.00, 8, 8, 40],
            [0.25, 12, 44, 110],
            [0.50, 20, 95, 140],
            [0.75, 60, 150, 150],
            [1.00, 200, 230, 210],
        ],
        dtype=np.float64,
    )
    return stops


def build_backscatter_colormap():
    # single-hue amber ramp, low(dark) -> high(bright) reflectivity
    stops = np.array(
        [
            [0.00, 15, 15, 15],
            [0.33, 90, 60, 20],
            [0.66, 180, 120, 30],
            [1.00, 255, 210, 120],
        ],
        dtype=np.float64,
    )
    return stops


def apply_colormap(values, vmin, vmax, stops):
    t = np.clip((values - vmin) / max(1e-9, (vmax - vmin)), 0.0, 1.0)
    out = np.empty((values.shape[0], 3), dtype=np.uint8)
    for ch in range(3):
        out[:, ch] = np.interp(t, stops[:, 0], stops[:, 1 + ch]).astype(np.uint8)
    return out


def build_globe_colormap():
    """The real GMT 'gmt/globe' palette (share/cpt/gmt/globe.cpt in the GMT
    source tree, by Lester M. Anderson) -- the classic combined
    bathymetry/topography relief ramp that GMT- and GMRT-family tools use by
    default (deep abyssal purple -> blue -> pale cyan at the shelf break,
    then a hard hinge at sea level into green lowlands -> tan/brown uplands
    -> grey/white peaks). Fixed ABSOLUTE elevation domain (-10000..+10000 m,
    not rescaled to this dataset's own min/max) so it reads the same
    depth/elevation colors GMRT's own map would show, and so a basemap
    dataset colored with this is visually distinct from survey meshes using
    build_depth_colormap()'s locally-normalized ramp -- that's the point of
    using it for a basemap layer.
    Returns (ocean_stops, land_stops), each an (N,4) array of [elev_m, r,g,b].
    """
    ocean = np.array(
        [
            [-10000, 153, 0, 255], [-9500, 153, 0, 255], [-9000, 153, 0, 255],
            [-8500, 136, 17, 255], [-8000, 119, 34, 255], [-7500, 102, 51, 255],
            [-7000, 85, 68, 255], [-6500, 68, 85, 255], [-6000, 51, 102, 255],
            [-5500, 34, 119, 255], [-5000, 17, 136, 255], [-4500, 0, 153, 255],
            [-4000, 27, 164, 255], [-3500, 54, 175, 255], [-3000, 81, 186, 255],
            [-2500, 108, 197, 255], [-2000, 134, 208, 255], [-1500, 161, 219, 255],
            [-1000, 188, 230, 255], [-500, 215, 241, 255], [-200, 241, 252, 255],
            [0, 241, 252, 255],
        ],
        dtype=np.float64,
    )
    land = np.array(
        [
            [0, 51, 102, 0],
            [100, 51, 204, 102], [200, 187, 228, 146], [500, 255, 220, 185],
            [1000, 243, 202, 137], [1500, 230, 184, 88], [2000, 217, 166, 39],
            [2500, 168, 154, 31], [3000, 164, 144, 25], [3500, 162, 134, 19],
            [4000, 159, 123, 13], [4500, 156, 113, 7], [5000, 153, 102, 0],
            [5500, 162, 89, 89], [6000, 178, 118, 118], [6500, 183, 147, 147],
            [7000, 194, 176, 176], [7500, 204, 204, 204], [8000, 229, 229, 229],
            [8500, 242, 242, 242], [9000, 255, 255, 255], [9500, 255, 255, 255],
            [10000, 255, 255, 255],
        ],
        dtype=np.float64,
    )
    return ocean, land


def apply_globe_colormap(z_m, ocean_stops, land_stops):
    """Like apply_colormap, but on the fixed globe.cpt domain with a hard
    hinge exactly at sea level (z=0 jumps from pale ocean cyan to land
    green, rather than blending) -- matches the source cpt's HARD_HINGE."""
    zc = np.clip(z_m, -10000.0, 10000.0)
    is_land = z_m >= 0
    out = np.empty((z_m.shape[0], 3), dtype=np.uint8)
    for ch in range(3):
        ocean_c = np.interp(zc, ocean_stops[:, 0], ocean_stops[:, 1 + ch])
        land_c = np.interp(zc, land_stops[:, 0], land_stops[:, 1 + ch])
        out[:, ch] = np.where(is_land, land_c, ocean_c).astype(np.uint8)
    return out


def compute_smooth_normals(ecef_xyz, tris):
    """Vectorised per-vertex normals via face-normal accumulation."""
    v0 = ecef_xyz[tris[:, 0]]
    v1 = ecef_xyz[tris[:, 1]]
    v2 = ecef_xyz[tris[:, 2]]
    face_n = np.cross(v1 - v0, v2 - v0)
    normals = np.zeros_like(ecef_xyz)
    for k in range(3):
        np.add.at(normals, tris[:, k], face_n)
    lens = np.linalg.norm(normals, axis=1)
    lens[lens == 0] = 1.0
    normals /= lens[:, None]
    return normals.astype(np.float32)


def scan_populated(z, r0, r1, c0, c1, chunk=500):
    """Count finite cells and their value range within a bbox, chunked (no full materialisation)."""
    populated = 0
    vmin, vmax = math.inf, -math.inf
    for rr in range(r0, r1 + 1, chunk):
        rr1 = min(r1 + 1, rr + chunk)
        block = np.asarray(z[rr:rr1, c0:c1 + 1])
        finite = np.isfinite(block)
        n = int(finite.sum())
        populated += n
        if n:
            vals = block[finite]
            vmin = min(vmin, float(vals.min()))
            vmax = max(vmax, float(vals.max()))
    return populated, vmin, vmax


def decimate_one_grid(path, stride, positive_down):
    """Read one bathy grid, crop to its own finite bbox, decimate by `stride`,
    and return (lon_rad, lat_rad, z_m, local_tris) for just that grid -- tris
    index into this grid's own lon/lat/z arrays (0-based), caller offsets them
    when concatenating multiple grids."""
    x, y, z, nc = read_grd(path)
    ny, nx = z.shape
    r0, r1, c0, c1 = tight_bbox(z)
    rows = np.arange(r0, r1 + 1, stride)
    cols = np.arange(c0, c1 + 1, stride)

    z_sub = np.asarray(z[np.ix_(rows, cols)]).astype(np.float64)
    if positive_down:
        z_sub = -z_sub
    lon_sub = np.radians(x[cols])
    lat_sub = np.radians(y[rows])
    lon_grid, lat_grid = np.meshgrid(lon_sub, lat_sub)

    finite = np.isfinite(z_sub)
    nr, ncols_ = z_sub.shape
    vert_id = -np.ones((nr, ncols_), dtype=np.int64)
    vert_id[finite] = np.arange(finite.sum())

    lon_v = lon_grid[finite]
    lat_v = lat_grid[finite]
    z_v = z_sub[finite].astype(np.float32)

    a = vert_id[:-1, :-1]
    b = vert_id[:-1, 1:]
    c = vert_id[1:, :-1]
    d = vert_id[1:, 1:]
    ok = (a >= 0) & (b >= 0) & (c >= 0) & (d >= 0)
    ai, bi, ci, di = a[ok], b[ok], c[ok], d[ok]
    tri1 = np.stack([ai, bi, di], axis=1)
    tri2 = np.stack([ai, di, ci], axis=1)
    tris = np.concatenate([tri1, tri2], axis=0).astype(np.int64)

    nc.close()
    return lon_v, lat_v, z_v, tris, (nx, ny), (r0, r1, c0, c1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bathy", required=True, nargs="+",
                     help="one or more bathymetry .grd files (GMT NetCDF, x/y/z). Multiple files are "
                          "treated as separate tiles of the same dataset (e.g. a chain of survey-line "
                          "grids too large to mosaic into one dense grid) -- each is decimated and "
                          "triangulated independently, then concatenated into one mesh with a shared "
                          "colour scale. They do not need to share an exact grid phase; a visible seam "
                          "at tile boundaries at high --stride is possible but is a decimation artifact, "
                          "not a data error.")
    ap.add_argument("--backscatter", help="optional co-registered backscatter .grd (single file, matched "
                                           "to every --bathy vertex by nearest-neighbour lon/lat lookup)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--max-triangles", type=int, default=1_500_000,
                     help="target triangle budget after decimation, summed across all --bathy files "
                          "(default 1.5M -- keep well under this on older machines)")
    ap.add_argument("--stride", type=int, help="override auto-picked decimation stride (grid posts per output post)")
    ap.add_argument("--positive-down", action="store_true",
                     help="source grid(s) store depth as positive-down (some MGDS products do -- see "
                          "GMRT_regional/Backscatter_MGDS/README_MGDS_backscatter_candidates.md for which). "
                          "Negated on read so the mesh always uses negative-below-sea-level like GeoMapApp/GMRT.")
    ap.add_argument("--label", help="display label for this dataset in the viewer's dropdown/checkbox list "
                                     "(default: derived from the first --bathy filename)")
    ap.add_argument("--colormap", choices=["depth", "globe"], default="depth",
                     help="'depth' (default): navy-to-pale-cyan ramp rescaled to this dataset's own min/max "
                          "-- good for a single detailed survey. 'globe': the real GMT/GMRT 'globe.cpt' relief "
                          "palette on its fixed -10000..+10000 m domain, land included -- use this for a wide "
                          "regional basemap layer so it reads as context, visually distinct from the survey "
                          "meshes on top of it.")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"scanning {len(args.bathy)} bathymetry file(s) for coverage/stride")
    scans = []
    total_populated = 0
    ref_dx = None
    ref_dy = None
    for path in args.bathy:
        x, y, z, nc = read_grd(path)
        r0, r1, c0, c1 = tight_bbox(z)
        populated, vmin, vmax = scan_populated(z, r0, r1, c0, c1)
        dx_deg = abs(x[1] - x[0])
        dy_deg = abs(y[1] - y[0])
        if ref_dx is None:
            ref_dx = dx_deg
            ref_dy = dy_deg
        elif abs(dx_deg - ref_dx) / ref_dx > 0.05:
            log(f"  WARNING: {Path(path).name} spacing {dx_deg:.6f} deg differs >5% from first file's "
                f"{ref_dx:.6f} deg -- will still be decimated with its own stride count, may look uneven "
                f"next to the other tiles")
        log(f"  {Path(path).name}: {len(x)}x{len(y)} grid, {populated:,} populated cells, range [{vmin:.0f}, {vmax:.0f}]")
        total_populated += populated
        scans.append(path)
        nc.close()

    stride = args.stride or choose_stride(total_populated, args.max_triangles)
    eff_res_m = ref_dx * stride * 111320
    dlon_deg = ref_dx * stride
    dlat_deg = ref_dy * stride
    log(f"  stride={stride} -> effective resolution ~{eff_res_m:.0f} m (native was ~{ref_dx*111320:.0f} m)")

    lon_parts, lat_parts, z_parts, tri_parts = [], [], [], []
    vert_offset = 0
    for path in scans:
        log(f"decimating {Path(path).name}")
        lon_v, lat_v, z_v, tris, _, _ = decimate_one_grid(path, stride, args.positive_down)
        if lon_v.shape[0] == 0:
            log(f"  (no surviving posts at this stride, skipping)")
            continue
        lon_parts.append(lon_v)
        lat_parts.append(lat_v)
        z_parts.append(z_v)
        tri_parts.append(tris + vert_offset)
        vert_offset += lon_v.shape[0]
        log(f"  {lon_v.shape[0]:,} vertices, {tris.shape[0]:,} triangles")

    lon_v = np.concatenate(lon_parts)
    lat_v = np.concatenate(lat_parts)
    z_v = np.concatenate(z_parts)
    tris = np.concatenate(tri_parts).astype(np.uint32)
    n_vert = lon_v.shape[0]
    log(f"  total: {n_vert:,} vertices, {tris.shape[0]:,} triangles")

    # true ECEF (unexaggerated) for normals + RTC center
    ex, ey, ez = geodetic_to_ecef(lon_v, lat_v, z_v.astype(np.float64))
    ecef = np.stack([ex, ey, ez], axis=1)
    center = ecef.mean(axis=0)

    log("  computing smooth normals")
    normals = compute_smooth_normals(ecef, tris)
    # make sure normals point outward (away from Earth's centre) on average --
    # guards against triangle winding being backwards for this grid orientation
    radial = ecef / np.linalg.norm(ecef, axis=1, keepdims=True)
    mean_dot = float(np.mean(np.sum(normals * radial, axis=1)))
    if mean_dot < 0:
        log(f"  normals point inward (mean dot {mean_dot:.3f}) -- flipping winding + normals")
        normals = -normals
        tris = tris[:, [0, 2, 1]]

    log(f"  building colour ramp ({args.colormap})")
    zmin, zmax = float(np.min(z_v)), float(np.max(z_v))
    if args.colormap == "globe":
        ocean_stops, land_stops = build_globe_colormap()
        depth_rgb = apply_globe_colormap(z_v.astype(np.float64), ocean_stops, land_stops)
        # absolute-domain colormap for the legend: elevation (m) -> rgb, not t in [0,1]
        colormap_stops_meta = {"domain": "absolute_m", "ocean": ocean_stops.tolist(), "land": land_stops.tolist()}
    else:
        depth_stops = build_depth_colormap()
        depth_rgb = apply_colormap(z_v, zmin, zmax, depth_stops)
        # relative-domain colormap for the legend: t in [0,1] over [zmin,zmax] -> rgb
        colormap_stops_meta = {"domain": "relative_0_1", "stops": depth_stops.tolist()}
    color_depth = np.concatenate([depth_rgb, np.full((n_vert, 1), 255, dtype=np.uint8)], axis=1)

    has_backscatter = False
    color_bs = None
    bs_min = bs_max = None
    bs_values_full = None
    bs_colormap_stops_meta = None
    if args.backscatter:
        log(f"reading backscatter: {args.backscatter}")
        sx, sy, sz, snc = read_grd(args.backscatter)
        s_dx = sx[1] - sx[0]
        s_dy = sy[1] - sy[0]
        col_idx = np.round((np.degrees(lon_v) - sx[0]) / s_dx).astype(np.int64)
        row_idx = np.round((np.degrees(lat_v) - sy[0]) / s_dy).astype(np.int64)
        sny, snx = sz.shape
        in_range = (col_idx >= 0) & (col_idx < snx) & (row_idx >= 0) & (row_idx < sny)
        bs_vals = np.full(n_vert, np.nan, dtype=np.float64)
        ci_ok = col_idx[in_range]
        ri_ok = row_idx[in_range]
        sz_arr = np.asarray(sz)  # backscatter mosaic is smaller; safe to materialise
        bs_vals[in_range] = sz_arr[ri_ok, ci_ok]
        bs_valid = np.isfinite(bs_vals)
        log(f"  backscatter matched at {bs_valid.sum():,} / {n_vert:,} vertices")
        if bs_valid.any():
            bs_min, bs_max = float(np.nanmin(bs_vals)), float(np.nanmax(bs_vals))
            bs_stops = build_backscatter_colormap()
            bs_rgb = np.full((n_vert, 3), 40, dtype=np.uint8)  # fallback grey for no-data
            bs_rgb[bs_valid] = apply_colormap(bs_vals[bs_valid], bs_min, bs_max, bs_stops)
            alpha = np.where(bs_valid, 255, 90).astype(np.uint8)
            color_bs = np.concatenate([bs_rgb, alpha[:, None]], axis=1)
            has_backscatter = True
            bs_values_full = bs_vals.astype(np.float32)  # NaN where no match, for click read-out
            bs_colormap_stops_meta = {"domain": "relative_0_1", "stops": bs_stops.tolist()}
        snc.close()

    # ---- write mesh.bin ----
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
        write_section(f, "z_m", z_v.astype(np.float32))
        write_section(f, "normal", normals.astype(np.float32))
        write_section(f, "color_depth", color_depth.astype(np.uint8))
        if has_backscatter:
            write_section(f, "color_backscatter", color_bs.astype(np.uint8))
            write_section(f, "value_backscatter", bs_values_full.astype(np.float32))
        write_section(f, "indices", tris.astype(np.uint32))

    meta = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": args.label or Path(args.bathy[0]).stem,
        "source_bathy": [str(Path(p).name) for p in args.bathy],
        "source_backscatter": str(Path(args.backscatter).name) if args.backscatter else None,
        "vertex_count": n_vert,
        "triangle_count": int(tris.shape[0]),
        "stride": stride,
        "effective_resolution_m": eff_res_m,
        "dlon_deg": dlon_deg,
        "dlat_deg": dlat_deg,
        "rtc_center_ecef": center.tolist(),
        "bbox": {
            "lon_min": math.degrees(float(lon_v.min())),
            "lon_max": math.degrees(float(lon_v.max())),
            "lat_min": math.degrees(float(lat_v.min())),
            "lat_max": math.degrees(float(lat_v.max())),
        },
        "z_range_m": [zmin, zmax],
        "colormap": args.colormap,
        "colormap_stops": colormap_stops_meta,
        "backscatter_range": [bs_min, bs_max] if has_backscatter else None,
        "backscatter_colormap_stops": bs_colormap_stops_meta,
        "has_backscatter": has_backscatter,
        "sections": sections,
    }
    with open(out_dir / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    log(f"wrote {mesh_path} ({mesh_path.stat().st_size/1e6:.1f} MB) + meta.json")
    log("done")


if __name__ == "__main__":
    main()
