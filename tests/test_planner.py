import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date

from garden import planner
from garden.ics import to_ics
from garden.server import make_server
from garden.zones import ZipLookup, ZoneError, frost_dates, parse_zone


class ZoneTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_zone("6B"), ("6", "b"))
        self.assertEqual(parse_zone("10"), ("10", None))
        for bad in ("2a", "12", "6c", "", "../etc"):
            with self.assertRaises(ZoneError):
                parse_zone(bad)

    def test_half_zones_shift_frost_dates(self):
        a, b = frost_dates("6a", 2026), frost_dates("6b", 2026)
        self.assertGreater(a.last_frost, b.last_frost)
        self.assertLess(a.first_frost, b.first_frost)

    def test_custom_dates_override_zone(self):
        fd = frost_dates("6b", 2026, last_frost=date(2026, 5, 1))
        self.assertEqual(fd.last_frost, date(2026, 5, 1))
        self.assertEqual(fd.source, "custom")

    def test_custom_only_requires_both(self):
        with self.assertRaises(ZoneError):
            frost_dates(None, 2026, last_frost=date(2026, 5, 1))
        with self.assertRaises(ZoneError):
            frost_dates(None, 2026, date(2026, 10, 1), date(2026, 5, 1))

    def test_zip_validation_never_hits_network(self):
        for bad in ("1234", "123456", "abcde", "12345/../x"):
            with self.assertRaises(ZoneError):
                ZipLookup(cache_path=None).lookup(bad)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.fd = frost_dates("6b", 2026)

    def plan(self, crop_id, fd=None):
        return planner.plan_crop(planner.CROPS_BY_ID[crop_id], fd or self.fd)

    def events(self, crop_id, kind, fd=None):
        return [e for e in self.plan(crop_id, fd)["events"] if e["type"] == kind]

    def test_all_crops_plan_in_every_zone(self):
        for z in ("3a", "5b", "7a", "9b", "11b"):
            fd = frost_dates(z, 2026)
            for crop in planner.CROPS:
                p = planner.plan_crop(crop, fd)
                for ev in p["events"]:
                    self.assertLessEqual(ev["start"], ev["end"], (z, crop["id"], ev))

    def test_tomato_indoors_before_transplant_before_harvest(self):
        si, tp, hv = (self.events("tomato", k)[0] for k in ("start_indoors", "transplant", "harvest"))
        self.assertLess(si["end"], tp["start"])
        self.assertLess(tp["start"], hv["start"])
        self.assertGreater(tp["start"], self.fd.last_frost.isoformat())  # after frost

    def test_tender_harvest_stops_at_first_frost(self):
        self.assertEqual(self.events("basil", "harvest")[0]["end"], self.fd.first_frost.isoformat())

    def test_hardy_harvest_runs_past_first_frost(self):
        self.assertGreater(self.events("kale", "harvest")[-1]["end"], self.fd.first_frost.isoformat())

    def test_lettuce_has_spring_and_fall_harvests(self):
        self.assertEqual(len(self.events("lettuce", "harvest")), 2)

    def test_zone_suitability(self):
        self.assertFalse(self.plan("meyer-lemon")["suitable"])
        self.assertTrue(self.plan("meyer-lemon", frost_dates("10a", 2026))["suitable"])
        cal = planner.build_calendar(self.fd, planner.select_crops(["meyer-lemon", "tomato"]))
        self.assertEqual([c["id"] for c in cal["crops"]], ["tomato"])

    def test_short_season_warning(self):
        self.assertTrue(self.plan("pumpkin", frost_dates("3a", 2026))["warnings"])

    def test_select_crops_errors(self):
        with self.assertRaises(KeyError):
            planner.select_crops(["nope"])
        with self.assertRaises(KeyError):
            planner.select_crops(category="tree")

    def test_tasks_between(self):
        cal = planner.build_calendar(self.fd, planner.select_crops(["tomato"]))
        tasks = planner.tasks_between(cal, date(2026, 2, 20), date(2026, 2, 27))
        self.assertEqual([t["type"] for t in tasks], ["start_indoors"])

    def test_ics(self):
        cal = planner.build_calendar(self.fd, planner.select_crops(category="herb"))
        ics = to_ics(cal)
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertEqual(ics.count("BEGIN:VEVENT"), sum(len(c["events"]) for c in cal["crops"]))
        self.assertTrue(all(len(line.encode()) <= 75 for line in ics.split("\r\n")))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = make_server("127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_index_and_headers(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

    def test_calendar_api(self):
        status, _, body = self.get("/api/calendar?zone=7a&category=fruit")
        self.assertEqual(status, 200)
        cal = json.loads(body)
        self.assertEqual(cal["frost"]["zone"], "7a")
        self.assertTrue(all(c["category"] == "fruit" for c in cal["crops"]))

    def test_bad_input_is_400_not_500(self):
        for q in ("zone=99", "zone=6b&year=abc", "zone=6b&crops=nope", "zone=6b&last_frost=tomorrow", "year=2026"):
            status, _, body = self.get(f"/api/calendar?{q}")
            self.assertEqual(status, 400, q)
            self.assertIn("error", json.loads(body))

    def test_no_path_traversal(self):
        for p in ("/static/../server.py", "/static/..%2fserver.py", "/../../etc/passwd", "/static/zones.py"):
            self.assertEqual(self.get(p)[0], 404, p)

    def test_ics_endpoint(self):
        status, headers, body = self.get("/api/calendar.ics?zone=5a&crops=tomato")
        self.assertEqual(status, 200)
        self.assertIn("text/calendar", headers["Content-Type"])
        self.assertIn(b"SUMMARY:Harvest: Tomato", body)


if __name__ == "__main__":
    unittest.main()

