#!/usr/bin/env bash
# Set up a self-contained Python virtual environment for the scripts in this
# folder, so they run the same way on any computer this drive is plugged
# into -- without depending on whatever's already installed system-wide (or
# in someone's personal conda setup) on that particular machine.
#
# A venv is tied to the OS/architecture it was created on, so it can't be
# copied between computers -- run this script once on EACH machine that
# needs to run these scripts (Mac, Linux, or Windows-with-WSL). It only
# takes a few seconds and needs internet access once, to download numpy/
# scipy/tifffile from PyPI.
#
# Usage:
#   cd scripts
#   ./setup_env.sh
#   source venv/bin/activate
#   python3 mosaic_geomapapp_grids.py --help

set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found on this machine -- install Python 3.9+ first." >&2
    exit 1
fi

if [ ! -d venv ]; then
    echo "Creating virtual environment in $(pwd)/venv ..."
    python3 -m venv venv
else
    echo "venv/ already exists here, reusing it."
fi

# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo
echo "Done. Environment ready at $(pwd)/venv"
echo "Activate it in a new shell with:"
echo "    source $(pwd)/venv/bin/activate"
