/* Optional Mapbox and Google engines.
 *
 * Both need credentials and are only loaded when the user selects them and
 * provides a public token/key. The key is stored in localStorage and never
 * written to the repository.
 */
(function () {
  "use strict";

  const tr = (key, fallback) => (typeof t === "function" ? t(key) : fallback);

  const MOROCCO_BOUNDS = [
    [-17.2, 20.7],
    [-1.0, 35.95],
  ];
  const EMPTY = { type: "FeatureCollection", features: [] };

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.async = true;
      script.onload = resolve;
      script.onerror = () => reject(new Error("failed to load " + src));
      document.head.appendChild(script);
    });
  }

  function loadCss(href) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.appendChild(link);
  }

  function toLine(shape) {
    return (shape || []).map(([lat, lon]) => [lon, lat]);
  }

  function boundsOf(shape) {
    const bounds = { west: 180, south: 90, east: -180, north: -90 };
    for (const [lat, lon] of shape || []) {
      bounds.west = Math.min(bounds.west, lon);
      bounds.east = Math.max(bounds.east, lon);
      bounds.south = Math.min(bounds.south, lat);
      bounds.north = Math.max(bounds.north, lat);
    }
    return bounds;
  }

  /* ------------------------------------------------------------------ */
  /* Mapbox                                                             */
  /* ------------------------------------------------------------------ */
  const MapboxEngine = (function () {
    let map = null;
    let journey = null;
    let user = null;
    let train = null;
    let userMarker = null;
    let trainMarker = null;
    let selected = null;
    let basemap = "satellite";

    const styles = {
      satellite: "mapbox://styles/mapbox/standard-satellite",
      streets: "mapbox://styles/mapbox/streets-v12",
      minimal: "mapbox://styles/mapbox/light-v11",
    };

    async function init(token) {
      if (map) return map;
      loadCss("https://api.mapbox.com/mapbox-gl-js/v3.7.0/mapbox-gl.css");
      await loadScript("https://api.mapbox.com/mapbox-gl-js/v3.7.0/mapbox-gl.js");
      mapboxgl.accessToken = token;
      map = new mapboxgl.Map({
        container: "mapbox",
        style: styles[basemap],
        center: [-9, 31],
        zoom: 5,
        pitch: 0,
        maxPitch: 70,
        worldview: "MA",
        config: { basemap: { show3dObjects: true, show3dLandmarks: true } },
      });
      map.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), "top-left");
      map.on("style.load", () => {
        install();
        replay();
      });
      return map;
    }

    function install() {
      if (!map || map.getSource("oncf-journey")) return;
      map.addSource("oncf-morocco", { type: "geojson", data: "/morocco.geojson" });
      map.addSource("oncf-journey", { type: "geojson", data: EMPTY });
      map.addSource("oncf-stations", { type: "geojson", data: EMPTY });
      map.addSource("oncf-selected", { type: "geojson", data: EMPTY });

      map.addLayer({
        id: "oncf-morocco-line", type: "line", source: "oncf-morocco",
        paint: { "line-color": "#f0a02a", "line-width": 1.5, "line-opacity": 0.9 },
      });
      map.addLayer({
        id: "oncf-journey-casing", type: "line", source: "oncf-journey",
        paint: { "line-color": "#0d1117", "line-width": 7, "line-opacity": 0.55 },
      });
      map.addLayer({
        id: "oncf-journey-line", type: "line", source: "oncf-journey",
        paint: { "line-color": "#e6edf3", "line-width": 3, "line-opacity": 0.95 },
      });
      map.addLayer({
        id: "oncf-stations", type: "circle", source: "oncf-stations",
        paint: {
          "circle-radius": 5, "circle-color": "#c8d3e0",
          "circle-stroke-color": "#0d1117", "circle-stroke-width": 2,
        },
      });
      map.addLayer({
        id: "oncf-selected-ring", type: "circle", source: "oncf-selected",
        paint: { "circle-radius": 9, "circle-color": "transparent", "circle-stroke-color": "#f0a02a", "circle-stroke-width": 2.5 },
      });
      map.on("click", "oncf-stations", (event) => {
        const feature = event.features && event.features[0];
        if (feature && typeof selectStop === "function") selectStop(Number(feature.properties.index));
      });
    }

    function replay() {
      if (journey) renderJourney(journey, false);
      if (user) setUser(user[0], user[1]);
      if (train) setTrain(train);
      if (selected != null) selectStop(selected, false);
    }

    function renderJourney(data, fit = true) {
      journey = data;
      if (!map || !map.getSource("oncf-journey")) return;
      map.getSource("oncf-journey").setData(
        data.shape && data.shape.length > 1
          ? { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: toLine(data.shape) } }
          : EMPTY
      );
      map.getSource("oncf-stations").setData({
        type: "FeatureCollection",
        features: (data.stops || []).map((stop, index) => ({
          type: "Feature",
          properties: { index, name: stop.name.fr },
          geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
        })),
      });
      if (fit && data.shape && data.shape.length > 1) fitRoute();
    }

    function selectStop(index, fly = true) {
      selected = index;
      if (!map || !journey) return;
      const stop = journey.stops[index];
      if (!stop) return;
      map.getSource("oncf-selected").setData({
        type: "FeatureCollection",
        features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [stop.lon, stop.lat] } }],
      });
      if (fly) map.flyTo({ center: [stop.lon, stop.lat], zoom: Math.max(map.getZoom(), 10), duration: 700 });
    }

    function setUser(lat, lon) {
      user = [lat, lon];
      if (!map) return;
      if (!userMarker) {
        const el = document.createElement("div");
        el.className = "user-dot";
        userMarker = new mapboxgl.Marker({ element: el }).setLngLat([lon, lat]).addTo(map);
      } else userMarker.setLngLat([lon, lat]);
    }

    function setTrain(point) {
      if (!point) return;
      train = point;
      if (!map) return;
      const lonLat = [point[1], point[0]];
      if (!trainMarker) {
        const el = document.createElement("div");
        el.className = "train-dot";
        el.textContent = "🚆";
        trainMarker = new mapboxgl.Marker({ element: el }).setLngLat(lonLat).addTo(map);
      } else trainMarker.setLngLat(lonLat);
    }

    function fitRoute() {
      if (!map || !journey || !journey.shape || journey.shape.length < 2) return fitMorocco();
      const b = boundsOf(journey.shape);
      map.fitBounds([[b.west, b.south], [b.east, b.north]], { padding: 40, duration: 700 });
    }

    function fitMorocco() {
      if (map) map.fitBounds(MOROCCO_BOUNDS, { padding: 20, duration: 700 });
    }

    function setBasemap(name) {
      basemap = name;
      if (!map) return;
      map.setStyle(styles[name] || styles.satellite);
    }

    function resize() { if (map) map.resize(); }

    return {
      init, isReady: () => !!map, renderJourney, selectStop, setUser, setTrain,
      fitRoute, fitMorocco, setBasemap, resize, getMap: () => map,
    };
  })();

  /* ------------------------------------------------------------------ */
  /* Google                                                             */
  /* ------------------------------------------------------------------ */
  const GoogleEngine = (function () {
    let map = null;
    let journey = null;
    let user = null;
    let train = null;
    let userMarker = null;
    let trainMarker = null;
    let line = null;
    let stopMarkers = [];
    let selected = null;

    function init(key) {
      if (map) return Promise.resolve(map);
      return new Promise((resolve, reject) => {
        const timer = setTimeout(
          () => reject(new Error("Google Maps did not load (check the key and billing)")),
          15000
        );
        window.__oncfGoogleReady = () => {
          clearTimeout(timer);
          map = new google.maps.Map(document.getElementById("mapGoogle"), {
            center: { lat: 31, lng: -9 },
            zoom: 5,
            mapTypeId: "hybrid",
            tilt: 45,
            heading: 0,
            gestureHandling: "greedy",
            fullscreenControl: false,
            streetViewControl: false,
            mapTypeControl: false,
          });
          resolve(map);
        };
        const script = document.createElement("script");
        script.src =
          "https://maps.googleapis.com/maps/api/js?key=" +
          encodeURIComponent(key) +
          "&region=MA&language=fr&callback=__oncfGoogleReady";
        script.async = true;
        script.onerror = () => reject(new Error("Google Maps failed to load"));
        document.head.appendChild(script);
      });
    }

    function renderJourney(data, fit = true) {
      journey = data;
      if (!map) return;
      if (line) line.setMap(null);
      stopMarkers.forEach((marker) => marker.setMap(null));
      stopMarkers = [];
      if (data.shape && data.shape.length > 1) {
        line = new google.maps.Polyline({
          path: data.shape.map(([lat, lon]) => ({ lat, lng: lon })),
          strokeColor: "#e6edf3",
          strokeOpacity: 0.95,
          strokeWeight: 4,
          map,
        });
      }
      (data.stops || []).forEach((stop, index) => {
        const marker = new google.maps.Marker({
          position: { lat: stop.lat, lng: stop.lon },
          map,
          title: stop.name.fr,
          icon: {
            path: google.maps.SymbolPath.CIRCLE,
            scale: 5,
            fillColor: stop.kind === "origin" || stop.kind === "destination" ? "#f0a02a" : "#c8d3e0",
            fillOpacity: 1,
            strokeColor: "#0d1117",
            strokeWeight: 2,
          },
        });
        marker.addListener("click", () => { if (typeof selectStop === "function") selectStop(index); });
        stopMarkers.push(marker);
      });
      if (fit && data.shape && data.shape.length > 1) fitRoute();
    }

    function selectStop(index, fly = true) {
      selected = index;
      if (!map || !journey) return;
      const stop = journey.stops[index];
      if (!stop) return;
      if (fly) map.panTo({ lat: stop.lat, lng: stop.lon });
    }

    function setUser(lat, lon) {
      user = [lat, lon];
      if (!map) return;
      if (!userMarker) {
        userMarker = new google.maps.Marker({
          position: { lat, lng: lon }, map, title: tr("map.you", "You"),
          icon: { path: google.maps.SymbolPath.CIRCLE, scale: 7, fillColor: "#4c9aff", fillOpacity: 1, strokeColor: "#fff", strokeWeight: 3 },
        });
      } else userMarker.setPosition({ lat, lng: lon });
    }

    function setTrain(point) {
      if (!point) return;
      train = point;
      if (!map) return;
      if (!trainMarker) {
        trainMarker = new google.maps.Marker({
          position: { lat: point[0], lng: point[1] }, map, title: tr("map.scheduledEstimateShort", "Scheduled estimate"),
          icon: { path: google.maps.SymbolPath.CIRCLE, scale: 7, fillColor: "#f0a02a", fillOpacity: 1, strokeColor: "#1a1206", strokeWeight: 2 },
        });
      } else trainMarker.setPosition({ lat: point[0], lng: point[1] });
    }

    function fitRoute() {
      if (!map || !journey || !journey.shape || journey.shape.length < 2) return fitMorocco();
      const bounds = new google.maps.LatLngBounds();
      journey.shape.forEach(([lat, lon]) => bounds.extend({ lat, lng: lon }));
      map.fitBounds(bounds);
    }

    function fitMorocco() {
      if (!map) return;
      const bounds = new google.maps.LatLngBounds();
      MOROCCO_BOUNDS.forEach(([lon, lat]) => bounds.extend({ lat, lng: lon }));
      map.fitBounds(bounds);
    }

    function setBasemap(name) {
      if (!map) return;
      map.setMapTypeId(name === "satellite" ? "hybrid" : name === "streets" ? "roadmap" : "terrain");
    }

    function resize() {
      if (map) google.maps.event.trigger(map, "resize");
    }

    return {
      init: (key) => init(key), isReady: () => !!map, renderJourney, selectStop,
      setUser, setTrain, fitRoute, fitMorocco, setBasemap, resize, getMap: () => map,
    };
  })();

  window.MapboxEngine = MapboxEngine;
  window.GoogleEngine = GoogleEngine;
})();
