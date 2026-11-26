"""The TwinPic window: a small local web page that talks to this computer only.

The server listens on 127.0.0.1 (this computer, never the network) and
prints a link with a fresh random key, new every time TwinPic starts.
Without the key every page answers 403, and pages that arrive with a
foreign Host header are refused, so no other program or tab can drive it.
"""

from __future__ import annotations

import io
import json
import os
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import core

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

_HTML_PATH = os.path.join(os.path.dirname(__file__), "ui.html")


def _load_html() -> str:
    with open(_HTML_PATH, encoding="utf-8") as f:
        return f.read()


def _bad_folders(folders):
    return [f for f in folders if not os.path.isdir(f)]


class _Handler(BaseHTTPRequestHandler):
    server_version = "TwinPic/0.1"

    # quieter logs
    def log_message(self, *a):  # noqa: D102
        pass

    # --- helpers -----------------------------------------------------
    def _key_ok(self, query) -> bool:
        return query.get("key", [""])[0] == self.server.token

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _forbidden(self):
        self._send(403, b"Forbidden: open TwinPic from the link it "
                        b"printed when it started.",
                   "text/plain; charset=utf-8")

    # --- routes ------------------------------------------------------
    @staticmethod
    def _threshold(raw, default=5):
        try:
            t = int(raw)
        except (TypeError, ValueError):
            raise ValueError("threshold must be a number from 0 to 64")
        return core.check_threshold(t)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        if not self._host_ok() or not self._key_ok(query):
            self._forbidden()
            return
        if url.path == "/":
            html = _load_html().replace("__TOKEN__", self.server.token)
            self._send(200, html.encode(), "text/html; charset=utf-8")
        elif url.path == "/api/scan":
            folders = [f for f in query.get("folders", [""])[0].split("\n") if f.strip()]
            bad = _bad_folders(folders)
            if bad:
                self._json(400, {"error": "folder not found: " + bad[0]})
                return
            try:
                threshold = self._threshold(query.get("threshold", ["5"])[0])
                res = core.scan(folders, threshold=threshold,
                                include_cloud=query.get("cloud", ["0"])[0] == "1")
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            except Exception as e:  # bad folder etc.
                self._json(400, {"error": str(e)})
                return
            # the preview the next "move aside" is allowed to touch
            self.server.last_scan = res
            self._json(200, _scan_payload(res))
        elif url.path == "/thumb":
            self._thumb(query.get("p", [""])[0])
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        if not self._host_ok() or not self._key_ok(query):
            self._forbidden()
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json(400, {"error": "could not read the request"})
            return
        if url.path == "/api/aside":
            holding = str(body.get("holding", "")).strip()
            if not holding:
                self._json(400, {"error": "holding folder is needed"})
                return
            res = self.server.last_scan
            if res is None or not res.groups:
                self._json(400, {"error": "scan a folder first - "
                                          "there is nothing to move yet"})
                return
            keepers = body.get("keepers", [])
            moves = body.get("moves")
            if not isinstance(keepers, list) \
                    or not all(isinstance(k, str) for k in keepers) \
                    or not isinstance(moves, list) \
                    or not all(isinstance(m, str) for m in moves):
                self._json(400, {"error": "keepers and moves must be "
                                          "lists of paths"})
                return
            # Only files from the scanned preview may move; the core
            # re-checks size, mtime and hash before touching anything.
            try:
                out = core.move_aside(res, holding,
                                      keepers=keepers, moves=moves)
            except core.ManifestExistsError as e:
                self._json(400, {"error": str(e)})
                return
            except core.ManifestError as e:
                self._json(400, {"error": str(e)})
                return
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, out)
        elif url.path == "/api/undo":
            holding = str(body.get("holding", "")).strip()
            if not holding:
                self._json(400, {"error": "holding folder is needed"})
                return
            try:
                self._json(200, core.undo(holding))
            except FileNotFoundError:
                self._json(404, {"error": "no TwinPic holding folder found there"})
            except core.ManifestError as e:
                self._json(400, {"error": str(e)})
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def _thumb(self, path: str):
        if Image is None:
            self._json(500, {"error": "Pillow is required"})
            return
        path = os.path.abspath(path)
        if os.path.splitext(path)[1].lower() not in core.IMAGE_EXTS \
                or not os.path.isfile(path):
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((160, 160))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=75)
            self._send(200, buf.getvalue(), "image/jpeg")
        except Exception:
            self._send(415, b"Cannot show this image", "text/plain; charset=utf-8")


def _scan_payload(res: "core.ScanResult") -> dict:
    def member(path):
        size, mtime = res.stats.get(path, (0, 0))
        return {"path": path, "size": size}
    return {
        "scanned": res.scanned,
        "singles": res.singles,
        "unreadable": res.unreadable,
        "cloud_skipped": len(res.cloud_skipped),
        "groups": [{
            "kind": g.kind,
            "keeper": g.keeper,
            "members": [member(p) for p in g.members],
        } for g in res.groups],
    }


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, token: str):
        super().__init__(("127.0.0.1", port), _Handler)
        self.token = token
        self.last_scan: "core.ScanResult | None" = None


def serve(port: int = 0, open_browser: bool = True) -> int:
    token = secrets.token_urlsafe(16)
    httpd = _Server(port, token)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?key={token}"
    print(url, flush=True)
    print("TwinPic is ready. Keep this window open; close it to quit.",
          flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
