"""Hardiness zone parsing, frost-date lookup, and ZIP -> zone lookup."""
from __future__ import annotations

import json
import os
import re
import ssl
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent / "data"
ZONE_RE = re.compile(r"^(1[01]|[3-9])([ab])?$")
ZIP_RE = re.compile(r"^\d{5}$")
ZIP_API = "https://phzmapi.org/{zip}.json"  # free USDA 2023 map data, no API key
# Some Python builds (pyenv/mise) don't see the OS trust store; fall back to common bundle paths.
SYSTEM_CA_BUNDLES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem")

_zone_data = json.loads((DATA_DIR / "zones.json").read_text())
ZONES = _zone_data["zones"]
HALF_SHIFT = timedelta(days=_zone_data["half_zone_shift_days"])


class ZoneError(ValueError):
    pass


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs() and not os.environ.get("SSL_CERT_FILE"):
        for path in SYSTEM_CA_BUNDLES:
            if os.path.exists(path):
                ctx.load_verify_locations(cafile=path)
                break
    return ctx


@dataclass
class FrostDates:
    last_frost: date
    first_frost: date
    zone: Optional[str]
    frost_free: bool
    source: str  # "zone" or "custom"

    def to_dict(self) -> dict:
        return {
            "zone": self.zone,
            "last_frost": self.last_frost.isoformat(),
            "first_frost": self.first_frost.isoformat(),
            "frost_free": self.frost_free,
            "source": self.source,
        }


def parse_zone(zone: str) -> tuple[str, Optional[str]]:
    """'6b' -> ('6', 'b'). Raises ZoneError for zones outside 3-11."""
    m = ZONE_RE.match(str(zone).strip().lower())
    if not m:
        raise ZoneError(f"Unsupported zone {zone!r}; expected 3-11 with optional a/b (e.g. '6b')")
    return m.group(1), m.group(2)


def zone_number(zone: str) -> int:
    return int(parse_zone(zone)[0])


def _md(year: int, md: str) -> date:
    month, day = (int(x) for x in md.split("-"))
    return date(year, month, day)


def frost_dates(
    zone: Optional[str] = None,
    year: Optional[int] = None,
    last_frost: Optional[date] = None,
    first_frost: Optional[date] = None,
) -> FrostDates:
    """Frost dates for a zone, optionally overridden by known local dates."""
    year = year or date.today().year
    if zone is None and (last_frost is None or first_frost is None):
        raise ZoneError("Provide a zone, or both last_frost and first_frost dates")

    frost_free = False
    lf = ff = None
    if zone is not None:
        num, half = parse_zone(zone)
        z = ZONES[num]
        lf, ff = _md(year, z["last_frost"]), _md(year, z["first_frost"])
        frost_free = z["frost_free"]
        if half == "a":  # colder half of the zone
            lf, ff = lf + HALF_SHIFT, ff - HALF_SHIFT
        elif half == "b":
            lf, ff = lf - HALF_SHIFT, ff + HALF_SHIFT

    custom = last_frost is not None or first_frost is not None
    lf = last_frost or lf
    ff = first_frost or ff
    if ff <= lf:
        raise ZoneError("first_frost must be after last_frost")
    return FrostDates(lf, ff, zone.lower() if zone else None, frost_free, "custom" if custom else "zone")


class ZipLookup:
    """ZIP -> zone via phzmapi.org, cached on disk so each ZIP is fetched once."""

    def __init__(self, cache_path: Optional[Path] = None, timeout: float = 5.0):
        cache_dir = Path(os.environ.get("GARDEN_CACHE_DIR", Path.home() / ".cache" / "garden-calendar"))
        self.cache_path = cache_path or cache_dir / "zip_zones.json"
        self.timeout = timeout
        self._lock = threading.Lock()
        self._ssl = _ssl_context()
        try:
            self._cache = json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            self._cache = {}

    def lookup(self, zip_code: str) -> dict:
        zip_code = str(zip_code).strip()
        if not ZIP_RE.match(zip_code):
            raise ZoneError("ZIP code must be exactly 5 digits")
        with self._lock:
            if zip_code in self._cache:
                return self._cache[zip_code]

        try:
            with urllib.request.urlopen(ZIP_API.format(zip=zip_code), timeout=self.timeout, context=self._ssl) as resp:
                raw = json.loads(resp.read(10_000))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise ZoneError(f"No zone found for ZIP {zip_code}") from e
            raise ZoneError(f"Zone lookup service error ({e.code}); pick your zone manually") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise ZoneError("Zone lookup service unreachable; pick your zone manually") from e

        zone = str(raw.get("zone", "")).lower()
        parse_zone(zone)  # reject anything we can't plan for (e.g. Alaska zone 1-2, Hawaii 12-13)
        result = {"zip": zip_code, "zone": zone, "temperature_range": raw.get("temperature_range")}
        with self._lock:
            self._cache[zip_code] = result
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache, indent=0, sort_keys=True))
            tmp.replace(self.cache_path)
        return result

