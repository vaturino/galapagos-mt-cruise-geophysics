# Data manifest

Every dataset this project uses: source, identifier, format, and
approximate size. None of these files are committed to this repository —
`scripts/` regenerates GeoMapApp-ready and viewer-ready copies from these
sources. See `GMRT_regional/README.md`, `GMRT_regional/Backscatter_MGDS/README.md`,
and `GMRT_regional/Geophysics_MGDS/README.md` for per-dataset usage notes
and caveats; this file is the source-of-truth index.

## Bathymetry & backscatter — Marine Geoscience Data System (MGDS)

Fetched via `marine-geo.org/tools/search/Files.php?data_set_uid=<UID>`.
Destination: `GMRT_regional/Backscatter_MGDS/<folder>/`.

| Folder | MGDS UID(s) | Cruise | Description | Approx. size |
|---|---|---|---|---|
| `MGL1106_CostaRica_CRISP/` | 21024 | MGL1106 | Kongsberg EM122 5 m backscatter, offshore Costa Rica (2011) | 1.4 GB |
| `DRFT04RR_GSC_Bathymetry/` | 23860 | DRFT04RR | EM122 bathymetry, Galápagos Islands (2001) | 413 MB |
| `DRFT04RR_GSC_Backscatter/` | 23858, 23859 | DRFT04RR | EM122 sidescan/backscatter, Galápagos Islands (2001) | 6.0 GB |
| `TN188_GSC_Bathymetry_8m/` | 9471 | TN188 | DSL-120A bathymetry, Galápagos Spreading Center (2005) | 1.5 GB |
| `GSC_97-86W_Compilation/` | 21842 | (compilation) | 100 m regional bathymetry compilation, 97–86°W | 471 MB |

## Gravity, magnetics & seismic — MGDS

Destination: `GMRT_regional/Geophysics_MGDS/<folder>/`.

| Folder | MGDS UID | Description | Approx. size |
|---|---|---|---|
| `Barckhausen_CentralAmerica_Magnetics/` | 6524 | Magnetic anomaly (IGRF removed) compilation, Central America subduction zone | 1 MB |
| `Bassett_CentralAmerica_ResidualGravity/` | 24026 | Residual free-air gravity anomaly, Central America subduction zone (one region of a global 18-region compilation; PDF companion at UID 24028 not used) | 55 MB |
| `SR1806_CocosNazca_Gravity/` | 31302 | Six processed gravity products, cruise SR1806 (2018), Cocos-Nazca spreading center | 7.3 MB |
| `Mittelstaedt_GalapagosIslands_Gravity/` | 26493 | Onshore free-air gravity stations, Santa Cruz & San Cristóbal — [Cleary et al., 2020](https://doi.org/10.1029/2019GC008722) | broken at source (0-byte download, confirmed twice) |

## Not included

| Identifier | Description | Why not included |
|---|---|---|
| MGDS UID 33013 (MGL2304, "MarineIGUANA/IGUANA") | 3-D P/S-wave seismic tomography of the Galápagos Plume mantle structure (Hufstetler, Hooft, Toomey, Ito et al., 2026) | Access-restricted |
| MGDS UID 20650 (AT18-12) | Ready-made processed grid, East Pacific Rise 9°N, just north of this project's corridor | Not yet reviewed |
| MGDS UIDs 31315, 31316, 31317 (AT50-09BC) | Processed backscatter, sidescan, and swath bathymetry, Galápagos Islands (2023), NetCDF/GeoTIFF | Found but not yet pulled |
| MGDS UID 27009/30050 (FK181210), 27008 (FK190106) | R/V Falkor, Costa Rica/Osa Peninsula | Raw multibeam ping data (`.gsf`) only; requires MB-System to grid |
| MGDS UID 17619 (AT07-13), 17405 (AT11-27), 31828/31881 (AT15-63) | Galápagos Spreading Center multibeam | Raw/lightly processed ping data only; requires MB-System to grid |
| ~85 single-ship MGD77 trackline datasets | Vema, Robert D. Conrad, Eltanin, Maurice Ewing, Thomas Washington, some modern Atlantis legs, 1960s–2010s | Processed but thin one-pass transit lines, not regional products |
| 76 of 77 seismic-tagged corridor datasets | Various cruises | Raw SEGY shot/stack data, navigation files, or PDF quick-look plots — not usable without dedicated seismic-processing software |

## Regional basemap — Global Multi-Resolution Topography (GMRT)

Destination: `GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap.grd`.

Fetched from GMRT's GridServer:

```
https://www.gmrt.org/services/GridServer?minlongitude=-98.5&maxlongitude=-73.5&minlatitude=-4.5&maxlatitude=10.7&layer=topo&format=coards&resolution=max
```

11381×6951 nodes, ~245 m/node native, 633 MB. See `GMRT_regional/README.md`
for the `resolution` parameter's behavior and `Viewer3D/README.md` for how
this basemap was decimated for the 3D viewer.

## Cruise-internal data (not tracked, not publicly obtainable)

The MV1007 cruise's own processed bathymetry and backscatter set
(`GeoMapApp_ready/` locally) is extracted from
`dfornari_untitled-transfer_2026-03-11_2117.zip` (~60 GB), an internal HMRG
data transfer for cruise MV1007. This is not a public dataset and has no
external download link — it must be obtained directly from the cruise's
data originators. Once available locally, extract it with
`scripts/extract_geomapapp_layers.py` (see `scripts/README.md`).
