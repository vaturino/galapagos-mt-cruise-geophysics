# Galápagos MT Cruise — Regional Geophysical Context

Tools and documentation for assembling, converting, and visualizing the
bathymetry, backscatter, gravity, magnetics, and seismic datasets covering
the wider Panama–Galápagos–Costa Rica corridor, in support of a Galápagos
magnetotelluric (MT) acquisition cruise. The cruise's own multibeam survey
(MV1007) is prepared as a separate local dataset (see [Data](#data) below)
and is not versioned in this repository.

## Repository layout

| Path | Contents |
|---|---|
| `GMRT_regional/` | Individual-cruise bathymetry, backscatter, gravity, magnetics, and seismic products for the wider corridor, pulled from the Marine Geoscience Data System (MGDS), plus a regional GMRT basemap grid. See [`GMRT_regional/README.md`](GMRT_regional/README.md). |
| `Viewer3D/` | Offline, self-contained 3D viewer (CesiumJS) for exploring the bathymetry, backscatter, and geophysics layers together. See [`Viewer3D/README.md`](Viewer3D/README.md). |
| `scripts/` | Python tooling for extracting, converting, validating, and meshing the datasets referenced above. See [`scripts/README.md`](scripts/README.md). |
| `DATA_MANIFEST.md` | Every dataset this project uses: source, identifier, format, size, and how to (re)obtain it. |

## Setup

Requires Python 3.9+. Most scripts need numpy, scipy, and tifffile, installed
into a local virtual environment (not committed to the repository, and not
shared between machines — each one is tied to the OS/architecture it was
built on):

```bash
cd scripts
./setup_env.sh        # Windows: setup_env.bat
source venv/bin/activate
```

This needs internet access once, to fetch the three packages from PyPI.
Everything else runs offline afterward. A couple of scripts (`extract_geomapapp_layers.py`,
the mmap-based inspectors) are stdlib-only and need no environment at all.

## Data

This repository holds code and documentation, not the datasets themselves —
the grids involved range from tens of MB to several GB per file, well past
what a git repository should carry, and several individual files exceed
GitHub's 100 MB per-file limit outright.

- [`DATA_MANIFEST.md`](DATA_MANIFEST.md) lists every dataset used by this
  project: source, identifier, format, and size.
- `scripts/` regenerates GeoMapApp-ready and viewer-ready copies from those
  sources — see [`scripts/README.md`](scripts/README.md) for which script
  does what and in what order.
- Two things exist locally alongside this repository but are intentionally
  excluded from it:
  - `GeoMapApp_ready/` — the MV1007 cruise's own processed bathymetry and
    backscatter, extracted from the cruise's HMRG data transfer via
    `scripts/extract_geomapapp_layers.py`. Not a public dataset, and not
    reproducible from anything in `DATA_MANIFEST.md`.
  - Everything under `GMRT_regional/*/` and `Viewer3D/data/` — reproducible
    from `DATA_MANIFEST.md` plus the scripts in `scripts/`.

## Known gaps

- No dataset in this project currently covers the Galápagos platform itself
  with processed gravity, magnetics, or seismic data. `GMRT_regional/Geophysics_MGDS/README.md`
  documents what was searched and why the platform itself came up empty.
