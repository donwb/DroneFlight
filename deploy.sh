#!/usr/bin/env bash
# Deploy code changes to the live site (manual deploy).
#
# Commit your changes first, then run this. It pushes to GitHub, then tells the
# droplet to pull the latest code and restart the clips API.
#   git commit -am "what changed"
#   ./deploy.sh
#
# Override the server with:  DRONE_SSH=root@host ./deploy.sh
set -euo pipefail

: "${DRONE_SSH:=root@flights.donwb.com}"
cd "$(dirname "$0")"

echo "==> Pushing to GitHub"
git push

echo "==> Pulling latest + restarting on $DRONE_SSH"
ssh "$DRONE_SSH" 'cd /opt/droneflights && sudo -u droneflights git pull --ff-only && systemctl restart droneflights'

echo
echo "Done — live at https://flights.donwb.com"
echo "(static changes apply immediately; the clips API was restarted)"
