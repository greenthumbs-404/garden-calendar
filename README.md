Garden Calendar
Planting and harvest calendars for home gardens across the US, by USDA hardiness zone.
It covers 52 vegetables, fruits, and herbs. You get backend scripts (CLI), a JSON API, and a small web UI.
No dependencies: you only need Python 3.9+. Nothing to `pip install`, so it runs fine on a Raspberry Pi or an old laptop.
Quick start
```bash
cd garden-calendar
python3 -m garden calendar --zone 6b --crops tomato,lettuce,garlic   # one zone, a few crops
python3 -m garden todo --zip 43215                                   # what to do in the next 2 weeks
python3 -m garden serve                                              # web UI at http://127.0.0.1:8080
python3 -m unittest discover -s tests -t .                           # run the tests
```
Scripts (CLI)
Command	What it does
`calendar`	Full-year windows. `--format text|json|csv|ics`
`todo`	Tasks overlapping the next `--days` (default 14) from `--date` (default today)
`zones` / `crops`	List zones with frost dates / list crop ids
`lookup-zip 12345`	ZIP to zone lookup
`serve`	Web UI + API (`--host`, `--port`)
Location options for `calendar`/`todo`: `--zone 6b`, or `--zip 12345`, or your real local frost dates with `--last-frost 2026-04-20 --first-frost 2026-10-15`.
Real local dates are more accurate than zone averages. Filters: `--category vegetable|fruit|herb`, `--crops a,b,c`, and `--all` to include crops not suited to the zone.
Example of a backend job, a weekly email of tasks via cron:
```cron
0 7 * * MON  cd /opt/garden-calendar && python3 -m garden todo --zone 6b --days 7 | mail -s "Garden this week" you@example.com
```
API
Endpoint	Notes
`GET /api/calendar?zone=6b&year=2026&category=herb&crops=basil,dill&all=1&last_frost=YYYY-MM-DD&first_frost=YYYY-MM-DD`	JSON calendar
`GET /api/calendar.ics?...same params...`	Calendar file you can import into Google, Apple, or Outlook
`GET /api/zip/12345`	ZIP to zone lookup
`GET /api/zones`, `GET /api/crops?category=fruit`	Reference data
`GET /healthz`	Health check for monitoring
How dates are computed
Each zone has an average last spring frost (LF) and first fall frost (FF) (`garden/data/zones.json`). Half zones shift these by 5 days: "a" is colder, "b" is warmer.
Each crop's windows are written in weeks relative to LF or FF (`garden/data/crops.json`). Example: tomato starts indoors 8-6 weeks before LF and is transplanted 1-3 weeks after.
Harvest is calculated as planting date + days to maturity.
Frost-tender crops stop at FF.
Frost-hardy crops can run up to 3 weeks past FF.
Perennials and fruit trees have fixed harvest windows.
To add or tune a crop, edit `crops.json`. The format is described at the top of the file.
Known limitation: hardiness zones measure winter lows, not season length. A few areas don't fit the zone average well, such as the Pacific Northwest coast (zone 8-9 with cool summers) and high deserts. Users there should enter local frost dates in the UI ("Know your local frost dates?").
ZIP lookups use the free phzmapi.org dataset (the 2023 USDA map). Each ZIP is cached in `~/.cache/garden-calendar/` (or `$GARDEN_CACHE_DIR`) after the first lookup. Everything else works offline.
Testing from another machine
Copy the folder over: `tar czf garden-calendar.tgz garden-calendar` then `scp` it, or use `git`.
On that machine: `python3 -m unittest discover -s tests -t .`, then `python3 -m garden serve --host 0.0.0.0 --port 8080`.
Open `http://<that-machine-ip>:8080` from a browser on the same network. If it doesn't load, check the firewall (`sudo ufw allow 8080/tcp` on Ubuntu or Raspberry Pi OS).
By default the server listens only on `127.0.0.1`. `--host 0.0.0.0` opens it to your network, so only use it on a network you trust.
Hosting on a small home server
`deploy/garden-calendar.service` is a hardened systemd unit with install steps in its header. It auto-starts on boot and restarts on crashes.
Before exposing it to the internet:
Put Caddy or nginx in front for HTTPS. Caddy gets certificates automatically.
Keep the app bound to `127.0.0.1` behind the proxy.
Add rate limiting at the proxy, since every ZIP lookup that misses the cache calls an outside service.
A Cloudflare Tunnel avoids opening router ports.
Growing later: the planner (`garden/planner.py`) knows nothing about HTTP. If traffic grows, port `server.py` to Flask or FastAPI behind gunicorn or uvicorn, or pre-generate static JSON per zone and serve it from a CDN. Calendars only depend on zone + year, so they cache very well.
Layout
```
garden/
  data/zones.json    frost dates per zone
  data/crops.json    crop rules
  zones.py           zone parsing, frost dates, ZIP lookup + cache
  planner.py         rules -> dated windows, to-do queries
  ics.py             calendar export
  cli.py             scripts
  server.py          web server + API (standard library only)
  static/            UI (plain HTML/CSS/JS)
tests/               unit + API tests
deploy/              systemd unit
```
