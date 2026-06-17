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

### Fast path: bootstrap.sh

Steps 1–5 below are automated by [`deploy/bootstrap.sh`](deploy/bootstrap.sh).
On a fresh Ubuntu droplet (DNS already pointing at it), get the script there and run it:

```sh
# PUBLIC repo — fetch and run on the droplet:
curl -fsSL -o bootstrap.sh https://raw.githubusercontent.com/YOU/REPO/main/deploy/bootstrap.sh
sudo bash bootstrap.sh

# PRIVATE repo — copy it up from your Mac (in the repo dir), then run with a TTY:
scp deploy/bootstrap.sh root@DROPLET_IP:
ssh -t root@DROPLET_IP 'bash bootstrap.sh'
```

It prompts for domain, repo URL, login username, and password, then does the rest.
It's idempotent — re-running pulls the latest code and re-renders the config.
(For a private repo the first run prints a deploy key to add to GitHub, then re-run.)

The manual steps below are the same thing spelled out, for reference or debugging.

### 1. Droplet
- Create a basic Ubuntu droplet (the $6–12/mo tier is plenty to start), adding
  your SSH key during creation. Point your domain's DNS `A` record at the IP.
- SSH in as root and create the app user (normal user with a shell, so you can
  rsync/SSH as it; password login disabled — key only):
  ```sh
  adduser --disabled-password --gecos "" droneflights
  install -d -o droneflights -g droneflights /opt/droneflights
  # let this user log in via your key (so publish.sh + CI can reach it):
  install -d -o droneflights -g droneflights -m 700 /home/droneflights/.ssh
  cp ~/.ssh/authorized_keys /home/droneflights/.ssh/
  chown droneflights:droneflights /home/droneflights/.ssh/authorized_keys
  chmod 600 /home/droneflights/.ssh/authorized_keys
  ```
- Lock the firewall to web + ssh only:
  ```sh
  ufw allow OpenSSH && ufw allow 80,443/tcp && ufw --force enable
  ```

### 2. Install Python, git, and Caddy
```sh
apt update && apt install -y python3 git debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
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
