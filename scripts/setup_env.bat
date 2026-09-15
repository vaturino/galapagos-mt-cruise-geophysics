@echo off
REM Set up a self-contained Python virtual environment for the scripts in
REM this folder, so they run the same way on any computer this drive is
REM plugged into. Run this once on each Windows machine that needs to run
REM these scripts (needs internet access once, to fetch numpy/scipy/tifffile
REM from PyPI).
REM
REM Usage:
REM   cd scripts
REM   setup_env.bat
REM   venv\Scripts\activate
REM   python mosaic_geomapapp_grids.py --help

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo python not found on this machine -- install Python 3.9+ first.
    exit /b 1
)

if not exist venv (
    echo Creating virtual environment in %cd%\venv ...
    python -m venv venv
) else (
    echo venv already exists here, reusing it.
)

call venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt

echo.
echo Done. Environment ready at %cd%\venv
echo Activate it in a new shell with:
echo     venv\Scripts\activate
