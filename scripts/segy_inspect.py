#!/usr/bin/env python3
"""
Inspect a SEGY seismic file (multichannel seismic reflection, singlebeam,
sub-bottom profiler, etc) and print a catalog-style summary: trace count,
sample rate, record length, and shot-point geometry (extent + track) read
from the trace headers -- without loading trace DATA into memory (segyio
opens SEGY lazily; only headers are read here).

SEGY is fundamentally NOT a grid -- it's a sequence of traces along a
survey track, so it can't become a bathymetry mesh the way the .grd/.tif
readers in build_cesium_mesh.py do. This script exists to answer "what's
in this file and where was it shot" for cataloging purposes (matching how
the DOA-ETP metadata CSV catalogs MBES survey footprints), and optionally
to export the shot-point track as a simple CSV the existing CSV
track-point upload feature in Viewer3D can show as an overlay -- that is
the extent of what this project's viewer can currently do with SEGY.

Usage:
  python3 segy_inspect.py SURVEY_LINE.sgy
  python3 segy_inspect.py SURVEY_LINE.sgy --track-csv out_track.csv
"""
import argparse
import json
import sys

import numpy as np
import segyio


def inspect(path):
    with segyio.open(path, "r", ignore_geometry=True) as f:
        n_traces = f.tracecount
        dt_us = segyio.dt(f)  # sample interval, microseconds
        n_samples = len(f.samples)
        record_length_s = (n_samples - 1) * dt_us / 1e6 if n_samples else 0.0

        # Trace-header coordinates: SEGY stores these as integers scaled by
        # SourceGroupScalar (byte 71-72) -- a negative scalar means "divide",
        # positive means "multiply" (0 or absent means "no scaling").
        xs = f.attributes(segyio.TraceField.SourceX)[:]
        ys = f.attributes(segyio.TraceField.SourceY)[:]
        scalars = f.attributes(segyio.TraceField.SourceGroupScalar)[:]

        def descale(vals, scalars):
            vals = vals.astype(np.float64)
            scalars = scalars.astype(np.float64)
            out = vals.copy()
            mul = scalars > 0
            div = scalars < 0
            out[mul] *= scalars[mul]
            out[div] /= -scalars[div]
            return out

        xs_d = descale(xs, scalars)
        ys_d = descale(ys, scalars)
        has_coords = bool(np.any(xs_d != 0) or np.any(ys_d != 0))

        summary = {
            "file": str(path),
            "trace_count": int(n_traces),
            "samples_per_trace": int(n_samples),
            "sample_interval_us": int(dt_us),
            "record_length_s": round(record_length_s, 4),
            "has_trace_coordinates": has_coords,
        }
        if has_coords:
            # SEGY SourceX/Y are commonly projected meters, not lon/lat -- flag
            # rather than guess; a real conversion needs the file's stated CRS.
            summary["source_x_range"] = [float(xs_d.min()), float(xs_d.max())]
            summary["source_y_range"] = [float(ys_d.min()), float(ys_d.max())]
            summary["coordinate_note"] = (
                "SourceX/SourceY as stored in the trace headers, descaled by "
                "SourceGroupScalar -- units depend on the file (often projected "
                "meters, sometimes lon/lat * 10^precision). Check the SEGY "
                "textual header (segyio.tools.wrap(f.text[0])) or the survey's "
                "own documentation before treating these as lon/lat degrees."
            )
        return summary, xs_d, ys_d, has_coords


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("segy_file")
    ap.add_argument("--track-csv", help="also write shot-point X/Y to this CSV (raw header units, see coordinate_note)")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON instead of text")
    args = ap.parse_args()

    summary, xs, ys, has_coords = inspect(args.segy_file)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"file:              {summary['file']}")
        print(f"traces:            {summary['trace_count']:,}")
        print(f"samples/trace:     {summary['samples_per_trace']}")
        print(f"sample interval:   {summary['sample_interval_us']} us")
        print(f"record length:     {summary['record_length_s']} s")
        if has_coords:
            print(f"source X range:    {summary['source_x_range']}")
            print(f"source Y range:    {summary['source_y_range']}")
            print(f"  NOTE: {summary['coordinate_note']}")
        else:
            print("source coordinates: not populated in trace headers (or all zero)")

    if args.track_csv and has_coords:
        with open(args.track_csv, "w") as f:
            f.write("trace_index,source_x,source_y\n")
            for i, (x, y) in enumerate(zip(xs, ys)):
                f.write(f"{i},{x},{y}\n")
        print(f"wrote {args.track_csv} ({len(xs):,} rows)")


if __name__ == "__main__":
    main()
