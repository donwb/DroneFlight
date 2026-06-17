# DroneFlights — project guide for Claude

A local + self-hosted **DJI flight library**: import flights, browse them (gallery
with grouping/map/filters), and watch each one with a synced map, altitude chart,
and live HUD. Built by Don (don.browning@gmail.com), who flies a **DJI Mini 4 Pro**
(and occasionally a Neo 2), mostly along the Florida east coast (New Smyrna Beach
area) and around Atlanta/Marietta.

**Live at https://flights.donwb.com** (password-protected). ~236 flights, growing.

---

## The core insight (how this whole thing works)

DJI `.LRF` files are **"Low Resolution Footage"** — 720p H.264 proxy videos, NOT a
data format. But they carry an **embedded protobuf telemetry stream**
(`dvtm_<model>.proto`) that `exiftool` decodes, even when DJI subtitles were never
enabled:

```
exiftool -ee3 -G3 -api RequestAll=3 -j file.LRF
```

~30 samples/sec: GPS lat/lon, abs/rel altitude, drone yaw/pitch/roll, gimbal,
ISO/shutter/f-number. The LRF's H.264 video also doubles as the browser-playable
proxy. **The big original `.MP4`s are never needed or stored** — the library is
built from `.LRF` files alone (decided with Don).

---

## File map

