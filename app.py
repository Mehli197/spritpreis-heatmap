"""
app.py  –  Spritpreis-Heatmap  (Streamlit)
Starten: streamlit run app.py
"""

import math
import time
from datetime import datetime

import requests
import folium
import folium.plugins as plugins
import streamlit as st
import streamlit.components.v1 as components

# ══════════════════════════════════════════════════════════════════════════════
#  KONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
API_KEY       = "44bd67ba-352c-4174-95df-32abe924ff12"
BASE_URL      = "https://creativecommons.tankerkoenig.de/json/list.php"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
FUEL_MAP      = {"diesel": "diesel", "super": "e5", "e10": "e10"}
MAX_RADIUS    = 25
API_DELAY     = 0.5


# ══════════════════════════════════════════════════════════════════════════════
#  HILFSFUNKTIONEN
# ══════════════════════════════════════════════════════════════════════════════

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def price_to_color(price, p_min, p_max):
    if p_max == p_min:
        return "#ffdd00"
    t = max(0.0, min(1.0, (price - p_min) / (p_max - p_min)))
    if t < 0.5:
        r, g = int(2 * t * 255), 200
    else:
        r, g = 220, int((1 - 2 * (t - 0.5)) * 200)
    return f"#{r:02x}{g:02x}{30:02x}"


def radius_to_zoom(radius_km):
    if radius_km <= 3:  return 14
    if radius_km <= 5:  return 13
    if radius_km <= 10: return 12
    if radius_km <= 20: return 11
    if radius_km <= 30: return 10
    return 9


# ══════════════════════════════════════════════════════════════════════════════
#  GEOCODING
# ══════════════════════════════════════════════════════════════════════════════

def geocode(ort: str):
    params = {
        "q": ort, "format": "json",
        "countrycodes": "de", "limit": 1, "addressdetails": 1,
    }
    headers = {"User-Agent": "SpritpreisHeatmap/1.0 (private project)"}
    try:
        r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
        results = r.json()
    except Exception as e:
        return None, f"Geocoding-Fehler: {e}"
    if not results:
        return None, f"Ort '{ort}' nicht gefunden."
    hit  = results[0]
    lat  = float(hit["lat"])
    lon  = float(hit["lon"])
    name = hit.get("display_name", ort).split(",")[0].strip()
    return (lat, lon, name), None


# ══════════════════════════════════════════════════════════════════════════════
#  API
# ══════════════════════════════════════════════════════════════════════════════

def fetch_by_location(lat, lon, radius_km, fuel_type, progress_cb=None):
    api_type = FUEL_MAP[fuel_type]
    stations = {}

    if radius_km <= MAX_RADIUS:
        grid        = [(lat, lon)]
        call_radius = radius_km
    else:
        step_lat    = (MAX_RADIUS * 1.3) / 111.0
        step_lon    = (MAX_RADIUS * 1.3) / (111.0 * math.cos(math.radians(lat)))
        call_radius = MAX_RADIUS
        grid        = []
        d_lat       = -radius_km / 111.0
        while d_lat <= radius_km / 111.0 + 0.001:
            d_lon_max = radius_km / (111.0 * math.cos(math.radians(lat)))
            d_lon = -d_lon_max
            while d_lon <= d_lon_max + 0.001:
                pt_lat, pt_lon = lat + d_lat, lon + d_lon
                if haversine_km(lat, lon, pt_lat, pt_lon) <= radius_km + 5:
                    grid.append((round(pt_lat, 4), round(pt_lon, 4)))
                d_lon += step_lon
            d_lat += step_lat

    n_calls = len(grid)
    for i, (g_lat, g_lon) in enumerate(grid):
        if progress_cb:
            progress_cb(i / n_calls, f"API-Call {i+1} / {n_calls} …")
        params = {
            "lat": g_lat, "lng": g_lon,
            "rad": call_radius,
            "type": api_type,
            "sort": "price",
            "apikey": API_KEY,
        }
        try:
            r    = requests.get(BASE_URL, params=params, timeout=10)
            data = r.json()
            if data.get("ok"):
                for s in data.get("stations", []):
                    sid = s.get("id")
                    if sid and s.get("price") and s["price"] > 0:
                        dist = haversine_km(lat, lon, s["lat"], s["lng"])
                        if dist <= radius_km:
                            s["_dist_km"] = round(dist, 1)
                            stations[sid] = s
        except Exception:
            pass
        if n_calls > 1:
            time.sleep(API_DELAY)

    if progress_cb:
        progress_cb(1.0, f"Fertig – {len(stations)} Tankstellen gefunden")
    return stations


