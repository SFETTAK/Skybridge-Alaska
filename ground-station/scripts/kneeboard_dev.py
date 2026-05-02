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
import subprocess
import threading
import time
import uuid
import urllib.request
import urllib.error

from flask import Flask, jsonify, request
from flask_sock import Sock

app = Flask(__name__)
sock = Sock(app)

_ACCESS_LOG = "/opt/skybridge/logs/access.jsonl"


@app.after_request
def _audit_log(response):
    entry = {
        "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "service": "kneeboard",
        "method": request.method,
        "path": request.path,
        "status": response.status_code,
        "remote": request.remote_addr,
    }
    try:
        with open(_ACCESS_LOG, "a") as _f:
            _f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
    return response

TRANSCRIPT_DIR = "/mnt/nvme/skybridge/transcripts"
ADSB_JSON = "/run/readsb/aircraft.json"
METAR_CACHE = {"data": None, "ts": 0}
METAR_TTL = 300  # 5 min cache


def _freshness(max_age_s):
    return {"as_of": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "max_age_s": max_age_s}

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

@app.route("/icons-preview")
def icons_preview():
    """Live preview of the deployed hybrid icon scheme (DEV)."""
    SVGS = {
      "current": '<path d="M12 4 L19 20 L12 16 L5 20 Z"/>',
      "ga_single": '<path d="M12 3 L12.6 8 L20 11 L20 12 L12.6 11.5 L12.6 17 L14.5 19 L14.5 20 L9.5 20 L9.5 19 L11.4 17 L11.4 11.5 L4 12 L4 11 L11.4 8 Z"/>',
      "ga_twin":   '<path d="M12 3 L13 8 L20 12 L20 13 L13 12 L13 18 L15 20 L9 20 L11 18 L11 12 L4 13 L4 12 L11 8 Z"/><circle cx="6" cy="12" r="1"/><circle cx="18" cy="12" r="1"/>',
      "turboprop": '<path d="M12 2 L13 7 L21 11 L21 13 L13 12 L13 19 L16 21 L16 22 L8 22 L8 21 L11 19 L11 12 L3 13 L3 11 L11 7 Z"/><line x1="11" y1="1" x2="13" y2="1" stroke="currentColor" stroke-width="1"/>',
      "jet":       '<path d="M12 2 L13 7 L22 14 L22 15 L13 13 L13 19 L17 22 L17 22.5 L7 22.5 L7 22 L11 19 L11 13 L2 15 L2 14 L11 7 Z"/>',
      "widebody":  '<path d="M12 1 L13 6 L23 14 L23 15 L13 13 L13 20 L18 22.5 L18 23 L6 23 L6 22.5 L11 20 L11 13 L1 15 L1 14 L11 6 Z"/><circle cx="6.5" cy="12" r="0.7"/><circle cx="9" cy="11" r="0.7"/><circle cx="15" cy="11" r="0.7"/><circle cx="17.5" cy="12" r="0.7"/>',
      "helicopter":'<circle cx="12" cy="12" r="11" fill="none" stroke="currentColor" stroke-width="0.5" opacity="0.5"/><path d="M10 6 L14 6 L14 17 L15 18 L9 18 L10 17 Z M11 18 L13 18 L13 22 L11 22 Z"/><rect x="2" y="11.5" width="20" height="1" opacity="0.7"/>',
      "military":  '<path d="M12 1 L13 7 L21 19 L18 19 L13 14 L13.5 21 L16 22.5 L8 22.5 L10.5 21 L11 14 L6 19 L3 19 L11 7 Z"/>',
    }
    LABELS = [
      ("ga_single",  "GA single",       "C172, PA28, SR22, DA40",              "L",   "20 px"),
      ("ga_twin",    "GA twin / piston","BE58, PA34, P32R",                    "L",   "22 px"),
      ("turboprop",  "Turboprop",       "PC12, C208, BE20, AT72, DH8x",        "L–M", "26 px"),
      ("jet",        "Narrowbody jet",  "B737, A320, A220, B752",              "M",   "30 px"),
      ("widebody",   "Widebody jet",    "B77x, B78x, A33x, A35x, B744",        "H",   "36 px"),
      ("helicopter", "Helicopter",      "EC35, AS50, R44, B06, S92, MD52",     "L",   "24 px"),
      ("military",   "Military / fighter","RCH/PAT callsigns, F-16/F-22, etc.","var", "28 px"),
    ]

    # Altitude bands — fill colors (matches ALT_BANDS in the JS renderer)
    # INVERTED: low altitude = RED, high altitude = GREEN/teal (importance gradient)
    ALTS = [
      ("Ground/Taxi",     "#cccccc"),
      ("0–1.5k (pattern)","#ff2244"),
      ("1.5–3k (low VFR)","#ff7722"),
      ("3–6k (mid VFR)",  "#ffbb00"),
      ("6–10k (high VFR)","#ffee22"),
      ("10–18k (IFR)",    "#88dd22"),
      ("18k+ (jet)",      "#33cc88"),
      ("Emergency 7700",  "#ff0033"),
    ]

    # Operator classes — outline colors (matches OUTLINE_BY_CLASS in JS)
    CLASSES = [
      ("GA / Private",    "#ffffff"),
      ("Commercial",      "#3399ff"),
      ("Cargo",           "#cc44ff"),
      ("Military",        "#9aaa3a"),
      ("Medivac",         "#ff66cc"),
      ("Coast Guard",     "#5599ff"),
      ("Unknown",         "#000000"),
    ]

    OUTLINE_W = 0.9  # same as ICON_OUTLINE_WIDTH in JS

    def cell(svg, fill, outline, size=28, rot=0, label=None):
        rotstyle = f"transform:rotate({rot}deg);" if rot else ""
        return (f'<span class="ic">'
                f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="{fill}" '
                f'stroke="{outline}" stroke-width="{OUTLINE_W}" stroke-linejoin="round" '
                f'style="{rotstyle}filter:drop-shadow(0 0 2px {fill})">{svg}</svg>'
                f'{("<small>"+label+"</small>") if label else ""}</span>')

    # Per-silhouette rows
    rows = ""
    for key, name, examples, wake, size in LABELS:
        s = SVGS[key]
        # Sizes: small / mid / large at amber + GA outline
        sizes_demo = "".join(cell(s, "#ffaa00", "#ffffff", sz) for sz in (20, 28, 40))
        # Altitude variants (8 fills × white outline)
        alt_demo = "".join(cell(s, c, "#ffffff", 28) for _, c in ALTS)
        # Operator-class variants (7 outlines × amber fill)
        cls_demo = "".join(cell(s, "#ffaa00", c, 28) for _, c in CLASSES)
        # Rotations
        rot_demo = "".join(cell(s, "#ffaa00", "#3399ff", 28, r) for r in (45, 135, 225))
        rows += f"""
        <tr>
          <td class="cat">{name}</td>
          <td class="ex">{examples}</td>
          <td class="wk">{wake}</td>
          <td class="sz">{size}</td>
          <td class="icons">
            <div class="grp" data-grp="size">{sizes_demo}</div>
            <div class="grp" data-grp="alt">{alt_demo}</div>
            <div class="grp" data-grp="class">{cls_demo}</div>
            <div class="grp" data-grp="rot">{rot_demo}</div>
          </td>
        </tr>"""

    # Hybrid matrix: jet silhouette × every (alt × class) combo
    matrix_head = "<th></th>" + "".join(f'<th class="mhdr" style="color:{c}">{n}</th>' for n, c in CLASSES)
    matrix_rows = ""
    for alt_name, alt_color in ALTS:
        cells = "".join(f'<td>{cell(SVGS["jet"], alt_color, cls_color, 30)}</td>'
                        for _, cls_color in CLASSES)
        matrix_rows += f'<tr><th class="mhdr-row" style="color:{alt_color}">{alt_name}</th>{cells}</tr>'

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Plane Icon Preview — DEV</title>
    <style>
    body {{ background:#0a0e16; color:#d8e1ec; font-family:system-ui,sans-serif; padding:24px; margin:0; }}
    h1 {{ color:#23d18b; font-size:18px; letter-spacing:2px; text-transform:uppercase; margin:0 0 8px; }}
    h1 .dev {{ background:#ff8800; color:#000; padding:2px 8px; border-radius:4px; font-size:12px; margin-left:8px; vertical-align:middle; }}
    p.lede {{ color:#9aa5b8; font-size:13px; max-width:980px; line-height:1.5; }}
    h2 {{ color:#0090ff; font-size:13px; text-transform:uppercase; letter-spacing:1.5px; margin:28px 0 8px; }}
    table {{ border-collapse:collapse; margin-top:14px; background:#141a26; border-radius:8px; overflow:hidden; }}
    th, td {{ padding:10px 14px; text-align:left; border-bottom:1px solid #2a3140; vertical-align:middle; font-size:12px; }}
    th {{ background:#1f2738; color:#9aa5b8; text-transform:uppercase; letter-spacing:1px; font-size:10px; }}
    td.cat {{ color:#23d18b; font-weight:700; }}
    td.ex  {{ color:#9aa5b8; font-size:11px; max-width:200px; }}
    td.wk  {{ color:#ffaa00; font-weight:700; text-align:center; }}
    td.sz  {{ color:#9aa5b8; font-size:11px; text-align:center; }}
    td.icons {{ white-space:nowrap; }}
    .grp {{ display:inline-block; padding:4px 10px; margin:0 6px 0 0; border-right:1px dashed #2a3140; }}
    .grp:last-child {{ border-right:none; }}
    .ic {{ display:inline-flex; align-items:center; justify-content:center; width:48px; height:48px; vertical-align:middle; }}
    .legend {{ display:flex; gap:14px; flex-wrap:wrap; font-size:11px; color:#9aa5b8; align-items:center; }}
    .legend span {{ display:flex; align-items:center; gap:5px; }}
    .legend .swatch {{ width:14px; height:14px; border-radius:3px; }}
    .legend .ring {{ width:14px; height:14px; border-radius:50%; border:2px solid; background:transparent; }}
    .matrix {{ border-collapse:collapse; margin-top:14px; background:#141a26; border-radius:8px; }}
    .matrix th.mhdr {{ font-size:9px; padding:6px; text-transform:none; letter-spacing:0; }}
    .matrix th.mhdr-row {{ font-size:10px; padding:6px 12px; text-transform:none; letter-spacing:0; text-align:right; white-space:nowrap; }}
    .matrix td {{ padding:4px; text-align:center; }}
    code {{ background:#1f2738; padding:1px 5px; border-radius:3px; color:#23d18b; font-size:11px; }}
    .open-q {{ background:#1f2738; padding:14px 18px; border-radius:8px; margin-top:24px; font-size:12px; line-height:1.6; }}
    .open-q ol {{ margin:8px 0 0 18px; padding:0; }}
    </style></head><body>
    <h1>Plane Icon Preview <span class="dev">DEV</span></h1>
    <p class="lede">This page reflects what's actually rendering on the dev kneeboard at <code>:8084</code>:
    <strong>fill = altitude band</strong>, <strong>outline = operator class</strong>, <strong>shape = silhouette category</strong>, <strong>size = wake class × <code>ICON_SCALE</code></strong>. Each per-silhouette row shows four groups separated by dashed lines: <em>sizes</em> (20/28/40 px) · <em>altitude fills</em> · <em>operator outlines</em> · <em>rotations</em>.</p>

    <h2>Altitude bands → fill color</h2>
    <div class="legend">
      {"".join(f'<span><span class="swatch" style="background:{c}"></span>{n}</span>' for n, c in ALTS)}
    </div>

    <h2>Operator class → outline color</h2>
    <div class="legend">
      {"".join(f'<span><span class="ring" style="border-color:{c}"></span>{n}</span>' for n, c in CLASSES)}
    </div>

    <h2>Per-silhouette: sizes · alt fills · class outlines · rotations</h2>
    <table>
      <tr><th style="width:120px">Category</th><th style="width:200px">Examples</th><th>Wake</th><th>Size</th><th>Sizes (3) · Alt fills (8) · Class outlines (7) · Rotations (3)</th></tr>
      {rows}
    </table>

    <h2>Full hybrid matrix — narrowbody-jet silhouette at every (altitude × class) combo</h2>
    <table class="matrix">
      <tr>{matrix_head}</tr>
      {matrix_rows}
    </table>

    <div class="open-q">
      <strong>Live tunables in <code>kneeboard_dev.py</code> — all in the same icon-system block:</strong>
      <ul style="margin:8px 0 0 18px;padding:0;">
        <li><code>ALT_BANDS</code> — adjust altitude breakpoints / colors</li>
        <li><code>OUTLINE_BY_CLASS</code> — retint outlines per operator class</li>
        <li><code>OPERATOR_CLASS</code> — assign carriers to commercial/cargo/military/etc.</li>
        <li><code>ICON_SIZE_BY_WAKE</code> — pixel size per wake category L/M/H/J</li>
        <li><code>ICON_SCALE</code> + <code>ICON_BRIGHTNESS</code> — live sliders in the LYRS panel</li>
        <li><code>COLOR_MODE</code> — flip fill ↔ outline (<code>'altitude-fill'</code> ↔ <code>'class-fill'</code>)</li>
        <li><code>ICON_OUTLINE_WIDTH</code> — stroke width</li>
      </ul>
    </div>
    </body></html>"""


VECTOR_MIN_GS_KT = 30  # skip lookahead vector for stationary / taxi aircraft

def _project_ahead(lat, lon, track_deg, gs_kt, minutes):
    """Server-side flat-earth projection. Mirror of the (now-removed) client fn."""
    import math
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    if not isinstance(gs_kt, (int, float)) or gs_kt < VECTOR_MIN_GS_KT:
        return None
    if track_deg in (None, ""):
        return None
    nm = gs_kt * (minutes / 60.0)
    rad = float(track_deg) * math.pi / 180.0
    d_lat = (nm * math.cos(rad)) / 60.0
    d_lon = (nm * math.sin(rad)) / (60.0 * math.cos(lat * math.pi / 180.0))
    return [round(lat + d_lat, 6), round(lon + d_lon, 6)]


@app.route("/api/traffic")
def api_traffic():
    """Merged ADS-B: local readsb + ADSB.fi statewide.

    Reads from the in-process cache populated by the background ticker, so the
    response is fast and trails accumulate on the server even when no clients
    are connected.

    Query params:
      lookahead_min — if provided, server stamps each aircraft with vec_lat /
                      vec_lon (projected position N minutes ahead). Default 0
                      = no projection (client may project itself).
    """
    _ensure_ticker_started()
    with _LATEST_LOCK:
        aircraft = list(_LATEST_AIRCRAFT)
    # If the cache is empty (very first request after startup, before the
    # ticker has run), do a synchronous fetch so the client gets data now.
    if not aircraft:
        fresh = _fetch_adsbfi()
        _update_trails(fresh)
        _update_aircraft_cache(fresh)
        with _LATEST_LOCK:
            aircraft = list(_LATEST_AIRCRAFT)

    # Optional server-side lookahead vector projection
    try:
        lookahead = float(request.args.get("lookahead_min", "0"))
    except (TypeError, ValueError):
        lookahead = 0.0
    if lookahead > 0:
        # Annotate each aircraft with vec_lat/vec_lon when we can project
        annotated = []
        for ac in aircraft:
            tip = _project_ahead(ac.get("lat"), ac.get("lon"),
                                 ac.get("track"), ac.get("gs"), lookahead)
            if tip is not None:
                ac = dict(ac)  # don't mutate the cache entry
                ac["vec_lat"] = tip[0]
                ac["vec_lon"] = tip[1]
                ac["vec_min"] = lookahead
            annotated.append(ac)
        aircraft = annotated

    local_ct = sum(1 for a in aircraft if a.get("src") == "local")
    return jsonify({
        "aircraft": aircraft,
        "total": len(aircraft),
        "local": local_ct,
        "adsbfi": len(aircraft) - local_ct,
        "lookahead_min": lookahead,
        **_freshness(30),
    })


def _traffic_ticker_loop():
    """Background loop: fetches ADS-B every TRAFFIC_TICK_SEC and feeds trails.
    Runs in a daemon thread so trails accumulate continuously regardless of
    client activity."""
    while True:
        try:
            aircraft = _fetch_adsbfi()
            _update_trails(aircraft)
            _update_aircraft_cache(aircraft)
        except Exception as e:
            print(f"[traffic-ticker] error: {e}")
        time.sleep(TRAFFIC_TICK_SEC)


def _update_aircraft_cache(fresh_list):
    """Merge fresh fetch into _AIRCRAFT_BY_HEX with last_seen_ts.
    Aircraft NOT in this fresh fetch but seen within AIRCRAFT_GRACE_SEC are
    retained (with `stale_sec` indicating how old the position is). Aircraft
    older than the grace window are evicted.
    """
    now = time.time()
    fresh_hex = set()
    with _LATEST_LOCK:
        # 1. Insert/update fresh aircraft
        for ac in fresh_list:
            hx = ac.get("hex")
            if not hx:
                continue
            fresh_hex.add(hx)
            ac_copy = dict(ac)
            ac_copy["last_seen_ts"] = now
            ac_copy["stale_sec"] = 0
            _AIRCRAFT_BY_HEX[hx] = ac_copy
        # 2. Age cached entries not in this fetch; evict beyond grace
        evict = []
        for hx, ac in _AIRCRAFT_BY_HEX.items():
            if hx in fresh_hex:
                continue
            age = now - ac.get("last_seen_ts", now)
            if age > AIRCRAFT_GRACE_SEC:
                evict.append(hx)
            else:
                ac["stale_sec"] = round(age, 1)
        for hx in evict:
            del _AIRCRAFT_BY_HEX[hx]
        # 3. Rebuild the snapshot list (fresh + still-in-grace stale)
        _LATEST_AIRCRAFT[:] = list(_AIRCRAFT_BY_HEX.values())


def _ensure_ticker_started():
    """Idempotent start of the background ticker thread."""
    global _TICKER_STARTED
    if _TICKER_STARTED:
        return
    with _LATEST_LOCK:
        if _TICKER_STARTED:
            return
        t = threading.Thread(target=_traffic_ticker_loop, name="traffic-ticker", daemon=True)
        t.start()
        _TICKER_STARTED = True
        print("[traffic-ticker] background trail collection started")


# ─── Server-side ADS-B trail state ────────────────────────────────────────────
# Each entry: hex -> [(lat, lon, alt, ts), ...]  (ts is epoch seconds; alt is the
# raw value from /api/traffic — number, "ground", or empty/None for unknown)
# Server keeps a long history; client decides how much to render via ?max_age_min.
TRAIL_MAX_POINTS = 17280     # 24 h hard ceiling per aircraft (5 s polling × 60 × 60 × 24 / 60)
                             # — TTL almost always trims first; this is a memory safety cap.
TRAIL_MIN_DEG    = 0.0005    # ~50 m dedup so stationary aircraft don't bloat
TRAIL_TTL_SEC    = 86400     # drop trails for aircraft not seen in 24 hours
TRAFFIC_TICK_SEC      = 5    # background pull cadence — independent of clients
AIRCRAFT_GRACE_SEC    = 60   # keep aircraft in cache this long after last seen
                             # (covers brief feed gaps; client can fade them stale)
_TRAIL_STATE = {}
_TRAIL_LOCK  = threading.Lock()
# _AIRCRAFT_BY_HEX: hex → ac dict (with extra `last_seen_ts`, `stale_sec` fields).
# Surviving last_seen-grace aircraft are kept here even when not in the latest
# fetch, so the client doesn't drop their icons during brief feed gaps.
_AIRCRAFT_BY_HEX = {}
_LATEST_AIRCRAFT = []
_LATEST_LOCK = threading.Lock()
_TICKER_STARTED = False

def _update_trails(aircraft_list):
    """Append each aircraft's current position to its server-side trail."""
    now = time.time()
    seen = set()
    with _TRAIL_LOCK:
        for ac in aircraft_list:
            hx = ac.get("hex")
            lat = ac.get("lat"); lon = ac.get("lon")
            if not hx or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            seen.add(hx)
            trail = _TRAIL_STATE.setdefault(hx, [])
            if not trail or abs(trail[-1][0] - lat) > TRAIL_MIN_DEG \
                         or abs(trail[-1][1] - lon) > TRAIL_MIN_DEG:
                trail.append((lat, lon, ac.get("alt"), now))
                if len(trail) > TRAIL_MAX_POINTS:
                    del trail[:len(trail) - TRAIL_MAX_POINTS]
        # TTL cleanup — drop trails for hex not in this update AND last point old
        stale = [h for h, pts in _TRAIL_STATE.items()
                 if h not in seen and pts and (now - pts[-1][3]) > TRAIL_TTL_SEC]
        for h in stale:
            del _TRAIL_STATE[h]


@app.route("/api/traffic/trails")
def api_traffic_trails():
    """All known aircraft trails (server keeps up to TRAIL_TTL_SEC of history).

    Query params:
      max_age_min — render-window length: include only points within this many
                    minutes of the anchor end-time. Default: full history.
      until_ts    — anchor end-time (epoch seconds). Default: now.
                    Combined with max_age_min lets the client scrub through
                    history (rewind): pass an until_ts in the past to fetch
                    a window that ENDS at that timestamp.
    Returns: { hex: [{lat, lon, alt, ts}, ...], ... }
    """
    end_time = time.time()
    try:
        u = request.args.get("until_ts")
        if u:
            end_time = float(u)
    except (TypeError, ValueError):
        pass

    cutoff = None
    try:
        max_age = float(request.args.get("max_age_min", "0"))
        if max_age > 0:
            cutoff = end_time - (max_age * 60)
    except (TypeError, ValueError):
        cutoff = None

    with _TRAIL_LOCK:
        out = {}
        for hx, pts in _TRAIL_STATE.items():
            filtered = [p for p in pts if p[3] <= end_time]
            if cutoff is not None:
                filtered = [p for p in filtered if p[3] >= cutoff]
            if len(filtered) >= 2:
                out[hx] = [{"lat": p[0], "lon": p[1], "alt": p[2], "ts": p[3]}
                           for p in filtered]
    return jsonify({"end_time": end_time, "trails": out})


@app.route("/api/radio")
def api_radio():
    """Recent VHF transcripts — last N entries, falling back across recent days."""
    limit = int(request.args.get("limit", 30))
    entries = []
    # Walk back up to 7 days so the panel is never empty on quiet mornings
    for delta in range(7):
        day = (datetime.date.today() - datetime.timedelta(days=delta)).isoformat()
        tfile = os.path.join(TRANSCRIPT_DIR, f"{day}.txt")
        if not os.path.exists(tfile):
            continue
        day_entries = []
        with open(tfile) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                m = re.match(r"(\S+)\s+(?:\[([^\]]+)\]\s+)?(.*)", line)
                if m:
                    ts_str, freq, text = m.groups()
                    day_entries.append({
                        "ts": ts_str,
                        "freq": freq or "",
                        "text": text,
                    })
        entries = day_entries + entries
        if len(entries) >= limit:
            break
    # Return newest first, limited
    return jsonify(entries[-limit:][::-1])


# Station coordinates for distance-sort (lat, lon). The pi sits at PALH (Lake Hood).
_STATION_LL = {
    "PANC":(61.1744,-149.9964), "PAMR":(61.2136,-149.8442), "PAED":(61.2510,-149.8063),
    "PALH":(61.1860,-150.0390), "PAFA":(64.8151,-147.8564), "PAJN":(58.3547,-134.5762),
    "PABE":(60.7798,-161.8380), "PAOM":(64.5122,-165.4453), "PADQ":(57.7500,-152.4939),
    "PABR":(71.2854,-156.7660), "PAEN":(60.5731,-151.2450), "PAHO":(59.6456,-151.4770),
    "PAKN":(58.6768,-156.6492), "PAMC":(62.9530,-155.6060), "PAVD":(61.1340,-146.2486),
    "PAAQ":(61.5949,-149.0887), "PAEI":(64.6655,-147.1013), "PAKT":(55.3556,-131.7139),
    "PAOT":(66.8847,-162.5985), "PASN":(57.1671,-170.2205), "PATK":(62.3205,-150.0937),
    "PAUN":(63.8884,-160.7989), "PADU":(53.9001,-166.5436), "PAGA":(64.7361,-156.9372),
    "PAIL":(59.7503,-154.9106), "PAWG":(56.4842,-132.3697), "PFYU":(66.5715,-145.2497),
    "PPIZ":(69.7327,-163.0053), "PAAQ":(61.5949,-149.0887), "PABI":(64.5138,-165.4407),
    "PANN":(60.0438,-161.9778), "PASN":(57.1671,-170.2205),
}

# Station to sort distances from. Lake Hood (the Pi's location).
_DIST_ANCHOR = (61.1860, -150.0390)

def _great_circle_nm(a, b):
    """Great-circle distance in nautical miles."""
    import math
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * 3440.065 * math.asin(math.sqrt(h))


@app.route("/api/weather")
def api_weather():
    """Fetch METAR/TAF for stations, sorted by distance from PALH.
    Each station includes a `reportTime` (ISO-8601 from aviationweather.gov)
    so the client can show observation age. Ordering is server-driven so all
    clients render closest-first identically.
    """
    stations = request.args.get("stations", "PANC,PALH,PAMR,PAED,PAAQ,PAFA")
    now = time.time()
    if METAR_CACHE["data"] and (now - METAR_CACHE["ts"]) < METAR_TTL \
                             and METAR_CACHE.get("key") == stations:
        return jsonify(METAR_CACHE["data"])

    # Stale-while-upstream-slow: keep the previously-served data on hand so we
    # can return it if the refetch fails or times out. Without this, a single
    # slow aviationweather.gov call freezes the whole panel for every client.
    stale_data = METAR_CACHE.get("data")

    icaos = [s.strip().upper() for s in stations.split(",") if s.strip()]
    # Distance-sorted, closest first. Stations not in lookup go last (alphabetical).
    icaos.sort(key=lambda s: _great_circle_nm(_DIST_ANCHOR, _STATION_LL[s]) if s in _STATION_LL else 9999)

    # Tighter timeout per upstream call so the whole loop bounds at ≤ ~9 stations × 3 s = 27 s worst case.
    PER_REQ_TIMEOUT = 3
    metars, tafs, meta = {}, {}, {}
    upstream_failures = 0
    for stn in icaos:
        # METAR
        try:
            url = f"https://aviationweather.gov/api/data/metar?ids={stn}&format=json"
            req = urllib.request.Request(url, headers={"User-Agent": "SkyBridge/1.0"})
            with urllib.request.urlopen(req, timeout=PER_REQ_TIMEOUT) as resp:
                data = json.loads(resp.read())
                if data:
                    metars[stn] = data[0].get("rawOb", "")
                    meta[stn] = {
                        "reportTime": data[0].get("reportTime", ""),
                        "distNm": round(_great_circle_nm(_DIST_ANCHOR, _STATION_LL[stn]), 1)
                                  if stn in _STATION_LL else None,
                    }
        except Exception:
            upstream_failures += 1
            # Reuse stale data for this station if we have it, instead of "(unavailable)"
            if stale_data:
                old_metar = (stale_data.get("metars") or {}).get(stn)
                old_meta  = (stale_data.get("meta")   or {}).get(stn, {})
                if old_metar and old_metar != "(unavailable)":
                    metars[stn] = old_metar
                    meta[stn] = {**old_meta, "stale": True}
                    continue
            metars[stn] = "(unavailable)"
            meta[stn] = {"reportTime": "", "distNm": None, "stale": True}

        # TAF
        try:
            url = f"https://aviationweather.gov/api/data/taf?ids={stn}&format=json"
            req = urllib.request.Request(url, headers={"User-Agent": "SkyBridge/1.0"})
            with urllib.request.urlopen(req, timeout=PER_REQ_TIMEOUT) as resp:
                data = json.loads(resp.read())
                if data:
                    tafs[stn] = data[0].get("rawTAF", "")
        except Exception:
            # Fall back to stale TAF if any
            if stale_data:
                old_taf = (stale_data.get("tafs") or {}).get(stn)
                if old_taf:
                    tafs[stn] = old_taf

    # If aviationweather.gov was completely unreachable AND we have a previous
    # full response, return the stale snapshot rather than a half-empty new one.
    every_station_failed = upstream_failures >= len(icaos)
    if every_station_failed and stale_data:
        stale = dict(stale_data)
        stale["upstream"] = "unreachable"
        stale["served_from_cache_age_sec"] = int(now - METAR_CACHE.get("ts", now))
        return jsonify(stale)

    result = {
        "metars": metars,
        "tafs": tafs,
        "meta": meta,            # per-station: reportTime, distNm, [stale]
        "stations": icaos,        # explicit sort order
        "anchor": list(_DIST_ANCHOR),
        "upstream": "ok" if upstream_failures == 0 else "partial",
        "upstream_failures": upstream_failures,
        **_freshness(300),
    }
    METAR_CACHE["data"] = result
    METAR_CACHE["ts"] = now
    METAR_CACHE["key"] = stations
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
        **_freshness(60),
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
        {"id": 133, "name": "Lake Hood (PALH)",         "icao": "PALH"},
        {"id": 1,   "name": "Merrill Field (PAMR)",     "icao": "PAMR"},
        {"id": 265, "name": "Merrill Field 2 (PAMR)",   "icao": "PAMR"},
        {"id": 166, "name": "Fairbanks Intl (PAFA)",    "icao": "PAFA"},
        {"id":  67, "name": "Thompson Pass",            "icao": ""},
        {"id": 101, "name": "Whittier Harbor",          "icao": ""},
        {"id": 595, "name": "Anaktuvuk Pass",           "icao": ""},
        {"id": 496, "name": "Atqasuk (PATK)",           "icao": "PATK"},
        {"id": 562, "name": "Wainwright",               "icao": ""},
        {"id": 529, "name": "Nuiqsut (PAQT)",           "icao": "PAQT"},
        {"id": 430, "name": "Kaktovik",                 "icao": ""},
        {"id":   2, "name": "Rampart (PRMP)",           "icao": "PRMP"},
        {"id": 694, "name": "Port Graham",              "icao": ""},
        {"id": 232, "name": "Port Townsend",            "icao": ""},
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


@app.route("/api/mwos/catalog")
def api_mwos_catalog():
    """All known MWOS stations: Montis catalog + supplemental Alaska sites."""
    MONTIS_KEY = "VESTUG2IIGDKKCDJFDQC6ZZAODETADWB"
    # Updated 2026-04-30 from Montis catalog (24 active stations).
    # Excludes Dahl Creek (PODC) which upstream returns at (0,0) — invalid coords.
    FALLBACK = [
        {"id": 133, "siteName": "Lake Hood MWOS",        "icaoId": "PALH", "latitude": 61.1776,    "longitude": -149.9615,   "state": "AK", "source": "montis"},
        {"id": 1,   "siteName": "Merrill Field MWOS",   "icaoId": "PAMR", "latitude": 61.2167,    "longitude": -149.8337,   "state": "AK", "source": "montis"},
        {"id": 265, "siteName": "Merrill Field MWOS 2", "icaoId": "PAMR", "latitude": 61.2148,    "longitude": -149.8396,   "state": "AK", "source": "montis"},
        {"id": 166, "siteName": "Fairbanks Intl MWOS",  "icaoId": "PAFA", "latitude": 64.813056,  "longitude": -147.8737,   "state": "AK", "source": "montis"},
        {"id":  67, "siteName": "Thompson Pass MWOS",   "icaoId": "",     "latitude": 61.141065,  "longitude": -145.749145, "state": "AK", "source": "montis"},
        {"id": 101, "siteName": "Whittier Harbor MWOS", "icaoId": "",     "latitude": 60.7775,    "longitude": -148.6862,   "state": "AK", "source": "montis"},
        {"id": 595, "siteName": "Anaktuvuk Pass MWOS",  "icaoId": "",     "latitude": 68.137126,  "longitude": -151.741023, "state": "AK", "source": "montis"},
        {"id": 496, "siteName": "Atqasuk MWOS",         "icaoId": "PATK", "latitude": 70.4697,    "longitude": -157.4307,   "state": "AK", "source": "montis"},
        {"id": 562, "siteName": "Wainwright MWOS",      "icaoId": "",     "latitude": 70.638167,  "longitude": -160.018044, "state": "AK", "source": "montis"},
        {"id": 529, "siteName": "Nuiqsut MWOS",         "icaoId": "PAQT", "latitude": 70.2129,    "longitude": -150.9998,   "state": "AK", "source": "montis"},
        {"id": 430, "siteName": "Kaktovik MWOS",        "icaoId": "",     "latitude": 70.1101,    "longitude": -143.635,    "state": "AK", "source": "montis"},
        {"id":   2, "siteName": "Rampart MWOS",         "icaoId": "PRMP", "latitude": 65.51125,   "longitude": -150.15225,  "state": "AK", "source": "montis"},
        {"id": 694, "siteName": "Port Graham MWOS",     "icaoId": "",     "latitude": 59.350842,  "longitude": -151.827721, "state": "AK", "source": "montis"},
        {"id": 232, "siteName": "Port Townsend MWOS",   "icaoId": "",     "latitude": 48.106887,  "longitude": -122.77775,  "state": "WA", "source": "montis"},
    ]
    SUPPLEMENTAL = [
        {"id": None, "siteName": "Anchorage Intl (PANC)", "icaoId": "PANC", "latitude": 61.1743, "longitude": -149.9960, "state": "AK", "source": "faa"},
        {"id": None, "siteName": "Elmendorf AFB (PAED)", "icaoId": "PAED", "latitude": 61.2506, "longitude": -149.8064, "state": "AK", "source": "faa"},
        {"id": None, "siteName": "Juneau Intl (PAJN)", "icaoId": "PAJN", "latitude": 58.3550, "longitude": -134.5762, "state": "AK", "source": "faa"},
        {"id": None, "siteName": "Kodiak (PADQ)", "icaoId": "PADQ", "latitude": 57.7500, "longitude": -152.4939, "state": "AK", "source": "faa"},
        {"id": None, "siteName": "Kenai Municipal (PAEN)", "icaoId": "PAEN", "latitude": 60.5731, "longitude": -151.2431, "state": "AK", "source": "faa"},
        {"id": None, "siteName": "Bethel (PABT)", "icaoId": "PABT", "latitude": 60.7797, "longitude": -161.8379, "state": "AK", "source": "faa"},
        {"id": None, "siteName": "Yakutat (PAYA)", "icaoId": "PAYA", "latitude": 59.5033, "longitude": -139.6603, "state": "AK", "source": "faa"},
        {"id": None, "siteName": "Nome (PAOM)", "icaoId": "PAOM", "latitude": 64.5122, "longitude": -165.4453, "state": "AK", "source": "faa"},
        {"id": None, "siteName": "Utqiagvik/Barrow (PABR)", "icaoId": "PABR", "latitude": 71.2856, "longitude": -156.7664, "state": "AK", "source": "faa"},
        {"id": None, "siteName": "Talkeetna (PATK)", "icaoId": "PATK", "latitude": 62.3200, "longitude": -150.0942, "state": "AK", "source": "faa"},
    ]
    try:
        import ssl
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            "https://api.montiscorp.com/mwos",
            headers={"authorization": MONTIS_KEY}
        )
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            raw = json.loads(resp.read())
        montis_stations = [
            {
                "id": s.get("id"),
                "siteName": s.get("siteName", ""),
                "icaoId": s.get("icaoId", ""),
                "latitude": s.get("latitude", 0),
                "longitude": s.get("longitude", 0),
                "state": s.get("state", ""),
                "source": "montis",
            }
            for s in raw
            if isinstance(s, dict)
            # Drop upstream stations with missing/invalid coords (e.g. Dahl Creek at 0,0)
            and isinstance(s.get("latitude"), (int, float))
            and isinstance(s.get("longitude"), (int, float))
            and not (s.get("latitude") == 0 and s.get("longitude") == 0)
            and -90 <= s.get("latitude") <= 90
            and -180 <= s.get("longitude") <= 180
        ]
    except Exception:
        montis_stations = FALLBACK

    existing_icao = {s["icaoId"] for s in montis_stations if s["icaoId"]}
    extras = [s for s in SUPPLEMENTAL if s["icaoId"] not in existing_icao]
    return jsonify(montis_stations + extras)


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


@app.route("/api/cwa")
def api_cwa():
    """Center Weather Advisories — Alaska/ZAN only.
    Source: aviationweather.gov /api/data/cwa (public, no auth). Each advisory
    has a polygon + hazard text. CWAs are short-fuse (1-2 hour) advisories
    issued by ARTCC center-weather meteorologists for hazards like turbulence,
    icing, low-level wind shear, convective weather.
    """
    raw = _wx_fetch("https://aviationweather.gov/api/data/cwa?format=json", "cwa") or []
    out = []
    for c in raw if isinstance(raw, list) else []:
        # Filter to Anchorage Center (ZAN) advisories
        if c.get("cwsu") not in ("ZAN", "PAZA"):
            continue
        coords = c.get("coords", [])
        polygon = []
        for pt in coords:
            try:
                polygon.append([float(pt["lat"]), float(pt["lon"])])
            except (KeyError, ValueError, TypeError):
                pass
        out.append({
            "id": f"cwa-{c.get('cwsu','?')}-{c.get('seriesId','?')}",
            "cwsu": c.get("cwsu", ""),
            "name": c.get("name", ""),
            "hazard": c.get("hazard", ""),
            "qualifier": c.get("qualifier", ""),
            "base": c.get("base"),
            "top": c.get("top"),
            "validFrom": c.get("validTimeFrom"),
            "validTo": c.get("validTimeTo"),
            "polygon": polygon,
            "raw": c.get("rawText", ""),
        })
    return jsonify(out)


@app.route("/api/tfr")
def api_tfr():
    """Temporary Flight Restrictions — Alaska only.
    Source: tfr.faa.gov/tfrapi/exportTfrList (public, no auth, JSON). Returns
    metadata only (no polygon); polygon would require fetching each TFR's KML
    separately. For now we display a pin-per-TFR with the description.
    """
    raw = _wx_fetch("https://tfr.faa.gov/tfrapi/exportTfrList", "tfr-list") or []
    out = []
    for t in raw if isinstance(raw, list) else []:
        # Alaska's ARTCC = ZAN. State = AK.
        if t.get("facility") != "ZAN" and t.get("state") != "AK":
            continue
        out.append({
            "id": t.get("notam_id", ""),
            "type": t.get("type", ""),
            "facility": t.get("facility", ""),
            "state": t.get("state", ""),
            "description": t.get("description", ""),
            "createdAt": t.get("creation_date", ""),
        })
    return jsonify({"count": len(out), "tfrs": out, "ts": int(time.time())})


@app.route("/api/notams")
def api_notams():
    """FAA NOTAMs (Notices to Airmen).
    The FAA NOTAM API at api.faa.gov requires registration and an API key.
    Set environment variable FAA_NOTAM_KEY (and optionally FAA_NOTAM_CLIENT_ID)
    on the kneeboard service to enable this endpoint.

    When the key is missing the endpoint returns a status:disabled response so
    the UI can show 'NOTAMs unavailable — register at api.faa.gov' instead of
    silently failing.
    """
    key = os.environ.get("FAA_NOTAM_KEY", "").strip()
    client_id = os.environ.get("FAA_NOTAM_CLIENT_ID", "").strip()
    if not key:
        return jsonify({
            "status": "disabled",
            "reason": "FAA_NOTAM_KEY env var not set on kneeboard service",
            "register_url": "https://api.faa.gov",
            "notams": [],
        })

    # Default to Alaska airports we care about. Override with ?icao=PANC,PAFA
    icao = request.args.get("icao", "PANC,PAFA,PAJN,PADQ,PAOM,PABE,PAEN,PAMR,PALH,PABR,PAOT,PAKN,PAVD,PAEI,PATK")
    cache_key = f"notams-{icao}"
    raw = _wx_fetch(
        f"https://external-api.faa.gov/notamapi/v1/notams?icaoLocation={icao}&pageSize=50",
        cache_key,
        headers={"client_id": client_id, "client_secret": key},
    ) or {}
    items = raw.get("items", []) if isinstance(raw, dict) else []
    out = []
    for n in items:
        props = n.get("properties", {}).get("coreNOTAMData", {}).get("notam", {})
        out.append({
            "id": props.get("number", ""),
            "icao": props.get("icaoLocation", ""),
            "type": props.get("classification", ""),
            "issued": props.get("issued", ""),
            "effective": props.get("effectiveStart", ""),
            "expires": props.get("effectiveEnd", ""),
            "text": props.get("text", ""),
        })
    return jsonify({"status": "ok", "count": len(out), "notams": out, "ts": int(time.time())})


@app.route("/api/briefing/latest")
def api_briefing_latest():
    import pathlib
    briefing_path = pathlib.Path("/mnt/nvme/skybridge/briefings/latest.md")
    try:
        markdown = briefing_path.read_text()
        return jsonify({"markdown": markdown})
    except FileNotFoundError:
        return jsonify({"markdown": ""}), 404


@app.route("/api/atlas/airports")
def api_atlas_airports():
    import sqlite3 as _sqlite3
    db_path = "/opt/skybridge/atlas/activity.db"
    try:
        con = _sqlite3.connect(db_path)
        cur = con.cursor()
        # per-airport flight counts and type mix
        cur.execute("""
            SELECT origin, count(*) as flights,
                   count(DISTINCT date) as active_days,
                   round(avg(duration_s)/60, 1) as avg_dur_min,
                   count(DISTINCT category) as type_count
            FROM flights
            WHERE origin IS NOT NULL AND origin != ''
            GROUP BY origin
            ORDER BY flights DESC
        """)
        rows = cur.fetchall()
        airports = {}
        for row in rows:
            arpt, cnt, days, avg_dur, types = row
            airports[arpt] = {
                "airport": arpt,
                "flights": cnt,
                "active_days": days,
                "avg_duration_min": avg_dur,
                "type_count": types,
                "hour_heatmap": [0] * 24,
                "dow_heatmap": [0] * 7,
            }
        # hour-of-day heatmap (UTC hours from start_ts)
        cur.execute("""
            SELECT origin, CAST(strftime('%H', datetime(start_ts,'unixepoch')) AS INTEGER) as hr,
                   count(*) as cnt
            FROM flights
            WHERE origin IS NOT NULL AND origin != '' AND start_ts IS NOT NULL
            GROUP BY origin, hr
        """)
        for arpt, hr, cnt in cur.fetchall():
            if arpt in airports and 0 <= hr < 24:
                airports[arpt]["hour_heatmap"][hr] = cnt
        # day-of-week heatmap (0=Mon...6=Sun via strftime %w 0=Sun)
        cur.execute("""
            SELECT origin, CAST(strftime('%w', date) AS INTEGER) as dow,
                   count(*) as cnt
            FROM flights
            WHERE origin IS NOT NULL AND origin != '' AND date IS NOT NULL
            GROUP BY origin, dow
        """)
        for arpt, dow, cnt in cur.fetchall():
            if arpt in airports:
                # strftime %w: 0=Sun -> map to Mon-based: Sun=6, Mon=0..Sat=5
                mon_based = (dow - 1) % 7
                airports[arpt]["dow_heatmap"][mon_based] = cnt
        con.close()
        return jsonify({"airports": list(airports.values()), "as_of": datetime.datetime.utcnow().isoformat() + "Z"})
    except Exception as e:
        print(f"Atlas airports error: {e}")
        return jsonify({"airports": [], "error": str(e)}), 500


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
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gridstack@10.3.1/dist/gridstack.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/gridstack@10.3.1/dist/gridstack-all.js"></script>
<style>
  :root {
    --bg: #0a0e14; --panel-alpha: 0.92; --surface: rgba(11,15,21,var(--panel-alpha)); --surface2: #1a2230;
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

  /* ── LAYOUT (DEV: map fills viewport; panels overlay with translucent bg) ── */
  #map { position:absolute; top:44px; left:0; right:0; bottom:0; z-index:1;
         background:#1a2230; }
  /* Override Leaflet's default white tile background so unloaded squares
     match the dark theme during pan/zoom instead of flashing white. */
  .leaflet-container { background:#1a2230 !important; }

  /* ── CHAT SIDEBAR — DEV: bottom half of the unified left column ──
     border-top is a softer rgba so it reads as a section divider, not a hard
     break between two stacked panels. */
  #chat-sidebar {
    position:absolute; top:calc(44px + (100vh - 44px) / 2); left:64px; bottom:0;
    width:300px; z-index:900;
    background:var(--surface);
    border-right:1px solid var(--border);
    border-top:1px solid rgba(255,255,255,0.06);
    display:flex; flex-direction:column;
    backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
    transform:translateX(0); transition:transform .25s ease;
  }
  /* Same: shift fully off-screen so the FAB column stays clickable. */
  #chat-sidebar.collapsed { transform:translateX(-380px); }
  /* Hide the chevron toggle when the chat is collapsed (PNL FAB replaces it) */
  /* Slide chat fully off-screen left when collapsed (matches radio-panel) */
  #chat-sidebar.collapsed { transform:translateX(-380px); }
  /* Chevron toggle removed — PNL FAB is the single side-panel control.
     Otherwise users could divergent-toggle one panel and PNL state would mis-sync. */
  #chat-sidebar-toggle { display:none !important; }
  #chat-history {
    flex:1; overflow-y:auto; padding:10px 10px 6px;
    display:flex; flex-direction:column; gap:8px;
  }
  #chat-history::-webkit-scrollbar { width:4px; }
  #chat-history::-webkit-scrollbar-thumb { background:var(--border); border-radius:2px; }
  .chat-msg { max-width:85%; padding:7px 10px; border-radius:10px; font-size:12px; line-height:1.5; word-break:break-word; }
  .chat-msg.pilot { align-self:flex-end; background:var(--green); color:#000; border-radius:10px 10px 2px 10px; }
  .chat-msg.blaze { align-self:flex-start; background:var(--surface2); color:var(--white); border-radius:10px 10px 10px 2px; border:1px solid var(--border); }
  .chat-msg.sys { align-self:center; color:var(--text2); font-size:10px; background:transparent; }
  #chat-input-row {
    display:flex; gap:6px; padding:8px 10px;
    border-top:1px solid var(--border);
  }
  #chat-input {
    flex:1; background:var(--surface2); border:1px solid var(--border);
    border-radius:6px; padding:7px 10px; color:var(--text); font-size:12px;
    resize:none; outline:none; font-family:inherit;
  }
  #chat-input:focus { border-color:var(--green); }
  #chat-send {
    background:var(--green); color:#000; border:none; border-radius:6px;
    padding:7px 12px; font-size:12px; font-weight:700; cursor:pointer;
  }
  #chat-mic {
    background:var(--surface2); color:var(--text); border:1px solid var(--border);
    border-radius:6px; padding:7px 10px; font-size:14px; cursor:pointer;
    flex-shrink:0;
  }
  #chat-mic.listening { background:var(--red,#e74c3c); color:#fff; border-color:var(--red,#e74c3c); }
  #chat-header {
    padding:8px 10px; font-size:11px; font-weight:700; color:var(--green);
    letter-spacing:1px; border-bottom:1px solid var(--border);
    text-transform:uppercase;
    display:flex; align-items:center; justify-content:space-between; gap:8px;
  }
  #chat-clear-btn {
    background:var(--surface2); color:var(--text2); border:1px solid var(--border);
    border-radius:5px; padding:3px 8px; font-size:10px; font-weight:700;
    text-transform:uppercase; letter-spacing:0.5px; cursor:pointer;
    transition:background 0.15s, color 0.15s;
  }
  #chat-clear-btn:hover { background:var(--red,#e74c3c); color:#fff; border-color:var(--red,#e74c3c); }

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
    position:absolute; top:44px; left:0; bottom:0; z-index:1500;
    display:flex; flex-direction:column; gap:6px;
    align-items:flex-start;
    /* Full-height translucent dock rail flush to the left edge. Buttons stack
       at the top; the rest of the rail is empty backdrop so there's no map
       sliver visible underneath when AI and VHF panels are collapsed. */
    padding:8px 6px 8px 14px;
    background:rgba(11,15,21,0.55);
    border-right:1px solid var(--border);
    backdrop-filter:blur(10px);
    -webkit-backdrop-filter:blur(10px);
    box-shadow:4px 0 14px rgba(0,0,0,0.45);
    pointer-events:none;            /* let map clicks through the empty space below the buttons */
  }
  .fab-col .fab { pointer-events:auto; }
  .fab {
    width:48px; height:48px; border-radius:10px; border:1px solid var(--border);
    background:var(--surface); color:var(--text); font-size:11px; font-weight:700;
    cursor:pointer; display:flex; flex-direction:column; align-items:center;
    justify-content:center; gap:2px; backdrop-filter:blur(10px);
    transition:background 0.15s, transform 0.1s;
  }
  .fab:active { transform:scale(0.9); }
  .fab.on { background:var(--green); color:#000; border-color:var(--green); }
  .fab.off { opacity:0.5; }
  .fab svg { width:18px; height:18px; }
  .fab .ft { font-size:7px; letter-spacing:0.5px; text-transform:uppercase; }

  /* Layer control panel */
  .layer-panel {
    position:absolute; top:52px; left:64px; z-index:1100;
    background:var(--surface); border:1px solid var(--border); border-radius:10px;
    padding:8px 0; min-width:220px;
    transform:scaleY(0); transform-origin:top left; transition:transform 0.2s;
    box-shadow:0 4px 18px rgba(0,0,0,0.45);
    max-height:calc(100vh - 64px);
    overflow-y:auto;
    -webkit-overflow-scrolling:touch;
    overscroll-behavior:contain;
  }
  .layer-panel::-webkit-scrollbar { width:6px; }
  .layer-panel::-webkit-scrollbar-track { background:transparent; }
  .layer-panel::-webkit-scrollbar-thumb { background:var(--border); border-radius:3px; }
  .layer-panel::-webkit-scrollbar-thumb:hover { background:var(--text2); }
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
  /* Icon size slider — appended to layer panel */
  .layer-divider { border-top:1px solid var(--border); margin:6px 0; }
  .size-row { padding:10px 14px; display:flex; flex-direction:column; gap:6px; font-size:11px; }
  .size-row .size-hdr { display:flex; justify-content:space-between; align-items:baseline; }
  .size-row .size-label { color:var(--text2); text-transform:uppercase; letter-spacing:1px; font-weight:700; font-size:10px; }
  .size-row .size-value { color:var(--green); font-size:12px; font-weight:700; }
  .size-row input[type=range] { width:100%; accent-color:var(--green); margin:0; }

  /* Radio panel — DEV: top half of the unified left column.
     Border-bottom omitted so radio + chat read as one continuous column;
     chat's faint border-top is the only divider between the two sections. */
  .radio-panel {
    position:absolute; top:44px; left:64px; width:300px;
    height:calc((100vh - 44px) / 2); z-index:1000;
    background:var(--surface); border-right:1px solid var(--border);
    backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
    display:flex; flex-direction:column;
    transform:translateX(0); transition:transform .25s ease;
  }
  /* Slide panel fully off-screen to the LEFT so it never overlaps the FAB column.
     panel left:64 + width:300 = right edge 364; we shift -380 so right edge ends at -16 (off-screen). */
  .radio-panel.collapsed { transform:translateX(-380px); }
  .radio-panel.expanded { /* no-op in side layout */ }
  .radio-bar {
    display:flex; align-items:center; padding:10px 14px; min-height:40px;
    flex-shrink:0; border-bottom:1px solid var(--border); gap:10px;
  }
  .radio-bar .rt { font-size:12px; font-weight:700; color:var(--amber);
    letter-spacing:1px; text-transform:uppercase; flex:1; }
  .radio-bar .grip { display:none; }
  .radio-bar .badge { background:var(--amber); color:#000; font-size:9px;
    font-weight:800; padding:2px 7px; border-radius:8px; }
  .radio-log { flex:1; overflow-y:auto; padding:8px; -webkit-overflow-scrolling:touch; }
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

  /* FAA Comms list (NOTAMs + TFRs) — middle section of the WX panel */
  #faaCommsList { flex:1; min-height:0; overflow-y:auto; -webkit-overflow-scrolling:touch;
    border-top:1px solid var(--border); }
  #faaCommsList .fc-hdr { padding:8px 12px; font-size:10px; font-weight:700;
    color:#ff6688; text-transform:uppercase; letter-spacing:1.2px;
    border-bottom:1px solid var(--border);
    display:flex; align-items:center; justify-content:space-between; gap:6px;
    background:rgba(255,102,136,0.08); }
  #faaCommsList .fc-empty { padding:10px 14px; font-size:11px; color:var(--text2); }
  #faaCommsList .fc-item {
    padding:6px 12px; border-bottom:1px solid var(--border); font-size:11px;
    line-height:1.4; cursor:default;
  }
  #faaCommsList .fc-item .fc-id { color:#ff8800; font-weight:700; font-size:10px; }
  #faaCommsList .fc-item .fc-type { color:var(--text2); font-size:9px;
    text-transform:uppercase; letter-spacing:1px; margin-left:6px; }
  #faaCommsList .fc-item .fc-text { color:var(--text); white-space:pre-wrap;
    word-wrap:break-word; }
  #faaCommsList .fc-item.tfr { border-left:3px solid #ff3300; }
  #faaCommsList .fc-item.cwa { border-left:3px solid #ff66ff; }
  #faaCommsList .fc-item.notam { border-left:3px solid #ffcc00; }
  #faaCommsList .fc-item.disabled { color:var(--text2); font-style:italic; }
  #wxContent { flex:0 0 auto; max-height:34vh; overflow-y:auto;
    -webkit-overflow-scrolling:touch; border-bottom:1px solid var(--border); }

  /* Weather text panel — right slide (DEV: full height, flex column, MWOS docked at bottom) */
  .wx-panel {
    position:absolute; top:44px; right:0; bottom:0; z-index:1000;
    width:320px;
    background:var(--surface); border-left:1px solid var(--border);
    transform:translateX(100%); transition:transform 0.3s;
    display:flex; flex-direction:column;
  }
  .wx-panel.open { transform:translateX(0); }
  .wx-hdr { padding:10px 14px; font-size:12px; font-weight:700; color:var(--blue);
    text-transform:uppercase; letter-spacing:1px; border-bottom:1px solid var(--border);
    display:flex; justify-content:space-between; align-items:center;
    flex-shrink:0; }
  .wx-hdr button { width:30px; height:30px; background:var(--surface2); border:none;
    color:var(--text); font-size:16px; border-radius:6px; cursor:pointer; }
  /* (wxContent flex/max-height defined above with FAA comms styles) */
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

  /* Popups — opacity tunable via --overlay-bg-alpha (LYRS slider) */
  :root { --overlay-bg-alpha: 0.95; }
  .leaflet-popup-content-wrapper {
    background:rgba(11,15,21,var(--overlay-bg-alpha))!important;
    color:var(--text)!important;
    border:1px solid var(--border)!important; border-radius:8px!important;
    backdrop-filter:blur(6px); -webkit-backdrop-filter:blur(6px);
  }
  .leaflet-popup-tip { background:rgba(11,15,21,var(--overlay-bg-alpha))!important; }
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

  /* Rewind scrubber bar — bottom-center of the map (video transport feel) */
  .rewind-bar {
    position:absolute; bottom:14px; left:calc(50% + 80px); transform:translateX(-50%);
    z-index:1050;
    background:rgba(20,26,38,0.85);
    border:1px solid var(--border); border-radius:22px;
    padding:6px 12px;
    display:flex; align-items:center; gap:8px;
    backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
    box-shadow:0 4px 16px rgba(0,0,0,0.55);
    min-width:520px; max-width:calc(100vw - 740px);
    user-select:none;
  }
  .rewind-bar .rb-btn {
    background:var(--surface2); color:var(--text);
    border:1px solid var(--border); border-radius:13px;
    padding:4px 10px; font-size:11px; font-weight:700;
    letter-spacing:0.5px; cursor:pointer; flex-shrink:0;
    transition:background 0.12s, color 0.12s, border-color 0.12s;
  }
  .rewind-bar .rb-btn:hover { background:#2a3140; }
  .rewind-bar .rb-btn:active { transform:scale(0.96); }
  .rewind-bar .rb-btn.active {
    background:var(--green); color:#000; border-color:var(--green);
  }
  .rewind-bar .rb-live { padding:4px 14px; }
  .rewind-bar .rb-slider {
    flex:1; min-width:160px; height:6px; margin:0;
    accent-color:var(--green);
  }
  .rewind-bar .rb-time {
    font-size:12px; font-weight:800; min-width:74px;
    text-align:right; color:var(--green);
    letter-spacing:0.5px;
  }
  .rewind-bar.rewound { border-color:#ff8800; }
  .rewind-bar.rewound .rb-time { color:#ff8800; }
  .rewind-bar.rewound .rb-slider { accent-color:#ff8800; }
  @media (max-width:1000px) {
    .rewind-bar { min-width:auto; left:50%; max-width:calc(100vw - 80px); }
  }

  /* MWOS panel — DEV: docked at bottom of WX panel */
  #mwos-panel {
    flex-shrink:0;
    background:var(--surface2); border-top:1px solid var(--border);
    padding:8px;
    display:none;
  }
  #mwos-panel.visible { display:block; }
  #mwos-panel .mp-hdr { font-size:10px; font-weight:700; color:#ff6600;
    text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;
    display:flex; align-items:center; justify-content:space-between; }
  .mwos-camera-grid { display:grid; grid-template-columns:1fr 1fr; gap:4px; }
  .mwos-camera-grid .mc-item { position:relative; }
  .mwos-camera-grid .mc-item img { width:100%; border-radius:4px; cursor:pointer;
    border:2px solid transparent; }
  .mwos-camera-grid .mc-dir { font-size:8px; color:var(--text2); text-align:center;
    text-transform:uppercase; letter-spacing:1px; margin-top:2px; }
  .mc-fresh { position:absolute; top:3px; right:3px; width:8px; height:8px;
    border-radius:50%; border:1px solid rgba(0,0,0,0.4); }
  .mc-fresh.fresh-green { background:var(--green); }
  .mc-fresh.fresh-yellow { background:var(--amber); }
  .mc-fresh.fresh-red { background:var(--red); }

  /* Map: no filter on satellite tiles */
  .leaflet-control-zoom a { background:var(--surface)!important; color:var(--text)!important;
    border-color:var(--border)!important; width:36px!important; height:36px!important;
    line-height:36px!important; font-size:16px!important; }
  .leaflet-control-attribution { display:none!important; }

  /* Responsive: tablet portrait — map top 60%, widgets stack below */
  @media (max-width: 1024px) {
    #map { height:60vh!important; }
    .sidebar { width:100%!important; height:auto!important; position:relative!important; }
    .top-bar { font-size:13px; }
    .leaflet-control-zoom a { width:44px!important; height:44px!important; line-height:44px!important; }
  }
  @media (orientation: portrait) {
    #map { height:55vh!important; }
    .sidebar { width:100%!important; height:auto!important; position:relative!important; flex-direction:row!important; flex-wrap:wrap!important; }
    .widget { min-height:44px; }
    button, .btn, input[type=button], .leaflet-control-zoom a { min-height:44px!important; min-width:44px!important; }
  }
</style>
</head>
<body>

<div class="top-bar">
  <span class="logo">SKYBRIDGE</span>
  <span class="sub">Kneeboard</span>
  <span style="background:#ff8800;color:#000;font-size:10px;font-weight:800;padding:2px 6px;border-radius:4px;letter-spacing:1px;">DEV</span>
  <div class="gps-strip">
    <span id="gpsStatus">GPS: acquiring</span>
    <span>GS: <span class="v" id="gsSpd">--</span>kt</span>
    <span>HDG: <span class="v" id="gsHdg">---</span></span>
    <span>ALT: <span class="v" id="gsAlt">----</span>ft</span>
    <span>TFC: <span class="v" id="tfcCt">0</span></span>
  </div>
  <button onclick="resetLayout()" style="margin-left:8px;padding:2px 8px;font-size:10px;cursor:pointer;">Reset Layout</button>
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
  <button class="fab" id="fSide" onclick="toggleSidePanels()" title="Show / hide VHF + Blaze panels">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="6" height="18" rx="1"/><path d="M11 8h10M11 12h10M11 16h7"/></svg>
    <span class="ft">PNL</span>
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
  <div class="layer-row" onclick="toggleLayer('ifrLow')">
    <div class="dot" style="background:#1f5c8b"></div>
    <span class="lname">L2a IFR Low Enroute</span>
    <div class="tog" id="togIfrLow"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('ifrHigh')">
    <div class="dot" style="background:#0a3a5e"></div>
    <span class="lname">L2b IFR High Enroute</span>
    <div class="tog" id="togIfrHigh"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('radar')">
    <div class="dot" style="background:#00cc44"></div>
    <span class="lname">L3 NEXRAD Radar</span>
    <div class="tog on" id="togRadar"></div>
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
  <div class="layer-row" onclick="toggleLayer('trails')">
    <div class="dot" style="background:#ffaa00"></div>
    <span class="lname">L13 ADS-B Trails</span>
    <div class="tog on" id="togTrails"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('vectors')">
    <div class="dot" style="background:#3399ff"></div>
    <span class="lname">L14 Predicted Path</span>
    <div class="tog on" id="togVectors"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('cwa')">
    <div class="dot" style="background:#ff66ff"></div>
    <span class="lname">L15 CWA (Center Wx)</span>
    <div class="tog on" id="togCwa"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('tfr')">
    <div class="dot" style="background:#ff3300"></div>
    <span class="lname">L16 TFRs</span>
    <div class="tog on" id="togTfr"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('notams')">
    <div class="dot" style="background:#ffff00"></div>
    <span class="lname">L17 NOTAMs</span>
    <div class="tog on" id="togNotams"></div>
  </div>
  <div class="layer-divider"></div>
  <div class="size-row">
    <div class="size-hdr">
      <span class="size-label">Plane Icon Size</span>
      <span class="size-value" id="iconScaleValue">1.0×</span>
    </div>
    <input type="range" id="iconScaleSlider" min="0.5" max="2.5" step="0.1" value="1.1" oninput="applyIconScale(this.value)" />
  </div>
  <div class="size-row">
    <div class="size-hdr">
      <span class="size-label">Plane Icon Brightness</span>
      <span class="size-value" id="iconBrightnessValue">1.0×</span>
    </div>
    <input type="range" id="iconBrightnessSlider" min="0.3" max="2.0" step="0.05" value="1.15" oninput="applyIconBrightness(this.value)" />
  </div>
  <div class="size-row">
    <div class="size-hdr">
      <span class="size-label">Predicted-Path Lookahead</span>
      <span class="size-value" id="lookaheadValue">3 min</span>
    </div>
    <input type="range" id="lookaheadSlider" min="1" max="15" step="1" value="2" oninput="applyLookahead(this.value)" />
  </div>
  <div class="size-row">
    <div class="size-hdr">
      <span class="size-label">Trail Length (rendered)</span>
      <span class="size-value" id="trailRenderValue">5 min</span>
    </div>
    <input type="range" id="trailRenderSlider" min="1" max="60" step="1" value="60" oninput="applyTrailRender(this.value)" />
  </div>
  <div class="size-row">
    <div class="size-hdr">
      <span class="size-label">Polygon Fill Opacity</span>
      <span class="size-value" id="polyAlphaValue">18 %</span>
    </div>
    <input type="range" id="polyAlphaSlider" min="0" max="80" step="2" value="10" oninput="applyPolyAlpha(this.value)" />
  </div>
  <div class="size-row">
    <div class="size-hdr">
      <span class="size-label">Panel Background Opacity</span>
      <span class="size-value" id="panelAlphaValue">92 %</span>
    </div>
    <input type="range" id="panelAlphaSlider" min="20" max="100" step="2" value="74" oninput="applyPanelAlpha(this.value)" />
  </div>
  <div class="size-row">
    <div class="size-hdr">
      <span class="size-label">L2 VFR Sectional Opacity</span>
      <span class="size-value" id="vfrOpValue">55 %</span>
    </div>
    <input type="range" id="vfrOpSlider" min="5" max="100" step="5" value="25" oninput="applyChartOp('sect', this.value)" />
  </div>
  <div class="size-row">
    <div class="size-hdr">
      <span class="size-label">L2a IFR Low Opacity</span>
      <span class="size-value" id="ifrLowOpValue">65 %</span>
    </div>
    <input type="range" id="ifrLowOpSlider" min="5" max="100" step="5" value="25" oninput="applyChartOp('ifrLow', this.value)" />
  </div>
  <div class="size-row">
    <div class="size-hdr">
      <span class="size-label">L2b IFR High Opacity</span>
      <span class="size-value" id="ifrHighOpValue">65 %</span>
    </div>
    <input type="range" id="ifrHighOpSlider" min="5" max="100" step="5" value="25" oninput="applyChartOp('ifrHigh', this.value)" />
  </div>
</div>

<div class="wx-panel open" id="wxPanel">
  <div class="wx-hdr">Weather<button onclick="toggleWx()">&times;</button></div>
  <div id="wxContent"><div class="mb"><span class="ms">Loading...</span></div></div>
  <div id="faaCommsList">
    <div class="fc-hdr"><span>FAA Comms — TFRs · NOTAMs · CWAs</span><span id="fcCount" style="color:var(--text2);font-weight:400">…</span></div>
    <div class="fc-empty">Loading…</div>
  </div>
  <div id="mwos-panel">
    <div class="mp-hdr">
      <span id="mwosPanelName">MWOS Cameras</span>
      <span id="mwosPanelTime" style="color:var(--text2);font-weight:400"></span>
    </div>
    <div class="mwos-camera-grid" id="mwosPanelGrid"></div>
  </div>
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

<!-- Rewind scrubber bar — bottom-center of the map -->
<div class="rewind-bar" id="rewindBar">
  <button class="rb-btn rb-live active" id="rbLive" onclick="applyRewind(0)" title="Snap to live">⏵ LIVE</button>
  <button class="rb-btn" onclick="bumpRewind(1)"  title="Jump back 1 minute">−1m</button>
  <button class="rb-btn" onclick="bumpRewind(5)"  title="Jump back 5 minutes">−5m</button>
  <button class="rb-btn" onclick="bumpRewind(10)" title="Jump back 10 minutes">−10m</button>
  <input type="range" id="rewindSlider" class="rb-slider" min="0" max="60" step="1" value="0" oninput="applyRewind(this.value)" />
  <span class="rb-time" id="rewindValue">LIVE</span>
</div>

<script>
// ═══════════════════════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════════════════════
let map, pilotMarker, pilotPos=null, pilotHdg=0, followPilot=true;
let destMarker=null, destLine=null;

// Layer groups
let layers = {
  sat: null, sect: null, ifrLow: null, ifrHigh: null, radar: null,
  metar: null, sigmet: null, pirep: null, traffic: null, radio: true,
  mwos: null, gairmet: null, volash: null, nwsalerts: null
};
let layerGroups = {};
let trafficMarkers = {};
let pilotContext = {aircraft:[], metar:{}, transcripts:[]};

// ═══════════════════════════════════════════════════════════════════
// AIRCRAFT ICON SYSTEM — easy to tweak in one place
// ═══════════════════════════════════════════════════════════════════
// Master scale multiplier. Live-adjustable via the slider in the LYRS panel.
// Tie to viewport / devicePixelRatio later for resolution-aware scaling.
let ICON_SCALE = parseFloat(localStorage.getItem('skybridge-icon-scale') || '1.1');
if (isNaN(ICON_SCALE) || ICON_SCALE < 0.5 || ICON_SCALE > 3.0) ICON_SCALE = 1.1;

// Brightness multiplier (CSS filter). 1.0 = normal, <1 dims, >1 brightens. Live-adjustable.
let ICON_BRIGHTNESS = parseFloat(localStorage.getItem('skybridge-icon-brightness') || '1.15');
if (isNaN(ICON_BRIGHTNESS) || ICON_BRIGHTNESS < 0.3 || ICON_BRIGHTNESS > 2.0) ICON_BRIGHTNESS = 1.15;

// Per-chart-layer opacity (VFR Sectional + IFR Low + IFR High independently
// tunable via three sliders in LYRS). Range 0.05 – 1.0; persisted per layer.
function _loadChartOp(key, def) {
  const v = parseFloat(localStorage.getItem(key) || String(def));
  return (isNaN(v) || v < 0.05 || v > 1.0) ? def : v;
}
let VFR_OPACITY      = _loadChartOp('skybridge-vfr-opacity',     0.25);
let IFR_LOW_OPACITY  = _loadChartOp('skybridge-ifr-low-opacity', 0.25);
let IFR_HIGH_OPACITY = _loadChartOp('skybridge-ifr-high-opacity',0.25);

// ───── ADS-B trails + heading-vector lookahead ─────
// Trail history is maintained server-side (TRAIL_MAX_POINTS=720, TRAIL_TTL_SEC=3600 → up to 60 min).
// Client renders only the last N minutes by default; live-adjustable via slider for "timeline rewind".
const TRAIL_WEIGHT      = 2;    // line width per segment
const TRAIL_OPACITY     = 0.65; // segment opacity
const VECTOR_DASH       = '4 4'; // dashed lookahead line
const VECTOR_WEIGHT     = 1.5;

let TRAIL_RENDER_MIN = parseFloat(localStorage.getItem('skybridge-trail-render-min') || '60');
if (isNaN(TRAIL_RENDER_MIN) || TRAIL_RENDER_MIN < 1 || TRAIL_RENDER_MIN > 60) TRAIL_RENDER_MIN = 5;

// Rewind anchor in MINUTES AGO. 0 = live (slide all the way left), 60 = 1 hour ago.
// When non-zero, trails are rendered as they appeared at that point in time.
// NOT persisted across reloads — rewind always returns to live on a fresh load.
let TRAIL_REWIND_MIN = 0;
let LOOKAHEAD_MIN = parseFloat(localStorage.getItem('skybridge-lookahead-min') || '2');
if (isNaN(LOOKAHEAD_MIN) || LOOKAHEAD_MIN < 1 || LOOKAHEAD_MIN > 15) LOOKAHEAD_MIN = 3;
const VECTOR_MIN_GS_KT  = 30;   // skip lookahead for slow/stationary aircraft

// Project (lat,lon) forward by `min` minutes given track° and groundspeed (kt).
// Returns a [lat, lon] pair using flat-earth approx (good enough for ≤ 15 min).
function projectAhead(lat, lon, trackDeg, gsKt, min) {
  const nm = (gsKt || 0) * (min / 60);
  const rad = (trackDeg || 0) * Math.PI / 180;
  const dLat = (nm * Math.cos(rad)) / 60;                          // 60 nm/deg lat
  const dLon = (nm * Math.sin(rad)) / (60 * Math.cos(lat * Math.PI / 180));
  return [lat + dLat, lon + dLon];
}

// Pixel size by ICAO wake category. Multiplied by ICON_SCALE at render time.
const ICON_SIZE_BY_WAKE = { L: 20, M: 28, H: 34, J: 40 };

// Outline width (viewBox units; 0.9 ≈ 1.05 px on screen at size 28).
const ICON_OUTLINE_WIDTH = 0.9;

// Which color goes on the fill vs. the outline?
//   'altitude-fill' (default): big silhouette = altitude, ring = operator class
//   'class-fill'             : big silhouette = operator class, ring = altitude
// Flip this if the inverse reads better on the videowall.
const COLOR_MODE = 'altitude-fill';

// Outline color by operator class. Edit values to retint.
const OUTLINE_BY_CLASS = {
  commercial: '#3399ff',  // bright blue — scheduled airlines (AAL, DAL, UAL, ASA, KAL, etc.)
  cargo:      '#cc44ff',  // purple    — freight (FDX, UPS, GTI, NAC, CKS, etc.)
  military:   '#9aaa3a',  // olive     — RCH/PAT/REACH/NAVY/AF callsigns
  medivac:    '#ff66cc',  // pink      — LIFE/MEDIC/MERCY/GUARDIAN
  coastguard: '#5599ff',  // light blue — CG
  ga:         '#ffffff',  // white     — N-registered, private, GA
  unknown:    '#000000',  // black     — fallback (no callsign / unrecognized)
};

// ICAO 3-letter airline/operator code → class. Add more as you spot them.
const OPERATOR_CLASS = {
  // US mainline + low-cost
  AAL:'commercial', UAL:'commercial', DAL:'commercial', SWA:'commercial', JBU:'commercial',
  ASA:'commercial', HAL:'commercial', AAY:'commercial', FFT:'commercial', NKS:'commercial', SCX:'commercial',
  // Canada
  ACA:'commercial', WJA:'commercial', JZA:'commercial', POE:'cargo',
  // Asia/Pacific over Anchorage (jet route ANC)
  KAL:'commercial', ANA:'commercial', JAL:'commercial', CCA:'commercial', CES:'commercial',
  CSN:'commercial', CHH:'commercial', AAR:'commercial', CPA:'commercial', PAL:'commercial', SIA:'commercial',
  EVA:'commercial', CAL:'commercial', TGW:'commercial', QFA:'commercial', ANZ:'commercial',
  // Europe
  AFR:'commercial', BAW:'commercial', DLH:'commercial', KLM:'commercial', SAS:'commercial',
  IBE:'commercial', VIR:'commercial', ICE:'commercial', FIN:'commercial', AUA:'commercial',
  // Alaska regional
  AIH:'commercial',  // Iliamna Air / similar
  RVF:'commercial', AER:'commercial', WBN:'commercial', SIL:'commercial', NAC:'cargo',
  RVV:'commercial', SRY:'commercial', QXE:'commercial',  // Reeve, Sky Regional, Horizon Air
  GTI:'cargo',  // Atlas Air / Polar Cargo
  CSG:'cargo',  // China Cargo Airlines
  // Cargo
  FDX:'cargo', UPS:'cargo', GEC:'cargo', CKS:'cargo', NCA:'cargo', PAC:'cargo',
  ABX:'cargo', WGN:'cargo', GTV:'cargo', CLX:'cargo', CCM:'cargo',
};

function classifyOperator(flight) {
  if (!flight) return 'unknown';
  // N-registered → GA / private
  if (/^N\d/.test(flight)) return 'ga';
  // Other country tail-style registrations (C-FXXX, G-XXXX, JA-XXXX, etc.)
  if (/^[A-Z]{1,2}-/.test(flight)) return 'ga';
  // 3-letter ICAO airline code + flight number
  const m = flight.match(/^([A-Z]{3})\d/);
  if (m && OPERATOR_CLASS[m[1]]) return OPERATOR_CLASS[m[1]];
  return 'unknown';
}

// Altitude color bands — ascending [ceiling_ft, color]. Leftmost match wins.
// INVERTED: low altitude = RED (collision risk, pay attention),
//           high altitude = GREEN/BLUE (overhead jet traffic, ignore).
const ALT_BANDS = [
  [  1500, '#ff2244'],  // 0–1500   ft — RED — pattern / very low: pay attention
  [  3000, '#ff7722'],  // 1500–3k  ft — red-orange — local VFR
  [  6000, '#ffbb00'],  // 3k–6k    ft — amber — mid VFR
  [ 10000, '#ffee22'],  // 6k–10k   ft — yellow — high VFR / mountains
  [ 18000, '#88dd22'],  // 10k–18k  ft — yellow-green — IFR enroute
  [Infinity,'#33cc88'], // 18k+     ft — green/teal — jet altitudes (ignorable)
];
const ALT_COLOR_UNKNOWN = '#888';     // dark grey — alt entirely missing (rare)
const ALT_COLOR_GROUND  = '#cccccc';  // light grey — taxiing / stationary on the ground
                                      // try '#ffffff' (white) or '#88ccff' (sky-blue) if you prefer

function colorByAlt(alt) {
  // ADS-B uses the literal string "ground" for taxiing aircraft → distinct grey
  if (typeof alt === 'string' && alt.toLowerCase() === 'ground') return ALT_COLOR_GROUND;
  if (alt === null || alt === undefined || alt === '' || isNaN(alt)) return ALT_COLOR_UNKNOWN;
  for (const [ceiling, color] of ALT_BANDS) {
    if (alt < ceiling) return color;
  }
  return ALT_BANDS[ALT_BANDS.length - 1][1];
}

// SVG paths for each silhouette category. viewBox is 0 0 24 24, all face north.
const SILHOUETTES = {
  ga_single:  '<path d="M12 3 L12.6 8 L20 11 L20 12 L12.6 11.5 L12.6 17 L14.5 19 L14.5 20 L9.5 20 L9.5 19 L11.4 17 L11.4 11.5 L4 12 L4 11 L11.4 8 Z"/>',
  ga_twin:    '<path d="M12 3 L13 8 L20 12 L20 13 L13 12 L13 18 L15 20 L9 20 L11 18 L11 12 L4 13 L4 12 L11 8 Z"/><circle cx="6" cy="12" r="1"/><circle cx="18" cy="12" r="1"/>',
  turboprop:  '<path d="M12 2 L13 7 L21 11 L21 13 L13 12 L13 19 L16 21 L16 22 L8 22 L8 21 L11 19 L11 12 L3 13 L3 11 L11 7 Z"/><line x1="11" y1="1" x2="13" y2="1" stroke="currentColor" stroke-width="1"/>',
  jet:        '<path d="M12 2 L13 7 L22 14 L22 15 L13 13 L13 19 L17 22 L17 22.5 L7 22.5 L7 22 L11 19 L11 13 L2 15 L2 14 L11 7 Z"/>',
  widebody:   '<path d="M12 1 L13 6 L23 14 L23 15 L13 13 L13 20 L18 22.5 L18 23 L6 23 L6 22.5 L11 20 L11 13 L1 15 L1 14 L11 6 Z"/><circle cx="6.5" cy="12" r="0.7"/><circle cx="9" cy="11" r="0.7"/><circle cx="15" cy="11" r="0.7"/><circle cx="17.5" cy="12" r="0.7"/>',
  helicopter: '<circle cx="12" cy="12" r="11" fill="none" stroke="currentColor" stroke-width="0.5" opacity="0.5"/><path d="M10 6 L14 6 L14 17 L15 18 L9 18 L10 17 Z M11 18 L13 18 L13 22 L11 22 Z"/><rect x="2" y="11.5" width="20" height="1" opacity="0.7"/>',
  military:   '<path d="M12 1 L13 7 L21 19 L18 19 L13 14 L13.5 21 L16 22.5 L8 22.5 L10.5 21 L11 14 L6 19 L3 19 L11 7 Z"/>',
};

// ICAO type code → {category, wake}. Covers most Alaska airspace.
// Anything not here falls through to classify-by-desc, then to default GA.
const TYPE_CLASS = {
  // Cessna single
  'C152':['ga_single','L'],'C172':['ga_single','L'],'C175':['ga_single','L'],'C177':['ga_single','L'],
  'C182':['ga_single','L'],'C185':['ga_single','L'],'C206':['ga_single','L'],'C207':['ga_single','L'],
  'C210':['ga_single','L'],'C150':['ga_single','L'],
  // Piper / Cirrus / Diamond
  'PA28':['ga_single','L'],'PA32':['ga_single','L'],'PA46':['ga_single','L'],
  'SR20':['ga_single','L'],'SR22':['ga_single','L'],'DA40':['ga_single','L'],'DA20':['ga_single','L'],
  // GA twin piston
  'BE58':['ga_twin','L'],'BE76':['ga_twin','L'],'PA34':['ga_twin','L'],'PA44':['ga_twin','L'],
  'P32R':['ga_twin','L'],'C310':['ga_twin','L'],'C337':['ga_twin','L'],
  // Turboprops (single + twin)
  'PC12':['turboprop','L'],'PC24':['jet','M'],'C208':['turboprop','L'],'C20T':['turboprop','L'],
  'BE9L':['turboprop','L'],'BE20':['turboprop','L'],'BE30':['turboprop','L'],'BE35':['ga_single','L'],
  'B190':['turboprop','M'],'AT72':['turboprop','M'],'AT43':['turboprop','M'],'AT45':['turboprop','M'],
  'DH8A':['turboprop','M'],'DH8B':['turboprop','M'],'DH8C':['turboprop','M'],'DH8D':['turboprop','M'],
  'SF34':['turboprop','M'],'SF50':['jet','M'],
  // Business jets
  'C25A':['jet','M'],'C25B':['jet','M'],'C25C':['jet','M'],'C500':['jet','M'],'C525':['jet','M'],
  'C550':['jet','M'],'C560':['jet','M'],'C680':['jet','M'],'CL30':['jet','M'],'CL35':['jet','M'],
  'CL60':['jet','M'],'GLF4':['jet','M'],'GLF5':['jet','M'],'GLF6':['jet','M'],
  // Regional jets
  'CRJ2':['jet','M'],'CRJ7':['jet','M'],'CRJ9':['jet','M'],'CRJX':['jet','M'],
  'E135':['jet','M'],'E145':['jet','M'],'E170':['jet','M'],'E175':['jet','M'],'E190':['jet','M'],'E195':['jet','M'],
  // Narrowbody airliners
  'B731':['jet','M'],'B732':['jet','M'],'B733':['jet','M'],'B734':['jet','M'],'B735':['jet','M'],
  'B736':['jet','M'],'B737':['jet','M'],'B738':['jet','M'],'B739':['jet','M'],
  'B38M':['jet','M'],'B39M':['jet','M'],
  'B752':['jet','M'],'B753':['jet','M'],
  'A318':['jet','M'],'A319':['jet','M'],'A320':['jet','M'],'A321':['jet','M'],'A20N':['jet','M'],'A21N':['jet','M'],
  'A220':['jet','M'],'BCS1':['jet','M'],'BCS3':['jet','M'],
  'MD80':['jet','M'],'MD81':['jet','M'],'MD82':['jet','M'],'MD83':['jet','M'],'MD88':['jet','M'],'MD90':['jet','M'],
  // Widebodies
  'B762':['widebody','H'],'B763':['widebody','H'],'B764':['widebody','H'],
  'B772':['widebody','H'],'B773':['widebody','H'],'B77L':['widebody','H'],'B77W':['widebody','H'],'B77F':['widebody','H'],
  'B78X':['widebody','H'],'B788':['widebody','H'],'B789':['widebody','H'],
  'B741':['widebody','H'],'B742':['widebody','H'],'B743':['widebody','H'],'B744':['widebody','H'],'B748':['widebody','H'],
  'A306':['widebody','H'],'A30B':['widebody','H'],'A310':['widebody','H'],
  'A332':['widebody','H'],'A333':['widebody','H'],'A338':['widebody','H'],'A339':['widebody','H'],
  'A342':['widebody','H'],'A343':['widebody','H'],'A345':['widebody','H'],'A346':['widebody','H'],
  'A359':['widebody','H'],'A35K':['widebody','H'],
  'A388':['widebody','J'],
  'DC10':['widebody','H'],'MD11':['widebody','H'],'A124':['widebody','J'],
  // Helicopters
  'AS50':['helicopter','L'],'AS55':['helicopter','L'],'AS65':['helicopter','L'],
  'EC20':['helicopter','L'],'EC30':['helicopter','L'],'EC35':['helicopter','L'],'EC45':['helicopter','L'],
  'EC55':['helicopter','M'],'EC75':['helicopter','M'],'H125':['helicopter','L'],'H135':['helicopter','L'],
  'R22':['helicopter','L'],'R44':['helicopter','L'],'R66':['helicopter','L'],
  'B06':['helicopter','L'],'B407':['helicopter','L'],'B412':['helicopter','L'],'B429':['helicopter','L'],
  'S61':['helicopter','M'],'S70':['helicopter','M'],'S76':['helicopter','M'],'S92':['helicopter','M'],
  'MD52':['helicopter','L'],'MD60':['helicopter','L'],'MD90':['helicopter','L'],
  // Military (selected — most show up via callsign override, not type)
  'F16':['military','M'],'F18':['military','M'],'F22':['military','M'],'F35':['military','M'],
  'A10':['military','M'],'C17':['widebody','H'],'C5':['widebody','J'],'C130':['turboprop','M'],'C30J':['turboprop','M'],
  'KC10':['widebody','H'],'KC46':['widebody','H'],'KC135':['jet','M'],'E3CF':['widebody','H'],
};

// Callsign prefix → silhouette category override + operator class hint
const CALLSIGN_OVERRIDES = [
  { re: /^(RCH|PAT|REACH|MUSKOX|NAVY|AF\d|JAKE|ALERT)/, category: 'military', opclass: 'military' },
  { re: /^(CG\d|COAST)/,                                  category: null,        opclass: 'coastguard' },
  { re: /^(LIFE|MEDIC|MERCY|LIFEFLIGHT|GUARDIAN)/,        category: null,        opclass: 'medivac' },
];

// Emergency squawks → red override
const EMERGENCY_SQUAWKS = new Set(['7500','7600','7700']);

function classifyAircraft(ac) {
  const type = (ac.type || '').toUpperCase();
  const flight = (ac.flight || '').toUpperCase().trim();
  const desc = (ac.desc || '').toUpperCase();
  const squawk = (ac.squawk || '').trim();

  // 1) Type-code lookup
  let category = null, wake = null;
  if (TYPE_CLASS[type]) { [category, wake] = TYPE_CLASS[type]; }

  // 2) Fallback by desc keywords
  if (!category) {
    if (/HELICOPTER|ROTORCRAFT/.test(desc)) { category='helicopter'; wake='L'; }
    else if (/CESSNA 172|CESSNA 152|CESSNA 182|PIPER|CIRRUS|DIAMOND|BEECH 35|MOONEY/.test(desc)) { category='ga_single'; wake='L'; }
    else if (/BEECH 5[58]|BEECH 76|PIPER SENECA|PIPER AZTEC|CESSNA 31[0-9]/.test(desc)) { category='ga_twin'; wake='L'; }
    else if (/KING AIR|PILATUS|CARAVAN|TWIN OTTER|DASH 8|ATR|EMB 120/.test(desc)) { category='turboprop'; wake='L'; }
    else if (/BOEING 7[78]|AIRBUS A3[345]|AIRBUS A38|MD-?11|DC-?10/.test(desc)) { category='widebody'; wake='H'; }
    else if (/BOEING 7[3-6]|AIRBUS A[23][12]|EMBRAER 1[7-9]|CRJ|MD-?[89]/.test(desc)) { category='jet'; wake='M'; }
    else { category='ga_single'; wake='L'; }  // Default
  }

  // 3) Callsign overrides (military, medivac, coast guard)
  let opclassOverride = null;
  for (const ov of CALLSIGN_OVERRIDES) {
    if (ov.re.test(flight)) {
      if (ov.category) category = ov.category;
      if (ov.opclass)  opclassOverride = ov.opclass;
      break;
    }
  }

  // 4) Operator class
  const opclass = opclassOverride || classifyOperator(flight);
  const classColor = OUTLINE_BY_CLASS[opclass] || OUTLINE_BY_CLASS.unknown;

  // 5) Altitude color & emergency
  const isEmergency = EMERGENCY_SQUAWKS.has(squawk);
  const altColor = colorByAlt(ac.alt);

  const px = Math.round((ICON_SIZE_BY_WAKE[wake] || 20) * ICON_SCALE);
  return { category, wake, altColor, classColor, opclass, size: px, isEmergency };
}

// AIRCRAFT_GRACE_SEC mirror — must match server const. Used to compute fade.
const AIRCRAFT_GRACE_SEC_CLIENT = 60;

function aircraftDivIcon(ac, label) {
  const c = classifyAircraft(ac);
  const rot = ac.track || 0;
  const svg = SILHOUETTES[c.category] || SILHOUETTES.ga_single;

  // Pick fill / outline based on COLOR_MODE; emergency always overrides fill to red.
  let fill, outline;
  if (COLOR_MODE === 'class-fill') {
    fill    = c.isEmergency ? '#ff0033' : c.classColor;
    outline = c.altColor;
  } else { // 'altitude-fill' (default)
    fill    = c.isEmergency ? '#ff0033' : c.altColor;
    outline = c.classColor;
  }

  // Stale-fade: aircraft retained in server's grace window (last_seen > 0)
  // fade from 1.0 opacity (fresh) → 0.25 (about-to-evict) so users still
  // see them but visually note they're not currently being received.
  const staleSec = (typeof ac.stale_sec === 'number') ? ac.stale_sec : 0;
  const staleFrac = Math.min(1, staleSec / AIRCRAFT_GRACE_SEC_CLIENT);
  const opacity = (1 - 0.75 * staleFrac).toFixed(2);
  const dim = staleFrac > 0;
  const staleStripe = dim
    ? `;opacity:${opacity};filter:brightness(${ICON_BRIGHTNESS}) saturate(0.6) drop-shadow(0 0 2px #000)`
    : `;filter:brightness(${ICON_BRIGHTNESS}) drop-shadow(0 0 3px ${fill})`;
  const ageBadge = (staleSec >= 5)
    ? `<div style="position:absolute;top:-4px;right:-4px;background:#1a2230;border:1px solid #888;color:#bbb;font-size:8px;font-weight:700;padding:0 3px;border-radius:3px;line-height:1.4">${Math.round(staleSec)}s</div>`
    : '';

  const ring = c.isEmergency
    ? `<circle cx="12" cy="12" r="11" fill="none" stroke="${fill}" stroke-width="1.5" opacity="0.9"><animate attributeName="opacity" values="0.9;0.2;0.9" dur="1.2s" repeatCount="indefinite"/></circle>`
    : '';
  const html = `<div style="position:relative;width:${c.size}px;height:${c.size}px">
    ${ageBadge}
    <svg viewBox="0 0 24 24" width="${c.size}" height="${c.size}" fill="${fill}" stroke="${outline}" stroke-width="${ICON_OUTLINE_WIDTH}" stroke-linejoin="round" style="transform:rotate(${rot}deg)${staleStripe}">${svg}${ring}</svg>
    <div style="position:absolute;top:${c.size + 2}px;left:50%;transform:translateX(-50%);font-size:9px;color:${fill};white-space:nowrap;font-weight:700;text-shadow:0 0 3px #000;opacity:${opacity}">${label}</div>
  </div>`;
  return L.divIcon({ className:'', html, iconSize:[c.size, c.size], iconAnchor:[c.size/2, c.size/2] });
}

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
  map = L.map('map', { center:[61.17,-149.99], zoom:11, zoomControl:true, attributionControl:false });

  // L1: ESRI World Imagery (Satellite)
  layers.sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom:18, attribution:'ESRI'
  }).addTo(map);

  // FAA Aeronautical Charts (raster — vector tiles are not published by the FAA;
  // see docs/notam-roadmap.md for the vector-chart research path).
  // Real service names — not the made-up `US_*_Charts` from earlier.
  // Each one's max native zoom differs; use Leaflet maxNativeZoom so tiles
  // upscale instead of disappearing when the user zooms in past data limit.
  // CHART_OPACITY is set live by the slider below; default is the persisted value.
  layers.sect = L.tileLayer(
    'https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/VFR_Sectional/MapServer/tile/{z}/{y}/{x}',
    { minNativeZoom:8, maxNativeZoom:11, maxZoom:18, opacity:VFR_OPACITY }
  );
  layers.ifrLow = L.tileLayer(
    'https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/IFR_AreaLow/MapServer/tile/{z}/{y}/{x}',
    { minNativeZoom:7, maxNativeZoom:11, maxZoom:18, opacity:IFR_LOW_OPACITY }
  );
  layers.ifrHigh = L.tileLayer(
    'https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/IFR_High/MapServer/tile/{z}/{y}/{x}',
    { minNativeZoom:6, maxNativeZoom:9, maxZoom:18, opacity:IFR_HIGH_OPACITY }
  );
  // All three OFF by default — pilot toggles in LYRS

  // L3: NEXRAD Radar (Iowa State Mesonet) — ON by default
  layers.radar = L.tileLayer('https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png', {
    maxZoom:12, opacity:0.5, attribution:'IEM'
  }).addTo(map);

  // L4-L7: Layer groups for dynamic data
  layerGroups.metar = L.layerGroup().addTo(map);
  layerGroups.sigmet = L.layerGroup().addTo(map);
  layerGroups.pirep = L.layerGroup().addTo(map);
  layerGroups.trails = L.layerGroup().addTo(map);    // ADS-B breadcrumb trails (rendered under traffic)
  layerGroups.vectors = L.layerGroup().addTo(map);   // Heading-vector lookahead lines
  layerGroups.traffic = L.layerGroup().addTo(map);
  layerGroups.mwos = L.layerGroup().addTo(map);
  layerGroups.gairmet = L.layerGroup().addTo(map);
  layerGroups.volash = L.layerGroup().addTo(map);
  layerGroups.nwsalerts = L.layerGroup().addTo(map);
  layerGroups.cwa = L.layerGroup().addTo(map);
  layerGroups.tfr = L.layerGroup().addTo(map);
  layerGroups.notams = L.layerGroup().addTo(map);

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
  } else if (name === 'ifrLow') {
    isOn ? layers.ifrLow.addTo(map) : map.removeLayer(layers.ifrLow);
  } else if (name === 'ifrHigh') {
    isOn ? layers.ifrHigh.addTo(map) : map.removeLayer(layers.ifrHigh);
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
  } else if (name === 'trails') {
    isOn ? layerGroups.trails.addTo(map) : map.removeLayer(layerGroups.trails);
  } else if (name === 'vectors') {
    isOn ? layerGroups.vectors.addTo(map) : map.removeLayer(layerGroups.vectors);
  } else if (name === 'cwa') {
    isOn ? layerGroups.cwa.addTo(map) : map.removeLayer(layerGroups.cwa);
  } else if (name === 'tfr') {
    isOn ? layerGroups.tfr.addTo(map) : map.removeLayer(layerGroups.tfr);
  } else if (name === 'notams') {
    isOn ? layerGroups.notams.addTo(map) : map.removeLayer(layerGroups.notams);
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
// Cache of latest aircraft data keyed by hex — used by the icon-size slider
// so we can re-render markers immediately on scale change.
const _lastTrafficCache = {};
// Server-managed trail polylines, keyed by hex. Each entry: array of L.polyline
// segments (one per pair of consecutive points, colored by altitude at that
// point). Server is source of truth; client just renders.
const _trailSegments = {};
// Per-aircraft heading-vector polyline (dashed). Heading vector is derived
// client-side from latest position + track + groundspeed; not persisted.
const _trafficVectors = {};

function _reIconAllTraffic() {
  Object.keys(trafficMarkers).forEach(id => {
    const ac = _lastTrafficCache[id];
    if (ac) {
      const label = ac.flight || ac.reg || id;
      trafficMarkers[id].setIcon(aircraftDivIcon(ac, label));
    }
  });
}

window.applyIconScale = function(val) {
  const v = parseFloat(val);
  if (isNaN(v)) return;
  ICON_SCALE = v;
  document.getElementById('iconScaleValue').textContent = v.toFixed(1) + '×';
  try { localStorage.setItem('skybridge-icon-scale', String(v)); } catch(e) {}
  _reIconAllTraffic();
};

window.applyIconBrightness = function(val) {
  const v = parseFloat(val);
  if (isNaN(v)) return;
  ICON_BRIGHTNESS = v;
  document.getElementById('iconBrightnessValue').textContent = v.toFixed(2) + '×';
  try { localStorage.setItem('skybridge-icon-brightness', String(v)); } catch(e) {}
  _reIconAllTraffic();
};

window.applyLookahead = function(val) {
  const v = parseFloat(val);
  if (isNaN(v) || v < 1 || v > 15) return;
  LOOKAHEAD_MIN = v;
  document.getElementById('lookaheadValue').textContent = v + ' min';
  try { localStorage.setItem('skybridge-lookahead-min', String(v)); } catch(e) {}
  // Server projects vectors — fire pollFast to immediately re-fetch with new lookahead
  pollFast();
};

// Polygon fill opacity — applies to all overlay shapes (CWAs, NWS alerts,
// G-AIRMETs, volcanic ash, and future weather polygons). 0% = polygon outline
// only, 80% = nearly opaque fill. Persisted across reloads.
let POLY_ALPHA_PCT = parseInt(localStorage.getItem('skybridge-poly-alpha') || '10', 10);
if (isNaN(POLY_ALPHA_PCT) || POLY_ALPHA_PCT < 0 || POLY_ALPHA_PCT > 80) POLY_ALPHA_PCT = 10;

// Apply current opacity to every polygon already rendered in the named groups.
function _applyPolyAlphaToAll() {
  const opacity = POLY_ALPHA_PCT / 100;
  ['cwa','sigmet','gairmet','volash','nwsalerts'].forEach(name => {
    const grp = layerGroups[name];
    if (!grp) return;
    grp.eachLayer(l => {
      if (typeof l.setStyle === 'function') {
        try { l.setStyle({ fillOpacity: opacity }); } catch(e){}
      }
    });
  });
}

// Panel background opacity — drives --panel-alpha CSS variable. Affects every
// panel whose background is var(--surface): WX, METARs, MWOS, VHF, Blaze chat,
// LYRS panel itself, top bar, etc.
let PANEL_ALPHA_PCT = parseInt(localStorage.getItem('skybridge-panel-alpha') || '74', 10);
if (isNaN(PANEL_ALPHA_PCT) || PANEL_ALPHA_PCT < 20 || PANEL_ALPHA_PCT > 100) PANEL_ALPHA_PCT = 74;
document.documentElement.style.setProperty('--panel-alpha', (PANEL_ALPHA_PCT/100).toFixed(2));

// Per-chart-layer opacity slider handler. name = 'sect' | 'ifrLow' | 'ifrHigh'.
window.applyChartOp = function(name, val) {
  let v = parseInt(val, 10);
  if (isNaN(v) || v < 5 || v > 100) return;
  const opacity = v / 100;
  const meta = {
    sect:    { vname:'VFR_OPACITY',      lbl:'vfrOpValue',     ls:'skybridge-vfr-opacity'     },
    ifrLow:  { vname:'IFR_LOW_OPACITY',  lbl:'ifrLowOpValue',  ls:'skybridge-ifr-low-opacity' },
    ifrHigh: { vname:'IFR_HIGH_OPACITY', lbl:'ifrHighOpValue', ls:'skybridge-ifr-high-opacity'},
  }[name];
  if (!meta) return;
  // Update the layer + state
  if (layers[name] && typeof layers[name].setOpacity === 'function') {
    layers[name].setOpacity(opacity);
  }
  if (name === 'sect')        VFR_OPACITY      = opacity;
  else if (name === 'ifrLow') IFR_LOW_OPACITY  = opacity;
  else                        IFR_HIGH_OPACITY = opacity;
  const lbl = document.getElementById(meta.lbl);
  if (lbl) lbl.textContent = v + ' %';
  try { localStorage.setItem(meta.ls, String(opacity)); } catch(e) {}
};

window.applyPanelAlpha = function(val) {
  let v = parseInt(val, 10);
  if (isNaN(v) || v < 20 || v > 100) return;
  PANEL_ALPHA_PCT = v;
  document.documentElement.style.setProperty('--panel-alpha', (v/100).toFixed(2));
  const lbl = document.getElementById('panelAlphaValue');
  if (lbl) lbl.textContent = v + ' %';
  try { localStorage.setItem('skybridge-panel-alpha', String(v)); } catch(e) {}
};

window.applyPolyAlpha = function(val) {
  let v = parseInt(val, 10);
  if (isNaN(v) || v < 0 || v > 80) return;
  POLY_ALPHA_PCT = v;
  const lbl = document.getElementById('polyAlphaValue');
  if (lbl) lbl.textContent = v + ' %';
  try { localStorage.setItem('skybridge-poly-alpha', String(v)); } catch(e) {}
  _applyPolyAlphaToAll();
};

window.applyTrailRender = function(val) {
  const v = parseFloat(val);
  if (isNaN(v) || v < 1 || v > 60) return;
  TRAIL_RENDER_MIN = v;
  document.getElementById('trailRenderValue').textContent = v + ' min';
  try { localStorage.setItem('skybridge-trail-render-min', String(v)); } catch(e) {}
  // Re-fetch + redraw immediately with the new window
  loadTrails();
};

window.applyRewind = function(val) {
  let v = parseFloat(val);
  if (isNaN(v)) return;
  if (v < 0) v = 0;
  if (v > 60) v = 60;
  TRAIL_REWIND_MIN = v;
  const bar = document.getElementById('rewindBar');
  const lbl = document.getElementById('rewindValue');
  const slider = document.getElementById('rewindSlider');
  const liveBtn = document.getElementById('rbLive');
  if (slider) slider.value = v;
  if (lbl) lbl.textContent = (v === 0) ? 'LIVE' : ('−' + v + ' min');
  if (bar) bar.classList.toggle('rewound', v > 0);
  if (liveBtn) liveBtn.classList.toggle('active', v === 0);
  loadTrails();
};

window.bumpRewind = function(delta) {
  applyRewind(TRAIL_REWIND_MIN + delta);
};

function updateTraffic(data){
  const aircraft=data.aircraft||[];
  pilotContext.aircraft=aircraft.slice(0,10).map(ac=>({id:ac.flight||ac.reg||ac.hex,alt:ac.alt,gs:ac.gs}));
  const seen=new Set();
  document.getElementById('tfcCt').textContent=aircraft.length;
  aircraft.forEach(ac=>{
    const id=ac.hex; seen.add(id);
    _lastTrafficCache[id] = ac;
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
    if(trafficMarkers[id]){
      trafficMarkers[id].setLatLng([ac.lat,ac.lon]).setPopupContent(pop);
      trafficMarkers[id].setIcon(aircraftDivIcon(ac, label));
    } else {
      trafficMarkers[id]=L.marker([ac.lat,ac.lon],{icon:aircraftDivIcon(ac, label)}).bindPopup(pop);
      layerGroups.traffic.addLayer(trafficMarkers[id]);
    }

    // ── Heading-vector lookahead (server-projected) ──
    // Server supplies vec_lat / vec_lon when ?lookahead_min=N is passed.
    // Client only renders — no recomputation, no client-side staleness.
    if (typeof ac.vec_lat === 'number' && typeof ac.vec_lon === 'number') {
      const c = classifyAircraft(ac);
      const vColor = c.isEmergency ? '#ff0033' : c.altColor;
      if (_trafficVectors[id]) {
        _trafficVectors[id].setLatLngs([[ac.lat, ac.lon], [ac.vec_lat, ac.vec_lon]]);
        _trafficVectors[id].setStyle({ color: vColor });
      } else {
        _trafficVectors[id] = L.polyline([[ac.lat, ac.lon], [ac.vec_lat, ac.vec_lon]], {
          color: vColor, weight: VECTOR_WEIGHT, opacity: 0.85,
          dashArray: VECTOR_DASH, interactive: false,
        });
        layerGroups.vectors.addLayer(_trafficVectors[id]);
      }
    } else if (_trafficVectors[id]) {
      // Server didn't project (stationary, missing track/gs, or below threshold)
      layerGroups.vectors.removeLayer(_trafficVectors[id]);
      delete _trafficVectors[id];
    }
  });
  Object.keys(trafficMarkers).forEach(id=>{
    if(!seen.has(id)){
      layerGroups.traffic.removeLayer(trafficMarkers[id]);
      delete trafficMarkers[id];
      delete _lastTrafficCache[id];
      // Trail polylines for vanished aircraft are cleared by loadTrails() next tick
      if (_trafficVectors[id]) {
        layerGroups.vectors.removeLayer(_trafficVectors[id]);
        delete _trafficVectors[id];
      }
    }
  });
}

// ═══════════════════════════════════════════════════════════════════
// L8: VHF RADIO LOG
// ═══════════════════════════════════════════════════════════════════
async function loadRadio(){
  try{
    const r=await fetch('/api/radio?limit=30'); const data=await r.json();
    pilotContext.transcripts=data.slice(0,5).map(e=>e.text);
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
function contextInjection(msg) {
  try {
    const center = map ? map.getCenter() : null;
    const parts = ['[PILOT CONTEXT]'];
    if (center) parts.push('Map center: ' + center.lat.toFixed(4) + ', ' + center.lng.toFixed(4));
    if (pilotContext.aircraft.length) parts.push('Visible aircraft (' + pilotContext.aircraft.length + '): ' + pilotContext.aircraft.map(a=>a.id+(a.alt?'@'+a.alt+'ft':'')).join(', '));
    const metarEntries = Object.entries(pilotContext.metar);
    if (metarEntries.length) parts.push('METAR: ' + metarEntries.slice(0,2).map(([k,v])=>k+': '+v).join(' | '));
    if (pilotContext.transcripts.length) parts.push('Recent VHF:\n' + pilotContext.transcripts.map(t=>'  '+t).join('\n'));
    parts.push('[/PILOT CONTEXT]');
    return parts.join('\n') + '\n\n' + msg;
  } catch(e) { return msg; }
}
// Make a marker's popup toggle on second click. Default Leaflet just opens
// and you have to hit the X. With this, click-again closes it cleanly.
//
// IMPORTANT: bindPopup installs its OWN click handler that always opens. If we
// just add another click handler on top, the default opens the popup first,
// then ours sees isOpen()=true and closes it — net result: popup never shows.
// Solution: strip ALL click handlers, then install our toggle as the only one.
function bindClickTogglePopup(layer) {
  layer.off('click');
  layer.on('click', function() {
    const p = layer.getPopup();
    if (!p) return;
    if (p.isOpen()) layer.closePopup();
    else layer.openPopup();
  });
}

// Great-circle distance in nm (client-side mirror of server _great_circle_nm)
function _gcDistNm(a, b) {
  if (!a || !b) return Infinity;
  const R = 3440.065;
  const r1 = a[0]*Math.PI/180, r2 = b[0]*Math.PI/180;
  const dlat = r2-r1, dlon = (b[1]-a[1])*Math.PI/180;
  const h = Math.sin(dlat/2)**2 + Math.cos(r1)*Math.cos(r2)*Math.sin(dlon/2)**2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
const _PI_ANCHOR = [61.1860, -150.0390];   // Lake Hood (Pi location)
const _ICAO_LL = {  // Same dict used for NOTAM marker placement
  PANC:[61.1744,-149.9964], PAFA:[64.8151,-147.8564], PAJN:[58.3547,-134.5762],
  PAMR:[61.2136,-149.8442], PAED:[61.2510,-149.8063], PALH:[61.1860,-150.0390],
  PABE:[60.7798,-161.8380], PAOM:[64.5122,-165.4453], PADQ:[57.7500,-152.4939],
  PABR:[71.2854,-156.7660], PAEN:[60.5731,-151.2450], PAHO:[59.6456,-151.4770],
  PAKN:[58.6768,-156.6492], PAMC:[62.9530,-155.6060], PAVD:[61.1340,-146.2486],
  PAEI:[64.6655,-147.1013], PATK:[62.3205,-150.0937], PAOT:[66.8847,-162.5985],
};

function _ageStr(reportTimeIso){
  if(!reportTimeIso) return '';
  const t=Date.parse(reportTimeIso); if(isNaN(t)) return '';
  const ageMin=Math.max(0,Math.round((Date.now()-t)/60000));
  if(ageMin<1) return 'just now';
  if(ageMin<60) return ageMin+' min ago';
  const h=Math.floor(ageMin/60),m=ageMin%60;
  return h+'h '+m+'m ago';
}
function _ageColor(reportTimeIso){
  if(!reportTimeIso) return 'var(--text2)';
  const t=Date.parse(reportTimeIso); if(isNaN(t)) return 'var(--text2)';
  const ageMin=(Date.now()-t)/60000;
  if(ageMin<60) return 'var(--green)';        // fresh: under 1 hour
  if(ageMin<120) return '#ffcc00';             // stale: 1-2 hours
  return '#ff8800';                            // very stale: 2+ hours
}

async function loadWxText(){
  try{const r=await fetch('/api/weather');const d=await r.json();
    pilotContext.metar=d.metars||{};
    const order = Array.isArray(d.stations) && d.stations.length
        ? d.stations
        : Object.keys(d.metars||{});
    const meta = d.meta || {};
    // Render ALL METARs in distance-sorted order (closest at top, scroll for next).
    let h='';
    for (const stn of order) {
      const raw=d.metars[stn]||''; const taf=d.tafs?.[stn]||'';
      const m=meta[stn]||{};
      let cat='VFR',cc='var(--green)';
      const cm=raw.match(/(?:OVC|BKN)(\d{3})/);
      if(cm){const c=parseInt(cm[1])*100;if(c<500){cat='LIFR';cc='var(--magenta)';}else if(c<1000){cat='IFR';cc='var(--red)';}else if(c<3000){cat='MVFR';cc='var(--blue)';}}
      const vm=raw.match(/\s(\d+)SM/);if(vm&&parseInt(vm[1])<3){cat='IFR';cc='var(--red)';}if(vm&&parseInt(vm[1])<1){cat='LIFR';cc='var(--magenta)';}
      const ageT=_ageStr(m.reportTime);
      const ageC=_ageColor(m.reportTime);
      const distLbl=(m.distNm!=null)?`<span style="color:var(--text2);font-size:9px">${m.distNm} nm</span>`:'';
      const ageBadge=ageT?`<span style="color:${ageC};font-size:9px;font-weight:700;margin-left:6px">${ageT}</span>`:'';
      h+=`<div class="mb">
        <div style="display:flex;align-items:baseline;gap:6px;flex-wrap:wrap">
          <span class="ms" style="color:${cc}">${stn} — ${cat}</span>
          ${distLbl}
          ${ageBadge}
        </div>
        <div class="mr">${esc(raw)}</div>
        ${taf?`<div class="mt">${esc(taf)}</div>`:''}
      </div>`;
    }
    document.getElementById('wxContent').innerHTML=h||'<div class="mb"><span class="ms">No data</span></div>';
  }catch(e){}
}

// Try to extract a coarse lat/lon from a TFR description (e.g. "17NM NE OF FAIRBANKS")
// or fall back to the facility center. Returns [lat, lon] or null.
function _tfrApproxLocation(t) {
  const desc = (t.description || '').toUpperCase();
  for (const [icao, ll] of Object.entries(_ICAO_LL)) {
    // Map common city names to ICAO bounding
    const cityMap = {PANC:'ANCHORAGE', PAFA:'FAIRBANKS', PAMR:'MERRILL', PAED:'ELMENDORF',
      PALH:'LAKE HOOD', PABE:'BETHEL', PAOM:'NOME', PADQ:'KODIAK', PAEN:'KENAI',
      PAJN:'JUNEAU', PABR:'BARROW', PAEI:'EIELSON', PATK:'TALKEETNA', PAOT:'KOTZEBUE',
      PAKN:'KING SALMON', PAVD:'VALDEZ'};
    if (cityMap[icao] && desc.includes(cityMap[icao])) return ll;
  }
  if (desc.includes('CLEAR')) return [64.301, -149.184];   // Clear Air Force Station
  return null;
}

async function loadFaaComms(){
  const el = document.getElementById('faaCommsList');
  if (!el) return;
  let items = [];
  try {
    const [tfrR, cwaR, notamR] = await Promise.all([
      fetch('/api/tfr').then(r => r.json()).catch(()=>({})),
      fetch('/api/cwa').then(r => r.json()).catch(()=>[]),
      fetch('/api/notams').then(r => r.json()).catch(()=>({})),
    ]);

    // CWAs — distance from polygon centroid
    (Array.isArray(cwaR) ? cwaR : []).forEach(c => {
      const poly = c.polygon || [];
      let center = null;
      if (poly.length) {
        const lat = poly.reduce((a,p)=>a+p[0],0)/poly.length;
        const lon = poly.reduce((a,p)=>a+p[1],0)/poly.length;
        center = [lat, lon];
      }
      const dist = center ? _gcDistNm(_PI_ANCHOR, center) : Infinity;
      const validTo = c.validTo ? new Date(c.validTo*1000).toLocaleTimeString() : '';
      items.push({
        dist, kind: 'cwa',
        html: `<div class="fc-item cwa">
          <div><span class="fc-id">CWA ${c.cwsu||''}-${c.id?.split('-').pop()||''}</span>
               <span class="fc-type">${c.hazard||''} ${c.qualifier||''} ${center?'· '+dist.toFixed(0)+' nm':''}</span></div>
          <div class="fc-text" style="font-size:10px">until ${validTo} · ${(c.raw||'').split('\\n').slice(2,4).join(' ').slice(0,140)}…</div>
        </div>`,
      });
    });

    // TFRs — distance from approximated location
    (tfrR.tfrs || []).forEach(t => {
      const loc = _tfrApproxLocation(t);
      const dist = loc ? _gcDistNm(_PI_ANCHOR, loc) : Infinity;
      items.push({
        dist, kind: 'tfr',
        html: `<div class="fc-item tfr">
          <div><span class="fc-id">${t.id||''}</span>
               <span class="fc-type">TFR · ${t.type||''} ${loc?'· '+dist.toFixed(0)+' nm':''}</span></div>
          <div class="fc-text">${t.description||''}</div>
        </div>`,
      });
    });

    // NOTAMs — distance from ICAO lat/lon
    if (notamR.status === 'disabled') {
      items.push({
        dist: Infinity, kind: 'disabled',
        html: `<div class="fc-item notam disabled">
          <div><span class="fc-id">NOTAMs</span>
               <span class="fc-type">disabled</span></div>
          <div class="fc-text" style="font-size:10px">FAA NOTAM API key not configured. Register at api.faa.gov and set <code>FAA_NOTAM_KEY</code> on the kneeboard service.</div>
        </div>`,
      });
    } else {
      (notamR.notams || []).forEach(n => {
        const ll = _ICAO_LL[n.icao];
        const dist = ll ? _gcDistNm(_PI_ANCHOR, ll) : Infinity;
        items.push({
          dist, kind: 'notam',
          html: `<div class="fc-item notam">
            <div><span class="fc-id">${n.id||''}</span>
                 <span class="fc-type">${n.icao||''} · ${n.type||''} ${ll?'· '+dist.toFixed(0)+' nm':''}</span></div>
            <div class="fc-text">${(n.text||'').slice(0,300)}</div>
          </div>`,
        });
      });
    }
  } catch(e) { console.error('FAA comms load:', e); }

  // Sort: closest first; "disabled" / unknowns sink to bottom
  items.sort((a,b) => a.dist - b.dist);
  const count = items.filter(x => x.kind !== 'disabled').length;
  el.innerHTML =
    `<div class="fc-hdr"><span>FAA Comms — TFRs · NOTAMs · CWAs</span>` +
    `<span id="fcCount" style="color:var(--text2);font-weight:400">${count} active</span></div>` +
    (items.map(x => x.html).join('') || '<div class="fc-empty">No active TFRs, NOTAMs, or CWAs in ZAN/AK.</div>');
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
      const flightvisUrl=`https://flightvis.montiscorp.com/map?lat=${s.lat}&lon=${s.lon}&m=12638`;
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
        <div style="margin-top:6px"><a href="${flightvisUrl}" target="_blank" style="color:#ff6600;font-size:10px;text-decoration:none">&#9654; Open in FlightVis</a></div>
      </div>`;
      const m=L.marker([s.lat,s.lon],{icon,zIndexOffset:500}).bindPopup(pop,{maxWidth:340,minWidth:280});
      layerGroups.mwos.addLayer(m);
    });
    updateMwosPanel(data);
  }catch(e){console.error('MWOS error:',e);}
}

function updateMwosPanel(stations){
  const panel=document.getElementById('mwos-panel');
  const grid=document.getElementById('mwosPanelGrid');
  const nameEl=document.getElementById('mwosPanelName');
  const timeEl=document.getElementById('mwosPanelTime');
  if(!stations||!stations.length){panel.classList.remove('visible');return;}
  let best=null;
  if(pilotPos){
    let minD=Infinity;
    stations.forEach(s=>{
      if(!s.cameras||!s.cameras.length)return;
      const d=(s.lat-pilotPos.lat)**2+(s.lon-pilotPos.lng)**2;
      if(d<minD){minD=d;best=s;}
    });
  }
  if(!best)best=stations.find(s=>s.cameras&&s.cameras.length)||stations[0];
  if(!best||!best.cameras||!best.cameras.length){panel.classList.remove('visible');return;}
  const cams=best.cameras.slice(0,4);
  const now=Date.now();
  grid.innerHTML=cams.map(c=>{
    const ts=c.ts?new Date(c.ts).getTime():0;
    const age=ts?now-ts:Infinity;
    const fc=age<300000?'fresh-green':age<1800000?'fresh-yellow':'fresh-red';
    return`<div class="mc-item"><img src="${c.url}" alt="${c.dir}" onclick="window.open('${c.url}','_blank')"/><div class="mc-fresh ${fc}"></div><div class="mc-dir">${c.dir}</div></div>`;
  }).join('');
  nameEl.textContent=best.name;
  const t=best.obs&&best.obs.time?new Date(best.obs.time).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'';
  timeEl.textContent=t;
  panel.classList.add('visible');
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
  try{
    const r=await fetch('/api/traffic?lookahead_min=' + LOOKAHEAD_MIN);
    const d=await r.json();
    updateTraffic(d);
  }catch(e){}
  loadTrails();
  loadRadio();
}

// Render server-managed ADS-B trails. Each segment is colored by the altitude
// at the time/location of its starting point — so climbs and descents show as
// gradient color changes along the trail.
async function loadTrails(){
  let payload, data;
  try {
    let url = '/api/traffic/trails?max_age_min=' + TRAIL_RENDER_MIN;
    if (TRAIL_REWIND_MIN > 0) {
      const untilTs = (Date.now() / 1000) - (TRAIL_REWIND_MIN * 60);
      url += '&until_ts=' + untilTs.toFixed(0);
    }
    const r = await fetch(url);
    payload = await r.json();
    data = payload && payload.trails ? payload.trails : (payload || {});
  } catch(e) { return; }
  const fresh = new Set(Object.keys(data));
  // Drop trails for aircraft no longer reported
  Object.keys(_trailSegments).forEach(id => {
    if (!fresh.has(id)) {
      _trailSegments[id].forEach(seg => layerGroups.trails.removeLayer(seg));
      delete _trailSegments[id];
    }
  });
  // Render fresh segmented trails
  Object.entries(data).forEach(([id, points]) => {
    if (!Array.isArray(points) || points.length < 2) return;
    // Tear down old segments for this hex (cheap; trails are short)
    if (_trailSegments[id]) _trailSegments[id].forEach(seg => layerGroups.trails.removeLayer(seg));
    const segs = [];
    for (let i = 1; i < points.length; i++) {
      const prev = points[i-1], cur = points[i];
      const segColor = colorByAlt(prev.alt);
      const seg = L.polyline([[prev.lat, prev.lon], [cur.lat, cur.lon]], {
        color: segColor, weight: TRAIL_WEIGHT, opacity: TRAIL_OPACITY,
        interactive: false, smoothFactor: 1.0,
      });
      layerGroups.trails.addLayer(seg);
      segs.push(seg);
    }
    _trailSegments[id] = segs;
  });
}
async function pollSlow(){
  loadMetarMap(); loadSigmets(); loadPireps(); loadMWOS();
  loadGairmet(); loadVolash(); loadNwsAlerts();
  loadCwa(); loadTfr(); loadNotams();
  loadFaaComms();   // sidebar list (TFRs + CWAs + NOTAMs)
  // After polygons re-render, reapply user's chosen fill opacity
  setTimeout(_applyPolyAlphaToAll, 1500);
}

// L15: Center Weather Advisories (polygon, magenta)
async function loadCwa(){
  try {
    const r = await fetch('/api/cwa');
    const data = await r.json();
    layerGroups.cwa.clearLayers();
    data.forEach(c => {
      if (!c.polygon || c.polygon.length < 3) return;
      const poly = L.polygon(c.polygon, {
        color:'#ff66ff', weight:2, fillColor:'#ff66ff', fillOpacity:0.18, dashArray:'5 4',
      });
      const validTo = c.validTo ? new Date(c.validTo*1000).toLocaleTimeString() : '?';
      poly.bindPopup(`<div class="pop">
        <div class="cs" style="color:#ff66ff">CWA — ${c.hazard||''} ${c.qualifier||''}</div>
        <div><span class="lbl">Center:</span> ${c.cwsu} (${c.name||''})</div>
        <div><span class="lbl">Valid until:</span> ${validTo}</div>
        ${c.base?`<div><span class="lbl">Base:</span> ${c.base}</div>`:''}
        ${c.top?`<div><span class="lbl">Top:</span> ${c.top}</div>`:''}
        <pre style="font-size:9px;color:var(--text2);white-space:pre-wrap;margin-top:4px">${(c.raw||'').slice(0,500)}</pre>
      </div>`, {maxWidth:380});
      bindClickTogglePopup(poly);
      layerGroups.cwa.addLayer(poly);
    });
  } catch(e) { console.error('CWA load error:', e); }
}

// Bearing-to-degrees lookup for "NN NM <DIR> OF <ANCHOR>" parsing
const _BEARING_DEG = {
  N:0, NNE:22.5, NE:45, ENE:67.5, E:90, ESE:112.5, SE:135, SSE:157.5,
  S:180, SSW:202.5, SW:225, WSW:247.5, W:270, WNW:292.5, NW:315, NNW:337.5,
};

// Parse a TFR description to a best-effort lat/lon. Examples:
//   "17NM NE OF FAIRBANKS, AK, ..."   → near PAFA, offset 17 NM NE
//   "CLEAR, AK, ..."                   → Clear AFS
//   "Brownsville, TX, ..."             → fall back to anchor (returns null)
function _tfrLocate(t) {
  const desc = (t.description || '').toUpperCase();
  // Known landmarks not in airport dict
  if (desc.includes('CLEAR')) return [64.301, -149.184];          // Clear AFS
  if (desc.includes('CAPE CANAVERAL')) return [28.5, -80.6];      // (filtered AK-only anyway)

  // Find anchor airport / city
  const cityMap = {
    PANC:'ANCHORAGE', PAFA:'FAIRBANKS', PAMR:'MERRILL', PAED:'ELMENDORF',
    PALH:'LAKE HOOD', PABE:'BETHEL', PAOM:'NOME', PADQ:'KODIAK', PAEN:'KENAI',
    PAJN:'JUNEAU', PABR:'BARROW', PAEI:'EIELSON', PATK:'TALKEETNA',
    PAOT:'KOTZEBUE', PAKN:'KING SALMON', PAVD:'VALDEZ', PAHO:'HOMER',
  };
  let anchor = null;
  for (const [icao, city] of Object.entries(cityMap)) {
    if (desc.includes(city) && _ICAO_LL[icao]) { anchor = _ICAO_LL[icao]; break; }
  }
  if (!anchor) return null;

  // Try to extract "NN NM <DIR> OF" offset
  const m = desc.match(/(\d+)\s*NM\s+(NNE|NE|ENE|NNW|NW|WNW|ESE|SE|SSE|SSW|SW|WSW|N|S|E|W)\s+OF/);
  if (m) {
    const nm = parseFloat(m[1]);
    const deg = _BEARING_DEG[m[2]] ?? 0;
    const rad = deg * Math.PI / 180;
    const dLat = (nm * Math.cos(rad)) / 60;
    const dLon = (nm * Math.sin(rad)) / (60 * Math.cos(anchor[0] * Math.PI / 180));
    return [+(anchor[0] + dLat).toFixed(4), +(anchor[1] + dLon).toFixed(4)];
  }
  return anchor;  // fall back to airport itself
}

// L16: TFR pins — placed at parsed-from-description location (no real polygons
// since the FAA TFR API doesn't publicly expose shape data). Pins land in the
// right neighborhood; popup shows the full description.
async function loadTfr(){
  try {
    const r = await fetch('/api/tfr');
    const data = await r.json();
    const tfrs = data.tfrs || [];
    layerGroups.tfr.clearLayers();
    if (tfrs.length === 0) return;
    // Aircraft fanned out at ZAN center for any TFR we can't locate
    const ZAN_CENTER = [63.0, -152.0];
    let unknownIdx = 0;
    tfrs.forEach((t) => {
      let loc = _tfrLocate(t);
      let isApprox = false;
      if (!loc) {
        // Unlocatable — fan around ZAN center so they don't all stack
        loc = [ZAN_CENTER[0] + (unknownIdx % 5) * 0.4,
               ZAN_CENTER[1] + Math.floor(unknownIdx / 5) * 0.5];
        unknownIdx++;
        isApprox = true;
      }
      const icon = L.divIcon({className:'',
        html:`<div style="width:24px;height:24px;background:#ff3300;border:2px solid #fff;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:800;color:#fff;text-shadow:0 0 2px #000;box-shadow:0 0 8px rgba(255,51,0,0.7)${isApprox?';opacity:0.7':''}">!</div>`,
        iconSize:[28,28], iconAnchor:[14,14]});
      const m = L.marker(loc, {icon});
      m.bindPopup(`<div class="pop">
        <div class="cs" style="color:#ff3300">TFR — ${t.type||''}</div>
        <div><span class="lbl">NOTAM ID:</span> ${t.id||''}</div>
        <div><span class="lbl">Facility:</span> ${t.facility||''} (${t.state||''})</div>
        <div><span class="lbl">Created:</span> ${t.createdAt||''}</div>
        <div style="margin-top:4px;font-size:11px;color:var(--text)">${t.description||''}</div>
        <div style="margin-top:6px;font-size:9px;color:var(--text2)">${isApprox?'Pin position approximate (description did not parse)':'Pin parsed from description — actual TFR polygon not exposed by FAA public API'}</div>
      </div>`, {maxWidth:340});
      bindClickTogglePopup(m);
      layerGroups.tfr.addLayer(m);
    });
  } catch(e) { console.error('TFR load error:', e); }
}

// L17: NOTAMs (requires FAA_NOTAM_KEY env var on the kneeboard service)
async function loadNotams(){
  try {
    const r = await fetch('/api/notams');
    const data = await r.json();
    layerGroups.notams.clearLayers();
    if (data.status === 'disabled') {
      // Show a single info pin at PANC explaining the gap
      const icon = L.divIcon({className:'',
        html:`<div style="width:18px;height:18px;background:#888;border:2px solid #fff;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:800;color:#fff">i</div>`,
        iconSize:[22,22], iconAnchor:[11,11]});
      const m = L.marker([61.1744, -149.9964], {icon});
      m.bindPopup(`<div class="pop">
        <div class="cs" style="color:#ffcc00">NOTAMs unavailable</div>
        <div style="margin-top:4px;font-size:11px">Register for an FAA NOTAM API key at <code>api.faa.gov</code> and set <code>FAA_NOTAM_KEY</code> on the kneeboard service.</div>
      </div>`, {maxWidth:300});
      bindClickTogglePopup(m);
      layerGroups.notams.addLayer(m);
      return;
    }
    const notams = data.notams || [];
    const ICAO_LL = {
      PANC:[61.1744,-149.9964], PAFA:[64.8151,-147.8564], PAJN:[58.3547,-134.5762],
      PAMR:[61.2136,-149.8442], PAED:[61.2510,-149.8063], PALH:[61.1860,-150.0390],
      PABE:[60.7798,-161.8380], PAOM:[64.5122,-165.4453], PADQ:[57.7500,-152.4939],
      PABR:[71.2854,-156.7660], PAEN:[60.5731,-151.2450], PAHO:[59.6456,-151.4770],
      PAKN:[58.6768,-156.6492], PAMC:[62.9530,-155.6060], PAVD:[61.1340,-146.2486],
      PAEI:[64.6655,-147.1013], PATK:[62.3205,-150.0937], PAOT:[66.8847,-162.5985],
    };
    // Group NOTAMs by airport
    const byIcao = {};
    notams.forEach(n => { if (!byIcao[n.icao]) byIcao[n.icao] = []; byIcao[n.icao].push(n); });
    Object.entries(byIcao).forEach(([icao, list]) => {
      const ll = ICAO_LL[icao];
      if (!ll) return;
      const icon = L.divIcon({className:'',
        html:`<div style="width:22px;height:22px;background:#ffcc00;border:2px solid #000;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:800;color:#000">${list.length}</div>`,
        iconSize:[26,26], iconAnchor:[13,13]});
      const m = L.marker(ll, {icon});
      const body = list.slice(0, 8).map(n =>
        `<div style="border-top:1px solid var(--border);padding:4px 0;font-size:10px">
          <div style="color:var(--amber);font-weight:700">${n.id||''} — ${n.type||''}</div>
          <div style="color:var(--text);white-space:pre-wrap">${(n.text||'').slice(0,240)}</div>
          ${n.expires?`<div style="color:var(--text2);font-size:9px">Until: ${n.expires}</div>`:''}
        </div>`).join('');
      m.bindPopup(`<div class="pop" style="max-width:380px">
        <div class="cs" style="color:#ffcc00">${icao} — ${list.length} NOTAM${list.length>1?'s':''}</div>
        ${body}
        ${list.length>8?`<div style="font-size:9px;color:var(--text2);padding-top:4px">+${list.length-8} more</div>`:''}
      </div>`, {maxWidth:400, minWidth:300});
      bindClickTogglePopup(m);
      layerGroups.notams.addLayer(m);
    });
  } catch(e) { console.error('NOTAM load error:', e); }
}

// ═══════════════════════════════════════════════════════════════════
// LAYOUT PERSISTENCE — skybridge-layout-v1
// ═══════════════════════════════════════════════════════════════════
const LAYOUT_KEY = 'skybridge-layout-v1';
function saveLayout(serializedItems) {
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(serializedItems));
}
function loadLayout() {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY)); } catch(e) { return null; }
}
function resetLayout() {
  localStorage.removeItem(LAYOUT_KEY);
  location.reload();
}

// ═══════════════════════════════════════════════════════════════════
// CHAT SIDEBAR
// ═══════════════════════════════════════════════════════════════════
(function() {
  const CHAT_KEY = 'skybridge-chat-v1';
  const MAX_AGE_MS = 24 * 60 * 60 * 1000;
  let chatWs = null;
  let chatCollapsed = false;

  function loadChatHistory() {
    try {
      const raw = localStorage.getItem(CHAT_KEY);
      if (!raw) return [];
      const msgs = JSON.parse(raw);
      const cutoff = Date.now() - MAX_AGE_MS;
      return msgs.filter(m => m.ts > cutoff);
    } catch(e) { return []; }
  }

  function saveChatHistory(msgs) {
    const cutoff = Date.now() - MAX_AGE_MS;
    const pruned = msgs.filter(m => m.ts > cutoff).slice(-200);
    try { localStorage.setItem(CHAT_KEY, JSON.stringify(pruned)); } catch(e) {}
  }

  function appendChatMsg(role, text, ts) {
    const hist = document.getElementById('chat-history');
    const div = document.createElement('div');
    div.className = 'chat-msg ' + role;
    div.textContent = text;
    hist.appendChild(div);
    hist.scrollTop = hist.scrollHeight;
    const msgs = loadChatHistory();
    msgs.push({role, text, ts: ts || Date.now()});
    saveChatHistory(msgs);
  }

  function restoreChatHistory() {
    const hist = document.getElementById('chat-history');
    hist.innerHTML = '';
    const msgs = loadChatHistory();
    msgs.forEach(m => {
      const div = document.createElement('div');
      div.className = 'chat-msg ' + m.role;
      div.textContent = m.text;
      hist.appendChild(div);
    });
    hist.scrollTop = hist.scrollHeight;
  }

  function connectChatWs() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    chatWs = new WebSocket(proto + '://' + location.host + '/ws/chat');
    chatWs.onmessage = e => {
      appendChatMsg('blaze', e.data);
    };
    chatWs.onclose = () => {
      chatWs = null;
      setTimeout(connectChatWs, 3000);
    };
    chatWs.onerror = () => chatWs.close();
  }

  window.sendChat = function() {
    const inp = document.getElementById('chat-input');
    const text = inp.value.trim();
    if (!text) return;
    inp.value = '';
    appendChatMsg('pilot', text);
    if (!chatWs || chatWs.readyState !== WebSocket.OPEN) {
      appendChatMsg('sys', '[reconnecting…]');
      connectChatWs();
      setTimeout(() => {
        if (chatWs && chatWs.readyState === WebSocket.OPEN) chatWs.send(contextInjection(text));
      }, 1500);
    } else {
      chatWs.send(contextInjection(text));
    }
  };

  window.clearChat = function() {
    if (!confirm('Clear chat history and start a fresh Blaze session?')) return;
    document.getElementById('chat-history').innerHTML = '';
    try { localStorage.removeItem(CHAT_KEY); } catch(e) {}
    document.cookie = 'skybridge_session=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
    if (chatWs) { try { chatWs.close(); } catch(e) {} chatWs = null; }
    appendChatMsg('sys', '[chat cleared — new Blaze session]');
    connectChatWs();
  };

  // One button collapses BOTH the VHF radio panel and the Blaze chat sidebar.
  // State persists across reloads.
  window.toggleSidePanels = function() {
    const radio = document.getElementById('radioPanel');
    const chat  = document.getElementById('chat-sidebar');
    const btn   = document.getElementById('fSide');
    const collapse = radio && !radio.classList.contains('collapsed');
    if (radio) radio.classList.toggle('collapsed', collapse);
    if (chat)  chat.classList.toggle('collapsed',  collapse);
    if (btn)   btn.classList.toggle('off',          collapse);
    try { localStorage.setItem('skybridge-side-collapsed', collapse ? '1' : '0'); } catch(e) {}
  };

  // Apply persisted side-panel state on load
  (function _initSidePanels(){
    if (localStorage.getItem('skybridge-side-collapsed') !== '1') return;
    const radio = document.getElementById('radioPanel');
    const chat  = document.getElementById('chat-sidebar');
    const btn   = document.getElementById('fSide');
    if (radio) radio.classList.add('collapsed');
    if (chat)  chat.classList.add('collapsed');
    if (btn)   btn.classList.add('off');
  })();

  window.toggleChatSidebar = function() {
    const sb = document.getElementById('chat-sidebar');
    const btn = document.getElementById('chat-sidebar-toggle');
    chatCollapsed = !chatCollapsed;
    sb.classList.toggle('collapsed', chatCollapsed);
    btn.innerHTML = chatCollapsed ? '&#x276F;' : '&#x276E;';
  };

  // Voice input via Web Speech API (webkit prefix for Safari)
  var _voiceRecognition = null;
  window.toggleVoiceInput = function() {
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    var micBtn = document.getElementById('chat-mic');
    if (!SR) {
      appendChatMsg('sys', '[Voice input not supported in this browser]');
      return;
    }
    if (_voiceRecognition) {
      _voiceRecognition.stop();
      _voiceRecognition = null;
      if (micBtn) micBtn.classList.remove('listening');
      return;
    }
    var r = new SR();
    r.lang = 'en-US';
    r.interimResults = false;
    r.maxAlternatives = 1;
    _voiceRecognition = r;
    if (micBtn) micBtn.classList.add('listening');
    r.onresult = function(e) {
      var transcript = e.results[0][0].transcript;
      var inp = document.getElementById('chat-input');
      if (inp) inp.value = (inp.value ? inp.value + ' ' : '') + transcript;
    };
    r.onend = function() {
      _voiceRecognition = null;
      if (micBtn) micBtn.classList.remove('listening');
    };
    r.onerror = function() {
      _voiceRecognition = null;
      if (micBtn) micBtn.classList.remove('listening');
    };
    r.start();
  };

  document.addEventListener('DOMContentLoaded', function() {
    restoreChatHistory();
    connectChatWs();
    document.getElementById('chat-input').addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
    });
  });
})();

// ═══════════════════════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════════════════════
// Sync slider UI to the persisted ICON_SCALE / ICON_BRIGHTNESS / LOOKAHEAD before first render
(function syncIconControlsUI(){
  const s = document.getElementById('iconScaleSlider');
  const v = document.getElementById('iconScaleValue');
  if (s) s.value = ICON_SCALE.toFixed(1);
  if (v) v.textContent = ICON_SCALE.toFixed(1) + '×';
  const bs = document.getElementById('iconBrightnessSlider');
  const bv = document.getElementById('iconBrightnessValue');
  if (bs) bs.value = ICON_BRIGHTNESS.toFixed(2);
  if (bv) bv.textContent = ICON_BRIGHTNESS.toFixed(2) + '×';
  const ls = document.getElementById('lookaheadSlider');
  const lv = document.getElementById('lookaheadValue');
  if (ls) ls.value = LOOKAHEAD_MIN;
  if (lv) lv.textContent = LOOKAHEAD_MIN + ' min';
  const ts_ = document.getElementById('trailRenderSlider');
  const tv = document.getElementById('trailRenderValue');
  if (ts_) ts_.value = TRAIL_RENDER_MIN;
  if (tv) tv.textContent = TRAIL_RENDER_MIN + ' min';
  // Rewind scrubber bar: sync UI to TRAIL_REWIND_MIN (always starts 0/LIVE)
  applyRewind(TRAIL_REWIND_MIN);
  // Polygon opacity slider sync
  const pa = document.getElementById('polyAlphaSlider');
  const pv = document.getElementById('polyAlphaValue');
  if (pa) pa.value = POLY_ALPHA_PCT;
  if (pv) pv.textContent = POLY_ALPHA_PCT + ' %';
  // Apply persisted opacity once polygons render in
  setTimeout(_applyPolyAlphaToAll, 800);
  // Panel background opacity slider sync
  const ka = document.getElementById('panelAlphaSlider');
  const kv = document.getElementById('panelAlphaValue');
  if (ka) ka.value = PANEL_ALPHA_PCT;
  if (kv) kv.textContent = PANEL_ALPHA_PCT + ' %';
  // Per-chart-layer opacity slider sync (3 sliders)
  [
    ['vfrOpSlider',     'vfrOpValue',     VFR_OPACITY],
    ['ifrLowOpSlider',  'ifrLowOpValue',  IFR_LOW_OPACITY],
    ['ifrHighOpSlider', 'ifrHighOpValue', IFR_HIGH_OPACITY],
  ].forEach(([sid, lid, op]) => {
    const s = document.getElementById(sid);
    const l = document.getElementById(lid);
    const pct = Math.round(op * 100);
    if (s) s.value = pct;
    if (l) l.textContent = pct + ' %';
  });
})();
initMap();
document.getElementById('destIn').addEventListener('keydown',e=>{if(e.key==='Enter')setDest();});
pollFast(); pollSlow(); loadWxText();
setInterval(pollFast, 5000);      // traffic + radio: 5s
setInterval(pollSlow, 300000);    // wx overlays: 5min
</script>
<div class="grid-stack" id="dashboard-grid" style="display:none"></div>

<div id="chat-sidebar">
  <button id="chat-sidebar-toggle" onclick="toggleChatSidebar()" title="Toggle chat">&#x276E;</button>
  <div id="chat-header"><span>&#x1F9E0; Blaze</span><button id="chat-clear-btn" onclick="clearChat()" title="Clear chat history and start a new session">Clear</button></div>
  <div id="chat-history"></div>
  <div id="chat-input-row">
    <textarea id="chat-input" rows="2" placeholder="Ask Blaze..."></textarea>
    <button id="chat-mic" onclick="toggleVoiceInput()" title="Push to talk">&#x1F3A4;</button>
    <button id="chat-send" onclick="sendChat()">Send</button>
  </div>
</div>
</body>
</html>"""


@sock.route("/ws/chat")
def ws_chat(ws):
    """WebSocket chat — proxies pilot messages to OpenClaw Blaze agent."""
    # Derive session_id from cookie or generate a new one
    environ = ws.environ
    cookie_header = environ.get("HTTP_COOKIE", "")
    session_id = None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("skybridge_session="):
            session_id = part[len("skybridge_session="):].strip()
            break
    if not session_id:
        session_id = str(uuid.uuid4())

    while True:
        try:
            msg = ws.receive(timeout=120)
        except Exception:
            break
        if msg is None:
            break
        try:
            result = subprocess.run(
                ["openclaw", "agent", "--agent", "main",
                 "--session-id", session_id,
                 "--json", "--timeout", "90",
                 "-m", msg],
                capture_output=True, text=True, timeout=100
            )
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    reply = data.get("text") or data.get("content") or result.stdout.strip()
                except Exception:
                    reply = result.stdout.strip()
            else:
                reply = f"[error] {result.stderr.strip() or 'agent returned non-zero exit'}"
        except subprocess.TimeoutExpired:
            reply = "[error] agent timed out (LLM > 100 s — try again, the model may be cold)"
        except Exception as e:
            reply = f"[error] {e}"
        ws.send(reply)


if __name__ == "__main__":
    _ensure_ticker_started()
    app.run(host=os.environ.get('KNEEBOARD_HOST', '100.108.6.51'), port=8084, debug=False)
