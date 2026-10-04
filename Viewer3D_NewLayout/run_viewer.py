#!/usr/bin/env python3
"""
run_viewer.py -- serve this folder on localhost and open the viewer in the
default browser. Standard library only (http.server + webbrowser): no pip
install, no internet, works with just a python3 on any machine this drive
is plugged into.

Cesium (and browsers in general) needs http:// rather than a bare file://
open, because the page loads its Workers/Assets over relative URLs that
browsers block for file:// pages -- this script exists to provide that
localhost server, nothing more.

Usage:
    python3 run_viewer.py           # picks a free port, opens it in Chrome
                                    # (falls back to the default browser)
    python3 run_viewer.py --port 8080 --no-browser
    python3 run_viewer.py --ship-feed   # also listen for the ship's GPS (UDP 55000) and
                                        # heading (UDP 55001) and show them in the viewer

Ship feed (optional, off unless --ship-feed): listens on UDP --gps-port / --heading-port
(defaults 55000 / 55001, the same ports `nc -ul 55000` / `nc -ul 55001` read). Accepts NMEA
0183 sentences from any talker (GP, GN, IN, HE, ...): position from GGA, RMC or GLL; heading
from HDT, THS or HDG (HDG is magnetic and is flagged as such). Plain "lat lon" / "heading"
numbers are accepted as a fallback. The latest fix is served at /ship.json; the viewer polls
it once a minute. Only one program can normally listen on a UDP port, so stop any
`nc -ul` on those ports first (or have the ship's system broadcast the feed).

Site lists elsewhere in the repo (previous dredges, AT53-04 dredge and MT sites) are
served at /sites/<file name> so the viewer can load them with one click (SITE_FILES).
"""
import argparse
import functools
import http.server
import json
import re
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


# ---------------------------------------------------------------- ship feed (optional)
class ShipState:
    """Latest ship position/heading from the UDP feeds, plus a 1-per-minute track."""

    TRACK_EVERY_S = 60
    TRACK_MAX = 24 * 60  # 24 h at one point per minute

    def __init__(self):
        self.lock = threading.Lock()
        self.pos = None       # dict(lat, lon, utc, sentence, received)
        self.heading = None   # dict(deg, true, sentence, received)
        self.motion = None    # dict(cog, sog_kn, sentence, received): course/speed over ground
        self.track = []       # [lon, lat, unix_time]
        self.errors = {}      # port -> error text (e.g. address in use)
        self.counts = {"gps_lines": 0, "heading_lines": 0, "ignored": 0}
        self.seen = {"gps": {}, "heading": {}}  # sentence type -> count, per port (diagnostics)

    def snapshot(self, enabled):
        now = time.time()
        with self.lock:
            out = {"enabled": enabled, "server_time": now, "errors": dict(self.errors), "counts": dict(self.counts),
                   "sentence_types_seen": {k: dict(v) for k, v in self.seen.items()}}
            if self.pos:
                out["position"] = dict(self.pos, age_s=round(now - self.pos["received"], 1))
            if self.heading:
                out["heading"] = dict(self.heading, age_s=round(now - self.heading["received"], 1))
            if self.motion:
                out["motion"] = dict(self.motion, age_s=round(now - self.motion["received"], 1))
            out["track"] = list(self.track)
        return out


def _nmea_ok(line):
    """True if the line has no checksum, or a correct one."""
    if "*" not in line:
        return True
    body, _, cs = line.lstrip("$!").partition("*")
    try:
        want = int(cs[:2], 16)
    except ValueError:
        return False
    got = 0
    for ch in body:
        got ^= ord(ch)
    return got == want


def _nmea_coord(value, hemi, deg_digits):
    """'0123.4567','N' -> decimal degrees (ddmm.mmmm / dddmm.mmmm)."""
    if not value or not hemi:
        return None
    deg = float(value[:deg_digits])
    minutes = float(value[deg_digits:])
    v = deg + minutes / 60.0
    return -v if hemi.upper() in ("S", "W") else v


_FLOATS = re.compile(r"[-+]?\d+(?:\.\d+)?")