# ══════════════════════════════════════════════════════════════════════════════
#  KARTE
# ══════════════════════════════════════════════════════════════════════════════

def build_map_html(stations, fuel_type, region_name,
                   center, zoom, search_radius=None, top_n=10):
    prices       = [s["price"] for s in stations.values()]
    p_min, p_max = min(prices), max(prices)
    p_avg        = sum(prices) / len(prices)

    m = folium.Map(location=list(center), zoom_start=zoom,
                   tiles=None, control_scale=True)
    folium.TileLayer("CartoDB positron",    name="Karte (hell)",   control=True).add_to(m)
    folium.TileLayer("CartoDB dark_matter", name="Karte (dunkel)", control=True).add_to(m)

    # Suchradius-Kreis
    if search_radius and center:
        folium.Circle(
            location=list(center), radius=search_radius * 1000,
            color="#3388ff", weight=2,
            fill=True, fill_color="#3388ff", fill_opacity=0.05,
            tooltip=f"Suchradius: {search_radius:.0f} km",
        ).add_to(m)
        folium.Marker(
            location=list(center),
            tooltip=f"📍 {region_name}",
            icon=folium.Icon(color="blue", icon="home"),
        ).add_to(m)

    # Heatmap
    heat_data = []
    for s in stations.values():
        w = 0.3 + 0.7 * (s["price"] - p_min) / (p_max - p_min) if p_max > p_min else 0.5
        heat_data.append([s["lat"], s["lng"], w])

    plugins.HeatMap(
        heat_data, name="🔥 Heatmap",
        min_opacity=0.35, max_zoom=13, radius=20, blur=25,
        gradient={"0.0": "#00ff00", "0.3": "#aaff00", "0.5": "#ffff00",
                  "0.7": "#ffaa00", "1.0": "#ff0000"},
        show=True,
    ).add_to(m)

    # Alle Marker
    marker_fg = folium.FeatureGroup(name="📍 Alle Tankstellen", show=False)
    for s in stations.values():
        color     = price_to_color(s["price"], p_min, p_max)
        brand     = s.get("brand") or "–"
        name      = s.get("name")  or brand
        street    = f"{s.get('street', '')} {s.get('houseNumber', '')}".strip()
        place     = s.get("place", "")
        dist_line = f"<br>📏 {s['_dist_km']:.1f} km" if "_dist_km" in s else ""
        popup = f"""<div style="font-family:sans-serif;min-width:180px">
            <b>{name}</b><br><span style="color:#666">{brand}</span><br><br>
            📍 {street}, {place}<br>
            <span style="font-size:18px;font-weight:700;color:{color}">{s['price']:.3f} €</span>
            <small>/L {fuel_type.upper()}</small>{dist_line}</div>"""
        folium.CircleMarker(
            [s["lat"], s["lng"]], radius=5,
            color="white", weight=0.6,
            fill=True, fill_color=color, fill_opacity=0.85,
            popup=folium.Popup(popup, max_width=240),
            tooltip=f"{brand} – {s['price']:.3f} €",
        ).add_to(marker_fg)
    marker_fg.add_to(m)

    # Top N
    actual_top = min(top_n, len(stations))
    top_fg     = folium.FeatureGroup(name=f"⭐ Top {actual_top} günstigste", show=True)
    cheapest   = sorted(stations.values(), key=lambda x: x["price"])[:actual_top]
    for rank, s in enumerate(cheapest, 1):
        brand     = s.get("brand") or "–"
        name      = s.get("name")  or brand
        street    = f"{s.get('street', '')} {s.get('houseNumber', '')}".strip()
        place     = s.get("place", "")
        dist_line = f"<br>📏 {s['_dist_km']:.1f} km" if "_dist_km" in s else ""
        popup = f"""<div style="font-family:sans-serif;min-width:200px">
            <div style="background:#00a81e;color:white;padding:5px 10px;
                        border-radius:6px 6px 0 0;margin:-12px -12px 10px -12px;
                        font-weight:600">#{rank} günstigste</div>
            <b>{name}</b><br><span style="color:#666">{brand}</span><br><br>
            📍 {street}, {place}<br>
            <span style="font-size:20px;font-weight:700;color:#00a81e">{s['price']:.3f} €</span>
            <small>/L {fuel_type.upper()}</small>{dist_line}</div>"""
        folium.Marker(
            [s["lat"], s["lng"]],
            popup=folium.Popup(popup, max_width=260),
            tooltip=f"#{rank}  {brand} – {s['price']:.3f} €",
            icon=folium.DivIcon(
                html=f"""<div style="background:#00a81e;color:white;border-radius:50%;
                    width:28px;height:28px;line-height:28px;text-align:center;
                    font-weight:bold;font-size:13px;border:2px solid white;
                    box-shadow:0 1px 5px rgba(0,0,0,0.4)">{rank}</div>""",
                icon_size=(28, 28), icon_anchor=(14, 14),
            ),
        ).add_to(top_fg)
    top_fg.add_to(m)

    # Legende oben links
    ts_now      = datetime.now().strftime("%d.%m.%Y %H:%M")
    radius_text = f" · {search_radius:.0f} km" if search_radius else ""
    legend = f"""
    <style>
      .sprit-legend {{
        position: fixed; top: 10px; left: 10px; z-index: 9999;
        background: rgba(255,255,255,0.95); border-radius: 8px;
        padding: 8px 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.25);
        font-family: sans-serif; font-size: 11px; line-height: 1.6; max-width: 170px;
      }}
      .sprit-gradient {{
        height: 8px; border-radius: 4px; margin: 4px 0;
        background: linear-gradient(to right,#00ff00,#aaff00,#ffff00,#ffaa00,#ff0000);
      }}
      .sprit-minmax {{
        display: flex; justify-content: space-between; font-size: 10px; color: #666;
      }}
    </style>
    <div class="sprit-legend">
      <b>⛽ {fuel_type.upper()}</b>{radius_text}<br>
      <div class="sprit-gradient"></div>
      <div class="sprit-minmax">
        <span>{p_min:.3f}€</span><span>Ø{p_avg:.3f}€</span><span>{p_max:.3f}€</span>
      </div>
      <hr style="margin:4px 0;border:none;border-top:1px solid #ddd">
      <span style="color:#888;font-size:10px">{len(stations):,} Stationen<br>{ts_now}</span>
    </div>"""
    m.get_root().html.add_child(folium.Element(legend))
    folium.LayerControl(collapsed=False).add_to(m)

    html = m._repr_html_()

    # ── Höhen-Patch: html+body+folium-map auf 100% zwingen ───────────────────
    # Folium setzt die Map-Div auf "width:100%;height:100%" aber body hat keine
    # definierte Höhe → Leaflet bekommt height=0. Fix: body explizit auf 100vh.
    height_patch = """
    <style>
      html { height: 100%; }
      body { height: 100%; margin: 0; padding: 0; }
      .folium-map { height: 100% !important; }
    </style>"""
    html = html.replace("<head>", "<head>" + height_patch)

    return html


