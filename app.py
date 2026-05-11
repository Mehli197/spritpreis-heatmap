"""
app.py  –  Spritpreis-Heatmap  (Streamlit)
Alles in einer Datei, kein externer Import nötig.
Starten: streamlit run app.py
"""

import json
import math
import os
import time
from datetime import datetime
from pathlib import Path

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


def _slugify(text):
    replacements = {"ü":"ue","ö":"oe","ä":"ae","ß":"ss","Ü":"Ue","Ö":"Oe","Ä":"Ae"}
    for k, v in replacements.items():
        text = text.replace(k, v)
    text = text.lower().replace(" ", "_").replace("-", "_")
    return "".join(c if c.isalnum() or c == "_" else "" for c in text)


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
    hit = results[0]
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
        step_km  = MAX_RADIUS * 1.3
        step_lat = step_km / 111.0
        step_lon = step_km / (111.0 * math.cos(math.radians(lat)))
        call_radius = MAX_RADIUS
        grid = []
        d_lat = -radius_km / 111.0
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
            r = requests.get(BASE_URL, params=params, timeout=10)
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
    prices = [s["price"] for s in stations.values()]
    p_min, p_max = min(prices), max(prices)
    p_avg = sum(prices) / len(prices)

    m = folium.Map(location=list(center), zoom_start=zoom,
                   tiles=None, control_scale=True)
    folium.TileLayer("CartoDB positron",   name="Karte (hell)",   control=True).add_to(m)
    folium.TileLayer("CartoDB dark_matter", name="Karte (dunkel)", control=True).add_to(m)

    # Suchradius
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
        gradient={"0.0":"#00ff00","0.3":"#aaff00","0.5":"#ffff00",
                  "0.7":"#ffaa00","1.0":"#ff0000"},
        show=True,
    ).add_to(m)

    # Alle Marker
    marker_fg = folium.FeatureGroup(name="📍 Alle Tankstellen", show=False)
    for s in stations.values():
        color  = price_to_color(s["price"], p_min, p_max)
        brand  = s.get("brand") or "–"
        name   = s.get("name")  or brand
        street = f"{s.get('street','')} {s.get('houseNumber','')}".strip()
        place  = s.get("place", "")
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
    top_fg = folium.FeatureGroup(name=f"⭐ Top {actual_top} günstigste", show=True)
    cheapest = sorted(stations.values(), key=lambda x: x["price"])[:actual_top]
    for rank, s in enumerate(cheapest, 1):
        brand  = s.get("brand") or "–"
        name   = s.get("name")  or brand
        street = f"{s.get('street','')} {s.get('houseNumber','')}".strip()
        place  = s.get("place", "")
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

    # Legende
    ts_now = datetime.now().strftime("%d.%m.%Y %H:%M")
    radius_text = f"<br>Radius: {search_radius:.0f} km" if search_radius else ""
    legend = f"""<div style="position:fixed;bottom:35px;left:35px;z-index:9999;
        background:rgba(255,255,255,0.94);border-radius:10px;
        padding:14px 18px;box-shadow:0 2px 12px rgba(0,0,0,0.25);
        font-family:sans-serif;font-size:13px;line-height:1.8;max-width:240px">
        <b>⛽ {fuel_type.upper()} – {region_name}</b><br>
        <div style="height:10px;border-radius:5px;margin:6px 0;
            background:linear-gradient(to right,#00ff00,#aaff00,#ffff00,#ffaa00,#ff0000)"></div>
        <div style="display:flex;justify-content:space-between;font-size:11px;color:#666">
            <span>{p_min:.3f} €</span><span>Ø {p_avg:.3f} €</span><span>{p_max:.3f} €</span>
        </div>
        <hr style="margin:6px 0;border:none;border-top:1px solid #ddd">
        <small>{len(stations):,} Tankstellen{radius_text}<br>Stand: {ts_now}</small></div>"""
    m.get_root().html.add_child(folium.Element(legend))

    folium.LayerControl(collapsed=False).add_to(m)

    # Als HTML-String zurückgeben
    return m._repr_html_()


# ══════════════════════════════════════════════════════════════════════════════
#  STREAMLIT UI
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Spritpreis-Heatmap",
    page_icon="⛽",
    layout="wide",
)

st.title("⛽ Spritpreis-Heatmap")
st.caption("Tankstellen in deiner Umgebung – günstigste zuerst")

# Sidebar
with st.sidebar:
    st.header("🔍 Suche")
    ort        = st.text_input("Ort", placeholder="z.B. Frankfurt, Eschborn …")
    radius     = st.slider("Radius (km)", min_value=1, max_value=50, value=15)
    kraftstoff = st.selectbox("Kraftstoff", ["diesel", "super", "e10"])
    suchen     = st.button("🗺️ Karte laden", use_container_width=True)
    st.divider()
    st.caption("Daten: Tankerkönig API\nKarten: OpenStreetMap")

# Hauptbereich
if suchen and ort:
    result, err = geocode(ort)
    if err:
        st.error(err)
    else:
        lat, lon, name = result
        st.info(f"📍 {name} | Radius: {radius} km | {kraftstoff.upper()}")

        progress_bar  = st.progress(0)
        status_text   = st.empty()

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
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Tankstellen",  len(stations))
            col2.metric("Günstigste",   f"{min(prices):.3f} €")
            col3.metric("Durchschnitt", f"{sum(prices)/len(prices):.3f} €")
            col4.metric("Teuerste",     f"{max(prices):.3f} €")

            zoom     = radius_to_zoom(radius)
            map_html = build_map_html(stations, kraftstoff, name,
                                      center=(lat, lon), zoom=zoom,
                                      search_radius=radius)
            components.html(map_html, height=580, scrolling=False)

            st.subheader("🏆 Top 10 günstigste")
            cheapest = sorted(stations.values(), key=lambda x: x["price"])[:10]
            for i, s in enumerate(cheapest, 1):
                brand = s.get("brand") or "–"
                place = s.get("place", "")
                dist  = f"{s['_dist_km']:.1f} km" if "_dist_km" in s else "–"
                st.write(f"**{i}.** `{s['price']:.3f} €` — {brand}, {place} · {dist}")

elif suchen and not ort:
    st.warning("Bitte einen Ort eingeben.")
else:
    st.info("👈 Links einen Ort eingeben und auf **Karte laden** klicken.")