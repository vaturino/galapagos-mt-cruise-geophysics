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
from HDT, THS or HDG (HDG is converted to true when it carries variation, else flagged
magnetic). Checksums are required unless --allow-no-checksum; values must be finite and in
range; fixes must be flagged valid. Plain "lat lon" / "heading" text lines are accepted only
with --plain-feed. The latest fix is served at /ship.json; the viewer polls it once a minute.
Only one program can listen on a UDP port, so stop any `nc -ul` on those ports first (or have
the ship's system broadcast the feed).

Site lists elsewhere in the repo (previous dredges, AT53-04 dredge and MT sites) are
served at /sites/<file name> so the viewer can load them with one click (SITE_FILES).
"""
import argparse
import functools
import http.server
import json
import math
import os
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
        self.track_last = 0.0  # monotonic time of the last track point (immune to clock steps)
        self.errors = {}      # port -> error text (e.g. address in use)
        self.counts = {"gps_lines": 0, "heading_lines": 0, "ignored": 0}
        self.seen = {"gps": {}, "heading": {}}  # sentence type -> count, per port (diagnostics)

    def snapshot(self, enabled):
        now, mono = time.time(), time.monotonic()  # ages from the monotonic clock: immune to NTP steps
        with self.lock:
            out = {"enabled": enabled, "server_time": now, "errors": dict(self.errors), "counts": dict(self.counts),
                   "sentence_types_seen": {k: dict(v) for k, v in self.seen.items()},
                   "policy": dict(POLICY)}
            for key, src in (("position", self.pos), ("heading", self.heading), ("motion", self.motion)):
                if src:
                    d = {k: v for k, v in src.items() if k != "mono"}
                    d["age_s"] = round(mono - src["mono"], 1)
                    out[key] = d
            out["track"] = list(self.track)
        return out


# Parsing policy (operations): a sentence must be a whole, checksummed NMEA 0183 sentence
# unless the server was started with --allow-no-checksum; anything before the first '$'/'!'
# (logger timestamps, tag blocks) is stripped; every number must be finite and in range; a fix
# must be flagged valid. Plain "lat lon" / "heading" text is accepted only with --plain-feed and
# only when the WHOLE line is just those numbers. A wrong position is worse than no position.
POLICY = {"require_checksum": True, "plain_feed": False}


def _strip_to_sentence(line):
    """'12:00:00 $GPGGA,...*47' -> '$GPGGA,...*47' (None if there is no '$' or '!')."""
    i = min((p for p in (line.find("$"), line.find("!")) if p >= 0), default=-1)
    return line[i:].strip() if i >= 0 else None


def _nmea_ok(line):
    """Checksum check. Lines without '*hh' pass only when checksums are not required."""
    if "*" not in line:
        return not POLICY["require_checksum"]
    body, _, cs = line.lstrip("$!").partition("*")
    if len(cs) < 2:
        return False
    try:
        want = int(cs[:2], 16)
    except ValueError:
        return False
    got = 0
    for ch in body:
        got ^= ord(ch)
    return got == want


def _fnum(s):
    """float(s) if it is a finite plain number, else None ('nan', 'inf', '1e999', '' -> None)."""
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _nmea_coord(value, hemi, is_lat):
    """'0042.1234','N' -> decimal degrees. Degrees are everything before the last two digits
    left of the decimal point (so dropped leading zeros, e.g. '42.1234' = 0 deg 42.1234 min,
    still work). Minutes must be 0 <= m < 60; result within +-90 / +-180."""
    if not value or not hemi or hemi.upper() not in (("N", "S") if is_lat else ("E", "W")):
        return None
    if not re.fullmatch(r"\d+(\.\d+)?", value):
        return None
    ip = value.split(".")[0]
    deg = _fnum(ip[:-2] or "0")
    minutes = _fnum(value[len(ip) - 2:] if len(ip) >= 2 else value)
    if deg is None or minutes is None or not (0 <= minutes < 60):
        return None
    v = deg + minutes / 60.0
    if v > (90 if is_lat else 180):
        return None
    return -v if hemi.upper() in ("S", "W") else v


def _fields(line):
    s = _strip_to_sentence(line)
    if s is None or not _nmea_ok(s):
        return None
    return s.split("*")[0].split(",")


_PLAIN_POS = re.compile(r"^\s*([-+]?\d+\.\d+)[\s,]+([-+]?\d+\.\d+)\s*$")
_PLAIN_HDG = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def _mode_invalid(f, idx):
    """NMEA 2.3+ mode indicator at f[idx]: N = not valid, S = simulator."""
    return len(f) > idx and f[idx][:1].upper() in ("N", "S")


def parse_position(line):
    """Returns (lat, lon, utc_hhmmss_or_None, sentence_type) or None."""
    f = _fields(line)
    if f is None:
        if POLICY["plain_feed"] and "$" not in line and "!" not in line:
            m = _PLAIN_POS.match(line)
            if m:
                la, lo = _fnum(m.group(1)), _fnum(m.group(2))
                if la is not None and lo is not None and abs(la) <= 90 and abs(lo) <= 180 and (la, lo) != (0.0, 0.0):
                    return la, lo, None, "plain lat lon"
        return None
    kind = f[0][-3:].upper()
    try:
        if kind == "GGA" and len(f) > 6 and f[6] in ("1", "2", "3", "4", "5", "6"):  # 0 none, 7 manual, 8 simulator
            r = _nmea_coord(f[2], f[3], True), _nmea_coord(f[4], f[5], False), f[1] or None, "GGA"
        elif kind == "RMC" and len(f) > 6 and f[2].upper() == "A" and not _mode_invalid(f, 12):
            r = _nmea_coord(f[3], f[4], True), _nmea_coord(f[5], f[6], False), f[1] or None, "RMC"
        elif kind == "GLL" and len(f) > 4 and (len(f) < 7 or f[6].upper() == "A") and not _mode_invalid(f, 7):
            r = _nmea_coord(f[1], f[2], True), _nmea_coord(f[3], f[4], False), (f[5] if len(f) > 5 else "") or None, "GLL"
        else:
            return None
    except IndexError:
        return None
    if r[0] is None or r[1] is None or (r[0] == 0.0 and r[1] == 0.0):
        return None
    return r


def parse_motion(line):
    """Course and speed over ground from RMC or VTG: returns (cog_deg_true, sog_knots, type) or None.
    This is the direction the ship is MOVING, not where its bow points (that is HDT/THS)."""
    f = _fields(line)
    if f is None:
        return None
    kind = f[0][-3:].upper()
    try:
        if kind == "RMC" and len(f) > 8 and f[2].upper() == "A" and not _mode_invalid(f, 12):
            cog, sog, src = _fnum(f[8]), _fnum(f[7]), "RMC"
        elif kind == "VTG" and len(f) > 5 and not _mode_invalid(f, 9):
            cog, sog, src = _fnum(f[1]), _fnum(f[5]), "VTG"
        else:
            return None
    except IndexError:
        return None
    if cog is None or not (0 <= cog <= 360) or (sog is not None and not (0 <= sog < 60)):
        return None
    return cog % 360, sog, src


def parse_heading(line):
    """Returns (degrees, is_true_heading, sentence_type) or None.
    HDG: magnetic sensor heading, deviation, variation. Converted to true when the variation
    field is present (true = sensor + deviation + variation, E positive); else flagged magnetic."""
    f = _fields(line)
    if f is None:
        if POLICY["plain_feed"] and "$" not in line and "!" not in line:
            m = _PLAIN_HDG.match(line)
            if m and _fnum(m.group(1)) is not None and 0 <= float(m.group(1)) <= 360:
                return float(m.group(1)) % 360, True, "plain heading"
        return None
    kind = f[0][-3:].upper()
    h = _fnum(f[1]) if len(f) > 1 else None
    if h is None or not (0 <= h <= 360):
        return None
    if kind == "HDT":
        return h % 360, True, "HDT"
    if kind == "THS" and (len(f) < 3 or f[2][:1].upper() in ("A", "E", "M")):  # S simulator, V invalid
        return h % 360, True, "THS"
    if kind == "HDG":
        def signed(v, d):
            x = _fnum(v)
            return None if x is None else (x if d.upper() == "E" else -x if d.upper() == "W" else None)
        dev = signed(f[2], f[3]) if len(f) > 3 else None
        var = signed(f[4], f[5]) if len(f) > 5 else None
        if var is not None:
            return (h + (dev or 0.0) + var) % 360, True, "HDG (corrected to true)"
        return (h + (dev or 0.0)) % 360, False, "HDG (magnetic)"
    return None


_TAG = re.compile(r"^[$!][A-Z0-9]{4,6}$")


def _listen(port, kind, ship):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # no SO_REUSEADDR: a second viewer on the same port must fail loudly here, not
        # silently take the feed away from this one
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        with ship.lock:
            ship.errors[str(port)] = (f"cannot listen on UDP {port}: {e} "
                                      "(another program, e.g. nc or a second viewer, has the port)")
        print(f"[ship feed] cannot listen on UDP {port} ({kind}): {e}")
        return
    print(f"[ship feed] listening for {kind} on UDP {port}")
    while True:
        data, _ = sock.recvfrom(65535)
        # a UDP packet holds whole sentences: one, or several separated by line breaks
        for line in data.decode("ascii", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                _handle_line(line, kind, ship)
            except Exception as e:  # never let one odd line kill the listener thread
                with ship.lock:
                    ship.errors[f"{kind} parse"] = f"{type(e).__name__}: {e} (line ignored)"
                    ship.counts["ignored"] += 1


def _handle_line(line, kind, ship):
    now, mono = time.time(), time.monotonic()
    s = _strip_to_sentence(line)
    tag = s.split(",")[0][:7] if s else "(no $)"
    with ship.lock:
        if (_TAG.match(tag) or tag == "(no $)") and (tag in ship.seen[kind] or len(ship.seen[kind]) < 40):
            ship.seen[kind][tag] = ship.seen[kind].get(tag, 0) + 1
    if kind == "gps":
        m = parse_motion(line)
        if m is not None:
            with ship.lock:
                ship.motion = {"cog": m[0], "sog_kn": m[1], "sentence": m[2], "received": now, "mono": mono}
            if m[2] == "VTG":
                with ship.lock:
                    ship.counts["gps_lines"] += 1
                return
        r = parse_position(line)
        with ship.lock:
            ship.counts["gps_lines"] += 1
            if r is None:
                ship.counts["ignored"] += 1
                return
            lat, lon, utc, sent = r
            ship.pos = {"lat": lat, "lon": lon, "utc": utc, "sentence": sent, "received": now, "mono": mono}
            if not ship.track or mono - ship.track_last >= ship.TRACK_EVERY_S:
                ship.track.append([lon, lat, now])
                ship.track_last = mono
                del ship.track[: -ship.TRACK_MAX]
    else:
        r = parse_heading(line)
        with ship.lock:
            ship.counts["heading_lines"] += 1
            if r is None:
                ship.counts["ignored"] += 1
                return
            deg, true_hdg, sent = r
            ship.heading = {"deg": deg, "true": true_hdg, "sentence": sent, "received": now, "mono": mono}


# Site lists that live elsewhere in the repo, served at /sites/<name> (whitelist only).
REPO = Path(__file__).resolve().parent.parent
SITE_FILES = {
    "Previous_Dredges_Compiled.csv": "Site_Maps/Previous_Dredges_Compiled.csv",
    "DredgeSites.csv": "MT_dredging_coords/DredgeSites.csv",
    "MTsites.csv": "MT_dredging_coords/MTsites.csv",
    "DredgeLines.csv": "MT_dredging_coords/DredgeLines.csv",
}
MAX_EXPORT_DEG = 2.0  # per side; ~220 km. Whole datasets are pre-rendered in Native_Maps/
WRITABLE = {"DredgeLines.csv"}  # the viewer may save these (old copy kept in MT_dredging_coords/backups/)
LINE_COLUMNS = ("site", "start_lat", "start_lon", "end_lat", "end_lon")

# Python with numpy/rasterio/pyproj, for the native-resolution export and line refresh
# (this server itself is stdlib-only). Override with --python.
SCIENCE_PY = next((p for p in (Path.home() / "miniforge3/envs/claude-science-env/bin/python",) if p.exists()),
                  Path(sys.executable))


def _send(handler, code, body, ctype, extra=None):
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    for k, v in (extra or {}).items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


_save_lock = threading.Lock()   # one save at a time: backups and the refresh must not interleave
_export_lock = threading.Semaphore(1)  # one native export at a time (each can use GBs of RAM)
MAX_BODY = 5 * 1024 * 1024


def _atomic_write(path, data):
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _ascii(text):
    return text.encode("ascii", "backslashreplace").decode("ascii").replace("\r", " ").replace("\n", " ")


def save_lines(body):
    """Validate and save a DredgeLines.csv posted by the viewer: keep a never-overwritten backup,
    write atomically, then recompute the derived columns (lengths, depths, slopes) from the grids.
    Serialised by a lock. Returns (saved bytes, note)."""
    import csv
    import io
    import shutil
    import subprocess
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    if not rows or any(c not in rows[0] for c in LINE_COLUMNS):
        raise ValueError(f"need columns {', '.join(LINE_COLUMNS)}")
    seen = set()
    for i, r in enumerate(rows, 2):
        site = float(r["site"])
        if not site.is_integer() or site < 1:
            raise ValueError(f"row {i}: site must be a positive whole number, got {r['site']!r}")
        if site in seen:
            raise ValueError(f"row {i}: duplicate site {int(site)}")
        seen.add(site)
        for k, lim in (("start_lat", 90), ("end_lat", 90), ("start_lon", 180), ("end_lon", 180)):
            v = float(r[k])
            if not math.isfinite(v) or abs(v) > lim:
                raise ValueError(f"row {i}: {k} = {r[k]!r} is not a valid coordinate")
    with _save_lock:
        dst = REPO / SITE_FILES["DredgeLines.csv"]
        if dst.exists():
            bdir = dst.parent / "backups"
            bdir.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            for n in range(1000):  # never overwrite an earlier backup
                b = bdir / f"DredgeLines_{stamp}{'' if n == 0 else f'_{n}'}.csv"
                if not b.exists():
                    break
            shutil.copy2(dst, b)
        _atomic_write(dst, body)
        try:
            r = subprocess.run([str(SCIENCE_PY), "-W", "ignore", "dredge_plan.py", "refresh"], cwd=REPO / "Site_Maps",
                               capture_output=True, text=True, timeout=300)
            note = "saved; depths/slopes recomputed" if r.returncode == 0 else \
                "saved, but the depth refresh FAILED (depths/lengths are as drawn): " + \
                _ascii(((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1])
        except (OSError, subprocess.SubprocessError) as e:
            note = f"saved, but the depth refresh could not run ({_ascii(str(e))}); depths/lengths are as drawn"
        return dst.read_bytes(), note


_native_list = None


def native_datasets():
    """Registry of exportable datasets (scripts/native_render.py list --json), cached."""
    global _native_list
    if _native_list is None:
        import subprocess
        r = subprocess.run([str(SCIENCE_PY), "-W", "ignore", "native_render.py", "list", "--json"],
                           cwd=REPO / "scripts", capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"{SCIENCE_PY} could not run native_render.py (needs numpy/rasterio/pyproj): "
                               + (r.stderr.strip().splitlines() or ["?"])[-1])
        _native_list = r.stdout.encode()
    return _native_list


def export_native(q):
    """Native-resolution clip of one dataset over a lon/lat box -> zip bytes (GeoTIFFs + PNG + info)."""
    import subprocess
    import tempfile
    import zipfile
    ds = q.get("ds", "")
    known = {d["id"] for d in json.loads(native_datasets())["datasets"]}
    if ds not in known:
        raise ValueError(f"unknown dataset {ds!r}")
    w, e, s, n = (float(q[k]) for k in ("w", "e", "s", "n"))
    if not all(math.isfinite(v) for v in (w, e, s, n)) or not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError("box must have west < east and south < north, inside -180..180 / -90..90")
    if (e - w) > MAX_EXPORT_DEG or (n - s) > MAX_EXPORT_DEG:
        raise ValueError(f"box is {e - w:.2f} x {n - s:.2f} deg; the limit is {MAX_EXPORT_DEG} deg on a side "
                         "(zoom in or draw a smaller region; whole datasets are in Native_Maps/)")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", q.get("name") or f"{ds}_{s:.3f}_{n:.3f}_{w:.3f}_{e:.3f}").lstrip(".-")
    if not name:
        raise ValueError("bad name")
    if not _export_lock.acquire(timeout=5):
        raise RuntimeError("another export is still running; try again when it finishes")
    try:
        return _export_native_locked(subprocess, tempfile, zipfile, ds, w, e, s, n, name, q)
    finally:
        _export_lock.release()


def _export_native_locked(subprocess, tempfile, zipfile, ds, w, e, s, n, name, q):
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [str(SCIENCE_PY), "-W", "ignore", "native_render.py", "clip", ds, str(w), str(e), str(s), str(n),
               "--out", tmp, "--name", name]
        if q.get("nav") == "1":
            cmd.append("--nav")
        if q.get("wgs84") == "1":
            cmd.append("--wgs84")
        if q.get("slope") == "1":
            cmd.append("--slope")
        if q.get("cmap") and re.fullmatch(r"[A-Za-z0-9_.]+", q["cmap"]):
            cmd += ["--cmap", q["cmap"]]
        r = subprocess.run(cmd, cwd=REPO / "scripts", capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout) else "failed")
        buf = Path(tmp) / "out.zip"
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(Path(tmp).iterdir()):
                if f.name != "out.zip":
                    z.write(f, f"{name}/{f.name}")
        return buf.read_bytes(), name


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
            try:
                body = (REPO / SITE_FILES[name[7:]]).read_bytes()
            except OSError as err:
                _send(self, 404, f"{name[7:]} not found ({err.strerror})".encode(), "text/plain; charset=utf-8")
                return
            _send(self, 200, body, "text/csv; charset=utf-8")
            return
        if name == "/native_datasets":
            try:
                body = native_datasets()
            except Exception as err:
                _send(self, 500, json.dumps({"error": str(err)}).encode(), "application/json")
                return
            _send(self, 200, body, "application/json")
            return
        if name == "/export_native":
            if not self._same_origin():
                _send(self, 403, b"cross-origin request refused", "text/plain")
                return
            from urllib.parse import parse_qsl
            q = dict(parse_qsl(self.path.partition("?")[2]))
            try:
                body, fname = export_native(q)
            except Exception as err:  # report to the page rather than dropping the connection
                _send(self, 400, f"export failed: {err}".encode(), "text/plain; charset=utf-8")
                return
            _send(self, 200, body, "application/zip", {"Content-Disposition": f'attachment; filename="{fname}.zip"'})
            return
        if name == "/ship.json":
            snap = self.ship.snapshot(self.ship_enabled) if self.ship else {"enabled": False}
            try:
                body = json.dumps(snap, allow_nan=False).encode()
            except ValueError:  # should be impossible (parsers reject non-finite numbers); never send bad JSON
                snap.pop("track", None)
                snap["errors"] = dict(snap.get("errors", {}), json="non-finite value dropped")
                body = json.dumps(snap, default=str, allow_nan=True).replace("NaN", "null").encode()
            _send(self, 200, body, "application/json")
            return
        super().do_GET()

    def _same_origin(self):
        """Refuse requests a web page on another site could trigger (CSRF): Origin/Referer, when
        sent, must be this server; Host must be 127.0.0.1/localhost on our port."""
        port = self.server.server_address[1]
        ok_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in ok_hosts:
            return False
        for h in ("Origin", "Referer"):
            v = self.headers.get(h)
            if v and not any(v.startswith(f"http://{o}") for o in ok_hosts):
                return False
        return True

    def do_POST(self):
        name = self.path.split("?")[0]
        if not (name.startswith("/sites/") and name[7:] in WRITABLE):
            _send(self, 403, b"not writable", "text/plain")
            return
        if not self._same_origin():
            _send(self, 403, b"cross-origin request refused", "text/plain")
            return
        try:
            n = int(self.headers.get("Content-Length", ""))
        except ValueError:
            n = -1
        if not (0 < n <= MAX_BODY):
            _send(self, 411 if n < 0 else 413, b"need a Content-Length between 1 byte and 5 MB", "text/plain")
            return
        self.connection.settimeout(30)
        body = self.rfile.read(n)
        try:
            saved, note = save_lines(body)
        except Exception as err:
            _send(self, 400, f"not saved: {err}".encode(), "text/plain; charset=utf-8")
            return
        _send(self, 200, saved, "text/csv; charset=utf-8", {"X-Save-Note": _ascii(note)[:400]})

    def log_message(self, fmt, *args):
        # don't log the once-a-minute ship poll; args[0] is an HTTPStatus (not a str) for errors
        if "/ship.json" not in (str(args[0]) if args else ""):
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
    global SCIENCE_PY
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="port to serve on (default: pick a free one)")
    ap.add_argument("--no-browser", action="store_true", help="don't auto-open a browser tab")
    ap.add_argument("--ship-feed", action="store_true",
                    help="listen for the ship's GPS and heading on UDP and show them in the viewer")
    ap.add_argument("--gps-port", type=int, default=55000, help="UDP port of the GPS feed (default 55000)")
    ap.add_argument("--heading-port", type=int, default=55001, help="UDP port of the heading feed (default 55001)")
    ap.add_argument("--allow-no-checksum", action="store_true",
                    help="accept NMEA sentences without a *hh checksum (default: rejected, a cut-off sentence "
                         "could otherwise give a wrong position)")
    ap.add_argument("--plain-feed", action="store_true",
                    help="also accept plain-text 'lat lon' lines (GPS port) and 'heading' lines (heading port); "
                         "only lines that are exactly those numbers")
    ap.add_argument("--python", default=None,
                    help=f"python with numpy/rasterio/pyproj for native GeoTIFF export (default {SCIENCE_PY})")
    args = ap.parse_args()
    if args.python:
        SCIENCE_PY = Path(args.python)
    POLICY["require_checksum"] = not args.allow_no_checksum
    POLICY["plain_feed"] = args.plain_feed

    NoCacheHandler.ship = ShipState()
    NoCacheHandler.ship_enabled = args.ship_feed
    if args.ship_feed:
        for port, kind in ((args.gps_port, "gps"), (args.heading_port, "heading")):
            threading.Thread(target=_listen, args=(port, kind, NoCacheHandler.ship), daemon=True).start()

    root = Path(__file__).resolve().parent
    port = find_free_port(args.port)
    url = f"http://127.0.0.1:{port}/index.html"

    handler = functools.partial(NoCacheHandler, directory=str(root))
    try:
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        sys.exit(f"Cannot serve on 127.0.0.1:{port}: {e.strerror}. Is another viewer already running? "
                 "Use a different --port, or omit --port to pick a free one.")

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
