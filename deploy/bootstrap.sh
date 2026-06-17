#!/usr/bin/env bash
# DroneFlights — one-shot droplet setup (step 3). Run as root on a fresh Ubuntu droplet.
#
# It is idempotent: safe to re-run (re-running pulls latest code + re-renders config).
# It installs Python/git/Caddy, creates the app user, clones your repo, installs the
# clips-API systemd service, and configures Caddy with HTTPS + a password gate.
#
# Prereqs: droplet exists, your domain's DNS A-record points at it, repo is on GitHub.
#
# Get it onto the droplet and run it:
#   PUBLIC repo:   curl -fsSL -o bootstrap.sh \
#                    https://raw.githubusercontent.com/YOU/REPO/main/deploy/bootstrap.sh
#                  sudo bash bootstrap.sh
#   PRIVATE repo:  (from your Mac, in the repo)  scp deploy/bootstrap.sh root@DROPLET_IP:
#                  ssh -t root@DROPLET_IP 'bash bootstrap.sh'
#
# Config comes from prompts, or set these env vars to skip them:
#   DOMAIN, REPO_URL, AUTH_USER (default: don), AUTH_PASS
#
# NOTE: AUTH_USER / AUTH_PASS are the WEBSITE login you're creating (Caddy basic
# auth) — NOT a GitHub login. A public repo clones anonymously; no Git creds needed.
set -euo pipefail

APP_USER=droneflights
APP_DIR=/opt/droneflights
PORT=8778

[ "$(id -u)" -eq 0 ] || { echo "Please run as root (sudo)."; exit 1; }

# ---- config: env first, then prompt ----
DEFAULT_REPO="https://github.com/donwb/DroneFlight.git"
: "${DOMAIN:=}"; : "${REPO_URL:=}"; : "${AUTH_USER:=}"; : "${AUTH_PASS:=}"
[ -n "$DOMAIN" ]   || read -rp "Domain (e.g. flights.donwb.com): " DOMAIN
[ -n "$REPO_URL" ] || { read -rp "Git repo URL [$DEFAULT_REPO]: " REPO_URL; REPO_URL=${REPO_URL:-$DEFAULT_REPO}; }
# These are the website's password gate (Caddy basic auth) that you're creating now.
# NOT your GitHub credentials — the public repo clones with no login.
[ -n "$AUTH_USER" ] || { read -rp  "Choose a site login username [don]: " AUTH_USER; AUTH_USER=${AUTH_USER:-don}; }
[ -n "$AUTH_PASS" ] || { read -rsp "Choose a site login password: " AUTH_PASS; echo; }
[ -n "$DOMAIN" ] && [ -n "$REPO_URL" ] && [ -n "$AUTH_PASS" ] || {
  echo "Domain, repo URL, and a site password are all required."; exit 1; }

echo "==> App user + SSH access"
id "$APP_USER" &>/dev/null || adduser --disabled-password --gecos "" "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
install -d -o "$APP_USER" -g "$APP_USER" -m 700 "/home/$APP_USER/.ssh"
if [ -f /root/.ssh/authorized_keys ]; then           # let publish.sh / CI log in as the app user
  cp /root/.ssh/authorized_keys "/home/$APP_USER/.ssh/authorized_keys"
  chown "$APP_USER:$APP_USER" "/home/$APP_USER/.ssh/authorized_keys"
  chmod 600 "/home/$APP_USER/.ssh/authorized_keys"
fi

echo "==> Firewall (ssh + web only)"
ufw allow OpenSSH >/dev/null
ufw allow 80,443/tcp >/dev/null
ufw --force enable >/dev/null

echo "==> Packages (python3, git, caddy)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -qq
apt-get install -y -qq python3 git debian-keyring debian-archive-keyring apt-transport-https curl
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -y -qq
  apt-get install -y -qq caddy
fi

echo "==> Code"
sudo -u "$APP_USER" bash -c "ssh-keyscan -t ed25519,rsa github.com >> ~/.ssh/known_hosts 2>/dev/null" || true
if [ -d "$APP_DIR/.git" ]; then
  sudo -u "$APP_USER" env GIT_TERMINAL_PROMPT=0 git -C "$APP_DIR" pull --ff-only || true
elif ! sudo -u "$APP_USER" env GIT_TERMINAL_PROMPT=0 git clone "$REPO_URL" "$APP_DIR" 2>/tmp/clone.err; then
  KEY="/home/$APP_USER/.ssh/id_ed25519"
  sudo -u "$APP_USER" bash -c "[ -f '$KEY' ] || ssh-keygen -t ed25519 -N '' -f '$KEY'"
  echo
  echo "Clone failed (likely a private repo). Add this READ-ONLY deploy key to GitHub"
  echo "  (repo → Settings → Deploy keys → Add), then re-run this script with the git@ URL:"
  echo "------------------------------------------------------------------"
  cat "$KEY.pub"
  echo "------------------------------------------------------------------"
  exit 1
fi

echo "==> Clips API service"
cp "$APP_DIR/deploy/droneflights.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now droneflights
systemctl restart droneflights

echo "==> Caddy site (HTTPS + password)"
HASH="$(caddy hash-password --plaintext "$AUTH_PASS")"
cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
    root * $APP_DIR
    basic_auth {
        $AUTH_USER $HASH
    }
    @api path /api/*
    reverse_proxy @api 127.0.0.1:$PORT
    file_server
    encode gzip zstd
}
EOF
systemctl enable caddy >/dev/null 2>&1 || true
systemctl reload caddy 2>/dev/null || systemctl restart caddy

echo "==> Status"
sleep 2
systemctl is-active --quiet droneflights && echo "  clips API: running" || echo "  clips API: NOT running — check: journalctl -u droneflights -n 50"
systemctl is-active --quiet caddy        && echo "  caddy:     running" || echo "  caddy:     NOT running — check: journalctl -u caddy -n 50"
echo
echo "Done. Open https://$DOMAIN  (login: $AUTH_USER)"
echo "It will say 'No flights yet' until you upload media from your Mac:"
echo "  DRONE_HOST=$APP_USER@<droplet-ip> ./publish.sh"