def parse_position(line):
    """Returns (lat, lon, utc_hhmmss_or_None, sentence_type) or None."""
    line = line.strip()
    if line.startswith(("$", "!")):
        if not _nmea_ok(line):
            return None
        f = line.split("*")[0].split(",")
        kind = f[0][-3:].upper()
        try:
            if kind == "GGA" and len(f) > 6 and f[6] not in ("", "0"):      # fix quality 0 = no fix
                return _nmea_coord(f[2], f[3], 2), _nmea_coord(f[4], f[5], 3), f[1] or None, "GGA"
            if kind == "RMC" and len(f) > 6 and f[2].upper() == "A":         # A = valid
                return _nmea_coord(f[3], f[4], 2), _nmea_coord(f[5], f[6], 3), f[1] or None, "RMC"
            if kind == "GLL" and len(f) > 6 and (len(f) < 7 or f[6].upper() != "V"):
                return _nmea_coord(f[1], f[2], 2), _nmea_coord(f[3], f[4], 3), f[5] or None, "GLL"
        except (ValueError, IndexError):
            return None
        return None
    nums = [float(x) for x in _FLOATS.findall(line)]
    if len(nums) >= 2 and abs(nums[0]) <= 90 and abs(nums[1]) <= 180:
        return nums[0], nums[1], None, "plain lat lon"
    return None


def parse_motion(line):
    """Course and speed over ground from RMC or VTG: returns (cog_deg_true, sog_knots, type) or None.
    This is the direction the ship is MOVING, not where its bow points (that is HDT/THS)."""
    line = line.strip()
    if not line.startswith(("$", "!")) or not _nmea_ok(line):
        return None
    f = line.split("*")[0].split(",")
    kind = f[0][-3:].upper()
    try:
        if kind == "RMC" and len(f) > 8 and f[2].upper() == "A" and f[8]:
            return float(f[8]) % 360, float(f[7]) if f[7] else None, "RMC"
        if kind == "VTG" and len(f) > 5 and f[1]:
            return float(f[1]) % 360, float(f[5]) if f[5] else None, "VTG"
    except (ValueError, IndexError):
        return None
    return None


def parse_heading(line):
    """Returns (degrees, is_true_heading, sentence_type) or None."""
    line = line.strip()
    if line.startswith(("$", "!")):
        if not _nmea_ok(line):
            return None
        f = line.split("*")[0].split(",")
        kind = f[0][-3:].upper()
        try:
            if kind == "HDT" and f[1]:
                return float(f[1]) % 360, True, "HDT"
            if kind == "THS" and f[1] and (len(f) < 3 or f[2].upper() != "V"):
                return float(f[1]) % 360, True, "THS"
            if kind == "HDG" and f[1]:
                return float(f[1]) % 360, False, "HDG (magnetic)"
        except (ValueError, IndexError):
            return None
        return None
    nums = _FLOATS.findall(line)
    if len(nums) == 1:
        return float(nums[0]) % 360, True, "plain heading"
    return None


def _listen(port, kind, ship):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        with ship.lock:
            ship.errors[str(port)] = f"cannot listen on UDP {port}: {e}"
        print(f"[ship feed] cannot listen on UDP {port} ({kind}): {e}")
        return
    print(f"[ship feed] listening for {kind} on UDP {port}")
    while True:
        data, _ = sock.recvfrom(65535)
        # a UDP packet holds whole sentences: one, or several separated by line breaks
        for line in data.decode("ascii", errors="replace").splitlines():
            if not line.strip():
                continue
            now = time.time()
            tag = line.strip().split(",")[0][:8] if line.strip().startswith(("$", "!")) else "(plain text)"
            with ship.lock:
                ship.seen[kind][tag] = ship.seen[kind].get(tag, 0) + 1
            if kind == "gps":
                m = parse_motion(line)
                if m is not None:
                    with ship.lock:
                        ship.motion = {"cog": m[0], "sog_kn": m[1], "sentence": m[2], "received": now}
                    if line.strip().split(",")[0][-3:].upper() == "VTG":
                        with ship.lock:
                            ship.counts["gps_lines"] += 1
                        continue
                r = parse_position(line)
                with ship.lock:
                    ship.counts["gps_lines"] += 1
                    if r is None or r[0] is None or r[1] is None:
                        ship.counts["ignored"] += 1
                        continue
                    lat, lon, utc, sent = r
                    ship.pos = {"lat": lat, "lon": lon, "utc": utc, "sentence": sent, "received": now}
                    if not ship.track or now - ship.track[-1][2] >= ship.TRACK_EVERY_S:
                        ship.track.append([lon, lat, now])
                        del ship.track[: -ship.TRACK_MAX]
            else:
                r = parse_heading(line)
                with ship.lock:
                    ship.counts["heading_lines"] += 1
                    if r is None:
                        ship.counts["ignored"] += 1
                        continue
                    deg, true_hdg, sent = r
                    ship.heading = {"deg": deg, "true": true_hdg, "sentence": sent, "received": now}


