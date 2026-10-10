#!/usr/bin/env python3
"""Serve the static export (web/out) on localhost for the offline demo from a laptop: `npm run serve`.

python -m http.server would do, except for two things the installed app needs: the manifest's media type
(application/manifest+json) and a service worker that is never served from the HTTP cache, so a rebuild is picked
up on the next visit. Like Caddy in production, /foo/ serves out/foo/index.html. There is no /api here: live mode
shows its connection error, and ?mode=offline&set=hero-rail runs the recorded example with no network at all.
Standard library only.
"""
import functools
import http.server
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "out"


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".webmanifest": "application/manifest+json",
        ".js": "text/javascript",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".woff2": "font/woff2",
        ".geojson": "application/geo+json",
    }

    def end_headers(self):
        if self.path.split("?")[0] == "/sw.js":
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Service-Worker-Allowed", "/")
        super().end_headers()

    def log_message(self, fmt, *args):  # quiet: one line per request is noise during a demo
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    if not (OUT / "index.html").exists():
        sys.exit("web/out has no index.html: run `npm run build` first")
    handler = functools.partial(Handler, directory=str(OUT))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"serving web/out on http://localhost:{port}  (offline demo: http://localhost:{port}/?mode=offline&set=hero-rail)")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
