# How to re-run the site maps and tables yourself

These scripts make everything in `../1_Coordinates/` and `../2_Maps/`. The
files in this folder are a copy for reference. **Run the scripts from the
repo, not from here**: they find the bathymetry grids and the site files
relative to the repo folder.

- Repo on this machine: `/media/tmittal/extradrive1/galapagos-mt-cruise-geophysics/`
- GitHub: https://github.com/vaturino/galapagos-mt-cruise-geophysics (`main`)
- The scripts live in `galapagos-mt-cruise-geophysics/Site_Maps/`.

A full run takes about 35 s on this machine.

## 1. Quick start (this machine, everything already in place)

```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env
cd /media/tmittal/extradrive1/galapagos-mt-cruise-geophysics
git pull
cd Site_Maps
python make_site_maps.py            # ~16 s  site maps + coordinate tables
python compare_dredge_lists.py      # ~2 s   permit vs 29-Sep comparison
python make_previous_dredge_maps.py # ~14 s  previous-dredge maps, zooms, MV1007
python make_repeat_site_map.py      # ~4 s   D7/D25/D26 repeat-site map
```

Run them in this order. The later scripts import helpers from
`make_site_maps.py`, but each one reads its own inputs, so any single
script can be re-run on its own.

Then copy the results into this Permits folder (step 5).

## 2. What each script makes

| Script | Outputs (written next to the script, in `Site_Maps/`) |
|---|---|
| `make_site_maps.py` | `Dredge_Sites_Map`, `MT_Sites_Map`, `Combined_Sites_Map`, plus a `_Reserves_Map` version of each (`.png` + `.pdf`; the PDFs add coordinate-table pages). Also `Dredge_Sites_Table.csv`, `MT_Sites_Table.csv` and `AT5304_Site_Coordinates.xlsx`. |
| `compare_dredge_lists.py` | `Dredge_Permit_vs_Sep29_Comparison.png` / `.csv` |
| `make_previous_dredge_maps.py` | `Permit_Dredges_with_Previous_Map`, `Dredge_Glass_Status_Map`, `Dredge_Zoom_A`-`D` (+ `Dredge_Zooms_All.pdf`), `MV1007_Dredges_Map`, `Previous_Dredges_Regional_Map`. Also `Previous_Dredges_Compiled.csv` and `Permit_Sites_Nearest_Previous.csv`. |
| `make_repeat_site_map.py` | `Repeat_Dredge_Sites_Map.png` / `.pdf`, `Repeat_Dredge_Sites.csv` |

## 3. Inputs, and where they come from

**Tracked in git** (come with `git pull`):
- `MT_dredging_coords/DredgeSites.csv`: the 30 permit dredge sites, in
  planned order. **This is the file to edit if the dredge plan changes.**
- `MT_dredging_coords/MTsites.csv`: the 90 MT sites, in planned order.
- `Site_Maps/reserves/RMG.*` and `RMH.*`: DPNG marine-reserve shapefiles.
- `scripts/netcdf_lite.py`: the grid reader, imported automatically.

**Not in git** (large data; must already be in the repo folder):

| File (path inside the repo) | Used by | Where to get it |
|---|---|---|
| `GMRT_regional/GMRT_Basemap/GMRT_corridor_basemap_clean.grd` (633 MB) | all maps, table depths | **Copy it from this machine's repo** (md5 `92e3497ce3fca61b26e84183cb972a28`). See the warning below. |
| `FOR_TUSHAR/CUT_bath_clean.tif` (94 MB) | table depths (50 m grid), zoom maps | Datasets drive: `/media/tmittal/Datasets/FOR_TUSHAR/`. Mittelstaedt/Young compilation. |
| `GMRT_regional/GMRT_Basemap/GMRT_GSC_wide_high.grd` (11 MB) | regional map only | Download, no login: `curl -L -o GMRT_GSC_wide_high.grd "https://www.gmrt.org/services/GridServer?north=4.0&south=-1.5&east=-82.5&west=-102.5&layer=topo&format=coards&resolution=high"`. Should be 11,450,568 bytes. |

**⚠ Use the right basemap** (md5 `92e3497ce3fca61b26e84183cb972a28`).
An older version (md5 `a0fdca3d…`) has only the Wolf/Darwin spikes removed,
so maps made from it show the bad 3-7 km spikes in Colombia/Ecuador/Panama.
The Datasets drive had that older version until 2026-09-29. It was then
replaced with the correct file; the old one is kept as
`GMRT_corridor_basemap_clean_OLD_wolfdarwin_only.grd`. To rebuild the
correct grid from scratch, run from the repo's `scripts/` folder with the
repo venv:

