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
#   venv/bin/python3 mosaic_geomapapp_grids.py --help
#
# Run scripts as `venv/bin/python3 script.py`, not `source venv/bin/activate`
# first -- activate's PATH setup is a literal absolute path baked in at
# creation time and silently breaks (falls through to system Python with no
# error) if this venv is ever activated from a different absolute-path
# context than the one that created it -- e.g. created through a mounted/
# bridged view of this drive, then activated later from a normal local
# terminal, or vice versa. See scripts/README.md's "One-time setup" for the
# full explanation.

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

venv/bin/python3 -m pip install --upgrade pip >/dev/null
venv/bin/python3 -m pip install -r requirements.txt

echo
echo "Done. Environment ready at $(pwd)/venv"
echo "Run scripts with it directly (don't 'source activate' -- see the note"
echo "at the top of this file / scripts/README.md's 'One-time setup'):"
echo "    $(pwd)/venv/bin/python3 some_script.py"
