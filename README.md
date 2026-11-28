# ONCF Live Map

A Morocco rail tracker: scheduled stop times, **live station departure boards**
and a rich interactive map.

All runtime ONCF API traffic goes through the private gateway:

```text
https://shortora.duckdns.org/oncf
```

The app never calls the upstream data host directly. This repository contains
only the gateway client; the upstream extraction and research notes are kept in
a separate private repository.

![ONCF Live Map](docs/gateway-ui.png)

## Highlights

- **Map-first interface** — opens on the map, with a compact **Plan / Live /
  Map** toolbar.
- **Live station departures** — real departure times, delay minutes and delay
  level, refreshed every 60 seconds.
- **Where is it now?** — timetable-based position estimate with the current leg,
  delay shift and a clear “not GPS” label.
- **I'm on this trip** — one tap makes the map follow the estimated train marker,
  persists across reloads, and offers the mode when GPS finds you at the origin.
- **Direct destinations** — every city reachable from the current origin without
  changing trains, computed from the existing GTFS feed.
- **Speed card** — live GPS speed when the device reports it, otherwise the
  timetable average for the current leg, explicitly labelled as an estimate.
- **Observed punctuality** — local on-time/delay KPIs from the live boards this
  installation has actually seen.
- **Community reports** — travellers can report a cancellation or departure
  delay, confirm/dispute others, all marked unverified.
- **English / French / Arabic UI** — language selector with RTL Arabic.
- **Journey planner** — city or station autocomplete, popular routes, nearest
  station, recent routes, direct-train preselection.
- **Selected trip ribbon** — white/amber glowing route, animated direction
  dashes, green **Start** / red **End** markers, travelled portion filled amber.
- **Night map** — dark vector map by default, with all OpenFreeMap styles
  (Positron, Bright, Liberty, Dark, Fiord, 3D side view), Classic Leaflet,
  satellite and terrain available.
- **Traffic notices** — official works/service notices from the gateway.
- **Station search, stops, dwell times, GPS, proximity alerts and fullscreen.**

## Live departures

```bash
python -m oncf departures --station 231
```

The sidebar card:

- auto-fills with the journey origin and refreshes every 60 s;
- highlights your train with an amber border;
- has a **Near me** GPS shortcut and quick chips for Rabat Ville, Casa
  Voyageurs, Marrakech and Fès;
- serves the last disk cache in `--offline` mode.

This is a **station board**, not a train GPS feed. The app never claims a live
position between stations.

## Where is it now?

For a loaded journey, the **Where is it now?** card estimates the current
position from the published timetable and route shape:

- **Not departed — Casa Voyageurs**
- **At Fès**
- **En route: Fès → Meknès**
- **Arrived at Oujda**

A live station board can supply the actual departure delay at the origin; after
the train has left, community delay reports can shift the estimate. The card
shows the progress, the current/next station, the applied delay and its source.
Every value is labelled **Timetable estimate** — ONCF publishes no train
positions. The API endpoint is:

```text
GET /api/trip-status?train=MA&from=200&to=490&date=2026-11-27
```

## Community reports

The **Report a problem** button opens a native dialog for:

- **Departure delay** (minutes),
- **Cancellation**,
- **Other**.

Reports are stored locally in `data/reports/reports.json` (not committed), shown
next to the trip, and can be **confirmed** or **disputed**. Each browser gets a
random anonymous client id (`oncf:clientId`); the server keeps **one vote per
client per report**. Repeating the same vote is idempotent, and switching from
confirm to dispute moves the count instead of incrementing both. Buttons are
disabled while a vote is in flight. They are explicitly **unverified community
observations**, not official ONCF cancellations. The API is:

```text
GET  /api/reports?train=MA&date=2026-11-27&client_id=...
POST /api/reports
POST /api/reports/{id}/vote   {"vote":"confirm","client_id":"..."}
```

A cancellation report adds an advisory but does not overwrite the timetable
status. A delay report shifts the estimate and is labelled `community`; a live
board delay takes precedence when the train is still at the origin.

## Languages

The UI language selector supports **English, Français and العربية**. The choice
is stored in `oncf:lang`; Arabic switches the document to RTL. All UI chrome,
labels, dates, counts and error/banner messages are translated (including the
static map controls and dynamic panels). Official ONCF notice bodies, community
report messages and station names remain in their published language.

## I'm on this trip

Tap **I'm on this trip** on the status card to enter a persistent follow mode:

- the map centers on the estimated train marker / current stop and keeps
  following it as the timetable advances;
- the status card gets an amber onboard outline and the button becomes
  **Leave trip**;
- the choice survives a page reload (`localStorage` key `oncf:onboard`);
- if GPS is enabled and you are within 2 km of the boarding station, the app
  offers **“Yes, I'm on board”** instead of requiring the button.

The map follows the **timetable estimate**, not GPS telemetry. Your own GPS dot
stays visible and independent. **Leave trip** stops the follow behavior without
closing the journey.

## Direct destinations

For the current origin, the **Direct destinations** card lists every downstream
station reachable on the same train — no changes, no new API. It is computed by
iterating the GTFS trips that call at the origin and collecting their later
stops, then keeping the earliest departure per destination.

Each row shows the destination, next departure, train, product/colour and
duration. Clicking a row opens that direct journey in the existing planner.

```text
GET /api/destinations?station=200&date=2026-11-28&limit=40
```

