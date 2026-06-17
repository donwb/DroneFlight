#!/usr/bin/env bash
# Add new DJI flights after a trip: ingest them locally, then sync to the live site.
# This is CONTENT, not a code deploy — no restart needed.
#
#   ./add-flights.sh /Volumes/SDCARD/DCIM          # a folder/SD card (scanned for *.LRF)
#   ./add-flights.sh ~/Desktop/DJI_1234_D.LRF ...  # or specific files
#
# Both steps are safe to re-run: ingest skips flights already imported, and the
# upload only sends new/changed files. Override the server with:
#   DRONE_HOST=droneflights@host ./add-flights.sh <path>
set -euo pipefail

: "${DRONE_HOST:=droneflights@flights.donwb.com}"
cd "$(dirname "$0")"

if [ "$#" -eq 0 ]; then
  echo "Usage: ./add-flights.sh <folder-or-LRF> [more...]"
  echo "  e.g. ./add-flights.sh /Volumes/SDCARD/DCIM"
  exit 1
fi

echo "==> Ingesting new flights locally"
python3 ingest.py "$@"

echo
echo "==> Uploading to $DRONE_HOST"
DRONE_HOST="$DRONE_HOST" ./publish.sh

echo
echo "Done — refresh https://flights.donwb.com to see the new flights."
