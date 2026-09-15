# Data manifest

Every dataset this project uses: source, identifier, format, approximate
size, and the exact link needed to fetch it again. None of these files are
committed to this repository — `scripts/` regenerates GeoMapApp-ready and
viewer-ready copies from these sources. See `GMRT_regional/README.md`,
`GMRT_regional/Backscatter_MGDS/README.md`, and
`GMRT_regional/Geophysics_MGDS/README.md` for per-dataset usage notes and
caveats; this file is the source-of-truth index and the one place every
dataset's citation/download link is collected.

## How to fetch an MGDS dataset

Every "Dataset page" link below is a normal MGDS page
(`marine-geo.org/tools/datasets/<UID>`) listing that dataset's files,
citation, and license. Downloading requires a few clicks on that page —
select the files (or "Select All"), click **Download Selected File(s)**,
accept MGDS's terms of use, choose an intended-use category, then click
**Download** — MGDS always completes that last step by opening a new browser
tab, regardless of how the request is made, so it can't be scripted; it has
to be a real click in a real browser. The result lands in the browser's
normal Downloads folder as `MGDS_Download.tar` (a gzipped tarball — `tar
-xvf` then `gunzip` each `.grd.gz`/`.tif.gz` inside). Each dataset's own DOI
is the citable, permanent identifier if the page URL or files ever move.

## Bathymetry & backscatter — Marine Geoscience Data System (MGDS)

Destination: `GMRT_regional/Backscatter_MGDS/<folder>/`.

