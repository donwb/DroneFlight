#!/usr/bin/env python3
"""Ingest DJI flights into the library.

Scans a folder (or individual files) for DJI .LRF clips, and for each one not
already imported: extracts telemetry, copies the LRF's H.264 stream into a
browser-playable proxy.mp4, grabs a thumbnail, and records a summary in
manifest.json. Idempotent — re-running skips already-imported flights.

Originals are never modified or deleted; the library keeps only the lightweight
proxy + telemetry. After importing you can delete the big source files yourself.

Usage:
    python3 ingest.py /path/to/sdcard_or_folder
    python3 ingest.py DJI_0298.LRF DJI_0301.LRF
    python3 ingest.py --rebuild            # rebuild manifest from flights/ on disk
"""
import argparse, datetime, json, re, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

import telemetry

LIB = Path(__file__).resolve().parent
FLIGHTS = LIB / "flights"
MANIFEST = LIB / "manifest.json"
GEOCACHE = LIB / "geocache.json"
NAME_RE = re.compile(r"DJI_(\d{14})_(\d+)", re.I)

_geo = None
_geo_last = [0.0]


def _geocache():
    global _geo
    if _geo is None:
        _geo = json.loads(GEOCACHE.read_text()) if GEOCACHE.exists() else {}
    return _geo


def reverse_geocode(lat, lon):
    """lat/lon -> 'City, State' via OSM Nominatim, cached by ~1km grid cell so a
    library of flights at a handful of spots makes only a few API calls. Returns
    None on any failure (offline, rate-limited, no result) — grouping just shows
    'Unknown location' for those."""
    if lat is None or lon is None:
        return None
    cache = _geocache()
    key = f"{round(lat, 2)},{round(lon, 2)}"
    if key in cache:
        return cache[key]
    wait = 1.1 - (time.time() - _geo_last[0])          # Nominatim policy: <= 1 req/sec
    if wait > 0:
        time.sleep(wait)
    q = urllib.parse.urlencode({"lat": lat, "lon": lon, "format": "jsonv2",
                                "zoom": 16, "addressdetails": 1})
    req = urllib.request.Request(
        "https://nominatim.openstreetmap.org/reverse?" + q,
        headers={"User-Agent": "DroneFlights/1.0 (personal DJI flight library)"})
    place = None
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            a = json.load(r).get("address", {})
        # town/city first (groups populated-area flights), then a named natural/park
        # feature (parks & remote launches get their real name), then county/region.
        local = (a.get("city") or a.get("town") or a.get("village") or a.get("municipality")
                 or a.get("national_park") or a.get("protected_area") or a.get("nature_reserve")
                 or a.get("leisure") or a.get("natural") or a.get("tourism")
                 or a.get("beach") or a.get("bay") or a.get("island")
                 or a.get("hamlet") or a.get("county") or a.get("region"))
        place = ", ".join(p for p in (local, a.get("state")) if p) or None
    except Exception:
        place = None
    _geo_last[0] = time.time()
    cache[key] = place
    GEOCACHE.write_text(json.dumps(cache, indent=1))
    return place


def flight_id(lrf: Path):
    """DJI_20251024160000_0298_D.LRF -> ('20251024_160000_0298', datetime)."""
    m = NAME_RE.search(lrf.stem)
    if m:
        ts, clip = m.group(1), m.group(2)
        dt = datetime.datetime.strptime(ts, "%Y%m%d%H%M%S")
        return f"{ts[:8]}_{ts[8:]}_{clip}", dt
    return lrf.stem, None


def find_lrfs(paths):
    out = []
    for p in paths:
        p = Path(p).expanduser()
        if p.is_dir():
            out += sorted(p.rglob("*.LRF")) + sorted(p.rglob("*.lrf"))
        elif p.suffix.lower() == ".lrf":
            out.append(p)
        else:
            print(f"  skip (not an .LRF): {p.name}")
    # de-dupe, preserve order
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p); uniq.append(p)
    return uniq


def make_proxy(lrf: Path, dest: Path):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(lrf),
         "-map", "0:0", "-c:v", "copy", "-an", "-movflags", "+faststart", str(dest)],
        check=True,
    )


def make_thumb(proxy: Path, dest: Path, when=2.0):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", str(when), "-i", str(proxy),
         "-frames:v", "1", "-vf", "scale=480:-1", str(dest)],
        check=True,
    )


def summary_entry(fid, dt, data, proxy_name, thumb_name):
    m = data["meta"]
    return {
        "id": fid,
        "date": dt.isoformat() if dt else None,
        "model": m.get("model"),
        "duration": round(m["duration"], 1),
        "samples": m["samples"],
        "pathLength": round(m["pathLength"], 1),  # meters (raw SI; UI converts)
        "maxSpeed": round(m["maxSpeed"], 3),       # m/s   (raw SI; UI converts)
        "relAlt": m["relAlt"],
        "centroid": m["centroid"],
        "place": m.get("place"),
        "bounds": m["bounds"],
        "source": m["source"],
        "proxy": proxy_name,
        "thumb": thumb_name,
    }


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {"flights": []}


def write_manifest(man):
    man["flights"].sort(key=lambda f: f.get("date") or f["id"], reverse=True)
    man["generated"] = datetime.datetime.now().isoformat(timespec="seconds")
    man["count"] = len(man["flights"])
    MANIFEST.write_text(json.dumps(man, indent=1))


