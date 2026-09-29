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
    python3 run_viewer.py           # picks a free port, opens the browser
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

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)

    print(f"Serving {root} at {url}")
    print("Press Ctrl+C to stop.")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        httpd.shutdown()


if __name__ == "__main__":
    sys.exit(main())