```bash
venv/bin/python3 fetch_copernicus_reference.py   # needs internet once (~1 min)
venv/bin/python3 fix_gmrt_spikes.py              # ~2 min
venv/bin/python3 validate_gmrt_clean.py          # must end "ALL CHECKS PASSED"
```

**Paths outside the repo** (hard-coded near the top of two scripts; edit
them if these files move):
- `make_previous_dredge_maps.py`, line `PREV = ...`: the folder with
  `Galápagos samples_lats and longs.xlsx` and
  `MV1007_DredgeLog_167_2358_KL Edited.xls`. It currently points at
  `Work_AI/Permits/9_AT5304_Final_Sites_and_Maps/4_prev_dredges/`.
- `compare_dredge_lists.py`, line `PERMIT_CSV = ...`: the permit dredge list,
  `Work_AI/Permits/1_Dredging/3_sites/Combined_Cruise_Sites_Dredge.csv`.

## 4. Python environment

On this machine, use `claude-science-env`. It has everything:
numpy 2.4, scipy 1.17, pandas 3.0, matplotlib 3.10, cartopy 0.25,
geopandas 1.1, shapely 2.1, rasterio 1.4, cmocean 3.0, openpyxl 3.1.

Always activate it in the same shell command you run python in, and check
the interpreter path contains `envs/claude-science-env`:

```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate claude-science-env && python -c "import sys; print(sys.executable)"
```

On another machine, make an equivalent environment:

```bash
conda create -n sitemaps -c conda-forge python=3.11 numpy scipy pandas matplotlib cartopy geopandas shapely rasterio cmocean openpyxl
```

The repo's own `scripts/venv` is **not** enough: it has no cartopy,
geopandas or cmocean.

## 5. Copy results into this Permits folder

From `galapagos-mt-cruise-geophysics/Site_Maps/`:

```bash
T=/home/tmittal/Dropbox/Work_AI/Permits/9_AT5304_Final_Sites_and_Maps
cp *.png *.pdf Dredge_Permit_vs_Sep29_Comparison.csv "$T/2_Maps/"
cp Dredge_Sites_Table.csv MT_Sites_Table.csv AT5304_Site_Coordinates.xlsx Previous_Dredges_Compiled.csv Permit_Sites_Nearest_Previous.csv Repeat_Dredge_Sites.csv "$T/1_Coordinates/"
cp *.py "$T/4_Scripts/" && cp -r reserves "$T/4_Scripts/" && cp README.md "$T/4_Scripts/Site_Maps_README.md"
cp Repeat_Dredge_Sites_NOTE.md "$T/"
cp ../MT_dredging_coords/DredgeSites.* ../MT_dredging_coords/MTsites.* "$T/3_Site_Files/"
```

## 6. Common changes

- **Dredge or MT plan changes** (new positions, order or depths): edit
  `MT_dredging_coords/DredgeSites.csv` / `MTsites.csv`. Keep the columns
  `site,latitude,longitude,depth,status`: site numbers 1..N with no gaps,
  west longitude negative, depth in metres positive down. Then re-run all
  four scripts. If a site's listed depth is more than 300 m off the
  bathymetry, `make_site_maps.py` prints a `FLAG` line and highlights the
  cell orange in the PDF tables.
- **Colour range too narrow or wide:** `site_depth_range()` in
  `make_site_maps.py`. The range is the site depths ±300 m (`margin=`),
  rounded to 250 m (`step=`). The previous-dredge maps use the 5th-95th
  percentile of site depths (`pct=(5, 95)`).
- **Repeat-site threshold:** `REPEAT_KM = 2.0` at the top of
  `make_repeat_site_map.py`.
- **Depth-flag threshold:** `DEPTH_FLAG_M = 300` at the top of
  `make_site_maps.py`.

## 7. Committing and notes

- After a run, git shows the PDFs and the `.xlsx` as changed even when
  nothing else changed, because they embed a creation timestamp. If the
  CSVs are unchanged (`git status --short Site_Maps`), discard them with
  `git checkout -- Site_Maps`. Otherwise commit, e.g.
  `git add Site_Maps MT_dredging_coords && git commit -m "..." && git push`.
- If git says "Author identity unknown", commit with
  `git -c user.name="Tushar Mittal" -c user.email="tmittal@psu.edu" commit ...`.
  If a large push drops, retry with `git -c http.postBuffer=524288000 push`.
- Data corrections are applied in the code and listed in each script's
  docstring:
  - MV1007 D14 off-bottom longitude: 92°00.157'W.
  - The two east-longitude sign typos, which are also fixed in the
    spreadsheet itself.