def _is_county(p):
    return bool(p) and p.split(",")[0].strip().endswith("County")


def _place_for(data):
    """Geocode the takeoff point (where it launched — usually on land); fall back to centroid."""
    track = data.get("track") or []
    pt = track[0] if track else data.get("meta", {}).get("centroid")
    return reverse_geocode(pt.get("lat"), pt.get("lon")) if pt else None


def rebuild():
    # (re)assign place names for every flight, then write the manifest. Geocoding is
    # cached, so this is fast after the first run. Authoritative pass — ingest calls it.
    flights = []
    for d in sorted(FLIGHTS.iterdir()):
        fj = d / "flight.json"
        if not fj.is_file():
            continue
        data = json.loads(fj.read_text())
        meta = data.setdefault("meta", {})
        orig = meta.get("place")
        meta["place"] = _place_for(data)
        flights.append((d.name, data, fj, orig))

    # Snap county-only labels to the nearest named-town anchor (<=15km) so over-water
    # / unincorporated launches inherit a real town (e.g. "New Smyrna Beach").
    anchors = [(f[1]["meta"]["centroid"], f[1]["meta"]["place"]) for f in flights
               if f[1]["meta"].get("centroid") and f[1]["meta"].get("place")
               and not _is_county(f[1]["meta"]["place"])]
    for _, data, fj, orig in flights:
        m = data["meta"]; cen = m.get("centroid")
        if _is_county(m.get("place")) and cen and anchors:
            ap, ad = min(((p, telemetry.haversine(cen["lat"], cen["lon"], c["lat"], c["lon"]))
                          for c, p in anchors), key=lambda x: x[1])
            if ad < 15000:
                m["place"] = ap
        if m.get("place") != orig:                      # only rewrite when it changed
            fj.write_text(json.dumps(data))

    man = {"flights": []}
    for fid, data, fj, orig in flights:
        dt = None
        mm = NAME_RE.search(data["meta"].get("source", ""))
        if mm:
            dt = datetime.datetime.strptime(mm.group(1), "%Y%m%d%H%M%S")
        man["flights"].append(summary_entry(fid, dt, data, "proxy.mp4", "thumb.jpg"))
    write_manifest(man)
    print(f"rebuilt manifest: {len(man['flights'])} flights ({len(anchors)} town-anchored)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help="folders or .LRF files to import")
    ap.add_argument("--rebuild", action="store_true",
                    help="rebuild manifest.json from flights/ already on disk")
    ap.add_argument("--force", action="store_true", help="re-import even if present")
    a = ap.parse_args()

    if a.rebuild:
        rebuild(); return
    if not a.paths:
        ap.error("give a folder or .LRF files to import (or --rebuild)")

    man = load_manifest()
    have = {f["id"] for f in man["flights"]}
    lrfs = find_lrfs(a.paths)
    n = len(lrfs)
    print(f"found {n} .LRF file(s) — reading only, originals are never modified\n")
    added = skipped = failed = 0
    t0 = time.perf_counter()

    def eta(done):
        if not added:
            return ""
        per = (time.perf_counter() - t0) / added
        rem = per * (n - done)
        return f"  ~{int(rem//60)}m{int(rem%60):02d}s left" if rem >= 60 else f"  ~{int(rem)}s left"

    for i, lrf in enumerate(lrfs, 1):
        fid, dt = flight_id(lrf)
        pre = f"[{i}/{n}]"
        if fid in have and not a.force:
            skipped += 1
            print(f"{pre} = {lrf.name}  (already imported, skipped)")
            continue
        # print the file as work STARTS, so a slow file isn't a silent gap
        print(f"{pre} {lrf.name} … ", end="", flush=True)
        outdir = FLIGHTS / fid
        outdir.mkdir(parents=True, exist_ok=True)
        ts = time.perf_counter()
        try:
            data = telemetry.extract(lrf)
            (outdir / "flight.json").write_text(json.dumps(data))   # place assigned by rebuild() below
            make_proxy(lrf, outdir / "proxy.mp4")
            make_thumb(outdir / "proxy.mp4", outdir / "thumb.jpg")
            entry = summary_entry(fid, dt, data, "proxy.mp4", "thumb.jpg")
            man["flights"] = [f for f in man["flights"] if f["id"] != fid] + [entry]
            have.add(fid)
            added += 1
            mi = entry["pathLength"] * 0.000621371
            lo_ft = entry["relAlt"]["min"] * 3.28084
            hi_ft = entry["relAlt"]["max"] * 3.28084
            print(f"✓ {entry['duration']:.0f}s {mi:.2f}mi "
                  f"alt {lo_ft:.0f}-{hi_ft:.0f}ft"
                  f"{' · '+entry['place'] if entry.get('place') else ''} "
                  f"({time.perf_counter()-ts:.1f}s){eta(i)}", flush=True)
        except Exception as e:
            failed += 1
            print(f"✗ FAILED: {e}", flush=True)
        # save periodically so a long run is crash-safe and partially browsable
        if added and added % 20 == 0:
            write_manifest(man)

    if added:
        rebuild()        # geocode + place-snap + authoritative manifest for the new flights
    else:
        write_manifest(man)
    mins, secs = divmod(int(time.perf_counter() - t0), 60)
    print(f"\ndone in {mins}m{secs:02d}s: +{added} added, {skipped} skipped, "
          f"{failed} failed, {len(man['flights'])} total in library")


if __name__ == "__main__":
    main()
