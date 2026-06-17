"""DJI embedded-telemetry extraction (shared by ingest.py).

Decodes the protobuf `dvtm_*.proto` data stream that DJI muxes into .LRF/.MP4
files using exiftool, then derives speed/climb and a flight summary.
"""
import json, math, re, subprocess, sys
from pathlib import Path

DEG = re.compile(r"""([\d.]+)\s*deg\s*([\d.]+)'\s*([\d.]+)"\s*([NSEW])""")
SPEED_WINDOW = 0.5  # seconds — GPS jitter over 33ms samples is huge; smooth it


def to_decimal(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = DEG.search(v)
        if m:
            d, mm, ss, h = float(m[1]), float(m[2]), float(m[3]), m[4]
            val = d + mm / 60 + ss / 3600
            return -val if h in "SW" else val
        try:
            return float(v.split()[0])
        except ValueError:
            return None
    return None


def parse_time(v):
    """SampleTime may be '12.34 s', '12.34', or 'H:MM:SS(.ss)' -> seconds."""
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.replace(" s", "").strip()
    if ":" in s:
        sec = 0.0
        for p in s.split(":"):
            sec = sec * 60 + float(p)
        return sec
    try:
        return float(s)
    except ValueError:
        return None


def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.split()[0])
        except ValueError:
            return None
    return None


def haversine(a, b, c, d):
    R = 6371000.0
    p = math.radians
    dlat, dlon = p(c - a), p(d - b)
    x = math.sin(dlat / 2) ** 2 + math.cos(p(a)) * math.cos(p(c)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def _exiftool(path):
    out = subprocess.run(
        ["exiftool", "-ee3", "-G3", "-api", "RequestAll=3", "-j", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0 and not out.stdout.strip():
        raise RuntimeError(f"exiftool failed on {path}:\n{out.stderr}")
    return json.loads(out.stdout)[0]


def extract(path):
    """Return {'meta': {...}, 'track': [...]} for a DJI media file."""
    raw = _exiftool(path)
    docs = {}
    for k, v in raw.items():
        m = re.match(r"Doc(\d+):(.+)", k)
        if m:
            docs.setdefault(int(m.group(1)), {})[m.group(2)] = v

    samples = []
    for n in sorted(docs):
        d = docs[n]
        lat, lon = to_decimal(d.get("GPSLatitude")), to_decimal(d.get("GPSLongitude"))
        if lat is None or lon is None:
            continue
        samples.append({
            "t": parse_time(d.get("SampleTime")) or 0.0,
            "lat": lat, "lon": lon,
            "relAlt": num(d.get("RelativeAltitude")),
            "absAlt": num(d.get("AbsoluteAltitude")),
            "yaw": num(d.get("DroneYaw")), "pitch": num(d.get("DronePitch")),
            "roll": num(d.get("DroneRoll")),
            "gimbalYaw": num(d.get("GimbalYaw")), "gimbalPitch": num(d.get("GimbalPitch")),
            "iso": num(d.get("ISO")), "shutter": d.get("ShutterSpeed"),
            "fnum": num(d.get("FNumber")),
        })
    if not samples:
        raise RuntimeError(f"no GPS telemetry found in {path}")

    # keep strictly-increasing timestamps (DJI emits a trailing reset packet)
    mono, last_t = [], -1.0
    for s in samples:
        if s["t"] > last_t:
            mono.append(s); last_t = s["t"]
    samples = mono

    # derive speed (m/s) & climb (m/s) over a ~0.5s window to kill GPS jitter
    for i, s in enumerate(samples):
        j = i
        while j > 0 and (s["t"] - samples[j - 1]["t"]) < SPEED_WINDOW:
            j -= 1
        p = samples[j]
        dt = s["t"] - p["t"]
        if dt <= 0:
            s["speed"], s["climb"] = 0.0, 0.0
        else:
            s["speed"] = haversine(p["lat"], p["lon"], s["lat"], s["lon"]) / dt
            s["climb"] = ((s["relAlt"] or 0) - (p["relAlt"] or 0)) / dt

    lats = [s["lat"] for s in samples]
    lons = [s["lon"] for s in samples]
    rels = [s["relAlt"] for s in samples if s["relAlt"] is not None]
    speeds = [s["speed"] for s in samples]
    path_len = sum(
        haversine(samples[i-1]["lat"], samples[i-1]["lon"], samples[i]["lat"], samples[i]["lon"])
        for i in range(1, len(samples))
    )
    first_doc = docs[min(docs)] if docs else {}
    model = (first_doc.get("Model") or raw.get("Main:Encoder")
             or raw.get("Main:Model") or "DJI")
    meta = {
        "model": str(model).strip(),
        "duration": num(raw.get("Main:Duration") or raw.get("Duration")) or samples[-1]["t"],
        "samples": len(samples),
        "bounds": {"minLat": min(lats), "maxLat": max(lats),
                   "minLon": min(lons), "maxLon": max(lons)},
        "centroid": {"lat": sum(lats) / len(lats), "lon": sum(lons) / len(lons)},
        "relAlt": {"min": min(rels), "max": max(rels)} if rels else None,
        "maxSpeed": max(speeds) if speeds else 0.0,
        "pathLength": path_len,
        "source": Path(path).name,
    }
    return {"meta": meta, "track": samples}


if __name__ == "__main__":  # quick standalone use: telemetry.py file.LRF [out.json]
    data = extract(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else "flight.json"
    Path(out).write_text(json.dumps(data))
    m = data["meta"]
    print(f"{m['source']}: {m['samples']} samples, {m['pathLength']:.0f} m -> {out}")