| File | Role |
|------|------|
| `telemetry.py` | exiftool → telemetry + derived speed/climb + summary (shared module) |
| `ingest.py` | import CLI: scan `*.LRF` → flight.json + proxy.mp4 + thumb.jpg + manifest; geocodes places; idempotent. `--rebuild` (re-geocode + manifest), `--force` |
| `serve.py` | static server **with HTTP Range support** + the clips API (`/api/clips`) |
| `index.html` | gallery (grouping, map, filters, clip badges) |
| `player.html` | the per-flight viewer (`player.html?id=<id>`) |
| `manifest.json` | index of all flights (gitignored — data, rsync'd to server) |
| `flights/<id>/` | per flight: `flight.json`, `proxy.mp4`, `thumb.jpg`, optional `clips.json` |
| `geocache.json` | reverse-geocode cache (gitignored, local only) |
| `deploy/` | `bootstrap.sh` (one-shot droplet setup), `Caddyfile`, `droneflights.service` (templates) |
| `deploy.sh` / `add-flights.sh` / `publish.sh` | day-to-day scripts (see below) |
| `DEPLOY.md` / `README.md` | server runbook / overview |

Flight `id` = from filename: `DJI_20251024160000_0298_D.LRF` → `20251024_160000_0298`.

---

## Commands

```sh
# run locally to preview
python3 serve.py                       # http://localhost:8778

# add flights after a trip (ingest locally + upload to the live site)
./add-flights.sh /Volumes/SDCARD/DCIM

# deploy CODE changes (commit first, then this pushes + pulls + restarts)
git commit -am "msg"
./deploy.sh

# rebuild manifest + re-geocode from flights/ on disk
python3 ingest.py --rebuild

# publish DATA only (manifest + flight.json) — needs DRONE_HOST
DRONE_HOST=droneflights@flights.donwb.com ./publish.sh
```

**Two pipelines, on purpose:** *code* ships via `./deploy.sh` (git → droplet pull +
restart); *media/data* (`flights/`, `manifest.json`) ships via rsync
(`add-flights.sh` / `publish.sh`). Media is ~10GB+ and gitignored — never commit it.

---

## Hosting

- **DigitalOcean droplet** (Ubuntu 24.04), IP `24.144.98.141`, app at `/opt/droneflights`,
  runs as user `droneflights`.
- **Caddy** = auto-HTTPS + basic-auth password gate; serves static + media directly,
  reverse-proxies `/api/*` to `serve.py` (systemd service `droneflights`, on `127.0.0.1:8778`).
- Public GitHub repo: **github.com/donwb/DroneFlight** (note: repo name singular).
- No CI/GitHub Actions — deploys are the manual `./deploy.sh`.
- Fresh-droplet setup is automated by `deploy/bootstrap.sh` (see `DEPLOY.md`).

---

## Conventions & decisions (don't re-litigate)

- **Imperial units** everywhere: altitude **ft**, speed **mph**, climb **ft/s**,
  distance **mi**; angles stay degrees. Stored data is **raw SI**, converted at display.
- **Light map** (CartoDB "Positron" tiles) — Don prefers it over dark for visibility.
- **Clips** are stored server-side per flight in `flights/<id>/clips.json` (chosen over
  localStorage so they're portable library data). The standalone `clips.html` page was
  built then **removed** — the gallery surfaces clips via ★ badges + a "Clips only"
  filter (both depend on the `GET /api/clips` aggregate, so keep that endpoint).
- **Location grouping** uses reverse-geocoded place names (OSM Nominatim at ingest,
  cached). Decided AGAINST GeoNames — preferring named features (parks/beaches) over
  city + snapping county→nearest-town gives good names without a new dependency.

---

## Gotchas (learned the hard way — heed these)

- **Video seeking needs `+faststart`** on the proxy (`ffmpeg -c:v copy -movflags +faststart`)
  AND a server that honors HTTP **Range** — Python's stdlib `http.server` does NOT, which
  silently breaks scrubbing. `serve.py` adds Range + is `ThreadingHTTPServer` (single-thread
  blocks on keep-alive video connections).
- **`serve.py` must be restarted** to pick up code edits (Python loads once). `deploy.sh`
  does this. Editing the file isn't enough.
- **macOS ships bash 3.2**: empty-array `"${arr[@]}"` under `set -u` errors with "unbound
  variable" — use `${arr[@]+"${arr[@]}"}`. All shell scripts are shellcheck-clean.
- **zsh + pasted commands**: inline `# comments` and `(parentheses)` in commands break in
  interactive zsh (glob qualifiers / not-a-comment). **Give Don bare commands, no trailing
  comments.**
- **exiftool quirks**: `SampleTime` past 30s formats as `0:00:30` (H:MM:SS); model lives in
  the per-sample `Model` tag, not `Main:Model`. DJI emits a trailing telemetry packet with a
  reset timestamp — keep only strictly-increasing times. GPS speed from 33ms samples is very
  noisy — speed/climb smoothed over ~0.5s. (All handled in `telemetry.py`.)
- **iPad/responsive**: player uses `100dvh` (not `vh`) + a
  `(min-width:1000px) and (orientation:landscape)` dashboard breakpoint, else stacks/scrolls.
  Gallery uses a ResizeObserver to keep `--toolbar-h` accurate when the toolbar wraps.
- **time-of-day filter** (day/golden/night) is computed from a built-in solar-elevation calc
  with a longitude-derived timezone — accurate to ~±1hr (no real TZ stored). Approximate by design.
- Verifying video in the headless preview tool is unreliable — it throttles audio-less video
  autoplay in a background tab (AbortError). Toggle/icon logic is fine; just can't observe playback there.

---

## Player & gallery feature notes

- **Player**: synced Leaflet map (animated path + heading arrow), altitude chart that is
  **drag/click scrubbable** (pointer events seek the video), HUD, custom **tap-to-play overlay**
  (native controls removed — proxy has no audio, chart is the scrubber; the dark native overlay
  lingered ~3s on touch), fullscreen button.
- **Clips keyboard flow**: `I` (in) → `O` (out, auto-focuses the label) → type → `Enter` (save).
  Saved clips show as green bands on the chart + a list; click one to play just that segment.
- **Gallery**: grouping by Day / Month / **Place** (place ordered by flight count) / Flat with
  per-group summaries; **Grid ⇄ Map** toggle (Leaflet markercluster of takeoff points); filters
  (time-of-day, ★clips-only, model); summary stat bar; jump-to menu; settings persist (localStorage).

---

## Possible next steps (not done)
- Confirm telemetry extraction on a **Neo 2** clip (Don has few currently).
- ffmpeg **export** of marked clip segments to standalone video files (cut from the 720p proxy).
- Per-clip thumbnails (frame at the clip's in-point) instead of reusing the flight thumbnail.
- The original single-clip prototype at `/Users/donwb/dev/lrf` is superseded by this library.
