#!/usr/bin/env python3
"""Static file server with HTTP Range support, so <video> seeking works.

Python's stdlib handler ignores Range requests, which breaks in-browser video
scrubbing. Threaded so keep-alive video connections don't block the page.

Usage:  python3 serve.py [port]   (default 8778)
"""
import json, os, re, sys
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

RANGE = re.compile(r"bytes=(\d*)-(\d*)")
CLIPS_PATH = re.compile(r"^/api/clips/([A-Za-z0-9_]+)$")  # flight id only, no traversal


class RangeHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        # only endpoint: POST /api/clips/<flightId> writes flights/<id>/clips.json
        m = CLIPS_PATH.match(self.path)
        if not m:
            self.send_error(404)
            return
        fid = m.group(1)
        flightdir = os.path.join(os.getcwd(), "flights", fid)
        if not os.path.isdir(flightdir):
            self.send_error(404, "unknown flight")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
        except Exception:
            self.send_error(400, "bad json")
            return
        with open(os.path.join(flightdir, "clips.json"), "w") as f:
            json.dump(data, f, indent=1)
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_clips_index(self):
        # aggregate every flights/*/clips.json, enriched with flight metadata
        base = os.getcwd()
        meta = {}
        mpath = os.path.join(base, "manifest.json")
        if os.path.isfile(mpath):
            try:
                for f in json.load(open(mpath)).get("flights", []):
                    meta[f["id"]] = f
            except Exception:
                pass
        out = []
        flightsdir = os.path.join(base, "flights")
        if os.path.isdir(flightsdir):
            for fid in os.listdir(flightsdir):
                cpath = os.path.join(flightsdir, fid, "clips.json")
                if not os.path.isfile(cpath):
                    continue
                try:
                    data = json.load(open(cpath))
                except Exception:
                    continue
                m = meta.get(fid, {})
                for c in data.get("clips", []):
                    out.append({
                        "flightId": fid, "clipId": c.get("id"),
                        "label": c.get("label", ""), "in": c.get("in"), "out": c.get("out"),
                        "date": m.get("date"), "model": m.get("model"),
                        "thumb": f"flights/{fid}/thumb.jpg",
                    })
        out.sort(key=lambda c: ((c["date"] or ""), -(c["in"] or 0)), reverse=True)
        self._send_json({"clips": out, "count": len(out)})

    def do_GET(self):
        if self.path.split("?")[0] == "/api/clips":
            return self.send_clips_index()
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().do_GET()
        m = RANGE.match(rng)
        if not m:
            return super().do_GET()
        size = os.path.getsize(path)
        start = int(m.group(1)) if m.group(1) else 0
        end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_error(416)
            return
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


if __name__ == "__main__":
    # tolerate a non-numeric arg (e.g. a pasted "# comment"); fall back to default
    port = 8778
    for arg in sys.argv[1:]:
        if arg.isdigit():
            port = int(arg)
            break
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    # bind loopback by default (behind Caddy in prod); DRONE_BIND=0.0.0.0 for LAN access
    host = os.environ.get("DRONE_BIND", "127.0.0.1")
    # if the chosen port is busy, walk forward to the next free one
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, p), RangeHandler)
            port = p
            break
        except OSError as e:
            if e.errno == 48:  # address already in use
                print(f"port {p} busy, trying {p + 1}…")
                continue
            raise
    if httpd is None:
        sys.exit(f"no free port in {port}–{port + 19}; close some servers and retry")
    print(f"\n  DroneFlights library ready:  http://localhost:{port}\n"
          f"  (Range-enabled · Ctrl-C to stop)\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
