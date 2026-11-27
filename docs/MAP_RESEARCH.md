# Map research — GitHub survey (26 November 2026)

Queries used: `leaflet map`, `leaflet transit map`, `maplibre style`,
`openfreemap`, `gtfs map`, `railway map`, `leaflet animated route`,
`leaflet polyline decorator`, `leaflet minimap`, `transit map`, `train map`.
Stars are the values returned by the GitHub API at the time of the survey.

## Candidates reviewed

| Repository | What it offers | Licence | Stars | Verdict |
|---|---|---|---|---|
| [hyperknot/openfreemap](https://github.com/hyperknot/openfreemap) | Free OpenStreetMap vector tiles and styles; no API key | NOASSERTION | 6 527 | **Already used** by the 3D engine. Best free vector option. |
| [hyperknot/openfreemap-styles](https://github.com/hyperknot/openfreemap-styles) | Liberty / Bright / Positron style sources | NOASSERTION | 276 | **Used.** Liberty is the Detailed basemap (Night 3D is the default). |
| [OpenRailwayMap/OpenRailwayMap](https://github.com/OpenRailwayMap/OpenRailwayMap) | OSM railway-infrastructure overlay (tracks, signals, yards) | GPL-3.0 | 523 | **Not used.** Our GTFS network already shows the lines; the overlay adds clutter and GPL/tile-use questions. |
| [bbecquet/Leaflet.PolylineDecorator](https://github.com/bbecquet/Leaflet.PolylineDecorator) | Arrow/dash patterns along polylines | MIT | 521 | **Idea used.** Implemented animated direction dashes with native SVG instead of a dependency. |
| [Norkart/Leaflet-MiniMap](https://github.com/Norkart/Leaflet-MiniMap) | Overview inset | BSD-2-Clause | 390 | **Not used.** The Morocco/Fit buttons already give context; an inset would cover the map. |
| [lzear/ubahnchen](https://github.com/lzear/ubahnchen) | Animated Berlin subway map | — | 93 | **Inspiration** for the moving train and filled progress ribbon. |
| [vasile/transit-map](https://github.com/vasile/transit-map) | Transit-map simulation (swisstrains) | MIT | 369 | **Inspiration** for smooth scheduled movement. |
| [ad-freiburg/loom](https://github.com/ad-freiburg/loom) | Automated schematic transit maps | GPL-3.0 | 319 | **Future.** A separate schematic journey view, not a basemap. |
| [railmapgen/rmg](https://github.com/railmapgen/rmg) | Metro-style rail-map generator | GPL-3.0 | 280 | **Future.** Schematic export could be a nice extra. |
| [maplibre/maputnik](https://github.com/maplibre/maputnik) | Visual MapLibre style editor | MIT | 2 671 | **Reference** for tuning OpenFreeMap styles. |
| [protomaps/basemaps](https://github.com/protomaps/basemaps) | PMTiles basemap generation/styles | NOASSERTION | 780 | **Future offline option**, not a hosted key-free service. |
| [mapnificent/mapnificent](https://github.com/mapnificent/mapnificent) | Public-transport travel-time isochrones | NOASSERTION | 407 | **Out of scope** for this stop tracker. |

## What was implemented

1. **Detailed map** — MapLibre GL JS + **OpenFreeMap Liberty** vector
   tiles: streets, buildings, town/village labels and OpenStreetMap POIs
   (cafés, restaurants, shops, clinics, transport, landmarks). Liberty's stock
   `poi` layers start at zoom 15 (`poi_r1`), 16 (`poi_r7`) and 17 (`poi_r20`),
   but the planet tiles carry POI data from zoom 11. The app uses
   `setLayerZoomRange` to reveal major ranks (1–6) from zoom 11, mid-ranks
   (7–19) from 12 and the full set (20+) from 15, with `poi_transit` from 11.
   Town labels are pulled down to zoom 5 and villages to 6. Arabic labels are
   shaped with MapLibre's **RTL text plugin** (`@mapbox/mapbox-gl-rtl-text`,
   key-free CDN); without it the letters render disconnected and in visual
   order. Clicking a POI opens its name and category; the **Places** button
   toggles the POI layers.
2. **Selected trip ribbon** — when a journey loads, the vector engine dims the
   background network and fades POIs/city labels, then draws the trip as a white
   core inside a wide amber border with amber centre dashes, green Start and red
   End labels. **Focus trip** toggles the dimming in both engines. The fit
   padding is wide enough to keep the terminus labels on screen.
3. **Night basemap (default)** — the 3D engine opens on CARTO Dark Matter with
   our bilingual city labels and the ONCF network. The Classic Night view uses
   Esri World Imagery plus a dark-blue tint pane (`fill: #04101c`, opacity 0.68),
   our own labels and the Natural Earth outline. Detailed/terrain Classic views
   also keep our city labels at country/regional zoom and station dots from
   zoom 5.
4. **Progress ribbon** — in Classic, the travelled part of the selected route
   fills amber; the remaining part stays white with moving dashes.

No API key or tile provider was added. The only new runtime asset is MapLibre's
key-free RTL text plugin, loaded from the same CDN as MapLibre itself.

### Political-neutrality filter

The detailed style keeps every POI and town/village label but hides only the
political layers: any layer whose id contains `boundary`, `admin` or `country`,
plus `label_country_*`, `label_state` and the style's duplicate `label_city` /
`label_city_capital` (the app draws its own bilingual city markers). The
Natural Earth outline remains the single, app-owned border. This removes OSM's
country/state names and disputed boundary lines while keeping the phone-map
detail the user asked for.

## Rejected styles and why

- **Stamen / MapTiler / Mapbox hosted styles** require an API key or account;
  this project stays key-free.
- **Unfiltered OSM raster tiles** label/border the Western Sahara as a separate
  entity. The vector style is filtered (see above) so only its POI and
  town/village layers are used, with the app's own outline and city markers.
- **OpenRailwayMap** is attractive for rail detail but is a GPL overlay with its
  own tile-use policy; the GTFS network already communicates the routes.
- **maptoposter** (`originalankur/maptoposter`, 14.3k ★, MIT) and
  **google-map-react** (6.5k ★, MIT) were reviewed on request. maptoposter is a
  static Python poster generator, not an interactive basemap; google-map-react
  is React-only and needs a Google API key. Neither fits the key-free runtime.