# ══════════════════════════════════════════════════════════════════════════════
#  GPS-KOMPONENTE
# ══════════════════════════════════════════════════════════════════════════════

GPS_HTML = """
<style>
  body { margin: 0; font-family: sans-serif; }
  #gps-btn {
    width: 100%; padding: 12px; background: #0066cc; color: white;
    border: none; border-radius: 8px; font-size: 15px; cursor: pointer;
  }
  #gps-btn:disabled { background: #4caf50; cursor: default; }
  #gps-status {
    margin-top: 6px; font-size: 12px; color: #444;
    text-align: center; min-height: 16px;
  }
</style>
<button id="gps-btn" onclick="getLocation()">📍 Meinen Standort verwenden</button>
<div id="gps-status"></div>
<script>
function getLocation() {
  var btn    = document.getElementById('gps-btn');
  var status = document.getElementById('gps-status');
  if (!navigator.geolocation) {
    status.innerHTML = '❌ GPS nicht verfügbar.';
    return;
  }
  btn.disabled = true;
  btn.innerText = '⏳ Wird ermittelt …';
  navigator.geolocation.getCurrentPosition(
    function(pos) {
      var lat = pos.coords.latitude.toFixed(6);
      var lon = pos.coords.longitude.toFixed(6);
      btn.innerText = '✅ ' + lat + ', ' + lon;
      status.innerHTML = '↓ Übernehmen klicken';
      // In URL schreiben ohne Reload
      try {
        var url = new URL(window.parent.location.href);
        url.searchParams.set('gps_lat', lat);
        url.searchParams.set('gps_lon', lon);
        window.parent.history.replaceState({}, '', url.toString());
      } catch(e) {}
      // Felder direkt befüllen
      try {
        var allInputs = window.parent.document.querySelectorAll('input');
        allInputs.forEach(function(inp) {
          var wrapper = inp.closest('[data-testid="stTextInput"]');
          if (!wrapper) return;
          var label = wrapper.querySelector('label');
          if (!label) return;
          if (label.innerText.includes('Breitengrad')) {
            inp.value = lat;
            inp.dispatchEvent(new Event('input', {bubbles:true}));
            inp.dispatchEvent(new Event('change', {bubbles:true}));
          }
          if (label.innerText.includes('Längengrad')) {
            inp.value = lon;
            inp.dispatchEvent(new Event('input', {bubbles:true}));
            inp.dispatchEvent(new Event('change', {bubbles:true}));
          }
        });
      } catch(e) {}
    },
    function(err) {
      btn.disabled = false;
      btn.innerText = '📍 Meinen Standort verwenden';
      status.innerHTML = '❌ ' + err.message;
    },
    { enableHighAccuracy: true, timeout: 15000 }
  );
}
</script>
"""