| Folder | Cruise | Description | Approx. size | Dataset page | DOI |
|---|---|---|---|---|---|
| `MGL1106_CostaRica_CRISP/` | MGL1106 | Kongsberg EM122 5 m backscatter, offshore Costa Rica (2011) | 1.4 GB | [UID 21024](https://www.marine-geo.org/tools/datasets/21024) | [10.1594/IEDA/321024](https://doi.org/10.1594/IEDA/321024) |
| `DRFT04RR_GSC_Bathymetry/` | DRFT04RR | EM122 bathymetry, Galápagos Islands (2001) | 413 MB | [UID 23860](https://www.marine-geo.org/tools/datasets/23860) | [10.1594/IEDA/323860](https://doi.org/10.1594/IEDA/323860) |
| `DRFT04RR_GSC_Backscatter/` | DRFT04RR | EM122 sidescan/backscatter, Galápagos Islands (2001) — two file formats of the same survey, both pulled | 6.0 GB | [UID 23858](https://www.marine-geo.org/tools/datasets/23858) (NetCDF), [UID 23859](https://www.marine-geo.org/tools/datasets/23859) (ESRI ASCII) | [10.1594/IEDA/323858](https://doi.org/10.1594/IEDA/323858), [10.1594/IEDA/323859](https://doi.org/10.1594/IEDA/323859) |
| `TN188_GSC_Bathymetry_8m/` | TN188 | DSL-120A bathymetry, Galápagos Spreading Center (2005) | 1.5 GB | [UID 9471](https://www.marine-geo.org/tools/datasets/9471) | [10.1594/IEDA/100260](https://doi.org/10.1594/IEDA/100260) |
| `GSC_97-86W_Compilation/` | (compilation) | 100 m regional bathymetry compilation, 97–86°W | 471 MB | [UID 21842](https://www.marine-geo.org/tools/datasets/21842) | [10.1594/IEDA/321842](https://doi.org/10.1594/IEDA/321842) |
| `AT50-09BC_GalapagosPlatform_Bathymetry/` | AT50-09BC | EM124 multibeam bathymetry, 8 grids (15–50 m) actually covering the Galápagos Platform itself — Central/Eastern/Southern/Western Platform, Pinta Rift, Coral Croissant, plus the wider Galápagos/Ecuador-EEZ extents (2023) | 1.5 GB (both the as-downloaded and `_geomapapp` grids) | [UID 31315](https://www.marine-geo.org/tools/datasets/31315) | [10.26022/IEDA/331315](https://doi.org/10.26022/IEDA/331315) |

**AT50-09BC license note:** CC BY-NC-SA 3.0 (non-commercial, share-alike) —
stricter than the CC BY-NC-SA 3.0 US license on the other MGDS pulls above
only in that MGDS's own page lists it without the "US" port; treat it the
same way (attribution required, no commercial use, share derivatives under
the same license).

**AT50-09BC data quirk:** the as-downloaded grids encode nodata as literal
IEEE +Infinity in the `z` array, with no `_FillValue` attribute to catch it
— every other MGDS grid in this project used either a `_FillValue` or plain
NaN. `scripts/fix_mgds_grid.py` now detects and scrubs `+/-inf` unconditionally
(previously it only replaced values matching an explicit `_FillValue`), so
re-running it on any of these 8 files reproduces the `_geomapapp` twins
exactly; a version of the script from before this fix would silently bake
`inf` into the output's `actual_range` and data instead.

Two sibling MGDS datasets re-export the same AT50-09BC survey as GeoTIFF
instead of NetCDF and were not pulled (redundant with the grids above, and
not needed since this project isn't doing QGIS work): [UID 31316](https://www.marine-geo.org/tools/datasets/31316)
(colored/hillshaded "FPGT" GeoTIFF) and [UID 31317](https://www.marine-geo.org/tools/datasets/31317)
(plain elevation GeoTIFF).

## Gravity, magnetics & seismic — MGDS

Destination: `GMRT_regional/Geophysics_MGDS/<folder>/`.

| Folder | Description | Approx. size | Dataset page | DOI |
|---|---|---|---|---|
| `Barckhausen_CentralAmerica_Magnetics/` | Magnetic anomaly (IGRF removed) compilation, Central America subduction zone | 1 MB | [UID 6524](https://www.marine-geo.org/tools/datasets/6524) | [10.1594/IEDA/306524](https://doi.org/10.1594/IEDA/306524) |
| `Bassett_CentralAmerica_ResidualGravity/` | Residual free-air gravity anomaly, Central America subduction zone (one region of a global 18-region compilation) | 55 MB | [UID 24026](https://www.marine-geo.org/tools/datasets/24026) (PDF companion: [UID 24028](https://www.marine-geo.org/tools/datasets/24028), not used) | [10.1594/IEDA/324026](https://doi.org/10.1594/IEDA/324026) |
| `SR1806_CocosNazca_Gravity/` | Six processed gravity products, cruise SR1806 (2018), Cocos-Nazca spreading center | 7.3 MB | [UID 31302](https://www.marine-geo.org/tools/datasets/31302) | Zenodo [10.5281/zenodo.7590816](https://doi.org/10.5281/zenodo.7590816) (data); paper [10.1029/2022GL102133](https://doi.org/10.1029/2022GL102133) |
| `Mittelstaedt_GalapagosIslands_Gravity/` | Onshore free-air gravity stations, Santa Cruz & San Cristóbal | broken at source (0-byte download, confirmed twice) | [UID 26493](https://www.marine-geo.org/tools/datasets/26493) | [10.1594/IEDA/326493](https://doi.org/10.1594/IEDA/326493) (dataset); paper [Cleary et al., 2020, 10.1029/2019GC008722](https://doi.org/10.1029/2019GC008722) |

## Not included

| Identifier | Description | Why not included |
|---|---|---|
| [UID 33013](https://www.marine-geo.org/tools/datasets/33013) (MGL2304, "MarineIGUANA/IGUANA") | 3-D P/S-wave seismic tomography of the Galápagos Plume mantle structure (Hufstetler, Hooft, Toomey, Ito et al., 2026) | Access-restricted |
| [UID 20650](https://www.marine-geo.org/tools/datasets/20650) (AT18-12) | Ready-made processed grid, East Pacific Rise 9°N, just north of this project's corridor | Not yet reviewed |
| [UID 31316](https://www.marine-geo.org/tools/datasets/31316), [UID 31317](https://www.marine-geo.org/tools/datasets/31317) (AT50-09BC) | Same Galápagos Platform bathymetry as `AT50-09BC_GalapagosPlatform_Bathymetry/` above, re-exported as GeoTIFF | Redundant with the NetCDF grids already pulled; not needed without QGIS work |
| [UID 27009/30050](https://www.marine-geo.org/tools/datasets/27009) (FK181210), [UID 27008](https://www.marine-geo.org/tools/datasets/27008) (FK190106) | R/V Falkor, Costa Rica/Osa Peninsula | Raw multibeam ping data (`.gsf`) only; requires MB-System to grid |
| [UID 17619](https://www.marine-geo.org/tools/datasets/17619) (AT07-13), [UID 17405](https://www.marine-geo.org/tools/datasets/17405) (AT11-27), [UID 31828/31881](https://www.marine-geo.org/tools/datasets/31828) (AT15-63) | Galápagos Spreading Center multibeam | Raw/lightly processed ping data only; requires MB-System to grid |
| ~85 single-ship MGD77 trackline datasets | Vema, Robert D. Conrad, Eltanin, Maurice Ewing, Thomas Washington, some modern Atlantis legs, 1960s–2010s | Processed but thin one-pass transit lines, not regional products |
| 76 of 77 seismic-tagged corridor datasets | Various cruises | Raw SEGY shot/stack data, navigation files, or PDF quick-look plots — not usable without dedicated seismic-processing software |

## Regional basemap — Global Multi-Resolution Topography (GMRT)

Destination: `GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap.grd`.

Exact fetch URL used (GMRT's GridServer, a plain unauthenticated GET — no
login, no click-through, works from a direct browser navigation):

```
https://www.gmrt.org/services/GridServer?north=10.7&south=-4.5&east=-73.5&west=-98.5&layer=topo&format=coards&resolution=max
```

11381×6951 nodes, ~245 m/node native, 633 MB, `layer=topo` (GEBCO-filled).
See `GMRT_regional/README.md` for the `resolution` parameter's behavior
(nodes-per-side budget, not fixed spacing — the same URL at a wider bbox
returns a coarser grid) and `Viewer3D/README.md` for how this basemap was
decimated for the 3D viewer.

## Cruise-internal data (not tracked, not publicly obtainable)

The MV1007 cruise's own processed bathymetry and backscatter set
(`GeoMapApp_ready/` locally) is extracted from
`dfornari_untitled-transfer_2026-03-11_2117.zip` (~60 GB), an internal HMRG
data transfer for cruise MV1007. This is not a public dataset and has no
external download link — it must be obtained directly from the cruise's
data originators. Once available locally, extract it with
`scripts/extract_geomapapp_layers.py` (see `scripts/README.md`).
