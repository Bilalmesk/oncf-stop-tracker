/* MapLibre 3D engine for the ONCF map.
 *
 * Free, no API key. Uses OpenFreeMap vector tiles (detailed streets, POIs,
 * building footprints) and a satellite raster with 3D building extrusions.
 * OSM's disputed admin/country layers are hidden and the app's own Morocco
 * outline is drawn instead.
 */
(function () {
  "use strict";

  const tr = (key, fallback) => (typeof t === "function" ? t(key) : fallback);

  // MapLibre needs this plugin to shape Arabic: without it the letters are
  // drawn disconnected and in visual order. Lazy loading only fetches it when
  // an RTL label is first rendered. Free, no key.
  try {
    maplibregl.setRTLTextPlugin(
      "https://unpkg.com/@mapbox/mapbox-gl-rtl-text@0.2.3/mapbox-gl-rtl-text.js",
      true
    );
  } catch (error) {
    if (window.console) console.warn("RTL text plugin:", error.message);
  }

  const MOROCCO_BOUNDS = [
    [-17.2, 20.7],
    [-1.0, 35.95],
  ];
  const SATELLITE_TILES =
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
  const OFM_PLANET = "https://tiles.openfreemap.org/planet";
  const OFM_LIBERTY = "https://tiles.openfreemap.org/styles/liberty";
  const OFM_POSITRON = "https://tiles.openfreemap.org/styles/positron";
  const OFM_BRIGHT = "https://tiles.openfreemap.org/styles/bright";
  const OFM_DARK = "https://tiles.openfreemap.org/styles/dark";
  const OFM_FIORD = "https://tiles.openfreemap.org/styles/fiord";
  const CARTO_DARK = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

  let map = null;
  let ready = false;
  let basemap = "night";
  let networkData = null;
  let journeyData = null;
  let selectedIndex = null;
  let userCoords = null;
  let trainCoords = null;
  let trainMarker = null;
  let userMarker = null;
  let installed = false;
  let cityMarkers = [];
  let networkVisible = true;
  let placesVisible = true;
  let displayOptions = { labels: 55, buildings: true, routeGlow: true };
  let journeyFocus = false;
  let journeyEndMarkers = [];
  let poiLayersBound = new Set();

  const empty = { type: "FeatureCollection", features: [] };

  function satelliteStyle() {
    return {
      version: 8,
      glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
      sources: {
        sat: {
          type: "raster",
          tiles: [SATELLITE_TILES],
          tileSize: 256,
          maxzoom: 19,
          attribution: "Imagery &copy; Esri, Maxar · buildings &copy; OpenStreetMap",
        },
        ofm: { type: "vector", url: OFM_PLANET },
      },
      layers: [
        { id: "sat", type: "raster", source: "sat" },
        {
          id: "ofm-3d-buildings",
          type: "fill-extrusion",
          source: "ofm",
          "source-layer": "building",
          minzoom: 14,
          paint: {
            "fill-extrusion-color": "#d6cdc0",
            "fill-extrusion-opacity": 0.88,
            "fill-extrusion-height": [
              "interpolate", ["linear"], ["zoom"],
              14, 0,
              16, ["coalesce", ["get", "render_height"], ["get", "height"], 0],
            ],
            "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], ["get", "min_height"], 0],
          },
        },
      ],
    };
  }

  function hidePoliticalLayers() {
    if (!map || !map.getStyle()) return;
    for (const layer of map.getStyle().layers || []) {
      const id = (layer.id || "").toLowerCase();
      if (id.startsWith("oncf-")) continue;
      const political =
        id.includes("boundary") || id.includes("admin") || id.includes("country") ||
        id.includes("state") || id.startsWith("label_country") || id === "label_state";
      // Keep the style's town/village labels. Our bilingual markers cover the
      // major cities, so hide duplicate city labels from the tile style. The
      // CARTO Night style names them place_city_* / place_capital_*.
      const duplicateCity =
        id === "label_city" || id === "label_city_capital" ||
        id.startsWith("place_city") || id.startsWith("place_capital");
      if (political || duplicateCity) {
        try { map.setLayoutProperty(layer.id, "visibility", "none"); } catch (_) {}
      }
    }
  }

  function boostDetailLayers() {
    if (!map || !map.getStyle()) return;
    // OpenFreeMap's planet tiles carry POIs from zoom 11 and town/village
    // places from much lower zooms; Liberty only reveals them late. Show the
    // important POIs at city scale and towns/villages earlier so zooming out
    // keeps the map informative. Minor POIs still wait for street zoom.
    const ranges = {
      label_other: [7, 24],
      label_town: [5, 24],
      label_village: [6, 24],
      poi_r1: [11, 24],
      poi_r7: [12, 24],
      poi_r20: [15, 24],
      poi_transit: [11, 24],
    };
    for (const [id, [minzoom, maxzoom]] of Object.entries(ranges)) {
      if (!map.getLayer(id)) continue;
      try { map.setLayerZoomRange(id, minzoom, maxzoom); } catch (_) {}
    }
  }

  function poiLayerIds() {
    if (!map || !map.getStyle()) return [];
    return (map.getStyle().layers || [])
      .filter((layer) => layer.type === "symbol" && (
        layer["source-layer"] === "poi" || (layer.id || "").startsWith("poi")
      ))
      .map((layer) => layer.id);
  }

  function applyPlacesVisibility() {
    if (!map || !map.getStyle()) return;
    for (const id of poiLayerIds()) {
      try {
        map.setLayoutProperty(id, "visibility", placesVisible ? "visible" : "none");
      } catch (_) {}
    }
  }

  function setPlacesVisible(visible) {
    placesVisible = !!visible;
    applyPlacesVisibility();
    return placesVisible;
  }

  function applyDisplayOptions() {
    if (!map || !map.getStyle()) return;
    for (const id of ["building-3d", "ofm-3d-buildings"]) {
      if (!map.getLayer(id)) continue;
      try { map.setLayoutProperty(id, "visibility", displayOptions.buildings ? "visible" : "none"); } catch (_) {}
    }
    for (const id of ["oncf-journey-glow", "oncf-journey-flow"]) {
      if (!map.getLayer(id)) continue;
      try { map.setLayoutProperty(id, "visibility", displayOptions.routeGlow ? "visible" : "none"); } catch (_) {}
    }
  }

  function setDisplayOptions(options = {}) {
    displayOptions = { ...displayOptions, ...options };
    renderCityMarkers((networkData && networkData.cities) || []);
    applyDisplayOptions();
    return displayOptions;
  }

  function installPoiInteractions() {
    if (!map || !map.getStyle()) return;
    for (const id of poiLayerIds()) {
      if (poiLayersBound.has(id)) continue;
      poiLayersBound.add(id);
      map.on("click", id, (event) => {
        const feature = event.features && event.features[0];
        if (!feature) return;
        const p = feature.properties || {};
        const name = p.name || p["name:fr"] || p["name:latin"] || p["name:en"] || tr("map.place", "Place");
        const kind = [p.class, p.subclass].filter(Boolean).join(" · ");
        const el = document.createElement("div");
        const title = document.createElement("div");
        title.style.fontWeight = "650";
        title.textContent = name;
        el.appendChild(title);
        if (kind) {
          const sub = document.createElement("div");
          sub.className = "small";
          sub.style.color = "#a9bacd";
          sub.textContent = kind;
          el.appendChild(sub);
        }
        new maplibregl.Popup({ closeButton: true })
          .setLngLat(event.lngLat)
          .setDOMContent(el)
          .addTo(map);
      });
      map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
    }
  }

  function add3DBuildings() {
    // Liberty already ships an extruded `building-3d` layer on the same vector
    // tiles. Reusing it avoids a second `ofm-3d` source and duplicate tile
    // downloads, which is the main source of 3D loading jank.
    if (map.getLayer("building-3d")) return;
    if (map.getLayer("ofm-3d-buildings")) return;
    const style = map.getStyle() || {};
    const sourceId = ["openmaptiles", "ofm"].find((id) => style.sources && style.sources[id]);
    if (!sourceId && !map.getSource("ofm-3d")) {
      map.addSource("ofm-3d", { type: "vector", url: OFM_PLANET });
    }
    const layers = style.layers || [];
    const firstSymbol = layers.find((layer) => layer.type === "symbol");
    map.addLayer(
      {
        id: "ofm-3d-buildings",
        type: "fill-extrusion",
        source: sourceId || "ofm-3d",
        "source-layer": "building",
        minzoom: 14,
        paint: {
          "fill-extrusion-color": "#d6cdc0",
          "fill-extrusion-opacity": 0.88,
          "fill-extrusion-height": [
            "interpolate", ["linear"], ["zoom"],
            14, 0,
            16, ["coalesce", ["get", "render_height"], ["get", "height"], 0],
          ],
          "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], ["get", "min_height"], 0],
        },
      },
      firstSymbol ? firstSymbol.id : undefined
    );
  }

  function toLine(shape) {
    return shape.map(([lat, lon]) => [lon, lat]);
  }

  function routeFeatures(data) {
    const features = [];
    for (const route of data.routes || []) {
      if (!route.shape || route.shape.length < 2) continue;
      features.push({
        type: "Feature",
        properties: {
          color: route.color || "#888888",
          coach: !!route.coach,
          product: route.product || "Rail",
          name: route.name || "",
          approximate: !!route.approximate,
        },
        geometry: { type: "LineString", coordinates: toLine(route.shape) },
      });
    }
    return features;
  }

  function stationFeatures(data) {
    return (data.stations || []).map((station) => ({
      type: "Feature",
      properties: {
        code: station.code,
        name: station.name,
        ar: station.ar || "",
        city: station.city || "",
      },
      geometry: { type: "Point", coordinates: [station.lon, station.lat] },
    }));
  }

  function cityFeatures(data) {
    const ranked = [...(data.cities || [])];
    const rank = (city) => {
      if (typeof cityRank === "function") return cityRank(city);
      return 999;
    };
    ranked.sort((a, b) => rank(a) - rank(b) || b.stations - a.stations || a.name.localeCompare(b.name));
    return ranked.slice(0, 80).map((city) => ({
      type: "Feature",
      properties: {
        name: city.name,
        stations: city.stations,
      },
      geometry: { type: "Point", coordinates: [city.lon, city.lat] },
    }));
  }

  function journeyFeatures(data) {
    const line = {
      type: "FeatureCollection",
      features: data.shape && data.shape.length > 1
        ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: toLine(data.shape) } }]
        : [],
    };
    const stops = {
      type: "FeatureCollection",
      features: (data.stops || []).map((stop, index) => ({
        type: "Feature",
        properties: {
          seq: stop.sequence,
          index,
          name: stop.name.fr,
          arrival: stop.arrival_time,
          departure: stop.departure_time,
          duration: stop.duration_label,
          kind: stop.kind,
        },
        geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
      })),
    };
    return { line, stops };
  }

  function installOverlays() {
    // A style swap clears every custom source. Never trust `installed` across
    // styles: re-check the live style and reinstall when the source is gone.
    if (!map || map.getSource("oncf-morocco")) return;
    map.addSource("oncf-morocco", { type: "geojson", data: "/morocco.geojson" });
    map.addSource("oncf-net-routes", { type: "geojson", data: empty });
    map.addSource("oncf-net-stations", { type: "geojson", data: empty });
    map.addSource("oncf-cities", { type: "geojson", data: empty });
    map.addSource("oncf-journey", { type: "geojson", data: empty });
    map.addSource("oncf-journey-stations", { type: "geojson", data: empty });
    map.addSource("oncf-selected", { type: "geojson", data: empty });

    map.addLayer({
      id: "oncf-morocco-line",
      type: "line",
      source: "oncf-morocco",
      paint: { "line-color": "#f0a02a", "line-width": 1.5, "line-opacity": 0.9 },
    });

    map.addLayer({
      id: "oncf-network-coach",
      type: "line",
      source: "oncf-net-routes",
      filter: ["==", ["get", "coach"], true],
      paint: { "line-color": ["get", "color"], "line-width": 1.6, "line-opacity": 0.5, "line-dasharray": [3, 4] },
    });
    map.addLayer({
      id: "oncf-network-line",
      type: "line",
      source: "oncf-net-routes",
      filter: ["==", ["get", "coach"], false],
      paint: { "line-color": ["get", "color"], "line-width": 2.6, "line-opacity": 0.7 },
    });
    map.addLayer({
      id: "oncf-network-stations",
      type: "circle",
      source: "oncf-net-stations",
      paint: {
        "circle-radius": 2.8,
        "circle-color": "#e6edf3",
        "circle-stroke-color": "#0d1117",
        "circle-stroke-width": 1,
      },
    });

    // Selected trip: a wide amber glow, dark casing, white core and a dashed
    // amber centre line. Widths grow with zoom so the route stays obvious from
    // country view down to street level.
    map.addLayer({
      id: "oncf-journey-glow",
      type: "line",
      source: "oncf-journey",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#f0a02a",
        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 14, 10, 20, 15, 28],
        "line-opacity": 0.26,
        "line-blur": 6,
      },
    });
    map.addLayer({
      id: "oncf-journey-casing",
      type: "line",
      source: "oncf-journey",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#0b1017",
        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 9, 10, 12, 15, 16],
        "line-opacity": 0.92,
      },
    });
    map.addLayer({
      id: "oncf-journey-amber",
      type: "line",
      source: "oncf-journey",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#ffb020",
        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 7, 10, 9, 15, 13],
        "line-opacity": 1,
      },
    });
    map.addLayer({
      id: "oncf-journey-line",
      type: "line",
      source: "oncf-journey",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#ffffff",
        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 3, 10, 4.2, 15, 6.2],
        "line-opacity": 1,
      },
    });
    map.addLayer({
      id: "oncf-journey-flow",
      type: "line",
      source: "oncf-journey",
      layout: { "line-cap": "butt", "line-join": "round" },
      paint: {
        "line-color": "#ffb020",
        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1.8, 10, 2.6, 15, 3.6],
        "line-opacity": 0.9,
        "line-dasharray": [1.1, 2.6],
      },
    });
    map.addLayer({
      id: "oncf-journey-stations",
      type: "circle",
      source: "oncf-journey-stations",
      paint: {
        "circle-radius": ["case", ["==", ["get", "kind"], "origin"], 7.5, ["==", ["get", "kind"], "destination"], 7.5, 4],
        "circle-color": ["match", ["get", "kind"], "origin", "#3fb950", "destination", "#f85149", "#c8d3e0"],
        "circle-stroke-color": "#0b1017",
        "circle-stroke-width": 2.5,
      },
    });
    map.addLayer({
      id: "oncf-selected-ring",
      type: "circle",
      source: "oncf-selected",
      paint: { "circle-radius": 9, "circle-color": "transparent", "circle-stroke-color": "#f0a02a", "circle-stroke-width": 2.5 },
    });

    map.addLayer({
      id: "oncf-city-dots",
      type: "circle",
      source: "oncf-cities",
      paint: { "circle-radius": 1.8, "circle-color": "#ffffff", "circle-opacity": 0.7 },
    });

    installInteractions();
    installed = true;
  }

  function installInteractions() {
    map.on("click", "oncf-net-stations", (event) => {
      const feature = event.features && event.features[0];
      if (!feature) return;
      const p = feature.properties;
      new maplibregl.Popup({ closeButton: true })
        .setLngLat(event.lngLat)
        .setHTML(stationPopupHtml({ code: p.code, name: p.name, ar: p.ar, city: p.city }))
        .addTo(map);
    });

    map.on("click", "oncf-journey-stations", (event) => {
      const feature = event.features && event.features[0];
      if (!feature) return;
      if (typeof selectStop === "function") selectStop(Number(feature.properties.index));
    });

    for (const layer of ["oncf-net-stations", "oncf-journey-stations"]) {
      map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; });
    }

    const routePopup = (event) => {
      const feature = event.features && event.features[0];
      if (!feature) return;
      const p = feature.properties;
      const note = p.approximate
        ? `<div style="color:#8b98a9;font-size:11px;margin-top:4px">${tr("map.coachApprox", "Coach route — straight-line approximation")}</div>`
        : "";
      new maplibregl.Popup({ closeButton: true })
        .setLngLat(event.lngLat)
        .setHTML(
          `<div style="font-weight:650">${p.product}</div>` +
          `<div class="small" style="color:#a9bacd">${p.name || ""}</div>${note}`
        )
        .addTo(map);
    };
    map.on("click", "oncf-network-line", routePopup);
    map.on("click", "oncf-network-coach", routePopup);
    for (const layer of ["oncf-network-line", "oncf-network-coach"]) {
      map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; });
    }
  }

  function onStyleReady() {
    if (!map) return;
    hidePoliticalLayers();
    boostDetailLayers();
    add3DBuildings();
    installOverlays();
    installPoiInteractions();
    applyPlacesVisibility();
    applyDisplayOptions();
    applyJourneyFocus();
    replay();
  }

  function replay() {
    if (!ready) return;
    if (networkData) renderNetwork(networkData);
    if (journeyData) renderJourney(journeyData, false);
    if (userCoords) setUser(userCoords[0], userCoords[1]);
    if (trainCoords) setTrain(trainCoords);
    if (selectedIndex != null) selectStop(selectedIndex, false);
  }

  function renderNetwork(data) {
    networkData = data;
    if (!ready) return;
    const routes = map.getSource("oncf-net-routes");
    const stations = map.getSource("oncf-net-stations");
    const cities = map.getSource("oncf-cities");
    if (routes) routes.setData({ type: "FeatureCollection", features: routeFeatures(data) });
    if (stations) stations.setData({ type: "FeatureCollection", features: stationFeatures(data) });
    if (cities) cities.setData({ type: "FeatureCollection", features: cityFeatures(data) });
    renderCityMarkers(data.cities);
  }

  function renderCityMarkers(cities) {
    for (const entry of cityMarkers) entry.marker.remove();
    cityMarkers = [];
    if (!map) return;
    const rank = (city) => (typeof cityRank === "function" ? cityRank(city) : 999);
    const limit = Math.max(0, Math.min(80, Number(displayOptions.labels) || 0));
    const ranked = limit === 0 ? [] : [...(cities || [])]
      .sort((a, b) => rank(a) - rank(b) || b.stations - a.stations || a.name.localeCompare(b.name))
      .slice(0, limit);

    ranked.forEach((city, index) => {
      const element = document.createElement("div");
      element.className = "city-label";
      element.innerHTML =
        `<span class="city-name">${city.name}</span>` +
        (city.ar ? `<span class="city-ar" dir="rtl" lang="ar">${city.ar}</span>` : "");
      const marker = new maplibregl.Marker({ element, anchor: "bottom" })
        .setLngLat([city.lon, city.lat])
        .addTo(map);
      cityMarkers.push({ marker, element, rank: index });
    });
    updateCityMarkers();
  }

  function updateCityMarkers() {
    if (!map || !displayOptions.labels || !cityMarkers.length) return;
    const showArabic = map.getZoom() >= 6;
    // City labels are context, not network lines: keep them when the Network
    // toggle is off. Only a focused trip hides them.
    const active = !journeyFocus;

    // Show every city as a candidate, then keep only labels that fit without
    // overlapping. Priority order is the ranked city order, so the country
    // view keeps the big cities and smaller places appear as space allows.
    for (const entry of cityMarkers) {
      entry.element.style.display = active ? "" : "none";
      const arabic = entry.element.querySelector(".city-ar");
      if (arabic) arabic.style.display = showArabic ? "" : "none";
    }
    if (!active) return;

    const mapRect = map.getContainer().getBoundingClientRect();
    const blockers = [];
    const find = document.getElementById("mapFind");
    if (find && find.offsetParent) blockers.push(find.getBoundingClientRect());
    const control = map.getContainer().querySelector(".maplibregl-ctrl-top-left");
    if (control) blockers.push(control.getBoundingClientRect());
    const placed = [];
    for (const entry of cityMarkers) {
      const el = entry.element;
      const r = el.getBoundingClientRect();
      const box = { left: r.left - 3, right: r.right + 3, top: r.top - 2, bottom: r.bottom + 2 };
      // Hide labels that would be clipped at the edge or sit under the search
      // box / zoom controls; phone maps do the same instead of cutting text.
      const offscreen =
        box.left < mapRect.left + 4 || box.right > mapRect.right - 4 ||
        box.top < mapRect.top + 4 || box.bottom > mapRect.bottom - 4;
      const overlaps = placed.some(
        (p) => !(box.right < p.left || box.left > p.right || box.bottom < p.top || box.top > p.bottom)
      );
      const covered = blockers.some(
        (b) => !(box.right < b.left || box.left > b.right || box.bottom < b.top || box.top > b.bottom)
      );
      if (offscreen || overlaps || covered) el.style.display = "none";
      else placed.push(box);
    }
  }

  function applyJourneyFocus() {
    if (!map) return;
    const dim = journeyFocus;
    const networkPaint = {
      "oncf-network-line": ["line-opacity", dim ? 0.10 : 0.7],
      "oncf-network-coach": ["line-opacity", dim ? 0.05 : 0.5],
      "oncf-network-stations": ["circle-opacity", dim ? 0.08 : 1],
      "oncf-city-dots": ["circle-opacity", dim ? 0.08 : 0.7],
    };
    for (const [id, [prop, value]] of Object.entries(networkPaint)) {
      if (!map.getLayer(id)) continue;
      try { map.setPaintProperty(id, prop, value); } catch (_) {}
    }
    // Fade the basemap's own POIs and small-place labels while a trip is
    // focused, so the amber/white ribbon is unmistakably the main line.
    for (const id of ["label_town", "label_village", "label_other", "poi_r1", "poi_r7", "poi_r20", "poi_transit"]) {
      if (!map.getLayer(id)) continue;
      try { map.setPaintProperty(id, "text-opacity", dim ? 0.3 : 1); } catch (_) {}
      try { map.setPaintProperty(id, "icon-opacity", dim ? 0.3 : 1); } catch (_) {}
    }
    updateCityMarkers();
  }

  function setJourneyFocus(on) {
    journeyFocus = !!on;
    applyJourneyFocus();
    return journeyFocus;
  }

  function clearJourneyEndMarkers() {
    for (const marker of journeyEndMarkers) marker.remove();
    journeyEndMarkers = [];
  }

  function renderJourneyEndMarkers(data) {
    clearJourneyEndMarkers();
    if (!map || !data) return;
    const stops = data.stops || [];
    if (!stops.length) return;
    const origin = stops.find((stop) => stop.kind === "origin") || stops[0];
    const destination = [...stops].reverse().find((stop) => stop.kind === "destination") || stops[stops.length - 1];
    const add = (stop, role) => {
      if (!stop || stop.lat == null) return;
      const element = document.createElement("div");
      element.className = "journey-end " + role;
      const label = document.createElement("span");
      label.className = "journey-end-role";
      label.textContent = role === "origin" ? tr("map.start", "Start") : tr("map.end", "End");
      const name = document.createElement("span");
      name.textContent = (stop.name && (stop.name.fr || stop.name)) || tr("map.station", "Station");
      element.append(label, name);
      journeyEndMarkers.push(
        new maplibregl.Marker({ element, anchor: "bottom", offset: [0, -18] })
          .setLngLat([stop.lon, stop.lat])
          .addTo(map)
      );
    };
    add(origin, "origin");
    add(destination, "destination");
  }

  function renderJourney(data, fit = true) {
    journeyData = data;
    if (!ready) {
      renderJourneyEndMarkers(data);
      return;
    }
    const lineSource = map.getSource("oncf-journey");
    if (!lineSource) return;
    const { line, stops } = journeyFeatures(data);
    lineSource.setData(line);
    const stopsSource = map.getSource("oncf-journey-stations");
    if (stopsSource) stopsSource.setData(stops);
    const selectedSource = map.getSource("oncf-selected");
    if (selectedSource) selectedSource.setData(empty);
    renderJourneyEndMarkers(data);
    try {
      map.setPaintProperty(
        "oncf-journey-line",
        "line-dasharray",
        data.shape_source === "straight" ? [2, 1.4] : null
      );
    } catch (_) {}

    if (trainCoords) setTrain(trainCoords);
    else if (data.shape && data.shape.length) setTrain(data.shape[0]);

    if (fit && data.shape && data.shape.length > 1) {
      const bounds = data.shape.reduce(
        (acc, [lat, lon]) => acc.extend([lon, lat]),
        new maplibregl.LngLatBounds([data.shape[0][1], data.shape[0][0]], [data.shape[0][1], data.shape[0][0]])
      );
      map.fitBounds(bounds, { padding: 90, duration: 700 });
    }
  }

  function selectStop(index, fly = true) {
    selectedIndex = index;
    if (!ready || !journeyData) return;
    const stop = journeyData.stops[index];
    if (!stop || stop.lat == null) return;
    const selectedSource = map.getSource("oncf-selected");
    if (selectedSource) {
      selectedSource.setData({
        type: "FeatureCollection",
        features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [stop.lon, stop.lat] } }],
      });
    }
    if (fly) {
      map.flyTo({
        center: [stop.lon, stop.lat],
        zoom: Math.max(map.getZoom(), 10),
        duration: 700,
      });
    }
  }

  function setUser(lat, lon) {
    userCoords = [lat, lon];
    if (!map) return;
    if (!userMarker) {
      const el = document.createElement("div");
      el.className = "user-dot";
      userMarker = new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat([lon, lat]).addTo(map);
    } else {
      userMarker.setLngLat([lon, lat]);
    }
  }

  function setTrain(point) {
    if (!point) return;
    trainCoords = point;
    if (!map) return;
    const lonLat = [point[1], point[0]];
    if (!trainMarker) {
      const el = document.createElement("div");
      el.className = "train-dot";
      el.textContent = "🚆";
      trainMarker = new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat(lonLat).addTo(map);
    } else {
      trainMarker.setLngLat(lonLat);
    }
  }

  function fitMorocco() {
    if (!map) return;
    map.fitBounds(MOROCCO_BOUNDS, { padding: 20, duration: 700 });
  }

  function fitRoute() {
    if (!map) return;
    const shape = journeyData && journeyData.shape;
    if (!shape || shape.length < 2) {
      fitMorocco();
      return;
    }
    const bounds = new maplibregl.LngLatBounds([shape[0][1], shape[0][0]], [shape[0][1], shape[0][0]]);
    for (const [lat, lon] of shape) bounds.extend([lon, lat]);
    map.fitBounds(bounds, { padding: 90, duration: 700 });
  }

  function toggle3D() {
    if (!map) return;
    map.easeTo({ pitch: map.getPitch() > 10 ? 0 : 55, duration: 600 });
  }

  function styleFor(name) {
    if (name === "satellite") return satelliteStyle();
    if (name === "night") return CARTO_DARK;
    if (name === "positron") return OFM_POSITRON;
    if (name === "bright") return OFM_BRIGHT;
    if (name === "dark") return OFM_DARK;
    if (name === "fiord") return OFM_FIORD;
    return OFM_LIBERTY;
  }

  function setBasemap(name) {
    if (!map) return;
    const target = name === "detailed" || name === "streets" ? "liberty" : name;
    basemap = target;
    installed = false;
    map.setStyle(styleFor(target));
    if (target === "3d") {
      map.easeTo({ pitch: 55, bearing: -20, duration: 700 });
    } else if (map.getPitch() > 0 || map.getBearing() !== 0) {
      map.easeTo({ pitch: 0, bearing: 0, duration: 500 });
    }

    // setStyle does not always emit style.load in time; install once the map
    // is idle, unless the style.load handler already did it.
    const installWhenReady = () => {
      if (!map) return;
      if (!map.isStyleLoaded()) {
        map.once("idle", installWhenReady);
        return;
      }
      if (!map.getSource("oncf-morocco")) onStyleReady();
    };
    map.once("idle", installWhenReady);
  }

  function setNetworkVisible(visible) {
    if (!map) return;
    networkVisible = visible;
    const ids = [
      "oncf-network-coach", "oncf-network-line", "oncf-network-stations",
      "oncf-city-dots",
    ];
    for (const id of ids) {
      if (map.getLayer(id)) {
        map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
      }
    }
    applyJourneyFocus();
  }

  function init(containerId, initialBasemap) {
    if (map) return map;
    if (initialBasemap) basemap = initialBasemap;
    map = new maplibregl.Map({
      container: containerId || "map3d",
      style: styleFor(basemap),
      center: [-5.5, 33.9],
      zoom: 6,
      pitch: basemap === "3d" ? 55 : 0,
      bearing: basemap === "3d" ? -20 : 0,
      maxPitch: 70,
      attributionControl: true,
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-left");
    map.on("zoomend", updateCityMarkers);
    map.on("moveend", updateCityMarkers);
    map.on("style.load", () => {
      ready = true;
      installed = false; // the new style cleared all custom layers/sources
      onStyleReady();
    });
    // Some browsers/cached styles finish before the style.load listener is
    // attached; the idle fallback guarantees overlays and 3D buildings install.
    map.once("idle", () => {
      ready = true;
      if (!installed) onStyleReady();
    });
    map.on("error", (event) => {
      const message = (event.error && event.error.message) || "";
      if (message.includes("Unimplemented type")) return; // benign tile-geometry warning
      if (window.console) console.warn("MapLibre:", message);
    });
    return map;
  }

  window.VectorMap = {
    init,
    isReady: () => !!map,
    getMap: () => map,
    renderNetwork,
    renderJourney,
    selectStop,
    setUser,
    setTrain,
    fitMorocco,
    fitRoute,
    toggle3D,
    setBasemap,
    setNetworkVisible,
    setJourneyFocus,
    setPlacesVisible,
    setDisplayOptions,
    getPlacesVisible: () => placesVisible,
    fitMoroccoBounds: () => map && map.fitBounds(MOROCCO_BOUNDS, { padding: 20, duration: 0 }),
    resize: () => map && map.resize(),
    getBasemap: () => basemap,
  };
})();
