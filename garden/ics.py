"""iCalendar (.ics) export so the plan can be imported into Google/Apple/Outlook calendars."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    out, buf = [], b""
    for ch in line:
        b = ch.encode()
        if len(buf) + len(b) > 75:
            out.append(buf.decode())
            buf = b" "
        buf += b
    out.append(buf.decode())
    return "\r\n".join(out)


def to_ics(calendar: dict) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zone = calendar["frost"].get("zone") or "custom"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//garden-calendar//EN",
             f"X-WR-CALNAME:Garden calendar (zone {zone})"]
    for crop in calendar["crops"]:
        for i, ev in enumerate(crop["events"]):
            start = date.fromisoformat(ev["start"])
            end = date.fromisoformat(ev["end"]) + timedelta(days=1)  # DTEND is exclusive
            desc = " ".join(x for x in (ev.get("note", ""), crop["notes"]) if x)
            lines += [
                "BEGIN:VEVENT",
                f"UID:{crop['id']}-{ev['type']}-{i}-{start.isoformat()}@garden-calendar",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
                f"DTEND;VALUE=DATE:{end:%Y%m%d}",
                _fold(f"SUMMARY:{_escape(ev['label'] + ': ' + crop['name'])}"),
                _fold(f"DESCRIPTION:{_escape(desc)}"),
                "TRANSP:TRANSPARENT",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

