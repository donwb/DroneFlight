# Deploying DroneFlights to a DigitalOcean Droplet

**Shape:** one small Droplet. **Caddy** terminates HTTPS, password-protects the
site, serves the static files + videos directly, and proxies `/api/*` to
`serve.py` (the clips API) running as a `systemd` service on loopback.

**Two pipelines, on purpose:**
- **Code** (html / py / configs) → GitHub → **Actions** → SSH deploy on push.
- **Media + data** (`flights/`, `manifest.json`) → **`rsync`** via `publish.sh`.
  These are ~10GB and grow, so they are gitignored and never committed.

---

## One-time setup

### 1. Droplet
- Create a basic Ubuntu droplet (the $6–12/mo tier is plenty to start).
- Point your domain's DNS `A` record at the droplet IP.
- SSH in and create an app user + dirs:
  ```sh
  sudo adduser --system --group droneflights
  sudo mkdir -p /opt/droneflights
  sudo chown -R droneflights:droneflights /opt/droneflights
  ```
- Lock the firewall to web + ssh only:
  ```sh
  sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
  ```

### 2. Install Caddy + Python
```sh
sudo apt update && sudo apt install -y python3 git
# Caddy: https://caddyserver.com/docs/install#debian-ubuntu-raspbian
```

### 3. Get the code onto the droplet
```sh
sudo -u droneflights git clone https://github.com/<you>/droneflights.git /opt/droneflights
```

### 4. The clips API service
```sh
sudo cp /opt/droneflights/deploy/droneflights.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now droneflights
```

### 5. Caddy site config (HTTPS + password)
```sh
# generate a password hash (you'll be prompted)
caddy hash-password
# edit deploy/Caddyfile: set your real domain (first line) and paste the hash
sudo cp /opt/droneflights/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy     # Caddy auto-fetches a Let's Encrypt cert
```

### 6. GitHub Actions secrets
In the repo: **Settings → Secrets and variables → Actions**, add:
- `DROPLET_HOST` — droplet IP or hostname
- `DROPLET_USER` — a sudo-capable deploy user (give it passwordless `systemctl restart droneflights`)
- `DROPLET_SSH_KEY` — a **private** deploy key whose public half is in that user's `~/.ssh/authorized_keys`

### 7. First media upload (from your Mac)
```sh
cd ~/DroneFlights
DRONE_HOST=droneflights@<droplet-ip> ./publish.sh   # ~10GB once, deltas after
```

Visit `https://yourdomain` → enter the password → your library is live.

---

## Day-to-day

**Add flights** (local — needs the LRF files + exiftool/ffmpeg):
```sh
cd ~/DroneFlights
python3 ingest.py /Volumes/SDCARD/DCIM      # extract + proxy + thumb + manifest
DRONE_HOST=droneflights@<droplet-ip> ./publish.sh   # push the new media up
```

**Change the app** (player UI, server, etc.):
```sh
git add -A && git commit -m "..." && git push   # Actions deploys it
```

**Clips** marked on the live site write to `flights/<id>/clips.json` on the droplet.
If you want them mirrored back to your Mac:
```sh
rsync -avh droneflights@<droplet-ip>:/opt/droneflights/flights/ flights/ \
  --include='*/' --include='clips.json' --exclude='*'
```

---

## Notes
- **Never `git add` the media.** `.gitignore` already excludes `flights/` and
  `manifest.json`; if `git status` ever shows a `.mp4`, stop and fix the ignore.
- The droplet's base disk (~25–50GB) holds hundreds of flights. When it gets
  tight: resize the droplet, or attach a Block Storage Volume and move `flights/`
  onto it (symlink it back). If media ever gets huge, switch media to DO Spaces + CDN.
- `serve.py` binds `127.0.0.1` by default now (set `DRONE_BIND=0.0.0.0` only for
  direct LAN access). In prod it's never exposed directly — Caddy fronts it.
