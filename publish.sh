#!/usr/bin/env bash
# Sync the local library's MEDIA + DATA to the droplet.
# Code is deployed separately by GitHub Actions on push; this handles the big
# files that don't belong in git (proxy.mp4, thumb.jpg, flight.json, manifest, clips).
#
# Usage:
#   DRONE_HOST=droneflights@1.2.3.4 ./publish.sh
#   (optional)  DRONE_DEST=/opt/droneflights   DRONE_DELETE=1 ./publish.sh
#
# First run uploads everything (~10GB once); later runs send only changes.
set -euo pipefail

: "${DRONE_HOST:?set DRONE_HOST=user@droplet-ip-or-host}"
DEST="${DRONE_DEST:-/opt/droneflights}"

# --delete removes server files no longer present locally (off by default for safety)
DEL=()
[ "${DRONE_DELETE:-0}" = "1" ] && DEL=(--delete)

echo "Syncing flights/ -> $DRONE_HOST:$DEST/flights/"
rsync -avh --progress "${DEL[@]}" flights/ "$DRONE_HOST:$DEST/flights/"

echo "Syncing manifest.json"
rsync -avh manifest.json "$DRONE_HOST:$DEST/manifest.json"

echo
echo "Done. New/changed flights are live."
