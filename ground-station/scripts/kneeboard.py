#!/usr/bin/env python3
"""
SkyBridge Kneeboard — Pilot-facing web app
Tablet-optimized moving map with ADS-B, weather, and VHF transcript overlays.
Designed for one-handed operation in the cockpit.
Serves on port 8083.
"""

import datetime
import json
import os
import re
import urllib.request
import urllib.error

from flask import Flask, jsonify, request

app = Flask(__name__)

TRANSCRIPT_DIR = "/mnt/nvme/skybridge/transcripts"
ADSB_JSON = "/run/readsb/aircraft.json"
METAR_CACHE = {"data": None, "ts": 0}
METAR_TTL = 300  # 5 min cache

# ADS-B: ADSB.fi statewide feed — two overlapping 250nm circles cover ~500nm
_ADSB_FI_CACHE = {"data": [], "ts": 0}
_ADSB_FI_TTL = 8  # seconds between fetches (respect rate limits)
_ADSB_FI_CIRCLES = [
    (61.17, -150.0, 250),   # Anchorage / Southcentral
    (63.5,  -150.0, 250),   # Fairbanks / Interior / North Slope overlap
]


def _fetch_adsbfi():
    """Fetch statewide ADS-B from ADSB.fi, merge with local readsb."""
    import time, threading
    now = time.time()
    if _ADSB_FI_CACHE["data"] and (now - _ADSB_FI_CACHE["ts"]) < _ADSB_FI_TTL:
        return _ADSB_FI_CACHE["data"]

    merged = {}

    # 1) Local readsb — freshest data, wins on conflicts
    try:
        with open(ADSB_JSON) as f:
            local = json.load(f)
        for ac in local.get("aircraft", []):
            if "lat" in ac and "lon" in ac:
                merged[ac["hex"]] = {
                    "hex": ac.get("hex", ""),
                    "flight": ac.get("flight", "").strip(),
                    "reg": ac.get("r", ""),
                    "type": ac.get("t", ""),
                    "desc": ac.get("desc", ""),
                    "ownOp": ac.get("ownOp", ""),
                    "lat": ac["lat"],
                    "lon": ac["lon"],
                    "alt": ac.get("alt_baro", ac.get("alt_geom", "")),
                    "gs": ac.get("gs", ""),
                    "track": ac.get("track", ""),
                    "squawk": ac.get("squawk", ""),
                    "seen": ac.get("seen", 999),
                    "src": "local",
                }
    except Exception:
        pass

    # 2) ADSB.fi — two circles covering ~500nm of Southcentral AK
    for lat, lon, dist in _ADSB_FI_CIRCLES:
        try:
            url = f"https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{dist}"
            req = urllib.request.Request(url, headers={"User-Agent": "SkyBridge/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            for ac in data.get("aircraft", []):
                hx = ac.get("hex", "")
                if not hx or "lat" not in ac or "lon" not in ac:
                    continue
                if hx in merged and merged[hx]["src"] == "local":
                    # Enrich local with ADSB.fi metadata but keep local position
                    if not merged[hx]["reg"] and ac.get("r"):
                        merged[hx]["reg"] = ac["r"]
                    if not merged[hx]["type"] and ac.get("t"):
                        merged[hx]["type"] = ac["t"]
                    if not merged[hx]["desc"] and ac.get("desc"):
                        merged[hx]["desc"] = ac["desc"]
                    if not merged[hx]["ownOp"] and ac.get("ownOp"):
                        merged[hx]["ownOp"] = ac["ownOp"]
                    continue
                if hx not in merged:
                    merged[hx] = {
                        "hex": hx,
                        "flight": ac.get("flight", "").strip(),
                        "reg": ac.get("r", ""),
                        "type": ac.get("t", ""),
                        "desc": ac.get("desc", ""),
                        "ownOp": ac.get("ownOp", ""),
                        "lat": ac["lat"],
                        "lon": ac["lon"],
                        "alt": ac.get("alt_baro", ac.get("alt_geom", "")),
                        "gs": ac.get("gs", ""),
                        "track": ac.get("track", ""),
                        "squawk": ac.get("squawk", ""),
                        "seen": ac.get("seen_pos", 999),
                        "src": "adsb.fi",
                    }
        except Exception as e:
            print(f"ADSB.fi fetch error ({lat},{lon},{dist}): {e}")

    result = list(merged.values())
    _ADSB_FI_CACHE["data"] = result
    _ADSB_FI_CACHE["ts"] = now
    return result


# ── API ──────────────────────────────────────────────────────────────────────

@app.route("/api/traffic")
def api_traffic():
    """Merged ADS-B: local readsb + ADSB.fi statewide."""
    aircraft = _fetch_adsbfi()
    local_ct = sum(1 for a in aircraft if a.get("src") == "local")
    return jsonify({
        "aircraft": aircraft,
        "total": len(aircraft),
        "local": local_ct,
        "adsbfi": len(aircraft) - local_ct,
    })


@app.route("/api/radio")
def api_radio():
    """Recent VHF transcripts — last N entries."""
    limit = int(request.args.get("limit", 20))
    today = datetime.date.today().isoformat()
    tfile = os.path.join(TRANSCRIPT_DIR, f"{today}.txt")
    entries = []
    if os.path.exists(tfile):
        with open(tfile) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                m = re.match(r"(\S+)\s+(?:\[([^\]]+)\]\s+)?(.*)", line)
                if m:
                    ts_str, freq, text = m.groups()
                    entries.append({
                        "ts": ts_str,
                        "freq": freq or "",
                        "text": text,
                    })
    # Return newest first, limited
    return jsonify(entries[-limit:][::-1])


@app.route("/api/weather")
def api_weather():
    """Fetch METAR/TAF for stations near pilot position."""
    stations = request.args.get("stations", "PANC,PAMR,PAED,PALH")
    import time
    now = time.time()
    cache_key = stations
    if METAR_CACHE["data"] and (now - METAR_CACHE["ts"]) < METAR_TTL:
        return jsonify(METAR_CACHE["data"])

    result = {"metars": {}, "tafs": {}}
    for stn in stations.split(","):
        stn = stn.strip().upper()
        # Fetch METAR from aviationweather.gov
        try:
            url = f"https://aviationweather.gov/api/data/metar?ids={stn}&format=json"
            req = urllib.request.Request(url, headers={"User-Agent": "SkyBridge/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                if data:
                    result["metars"][stn] = data[0].get("rawOb", "")
        except Exception:
            result["metars"][stn] = "(unavailable)"

        # Fetch TAF
        try:
            url = f"https://aviationweather.gov/api/data/taf?ids={stn}&format=json"
            req = urllib.request.Request(url, headers={"User-Agent": "SkyBridge/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                if data:
                    result["tafs"][stn] = data[0].get("rawTAF", "")
        except Exception:
            pass

    METAR_CACHE["data"] = result
    METAR_CACHE["ts"] = now
    return jsonify(result)


@app.route("/api/station")
def api_station():
    """Ground station status."""
    import subprocess
    services = {}
    for svc in ["vhf-pipeline", "openwebrx", "readsb", "dump978-fa"]:
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=3)
            services[svc] = r.stdout.strip()
        except Exception:
            services[svc] = "unknown"
    return jsonify({
        "hostname": "DOT-VHF",
        "services": services,
        "location": {"lat": 61.1744, "lon": -149.9964, "name": "PANC"},
    })


# ── WEATHER POLYGON APIs (proxied to avoid CORS) ────────────────────────────

_WX_CACHE = {}
_WX_TTL = 300  # 5 min cache

def _wx_fetch(url, cache_key, headers=None):
    """Cached fetch from weather/data APIs."""
    import time
    now = time.time()
    if cache_key in _WX_CACHE and (now - _WX_CACHE[cache_key]["ts"]) < _WX_TTL:
        return _WX_CACHE[cache_key]["data"]
    try:
        hdrs = {"User-Agent": "SkyBridge/1.0"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        _WX_CACHE[cache_key] = {"data": data, "ts": now}
        return data
    except Exception as e:
        return _WX_CACHE.get(cache_key, {}).get("data", [])


@app.route("/api/sigmets")
def api_sigmets():
    """SIGMETs and AIRMETs with polygon coordinates."""
    data = _wx_fetch(
        "https://aviationweather.gov/api/data/airsigmet?format=json&type=sigmet,airmet",
        "sigmets"
    )
    features = []
    for item in (data or []):
        coords = item.get("coords", "")
        if not coords:
            continue
        # Parse coords list of {lat, lon} dicts into polygon
        try:
            pts = []
            if isinstance(coords, list):
                for c in coords:
                    if isinstance(c, dict) and "lat" in c and "lon" in c:
                        pts.append([float(c["lat"]), float(c["lon"])])
            else:
                for pair in str(coords).strip().split(" "):
                    parts = pair.split(",")
                    if len(parts) == 2:
                        pts.append([float(parts[0]), float(parts[1])])
            if len(pts) >= 3:
                hazard = item.get("hazard", "")
                severity = item.get("severity", "")
                raw = item.get("rawAirSigmet", "")
                sig_type = "SIGMET" if "sigmet" in item.get("airSigmetType", "").lower() else "AIRMET"
                color = "#ff4444" if sig_type == "SIGMET" else "#ffaa00"
                if "ICE" in hazard.upper():
                    color = "#00ccff"
                elif "TURB" in hazard.upper():
                    color = "#ff8800"
                elif "IFR" in hazard.upper() or "MTN" in hazard.upper():
                    color = "#cc44cc"
                features.append({
                    "type": sig_type,
                    "hazard": hazard,
                    "severity": severity,
                    "raw": raw,
                    "color": color,
                    "polygon": pts,
                })
        except (ValueError, IndexError):
            continue
    return jsonify(features)


@app.route("/api/pireps")
def api_pireps():
    """PIREPs with location."""
    data = _wx_fetch(
        "https://aviationweather.gov/api/data/pirep?format=json&age=3&dist=300&loc=61.17,-149.99",
        "pireps"
    )
    pireps = []
    for p in (data or []):
        lat = p.get("lat")
        lon = p.get("lon")
        if lat is None or lon is None:
            continue
        pireps.append({
            "lat": lat,
            "lon": lon,
            "raw": p.get("rawOb", ""),
            "type": p.get("reportType", ""),
            "altitude": p.get("fltlvl", ""),
            "aircraft": p.get("acType", ""),
            "turbulence": p.get("tbFreq", ""),
            "icing": p.get("icgType", ""),
            "urgent": p.get("reportType", "") == "Urgent",
        })
    return jsonify(pireps)


@app.route("/api/mwos")
def api_mwos():
    """Live weather + cameras from Montis Corp MWOS stations."""
    MONTIS_KEY = "VESTUG2IIGDKKCDJFDQC6ZZAODETADWB"
    STATIONS = [
        {"id": 133, "name": "Lake Hood (PALH)", "icao": "PALH"},
        {"id": 1,   "name": "Merrill Field (PAMR)", "icao": "PAMR"},
        {"id": 265, "name": "Merrill Field 2 (PAMR)", "icao": "PAMR"},
        {"id": 529, "name": "Nuiqsut (PAQT)", "icao": "PAQT"},
        {"id": 430, "name": "Kaktovik", "icao": ""},
        {"id": 694, "name": "Port Graham", "icao": ""},
        {"id": 232, "name": "Port Townsend", "icao": ""},
    ]
    result = []
    for stn in STATIONS:
        try:
            data = _wx_fetch(
                f"https://api.montiscorp.com/mwos/{stn['id']}",
                f"mwos_{stn['id']}",
                headers={"authorization": MONTIS_KEY}
            )
            if not data:
                continue
            obs = data.get("observations", [])
            latest = obs[0] if obs else {}
            cams = data.get("cameras", [])
            # Pick the 4 cardinal camera URLs
            cam_urls = []
            for c in sorted(cams, key=lambda x: x.get("bearingDegrees", 0)):
                url = c.get("currentImageUrl", "")
                if url:
                    cam_urls.append({
                        "dir": c.get("directionText", ""),
                        "url": url,
                        "ts": c.get("currentImageObservationTime", ""),
                    })
            result.append({
                "id": stn["id"],
                "name": stn["name"],
                "icao": stn["icao"],
                "lat": data.get("latitude", 0),
                "lon": data.get("longitude", 0),
                "status": data.get("siteStatus", ""),
                "maintenance": data.get("maintenanceMessage", ""),
                "obs": {
                    "time": latest.get("observationTime", ""),
                    "tempC": round(latest.get("tempC", 0), 1),
                    "dewC": round(latest.get("dewpointC", 0), 1),
                    "humidity": round(latest.get("humidityPct", 0), 1),
                    "wind": latest.get("windsText", ""),
                    "windDir": latest.get("windDirDegrees", ""),
                    "windKt": round(latest.get("windSpeedKt", 0), 1),
                    "gustKt": round(latest.get("windGustKt", 0), 1),
                    "pressHpa": round(latest.get("pressureHpa", 0), 2),
                    "precip": latest.get("precipType", ""),
                    "raw": latest.get("rawText", ""),
                },
                "cameras": cam_urls,
            })
        except Exception as e:
            print(f"MWOS {stn['name']} error: {e}")
    return jsonify(result)


@app.route("/api/metarmap")
def api_metarmap():
    """METARs for Alaska stations with positions for map dots."""
    # Key Alaska stations
    stations = "PANC,PAMR,PAED,PALH,PAFA,PAJN,PABE,PAOM,PADQ,PABR,PAEN,PAHO,PAKN,PAMC,PAVD,PAAQ,PABI,PAEI,PADU,PAGA,PAIL,PAKT,PANN,PAOT,PASN,PATK,PAUN,PAWG,PFYU,PPIZ"
    data = _wx_fetch(
        f"https://aviationweather.gov/api/data/metar?ids={stations}&format=json",
        "metarmap"
    )
    result = []
    for m in (data or []):
        lat = m.get("lat")
        lon = m.get("lon")
        if lat is None or lon is None:
            continue
        raw = m.get("rawOb", "")
        # Determine flight category
        cat = m.get("fltcat", "VFR")
        color = {"VFR": "#00d4aa", "MVFR": "#0090ff", "IFR": "#ff4444",
                 "LIFR": "#ff00ff"}.get(cat, "#888888")
        result.append({
            "station": m.get("icaoId", ""),
            "lat": lat, "lon": lon,
            "raw": raw,
            "cat": cat,
            "color": color,
            "temp": m.get("temp", ""),
            "dewp": m.get("dewp", ""),
            "wdir": m.get("wdir", ""),
            "wspd": m.get("wspd", ""),
            "vis": m.get("visib", ""),
            "alt": m.get("altim", ""),
        })
    return jsonify(result)


@app.route("/api/gairmet")
def api_gairmet():
    """Graphical AIRMETs — IFR, mountain obscuration, turbulence, icing, freezing level."""
    data = _wx_fetch(
        "https://aviationweather.gov/api/data/gairmet?format=json",
        "gairmet"
    )
    features = []
    for g in (data or []):
        coords = g.get("coords", [])
        if not coords:
            continue
        try:
            pts = []
            if isinstance(coords, list):
                for c in coords:
                    if isinstance(c, dict) and "lat" in c and "lon" in c:
                        pts.append([float(c["lat"]), float(c["lon"])])
            if len(pts) < 3:
                continue
            hazard = g.get("hazard", "")
            product = g.get("product", "")
            severity = g.get("severity", "")
            fh = g.get("forecastHour", "")
            base = g.get("base", "")
            top = g.get("top", "")
            due_to = g.get("due_to", "")
            # Color by hazard type
            color = "#ffaa00"  # default amber
            if "IFR" in hazard.upper():
                color = "#ff4444"
            elif "MT_OBSC" in hazard.upper():
                color = "#cc44cc"
            elif "TURB" in hazard.upper():
                color = "#ff8800"
            elif "ICE" in hazard.upper():
                color = "#00ccff"
            elif "FZLVL" in hazard.upper():
                color = "#4488ff"
            elif "SFC_WIND" in hazard.upper():
                color = "#ffcc00"
            features.append({
                "hazard": hazard,
                "product": product,
                "severity": severity,
                "forecastHour": fh,
                "base": base,
                "top": top,
                "due_to": due_to,
                "color": color,
                "polygon": pts,
            })
        except (ValueError, IndexError):
            continue
    return jsonify(features)


@app.route("/api/volash")
def api_volash():
    """Volcanic ash SIGMETs — critical for Alaska aviation."""
    data = _wx_fetch(
        "https://aviationweather.gov/api/data/isigmet?format=json&hazard=VA",
        "volash"
    )
    features = []
    for v in (data or []):
        coords = v.get("coords", [])
        if not coords:
            continue
        try:
            pts = []
            if isinstance(coords, list):
                for c in coords:
                    if isinstance(c, dict) and "lat" in c and "lon" in c:
                        pts.append([float(c["lat"]), float(c["lon"])])
            if len(pts) < 3:
                continue
            features.append({
                "firName": v.get("firName", ""),
                "volcano": v.get("qualifier", ""),
                "hazard": "VOLCANIC ASH",
                "base": v.get("base", "SFC"),
                "top": v.get("top", ""),
                "movement": f"{v.get('dir','')} {v.get('spd','')}kt".strip(),
                "raw": v.get("rawSigmet", ""),
                "color": "#ff0000",
                "polygon": pts,
            })
        except (ValueError, IndexError):
            continue
    return jsonify(features)


@app.route("/api/nwsalerts")
def api_nwsalerts():
    """NWS active alerts for Alaska — winter storms, wind, freezing rain etc."""
    import time
    now = time.time()
    cache_key = "nwsalerts"
    if cache_key in _WX_CACHE and (now - _WX_CACHE[cache_key]["ts"]) < _WX_TTL:
        cached = _WX_CACHE[cache_key]["data"]
        return jsonify(cached)
    try:
        url = "https://api.weather.gov/alerts/active?area=AK"
        req = urllib.request.Request(url, headers={
            "User-Agent": "SkyBridge/1.0",
            "Accept": "application/geo+json",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        alerts = []
        for f in data.get("features", []):
            props = f.get("properties", {})
            geo = f.get("geometry")
            # Determine severity color
            sev = props.get("severity", "")
            color = "#ffaa00"  # default amber
            if sev == "Extreme":
                color = "#ff0000"
            elif sev == "Severe":
                color = "#ff4444"
            elif sev == "Moderate":
                color = "#ff8800"
            elif sev == "Minor":
                color = "#ffcc00"
            # Extract polygon if available
            polygon = []
            if geo and geo.get("type") == "Polygon":
                polygon = [[c[1], c[0]] for c in geo["coordinates"][0]]
            alert = {
                "event": props.get("event", ""),
                "headline": props.get("headline", ""),
                "severity": sev,
                "urgency": props.get("urgency", ""),
                "description": props.get("description", "")[:500],
                "instruction": props.get("instruction", "")[:300] if props.get("instruction") else "",
                "onset": props.get("onset", ""),
                "expires": props.get("expires", ""),
                "color": color,
                "polygon": polygon,
            }
            alerts.append(alert)
        _WX_CACHE[cache_key] = {"data": alerts, "ts": now}
        return jsonify(alerts)
    except Exception as e:
        print(f"NWS alerts error: {e}")
        return jsonify(_WX_CACHE.get(cache_key, {}).get("data", []))


# ── HTML ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return HTML_PAGE


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>SkyBridge Kneeboard</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {
    --bg: #0a0e14; --surface: rgba(11,15,21,0.92); --surface2: #1a2230;
    --border: #2a3545; --text: #d0d8e0; --text2: #6b7a8d;
    --green: #00d4aa; --blue: #0090ff; --amber: #ffaa00;
    --red: #ff4444; --magenta: #ff00ff; --cyan: #00ccff; --white: #fff;
  }
  * { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html, body { height:100%; overflow:hidden; }
  body {
    font-family: -apple-system, 'SF Pro Text', 'Helvetica Neue', sans-serif;
    background: var(--bg); color: var(--text);
    touch-action: manipulation;
  }

  /* ── LAYOUT ── */
  #map { position:absolute; top:0; left:0; right:0; bottom:0; z-index:1; }

  .top-bar {
    position:absolute; top:0; left:0; right:0; z-index:1000; height:44px;
    background: var(--surface);
    display:flex; align-items:center; padding:0 12px; gap:10px;
    backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    border-bottom:1px solid var(--border);
  }
  .top-bar .logo { font-size:15px; font-weight:800; color:var(--green); letter-spacing:1.5px; }
  .top-bar .sub { font-size:10px; color:var(--text2); text-transform:uppercase; letter-spacing:2px; }
  .gps-strip { margin-left:auto; display:flex; gap:14px; font-size:11px; color:var(--text2); }
  .gps-strip .v { color:var(--green); font-weight:700; font-size:12px; }

  /* FABs — left side, big targets */
  .fab-col {
    position:absolute; top:52px; left:8px; z-index:1000;
    display:flex; flex-direction:column; gap:6px;
  }
  .fab {
    width:48px; height:48px; border-radius:10px; border:1px solid var(--border);
    background:var(--surface); color:var(--text); font-size:11px; font-weight:700;
    cursor:pointer; display:flex; flex-direction:column; align-items:center;
    justify-content:center; gap:2px; backdrop-filter:blur(10px);
    transition:background 0.15s, transform 0.1s;
  }
  .fab:active { transform:scale(0.9); }
  .fab.on { background:var(--green); color:#000; border-color:var(--green); }
  .fab svg { width:18px; height:18px; }
  .fab .ft { font-size:7px; letter-spacing:0.5px; text-transform:uppercase; }

  /* Layer control panel */
  .layer-panel {
    position:absolute; top:52px; left:64px; z-index:1000;
    background:var(--surface); border:1px solid var(--border); border-radius:10px;
    padding:8px 0; min-width:200px;
    transform:scaleY(0); transform-origin:top left; transition:transform 0.2s;
  }
  .layer-panel.open { transform:scaleY(1); }
  .layer-row {
    display:flex; align-items:center; padding:10px 14px; gap:10px;
    cursor:pointer; font-size:12px;
  }
  .layer-row:active { background:var(--surface2); }
  .layer-row .dot { width:12px; height:12px; border-radius:3px; flex-shrink:0; }
  .layer-row .lname { flex:1; }
  .layer-row .tog { width:36px; height:20px; border-radius:10px; background:var(--surface2);
    position:relative; transition:background 0.2s; border:1px solid var(--border); }
  .layer-row .tog.on { background:var(--green); border-color:var(--green); }
  .layer-row .tog::after {
    content:''; position:absolute; top:2px; left:2px; width:14px; height:14px;
    border-radius:7px; background:var(--white); transition:left 0.2s;
  }
  .layer-row .tog.on::after { left:18px; }

  /* Radio panel — bottom */
  .radio-panel {
    position:absolute; bottom:0; left:0; right:0; z-index:1000;
    background:var(--surface); border-top:1px solid var(--border);
    backdrop-filter:blur(12px); transition:height 0.3s; height:130px;
    display:flex; flex-direction:column;
  }
  .radio-panel.expanded { height:50vh; }
  .radio-bar {
    display:flex; align-items:center; padding:6px 14px; min-height:32px;
    cursor:pointer; flex-shrink:0;
  }
  .radio-bar .rt { font-size:12px; font-weight:700; color:var(--amber);
    letter-spacing:1px; text-transform:uppercase; }
  .radio-bar .grip { width:40px; height:3px; background:var(--text2);
    border-radius:2px; margin:0 auto; }
  .radio-bar .badge { background:var(--amber); color:#000; font-size:9px;
    font-weight:800; padding:2px 7px; border-radius:8px; }
  .radio-log { flex:1; overflow-y:auto; padding:0 8px 8px; -webkit-overflow-scrolling:touch; }
  .rentry {
    padding:5px 8px; margin-bottom:3px; background:rgba(26,34,48,0.7);
    border-radius:5px; border-left:3px solid var(--amber);
  }
  .rentry .rm { font-size:9px; color:var(--text2); display:flex; gap:8px; }
  .rentry .rm .rf { color:var(--green); font-weight:600; }
  .rentry .rtx { font-size:12px; line-height:1.35; color:var(--text); }
  .cstag { display:inline-block; background:var(--blue); color:#fff;
    font-size:9px; font-weight:700; padding:1px 5px; border-radius:3px;
    margin-left:3px; vertical-align:middle; }

  /* Weather text panel — right slide */
  .wx-panel {
    position:absolute; top:44px; right:0; z-index:1000;
    width:320px; max-height:calc(100vh - 180px);
    background:var(--surface); border-left:1px solid var(--border);
    border-bottom:1px solid var(--border); border-radius:0 0 0 8px;
    transform:translateX(100%); transition:transform 0.3s;
    overflow-y:auto; -webkit-overflow-scrolling:touch;
  }
  .wx-panel.open { transform:translateX(0); }
  .wx-hdr { padding:10px 14px; font-size:12px; font-weight:700; color:var(--blue);
    text-transform:uppercase; letter-spacing:1px; border-bottom:1px solid var(--border);
    display:flex; justify-content:space-between; align-items:center; }
  .wx-hdr button { width:30px; height:30px; background:var(--surface2); border:none;
    color:var(--text); font-size:16px; border-radius:6px; cursor:pointer; }
  .mb { padding:8px 14px; border-bottom:1px solid var(--border); }
  .mb .ms { font-size:13px; font-weight:700; }
  .mb .mr { font-size:11px; color:var(--text); font-family:monospace; line-height:1.5;
    word-break:break-all; margin-top:3px; }
  .mb .mt { font-size:10px; color:var(--text2); font-family:monospace;
    line-height:1.3; margin-top:4px; word-break:break-all; }

  /* Dest overlay */
  .dest-ov {
    position:absolute; top:50%; left:50%; transform:translate(-50%,-50%);
    z-index:2000; background:var(--surface); border:1px solid var(--border);
    border-radius:12px; padding:20px; width:340px; max-width:90vw; display:none;
  }
  .dest-ov.open { display:block; }
  .dest-ov h3 { font-size:13px; color:var(--green); margin-bottom:10px;
    text-transform:uppercase; letter-spacing:1px; }
  .dest-ov input { width:100%; padding:12px; font-size:16px; border-radius:8px;
    border:1px solid var(--border); background:var(--surface2); color:var(--text);
    font-family:inherit; margin-bottom:8px; }
  .dest-ov .br { display:flex; gap:8px; }
  .dest-ov button { flex:1; padding:12px; font-size:14px; font-weight:600;
    border-radius:8px; border:none; cursor:pointer; font-family:inherit; }
  .dest-ov .bg { background:var(--green); color:#000; }
  .dest-ov .bc { background:var(--surface2); color:var(--text); border:1px solid var(--border); }

  /* Popups */
  .leaflet-popup-content-wrapper { background:rgba(11,15,21,0.95)!important;
    color:var(--text)!important; border:1px solid var(--border)!important; border-radius:8px!important; }
  .leaflet-popup-tip { background:rgba(11,15,21,0.95)!important; }
  .pop { font-size:11px; line-height:1.5; }
  .pop .cs { font-size:14px; font-weight:700; color:var(--green); }
  .pop .lbl { color:var(--text2); }

  .station-icon { background:var(--green); width:14px; height:14px; border-radius:50%;
    border:2px solid #fff; box-shadow:0 0 8px var(--green); }
  .mwos-icon { width:22px; height:22px; border-radius:4px; background:#ff6600;
    border:2px solid #fff; box-shadow:0 0 8px #ff6600; display:flex;
    align-items:center; justify-content:center; font-size:10px; font-weight:900; color:#fff; }
  .mwos-pop { font-size:11px; line-height:1.5; max-width:320px; }
  .mwos-pop .mh { font-size:14px; font-weight:700; color:#ff6600; margin-bottom:4px; }
  .mwos-pop .mobs { font-family:monospace; font-size:10px; background:rgba(26,34,48,0.8);
    padding:6px 8px; border-radius:4px; margin:4px 0; line-height:1.6; }
  .mwos-pop .mcams { display:grid; grid-template-columns:1fr 1fr; gap:4px; margin-top:6px; }
  .mwos-pop .mcams img { width:100%; border-radius:4px; cursor:pointer; }
  .mwos-pop .mcams .cdir { font-size:8px; color:var(--text2); text-align:center;
    text-transform:uppercase; letter-spacing:1px; }

  /* Map: no filter on satellite tiles */
  .leaflet-control-zoom a { background:var(--surface)!important; color:var(--text)!important;
    border-color:var(--border)!important; width:36px!important; height:36px!important;
    line-height:36px!important; font-size:16px!important; }
  .leaflet-control-attribution { display:none!important; }
</style>
</head>
<body>

<div class="top-bar">
  <span class="logo">SKYBRIDGE</span>
  <span class="sub">Kneeboard</span>
  <div class="gps-strip">
    <span id="gpsStatus">GPS: acquiring</span>
    <span>GS: <span class="v" id="gsSpd">--</span>kt</span>
    <span>HDG: <span class="v" id="gsHdg">---</span></span>
    <span>ALT: <span class="v" id="gsAlt">----</span>ft</span>
    <span>TFC: <span class="v" id="tfcCt">0</span></span>
  </div>
</div>

<div class="fab-col">
  <button class="fab" id="fGps" onclick="centerOnPilot()">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></svg>
    <span class="ft">GPS</span>
  </button>
  <button class="fab" onclick="toggleDest()">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z"/><circle cx="12" cy="10" r="3"/></svg>
    <span class="ft">DEST</span>
  </button>
  <button class="fab" id="fLyr" onclick="toggleLayerPanel()">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
    <span class="ft">LYRS</span>
  </button>
  <button class="fab" onclick="toggleWx()">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 15h18M3 19h18M5 11a7 7 0 0114 0"/></svg>
    <span class="ft">WX</span>
  </button>
</div>

<!-- Layer Control Panel -->
<div class="layer-panel" id="layerPanel">
  <div class="layer-row" onclick="toggleLayer('sat')">
    <div class="dot" style="background:#555"></div>
    <span class="lname">L1 Satellite</span>
    <div class="tog on" id="togSat"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('sect')">
    <div class="dot" style="background:#8b6914"></div>
    <span class="lname">L2 VFR Sectional</span>
    <div class="tog" id="togSect"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('radar')">
    <div class="dot" style="background:#00cc44"></div>
    <span class="lname">L3 NEXRAD Radar</span>
    <div class="tog" id="togRadar"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('metar')">
    <div class="dot" style="background:var(--green)"></div>
    <span class="lname">L4 METAR Stations</span>
    <div class="tog on" id="togMetar"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('sigmet')">
    <div class="dot" style="background:var(--red)"></div>
    <span class="lname">L5 SIGMETs/AIRMETs</span>
    <div class="tog on" id="togSigmet"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('pirep')">
    <div class="dot" style="background:var(--cyan)"></div>
    <span class="lname">L6 PIREPs</span>
    <div class="tog on" id="togPirep"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('traffic')">
    <div class="dot" style="background:var(--amber)"></div>
    <span class="lname">L7 ADS-B Traffic</span>
    <div class="tog on" id="togTraffic"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('radio')">
    <div class="dot" style="background:var(--amber)"></div>
    <span class="lname">L8 VHF Radio</span>
    <div class="tog on" id="togRadio"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('mwos')">
    <div class="dot" style="background:#ff6600"></div>
    <span class="lname">L9 MWOS Stations</span>
    <div class="tog on" id="togMwos"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('gairmet')">
    <div class="dot" style="background:#ff8800"></div>
    <span class="lname">L10 G-AIRMETs</span>
    <div class="tog on" id="togGairmet"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('volash')">
    <div class="dot" style="background:#ff0000"></div>
    <span class="lname">L11 Volcanic Ash</span>
    <div class="tog on" id="togVolash"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('nwsalerts')">
    <div class="dot" style="background:#ffcc00"></div>
    <span class="lname">L12 NWS Alerts</span>
    <div class="tog on" id="togNwsalerts"></div>
  </div>
</div>

<div class="wx-panel" id="wxPanel">
  <div class="wx-hdr">Weather<button onclick="toggleWx()">&times;</button></div>
  <div id="wxContent"><div class="mb"><span class="ms">Loading...</span></div></div>
</div>

<div class="dest-ov" id="destOv">
  <h3>Set Destination</h3>
  <input id="destIn" type="text" placeholder="ICAO (PAFA) or lat,lon" autocomplete="off" autocapitalize="characters">
  <div class="br">
    <button class="bc" onclick="toggleDest()">Cancel</button>
    <button class="bg" onclick="setDest()">Go Direct</button>
  </div>
</div>

<div class="radio-panel" id="radioPanel">
  <div class="radio-bar" onclick="document.getElementById('radioPanel').classList.toggle('expanded')">
    <span class="rt">VHF RADIO</span>
    <div class="grip"></div>
    <span class="badge" id="rBadge">0</span>
  </div>
  <div class="radio-log" id="radioLog"></div>
</div>

<div id="map"></div>

<script>
// ═══════════════════════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════════════════════
let map, pilotMarker, pilotPos=null, pilotHdg=0, followPilot=true;
let destMarker=null, destLine=null;

// Layer groups
let layers = {
  sat: null, sect: null, radar: null,
  metar: null, sigmet: null, pirep: null, traffic: null, radio: true,
  mwos: null, gairmet: null, volash: null, nwsalerts: null
};
let layerGroups = {};
let trafficMarkers = {};

const AIRPORTS = {
  'PANC':[61.1744,-149.9964],'PAFA':[64.8151,-147.8564],'PAJN':[58.3547,-134.5762],
  'PAMR':[61.2136,-149.8442],'PAED':[61.2510,-149.8063],'PALH':[61.1860,-150.0390],
  'PABE':[60.7798,-161.8380],'PAOM':[64.5122,-165.4453],'PADQ':[57.7500,-152.4939],
  'PABR':[71.2854,-156.7660],'PAEN':[60.5731,-151.2450],'PAHO':[59.6456,-151.4770],
  'PAKN':[58.6768,-156.6492],'PAMC':[62.9530,-155.6060],'PAVD':[61.1340,-146.2486],
  'PAAQ':[61.5949,-149.0887],'PABI':[64.5138,-165.4407],'PAEI':[64.6655,-147.1013],
  'PAKT':[55.3556,-131.7139],'PANN':[60.0438,-161.9778],'PAOT':[66.8847,-162.5985],
  'PASN':[57.1671,-170.2205],'PATK':[62.3205,-150.0937],'PAUN':[63.8884,-160.7989],
};

// ═══════════════════════════════════════════════════════════════════
// MAP INIT — 8 LAYERS
// ═══════════════════════════════════════════════════════════════════
function initMap() {
  map = L.map('map', { center:[61.17,-149.99], zoom:8, zoomControl:true, attributionControl:false });

  // L1: ESRI World Imagery (Satellite)
  layers.sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom:18, attribution:'ESRI'
  }).addTo(map);

  // L2: VFR Sectional Chart (FAA via ArcGIS)
  layers.sect = L.tileLayer('https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/US_VFR_Sectional_Charts/MapServer/tile/{z}/{y}/{x}', {
    maxZoom:12, opacity:0.55
  });
  // Off by default

  // L3: NEXRAD Radar (Iowa State Mesonet)
  layers.radar = L.tileLayer('https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png', {
    maxZoom:12, opacity:0.5, attribution:'IEM'
  });
  // Off by default

  // L4-L7: Layer groups for dynamic data
  layerGroups.metar = L.layerGroup().addTo(map);
  layerGroups.sigmet = L.layerGroup().addTo(map);
  layerGroups.pirep = L.layerGroup().addTo(map);
  layerGroups.traffic = L.layerGroup().addTo(map);
  layerGroups.mwos = L.layerGroup().addTo(map);
  layerGroups.gairmet = L.layerGroup().addTo(map);
  layerGroups.volash = L.layerGroup().addTo(map);
  layerGroups.nwsalerts = L.layerGroup().addTo(map);

  // Ground station
  const sIcon = L.divIcon({className:'station-icon',iconSize:[14,14],iconAnchor:[7,7]});
  L.marker([61.1744,-149.9964],{icon:sIcon})
    .bindPopup('<div class="pop"><div class="cs">DOT-VHF</div>Ground Station<br>PANC / Ted Stevens</div>')
    .addTo(map);

  // GPS
  if (navigator.geolocation) {
    navigator.geolocation.watchPosition(onGPS, onGPSErr,
      {enableHighAccuracy:true, maximumAge:2000, timeout:10000});
  }
  map.on('dragstart', ()=>{ followPilot=false; });
}

// ═══════════════════════════════════════════════════════════════════
// LAYER TOGGLES
// ═══════════════════════════════════════════════════════════════════
function toggleLayerPanel() {
  document.getElementById('layerPanel').classList.toggle('open');
}

function toggleLayer(name) {
  const tog = document.getElementById('tog'+name.charAt(0).toUpperCase()+name.slice(1));
  const isOn = tog.classList.toggle('on');

  if (name === 'sat') {
    isOn ? layers.sat.addTo(map) : map.removeLayer(layers.sat);
  } else if (name === 'sect') {
    isOn ? layers.sect.addTo(map) : map.removeLayer(layers.sect);
  } else if (name === 'radar') {
    isOn ? layers.radar.addTo(map) : map.removeLayer(layers.radar);
  } else if (name === 'metar') {
    isOn ? layerGroups.metar.addTo(map) : map.removeLayer(layerGroups.metar);
  } else if (name === 'sigmet') {
    isOn ? layerGroups.sigmet.addTo(map) : map.removeLayer(layerGroups.sigmet);
  } else if (name === 'pirep') {
    isOn ? layerGroups.pirep.addTo(map) : map.removeLayer(layerGroups.pirep);
  } else if (name === 'traffic') {
    isOn ? layerGroups.traffic.addTo(map) : map.removeLayer(layerGroups.traffic);
  } else if (name === 'mwos') {
    isOn ? layerGroups.mwos.addTo(map) : map.removeLayer(layerGroups.mwos);
  } else if (name === 'gairmet') {
    isOn ? layerGroups.gairmet.addTo(map) : map.removeLayer(layerGroups.gairmet);
  } else if (name === 'volash') {
    isOn ? layerGroups.volash.addTo(map) : map.removeLayer(layerGroups.volash);
  } else if (name === 'nwsalerts') {
    isOn ? layerGroups.nwsalerts.addTo(map) : map.removeLayer(layerGroups.nwsalerts);
  } else if (name === 'radio') {
    layers.radio = isOn;
    document.getElementById('radioPanel').style.display = isOn ? '' : 'none';
  }
}

// ═══════════════════════════════════════════════════════════════════
// GPS
// ═══════════════════════════════════════════════════════════════════
function onGPS(pos) {
  pilotPos = [pos.coords.latitude, pos.coords.longitude];
  const spd = pos.coords.speed ? (pos.coords.speed*1.944).toFixed(0) : '--';
  const hdg = pos.coords.heading ? pos.coords.heading.toFixed(0).padStart(3,'0') : '---';
  const alt = pos.coords.altitude ? (pos.coords.altitude*3.281).toFixed(0) : '----';
  document.getElementById('gpsStatus').innerHTML='GPS: <span class="v">LOCK</span>';
  document.getElementById('gsSpd').textContent=spd;
  document.getElementById('gsHdg').textContent=hdg;
  document.getElementById('gsAlt').textContent=alt;
  if (pos.coords.heading) pilotHdg=pos.coords.heading;

  if (!pilotMarker) {
    pilotMarker = L.marker(pilotPos, {
      icon: L.divIcon({ className:'',
        html:`<div id="pIcon" style="width:0;height:0;border-left:10px solid transparent;border-right:10px solid transparent;border-bottom:24px solid #00d4aa;filter:drop-shadow(0 0 6px #00d4aa);transform:rotate(0deg)"></div>`,
        iconSize:[20,24], iconAnchor:[10,12] }),
      zIndexOffset:1000
    }).addTo(map);
  } else { pilotMarker.setLatLng(pilotPos); }
  const ic=document.getElementById('pIcon');
  if(ic) ic.style.transform=`rotate(${pilotHdg}deg)`;
  if(destMarker&&destLine) destLine.setLatLngs([pilotPos,destMarker.getLatLng()]);
  if(followPilot) map.panTo(pilotPos);
}
function onGPSErr(e){document.getElementById('gpsStatus').innerHTML='GPS: <span style="color:var(--red)">NO FIX</span>';}
function centerOnPilot(){
  followPilot=true;
  const b=document.getElementById('fGps'); b.classList.add('on');
  if(pilotPos) map.setView(pilotPos,map.getZoom());
  setTimeout(()=>b.classList.remove('on'),1000);
}

// ═══════════════════════════════════════════════════════════════════
// DESTINATION
// ═══════════════════════════════════════════════════════════════════
function toggleDest(){document.getElementById('destOv').classList.toggle('open');
  if(document.getElementById('destOv').classList.contains('open')) document.getElementById('destIn').focus();}
function setDest(){
  const v=document.getElementById('destIn').value.trim().toUpperCase();
  if(!v) return;
  let lat,lon,label;
  if(AIRPORTS[v]){[lat,lon]=AIRPORTS[v];label=v;}
  else{const p=v.split(/[,\s]+/);if(p.length===2){lat=parseFloat(p[0]);lon=parseFloat(p[1]);label=`${lat.toFixed(2)},${lon.toFixed(2)}`;}
  else{alert('Unknown airport. Use ICAO code or lat,lon.');return;}}
  if(destMarker)map.removeLayer(destMarker);
  if(destLine)map.removeLayer(destLine);
  destMarker=L.marker([lat,lon],{icon:L.divIcon({className:'',
    html:`<div style="width:20px;height:20px;background:var(--blue);border:2px solid #fff;border-radius:50%;box-shadow:0 0 10px var(--blue);display:flex;align-items:center;justify-content:center;font-size:10px;color:#fff;font-weight:800">D</div>`,
    iconSize:[20,20],iconAnchor:[10,10]})}).addTo(map);
  const start=pilotPos||[61.17,-149.99];
  destLine=L.polyline([start,[lat,lon]],{color:'#0090ff',weight:2,dashArray:'8,6',opacity:0.8}).addTo(map);
  const d=map.distance(start,[lat,lon])/1852;
  destMarker.bindPopup(`<div class="pop"><div class="cs">${label}</div>Destination<br><b>${d.toFixed(1)} NM</b></div>`);
  toggleDest();
  map.fitBounds([start,[lat,lon]],{padding:[80,80]});
}

// ═══════════════════════════════════════════════════════════════════
// L4: METAR STATIONS (colored dots on map)
// ═══════════════════════════════════════════════════════════════════
async function loadMetarMap(){
  try{
    const r=await fetch('/api/metarmap'); const data=await r.json();
    layerGroups.metar.clearLayers();
    data.forEach(m=>{
      const c=L.circleMarker([m.lat,m.lon],{radius:7,fillColor:m.color,color:'#fff',
        weight:1.5,fillOpacity:0.9}).bindPopup(
        `<div class="pop"><div class="cs" style="color:${m.color}">${m.station} — ${m.cat}</div>
        <div style="font-family:monospace;font-size:10px;line-height:1.5">${esc(m.raw)}</div>
        ${m.temp!==''?`<div><span class="lbl">Temp:</span> ${m.temp}C <span class="lbl">Dew:</span> ${m.dewp}C</div>`:''}
        ${m.wdir?`<div><span class="lbl">Wind:</span> ${m.wdir}@${m.wspd}kt</div>`:''}
        ${m.vis?`<div><span class="lbl">Vis:</span> ${m.vis}SM <span class="lbl">Alt:</span> ${m.alt}</div>`:''}
        </div>`);
      layerGroups.metar.addLayer(c);
    });
  }catch(e){console.error('METAR map error:',e);}
}

// ═══════════════════════════════════════════════════════════════════
// L5: SIGMETs / AIRMETs (polygons)
// ═══════════════════════════════════════════════════════════════════
async function loadSigmets(){
  try{
    const r=await fetch('/api/sigmets'); const data=await r.json();
    layerGroups.sigmet.clearLayers();
    data.forEach(s=>{
      const poly=L.polygon(s.polygon,{color:s.color,fillColor:s.color,fillOpacity:0.15,weight:2,dashArray:s.type==='SIGMET'?'':'6,4'})
        .bindPopup(`<div class="pop"><div class="cs" style="color:${s.color}">${s.type}: ${s.hazard}</div>
        <div style="font-size:10px;color:var(--text2)">${s.severity}</div>
        <div style="font-family:monospace;font-size:9px;line-height:1.4;margin-top:4px">${esc(s.raw)}</div></div>`);
      layerGroups.sigmet.addLayer(poly);
    });
  }catch(e){console.error('SIGMET error:',e);}
}

// ═══════════════════════════════════════════════════════════════════
// L6: PIREPs (point markers)
// ═══════════════════════════════════════════════════════════════════
async function loadPireps(){
  try{
    const r=await fetch('/api/pireps'); const data=await r.json();
    layerGroups.pirep.clearLayers();
    data.forEach(p=>{
      const color=p.urgent?'#ff4444':'#00ccff';
      const icon=L.divIcon({className:'',
        html:`<div style="width:10px;height:10px;background:${color};border:1.5px solid #fff;border-radius:2px;box-shadow:0 0 4px ${color}"></div>`,
        iconSize:[10,10],iconAnchor:[5,5]});
      const m=L.marker([p.lat,p.lon],{icon}).bindPopup(
        `<div class="pop"><div class="cs" style="color:${color}">${p.urgent?'URGENT ':''}PIREP</div>
        ${p.altitude?`<div><span class="lbl">FL:</span> ${p.altitude}</div>`:''}
        ${p.aircraft?`<div><span class="lbl">AC:</span> ${p.aircraft}</div>`:''}
        ${p.turbulence?`<div><span class="lbl">Turb:</span> ${p.turbulence}</div>`:''}
        ${p.icing?`<div><span class="lbl">Ice:</span> ${p.icing}</div>`:''}
        <div style="font-family:monospace;font-size:9px;margin-top:4px;line-height:1.3">${esc(p.raw)}</div></div>`);
      layerGroups.pirep.addLayer(m);
    });
  }catch(e){console.error('PIREP error:',e);}
}

// ═══════════════════════════════════════════════════════════════════
// L7: ADS-B TRAFFIC
// ═══════════════════════════════════════════════════════════════════
function updateTraffic(data){
  const aircraft=data.aircraft||[];
  const seen=new Set();
  document.getElementById('tfcCt').textContent=aircraft.length;
  aircraft.forEach(ac=>{
    const id=ac.hex; seen.add(id);
    const label=ac.flight||ac.reg||id;
    const rot=ac.track||0;
    const isLocal=ac.src==='local';
    const color=ac.alt&&ac.alt>18000?'#ff4444':ac.alt&&ac.alt>10000?'#0090ff':'#ffaa00';
    const srcBadge=isLocal?'<span style="color:var(--green);font-size:9px;font-weight:700">LOCAL</span>':'<span style="color:var(--text2);font-size:9px">ADSB.fi</span>';
    const pop=`<div class="pop"><div class="cs">${label}</div>${srcBadge}
      ${ac.reg?`<div><span class="lbl">Reg:</span> ${ac.reg}</div>`:''}
      ${ac.desc?`<div><span class="lbl">A/C:</span> ${ac.desc}</div>`:''}
      ${ac.ownOp?`<div><span class="lbl">Op:</span> ${ac.ownOp}</div>`:''}
      ${ac.alt?`<div><span class="lbl">Alt:</span> ${ac.alt}ft</div>`:''}
      ${ac.gs?`<div><span class="lbl">Spd:</span> ${Math.round(ac.gs)}kt <span class="lbl">Hdg:</span> ${Math.round(ac.track||0)}&deg;</div>`:''}
      ${ac.squawk?`<div><span class="lbl">Sqk:</span> ${ac.squawk}</div>`:''}
      ${ac.type?`<div><span class="lbl">Type:</span> ${ac.type}</div>`:''}
    </div>`;
    if(trafficMarkers[id]){trafficMarkers[id].setLatLng([ac.lat,ac.lon]).setPopupContent(pop);}
    else{
      const icon=L.divIcon({className:'',
        html:`<div style="position:relative"><div style="width:0;height:0;border-left:6px solid transparent;border-right:6px solid transparent;border-bottom:16px solid ${color};transform:rotate(${rot}deg);filter:drop-shadow(0 0 3px ${color})"></div><div style="position:absolute;top:18px;left:50%;transform:translateX(-50%);font-size:8px;color:${color};white-space:nowrap;font-weight:700;text-shadow:0 0 3px #000">${label}</div></div>`,
        iconSize:[12,16],iconAnchor:[6,8]});
      trafficMarkers[id]=L.marker([ac.lat,ac.lon],{icon}).bindPopup(pop);
      layerGroups.traffic.addLayer(trafficMarkers[id]);
    }
  });
  Object.keys(trafficMarkers).forEach(id=>{if(!seen.has(id)){layerGroups.traffic.removeLayer(trafficMarkers[id]);delete trafficMarkers[id];}});
}

// ═══════════════════════════════════════════════════════════════════
// L8: VHF RADIO LOG
// ═══════════════════════════════════════════════════════════════════
async function loadRadio(){
  try{
    const r=await fetch('/api/radio?limit=30'); const data=await r.json();
    document.getElementById('rBadge').textContent=data.length;
    const log=document.getElementById('radioLog');
    log.innerHTML=data.map(e=>{
      const t=e.ts.split('T')[1]?.substring(0,8)||e.ts;
      let tx=esc(e.text);
      tx=tx.replace(/\b(N\d{1,5}[A-Z]{0,2})\b/g,'<span class="cstag">$1</span>');
      tx=tx.replace(/\b(Alaska|United|FedEx|UPS|Polar|Atlas)\s+(\d{1,4})\b/gi,'$1 $2 <span class="cstag">$1 $2</span>');
      return`<div class="rentry"><div class="rm"><span>${t}Z</span><span class="rf">${e.freq}</span></div><div class="rtx">${tx}</div></div>`;
    }).join('');
  }catch(e){}
}

// ═══════════════════════════════════════════════════════════════════
// WEATHER TEXT PANEL
// ═══════════════════════════════════════════════════════════════════
function toggleWx(){document.getElementById('wxPanel').classList.toggle('open');
  if(document.getElementById('wxPanel').classList.contains('open'))loadWxText();}
async function loadWxText(){
  try{const r=await fetch('/api/weather');const d=await r.json();
    let h='';
    for(const[stn,raw] of Object.entries(d.metars)){
      const taf=d.tafs[stn]||'';
      let cat='VFR',cc='var(--green)';
      const cm=raw.match(/(?:OVC|BKN)(\d{3})/);
      if(cm){const c=parseInt(cm[1])*100;if(c<500){cat='LIFR';cc='var(--magenta)';}else if(c<1000){cat='IFR';cc='var(--red)';}else if(c<3000){cat='MVFR';cc='var(--blue)';}}
      const vm=raw.match(/\s(\d+)SM/);if(vm&&parseInt(vm[1])<3){cat='IFR';cc='var(--red)';}if(vm&&parseInt(vm[1])<1){cat='LIFR';cc='var(--magenta)';}
      h+=`<div class="mb"><span class="ms" style="color:${cc}">${stn} — ${cat}</span><div class="mr">${esc(raw)}</div>${taf?`<div class="mt">${esc(taf)}</div>`:''}</div>`;
    }
    document.getElementById('wxContent').innerHTML=h||'<div class="mb"><span class="ms">No data</span></div>';
  }catch(e){}
}

// ═══════════════════════════════════════════════════════════════════
// L9: MWOS WEATHER STATIONS (Montis Corp)
// ═══════════════════════════════════════════════════════════════════
async function loadMWOS(){
  try{
    const r=await fetch('/api/mwos'); const data=await r.json();
    layerGroups.mwos.clearLayers();
    data.forEach(s=>{
      const icon=L.divIcon({className:'',
        html:`<div class="mwos-icon">WX</div>`,
        iconSize:[22,22],iconAnchor:[11,11]});
      const o=s.obs;
      const tempF=o.tempC?(o.tempC*9/5+32).toFixed(1):'--';
      const dewF=o.dewC?(o.dewC*9/5+32).toFixed(1):'--';
      let camHtml='';
      if(s.cameras&&s.cameras.length){
        camHtml='<div class="mcams">';
        s.cameras.forEach(c=>{
          camHtml+=`<div><img src="${c.url}" alt="${c.dir}" onclick="window.open('${c.url}','_blank')"/><div class="cdir">${c.dir}</div></div>`;
        });
        camHtml+='</div>';
      }
      const pop=`<div class="mwos-pop">
        <div class="mh">${s.name}</div>
        <div style="font-size:10px;color:var(--text2)">MWOS Live &mdash; ${o.time?new Date(o.time).toLocaleTimeString():'no data'}</div>
        <div class="mobs">
          Temp: ${tempF}&deg;F (${o.tempC}&deg;C)<br>
          Dew: ${dewF}&deg;F (${o.dewC}&deg;C)<br>
          Humidity: ${o.humidity}%<br>
          Wind: ${o.windKt} kts from ${Math.round(o.windDir)}&deg; (gusts ${o.gustKt} kts)<br>
          Pressure: ${o.pressHpa} inHg<br>
          Precip: ${o.precip}
        </div>
        <div style="font-family:monospace;font-size:9px;color:var(--text2);margin:4px 0">${esc(o.raw)}</div>
        ${camHtml}
      </div>`;
      const m=L.marker([s.lat,s.lon],{icon,zIndexOffset:500}).bindPopup(pop,{maxWidth:340,minWidth:280});
      layerGroups.mwos.addLayer(m);
    });
  }catch(e){console.error('MWOS error:',e);}
}

// ═══════════════════════════════════════════════════════════════════
// L10: G-AIRMETs (graphical AIRMETs)
// ═══════════════════════════════════════════════════════════════════
async function loadGairmet(){
  try{
    const r=await fetch('/api/gairmet'); const data=await r.json();
    layerGroups.gairmet.clearLayers();
    data.forEach(g=>{
      const poly=L.polygon(g.polygon,{color:g.color,fillColor:g.color,fillOpacity:0.12,weight:1.5,dashArray:'4,4'})
        .bindPopup(`<div class="pop"><div class="cs" style="color:${g.color}">G-AIRMET: ${g.hazard}</div>
        <div><span class="lbl">Product:</span> ${g.product} <span class="lbl">FH:</span> +${g.forecastHour}h</div>
        ${g.severity?`<div><span class="lbl">Severity:</span> ${g.severity}</div>`:''}
        ${g.due_to?`<div><span class="lbl">Due to:</span> ${g.due_to}</div>`:''}
        ${g.base||g.top?`<div><span class="lbl">Base:</span> ${g.base||'SFC'} <span class="lbl">Top:</span> ${g.top||'--'}</div>`:''}
        </div>`);
      layerGroups.gairmet.addLayer(poly);
    });
  }catch(e){console.error('G-AIRMET error:',e);}
}

// ═══════════════════════════════════════════════════════════════════
// L11: VOLCANIC ASH SIGMETs
// ═══════════════════════════════════════════════════════════════════
async function loadVolash(){
  try{
    const r=await fetch('/api/volash'); const data=await r.json();
    layerGroups.volash.clearLayers();
    data.forEach(v=>{
      const poly=L.polygon(v.polygon,{color:'#ff0000',fillColor:'#ff0000',fillOpacity:0.25,weight:3})
        .bindPopup(`<div class="pop"><div class="cs" style="color:#ff0000;font-size:16px">⚠ VOLCANIC ASH</div>
        ${v.volcano?`<div style="font-size:13px;font-weight:700">${v.volcano}</div>`:''}
        <div><span class="lbl">FIR:</span> ${v.firName}</div>
        ${v.base||v.top?`<div><span class="lbl">Base:</span> ${v.base||'SFC'} <span class="lbl">Top:</span> ${v.top||'--'}</div>`:''}
        ${v.movement?`<div><span class="lbl">Movement:</span> ${v.movement}</div>`:''}
        <div style="font-family:monospace;font-size:9px;margin-top:4px;line-height:1.3">${esc(v.raw)}</div></div>`);
      layerGroups.volash.addLayer(poly);
    });
  }catch(e){console.error('Volcanic ash error:',e);}
}

// ═══════════════════════════════════════════════════════════════════
// L12: NWS ALERTS
// ═══════════════════════════════════════════════════════════════════
async function loadNwsAlerts(){
  try{
    const r=await fetch('/api/nwsalerts'); const data=await r.json();
    layerGroups.nwsalerts.clearLayers();
    data.forEach(a=>{
      if(a.polygon&&a.polygon.length>=3){
        const poly=L.polygon(a.polygon,{color:a.color,fillColor:a.color,fillOpacity:0.15,weight:2})
          .bindPopup(`<div class="pop"><div class="cs" style="color:${a.color}">${a.event}</div>
          <div style="font-size:10px;color:var(--text2)">${a.severity} / ${a.urgency}</div>
          <div style="font-size:11px;margin-top:4px;line-height:1.4">${esc(a.headline)}</div>
          <div style="font-size:10px;margin-top:4px;line-height:1.3;max-height:150px;overflow-y:auto">${esc(a.description).substring(0,300)}</div>
          ${a.instruction?`<div style="font-size:10px;color:var(--amber);margin-top:4px">${esc(a.instruction).substring(0,200)}</div>`:''}
          </div>`,{maxWidth:320});
        layerGroups.nwsalerts.addLayer(poly);
      } else {
        // No polygon — just show as a text entry (zone-based alert)
        // We'll skip these for now since they don't have map coordinates
      }
    });
  }catch(e){console.error('NWS alerts error:',e);}
}

// ═══════════════════════════════════════════════════════════════════
// UTILS
// ═══════════════════════════════════════════════════════════════════
function esc(s){return s?s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'):'';}

// ═══════════════════════════════════════════════════════════════════
// POLLING
// ═══════════════════════════════════════════════════════════════════
async function pollFast(){
  try{const r=await fetch('/api/traffic');const d=await r.json();updateTraffic(d);}catch(e){}
  loadRadio();
}
async function pollSlow(){
  loadMetarMap(); loadSigmets(); loadPireps(); loadMWOS();
  loadGairmet(); loadVolash(); loadNwsAlerts();
}

// ═══════════════════════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════════════════════
initMap();
document.getElementById('destIn').addEventListener('keydown',e=>{if(e.key==='Enter')setDest();});
pollFast(); pollSlow(); loadWxText();
setInterval(pollFast, 5000);      // traffic + radio: 5s
setInterval(pollSlow, 300000);    // wx overlays: 5min
</script>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8083, debug=False)
