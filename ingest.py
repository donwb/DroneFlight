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
import argparse, datetime, json, re, subprocess, sys, time
from pathlib import Path

import telemetry

LIB = Path(__file__).resolve().parent
FLIGHTS = LIB / "flights"
MANIFEST = LIB / "manifest.json"
NAME_RE = re.compile(r"DJI_(\d{14})_(\d+)", re.I)


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


def rebuild():
    man = {"flights": []}
    for d in sorted(FLIGHTS.iterdir()):
        fj = d / "flight.json"
        if fj.is_file():
            data = json.loads(fj.read_text())
            dt = None
            m = NAME_RE.search(data["meta"].get("source", ""))
            if m:
                dt = datetime.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
            man["flights"].append(summary_entry(d.name, dt, data, "proxy.mp4", "thumb.jpg"))
    write_manifest(man)
    print(f"rebuilt manifest: {len(man['flights'])} flights")


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
            (outdir / "flight.json").write_text(json.dumps(data))
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
                  f"alt {lo_ft:.0f}-{hi_ft:.0f}ft "
                  f"({time.perf_counter()-ts:.1f}s){eta(i)}", flush=True)
        except Exception as e:
            failed += 1
            print(f"✗ FAILED: {e}", flush=True)
        # save periodically so a long run is crash-safe and partially browsable
        if added and added % 20 == 0:
            write_manifest(man)

    write_manifest(man)
    mins, secs = divmod(int(time.perf_counter() - t0), 60)
    print(f"\ndone in {mins}m{secs:02d}s: +{added} added, {skipped} skipped, "
          f"{failed} failed, {len(man['flights'])} total in library")


if __name__ == "__main__":
    main()