# ══════════════════════════════════════════════════════════════════════════════
#  STREAMLIT UI
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Spritpreis-Heatmap",
    page_icon="⛽",
    layout="wide",
)

# Session State
if "fullscreen" not in st.session_state:
    st.session_state.fullscreen = False
if "stations" not in st.session_state:
    st.session_state.stations = None
if "map_html" not in st.session_state:
    st.session_state.map_html = None
if "gps_lat" not in st.session_state:
    st.session_state.gps_lat = None
if "gps_lon" not in st.session_state:
    st.session_state.gps_lon = None

# GPS aus URL lesen
params = st.query_params
if "gps_lat" in params and "gps_lon" in params:
    try:
        st.session_state.gps_lat = float(params["gps_lat"])
        st.session_state.gps_lon = float(params["gps_lon"])
    except Exception:
        pass

# ── Vollbild-Modus ────────────────────────────────────────────────────────────
if st.session_state.fullscreen and st.session_state.map_html:
    st.markdown(
        "<style>.block-container{padding:0.5rem 1rem !important;}"
        "header{display:none !important;}</style>",
        unsafe_allow_html=True,
    )
    if st.button("✕ Vollbild beenden", type="secondary"):
        st.session_state.fullscreen = False
        st.rerun()
    # Im Vollbild: iframe so hoch wie möglich
    components.html(st.session_state.map_html, height=750, scrolling=False)
    if st.session_state.stations:
        st.subheader("🏆 Top 10 günstigste")
        for i, s in enumerate(
            sorted(st.session_state.stations.values(), key=lambda x: x["price"])[:10], 1
        ):
            brand = s.get("brand") or "–"
            place = s.get("place", "")
            dist  = f"{s['_dist_km']:.1f} km" if "_dist_km" in s else "–"
            st.write(f"**{i}.** `{s['price']:.3f} €` — {brand}, {place} · {dist}")
    st.stop()

# ── Normales Layout ───────────────────────────────────────────────────────────
st.title("⛽ Spritpreis-Heatmap")
st.caption("Tankstellen in deiner Umgebung – günstigste zuerst")

