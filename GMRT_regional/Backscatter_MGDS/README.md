# Backscatter_MGDS

Acoustic backscatter and bathymetry, individual-cruise resolution, for the
Central America ↔ Galápagos corridor — sourced cruise-by-cruise from the
Marine Geoscience Data System (MGDS) rather than from GMRT's blended
synthesis. Dataset identifiers (MGDS UIDs) and sources are in
[`DATA_MANIFEST.md`](../../DATA_MANIFEST.md).

## Using these files

Each subfolder holds the original files as downloaded, plus a
`*_geomapapp.grd` or `*_geomapapp.tif` twin for every file that needed a
format fix to open in GeoMapApp. **Load the `_geomapapp` version.** MGDS
ships grids in several non-standard conventions that GeoMapApp's importer
doesn't recognize as-is; `scripts/fix_mgds_grid.py` produces the twins by
renaming/reprojecting the same data into a format it does recognize.

### `MGL1106_CostaRica_CRISP/`
Kongsberg EM122 5 m backscatter grid, offshore Costa Rica (2011), extent
84.4–83.933°W / 8.3–9.067°N. `Sidescan_5m.grd` is already GeoMapApp-ready as
downloaded — no `_geomapapp` twin needed.

### `DRFT04RR_GSC_Bathymetry/`
10 EM122 bathymetry grids off the Galápagos Islands, cruise DRFT04RR (2001):
one combined 100 m grid, one 10 m grid, and 6 individual survey-line grids
at 50–400 m.

- **`86w_all_bathy_10m` is an interpolated resample of the 100 m grid**, not
  independently higher-resolution data (confirmed via its own `grdsample`
  provenance metadata) — use the individual line grids for genuine
  higher-than-GMRT detail here.
- Depths are stored positive-down (~2300–3700 m), the opposite convention
  from GMRT/MV1007's negative-elevation grids. Check the per-layer
  colorscale before loading both together, or the seafloor renders inverted.

### `DRFT04RR_GSC_Backscatter/`
28 sidescan/backscatter products from the same cruise, 8–16 m/pixel, UTM
zone 15N — converted to GeoTIFF (EPSG:32615) since the source netCDF carries
no usable CRS. `rr0101r-50-06.8m.ss.grd` does not convert: its data section
is shorter than its own header declares (confirmed by re-downloading and
re-extracting independently), which is corruption in MGDS's own copy, not
introduced by this conversion. Skip it, or re-download that line from MGDS
UID 23858 if it's needed.

### `TN188_GSC_Bathymetry_8m/`
26 DSL-120A-derived bathymetry grids, 8 m resolution, Galápagos Spreading
Center (2005) — pairs with the TN188 sidescan grid extracted separately in
`GeoMapApp_ready/`. Source files use 0–360° longitude; the `_geomapapp`
twins are renumbered to −180/180 for consistency with everything else here.

### `GSC_97-86W_Compilation/`
`GSC_97-86W_100m_comp.grd` — single regional compilation, 97–86°W / −2–4°N,
100 m/cell. Already GeoMapApp-ready as downloaded.

## Candidates considered and not included

An MGDS search of this corridor for `Backscatter:Acoustic` returned 81
datasets. Most are raw or lightly processed multibeam ping files rather than
finished grids — notably AT07-13, AT11-27, AT15-63 (Galápagos Spreading
Center) and FK181210/FK190106 (Costa Rica, R/V Falkor), all of which require
MB-System (`mbgrid`, `mbbackangle` + `mbmosaic`) to become viewable, and none
of which are included here.

One ready-made grid from that search remains unreviewed, just north of this
corridor at the East Pacific Rise 9°N: MGDS UID 20650 (cruise AT18-12,
2011).
