"""Small dependency-free web server: static UI + JSON API.

Fine for a home server / Raspberry Pi. To grow later, put it behind nginx or Caddy (for HTTPS)
or port the handlers to Flask/FastAPI; the planner module doesn't care what serves it.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, planner
from .ics import to_ics
from .zones import ZONES, ZipLookup, ZoneError, frost_dates

STATIC_DIR = Path(__file__).parent / "static"
# Fixed allowlist: request paths never touch the filesystem directly.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_CROPS_PARAM = 100
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'",
}

zip_lookup = ZipLookup()


class BadRequest(Exception):
    pass


def _one(q: dict, key: str):
    vals = q.get(key)
    return vals[0].strip() if vals and vals[0].strip() else None


def _date_param(q: dict, key: str):
    v = _one(q, key)
    if v is None:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise BadRequest(f"{key} must be YYYY-MM-DD")


def calendar_from_query(q: dict) -> dict:
    year = _one(q, "year")
    try:
        year = int(year) if year else date.today().year
    except ValueError:
        raise BadRequest("year must be a number")
    if not 1900 <= year <= 2200:
        raise BadRequest("year out of range")

    zone = _one(q, "zone")
    if not zone and _one(q, "zip"):
        zone = zip_lookup.lookup(_one(q, "zip"))["zone"]
    fd = frost_dates(zone, year, _date_param(q, "last_frost"), _date_param(q, "first_frost"))

    ids = [c for c in (_one(q, "crops") or "").split(",") if c]
    if len(ids) > MAX_CROPS_PARAM:
        raise BadRequest("too many crops")
    crops = planner.select_crops(ids or None, _one(q, "category"))
    return planner.build_calendar(fd, crops, include_unsuitable=_one(q, "all") in ("1", "true"))


class Handler(BaseHTTPRequestHandler):
    server_version = f"garden-calendar/{__version__}"

    def _send(self, status: int, body: bytes, content_type: str, extra: dict = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json", {"Cache-Control": "no-store"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        try:
            if url.path in STATIC_FILES:
                name, ctype = STATIC_FILES[url.path]
                self._send(200, (STATIC_DIR / name).read_bytes(), ctype, {"Cache-Control": "max-age=300"})
            elif url.path == "/healthz":
                self._json({"status": "ok", "version": __version__})
            elif url.path == "/api/zones":
                self._json(ZONES)
            elif url.path == "/api/crops":
                self._json(planner.select_crops(category=_one(q, "category")))
            elif url.path == "/api/calendar":
                self._json(calendar_from_query(q))
            elif url.path == "/api/calendar.ics":
                cal = calendar_from_query(q)
                self._send(200, to_ics(cal).encode(), "text/calendar; charset=utf-8",
                           {"Content-Disposition": 'attachment; filename="garden-calendar.ics"'})
            elif url.path.startswith("/api/zip/"):
                self._json(zip_lookup.lookup(url.path.rsplit("/", 1)[-1]))
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (BadRequest, ZoneError, KeyError) as e:
            self._json({"error": str(e.args[0] if e.args else e)}, HTTPStatus.BAD_REQUEST)
        except Exception:
            self.log_error("unhandled error for %s", self.path)
            import traceback
            traceback.print_exc()
            self._json({"error": "internal error"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    httpd = make_server(host, port)
    print(f"Garden calendar on http://{host}:{port}  (Ctrl+C to stop)", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

