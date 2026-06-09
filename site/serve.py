#!/usr/bin/env python3
"""Static dev server for the reader with caching disabled.

`python3 -m http.server` sends no cache headers, so browsers (Chrome especially) keep
serving an old manifest.json / app.js after a rebuild. This sends no-cache on everything so
a plain reload always shows the latest build.

  python3 serve.py [port]     # default 8780  ->  http://localhost:8780
"""
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8780
    here = str(Path(__file__).resolve().parent)
    handler = partial(NoCacheHandler, directory=here)
    print(f"reader (no-cache) → http://localhost:{port}  (serving {here})")
    ThreadingHTTPServer(("", port), handler).serve_forever()


if __name__ == "__main__":
    main()