with st.sidebar:
    st.header("🔍 Suche")

    st.subheader("📍 GPS-Standort")
    components.html(GPS_HTML, height=80)

    lat_input = st.text_input(
        "Breitengrad (lat)",
        value=str(st.session_state.gps_lat) if st.session_state.gps_lat else "",
        placeholder="50.1234"
    )
    lon_input = st.text_input(
        "Längengrad (lon)",
        value=str(st.session_state.gps_lon) if st.session_state.gps_lon else "",
        placeholder="8.5678"
    )
    if st.button("✅ GPS übernehmen", use_container_width=True):
        try:
            st.session_state.gps_lat = float(lat_input)
            st.session_state.gps_lon = float(lon_input)
            st.success(f"✅ {st.session_state.gps_lat:.4f}°N, {st.session_state.gps_lon:.4f}°E")
        except ValueError:
            st.error("Ungültige Koordinaten.")

    if st.session_state.gps_lat:
        st.caption(f"✅ GPS aktiv: {st.session_state.gps_lat:.4f}, {st.session_state.gps_lon:.4f}")
        if st.button("🗑️ GPS zurücksetzen"):
            st.session_state.gps_lat = None
            st.session_state.gps_lon = None
            st.query_params.clear()
            st.rerun()

    st.divider()
    st.subheader("🏙️ Oder Ort eingeben")
    ort = st.text_input("Ort", placeholder="z.B. Frankfurt, Eschborn …")
    st.divider()

    radius     = st.slider("Radius (km)", min_value=1, max_value=50, value=15)
    kraftstoff = st.selectbox("Kraftstoff", ["diesel", "super", "e10"])
    st.divider()
    suchen = st.button("🗺️ Karte laden", use_container_width=True, type="primary")
    st.caption("Daten: Tankerkönig API · Karten: OSM")


# ── Suche ─────────────────────────────────────────────────────────────────────
if suchen:
    if st.session_state.gps_lat and st.session_state.gps_lon:
        lat, lon, name = st.session_state.gps_lat, st.session_state.gps_lon, "Mein Standort"
        err = None
    elif ort:
        result, err = geocode(ort)
        if result:
            lat, lon, name = result
        else:
            lat = lon = name = None
    else:
        err = "Bitte Ort eingeben oder GPS übernehmen."
        lat = None

    if lat is None:
        st.error(err)
    else:
        st.info(f"📍 {name} · {radius} km · {kraftstoff.upper()}")
        progress_bar = st.progress(0)
        status_text  = st.empty()

        def update_progress(pct, msg):
            progress_bar.progress(pct)
            status_text.text(msg)

        stations = fetch_by_location(lat, lon, radius, kraftstoff, update_progress)
        progress_bar.empty()
        status_text.empty()

        if not stations:
            st.warning("Keine Tankstellen gefunden. Radius vergrößern?")
        else:
            prices = [s["price"] for s in stations.values()]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Tankstellen",  len(stations))
            c2.metric("Günstigste",   f"{min(prices):.3f} €")
            c3.metric("Durchschnitt", f"{sum(prices)/len(prices):.3f} €")
            c4.metric("Teuerste",     f"{max(prices):.3f} €")

            st.session_state.stations = stations
            st.session_state.map_html = build_map_html(
                stations, kraftstoff, name,
                center=(lat, lon),
                zoom=radius_to_zoom(radius),
                search_radius=radius,
            )


# ── Karte anzeigen ────────────────────────────────────────────────────────────
if st.session_state.map_html:
    col_title, col_btn = st.columns([11, 1])
    with col_btn:
        if st.button("⛶", help="Vollbild", use_container_width=True):
            st.session_state.fullscreen = True
            st.rerun()

    # Karte: iframe-Höhe großzügig, innen füllt html+body+folium-map auf 100%
    components.html(st.session_state.map_html, height=500, scrolling=False)

    if st.session_state.stations:
        st.subheader("🏆 Top 10 günstigste")
        for i, s in enumerate(
            sorted(st.session_state.stations.values(), key=lambda x: x["price"])[:10], 1
        ):
            brand = s.get("brand") or "–"
            place = s.get("place", "")
            dist  = f"{s['_dist_km']:.1f} km" if "_dist_km" in s else "–"
            st.write(f"**{i}.** `{s['price']:.3f} €` — {brand}, {place} · {dist}")

else:
    st.info("👈 Ort eingeben oder GPS nutzen, dann **Karte laden** klicken.")
