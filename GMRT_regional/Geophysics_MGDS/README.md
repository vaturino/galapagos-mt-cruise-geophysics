# Geophysics_MGDS

Gravity, magnetics, and seismic products for the Central America ↔
Galápagos corridor, sourced from the Marine Geoscience Data System (MGDS).
Dataset identifiers and sources are in
[`DATA_MANIFEST.md`](../../DATA_MANIFEST.md).

## Coverage

**None of the products below actually cover the Galápagos platform
itself** — the corridor-wide search that found them surfaced genuine
regional compilations, but for the subduction zone and spreading center on
either side of the platform, not the platform's own footprint (roughly
89–92°W, 1.5°S–1°N).

| Dataset | Region | Overlaps the Galápagos platform? |
|---|---|---|
| Barckhausen Central America magnetics | Central America subduction zone / coast | No — well northeast of the platform |
| Bassett & Watts residual gravity | Central America trench, 111–78°W / 1.2–22.9°N | No — southern edge stops ~1° north of the platform |
| SR1806 Cocos-Nazca gravity products | Cocos-Nazca spreading center, 105–95°W / −1–5°N | No — eastern edge stops ~3° short of the platform |
| Mittelstaedt onshore Galápagos gravity | Santa Cruz & San Cristóbal Islands | Yes, but not currently available (see below) |

A repeat search restricted to the platform's own bounding box (93–88°W,
2°S–1.5°N) turned up only the same legacy single-ship tracklines already
excluded below, plus the two datasets noted as not included. There is
currently no working processed gravity, magnetics, or seismic product that
actually covers the platform.

## What's here

- **`Barckhausen_CentralAmerica_Magnetics/`** — regional magnetic anomaly
  (IGRF removed) compilation. `central_america_mag.grd` is the original
  (old-style GMT, won't open in GeoMapApp as-is); use
  `central_america_mag_geomapapp.grd`.

- **`Bassett_CentralAmerica_ResidualGravity/`** — residual free-air gravity
  anomaly over the Central America subduction zone, one region pulled from
  a global 18-region compilation. `CentAm_Residual_gravity.grd` is the
  original (0–360° longitude); use `CentAm_Residual_gravity_geomapapp.grd`.

- **`SR1806_CocosNazca_Gravity/`** — six processed gravity products from
  cruise SR1806 (2018) at the Cocos-Nazca spreading center, already in
  GeoMapApp-native format, no conversion needed: `105W95W1S5N_mba.grd`
  (mantle Bouguer anomaly), `_mba_global_topo_global_FAA.grd` /
  `_mba_ship_topo_global_FAA.grd` (two MBA variants by input topography/FAA
  source), `_rmba_1k.grd` (residual mantle Bouguer anomaly), `_crust_1k.grd`
  (gravity-derived crustal thickness), `_thermal_1k.grd` (1-D half-space
  cooling thermal model).

- **`Mittelstaedt_GalapagosIslands_Gravity/`** — onshore free-air gravity
  stations on Santa Cruz and San Cristóbal (Cleary et al., 2020,
  [doi:10.1029/2019GC008722](https://doi.org/10.1029/2019GC008722)). Empty:
  MGDS served the source file (`Cleary_et_al_Galapagos_FAA_data.txt.gz`) as
  a genuine 0-byte file on two separate download attempts. If this data is
  needed, check the paper's supplementary material, or report the broken
  file to MGDS.

## Not included

- ~85 single-ship MGD77 trackline datasets (Vema, Robert D. Conrad,
  Eltanin, Maurice Ewing, Thomas Washington, some modern Atlantis legs,
  1960s–2010s) turned up in the same search. They're processed data, but
  each is a thin one-pass transit line rather than a regional product.
- All but one of 77 seismic-tagged datasets in the corridor are raw SEGY
  shot/stack data, navigation files, or PDF quick-look plots — not usable
  without dedicated seismic-processing software.
- **MGL2304 "MarineIGUANA/IGUANA"** (MGDS UID 33013): a 3-D P- and S-wave
  seismic tomography model of the Galápagos Plume mantle structure
  (Hufstetler, Hooft, Toomey, Ito et al., 2026) — the one genuinely
  platform-covering processed seismic product found, and the only seismic
  exception to the raw-data pattern above. Not included: access-restricted.
  If this changes, note that it's a 3-D volume (longitude/latitude/depth,
  UTM zone 35N, NetCDF4/HDF5), a different structure from every other grid
  in this project — `scripts/fix_mgds_grid.py` and its underlying
  NetCDF3-only reader can't open it as-is, and it would likely need
  depth-slicing into 2-D grids to visualize alongside everything else here.
