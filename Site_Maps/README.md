# Site_Maps

Maps and coordinate tables for the AT53-04 dredge and MT sites, with every
site numbered in planned order.

| File | What it is |
|---|---|
| `Dredge_Sites_Map.png` / `.pdf` | 30 dredge sites, D1-D30, joined in planned order, on bathymetry only. The PDF adds a coordinate table page. |
| `MT_Sites_Map.png` / `.pdf` | 90 MT sites, 1-90, joined in planned order. The PDF adds 3 table pages. |
| `Combined_Sites_Map.png` / `.pdf` | Both sets, dredge track dark red and MT track black. The PDF adds both tables. |
| `*_Reserves_Map.png` / `.pdf` | The same three maps with the marine-reserve zones filled and labelled: Galapagos Marine Reserve, Hermandad No-Take zone, Hermandad Responsible-Fishing zone. An overview inset shows both reserves in full and where the map sits. |
| `AT5304_Site_Coordinates.xlsx` | Both tables in one workbook (sheets `Dredge` and `MT`), for sharing. |
| `Dredge_Sites_Table.csv`, `MT_Sites_Table.csv` | The same tables as CSV. See "Table columns" below. |
| `Dredge_Permit_vs_Sep29_Comparison.png` / `.csv` | The permit dredge sites next to the alternative list that was briefly in `DredgeSites.csv` on 2026-09-29 (not used). |
| `Permit_Dredges_with_Previous_Map`, `Dredge_Glass_Status_Map`, `Dredge_Zoom_A`-`D` (+ `Dredge_Zooms_All.pdf`), `MV1007_Dredges_Map`, `Previous_Dredges_Regional_Map` (`.png`/`.pdf`) | Permit dredge sites with all previous dredges (MV1007, PLUME02, SO158, GSC cruises), by cruise and glass recovery, on a cmocean "deep" depth scale with a colour bar. Made by `make_previous_dredge_maps.py`. |
| `Repeat_Dredge_Sites_Map.png` / `.pdf`, `Repeat_Dredge_Sites.csv`, `Repeat_Dredge_Sites_NOTE.md` | D7, D25 and D26 are within 1 km of MV1007 dredges that recovered no glass or no rock. Decide before the cruise. Made by `make_repeat_site_map.py`. |
| `Previous_Dredges_Compiled.csv`, `Permit_Sites_Nearest_Previous.csv` | Every previous dredge station (104), and each permit site's nearest previous dredge and nearest glass-bearing one. |
| `make_site_maps.py`, `compare_dredge_lists.py`, `make_previous_dredge_maps.py`, `make_repeat_site_map.py` | Regenerate everything above. `make_previous_dredge_maps.py` reads the previous-dredge spreadsheets from `Work_AI/Permits/9_AT5304_Final_Sites_and_Maps/4_prev_dredges/` (path at the top of the script). |
| `dredge_plan.py` | Planned tows: writes/updates `MT_dredging_coords/DredgeLines.csv` (one line per site: start/end, length, azimuth, depths, slopes, grid used, flags). See `HOW_TO_RUN.md`. |
| `make_dredge_packets.py` | One data packet per planned dredge in `../Dredge_Packets/` (sheet + slope map, waypoints GPX/KML/CSV, profile CSV, native-resolution GeoTIFF clips for the navigation system). Each folder has a README explaining the plots and parameters. |
| `reserves/` | DPNG shapefiles of the Galapagos (RMG) and Hermandad (RMH) marine reserves, copied from `Work_AI/Permits/5_Sites_permits/Codes/Locations/`. |

**Source of the sites:** `MT_dredging_coords/DredgeSites.csv` and
`MTsites.csv`. "Sequence" means the site number in those files, and the
maps join the sites in that order. If the order at sea changes, edit the
CSVs and re-run.

- **Dredge sites are the permit sites.** They are the 30 sites in the
  dredge table of the submitted DPNG proposal
  (`FORMATO_PROPUESTA_INVESTIGACION 2025_TusharMittal_NSF_Nerc_Fixed.pdf`),
  in the same order and with the same depths. That table is identical to
  `Work_AI/Permits/1_Dredging/3_sites/Combined_Cruise_Sites_Dredge.csv`,
  the "lats and longs" spreadsheet, and the Dredge SOP database.
  `DredgeSites.csv` held a different list briefly on 2026-09-29. That list
  is kept as `DredgeSites_Sep29_alternative.csv` and is **not used**.