The station names are display-cleaned (`Aéroport Mohammed V`, `Aïn Taoujdate`,
`Beni Oukil`, `Sebaa Aioun`, `Facultés`, `Fès`, `Kénitra`, `Meknès`, `Salé`),
and Supratours `(suprat.)`/`(supra.)` markers are removed from route labels.
Station codes and trip identities are unchanged.

## Observed punctuality

Every fresh live station board is appended to a local JSONL log, but only when
a departure is first seen or its real time changes. Repeated identical polls do
not inflate the sample.

The **Observed punctuality** card shows:

- number of observations;
- on-time percentage (≤5 min);
- delayed (>5 min) and severe (>15 min);
- average/median/max delay;
- stations and trains covered.

```text
GET /api/punctuality?days=7
```

This is **your installation's sample**, not an official ONCF punctuality
statistic. The log lives in `data/punctuality/observations.jsonl` and is
git-ignored.

## Install and run

```bash
python -m pip install -r requirements.txt
python -m oncf live --port 8767
```

Open <http://127.0.0.1:8767>.

Defaults already point at the gateway. Override only if needed:

```bash
set ONCF_BASE_URL=https://shortora.duckdns.org/oncf
set ONCF_MOBILE_BASE=https://shortora.duckdns.org/oncf
```

Jump straight into a trip:

```bash
python -m oncf live --train MA --from 200 --to 490 --date 2026-11-27 --official-station 380
```

## CLI

```bash
python -m oncf stations --search Rabat
python -m oncf search --from 200 --to 490 --date 2026-11-27
python -m oncf stop --train MA --from 200 --to 490 --station 380 --date 2026-11-27
python -m oncf departures --station 231
python -m oncf sources
```

## Gateway

Only these read paths are used under `/oncf`:

| Path | Purpose |
|---|---|
| `POST /api/schedule` | station-to-station timetables |
| `GET /cache/stations`, `/cache/cities`, `/cache/getServerTimeZone` | reference data |
| `POST /mobile/DepartingTrains` | live station board |
| `POST /mobile/schedule` | mobile timetable |
| `GET /media/CMS/{fr,ar,en}/news` | traffic notices |
| `GET /media/CMS/{fr,ar,en}/pages` | timetable/notices fallback |
| `GET /media/static/...` | timetable PDFs |
| `GET /info-travaux` | official works page |

The gateway is read-only, rate-limited and makes its own TLS connection
upstream. No upstream hostnames, keys or cookies are part of this repository.

## Map

The default **Vector** engine uses MapLibre GL and a dark **Night** style. The
OpenFreeMap styles are available from the same selector:

- **Night** — CARTO Dark Matter (default);
- **Positron** — light minimal;
- **Bright** — light detailed;
- **Liberty** — detailed OSM-style streets/buildings;
- **Dark** — dark minimal;
- **Fiord** — blue minimal;
- **3D** — Liberty with a pitched side view (55°) and building extrusions;
- **Satellite** — Esri imagery;
- **Terrain** — Classic/Leaflet terrain.

- full Morocco network, stations, bilingual labels;
- selected journey ribbon with progress fill and direction dashes;
- station finder, scale, fullscreen, GPS;
- **Focus trip** dims the background network.

The **Classic** engine uses Leaflet with Night (Esri imagery + dark tint),
Satellite, Streets and Terrain. **3D map** adds pitch and building extrusions.
OpenFreeMap vector-only styles (Positron, Bright, Liberty, Dark, Fiord, 3D)
automatically switch to the Vector engine and are remembered across reloads.
See [`docs/MAP_RESEARCH.md`](docs/MAP_RESEARCH.md) for the key-free map survey.

## Honesty

- Live **station departure times and delay levels** are real.
- Train positions between stations, per-stop real times, platforms/voie and
  structured cancellations are **not published**. The orange map marker is a
  timetable estimate.
- The phone's GPS is live and stays on the device.
- ONCF data belongs to ONCF. This gateway is for personal/interoperability use;
  do not resell or redistribute the raw feed. The community GTFS feed remains
  ODbL.
- Timetable data from
  [oncf-gtfs-unofficial](https://github.com/orhazal/oncf-gtfs-unofficial)
  (ODbL), built from ONCF’s published timetables; station locations
  © OpenStreetMap contributors.

## Tests

```bash
python -m pytest -q
```

149 tests cover stop calculations, timezone quirks, GTFS, live departure
parsing/caching, route-shape fallback, works notices, trip status, community
reports and vote idempotence, direct destinations, punctuality logging, the
local API contract and source health. Tests that require official fixtures skip
automatically when the fixtures are absent.

## Project layout

```text
oncf/
  api.py            timetable client through the gateway
  mobile.py         live station departures + 45 s cache
  traffic.py        works/service notices
  sources.py        gateway health checks
  tracker.py        scheduled progress interpolation
  trip_status.py    where-is-it-now estimator + delay shift
  reports.py        local community reports store
  names.py          display-only GTFS station/route cleanup
  punctuality.py    append-only live-board observations + KPIs
  live.py           local HTTP server + UI
  web/i18n.js       English / French / Arabic UI strings
  web/              MapLibre/Leaflet map, live panel, Night style
docs/
  MAP_RESEARCH.md   key-free map stack survey
```

## Private original

The full upstream extraction, source report, legacy notes and historical
research live in a separate **private** repository. This public repository is
intentionally limited to the gateway version.
