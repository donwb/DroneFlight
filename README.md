# Drone Flights — local flight library

Import DJI flights, browse them as a gallery, and watch each one with a synced
map, altitude chart, and live HUD (speed / climb / gimbal / ISO / shutter / f-stop).

Built from the **`.LRF`** files alone: telemetry is decoded from DJI's embedded
`dvtm_*.proto` data stream (works even with subtitles off), and the LRF's 720p
video doubles as the browser-playable proxy. The big original `.MP4`s are never
needed or stored.

---

## 🚁 The two commands I'll forget

Live site: **https://flights.donwb.com**

```sh
# After a flight — add new footage to the live site (ingest locally + upload):
./add-flights.sh /Volumes/SDCARD/DCIM

# After changing the app — deploy code to the live site:
git commit -am "what changed" && ./deploy.sh

# Just want to test locally before deploying:
python3 serve.py            # then open http://localhost:8778
```

Both scripts already point at `flights.donwb.com`. `add-flights.sh` skips flights
already imported and uploads only new files; `deploy.sh` pushes, pulls on the
droplet, and restarts the API. Deploying needs the changes committed first.

(Full server setup + recovery lives in [DEPLOY.md](DEPLOY.md).)

---

## Requirements
`brew install exiftool ffmpeg` · `python3`

## Running locally (the underlying commands)

```sh
# 1. import — point at a folder, SD card, or individual .LRF files
python3 ingest.py /Volumes/SDCARD/DCIM         # scans recursively for *.LRF
python3 ingest.py ~/Desktop/DJI_0298_D.LRF     # or specific files
#   re-running skips anything already imported (idempotent)

# 2. browse + watch
python3 serve.py                                # http://localhost:8778
#   open that URL: gallery -> click a flight -> synced player
```

After a flight is imported you can safely delete the original `.MP4`/`.LRF` —
the library is self-contained. Ingest never modifies or deletes your source files.

## Layout

```
manifest.json          index of all flights (summary each; sorted newest-first)
index.html             gallery (search + sort)
player.html            viewer, opened as player.html?id=<flight-id>
ingest.py              import CLI  (--rebuild regenerates manifest from flights/)
telemetry.py           exiftool -> telemetry + speed/climb + summary
serve.py               static server WITH HTTP Range support (needed for seeking)
flights/<id>/          flight.json · proxy.mp4 (LRF video) · thumb.jpg
```

A flight `id` is derived from the DJI filename, e.g.
`DJI_20251024160000_0298_D.LRF` → `20251024_160000_0298`.

## Useful commands

```sh
python3 ingest.py --rebuild        # rebuild manifest.json from what's on disk
python3 ingest.py --force <file>   # re-import (e.g. after upgrading extraction)
```

## Notes / gotchas
- `.LRF` = "Low Resolution Footage" — a 720p H.264 proxy that also carries the
  telemetry track. Small + browser-native, so it's the ideal thing to sync against.
- Proxy is made with `ffmpeg -c:v copy -movflags +faststart` (instant, no re-encode);
  `+faststart` is required or the browser can't seek the video.
- `serve.py` adds HTTP Range support that Python's stdlib server lacks — without it
  video scrubbing silently fails.
- exiftool reports `SampleTime` past 30s as `0:00:30` (H:MM:SS); the model lives in
  the per-sample `Model` tag (`Main:Model` is empty). Both handled in telemetry.py.
- GPS speed from 33ms samples is very noisy; speed/climb are smoothed over ~0.5s.
