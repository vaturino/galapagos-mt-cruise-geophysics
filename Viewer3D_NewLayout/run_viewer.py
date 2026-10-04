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
"""
import argparse
import functools
import http.server
import socket
import sys
import threading
import webbrowser
from pathlib import Path


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """Never let the browser reuse a cached meta.json / mesh.bin. Without this, a rebuilt or
    restored layer can be read with a stale cached meta.json (wrong vertex count / section
    offsets) and silently fail to draw. Restored files with OLD timestamps are the worst
    case: the default handler answers If-Modified-Since with 304 whenever the file's mtime
    is not newer than the browser's cached copy."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

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
    args = ap.parse_args()

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
