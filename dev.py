#!/usr/bin/env python3
"""Zero-dependency dev server with live reload.

Usage:
    python dev.py              # http://localhost:8000
    python dev.py --port 3000
    python dev.py --no-open    # don't open a browser tab

Serves this folder, maps "/" to index2.html, disables caching, and injects a
small script into HTML pages that reloads the browser whenever a
.html/.js/.css file in the folder changes.
"""
import argparse
import http.server
import os
import socketserver
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY = "index2.html"
WATCH_EXT = {".html", ".js", ".css"}

RELOAD_SNIPPET = b"""
<script>
(function () {
  var last = null;
  function poll() {
    fetch('/__livereload', { cache: 'no-store' })
      .then(function (r) { return r.text(); })
      .then(function (v) {
        if (last !== null && v !== last) location.reload();
        last = v;
      })
      .catch(function () {})
      .finally(function () { setTimeout(poll, 500); });
  }
  poll();
})();
</script>
"""

_version = "0"


def _snapshot():
    return {
        p: p.stat().st_mtime
        for p in ROOT.iterdir()
        if p.is_file() and p.suffix in WATCH_EXT
    }


def watch():
    global _version
    prev = _snapshot()
    while True:
        time.sleep(0.4)
        try:
            cur = _snapshot()
        except OSError:
            continue
        if cur != prev:
            prev = cur
            _version = str(time.time())
            print("[dev] change detected, reloading browser")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/__livereload":
            body = _version.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/":
            path = "/" + ENTRY
        if path.endswith(".html"):
            file = (ROOT / path.lstrip("/")).resolve()
            if file.is_file() and ROOT in file.parents:
                html = file.read_bytes()
                idx = html.lower().rfind(b"</body>")
                html = html[:idx] + RELOAD_SNIPPET + html[idx:] if idx != -1 else html + RELOAD_SNIPPET
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
        super().do_GET()

    def log_message(self, fmt, *args):
        if "/__livereload" not in (args[0] if args else ""):
            super().log_message(fmt, *args)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    # SO_REUSEADDR on Windows would let us silently share a port another
    # process is using; keep it off so a busy port fails and we move on.
    allow_reuse_address = os.name != "nt"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    httpd = None
    for port in range(args.port, args.port + 20):
        try:
            httpd = Server((args.host, port), Handler)
            break
        except OSError as e:
            print(f"[dev] port {port} unavailable ({e.strerror}), trying {port + 1}")
    if httpd is None:
        raise SystemExit("[dev] no free port found")

    threading.Thread(target=watch, daemon=True).start()
    with httpd:
        url = f"http://localhost:{port}/"
        print(f"[dev] serving {ROOT} at {url} (Ctrl+C to stop)")
        if not args.no_open:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[dev] stopped")


if __name__ == "__main__":
    main()
