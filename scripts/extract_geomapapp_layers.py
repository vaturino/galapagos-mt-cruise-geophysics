#!/usr/bin/env python3
"""
extract_geomapapp_layers.py

Pull GeoMapApp-ready layers (GMT/NetCDF .grd grids and GeoTIFF .tif mosaics)
straight out of the big HMRG cruise-transfer zip WITHOUT unzipping the whole
archive. Because the zip stores its members with STORE (no compression),
Python's zipfile module can seek directly to each member via the archive's
central directory and copy just its bytes -- no need to decompress or
extract the other ~5,000 files (nav plots, .ps/.pdf, GMT sidecar files) that
GeoMapApp can't use as map layers anyway.

Stdlib only -- no packages to install, so it works with no internet access.

Usage:
    python3 extract_geomapapp_layers.py \\
        --zip /path/to/dfornari_untitled-transfer_2026-03-11_2117.zip \\
        --dest /path/to/drive/GeoMapApp_ready \\
        --include bathymetry backscatter

    # add mr1 to also pull the legacy MR1 towed-sidescan grids/tifs (~42.7GB):
    python3 extract_geomapapp_layers.py --zip ... --dest ... --include bathymetry backscatter mr1

    # see what would be extracted without writing anything:
    python3 extract_geomapapp_layers.py --zip ... --dest ... --dry-run

    # resume an interrupted run (skips files already fully extracted):
    python3 extract_geomapapp_layers.py --zip ... --dest ... --resume
"""

import argparse
import csv
import os
import sys
import time
import zipfile

# Map each requested "layer" to (source folder inside the zip, output subfolder,
# file extensions to keep). Everything else in that source folder (.ps, .pdf,
# .jpg, .info, .log, .cmd, .gmtdefaults4, .gmtcommands4, .key) is skipped --
# it's processing metadata / quicklook plots, not something GeoMapApp imports
# as a georeferenced layer.
LAYER_MAP = {
    "bathymetry": {
        "src_prefix": "FINAL-HMRG-MAP_SET-MV1007/FINAL-MV1007-BATHY-EM122/",
        "out_dir": "Bathymetry",
        "extensions": (".grd",),
    },
    "backscatter": {
        "src_prefix": "FINAL-HMRG-MAP_SET-MV1007/FINAL-MV1007-EM122-SIDESCAN/",
        "out_dir": "Backscatter",
        "extensions": (".grd", ".tif"),
    },
    "mr1": {
        "src_prefix": "FINAL-HMRG-MAP_SET-MV1007/FINAL-MV1007-MR1-SS/",
        "out_dir": "MR1_Sidescan",
        "extensions": (".grd", ".tif"),
    },
}


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def safe_join(base, *parts):
    """Join and make sure the result can't escape base (zip-slip guard)."""
    path = os.path.normpath(os.path.join(base, *parts))
    base = os.path.normpath(base)
    if not (path == base or path.startswith(base + os.sep)):
        raise ValueError(f"Unsafe path in archive: {parts}")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", required=True, help="Path to the cruise transfer zip")
    ap.add_argument("--dest", required=True, help="Destination folder to create/fill (e.g. .../GeoMapApp_ready)")
    ap.add_argument(
        "--include",
        nargs="+",
        choices=list(LAYER_MAP.keys()),
        default=["bathymetry", "backscatter"],
        help="Which layer groups to pull out (default: bathymetry backscatter). "
             "Add 'mr1' to also pull the legacy MR1 towed-sidescan grids/tifs (large, ~42.7GB).",
    )
    ap.add_argument("--dry-run", action="store_true", help="List what would be extracted, write nothing")
    ap.add_argument(
        "--resume",
        action="store_true",
        help="Skip files already present at the destination with the correct size "
             "(safe to re-run after an interrupted extraction).",
    )
    args = ap.parse_args()

    zip_path = args.zip
    dest_root = args.dest

    if not os.path.isfile(zip_path):
        sys.exit(f"Zip not found: {zip_path}")

    os.makedirs(dest_root, exist_ok=True)
    manifest_path = os.path.join(dest_root, "extraction_manifest.csv")
    manifest_rows = []

    t0 = time.time()
    total_bytes = 0
    total_files = 0
    per_layer_counts = {k: 0 for k in args.include}
    per_layer_bytes = {k: 0 for k in args.include}

    print(f"Opening {zip_path} ...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = zf.infolist()
        print(f"Archive has {len(infos)} entries. Scanning for requested layers: {args.include}")

        # Build the extraction plan first so we can report totals before doing any I/O.
        plan = []  # (member_info, layer_key, out_path)
        for info in infos:
            if info.is_dir():
                continue
            name = info.filename
            for layer_key in args.include:
                spec = LAYER_MAP[layer_key]
                if name.startswith(spec["src_prefix"]) and name.lower().endswith(spec["extensions"]):
                    rel = name[len(spec["src_prefix"]):]  # keep the per-survey-line subfolder, e.g. bty-50-16/50-16...grd
                    out_path = safe_join(dest_root, spec["out_dir"], rel)
                    plan.append((info, layer_key, out_path, rel))
                    break

        plan_bytes = sum(i.file_size for i, _, _, _ in plan)
        print(f"Plan: {len(plan)} files, {human(plan_bytes)} total, to be written under {dest_root}")

        if args.dry_run:
            for info, layer_key, out_path, rel in plan:
                print(f"  [{layer_key}] {info.filename} ({human(info.file_size)}) -> {out_path}")
            print("Dry run only -- nothing written.")
            return

        for idx, (info, layer_key, out_path, rel) in enumerate(plan, 1):
            if args.resume and os.path.isfile(out_path) and os.path.getsize(out_path) == info.file_size:
                pass  # already extracted correctly, skip the copy
            else:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                tmp_path = out_path + ".partial"
                with zf.open(info, "r") as src, open(tmp_path, "wb") as dst:
                    # copy in chunks; stored (uncompressed) members stream straight through
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                os.replace(tmp_path, out_path)
                # preserve original mtime
                try:
                    dt = info.date_time
                    mtime = time.mktime((*dt, 0, 0, -1))
                    os.utime(out_path, (mtime, mtime))
                except Exception:
                    pass

            total_bytes += info.file_size
            total_files += 1
            per_layer_counts[layer_key] += 1
            per_layer_bytes[layer_key] += info.file_size
            manifest_rows.append([layer_key, info.filename, rel, out_path, info.file_size])

            if idx % 50 == 0 or idx == len(plan):
                elapsed = time.time() - t0
                rate = total_bytes / elapsed if elapsed > 0 else 0
                print(f"  {idx}/{len(plan)} files, {human(total_bytes)} written "
                      f"({human(rate)}/s elapsed {elapsed:.0f}s)")

    with open(manifest_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["layer", "zip_entry", "relative_path", "extracted_to", "size_bytes"])
        w.writerows(manifest_rows)

    elapsed = time.time() - t0
    print("\nDone.")
    print(f"Extracted {total_files} files, {human(total_bytes)} total, in {elapsed:.0f}s")
    for k in args.include:
        print(f"  {k}: {per_layer_counts[k]} files, {human(per_layer_bytes[k])} -> {os.path.join(dest_root, LAYER_MAP[k]['out_dir'])}")
    print(f"Manifest written to {manifest_path}")


if __name__ == "__main__":
    main()