# Site lists that live elsewhere in the repo, served at /sites/<name> (whitelist only).
REPO = Path(__file__).resolve().parent.parent
SITE_FILES = {
    "Previous_Dredges_Compiled.csv": "Site_Maps/Previous_Dredges_Compiled.csv",
    "DredgeSites.csv": "MT_dredging_coords/DredgeSites.csv",
    "MTsites.csv": "MT_dredging_coords/MTsites.csv",
}


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """Never let the browser reuse a cached meta.json / mesh.bin. Without this, a rebuilt or
    restored layer can be read with a stale cached meta.json (wrong vertex count / section
    offsets) and silently fail to draw. Restored files with OLD timestamps are the worst
    case: the default handler answers If-Modified-Since with 304 whenever the file's mtime
    is not newer than the browser's cached copy."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    ship = None          # ShipState when --ship-feed is on
    ship_enabled = False

    def do_GET(self):
        name = self.path.split("?")[0]
        if name.startswith("/sites/") and name[7:] in SITE_FILES:
            body = (REPO / SITE_FILES[name[7:]]).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if name == "/ship.json":
            snap = self.ship.snapshot(self.ship_enabled) if self.ship else {"enabled": False}
            body = json.dumps(snap).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, fmt, *args):
        if "/ship.json" not in (str(args[0]) if args else ""):  # args[0] is an HTTPStatus for errors  # don't log the once-a-minute poll
            super().log_message(fmt, *args)

    def send_head(self):
        # drop conditional headers so the default handler never replies 304 Not Modified
        for h in ("If-Modified-Since", "If-None-Match"):
            if h in self.headers:
                del self.headers[h]
        return super().send_head()


def open_in_chrome(url):
    """Open url in Google Chrome if it is installed (normal install, or the Flatpak
    com.google.Chrome); otherwise fall back to the system default browser."""
    import shutil
    import subprocess
    for exe in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(exe):
            subprocess.Popen([exe, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Chrome ({exe})"
    if shutil.which("flatpak"):
        apps = subprocess.run(["flatpak", "list", "--app", "--columns=application"],
                              capture_output=True, text=True).stdout.split()
        for app in ("com.google.Chrome", "org.chromium.Chromium"):
            if app in apps:
                subprocess.Popen(["flatpak", "run", app, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"Chrome (flatpak {app})"
    webbrowser.open(url)
    return "default browser (Chrome not found)"


def find_free_port(preferred):
    if preferred:
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="port to serve on (default: pick a free one)")
    ap.add_argument("--no-browser", action="store_true", help="don't auto-open a browser tab")
    ap.add_argument("--ship-feed", action="store_true",
                    help="listen for the ship's GPS and heading on UDP and show them in the viewer")
    ap.add_argument("--gps-port", type=int, default=55000, help="UDP port of the GPS feed (default 55000)")
    ap.add_argument("--heading-port", type=int, default=55001, help="UDP port of the heading feed (default 55001)")
    args = ap.parse_args()

    NoCacheHandler.ship = ShipState()
    NoCacheHandler.ship_enabled = args.ship_feed
    if args.ship_feed:
        for port, kind in ((args.gps_port, "gps"), (args.heading_port, "heading")):
            threading.Thread(target=_listen, args=(port, kind, NoCacheHandler.ship), daemon=True).start()

    root = Path(__file__).resolve().parent
    port = find_free_port(args.port)
    url = f"http://127.0.0.1:{port}/index.html"

    handler = functools.partial(NoCacheHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)

    print(f"Serving {root} at {url}")
    print("Press Ctrl+C to stop.")

    if not args.no_browser:
        threading.Timer(0.6, lambda: print(f"Opened in {open_in_chrome(url)}")).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        httpd.shutdown()


if __name__ == "__main__":
    sys.exit(main())
