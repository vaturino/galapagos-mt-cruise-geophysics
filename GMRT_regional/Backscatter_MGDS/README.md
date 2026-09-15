# Backscatter_MGDS

Acoustic backscatter and bathymetry, individual-cruise resolution, for the
Central America ↔ Galápagos corridor — sourced cruise-by-cruise from the
Marine Geoscience Data System (MGDS) rather than from GMRT's blended
synthesis. Dataset identifiers, dataset-page links, and DOIs for everything
below are in [`DATA_MANIFEST.md`](../../DATA_MANIFEST.md) — that's the file
to use to re-fetch any of this from scratch.

## Using these files

Each subfolder holds the original files as downloaded, plus a
`*_geomapapp.grd` or `*_geomapapp.tif` twin for every file that needed a
format fix to open in GeoMapApp. **Load the `_geomapapp` version.** MGDS
ships grids in several non-standard conventions that GeoMapApp's importer
doesn't recognize as-is; `scripts/fix_mgds_grid.py` produces the twins by
renaming/reprojecting the same data into a format it does recognize.

### `MGL1106_CostaRica_CRISP/` ([dataset page](https://www.marine-geo.org/tools/datasets/21024), [DOI](https://doi.org/10.1594/IEDA/321024))
Kongsberg EM122 5 m backscatter grid, offshore Costa Rica (2011), extent
84.4–83.933°W / 8.3–9.067°N. `Sidescan_5m.grd` is already GeoMapApp-ready as
downloaded — no `_geomapapp` twin needed.

### `DRFT04RR_GSC_Bathymetry/` ([dataset page](https://www.marine-geo.org/tools/datasets/23860), [DOI](https://doi.org/10.1594/IEDA/323860))
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

### `DRFT04RR_GSC_Backscatter/` ([NetCDF: UID 23858](https://www.marine-geo.org/tools/datasets/23858)/[DOI](https://doi.org/10.1594/IEDA/323858), [ESRI ASCII: UID 23859](https://www.marine-geo.org/tools/datasets/23859)/[DOI](https://doi.org/10.1594/IEDA/323859))
28 sidescan/backscatter products from the same cruise, 8–16 m/pixel, UTM
zone 15N — converted to GeoTIFF (EPSG:32615) since the source netCDF carries
no usable CRS. `rr0101r-50-06.8m.ss.grd` does not convert: its data section
is shorter than its own header declares (confirmed by re-downloading and
re-extracting independently), which is corruption in MGDS's own copy, not
introduced by this conversion. Skip it, or re-download that line from MGDS
UID 23858 if it's needed.

### `TN188_GSC_Bathymetry_8m/` ([dataset page](https://www.marine-geo.org/tools/datasets/9471), [DOI](https://doi.org/10.1594/IEDA/100260))
26 DSL-120A-derived bathymetry grids, 8 m resolution, Galápagos Spreading
Center (2005) — pairs with the TN188 sidescan grid extracted separately in
`GeoMapApp_ready/`. Source files use 0–360° longitude; the `_geomapapp`
twins are renumbered to −180/180 for consistency with everything else here.

### `GSC_97-86W_Compilation/` ([dataset page](https://www.marine-geo.org/tools/datasets/21842), [DOI](https://doi.org/10.1594/IEDA/321842))
`GSC_97-86W_100m_comp.grd` — single regional compilation, 97–86°W / −2–4°N,
100 m/cell. Already GeoMapApp-ready as downloaded.

### `AT50-09BC_GalapagosPlatform_Bathymetry/`
8 EM124 multibeam bathymetry grids (15–50 m resolution) from R/V Atlantis
cruise AT50-09BC (April 2023) — **the only dataset in this project that
actually covers the Galápagos Platform itself** (see
[`Geophysics_MGDS/README.md`](../Geophysics_MGDS/README.md)'s Coverage
section for why every gravity/magnetics/seismic product falls short of the
platform; this bathymetry does not have that problem):

| File | Coverage |
|---|---|
| `AT5009_MB_CentralPlatform_WGS84_25m` | Central Galápagos Platform |
| `AT5009_MB_EasternPlatform_WGS84_25m` | Eastern Galápagos Platform |
| `AT5009_MB_SouthernPlatform_WGS84_30m` | Southern Galápagos Platform |
| `AT5009_MB_WesternPlatform_WGS84_30m` | Western Galápagos Platform |
| `AT5009_MB_PintaRift_WGS84_20m` | Pinta Rift |
| `AT5009_MB_CoralCroissant_WGS84_15m` | "Coral Croissant" (finest resolution of the set) |
| `AT5009_MB_Galapagos_WGS84_50m` | Full Galápagos region, 92.2–89.0°W / 1.9°S–0.8°N |
| `AL5009_MB_EcuadorEEZ_WGS84_50m` | Wider Ecuador EEZ, 92.2–87.4°W / 1.9°S–2.4°N |

As-downloaded, these grids are old-style GMT netCDF (`x_range`/`y_range`/
`z_range`/`dimension`/`z`, no CF conventions) — use the `_geomapapp` twin of
each. They also have a quirk unique to this dataset in the project: nodata
is stored as literal IEEE +Infinity with no `_FillValue` attribute, which
`scripts/fix_mgds_grid.py` now detects and scrubs (see
[`DATA_MANIFEST.md`](../../DATA_MANIFEST.md) for detail — this was a real
bug in the script, fixed once this dataset exposed it, not a pre-existing
known quirk).

License is CC BY-NC-SA 3.0 (non-commercial, share-alike) — see
[`DATA_MANIFEST.md`](../../DATA_MANIFEST.md) for the exact dataset page/DOI
and the two redundant GeoTIFF-format siblings that were not pulled.

## Candidates considered and not included

An MGDS search of this corridor for `Backscatter:Acoustic` returned 81
datasets. Most are raw or lightly processed multibeam ping files rather than
finished grids — notably [AT07-13](https://www.marine-geo.org/tools/datasets/17619),
[AT11-27](https://www.marine-geo.org/tools/datasets/17405),
[AT15-63](https://www.marine-geo.org/tools/datasets/31828) (Galápagos
Spreading Center) and [FK181210](https://www.marine-geo.org/tools/datasets/27009)/[FK190106](https://www.marine-geo.org/tools/datasets/27008)
(Costa Rica, R/V Falkor), all of which require MB-System (`mbgrid`,
`mbbackangle` + `mbmosaic`) to become viewable, and none of which are
included here.

One ready-made grid from that search remains unreviewed, just north of this
corridor at the East Pacific Rise 9°N: [UID 20650](https://www.marine-geo.org/tools/datasets/20650)
(cruise AT18-12, 2011).
