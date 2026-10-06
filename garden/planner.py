"""Turns crop rules + frost dates into dated planting/harvest windows."""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Iterable, Optional

from .zones import DATA_DIR, FrostDates, zone_number

CROPS: list[dict] = json.loads((DATA_DIR / "crops.json").read_text())["crops"]
CROPS_BY_ID = {c["id"]: c for c in CROPS}
CATEGORIES = ("vegetable", "fruit", "herb")

EVENT_LABELS = {
    "start_indoors": "Start seeds indoors",
    "transplant": "Transplant outdoors",
    "direct_sow": "Sow outdoors",
    "plant": "Plant",
    "fall_sow": "Sow for fall crop",
    "harvest": "Harvest",
}
HARDY_GRACE = timedelta(weeks=3)  # how long frost-tolerant crops keep going past first frost


def _anchor(name: str, fd: FrostDates) -> date:
    return {"LF": fd.last_frost, "FF": fd.first_frost}[name]


def resolve_window(spec: list, fd: FrostDates) -> tuple[date, date]:
    """[anchor, w1, w2] or [anchor1, w1, anchor2, w2] -> (start, end)."""
    if len(spec) == 3:
        a1, w1, w2 = spec
        a2 = a1
    else:
        a1, w1, a2, w2 = spec
    return _anchor(a1, fd) + timedelta(weeks=w1), _anchor(a2, fd) + timedelta(weeks=w2)


def _event(kind: str, start: date, end: date, note: Optional[str] = None) -> dict:
    ev = {"type": kind, "label": EVENT_LABELS[kind], "start": start.isoformat(), "end": end.isoformat()}
    if note:
        ev["note"] = note
    return ev


def _derived_harvest(crop: dict, plant_start: date, plant_end: date, fd: FrostDates, warnings: list):
    dmin, dmax = crop["days"]
    start = plant_start + timedelta(days=dmin)
    end = plant_end + timedelta(days=dmax)
    limit = fd.first_frost + (HARDY_GRACE if crop.get("hardy") else timedelta(0))
    if crop.get("until_frost"):
        end = limit
    end = min(end, limit)
    if start > limit:
        warnings.append("Season is likely too short here; choose a fast-maturing variety or extend with row covers.")
        return None
    if (end - start).days < 14:
        warnings.append("Very short harvest window here; a fast-maturing variety is safer.")
    return start, end


def plan_crop(crop: dict, fd: FrostDates) -> dict:
    events, warnings = [], []
    suitable = True
    if fd.zone and "zones" in crop:
        lo, hi = crop["zones"]
        if not lo <= zone_number(fd.zone) <= hi:
            suitable = False
            warnings.append(f"Best suited to zones {lo}-{hi}.")

    windows = {k: resolve_window(crop[k], fd) for k in EVENT_LABELS if k in crop and k != "harvest"}
    for kind in ("start_indoors", "plant", "transplant", "direct_sow", "fall_sow"):
        if kind in windows:
            events.append(_event(kind, *windows[kind]))

    if "harvest" in crop:
        h_start, h_end = resolve_window(crop["harvest"], fd)
        note = None
        if not crop.get("perennial") and "plant" in windows and h_start < windows["plant"][0]:
            note = "From the previous fall's planting"
        events.append(_event("harvest", h_start, h_end, note))
    elif "days" in crop:
        spring = [windows[k] for k in ("transplant", "direct_sow") if k in windows]
        if spring:
            h = _derived_harvest(crop, min(s for s, _ in spring), max(e for _, e in spring), fd, warnings)
            if h:
                events.append(_event("harvest", *h, note="Spring/summer planting" if "fall_sow" in windows else None))
        if "fall_sow" in windows:
            h = _derived_harvest(crop, *windows["fall_sow"], fd, warnings)
            spring_end = events[-1]["end"] if events[-1]["type"] == "harvest" else None
            if h and not (spring_end and h[0].isoformat() <= spring_end):  # skip if spring harvest already covers it
                events.append(_event("harvest", *h, note="Fall planting"))

    return {
        "id": crop["id"],
        "name": crop["name"],
        "category": crop["category"],
        "season": crop["season"],
        "perennial": bool(crop.get("perennial")),
        "suitable": suitable,
        "notes": crop.get("notes", ""),
        "warnings": list(dict.fromkeys(warnings)),
        "events": events,
    }


def select_crops(ids: Optional[Iterable[str]] = None, category: Optional[str] = None) -> list[dict]:
    if ids:
        ids = list(ids)
        unknown = [i for i in ids if i not in CROPS_BY_ID]
        if unknown:
            raise KeyError(f"Unknown crop id(s): {', '.join(unknown)}")
        crops = [CROPS_BY_ID[i] for i in ids]
    else:
        crops = CROPS
    if category:
        if category not in CATEGORIES:
            raise KeyError(f"Unknown category {category!r}; use one of {', '.join(CATEGORIES)}")
        crops = [c for c in crops if c["category"] == category]
    return crops


def build_calendar(fd: FrostDates, crops: list[dict], include_unsuitable: bool = False) -> dict:
    plans = [plan_crop(c, fd) for c in crops]
    if not include_unsuitable:
        plans = [p for p in plans if p["suitable"]]
    return {"frost": fd.to_dict(), "crops": plans}


def tasks_between(calendar: dict, start: date, end: date) -> list[dict]:
    """Every event window that overlaps [start, end], soonest first."""
    tasks = []
    for crop in calendar["crops"]:
        for ev in crop["events"]:
            if date.fromisoformat(ev["start"]) <= end and date.fromisoformat(ev["end"]) >= start:
                tasks.append({"crop": crop["name"], "crop_id": crop["id"], **ev})
    return sorted(tasks, key=lambda t: (t["start"], t["crop"]))

