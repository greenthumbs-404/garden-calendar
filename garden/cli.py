"""Command-line entry point: python3 -m garden <command> ..."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, timedelta

from . import planner
from .ics import to_ics
from .zones import ZONES, ZipLookup, ZoneError, frost_dates


def _fmt(d: str) -> str:
    return date.fromisoformat(d).strftime("%b %d %Y")


def _add_location_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--zone", help="USDA hardiness zone, e.g. 6b")
    p.add_argument("--zip", help="5-digit US ZIP code (looked up online, then cached)")
    p.add_argument("--last-frost", type=date.fromisoformat, help="Override average last spring frost (YYYY-MM-DD)")
    p.add_argument("--first-frost", type=date.fromisoformat, help="Override average first fall frost (YYYY-MM-DD)")
    p.add_argument("--year", type=int, default=date.today().year)
    p.add_argument("--crops", help="Comma-separated crop ids (see 'crops' command)")
    p.add_argument("--category", choices=planner.CATEGORIES)
    p.add_argument("--all", action="store_true", help="Include crops not suited to the zone")


def _calendar(args) -> dict:
    zone = args.zone
    if args.zip:
        zone = ZipLookup().lookup(args.zip)["zone"]
        print(f"ZIP {args.zip} is zone {zone}", file=sys.stderr)
    fd = frost_dates(zone, args.year, args.last_frost, args.first_frost)
    ids = [c.strip() for c in args.crops.split(",")] if args.crops else None
    return planner.build_calendar(fd, planner.select_crops(ids, args.category), args.all)


def _print_calendar(cal: dict) -> None:
    f = cal["frost"]
    print(f"Zone {f['zone'] or '(custom)'}: last frost ~{_fmt(f['last_frost'])}, "
          f"first frost ~{_fmt(f['first_frost'])}" + ("  [frost rare]" if f["frost_free"] else ""))
    for crop in cal["crops"]:
        print(f"\n{crop['name']} ({crop['category']})")
        for ev in crop["events"]:
            note = f"  ({ev['note']})" if ev.get("note") else ""
            print(f"  {ev['label']:<22} {_fmt(ev['start'])} - {_fmt(ev['end'])}{note}")
        for w in crop["warnings"]:
            print(f"  ! {w}")


def cmd_calendar(args) -> None:
    cal = _calendar(args)
    if args.format == "json":
        json.dump(cal, sys.stdout, indent=2)
        print()
    elif args.format == "ics":
        sys.stdout.write(to_ics(cal))
    elif args.format == "csv":
        w = csv.writer(sys.stdout)
        w.writerow(["crop", "category", "event", "start", "end", "note"])
        for crop in cal["crops"]:
            for ev in crop["events"]:
                w.writerow([crop["name"], crop["category"], ev["label"], ev["start"], ev["end"], ev.get("note", "")])
    else:
        _print_calendar(cal)


def cmd_todo(args) -> None:
    on = args.date or date.today()
    args.year = on.year
    cal = _calendar(args)
    tasks = planner.tasks_between(cal, on, on + timedelta(days=args.days))
    print(f"Garden tasks {on:%b %d} - {on + timedelta(days=args.days):%b %d %Y}:")
    if not tasks:
        print("  Nothing scheduled.")
    for t in tasks:
        print(f"  {t['label']:<22} {t['crop']:<28} {_fmt(t['start'])} - {_fmt(t['end'])}")


def cmd_zones(_args) -> None:
    for z, d in ZONES.items():
        print(f"Zone {z:>2}: last frost ~{d['last_frost']}, first frost ~{d['first_frost']}"
              + ("  (frost rare)" if d["frost_free"] else ""))


def cmd_crops(args) -> None:
    for c in planner.select_crops(category=args.category):
        zones = f"zones {c['zones'][0]}-{c['zones'][1]}" if "zones" in c else "all zones"
        print(f"{c['id']:<16} {c['name']:<28} {c['category']:<10} {c['season']:<10} {zones}")


def cmd_lookup_zip(args) -> None:
    print(json.dumps(ZipLookup().lookup(args.zip)))


def cmd_serve(args) -> None:
    from .server import serve
    serve(args.host, args.port)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="garden", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("calendar", help="Full-year planting/harvest calendar")
    _add_location_args(p)
    p.add_argument("--format", choices=["text", "json", "csv", "ics"], default="text")
    p.set_defaults(func=cmd_calendar)

    p = sub.add_parser("todo", help="What to do in the next N days")
    _add_location_args(p)
    p.add_argument("--date", type=date.fromisoformat, help="Start date (default today)")
    p.add_argument("--days", type=int, default=14)
    p.set_defaults(func=cmd_todo)

    sub.add_parser("zones", help="List zones and frost dates").set_defaults(func=cmd_zones)

    p = sub.add_parser("crops", help="List crops")
    p.add_argument("--category", choices=planner.CATEGORIES)
    p.set_defaults(func=cmd_crops)

    p = sub.add_parser("lookup-zip", help="Find the hardiness zone for a ZIP code")
    p.add_argument("zip")
    p.set_defaults(func=cmd_lookup_zip)

    p = sub.add_parser("serve", help="Run the web app + JSON API")
    p.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 to allow other machines to connect")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_serve)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except (ZoneError, KeyError) as e:
        print(f"error: {e.args[0] if e.args else e}", file=sys.stderr)
        return 2
    return 0