- **MT sites:** 87 of the 90 are permit MT stations, with the same
  positions and depths. MT46-48 are new since the permit table (see below).

## Regenerate

```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env
cd Site_Maps && python make_site_maps.py
```

This needs matplotlib, cartopy, geopandas and rasterio, which the repo's
`scripts/venv` doesn't include. It also needs the repaired GMRT basemap
(`GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap_clean.grd`) and
`FOR_TUSHAR/CUT_bath_clean.tif`. See `DATA_MANIFEST.md`.

Labels are placed automatically. Each label tries 16 positions around its
site and takes the one that overlaps least with markers and labels
already placed. Dredge labels are placed first.

## Table columns

- `site_id`, `sequence`: D01..D30 or MT01..MT90, and the planned order.
- `lat_dd`, `lon_dd`: WGS-84 decimal degrees, 5 decimals, west negative.
- `lat_ddm`, `lon_ddm`: degrees and decimal minutes (e.g. `1°56.6885' N`),
  the usual bridge format.
- `lat_dms`, `lon_dms`: degrees, minutes, seconds (e.g. `1°56'41.31" N`).
  Converting back to decimal matches `lat_dd`/`lon_dd` within 0.6 m. That
  residual is the rounding of the 5-decimal column; the source values are
  more precise.
- `depth_listed_m`: depth from the planning file.
- `depth_50m_m`: depth from the Mittelstaedt/Young 50 m platform grid.
  Blank for sites outside its 0.5S-2.0N coverage.
- `depth_gmrt_m`: depth from the repaired GMRT grid (~245 m cells).
- `depth_flag`: filled in when the listed depth is more than 300 m off the
  bathymetry.
- `reserve_zone`: point-in-polygon result against `reserves/`.
- `leg_from_prev_km`, `cumulative_km`: great-circle distances along the
  sequence.

## Checks (as of 2026-09-29)

**Dredge sites vs the permit.** Each `DredgeSites.csv` row matches its
permit-table row, in order, to within 0.005 deg (the proposal prints 2
decimals). All 30 depths are identical to the permit table. 29 of 30
depths equal GMRT's online point service exactly, and one is 1 m off.
Against the independent 50 m Mittelstaedt grid, the listed depths differ
by a median of 7 m, maximum 28 m (D28).

**MT sites vs the permit.**
- 87 of 90 match a permit MT station, with identical depths.
- **MT46-MT48 are not in the permit table.** They are 18-54 km from any
  permit station, but inside the permitted area (Galapagos MR / Hermandad).
  Their depths were placeholders and were fixed on 2026-09-29 to 2770,
  2712 and 2547 m. These come from GMRT's point service, the same source
  as every other MT depth:

  | Site | GMRT point service | GMRT grid (245 m) | Mittelstaedt 50 m | DOA-ETP multibeam (100 m) |
  |---|---|---|---|---|
  | MT46 | **2770** | 2770 | 2780 | no data |
  | MT47 | **2712** | 2710 | 2710 | 2709 |
  | MT48 | **2547** | 2540 | 2575 | no data |

  MT48 is the least certain: the grids disagree by 35 m, and the depth
  within 250 m ranges from 2524 to 2585 m.
- Permit MT stations 23, 25-30, 58, 60 and 91-94 are not in the current
  plan.
- All 90 MT depths (1591-3495 m) are inside the permit's 1400-3500 m
  range for seafloor instruments (condition 20).

**The permit (PC-103-26) lists no coordinates.** It authorizes an area:
"cadenas volcanicas y dorsal de expansion adyacente al norte de Galapagos
(Reserva Marina de Galapagos y Hermandad)" (condition 19). New study sites
need a SOLICITUD DE INCLUSION (condition 18). Reserve zones from the DPNG
shapefiles:
- **Dredge:** 29 of 30 are in the Galapagos MR. **D21** (1.89588 N,
  91.13062 W) is just inside the **Hermandad No-Take zone**, near the
  reserve boundary.
- **MT:**
  - 75 of 90 are in the Galapagos MR.
  - MT74 is in the Hermandad No-Take zone.
  - MT16-17, MT45-47 and MT75-78 are in the Hermandad Responsible-Fishing
    zone.
  - **MT11-15 are outside both reserve polygons**, so they may fall outside
    the permit's authorized area. Check.
- The polygons' boundary precision hasn't been verified here.
