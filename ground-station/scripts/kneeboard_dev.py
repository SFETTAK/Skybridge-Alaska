#!/usr/bin/env python3
"""
SkyBridge Kneeboard — Pilot-facing web app
Tablet-optimized moving map with ADS-B, weather, and VHF transcript overlays.
Designed for one-handed operation in the cockpit.
Serves on port 8083.
"""

import datetime
import json
import math
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
import urllib.request
import urllib.error

from flask import Flask, jsonify, request, make_response, redirect
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
    """Merge local readsb (always fresh) with cached ADSB.fi (statewide fill).
    Local readsb is read every call — it's a local file that updates every
    second and is free. ADSB.fi is cached at _ADSB_FI_TTL to respect their
    rate limit. Previously the entire merged result was cached, which made
    locally-tracked planes appear frozen between cache refreshes."""
    import time
    now = time.time()
    merged = {}

    # 1) Local readsb — ALWAYS fresh, every call. Wins on hex collisions.
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

    # 2) ADSB.fi — cached 8s to stay under the rate limit. Only its remote
    #    leg is cached; the merged-with-readsb output is recomputed each call.
    if _ADSB_FI_CACHE["data"] and (now - _ADSB_FI_CACHE["ts"]) < _ADSB_FI_TTL:
        adsbfi_aircraft = _ADSB_FI_CACHE["data"]
    else:
        adsbfi_aircraft = []
        for lat, lon, dist in _ADSB_FI_CIRCLES:
            try:
                url = f"https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{dist}"
                req = urllib.request.Request(url, headers={"User-Agent": "SkyBridge/1.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read())
                adsbfi_aircraft.extend(data.get("aircraft", []))
            except Exception as e:
                print(f"ADSB.fi fetch error ({lat},{lon},{dist}): {e}")
        _ADSB_FI_CACHE["data"] = adsbfi_aircraft
        _ADSB_FI_CACHE["ts"] = now

    # 3) Merge ADSB.fi into the local-first map
    for ac in adsbfi_aircraft:
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

    return list(merged.values())


# ── API ──────────────────────────────────────────────────────────────────────

# ───────────────────────── PUBLIC-FACING CHROME ─────────────────────────
# Frozen-fork dashboards under /public/<name>. Dev originals (/icons-preview,
# /wx-icons-preview, /wx-validate, /wx-shootout) stay the active surface.
# When you want to re-publish, re-run /tmp/fork_public_v2.py from a clean
# checkout of the dev pages.

_PUBLIC_NAV_LINKS = [
    ("/public/wx-shootout",      "Weather Shootout"),
    ("/public/wx-validate",      "Comp Validate"),
    ("/public/wx-icons-preview", "HUD Icons"),
    ("/public/icons-preview",    "ADS-B Icons"),
]

def _public_nav_html(active_path):
    items = "".join(
        f'<a href="{r}" class="sb-nl{(" active" if r == active_path else "")}">{label}</a>'
        for r, label in _PUBLIC_NAV_LINKS
    )
    # Kneeboard cross-link with LAN-bypass JS — lets visitors on a dashboard
    # jump to the kneeboard. Raw-IP access goes direct to :8084, hostname
    # access goes through Caddy/Authelia.
    kb_button = (
        '<a href="/" id="sb-kb-nav-link" class="sb-nl sb-kb" '
        'title="Open kneeboard (dev build · authorized testers)">'
        '&#9992; Kneeboard '
        '<span class="sb-kb-tag">DEV</span>'
        '</a>'
    )
    kb_script = (
        '<script>'
        '(function(){'
          # Always send Kneeboard to /kneeboard. Caddy's bare-/ redirect
          # doesn't catch this path. Flask's _public_auth_before will
          # prompt for the password if needed.
          'var link=document.getElementById("sb-kb-nav-link");'
          'if(link){'
            'link.href="/kneeboard";'
            'link.title="Open kneeboard (login required)";'
          '}'
        '})();'
        '</script>'
    )
    # Brand link points to the PREVIEW landing page on :8086 while we test.
    # When the new landing page is ported into kneeboard_dev.py and lives at
    # /public on :8084, change this back to "/public".
    return (
        '<nav class="sb-public-nav">'
          '<a href="/public" class="sb-brand" title="Project landing page">'
            '<span class="sb-mark">★</span>'
            '<span class="sb-text">SkyBridge Alaska</span>'
            '<span class="sb-tag">BETA</span>'
          '</a>'
          f'<div class="sb-links">{kb_button}{items}</div>'
          '<div class="sb-meta">'
            '<a href="https://github.com/SFETTAK/Skybridge-Alaska" target="_blank" rel="noopener">github</a>'
            '<span class="sb-dot">·</span>'
            '<span>open source</span>'
          '</div>'
        '</nav>'
        f'{kb_script}'
    )

_PUBLIC_FOOTER_HTML = (
    '<footer class="sb-public-footer">'
    'SkyBridge Alaska · Open-source aviation weather network for Alaska · '
    '<a href="https://github.com/SFETTAK/Skybridge-Alaska" target="_blank" rel="noopener">github</a> '
    '· public beta'
    '</footer>'
)

_PUBLIC_NAV_CSS = """\
<style id="sb-public-chrome-css">
:root {
  --sb-bg: #0a0e16; --sb-panel: #141a26; --sb-line: #2a3140;
  --sb-text: #d8e1ec; --sb-dim: #9aa5b8; --sb-brand: #23d18b;
  --sb-accent: #0090ff; --sb-warn: #ffaa00;
}
.sb-public-nav {
  display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  background: linear-gradient(180deg, #0d121d 0%, rgba(10,14,22,0.85) 100%);
  border-bottom: 1px solid var(--sb-line);
  padding: 10px 22px; position: sticky; top: 0; z-index: 9999;
  backdrop-filter: blur(10px);
  font-family: system-ui, sans-serif;
}
.sb-public-nav .sb-brand {
  display: flex; align-items: center; gap: 8px;
  text-decoration: none; color: var(--sb-text); font-weight: 700;
}
.sb-public-nav .sb-mark { color: var(--sb-brand); font-size: 18px; }
.sb-public-nav .sb-text { letter-spacing: 1px; font-size: 13px; }
.sb-public-nav .sb-tag {
  background: var(--sb-warn); color: #000; font-size: 9px; padding: 2px 6px;
  border-radius: 3px; font-weight: 800; letter-spacing: 1px;
}
.sb-public-nav .sb-links { display: flex; gap: 4px; flex-wrap: wrap; }
.sb-public-nav .sb-nl {
  color: var(--sb-dim); padding: 6px 12px; border-radius: 6px;
  text-decoration: none; font-size: 12px; font-weight: 600;
  letter-spacing: 0.5px; transition: all 0.15s;
}
.sb-public-nav .sb-nl:hover { background: var(--sb-panel); color: var(--sb-text); }
.sb-public-nav .sb-nl.active { background: var(--sb-accent); color: #0a0e16; }
.sb-public-nav .sb-nl.sb-kb {
  background: rgba(35,209,139,0.08); color: var(--sb-brand);
  border: 1px solid rgba(35,209,139,0.3); margin-right: 6px;
}
.sb-public-nav .sb-nl.sb-kb:hover {
  background: rgba(35,209,139,0.18); color: #00ffaa;
  border-color: var(--sb-brand);
}
.sb-public-nav .sb-nl.sb-kb .sb-kb-tag {
  display: inline-block; background: #2a3140; color: var(--sb-dim);
  font-size: 8px; padding: 1px 5px; border-radius: 3px;
  font-weight: 800; letter-spacing: 1px; margin-left: 4px; vertical-align: middle;
}
.sb-public-nav .sb-meta {
  margin-left: auto; display: flex; gap: 6px; align-items: center;
  color: var(--sb-dim); font-size: 11px;
}
.sb-public-nav .sb-meta a { color: var(--sb-accent); text-decoration: none; }
.sb-public-nav .sb-meta a:hover { text-decoration: underline; }
.sb-public-nav .sb-dot { color: #445; }
.sb-public-footer {
  margin-top: 40px; padding: 18px 22px; border-top: 1px solid #2a3140;
  background: #0d121d; color: #9aa5b8; font-size: 11px; text-align: center;
  font-family: system-ui, sans-serif;
}
.sb-public-footer a { color: #0090ff; text-decoration: none; }
.sb-public-footer a:hover { text-decoration: underline; }
.beta {
  background: #0090ff; color: #0a0e16;
  padding: 2px 8px; border-radius: 4px; font-size: 11px;
  margin-left: 8px; vertical-align: middle; font-weight: 700;
  letter-spacing: 1px;
}
</style>
"""

def _publicize(html, active_path):
    """Post-process a public dashboard's HTML response: inject chrome CSS,
    nav, and footer at the placeholder anchors. Active-path drives which
    nav link is highlighted."""
    return (html
        .replace('<!--SBNAVCSS-->', _PUBLIC_NAV_CSS)
        .replace('<!--SBNAV-->',    _public_nav_html(active_path))
        .replace('<!--SBFOOTER-->', _PUBLIC_FOOTER_HTML))


# Per-card interpretive guide. Keys must match _PUBLIC_NAV_LINKS paths.
_DASHBOARD_GUIDES = {
    '/public/wx-shootout':
        "Each weather source rendered as itself in its own color on a single map. "
        "Where colors agree, sources agree. Where they fan out, model disagreement is "
        "happening — useful for spotting frontal passages and observation-poor regions.",
    '/public/wx-validate':
        "30-day rolling agreement statistics for every ingested source against the "
        "METAR baseline. High MAE means a source is drifting from ground truth at "
        "that anchor.",
    '/public/wx-icons-preview':
        "Design preview of weather symbology used on the in-development pilot kneeboard.",
    '/public/icons-preview':
        "Design preview of aircraft category icons. Silhouettes by category, color by "
        "altitude band, outline by operator class.",
}


@app.route("/public", strict_slashes=False)
def public_index():
    cards = ""
    for r, label in _PUBLIC_NAV_LINKS:
        guide = _DASHBOARD_GUIDES.get(r, "")
        cards += (
            f'<a class="dash-card" href="{r}">'
            f'<h3>{label}</h3>'
            f'<p class="cpath">{r}</p>'
            f'<p class="cguide">{guide}</p>'
            f'</a>'
        )

    body = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SkyBridge Alaska — Project Dashboard</title>
<!--SBNAVCSS-->
<style>
  body {{ background: #0a0e16; color: #d8e1ec; font-family: system-ui, -apple-system, sans-serif;
         margin: 0; padding: 0; min-height: 100vh; display: flex; flex-direction: column; line-height: 1.5; }}
  main {{ max-width: 980px; margin: 0 auto; padding: 40px 22px 80px; flex: 1; width: 100%; box-sizing: border-box; }}
  a {{ color: #23d18b; text-decoration: none; }}
  a:hover {{ color: #00ffaa; }}
  code {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px;
          background: #141a26; padding: 1px 6px; border-radius: 3px; color: #d8e1ec; }}

  h1 {{ color: #23d18b; font-size: 30px; letter-spacing: 2px; margin: 0 0 10px; }}
  .lede {{ color: #d8e1ec; font-size: 15px; max-width: 760px; line-height: 1.65; margin: 0 0 18px; }}
  .lede .muted {{ color: #9aa5b8; }}
  section {{ margin-top: 44px; }}
  section > h2 {{ color: #23d18b; font-size: 13px; text-transform: uppercase;
                  letter-spacing: 2px; margin: 0 0 14px; }}
  section p, section li {{ color: #c8d2e2; font-size: 14px; line-height: 1.65; margin: 0 0 10px; }}
  section .muted {{ color: #9aa5b8; }}
  section a {{ border-bottom: 1px dotted #23d18b66; }}

  .pills {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 18px 0 0; }}
  .pill {{ background: #141a26; border: 1px solid #2a3140; border-radius: 999px;
           padding: 6px 12px; font-size: 12px; color: #9aa5b8; }}
  .pill strong {{ color: #23d18b; font-weight: 600; }}
  .pill.beta strong {{ color: #f5a623; }}

  .strip {{ background: #0d1a2e; border: 1px solid #1d2a44; border-radius: 8px;
            padding: 12px 18px; margin: 24px 0 0; display: flex; flex-wrap: wrap;
            gap: 18px; font-size: 12px; color: #9aa5b8; align-items: center; }}
  .strip .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%;
                 background: #23d18b; margin-right: 6px;
                 animation: pulse 2s ease-in-out infinite; }}
  .strip .dot.amber {{ background: #f5a623; }}
  .strip .dot.grey {{ background: #4a5568; animation: none; }}
  .strip strong {{ color: #d8e1ec; font-weight: 600; }}
  @keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}

  .qmap {{ background: #141a26; border: 1px solid #2a3140; border-radius: 8px; padding: 14px 18px; }}
  .qmap dl {{ margin: 0; display: grid; grid-template-columns: auto 1fr; column-gap: 16px; row-gap: 8px; }}
  .qmap dt {{ color: #9aa5b8; font-size: 13px; }}
  .qmap dd {{ margin: 0; font-size: 13px; }}

  .deploy-list {{ list-style: none; padding: 0; margin: 0 0 14px; }}
  .deploy-list li {{ padding-left: 26px; position: relative; margin: 0 0 8px;
                     font-size: 13px; line-height: 1.55; color: #c8d2e2; }}
  .deploy-list li::before {{ position: absolute; left: 0; top: 0; font-weight: 700; font-size: 14px; }}
  .deploy-list li.ok::before    {{ content: "✓"; color: #23d18b; }}
  .deploy-list li.amber::before {{ content: "○"; color: #f5a623; }}
  .deploy-list li.grey::before  {{ content: "~"; color: #6b7689; }}
  h4.dl-heading {{ font-size: 11px; color: #9aa5b8; text-transform: uppercase;
                   letter-spacing: 1.5px; margin: 22px 0 10px; font-weight: 600; }}

  .safety {{ background: #1f1810; border: 1.5px solid #f5a623; border-radius: 10px;
             padding: 18px 22px; }}
  .safety h2 {{ color: #f5a623 !important; font-size: 14px; margin: 0 0 10px;
                letter-spacing: 1.5px; }}
  .safety p {{ color: #e6d5b8; }}
  .safety strong {{ color: #f5a623; }}
  .safety a {{ color: #f5a623; border-bottom: 1px dotted #f5a62366; }}

  a.pilot-status {{ display: block; text-decoration: none; color: inherit;
                    background: #141a26; border: 1px solid #2a3140; border-radius: 10px;
                    padding: 18px 22px; margin: 0 0 18px;
                    border-left: 3px solid #6b7689; transition: all 0.2s; }}
  a.pilot-status:hover {{ border-color: #23d18b; border-left-color: #23d18b;
                          transform: translateY(-1px); }}
  a.pilot-status:hover .open-arrow {{ color: #23d18b; transform: translateX(2px); }}
  .pilot-status h3 {{ color: #d8e1ec; margin: 0 0 4px; font-size: 16px; }}
  .pilot-status .badge {{ display: inline-block; background: #2a3140; color: #9aa5b8;
                          font-size: 10px; font-weight: 700; letter-spacing: 1.5px;
                          padding: 2px 8px; border-radius: 3px; text-transform: uppercase;
                          margin-left: 8px; vertical-align: middle; }}
  .pilot-status .open-arrow {{ float: right; color: #6b7689; font-size: 18px;
                               transition: all 0.2s; }}
  .pilot-status p {{ color: #9aa5b8; font-size: 13px; margin: 0; line-height: 1.55; }}

  .dash-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }}
  .dash-card {{ background: #141a26; border: 1px solid #2a3140; border-radius: 10px;
                padding: 18px; text-decoration: none; color: #d8e1ec; transition: all 0.2s; display: block; }}
  .dash-card:hover {{ border-color: #23d18b; transform: translateY(-2px); }}
  .dash-card h3 {{ color: #23d18b; font-size: 16px; margin: 0 0 4px; }}
  .dash-card .cpath {{ color: #9aa5b8; font-family: ui-monospace, monospace; font-size: 11px; margin: 0 0 10px; }}
  .dash-card .cguide {{ color: #9aa5b8; font-size: 12px; line-height: 1.55; margin: 0; }}

  .phase {{ margin: 0 0 18px; }}
  .phase h4 {{ color: #d8e1ec; font-size: 13px; letter-spacing: 1px; margin: 0 0 6px;
               text-transform: uppercase; }}
  .phase ul {{ list-style: none; padding: 0; margin: 0; }}
  .phase ul li {{ padding-left: 22px; position: relative; font-size: 13px;
                  color: #c8d2e2; margin: 0 0 4px; }}
  .phase ul li::before {{ position: absolute; left: 0; top: 0; }}
  .phase ul li.ok::before {{ content: "✓"; color: #23d18b; font-weight: 700; }}
  .phase ul li.todo::before {{ content: "○"; color: #6b7689; }}

  .engage {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
  .engage-card {{ background: #141a26; border: 1px solid #2a3140; border-radius: 8px; padding: 16px; }}
  .engage-card h4 {{ color: #23d18b; font-size: 13px; text-transform: uppercase;
                     letter-spacing: 1.5px; margin: 0 0 8px; }}
  .engage-card p {{ color: #9aa5b8; font-size: 12px; margin: 0; line-height: 1.55; }}

  .reading {{ list-style: none; padding: 0; margin: 0; }}
  .reading li {{ padding-left: 18px; position: relative; font-size: 13px; margin: 0 0 8px; }}
  .reading li::before {{ content: "→"; position: absolute; left: 0; color: #23d18b; }}
</style></head><body>
<!--SBNAV-->
<main>

  <section style="margin-top: 0;">
    <h1>SkyBridge Alaska</h1>
    <p class="lede">
      A multi-source weather and aviation-data network for Alaska. SkyBridge ingests
      every authoritative weather source available — METARs, NWS gridded forecasts,
      NOAA GFS, ECMWF, GEM, JMA, and the Montis MWOS network — and renders each as
      itself, side by side, on dashboards for stakeholders, reviewers, and the
      eventual in-flight pilot product.
      <span class="muted">SkyBridge does not modify authoritative data; it makes more
      sources visible.</span>
    </p>
    <div class="pills">
      <span class="pill"><strong>STATUS:</strong> One ground station operational at
        Alaska DOT&amp;PF · multi-station deployment is the next phase</span>
      <span class="pill beta"><strong>STAGE:</strong> Beta · active development</span>
      <span class="pill"><strong>THIS PAGE:</strong> Project briefing dashboard, not for in-flight use</span>
    </div>
  </section>

  <div class="strip" id="sb-strip">
    <span><span class="dot grey" id="sb-dot"></span><span id="sb-summary">Loading live status…</span></span>
    <span id="sb-sources-wrap" hidden>sources online <strong id="sb-sources">—</strong></span>
    <span id="sb-aircraft-wrap" hidden>aircraft tracked <strong id="sb-aircraft">—</strong></span>
    <span id="sb-lastupdate-wrap" hidden>last update <strong id="sb-lastupdate">—</strong></span>
    <span id="sb-uptime-wrap" hidden>uptime <strong id="sb-uptime">—</strong></span>
  </div>

  <section id="safety">
    <div class="safety">
      <h2>⚠ Static reference — not for in-flight use</h2>
      <p>This page is a project briefing dashboard. The dashboards below show
         <strong>static reference views, multi-source comparisons, and accuracy statistics</strong>.
         They are NOT real-time pilot decision-support tools.</p>
      <p>The in-flight pilot kneeboard (live moving map, real-time ADS-B, MWOS cameras,
         VHF transcripts) is a separate product currently in active development and used only
         by authorized testers. It will graduate to wider deployment when accuracy attestation
         and operational testing support that.</p>
      <p><strong>Pilots:</strong> make go/no-go decisions using FAA-approved weather sources.
         SkyBridge is supplementary information that adds context where official sources have
         coverage gaps. Always cross-check.</p>
      <p><strong>Regulatory posture:</strong> SkyBridge is not currently FAA-certified or
         TSO-approved. The certification path is described in
         <a href="https://github.com/SFETTAK/Skybridge-Alaska/blob/main/paper-lab/08-cert-path.md" target="_blank" rel="noopener">paper-lab/08-cert-path.md ↗</a>.</p>
    </div>
  </section>

  <section id="dashboards">
    <h2>Live dashboards</h2>

    <a class="pilot-status" id="sb-kb-card-link" href="/" title="Open kneeboard development build">
      <h3>Pilot Kneeboard <span class="badge">In Development</span> <span class="open-arrow">→</span></h3>
      <p>Live moving-map cockpit dashboard for pilots: real-time ADS-B traffic, weather
         overlays, VHF audio transcripts, station feeds. This is a <em>separate product</em>
         from the project dashboard you are on now, and is not yet ready for in-flight use.
         Authorized testers have access to development builds. Click to open the development
         kneeboard. Status: not deployed for general flight use.</p>
    </a>
    <script>
      // Always send Pilot Kneeboard to /kneeboard. Flask gates this with the
      // shared password; the bare-/ Caddy redirect to /public doesn't catch it.
      (function() {{
        var link = document.getElementById('sb-kb-card-link');
        if (link) {{
          link.href = '/kneeboard';
          link.title = 'Open kneeboard (login required)';
        }}
      }})();
    </script>

    <div class="dash-cards">{cards}</div>
  </section>

  <section>
    <h2>What's on this page</h2>
    <div class="qmap">
      <dl>
        <dt>Why does this exist?</dt><dd><a href="#problem">→ The Problem</a></dd>
        <dt>What's actually deployed?</dt><dd><a href="#deployed">→ What's Working Today</a></dd>
        <dt>How does it work?</dt><dd><a href="#architecture">→ How It Works</a></dd>
        <dt>Where is this going?</dt><dd><a href="#roadmap">→ Roadmap</a></dd>
        <dt>How accurate is the data?</dt><dd><a href="#accuracy">→ Accuracy &amp; Audit</a></dd>
        <dt>How do I help?</dt><dd><a href="#engage">→ How to Engage</a></dd>
        <dt>Want the deep technical dive?</dt><dd><a href="https://github.com/SFETTAK/Skybridge-Alaska/tree/main/paper-lab" target="_blank" rel="noopener">→ paper-lab on GitHub ↗</a></dd>
      </dl>
    </div>
  </section>

  <section id="problem">
    <h2>The problem SkyBridge is designed to address</h2>
    <p>Alaska is the most dangerous place to fly a small aircraft in the United States.
       General-aviation pilots in Alaska are statistically <strong>36 times</strong> more
       likely to die on the job than the average American worker
       <span class="muted">[CDC/NIOSH via Washington Post 2014]</span>.</p>
    <p>Roughly <strong>80%</strong> of Alaska has no reliable real-time weather coverage and
       significant gaps in VHF radio coverage
       <span class="muted">[Alaska DOT&amp;PF Aviation Gap Analysis, March 2024]</span>.
       Mountain passes, coastal villages, and the entire Arctic Slope have observation
       gaps where the official FAA AWOS / ASOS network has no coverage at all. Where it
       does have coverage, FAA 2023 data shows approximately one in three weather
       stations was experiencing some type of outage on an average day — significant
       enough that the Alaska State Legislature has petitioned Congress to address it.</p>
    <p>Traditional infrastructure (FAA AWOS at $200K+ per site) cannot economically close
       this gap. Decade-old federal mapping programs remain stuck in continuing-resolution
       funding cycles. SkyBridge takes a different approach: commodity hardware,
       open-source software, and aggregation of public data feeds.</p>
  </section>

  <section id="deployed">
    <h2>What's working today (DOT-VHF, Anchorage Bowl)</h2>
    <ul class="deploy-list">
      <li class="ok">One operational ground station running 40+ services</li>
      <li class="ok">VHF voice transcription pipeline (channelized AM demod, adaptive squelch,
        on-device speech recognition, ATC lexicon post-processing, ADS-B callsign correlation)</li>
      <li class="ok">ADS-B reception (1090 MHz local + 978 UAT local)</li>
      <li class="ok">Statewide ADS-B aggregation (local + ADSB.fi merge, ~500 nm)</li>
      <li class="ok">Multi-source weather ingestion (NWS, NOAA, ECMWF, GEM, JMA, Montis MWOS)</li>
      <li class="ok">Multi-source side-by-side display dashboards (Wx Shootout, Wx Validate)</li>
      <li class="ok">Continuous accuracy comparison vs METAR baseline</li>
      <li class="ok">Audit log (every config change timestamped + logged)</li>
    </ul>

    <h4 class="dl-heading">Node hardware</h4>
    <ul class="deploy-list">
      <li class="ok">SkyBridge nodes run on commodity single-board computers. The hardware
        choice is what makes per-station cost roughly $500 instead of a six-figure
        traditional weather installation. The software, not the hardware, is the project.</li>
    </ul>

    <h4 class="dl-heading">In active development</h4>
    <ul class="deploy-list">
      <li class="amber">Pilot kneeboard (in-flight moving-map product)</li>
      <li class="amber">Multi-station mesh deployment</li>
      <li class="amber">Mobile app</li>
      <li class="amber">Sensor expansion to remote sites</li>
      <li class="amber">NOTAM ingestion (FAA API key registration pending)</li>
      <li class="amber">TFR polygon shapes (blocked on FAA public API)</li>
      <li class="amber">TAIGA encoding integration</li>
    </ul>

    <h4 class="dl-heading">Documented architecture, not yet built</h4>
    <ul class="deploy-list">
      <li class="grey">Pilot-to-pilot mesh data muling</li>
      <li class="grey">Cabin / remote-village node delivery via overflight</li>
      <li class="grey">Volatility-driven adaptive refresh</li>
    </ul>
  </section>

  <section id="architecture">
    <h2>How it works</h2>
    <p>SkyBridge is a <strong>hybrid network</strong>, not a mesh network with internet on the side.</p>
    <p>Internet (cellular, wifi, edge tunnels, Starlink, Iridium) is the spine that connects
       regional clusters to a central hub. LoRa mesh (Meshtastic) is last-mile distribution
       within a region — and the fallback when the spine is cut.
       <span class="muted">Mesh is the safety net, not the primary delivery channel.</span></p>
    <p>Concretely:</p>
    <ul>
      <li>Each region has a ground station that listens on VHF, receives ADS-B, ingests
          internet weather feeds, transcribes voice, and serves a moving-map kneeboard to
          pilots over LAN — and over Meshtastic LoRa for pilots out of cellular range.</li>
      <li>A central hub aggregates across regions and synthesizes statewide views.</li>
      <li>When a pilot moves between regions, their device subscribes to the new region's
          data on arrival.</li>
      <li>When the spine is cut, regional ground stations keep working independently.
          Cross-region synthesis pauses; local data continues to flow.</li>
    </ul>
    <p>This is the steady-state architecture as designed; with one site live today,
       regional independence is a property of the system that becomes visible as
       additional ground stations deploy.</p>
  </section>

  <section id="roadmap">
    <h2>Roadmap</h2>

    <div class="phase">
      <h4>Phase 0 — today</h4>
      <ul>
        <li class="ok">DOT-VHF station operational</li>
        <li class="ok">Paper-lab v0.1 published to GitHub</li>
        <li class="ok">Public mirror via Cloudflare Tunnel</li>
        <li class="ok">Auth gateway hardened</li>
        <li class="ok">Project briefing dashboard live</li>
      </ul>
    </div>

    <div class="phase">
      <h4>Phase 1 — next</h4>
      <ul>
        <li class="todo">Multi-station deployment (research proposal submitted)</li>
        <li class="todo">Statewide anchor expansion</li>
        <li class="todo">TAIGA wire encoding integrated</li>
        <li class="todo">First quarterly accuracy attestation report</li>
        <li class="todo">Pilot kneeboard graduates to authorized-tester deployment</li>
      </ul>
    </div>

    <div class="phase">
      <h4>Phase 2 — after that</h4>
      <ul>
        <li class="todo">Bench test simulator built and validated</li>
        <li class="todo">First peer-review submission (NASA Technical Memorandum series)</li>
        <li class="todo">First Part 135 / DOT&amp;PF formal pilot deployment</li>
      </ul>
    </div>

    <div class="phase">
      <h4>Phase 3 — longer term</h4>
      <ul>
        <li class="todo">Independent third-party accuracy audit</li>
        <li class="todo">FAA AAAI working-group engagement</li>
        <li class="todo">Recognition as supplementary information source</li>
        <li class="todo">Multi-state deployment exploration</li>
      </ul>
    </div>

    <p class="muted" style="font-size: 12px;">Each phase has explicit gates. Phase N+1 does not start until Phase N's empirical evidence is on the public mirror.</p>
  </section>

  <section id="accuracy">
    <h2>Accuracy &amp; audit — how you can verify</h2>
    <p><strong>Provenance</strong> — every value SkyBridge displays is traceable to its source.
       The Wx Shootout and Wx Validate dashboards make this visible: each source rendered as
       itself, with its name attached.</p>
    <p><strong>Continuous comparison</strong> — every ingested source is compared continuously
       against the METAR baseline (the FAA-approved ground truth). 30-day rolling MAE,
       signed bias, median error, 95th percentile, drift trend. Per anchor. Per source. Per field.
       A reader can verify any claim about accuracy by querying it themselves.</p>
    <p><strong>Audit log</strong> — every meaningful event logged: config changes, source health
       transitions, schema events, network events. Permanent retention. Read-only access for
       authenticated auditors.</p>
    <p>What SkyBridge does <em>not</em> do: modify authoritative data. Each source is shown as
       itself; the comparison is research transparency, not a derived product.</p>
  </section>

  <section id="engage">
    <h2>How to engage</h2>
    <div class="engage">
      <div class="engage-card">
        <h4>Review the work</h4>
        <p>Read the paper-lab on GitHub. Open issues with questions. Open-source under
           AGPL-3.0 with a commercial option.
           <a href="https://github.com/SFETTAK/Skybridge-Alaska/tree/main/paper-lab" target="_blank" rel="noopener">paper-lab ↗</a></p>
      </div>
      <div class="engage-card">
        <h4>Contribute code</h4>
        <p>PRs welcome. Architecture is documented; the code is small enough to read in an
           afternoon. Start with the network topology and protocol stack docs.</p>
      </div>
      <div class="engage-card">
        <h4>Host a station</h4>
        <p>If you operate an airport, hangar, or remote facility and want to host a regional
           ground station, contact the maintainers. Multi-station validation is the Phase 1
           deliverable.</p>
      </div>
      <div class="engage-card">
        <h4>Partner</h4>
        <p>Other states with similar terrain (Montana, Idaho, Wyoming, Maine) and Alaska's
           regional consortia face structurally similar problems. The architecture is portable.</p>
      </div>
      <div class="engage-card">
        <h4>Fund</h4>
        <p>Phase 1 is funding-dependent. Federal SMART Grant adjacent work operates in the
           same operational space. Talk to the maintainers about funding paths.</p>
      </div>
      <div class="engage-card">
        <h4>Contact</h4>
        <p>Steven Fett — <a href="mailto:steven.fett@alaska.gov">steven.fett@alaska.gov</a><br>
           <a href="https://github.com/SFETTAK/Skybridge-Alaska" target="_blank" rel="noopener">github.com/SFETTAK/Skybridge-Alaska ↗</a></p>
      </div>
    </div>
  </section>

  <section>
    <h2>Background reading</h2>
    <ul class="reading">
      <li><a href="https://github.com/SFETTAK/Skybridge-Alaska" target="_blank" rel="noopener">Project repository (paper-lab, code, docs) ↗</a></li>
      <li><a href="https://github.com/SFETTAK/Skybridge-Alaska/blob/main/docs/aviation-safety-context.md" target="_blank" rel="noopener">Aviation safety context — sourced statistics ↗</a></li>
      <li><a href="https://github.com/SFETTAK/Skybridge-Alaska/blob/main/paper-lab/08-cert-path.md" target="_blank" rel="noopener">Certification path (paper-lab) ↗</a></li>
      <li><a href="https://dot.alaska.gov/stwdav/" target="_blank" rel="noopener">Alaska DOT&amp;PF Statewide Aviation ↗</a> — contact for Gap Analysis (March 2024)</li>
      <li><a href="https://www.alaskaasp.com/media/4935/2024-12-10_weather_white_paper_final.pdf" target="_blank" rel="noopener">Aviation Weather Reporting in Alaska — Update Dec 2024 ↗</a> (white paper covering FAA AWOS / ASOS outage rates and statewide gaps)</li>
    </ul>
  </section>

</main>
<!--SBFOOTER-->

<script>
(function() {{
  function fmtAge(seconds) {{
    if (seconds == null || isNaN(seconds)) return '—';
    if (seconds < 60) return Math.round(seconds) + 's ago';
    if (seconds < 3600) return Math.round(seconds/60) + 'm ago';
    if (seconds < 86400) return Math.round(seconds/3600) + 'h ago';
    return Math.round(seconds/86400) + 'd ago';
  }}
  function fmtUptime(seconds) {{
    if (seconds == null || isNaN(seconds)) return '—';
    if (seconds < 3600) return Math.round(seconds/60) + 'm';
    if (seconds < 86400) return Math.round(seconds/3600) + 'h';
    return Math.round(seconds/86400) + 'd';
  }}
  function setText(id, v) {{
    var el = document.getElementById(id);
    if (el) el.textContent = (v == null || v === '') ? '—' : v;
  }}
  function showMetric(wrapId, value, formatter) {{
    var wrap = document.getElementById(wrapId);
    if (!wrap) return;
    if (value == null || (typeof value === 'number' && isNaN(value))) {{
      wrap.hidden = true;
      return;
    }}
    wrap.hidden = false;
    var strongs = wrap.getElementsByTagName('strong');
    if (strongs.length) {{
      strongs[0].textContent = formatter ? formatter(value) : String(value);
    }}
  }}
  function refresh() {{
    fetch('/api/wx/status', {{cache: 'no-store'}})
      .then(function(r) {{ return r.ok ? r.json() : null; }})
      .then(function(d) {{
        if (!d) {{
          setText('sb-summary', 'Status unavailable');
          var dot = document.getElementById('sb-dot');
          if (dot) {{ dot.className = 'dot grey'; }}
          return;
        }}
        var dot = document.getElementById('sb-dot');
        if (dot) {{
          dot.className = 'dot' + (d.healthy === false ? ' amber' : '');
        }}
        setText('sb-summary', d.healthy === false ? 'Degraded' : 'Operational');
        var srcVal = (d.sources_online != null && d.sources_total != null)
                     ? (d.sources_online + ' / ' + d.sources_total) : null;
        showMetric('sb-sources-wrap', srcVal);
        showMetric('sb-aircraft-wrap', d.aircraft_tracked);
        showMetric('sb-lastupdate-wrap', d.last_update_age_sec, fmtAge);
        showMetric('sb-uptime-wrap', d.station_uptime_seconds, fmtUptime);
      }})
      .catch(function() {{
        setText('sb-summary', 'Status unavailable');
      }});
  }}
  refresh();
  setInterval(refresh, 30000);
}})();
</script>
</body></html>"""
    return _publicize(body, '/public')


# /api/wx/status — landing-page status strip data.
# Aircraft count + last-update age + uptime are real; source counts pending.
@app.route("/api/wx/status")
def api_wx_status():
    import time as _time
    try:
        _boot = float(open('/proc/uptime').read().split()[0])
    except Exception:
        _boot = 0
    try:
        with _LATEST_LOCK:
            _aircraft_count = len(_LATEST_AIRCRAFT)
    except Exception:
        _aircraft_count = None
    try:
        _ts = METAR_CACHE.get("ts") or 0
        _age = max(0, int(_time.time() - _ts)) if _ts else None
    except Exception:
        _age = None
    return ({
        "healthy": True,
        "sources_online": None,
        "sources_total": None,
        "aircraft_tracked": _aircraft_count,
        "last_update_age_sec": _age,
        "station_uptime_seconds": int(_boot),
    }, 200, {"Content-Type": "application/json"})

# ─────────────────────────────────────────────────────────────────────────


@app.route("/icons-preview")
def icons_preview():
    """Mirror of the live ADS-B icon renderer in kneeboard_dev.py. Updated to
    match every visual behavior on the dev kneeboard at :8084 — fill drives
    altitude band, outline drives operator class, brightness/drop-shadow filter
    matches live, emergency squawks paint red with a pulsing ring, stale-fade
    + age badge mirror the server's grace window. Use this page as a feedback
    surface; tune values in the JS block and refresh."""

    # ── Source-of-truth values mirrored from the live JS constants ──────────
    SVGS = {
      "ga_single":  '<path d="M12 3 L12.6 8 L20 11 L20 12 L12.6 11.5 L12.6 17 L14.5 19 L14.5 20 L9.5 20 L9.5 19 L11.4 17 L11.4 11.5 L4 12 L4 11 L11.4 8 Z"/>',
      "ga_twin":    '<path d="M12 3 L13 8 L20 12 L20 13 L13 12 L13 18 L15 20 L9 20 L11 18 L11 12 L4 13 L4 12 L11 8 Z"/><circle cx="6" cy="12" r="1"/><circle cx="18" cy="12" r="1"/>',
      "turboprop":  '<path d="M12 2 L13 7 L21 11 L21 13 L13 12 L13 19 L16 21 L16 22 L8 22 L8 21 L11 19 L11 12 L3 13 L3 11 L11 7 Z"/><line x1="11" y1="1" x2="13" y2="1" stroke="currentColor" stroke-width="1"/>',
      "jet":        '<path d="M12 2 L13 7 L22 14 L22 15 L13 13 L13 19 L17 22 L17 22.5 L7 22.5 L7 22 L11 19 L11 13 L2 15 L2 14 L11 7 Z"/>',
      "widebody":   '<path d="M12 1 L13 6 L23 14 L23 15 L13 13 L13 20 L18 22.5 L18 23 L6 23 L6 22.5 L11 20 L11 13 L1 15 L1 14 L11 6 Z"/><circle cx="6.5" cy="12" r="0.7"/><circle cx="9" cy="11" r="0.7"/><circle cx="15" cy="11" r="0.7"/><circle cx="17.5" cy="12" r="0.7"/>',
      "helicopter": '<circle cx="12" cy="12" r="11" fill="none" stroke="currentColor" stroke-width="0.5" opacity="0.5"/><path d="M10 6 L14 6 L14 17 L15 18 L9 18 L10 17 Z M11 18 L13 18 L13 22 L11 22 Z"/><rect x="2" y="11.5" width="20" height="1" opacity="0.7"/>',
      "military":   '<path d="M12 1 L13 7 L21 19 L18 19 L13 14 L13.5 21 L16 22.5 L8 22.5 L10.5 21 L11 14 L6 19 L3 19 L11 7 Z"/>',
    }

    # Mirrors ICON_SIZE_BY_WAKE + per-category mapping; at-rendered px = wake × ICON_SCALE
    LABELS = [
      ("ga_single",  "GA single",         "C172, PA28, SR22, DA40",                    "L",   "20 px"),
      ("ga_twin",    "GA twin / piston",  "BE58, PA34, P32R",                          "L",   "20 px"),
      ("turboprop",  "Turboprop",         "PC12, C208, BE20, AT72, DH8x",              "L–M", "20–28 px"),
      ("jet",        "Narrowbody jet",    "B737, A320, A220, B752",                    "M",   "28 px"),
      ("widebody",   "Widebody jet",      "B77x, B78x, A33x, A35x, B744",              "H–J", "34–40 px"),
      ("helicopter", "Helicopter",        "EC35, AS50, R44, B06, S92, MD52",           "L",   "20 px"),
      ("military",   "Military / fighter","RCH/PAT callsigns, F-16/F-22, C17, KC135",  "var", "28 px"),
    ]

    # ALT_BANDS in JS → mirrored exactly here. INVERTED scale: low = RED.
    ALTS = [
      ("Ground/Taxi",      "#cccccc"),
      ("0–1.5k (pattern)", "#ff2244"),
      ("1.5–3k (low VFR)", "#ff7722"),
      ("3–6k (mid VFR)",   "#ffbb00"),
      ("6–10k (high VFR)", "#ffee22"),
      ("10–18k (IFR)",     "#88dd22"),
      ("18k+ (jet)",       "#33cc88"),
      ("Unknown alt",      "#888888"),
    ]
    # OUTLINE_BY_CLASS in JS → mirrored exactly here.
    CLASSES = [
      ("GA / Private", "#ffffff"),
      ("Commercial",   "#3399ff"),
      ("Cargo",        "#cc44ff"),
      ("Military",     "#9aaa3a"),
      ("Medivac",      "#ff66cc"),
      ("Coast Guard",  "#5599ff"),
      ("Unknown",      "#000000"),
    ]
    # Live defaults that the slider tunes — show actual values
    ICON_SCALE       = 1.10
    ICON_BRIGHTNESS  = 1.15
    OUTLINE_W        = 0.9     # ICON_OUTLINE_WIDTH

    EMERGENCY_FILL = "#ff0033"  # squawks 7500/7600/7700 override the alt-band fill

    def icon_svg(svg_inner, fill, outline, size=28, rot=0,
                 brightness=ICON_BRIGHTNESS, opacity=1.0,
                 emergency=False, glow_color=None):
        """Match aircraftDivIcon() in the JS exactly: brightness filter,
        drop-shadow keyed to fill, stroke-linejoin=round, optional pulsing ring
        on emergency, optional opacity for stale-fade demonstration."""
        glow = glow_color or fill
        rotstyle = f"transform:rotate({rot}deg);" if rot else ""
        # Live applies brightness + 3px drop-shadow to fresh; brightness +
        # saturate(0.6) + 2px drop-shadow #000 when stale.
        if opacity < 1.0:
            filt = f"brightness({brightness}) saturate(0.6) drop-shadow(0 0 2px #000)"
        else:
            filt = f"brightness({brightness}) drop-shadow(0 0 3px {glow})"
        em_ring = ("<circle cx='12' cy='12' r='11' fill='none' stroke='" + EMERGENCY_FILL + "' "
                   "stroke-width='1.5' opacity='0.9'>"
                   "<animate attributeName='opacity' values='0.9;0.2;0.9' dur='1.2s' repeatCount='indefinite'/>"
                   "</circle>") if emergency else ""
        return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" '
                f'fill="{fill}" stroke="{outline}" stroke-width="{OUTLINE_W}" '
                f'stroke-linejoin="round" '
                f'style="{rotstyle}opacity:{opacity:.2f};filter:{filt}">'
                f'{svg_inner}{em_ring}</svg>')

    def cell(svg, fill, outline, size=28, rot=0, label=None,
             opacity=1.0, emergency=False, badge=None):
        """Cell wrapping icon_svg with optional age badge + caption."""
        ic = icon_svg(svg, fill, outline, size, rot, opacity=opacity, emergency=emergency)
        badge_html = f'<div class="badge">{badge}</div>' if badge else ''
        # Label is rendered on the live map below the icon in fill color,
        # mirrored here as a small caption.
        cap = ''
        if label:
            cap = f'<div class="cap" style="color:{fill}">{label}</div>'
        return f'<span class="ic" style="position:relative">{badge_html}{ic}{cap}</span>'

    # ── Per-silhouette rows: sizes / alt fills / class outlines / rotations ─
    rows = ""
    for key, name, examples, wake, size in LABELS:
        s = SVGS[key]
        sizes_demo = "".join(cell(s, "#ffbb00", "#ffffff", sz) for sz in (20, 28, 40))
        alt_demo   = "".join(cell(s, c, "#ffffff", 28) for _, c in ALTS)
        cls_demo   = "".join(cell(s, "#ffbb00", c, 28) for _, c in CLASSES)
        rot_demo   = "".join(cell(s, "#ffbb00", "#3399ff", 28, r) for r in (0, 45, 135, 225))
        rows += f'''
        <tr>
          <td class="cat">{name}</td>
          <td class="ex">{examples}</td>
          <td class="wk">{wake}</td>
          <td class="sz">{size}</td>
          <td class="icons">
            <div class="grp">{sizes_demo}</div>
            <div class="grp">{alt_demo}</div>
            <div class="grp">{cls_demo}</div>
            <div class="grp">{rot_demo}</div>
          </td>
        </tr>'''

    # ── Special-state rows: emergency squawk + stale-fade + ground/unknown ──
    em_demo = "".join([
        cell(SVGS["jet"], EMERGENCY_FILL, "#ffffff", 32, emergency=True, label="N123EM"),
        cell(SVGS["ga_single"], EMERGENCY_FILL, "#ffffff", 24, emergency=True, label="N456EM"),
        cell(SVGS["helicopter"], EMERGENCY_FILL, "#ff66cc", 28, emergency=True, label="LIFE7"),
    ])
    # Stale-fade: opacity steps + age badges mirroring the live formula
    # opacity = 1 - 0.75 * (stale_sec / 60). Badge appears at >=5s.
    stale_demo = "".join([
        cell(SVGS["jet"], "#ffbb00", "#3399ff", 30, label="t=0",  opacity=1.00),
        cell(SVGS["jet"], "#ffbb00", "#3399ff", 30, label="t=10", opacity=0.875, badge="10s"),
        cell(SVGS["jet"], "#ffbb00", "#3399ff", 30, label="t=30", opacity=0.625, badge="30s"),
        cell(SVGS["jet"], "#ffbb00", "#3399ff", 30, label="t=50", opacity=0.375, badge="50s"),
        cell(SVGS["jet"], "#ffbb00", "#3399ff", 30, label="t=60", opacity=0.250, badge="60s"),
    ])
    ground_demo = "".join([
        cell(SVGS["ga_single"], "#cccccc", "#ffffff",  24, label="GROUND"),
        cell(SVGS["jet"],       "#cccccc", "#3399ff", 30, label="TAXI"),
        cell(SVGS["ga_single"], "#888888", "#000000", 24, label="ALT?"),
    ])

    # ── Hybrid matrix: jet × every (alt × class) combo ──────────────────────
    matrix_head = "<th></th>" + "".join(f'<th class="mhdr" style="color:{c}">{n}</th>' for n, c in CLASSES)
    matrix_rows = ""
    for alt_name, alt_color in ALTS:
        cells = "".join(f'<td>{cell(SVGS["jet"], alt_color, cls_color, 30)}</td>'
                        for _, cls_color in CLASSES)
        matrix_rows += f'<tr><th class="mhdr-row" style="color:{alt_color}">{alt_name}</th>{cells}</tr>'

    return f'''<!doctype html><html><head><meta charset="utf-8"><title>ADS-B Icon Preview — DEV</title>
    <style>
    body {{ background:#0a0e16; color:#d8e1ec; font-family:system-ui,sans-serif; padding:24px; margin:0; }}
    h1 {{ color:#23d18b; font-size:18px; letter-spacing:2px; text-transform:uppercase; margin:0 0 8px; }}
    h1 .dev {{ background:#ff8800; color:#000; padding:2px 8px; border-radius:4px; font-size:12px; margin-left:8px; vertical-align:middle; }}
    h1 a {{ color:#0090ff; font-size:11px; letter-spacing:1px; margin-left:14px; text-decoration:none; }}
    p.lede {{ color:#9aa5b8; font-size:13px; max-width:980px; line-height:1.5; }}
    h2 {{ color:#0090ff; font-size:13px; text-transform:uppercase; letter-spacing:1.5px; margin:28px 0 8px; }}
    h2 small {{ color:#7a8497; font-weight:400; text-transform:none; letter-spacing:0; margin-left:8px; font-size:11px; }}
    table {{ border-collapse:collapse; margin-top:14px; background:#141a26; border-radius:8px; overflow:hidden; }}
    th, td {{ padding:10px 14px; text-align:left; border-bottom:1px solid #2a3140; vertical-align:middle; font-size:12px; }}
    th {{ background:#1f2738; color:#9aa5b8; text-transform:uppercase; letter-spacing:1px; font-size:10px; }}
    td.cat {{ color:#23d18b; font-weight:700; }}
    td.ex  {{ color:#9aa5b8; font-size:11px; max-width:220px; }}
    td.wk  {{ color:#ffbb00; font-weight:700; text-align:center; }}
    td.sz  {{ color:#9aa5b8; font-size:11px; text-align:center; }}
    td.icons {{ white-space:nowrap; }}
    .grp {{ display:inline-block; padding:6px 10px; margin:0 6px 0 0; border-right:1px dashed #2a3140; }}
    .grp:last-child {{ border-right:none; }}
    .ic {{ display:inline-flex; flex-direction:column; align-items:center; justify-content:center; min-width:54px; min-height:64px; margin:0 4px; vertical-align:top; }}
    .ic .cap {{ font-size:9px; font-weight:700; margin-top:2px; text-shadow:0 0 3px #000; white-space:nowrap; }}
    .badge {{ position:absolute; top:-4px; right:-4px; background:#1a2230; border:1px solid #888; color:#bbb; font-size:8px; font-weight:700; padding:0 3px; border-radius:3px; line-height:1.4; z-index:2; }}
    .legend {{ display:flex; gap:14px; flex-wrap:wrap; font-size:11px; color:#9aa5b8; align-items:center; }}
    .legend span {{ display:flex; align-items:center; gap:5px; }}
    .legend .swatch {{ width:14px; height:14px; border-radius:3px; }}
    .legend .ring {{ width:14px; height:14px; border-radius:50%; border:2px solid; background:transparent; }}
    .matrix {{ border-collapse:collapse; margin-top:14px; background:#141a26; border-radius:8px; }}
    .matrix th.mhdr {{ font-size:9px; padding:6px; text-transform:none; letter-spacing:0; }}
    .matrix th.mhdr-row {{ font-size:10px; padding:6px 12px; text-transform:none; letter-spacing:0; text-align:right; white-space:nowrap; }}
    .matrix td {{ padding:4px; text-align:center; }}
    .panel {{ background:#141a26; border-radius:8px; padding:14px 18px; margin-top:14px; }}
    .panel .row {{ display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start; }}
    code {{ background:#1f2738; padding:1px 5px; border-radius:3px; color:#23d18b; font-size:11px; }}
    .open-q {{ background:#1f2738; padding:14px 18px; border-radius:8px; margin-top:24px; font-size:12px; line-height:1.6; }}
    .kv {{ display:grid; grid-template-columns:auto 1fr; gap:4px 18px; font-size:12px; max-width:560px; }}
    .kv code {{ font-size:11px; }}
    .kv .v {{ color:#ffd24c; font-weight:700; }}
    </style></head><body>

    <h1>ADS-B Icon Preview <span class="dev">DEV — LIVE MIRROR</span>
        <a href="/wx-icons-preview">→ wx-icons-preview</a></h1>

    <p class="lede">Pixel-faithful mirror of <code>aircraftDivIcon()</code> on the live dev kneeboard at <code>:8084</code>.
    <strong>Fill = altitude band</strong>, <strong>outline = operator class</strong>, <strong>shape = silhouette category</strong>,
    <strong>size = wake × <code>ICON_SCALE</code></strong>. Brightness filter, drop-shadow color, emergency pulse, stale-fade,
    and age badges all reproduced exactly.</p>

    <div class="panel">
      <h2 style="margin-top:0">Live tuning state <small>(values you'd see at this instant on :8084)</small></h2>
      <div class="kv">
        <code>ICON_SCALE</code>          <span class="v">{ICON_SCALE}</span>
        <code>ICON_BRIGHTNESS</code>     <span class="v">{ICON_BRIGHTNESS}</span>
        <code>ICON_OUTLINE_WIDTH</code>  <span class="v">{OUTLINE_W}</span>
        <code>COLOR_MODE</code>          <span class="v">altitude-fill</span>
        <code>EMERGENCY_SQUAWKS</code>   <span class="v">7500 · 7600 · 7700 → fill becomes red, ring pulses</span>
        <code>AIRCRAFT_GRACE_SEC</code>  <span class="v">60 (opacity fades 1.00 → 0.25)</span>
      </div>
    </div>

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
      <tr><th style="width:120px">Category</th><th style="width:220px">Examples</th><th>Wake</th><th>Size</th><th>Sizes (3) · Alt fills (8) · Class outlines (7) · Rotations (4)</th></tr>
      {rows}
    </table>

    <h2>Emergency squawks <small>fill → red + animated 1.2s pulsing ring</small></h2>
    <div class="panel"><div class="row">{em_demo}</div></div>

    <h2>Stale-fade + age badge <small>opacity = 1 − 0.75 × (stale_sec / 60); badge appears at ≥5s; saturate(0.6) kicks in once stale</small></h2>
    <div class="panel"><div class="row">{stale_demo}</div></div>

    <h2>Ground / Taxi / Unknown altitude <small>special grey fills outside the standard altitude bands</small></h2>
    <div class="panel"><div class="row">{ground_demo}</div></div>

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
        <li><code>EMERGENCY_SQUAWKS</code> — squawks that override fill to red + add pulsing ring</li>
        <li><code>AIRCRAFT_GRACE_SEC_CLIENT</code> — fade window length (default 60s)</li>
      </ul>
      <p style="margin:14px 0 0">Companion preview at <a href="/wx-icons-preview" style="color:#0090ff">/wx-icons-preview</a> covers the weather-HUD instrument family (ceiling, freezing-level, wind compass, plane-in-fog, stratus stack, pressure VSI, animated anemometer).</p>
    </div>
    </body></html>'''


@app.route("/wx-icons-preview")
def wx_icons_preview():
    """Live preview of weather-HUD instrument icons we're brainstorming for the
    glanceable bottom strip + per-station overlays. Each row shows the same
    icon under multiple conditions so we can see how it 'reads' at a glance
    without a legend. Iterate freely — this is a sandbox.
    """
    # Each scene is a dict: name → list of (label, params) tuples.
    # Params drive the SVG generation per instrument family.

    # ── Mini attitude indicator → ceiling ─────────────────────────────
    # Renders a circular AI face. Horizon-line vertical position = ceiling AGL.
    # Sky color = cloud overcast tint. Ground stays the same.
    def ai_ceiling(ceil_ft, cov_pct, label, sensor_alt_ft=0):
        # Brown ground band FIXED at 0ft (y=70..80). White ceiling line
        # MOVES with cloud base. Yellow plane sits at sensor altitude
        # (defaults to ground). Vertical scale: 0..5000ft maps to y=70..10.
        ft_to_y = lambda ft: max(10, min(70, 70 - (ft / 5000.0) * 60))
        ceil_y   = ft_to_y(ceil_ft)
        sensor_y = ft_to_y(sensor_alt_ft)
        # Sky tint: blue at 0% cov → gray at 100%
        sky_r = int(38 + (170 - 38) * cov_pct / 100)
        sky_g = int(95 + (175 - 95) * cov_pct / 100)
        sky_b = int(150 + (180 - 150) * cov_pct / 100)
        sky = f"rgb({sky_r},{sky_g},{sky_b})"
        ground = "#3a2820"
        clear  = "#0d2540"
        warn = "#ff5040" if ceil_ft < 1000 else ("#ffaa00" if ceil_ft < 3000 else "#23d18b")
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <defs>
              <clipPath id="aic_{label}"><circle cx="40" cy="40" r="34"/></clipPath>
            </defs>
            <g clip-path="url(#aic_{label})">
              <rect x="0" y="0" width="80" height="{ceil_y}" fill="{sky}"/>
              <rect x="0" y="{ceil_y}" width="80" height="{70 - ceil_y}" fill="{clear}"/>
              <rect x="0" y="70" width="80" height="10" fill="{ground}"/>
              <line x1="0" y1="{ceil_y}" x2="80" y2="{ceil_y}" stroke="#fff" stroke-width="1.5"/>
              <line x1="68" y1="58" x2="76" y2="58" stroke="#fff" stroke-width="0.5" opacity="0.6"/>
              <line x1="68" y1="34" x2="76" y2="34" stroke="#fff" stroke-width="0.5" opacity="0.6"/>
              <line x1="68" y1="10" x2="76" y2="10" stroke="#fff" stroke-width="0.5" opacity="0.6"/>
              <text x="65" y="59" fill="#fff" font-size="6" text-anchor="end" opacity="0.7">1k</text>
              <text x="65" y="35" fill="#fff" font-size="6" text-anchor="end" opacity="0.7">3k</text>
              <text x="65" y="11" fill="#fff" font-size="6" text-anchor="end" opacity="0.7">5k</text>
              <line x1="32" y1="{sensor_y}" x2="48" y2="{sensor_y}" stroke="#ffd24c" stroke-width="2"/>
              <circle cx="40" cy="{sensor_y}" r="2" fill="#ffd24c"/>
            </g>
            <circle cx="40" cy="40" r="34" fill="none" stroke="{warn}" stroke-width="2"/>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:{warn}">{ceil_ft if ceil_ft else "CLR"} ft / {cov_pct}%</div>
        </div>'''

    # ── Altimeter face → freezing level ───────────────────────────────
    def alt_freezing(fz_ft, label):
        # Needle: 0..12000 ft maps to 0..360 degrees. 3000 ft → 90°.
        ang = (fz_ft / 12000.0) * 360
        warn = "#ff5040" if fz_ft < 2000 else ("#ffaa00" if fz_ft < 4000 else "#23d18b")
        # Red arc 0..4000 ft = 0..120° on the dial
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <circle cx="40" cy="40" r="34" fill="#101626" stroke="#445" stroke-width="1.5"/>
            <!-- icing-risk arc: 0..4000 ft -->
            <path d="M 40 6 A 34 34 0 0 1 70.4 56.6" fill="none" stroke="#ff5040" stroke-width="3" opacity="0.4"/>
            <!-- tick marks every 30° (0,3,6,9 = 0,3,6,9 thousand) -->
            <g stroke="#fff" stroke-width="1" opacity="0.5">
              <line x1="40" y1="6" x2="40" y2="12"/>
              <line x1="74" y1="40" x2="68" y2="40"/>
              <line x1="40" y1="74" x2="40" y2="68"/>
              <line x1="6" y1="40" x2="12" y2="40"/>
            </g>
            <text x="40" y="20" fill="#fff" font-size="7" text-anchor="middle">0</text>
            <text x="62" y="42" fill="#fff" font-size="7" text-anchor="middle">3</text>
            <text x="40" y="65" fill="#fff" font-size="7" text-anchor="middle">6</text>
            <text x="18" y="42" fill="#fff" font-size="7" text-anchor="middle">9</text>
            <!-- needle -->
            <g transform="rotate({ang} 40 40)">
              <line x1="40" y1="40" x2="40" y2="12" stroke="{warn}" stroke-width="2"/>
              <polygon points="38,14 42,14 40,8" fill="{warn}"/>
            </g>
            <circle cx="40" cy="40" r="2.5" fill="{warn}"/>
            <text x="40" y="55" fill="#9aa5b8" font-size="5" text-anchor="middle">FRZ LVL</text>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:{warn}">{fz_ft} ft</div>
        </div>'''

    # ── Wind compass ──────────────────────────────────────────────────
    def wind_compass(deg, kt, gust, label):
        # Wind feather points FROM. Color by speed.
        if kt < 8:    color = "#88ccff"
        elif kt < 18: color = "#c8e88c"
        elif kt < 30: color = "#ffd24c"
        else:         color = "#ff5040"
        feather_len = 12 + min(kt, 40) * 0.4   # 12..28
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <circle cx="40" cy="40" r="34" fill="#101626" stroke="#445" stroke-width="1.5"/>
            <!-- cardinal letters -->
            <text x="40" y="13" fill="#9aa5b8" font-size="8" text-anchor="middle">N</text>
            <text x="68" y="43" fill="#9aa5b8" font-size="7" text-anchor="middle">E</text>
            <text x="40" y="71" fill="#9aa5b8" font-size="7" text-anchor="middle">S</text>
            <text x="13" y="43" fill="#9aa5b8" font-size="7" text-anchor="middle">W</text>
            <!-- 30° tick ring -->
            <g stroke="#445" stroke-width="0.5">
              {''.join(f'<line x1="40" y1="6" x2="40" y2="10" transform="rotate({a} 40 40)"/>' for a in range(0, 360, 30))}
            </g>
            <!-- Wind feather pointing FROM-direction (so it points away from where wind comes from) -->
            <g transform="rotate({deg} 40 40)">
              <line x1="40" y1="40" x2="40" y2="{40 - feather_len}" stroke="{color}" stroke-width="3" stroke-linecap="round"/>
              <polygon points="36,{40 - feather_len + 6} 44,{40 - feather_len + 6} 40,{40 - feather_len - 2}" fill="{color}"/>
              <!-- barbs for speed: every 10 kt = full barb, half barb for 5 -->
              {''.join(f'<line x1="40" y1="{34 - i*4}" x2="46" y2="{30 - i*4}" stroke="{color}" stroke-width="1.5"/>' for i in range(min(kt // 10, 4)))}
            </g>
            <circle cx="40" cy="40" r="2" fill="{color}"/>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:{color}">{deg:03d}° / {kt}{f"G{gust}" if gust else ""}KT</div>
        </div>'''

    # ── Plane-in-fog → visibility ─────────────────────────────────────
    def plane_fog(vis_sm, label):
        # Plane silhouette; fog overlay alpha = clamped (1 - vis/10)
        fog_alpha = max(0, min(0.85, 1 - vis_sm / 10.0))
        warn = "#ff5040" if vis_sm < 3 else ("#ffaa00" if vis_sm < 5 else "#23d18b")
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <rect x="0" y="0" width="80" height="80" fill="#0a0e16" rx="8"/>
            <!-- Crosshair reticle (placeholder until proper plane vector) -->
            <g stroke="#d8e1ec" stroke-width="1.5" fill="none">
              <circle cx="40" cy="34" r="14"/>
              <line x1="40" y1="14" x2="40" y2="54"/>
              <line x1="20" y1="34" x2="60" y2="34"/>
              <circle cx="40" cy="34" r="2" fill="#d8e1ec"/>
            </g>
            <!-- fog wash -->
            <rect x="0" y="0" width="80" height="80" fill="#cfd6e0" opacity="{fog_alpha:.2f}" rx="8"/>
            <!-- distance scale ticks -->
            <line x1="6" y1="68" x2="74" y2="68" stroke="#445" stroke-width="0.5"/>
            <text x="6" y="75" fill="#666" font-size="5">0</text>
            <text x="40" y="75" fill="#666" font-size="5" text-anchor="middle">5sm</text>
            <text x="74" y="75" fill="#666" font-size="5" text-anchor="end">10+</text>
            <line x1="{6 + min(vis_sm, 10) * 6.8:.1f}" y1="64" x2="{6 + min(vis_sm, 10) * 6.8:.1f}" y2="72" stroke="{warn}" stroke-width="2"/>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:{warn}">VIS {vis_sm} SM</div>
        </div>'''

    # ── Stratus stack → cloud coverage by altitude ────────────────────
    def stratus_stack(low_pct, mid_pct, high_pct, label):
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <rect x="0" y="0" width="80" height="80" fill="#0a0e16" rx="8"/>
            <!-- HIGH band -->
            <rect x="8" y="14" width="64" height="6" fill="#cfd6e0" opacity="{high_pct/100:.2f}" rx="2"/>
            <text x="6" y="19" fill="#666" font-size="6" text-anchor="end">H</text>
            <!-- MID band -->
            <rect x="8" y="34" width="64" height="6" fill="#cfd6e0" opacity="{mid_pct/100:.2f}" rx="2"/>
            <text x="6" y="39" fill="#666" font-size="6" text-anchor="end">M</text>
            <!-- LOW band -->
            <rect x="8" y="54" width="64" height="6" fill="#cfd6e0" opacity="{low_pct/100:.2f}" rx="2"/>
            <text x="6" y="59" fill="#666" font-size="6" text-anchor="end">L</text>
            <!-- ground -->
            <rect x="0" y="68" width="80" height="12" fill="#3a2820" rx="0 0 8 8"/>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:#9aa5b8">L{low_pct} M{mid_pct} H{high_pct}</div>
        </div>'''

    # ── VSI needle → pressure tendency (ΔMSLP per 3hr in mb) ──────────
    def vsi_press(d_mb, label):
        # +/-3 mb / 3hr = full deflection. positive = up, negative = down.
        ang = max(-90, min(90, (d_mb / 3.0) * 90))
        warn = "#23d18b" if d_mb >= 0 else ("#ffaa00" if d_mb > -2 else "#ff5040")
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <circle cx="40" cy="40" r="34" fill="#101626" stroke="#445" stroke-width="1.5"/>
            <text x="40" y="13" fill="#9aa5b8" font-size="6" text-anchor="middle">+3</text>
            <text x="40" y="71" fill="#9aa5b8" font-size="6" text-anchor="middle">-3</text>
            <text x="13" y="43" fill="#9aa5b8" font-size="6" text-anchor="middle">0</text>
            <!-- arc up = green, arc down = red -->
            <path d="M 6 40 A 34 34 0 0 1 40 6" fill="none" stroke="#23d18b" stroke-width="2" opacity="0.4"/>
            <path d="M 6 40 A 34 34 0 0 0 40 74" fill="none" stroke="#ff5040" stroke-width="2" opacity="0.4"/>
            <!-- Needle: 0 = pointing left (W), +90 = up (N), -90 = down (S) -->
            <g transform="rotate({-ang} 40 40)">
              <line x1="40" y1="40" x2="10" y2="40" stroke="{warn}" stroke-width="2"/>
              <polygon points="12,38 12,42 6,40" fill="{warn}"/>
            </g>
            <circle cx="40" cy="40" r="2" fill="{warn}"/>
            <text x="40" y="62" fill="#9aa5b8" font-size="5" text-anchor="middle">ΔMSLP / 3h</text>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:{warn}">{d_mb:+.1f} mb</div>
        </div>'''

    # ── Animated anemometer → station wind speed ──────────────────────
    def anemo(kt, label, anim_id):
        if kt < 1:    speed = "0s"
        elif kt < 8:  speed = "3s"
        elif kt < 18: speed = "1.4s"
        elif kt < 30: speed = "0.7s"
        else:         speed = "0.35s"
        if kt < 8:    color = "#88ccff"
        elif kt < 18: color = "#c8e88c"
        elif kt < 30: color = "#ffd24c"
        else:         color = "#ff5040"
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <rect x="0" y="0" width="80" height="80" fill="#0a0e16" rx="8"/>
            <!-- post -->
            <line x1="40" y1="44" x2="40" y2="74" stroke="#445" stroke-width="2"/>
            <!-- 3 cup arms -->
            <g style="transform-origin: 40px 40px; animation: anemoSpin {speed} linear infinite">
              <line x1="40" y1="40" x2="40" y2="22" stroke="{color}" stroke-width="1.5"/>
              <circle cx="40" cy="20" r="3" fill="{color}"/>
              <line x1="40" y1="40" x2="55.6" y2="49" stroke="{color}" stroke-width="1.5"/>
              <circle cx="55.6" cy="49" r="3" fill="{color}"/>
              <line x1="40" y1="40" x2="24.4" y2="49" stroke="{color}" stroke-width="1.5"/>
              <circle cx="24.4" cy="49" r="3" fill="{color}"/>
            </g>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:{color}">{kt} KT</div>
        </div>'''

    # ── Demo conditions for each instrument ───────────────────────────
    rows_html = ""
    sections = [
        ("Mini Attitude Indicator → Ceiling",
         "Top half = sky/cloud tint, bottom = ground. Horizon line position encodes ceiling AGL. Tick marks at 1k/3k/5k. Ring color flushes warm when ceiling enters hazardous band.",
         [ai_ceiling(5000, 5, "CLR · 5sm vis"),
          ai_ceiling(3500, 35, "SCT 035"),
          ai_ceiling(1800, 75, "BKN 018"),
          ai_ceiling(600, 95, "OVC 006 — bad")]),
        ("Altimeter → Freezing Level",
         "Single needle on a 0-12k face. Red arc covers icing-risk band (0-4k AGL). Warm color when freezing layer drops into typical bush altitudes.",
         [alt_freezing(11000, "summer day"),
          alt_freezing(6500, "fall morning"),
          alt_freezing(3200, "freezing rain risk"),
          alt_freezing(1200, "icing — danger")]),
        ("Wind Compass → Surface Wind",
         "Feather points FROM. Length + color encode speed; barbs encode 10kt increments. Color palette matches the wind-flow streamlines.",
         [wind_compass(330, 5, 0, "calm"),
          wind_compass(180, 14, 21, "moderate gusty"),
          wind_compass(75, 28, 38, "strong cross"),
          wind_compass(220, 42, 55, "severe")]),
        ("Plane-in-Fog → Visibility",
         "Side-profile plane behind a fog wash. Alpha grows with reducing visibility — the icon physically demonstrates how much you'll see. Tick on horizontal scale shows actual sm.",
         [plane_fog(10, "10+ SM"),
          plane_fog(6, "6 SM"),
          plane_fog(3, "3 SM marginal"),
          plane_fog(1, "1 SM IFR")]),
        ("Stratus Stack → Cloud Coverage by Altitude",
         "Three horizontal bars (low / mid / high). Each bar's opacity = % coverage in that altitude band. SCT/BKN/OVC reads instantly without parsing abbreviations.",
         [stratus_stack(0, 0, 0, "CLR all levels"),
          stratus_stack(30, 15, 0, "FEW low / SCT mid"),
          stratus_stack(75, 30, 0, "BKN low"),
          stratus_stack(95, 90, 75, "OVC layered")]),
        ("VSI Needle → Pressure Tendency",
         "Needle deflection = ΔMSLP over 3 hours. Up = pressure climbing (weather improving), down = falling (storm approaching).",
         [vsi_press(2.5, "rising — clearing"),
          vsi_press(0.3, "steady"),
          vsi_press(-1.5, "falling"),
          vsi_press(-2.8, "plummeting — storm")]),
        ("Animated Anemometer → Per-Station Wind",
         "Tiny 3-cup spinning anemometer. Spin rate proportional to wind speed; eye catches motion before reading. For map-pin overlays at each MWOS.",
         [anemo(2, "calm", "a1"),
          anemo(12, "moderate", "a2"),
          anemo(24, "strong", "a3"),
          anemo(40, "gale", "a4")]),
    ]
    for title, blurb, cards in sections:
        rows_html += f'''
        <section>
          <h2>{title}</h2>
          <p class="blurb">{blurb}</p>
          <div class="cards">{"".join(cards)}</div>
        </section>'''

    # The proposed bottom strip — all instruments together at "current" condition
    strip_cards = "".join([
        ai_ceiling(2200, 65, "CEILING"),
        alt_freezing(4800, "FREEZING"),
        wind_compass(170, 13, 21, "WIND"),
        plane_fog(5, "VIS"),
        stratus_stack(45, 20, 5, "CLOUDS"),
        vsi_press(-0.8, "PRESSURE"),
    ])

    return f'''<!doctype html><html><head><meta charset="utf-8"><title>WX Icon Preview — DEV</title>
    <style>
    @keyframes anemoSpin {{ from {{ transform: rotate(0deg); }} to {{ transform: rotate(360deg); }} }}
    body {{ background:#0a0e16; color:#d8e1ec; font-family:system-ui,sans-serif; padding:24px; margin:0; }}
    h1 {{ color:#23d18b; font-size:18px; letter-spacing:2px; text-transform:uppercase; margin:0 0 8px; }}
    h1 .dev {{ background:#ff8800; color:#000; padding:2px 8px; border-radius:4px; font-size:12px; margin-left:8px; vertical-align:middle; }}
    p.lede {{ color:#9aa5b8; font-size:13px; max-width:980px; line-height:1.5; }}
    h2 {{ color:#0090ff; font-size:13px; text-transform:uppercase; letter-spacing:1.5px; margin:28px 0 4px; }}
    p.blurb {{ color:#9aa5b8; font-size:11px; max-width:780px; margin:0 0 12px; line-height:1.5; }}
    section {{ background:#141a26; padding:16px 20px; border-radius:10px; margin-bottom:14px; }}
    .cards {{ display:flex; gap:18px; flex-wrap:wrap; }}
    .card {{ background:#1a2030; border:1px solid #2a3140; border-radius:8px; padding:10px 12px 8px; text-align:center; min-width:120px; }}
    .lbl {{ color:#9aa5b8; font-size:10px; text-transform:uppercase; letter-spacing:1px; margin-top:4px; }}
    .val {{ font-size:11px; font-weight:600; margin-top:2px; }}
    .strip {{ background:linear-gradient(180deg, #0a0e16 0%, #0d121d 100%); border:1px solid #2a3140; border-radius:12px; padding:12px; margin-top:24px; }}
    .strip h2 {{ margin:0 0 10px; }}
    .strip .cards {{ justify-content:space-around; }}
    .note {{ background:#1f2738; padding:14px 18px; border-radius:8px; margin-top:24px; font-size:12px; line-height:1.6; color:#9aa5b8; }}
    code {{ background:#1f2738; padding:1px 5px; border-radius:3px; color:#23d18b; font-size:11px; }}
    </style></head><body>
    <h1>Weather HUD Icons <span class="dev">DEV — BRAINSTORM</span></h1>
    <p class="lede">Aviation 6-pack vocabulary repurposed for weather. Pilots already parse these shapes by reflex — free cognition. Each row shows the same icon under four conditions so we can see how it 'reads' at a glance without a legend. Iterate freely; nothing here is wired into the live kneeboard yet.</p>

    {rows_html}

    <div class="strip">
      <h2>Proposed bottom-strip layout — all instruments at current viewport conditions</h2>
      <p class="blurb">This is what the persistent ribbon along the bottom of the kneeboard map could look like. Six glanceable instruments showing viewport-averaged conditions.</p>
      <div class="cards">{strip_cards}</div>
    </div>

    <div class="note">
      <strong>Source:</strong> All icons are inline SVG generated server-side in <code>kneeboard_dev.py</code> at <code>/wx-icons-preview</code>. Edit and reload — no JS framework, no build step.<br>
      <strong>Next moves once we pick a starting set:</strong> wire them to <code>/api/wx/grid</code> (viewport-averaged) and to per-station data, port the SVG into the live kneeboard, and add a toggle to show/hide the bottom strip.
    </div>
    </body></html>'''


# ── /wx-validate historical logging (SQLite, 30-day rolling retention) ────
# Every 10 min, snapshot every (anchor × source) into wx_obs. Lets the dashboard
# replay any past hour and produce per-station per-field 30-day report cards
# for the FAA cert path. NVMe-resident so the SD card doesn't take write churn.
_WX_DB_PATH    = "/mnt/nvme/skybridge/wx-validate.db"
_WX_DB_LOCK    = threading.Lock()
_WX_LOG_PERIOD = 600        # 10 min between snapshots
_WX_RETENTION  = 30 * 86400 # 30 days

# Every MWOS station + a handful of supplemental airports without MWOS that
# we still want to track (PANC/PAED/PAJN, etc.). Each anchor: a stable id, a
# human name, lat/lon, optional ICAO if it's METAR-paired, and a short note.
# The whole point of the cert dashboard is to expose every node in our
# observation network — Walter Combs at Montis runs the calibrated MWOS fleet
# and this page is the public proof that his data agrees with NWS METARs.
# Default weights for the SkyBridge Composite weighted-ensemble. Higher =
# more trusted in the composite. Hand-tuned starting values; over time the
# historical-logging table lets us compute optimal weights from observed
# accuracy vs METAR baseline. These are TUNABLE — adjust as the data
# accumulates and we see which sources track METAR most closely.
_WX_CERT_WEIGHTS = {
    "metar":    1.00,   # FAA observation — ground truth (when present, dominates)
    "mwos":     0.90,   # Montis calibrated observation
    "nws":      0.50,   # NWS gridded forecast
    "om_gfs":   0.30,   # NOAA GFS via Open-Meteo
    "om_gem":   0.30,   # Canadian GEM
    "om_ecmwf": 0.40,   # ECMWF — historically high skill
    "om_jma":   0.20,   # Japan Met Agency
}

# Tag-to-display-name lookup for the certified composite annotation.
_WX_CERT_LABEL = {
    "metar": "METAR", "mwos": "MWOS", "nws": "NWS Grid",
    "om_gfs": "GFS", "om_gem": "GEM", "om_ecmwf": "ECMWF", "om_jma": "JMA",
}

def _compute_certified(by_source):
    """Weighted-ensemble composite from the 7 sources we collect. Input is a
    dict of {source_tag: unified-point or None}. Returns a unified-shape point
    with weighted-mean values per scalar field, plus a 'contributors' list
    identifying which sources actually fed each field.

    Wind direction is averaged via vector decomposition (atan2 of weighted u/v
    components) so 350° + 10° → 0°, not 180°.
    """
    if not by_source:
        return None
    out = {
        "source": "certified:skybridge",
        "label": "SKYBRIDGE COMPOSITE",
        "ts": "",
        "dir_deg": None, "speed_kt": None, "gust_kt": None,
        "temp_c": None, "freezing_level_ft": None,
        "cloud_pct": None, "cloud_low_pct": None,
        "cloud_mid_pct": None, "cloud_high_pct": None,
        "visibility_sm": None, "precip_mm": None, "pressure_mb": None,
        "contributors": {},   # per-field list of (source_tag, weight) used
        "weights_used":  dict(_WX_CERT_WEIGHTS),  # snapshot of weights at compute time
    }

    # Scalar-field weighted mean
    for field in ("speed_kt", "gust_kt", "temp_c", "freezing_level_ft",
                  "cloud_pct", "cloud_low_pct", "cloud_mid_pct",
                  "cloud_high_pct", "visibility_sm", "precip_mm", "pressure_mb"):
        weighted_sum = 0.0
        weight_sum = 0.0
        used = []
        for tag, pt in by_source.items():
            if pt is None: continue
            v = pt.get(field)
            if v is None or not isinstance(v, (int, float)): continue
            w = _WX_CERT_WEIGHTS.get(tag, 0)
            if w <= 0: continue
            weighted_sum += w * float(v)
            weight_sum += w
            used.append((tag, w))
        if weight_sum > 0:
            out[field] = round(weighted_sum / weight_sum, 2)
            out["contributors"][field] = used

    # Wind direction: vector mean (decompose to u/v, weight, recompose)
    u_sum = v_sum = w_sum = 0.0
    used_dir = []
    for tag, pt in by_source.items():
        if pt is None: continue
        d = pt.get("dir_deg")
        s = pt.get("speed_kt")
        if d is None or s is None or d < 0: continue   # skip VRB (-1)
        w = _WX_CERT_WEIGHTS.get(tag, 0)
        if w <= 0: continue
        rad = float(d) * math.pi / 180.0
        # Standard vector decomposition: u = sin, v = cos (north-up convention)
        u_sum += w * float(s) * math.sin(rad)
        v_sum += w * float(s) * math.cos(rad)
        w_sum += w
        used_dir.append((tag, w))
    if w_sum > 0:
        u_avg = u_sum / w_sum
        v_avg = v_sum / w_sum
        dir_avg = math.atan2(u_avg, v_avg) * 180.0 / math.pi
        if dir_avg < 0: dir_avg += 360
        out["dir_deg"] = round(dir_avg, 1)
        out["contributors"]["dir_deg"] = used_dir

    return out


_WX_VALIDATE_ANCHORS = [
    # ── Montis MWOS network (calibrated, private) ──
    {"id": "MWOS:133", "name": "Lake Hood MWOS",        "lat": 61.1776,    "lon": -149.9615,  "icao": "PALH", "note": "Float capital of the world / co-located METAR PALH"},
    {"id": "MWOS:1",   "name": "Merrill Field MWOS",   "lat": 61.2167,    "lon": -149.8337,  "icao": "PAMR", "note": "GA Class D / co-located METAR PAMR"},
    {"id": "MWOS:265", "name": "Merrill Field MWOS 2", "lat": 61.2148,    "lon": -149.8396,  "icao": "PAMR", "note": "Second MWOS at PAMR — internal cross-check"},
    {"id": "MWOS:166", "name": "Fairbanks Intl MWOS",  "lat": 64.813056,  "lon": -147.8737,  "icao": "PAFA", "note": "Interior / co-located METAR PAFA"},
    {"id": "MWOS:67",  "name": "Thompson Pass MWOS",   "lat": 61.141065,  "lon": -145.749145,"icao": "",     "note": "Mountain pass — no nearby METAR"},
    {"id": "MWOS:101", "name": "Whittier Harbor MWOS", "lat": 60.7775,    "lon": -148.6862,  "icao": "",     "note": "Coastal — Prince William Sound"},
    {"id": "MWOS:595", "name": "Anaktuvuk Pass MWOS",  "lat": 68.137126,  "lon": -151.741023,"icao": "",     "note": "Brooks Range pass — high elevation"},
    {"id": "MWOS:496", "name": "Atqasuk MWOS",         "lat": 70.4697,    "lon": -157.4307,  "icao": "PATK", "note": "North Slope / METAR PATK"},
    {"id": "MWOS:562", "name": "Wainwright MWOS",      "lat": 70.638167,  "lon": -160.018044,"icao": "",     "note": "Arctic coast"},
    {"id": "MWOS:529", "name": "Nuiqsut MWOS",         "lat": 70.2129,    "lon": -150.9998,  "icao": "PAQT", "note": "North Slope village"},
    {"id": "MWOS:430", "name": "Kaktovik MWOS",        "lat": 70.1101,    "lon": -143.635,   "icao": "",     "note": "Arctic Refuge / Barter Island"},
    {"id": "MWOS:2",   "name": "Rampart MWOS",         "lat": 65.51125,   "lon": -150.15225, "icao": "PRMP", "note": "Yukon River"},
    {"id": "MWOS:694", "name": "Port Graham MWOS",     "lat": 59.350842,  "lon": -151.827721,"icao": "",     "note": "Lower Kenai Peninsula"},
    {"id": "MWOS:232", "name": "Port Townsend MWOS",   "lat": 48.106887,  "lon": -122.77775, "icao": "",     "note": "WA state — out-of-region reference"},
    # ── Supplemental airports without MWOS (METAR + model only) ──
    {"id": "PANC", "name": "Anchorage Intl",     "lat": 61.1744, "lon": -149.9964, "icao": "PANC", "note": "Class C / Pacific gateway"},
    {"id": "PAED", "name": "Elmendorf AFB",      "lat": 61.2510, "lon": -149.8063, "icao": "PAED", "note": "JBER military / Anchorage Bowl"},
    {"id": "PAAQ", "name": "Palmer",             "lat": 61.5949, "lon": -149.0887, "icao": "PAAQ", "note": "Mat-Su valley"},
    {"id": "PAJN", "name": "Juneau",             "lat": 58.3547, "lon": -134.5762, "icao": "PAJN", "note": "SE AK / coastal terrain"},
    {"id": "PADQ", "name": "Kodiak",             "lat": 57.7500, "lon": -152.4939, "icao": "PADQ", "note": "Kodiak Island"},
    {"id": "PAEN", "name": "Kenai Municipal",    "lat": 60.5731, "lon": -151.2450, "icao": "PAEN", "note": "Kenai Peninsula"},
]

def _wx_db_init():
    """Create the wx_obs table + indexes if missing. Idempotent."""
    os.makedirs(os.path.dirname(_WX_DB_PATH), exist_ok=True)
    with sqlite3.connect(_WX_DB_PATH) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS wx_obs (
              ts             INTEGER NOT NULL,    -- unix epoch seconds (snapshot bucket)
              anchor         TEXT    NOT NULL,    -- ICAO
              source         TEXT    NOT NULL,    -- 'metar' | 'mwos' | 'nws' | 'om'
              dir_deg        REAL,
              speed_kt       REAL,
              gust_kt        REAL,
              temp_c         REAL,
              visibility_sm  REAL,
              pressure_mb    REAL,
              cloud_pct      REAL,
              raw_json       TEXT,                -- full unified-shape blob, for forensics
              PRIMARY KEY (ts, anchor, source)
            );
            CREATE INDEX IF NOT EXISTS idx_wx_obs_ts ON wx_obs(ts);
            CREATE INDEX IF NOT EXISTS idx_wx_obs_anchor_ts ON wx_obs(anchor, ts);
        """)
        con.commit()

def _wx_db_insert(ts, anchor, source_tag, p):
    """Insert (or replace) one row. p is a unified-shape point dict."""
    if p is None:
        return
    with _WX_DB_LOCK, sqlite3.connect(_WX_DB_PATH) as con:
        con.execute("""
            INSERT OR REPLACE INTO wx_obs
              (ts, anchor, source, dir_deg, speed_kt, gust_kt, temp_c,
               visibility_sm, pressure_mb, cloud_pct, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            int(ts), anchor, source_tag,
            p.get("dir_deg"), p.get("speed_kt"), p.get("gust_kt"),
            p.get("temp_c"), p.get("visibility_sm"), p.get("pressure_mb"),
            p.get("cloud_pct"), json.dumps(p, default=str)
        ))
        con.commit()

def _wx_db_purge():
    """Delete rows older than _WX_RETENTION."""
    cutoff = int(time.time()) - _WX_RETENTION
    with _WX_DB_LOCK, sqlite3.connect(_WX_DB_PATH) as con:
        con.execute("DELETE FROM wx_obs WHERE ts < ?", (cutoff,))
        con.commit()

def _wx_db_take_snapshot():
    """Pull every (anchor × source) right now and write to the DB. Designed
    to be called from the background thread every 10 min; also reusable from
    a debug endpoint."""
    try:
        # Trigger any uncached upstream fetches by calling the resolution
        # paths (these all cache internally).
        _ = _fetch_model_grid()
        anchor_lls = [(a["lat"], a["lon"]) for a in _WX_VALIDATE_ANCHORS]
        om_pts = _fetch_anchor_openmeteo(anchor_lls)

        # Warm METAR_CACHE / mwos_* via direct call paths if they haven't yet.
        # Note: no jsonify here — we just need the side effect of populating cache.
        try:
            with app.test_request_context():
                api_weather()  # warms METAR_CACHE
                api_mwos()     # warms _WX_CACHE['mwos_*']
        except Exception:
            pass

        metar_cache = METAR_CACHE.get("data") or {}
        metar_raws = metar_cache.get("metars", {})
        metar_meta = metar_cache.get("meta", {})

        # Round to nearest 5-min bucket so concurrent snapshots align cleanly.
        ts_bucket = int(time.time() // 300) * 300

        # Build MWOS proximity lookup once
        mwos_obs = []
        for hx, entry in _WX_CACHE.items():
            if not hx.startswith("mwos_"):
                continue
            d = entry.get("data") or {}
            obs_list = d.get("observations", []) or []
            if obs_list:
                mwos_obs.append((d, obs_list[0]))

        def nearest_mwos(lat, lon, max_nm=5):
            best = None; best_d = max_nm
            for d, latest in mwos_obs:
                dist = _great_circle_nm((lat, lon), (d.get("latitude", 0), d.get("longitude", 0)))
                if dist < best_d:
                    best = (d, latest); best_d = dist
            return best

        rows_written = 0
        for idx, anc in enumerate(_WX_VALIDATE_ANCHORS):
            anchor_id = anc["id"]
            lat, lon = anc["lat"], anc["lon"]
            icao = anc.get("icao") or ""
            m_raw = metar_raws.get(icao) if icao else None
            metar_pt = (_parse_metar_to_point(icao, m_raw, metar_meta.get(icao, {}).get("reportTime", ""))
                        if m_raw and m_raw != "(unavailable)" else None)
            # Specific MWOS for MWOS:N anchors, else closest-within-5nm
            mwos_pt = None
            if anchor_id.startswith("MWOS:"):
                site_id = anchor_id.split(":", 1)[1]
                ce = _WX_CACHE.get(f"mwos_{site_id}", {}).get("data") or {}
                obs_l = ce.get("observations", []) or []
                if obs_l:
                    mwos_pt = _mwos_to_point(ce, obs_l[0])
            else:
                nearest = nearest_mwos(lat, lon)
                mwos_pt = _mwos_to_point(nearest[0], nearest[1]) if nearest else None
            nws_pt = _fetch_nws_gridpoint(lat, lon)
            anchor_models = om_pts[idx] if idx < len(om_pts) else []
            # Build by-source map for the certified composite
            by_source = {"metar": metar_pt, "mwos": mwos_pt, "nws": nws_pt}
            for j, (_om_id, src_tag, _label, _note) in enumerate(_OM_MODELS):
                by_source[src_tag] = anchor_models[j] if j < len(anchor_models) else None

            # Log each individual source
            for tag, pt in by_source.items():
                if pt is not None:
                    _wx_db_insert(ts_bucket, anchor_id, tag, pt)
                    rows_written += 1
            # Compute and log the SkyBridge Composite (weighted-ensemble)
            cert_pt = _compute_certified(by_source)
            if cert_pt:
                _wx_db_insert(ts_bucket, anchor_id, "certified", cert_pt)
                rows_written += 1
        _wx_db_purge()
        print(f"[wx-validate] snapshot ts={ts_bucket} wrote {rows_written} rows")
        return rows_written
    except Exception as e:
        print(f"[wx-validate] snapshot error: {e}")
        return 0

def _wx_db_query_at(ts_target):
    """Return rows nearest to ts_target (within ±15 min). Grouped by
    (anchor, source). Returns dict of {(anchor, source): unified_pt_dict}."""
    with sqlite3.connect(_WX_DB_PATH) as con:
        con.row_factory = sqlite3.Row
        # Pick the row per (anchor,source) whose ts is closest to ts_target.
        # Simple approach: pull all rows in ±15 min window, then in Python
        # pick nearest per group.
        win = 15 * 60
        rows = con.execute("""
            SELECT * FROM wx_obs
            WHERE ts BETWEEN ? AND ?
        """, (ts_target - win, ts_target + win)).fetchall()
    grouped = {}
    for r in rows:
        key = (r["anchor"], r["source"])
        prev = grouped.get(key)
        if prev is None or abs(r["ts"] - ts_target) < abs(prev["ts"] - ts_target):
            grouped[key] = dict(r)
    # Re-hydrate to unified-shape dicts
    out = {}
    for (anchor, source), r in grouped.items():
        try:
            blob = json.loads(r["raw_json"]) if r.get("raw_json") else None
        except Exception:
            blob = None
        out[(anchor, source)] = blob or {
            "dir_deg": r["dir_deg"], "speed_kt": r["speed_kt"], "gust_kt": r["gust_kt"],
            "temp_c": r["temp_c"], "visibility_sm": r["visibility_sm"],
            "pressure_mb": r["pressure_mb"], "cloud_pct": r["cloud_pct"],
            "source": source + ":hist",
        }
    return out

def _wx_db_available_hours():
    """Returns sorted list of distinct hour-bucket epochs that have at least
    one row. Used by the date+hour picker to grey out unavailable hours."""
    with sqlite3.connect(_WX_DB_PATH) as con:
        rows = con.execute("""
            SELECT DISTINCT (ts / 3600) * 3600 AS h FROM wx_obs ORDER BY h DESC
        """).fetchall()
    return [r[0] for r in rows]

def _wx_logger_loop():
    """Background snapshot loop. Started once at import time."""
    while True:
        try:
            _wx_db_take_snapshot()
        except Exception as e:
            print(f"[wx-validate] loop error: {e}")
        time.sleep(_WX_LOG_PERIOD)

# Init DB + start the background snapshot thread
try:
    _wx_db_init()
    _wx_logger_thread = threading.Thread(target=_wx_logger_loop, daemon=True, name="wx-validate-logger")
    _wx_logger_thread.start()
except Exception as e:
    print(f"[wx-validate] failed to start logger: {e}")


@app.route("/api/wx-validate/timeline")
def api_wx_validate_timeline():
    """List of all hour-buckets that have at least one logged snapshot,
    most-recent first. Also returns row count + DB size for diagnostics."""
    hours = _wx_db_available_hours()
    db_size = os.path.getsize(_WX_DB_PATH) if os.path.exists(_WX_DB_PATH) else 0
    with sqlite3.connect(_WX_DB_PATH) as con:
        total = con.execute("SELECT COUNT(*) FROM wx_obs").fetchone()[0]
    return jsonify({
        "hours": hours,
        "count": len(hours),
        "row_total": total,
        "db_bytes": db_size,
        "retention_days": _WX_RETENTION // 86400,
        "log_period_s": _WX_LOG_PERIOD,
    })


@app.route("/api/wx-validate/snapshot-now")
def api_wx_validate_snapshot_now():
    """Force an immediate snapshot. Used by the dashboard's 'snapshot now'
    button. Returns row count written."""
    n = _wx_db_take_snapshot()
    return jsonify({"rows_written": n, "ts": int(time.time())})


@app.route("/wx-validate")
def wx_validate():
    """Multi-source weather comparison dashboard. Side-by-side rendering of
    the same observation across:
        NWS METAR     — FAA-approved authoritative observation
        MWOS          — Montis Corp calibrated automated weather (private)
        NWS Grid      — FAA-approved gridded forecast model
        Open-Meteo    — open-data model used as backfill
    For each anchor station we render: wind, temp, vis, pressure, cloud cover,
    plus per-field deltas vs the METAR baseline. The whole point is to show
    exactly where SkyBridge agrees with the canonical sources, where it
    enriches them with MWOS, and where any drift indicates a sensor needing
    calibration. This page is the seed of the FAA certification artifact.
    """
    # Pull every anchor — full MWOS network + supplemental airports. List is
    # the module-level _WX_VALIDATE_ANCHORS so the historical logger uses
    # exactly the same set.
    ANCHORS = _WX_VALIDATE_ANCHORS

    # Optional ?ts=<epoch> query param to replay a past hour from the DB.
    # If absent, we render the live now.
    from flask import request as _flask_request
    ts_q = _flask_request.args.get("ts", "").strip()
    historical = None
    if ts_q.isdigit():
        try:
            historical = _wx_db_query_at(int(ts_q))
        except Exception:
            historical = None

    # Fetch the live sources for all anchors. MWOS + METAR already in cache.
    # Open-Meteo: one batched call returns ALL models in one shot. NWS
    # gridpoint: one or two calls per anchor (cached). Historical mode pulls
    # everything from the DB instead.
    if historical is None:
        anchor_lls = [(a["lat"], a["lon"]) for a in ANCHORS]
        om_pts = _fetch_anchor_openmeteo(anchor_lls)
        # om_pts is list-of-list: [anchor_idx][model_idx]
        om_by_id = {}
        for i in range(len(ANCHORS)):
            anc_id = ANCHORS[i]["id"]
            anc_models = om_pts[i] if i < len(om_pts) else []
            om_by_id[anc_id] = {}
            for j, (_om_id, src_tag, _label, _note) in enumerate(_OM_MODELS):
                om_by_id[anc_id][src_tag] = anc_models[j] if j < len(anc_models) else None
    else:
        om_by_id = {}

    # Find the closest MWOS station to each anchor (within 5 nm = "co-located").
    mwos_obs = []
    for hx, entry in _WX_CACHE.items():
        if not hx.startswith("mwos_"):
            continue
        d = entry.get("data") or {}
        obs_list = d.get("observations", []) or []
        if not obs_list:
            continue
        mwos_obs.append((d, obs_list[0]))

    def nearest_mwos(lat, lon, max_nm=5):
        best = None
        best_d = max_nm
        for d, latest in mwos_obs:
            mlat = d.get("latitude", 0)
            mlon = d.get("longitude", 0)
            dist = _great_circle_nm((lat, lon), (mlat, mlon))
            if dist < best_d:
                best = (d, latest)
                best_d = dist
        return best

    # Pull METAR raw for each anchor & parse into unified shape.
    metar_cache = METAR_CACHE.get("data") or {}
    metar_raws = metar_cache.get("metars", {})
    metar_meta = metar_cache.get("meta", {})

    # ── Render helpers ────────────────────────────────────────────────────
    def fmt_wind(p):
        if p is None or p.get("dir_deg") is None or p.get("speed_kt") is None: return "—"
        d = p["dir_deg"]
        s = p["speed_kt"]
        g = p.get("gust_kt") or 0
        d_str = "VRB" if d == -1 else f"{int(d):03d}°"
        return f'{d_str} / {s:.0f}{f"G{g:.0f}" if g >= 1 else ""} kt'

    def fmt_temp(p):
        v = p.get("temp_c") if p else None
        return f'{v:.1f} °C' if isinstance(v, (int, float)) else "—"

    def fmt_vis(p):
        v = p.get("visibility_sm") if p else None
        return f'{v:.1f} sm' if isinstance(v, (int, float)) else "—"

    def fmt_press(p):
        v = p.get("pressure_mb") if p else None
        return f'{v:.1f} mb' if isinstance(v, (int, float)) else "—"

    def fmt_cloud(p):
        v = p.get("cloud_pct") if p else None
        return f'{int(v)}%' if isinstance(v, (int, float)) else "—"

    def delta_class(value, baseline, ok, marginal):
        """Return a CSS class based on |delta| vs thresholds."""
        if value is None or baseline is None: return "na"
        try:
            d = abs(float(value) - float(baseline))
        except (TypeError, ValueError):
            return "na"
        if d <= ok: return "ok"
        if d <= marginal: return "warn"
        return "bad"

    def cell_html(p, baseline, field_key, fmt_fn,
                  ok_thresh=None, marginal_thresh=None, extra_class=""):
        """Render a comparison cell — value + colored delta-class background.
        extra_class allows the certified column to add a distinguishing border."""
        ec = (" " + extra_class) if extra_class else ""
        if p is None:
            return f'<td class="empty{ec}">—</td>'
        if baseline is None or baseline is p:
            cls = "baseline"
        else:
            cls = delta_class(p.get(field_key), baseline.get(field_key), ok_thresh or 1, marginal_thresh or 3)
        return f'<td class="{cls}{ec}">{fmt_fn(p)}</td>'

    rows_html = ""
    summary_acc = {"wind_spd": [], "temp": [], "press": []}
    for anc in ANCHORS:
        anchor_id = anc["id"]
        name = anc["name"]
        note = anc["note"]
        lat = anc["lat"]
        lon = anc["lon"]
        icao = anc.get("icao") or ""
        # Build per-source unified-shape points. Historical mode reads from DB;
        # live mode pulls from current caches + makes any needed upstream calls.
        if historical is not None:
            metar_pt = historical.get((anchor_id, "metar"))
            mwos_pt  = historical.get((anchor_id, "mwos"))
            nws_pt   = historical.get((anchor_id, "nws"))
            om_models = {tag: historical.get((anchor_id, tag)) for _id, tag, _l, _n in _OM_MODELS}
        else:
            m_raw = metar_raws.get(icao) if icao else None
            metar_pt = (_parse_metar_to_point(icao, m_raw, metar_meta.get(icao, {}).get("reportTime", ""))
                        if m_raw and m_raw != "(unavailable)" else None)
            mwos_pt = None
            if anchor_id.startswith("MWOS:"):
                site_id = anchor_id.split(":", 1)[1]
                cache_entry = _WX_CACHE.get(f"mwos_{site_id}", {}).get("data") or {}
                obs_list = cache_entry.get("observations", []) or []
                if obs_list:
                    mwos_pt = _mwos_to_point(cache_entry, obs_list[0])
            else:
                nearest = nearest_mwos(lat, lon)
                mwos_pt = _mwos_to_point(nearest[0], nearest[1]) if nearest else None
            nws_pt = _fetch_nws_gridpoint(lat, lon)
            om_models = om_by_id.get(anchor_id, {})

        # Build the source list: 3 fixed (METAR, MWOS, NWS Grid) + N models
        sources = [
            ("METAR",     metar_pt, "FAA-approved obs"),
            ("MWOS",      mwos_pt,  "Montis-calibrated"),
            ("NWS Grid",  nws_pt,   "FAA-approved fcst"),
        ]
        for _om_id, src_tag, label, _note in _OM_MODELS:
            sources.append((label, om_models.get(src_tag), "model"))

        # Compute the SkyBridge Composite — weighted mean across all
        # sources that reported. Appears as the rightmost column.
        if historical is not None:
            cert_pt = historical.get((anchor_id, "certified"))
        else:
            by_source = {"metar": metar_pt, "mwos": mwos_pt, "nws": nws_pt}
            for _om_id, src_tag, _label, _note in _OM_MODELS:
                by_source[src_tag] = om_models.get(src_tag)
            cert_pt = _compute_certified(by_source)
        sources.append(("SkyBridge", cert_pt, "certified"))

        # Track per-field deltas vs METAR for the summary table at the bottom.
        # Skip METAR itself (sources[0]); evaluate every other source including
        # the SkyBridge composite at the end.
        if metar_pt:
            for label, src, _kind in sources[1:]:
                if not src: continue
                src_id = src.get("source", "")
                if metar_pt.get("speed_kt") is not None and src.get("speed_kt") is not None:
                    summary_acc["wind_spd"].append((anchor_id, src_id, src["speed_kt"] - metar_pt["speed_kt"]))
                if metar_pt.get("temp_c") is not None and src.get("temp_c") is not None:
                    summary_acc["temp"].append((anchor_id, src_id, src["temp_c"] - metar_pt["temp_c"]))
                if metar_pt.get("pressure_mb") is not None and src.get("pressure_mb") is not None:
                    summary_acc["press"].append((anchor_id, src_id, src["pressure_mb"] - metar_pt["pressure_mb"]))

        # Display id: ICAO if METAR-paired, otherwise the MWOS:nnn id
        display_id = icao if icao else anchor_id
        # Helper: render one row; last source (certified composite) gets cert-cell
        def _row_cells(field, fmt_fn, ok_t, marg_t):
            html_acc = ""
            for i, (lbl, pt, kind) in enumerate(sources):
                ec = "cert-cell" if kind == "certified" else ""
                html_acc += cell_html(pt, metar_pt, field, fmt_fn, ok_t, marg_t, extra_class=ec)
            return html_acc

        rows_html += f'''
        <tr class="anchor-row">
          <td class="anchor" rowspan="5"><strong>{display_id}</strong><br><span class="aname">{name}</span><br><span class="anote">{note}</span></td>
          <td class="metric">Wind</td>{_row_cells("speed_kt", fmt_wind, 3, 7)}</tr>
        <tr><td class="metric">Temp</td>{_row_cells("temp_c", fmt_temp, 1, 3)}</tr>
        <tr><td class="metric">Visibility</td>{_row_cells("visibility_sm", fmt_vis, 1, 3)}</tr>
        <tr><td class="metric">Pressure</td>{_row_cells("pressure_mb", fmt_press, 1, 3)}</tr>
        <tr class="row-end"><td class="metric">Cloud</td>{_row_cells("cloud_pct", fmt_cloud, 20, 40)}</tr>
'''

    # Summary stats: per-source MAE vs METAR
    def mae(deltas, src_filter):
        vals = [abs(d) for icao, src, d in deltas if src.startswith(src_filter)]
        if not vals: return ("—", 0)
        return (f"{sum(vals)/len(vals):.2f}", len(vals))
    def bias(deltas, src_filter):
        vals = [d for icao, src, d in deltas if src.startswith(src_filter)]
        if not vals: return "—"
        b = sum(vals)/len(vals)
        return f"{b:+.2f}"

    src_keys = [("MWOS", "mwos:"), ("NWS Grid", "nws:")]
    for _om_id, src_tag, label, _note in _OM_MODELS:
        src_keys.append((label, "model:" + src_tag))
    src_keys.append(("SkyBridge Composite", "certified:"))
    summary_rows = ""
    for label, prefix in src_keys:
        ws_mae, ws_n = mae(summary_acc["wind_spd"], prefix)
        ws_bi = bias(summary_acc["wind_spd"], prefix)
        t_mae, t_n  = mae(summary_acc["temp"], prefix)
        t_bi = bias(summary_acc["temp"], prefix)
        p_mae, p_n  = mae(summary_acc["press"], prefix)
        p_bi = bias(summary_acc["press"], prefix)
        summary_rows += f'''
        <tr>
          <td><strong>{label}</strong></td>
          <td>{ws_mae} <span class="bias">({ws_bi})</span> <span class="n">n={ws_n}</span></td>
          <td>{t_mae} <span class="bias">({t_bi})</span> <span class="n">n={t_n}</span></td>
          <td>{p_mae} <span class="bias">({p_bi})</span> <span class="n">n={p_n}</span></td>
        </tr>'''

    # Provenance strip — which upstreams are FAA-approved, calibrated, or open
    metar_age = int(time.time() - METAR_CACHE.get("ts", 0)) if METAR_CACHE.get("ts") else None
    om_cache = _WX_CACHE.get("openmeteo_anchors") or {}
    om_age = int(time.time() - om_cache.get("ts", 0)) if om_cache.get("ts") else None
    nws_cache_keys = [k for k in _WX_CACHE if k.startswith("nws_grid_")]
    nws_age = None
    if nws_cache_keys:
        ages = [int(time.time() - _WX_CACHE[k].get("ts", 0)) for k in nws_cache_keys]
        nws_age = min(ages) if ages else None
    mwos_cache_keys = [k for k in _WX_CACHE if k.startswith("mwos_")]
    mwos_age = None
    if mwos_cache_keys:
        ages = [int(time.time() - _WX_CACHE[k].get("ts", 0)) for k in mwos_cache_keys]
        mwos_age = min(ages) if ages else None

    def age_str(s):
        if s is None: return "—"
        if s < 60:  return f"{s}s ago"
        if s < 3600: return f"{s//60}m ago"
        return f"{s//3600}h ago"

    return f'''<!doctype html><html><head><meta charset="utf-8"><title>Weather Validate — DEV</title>
    <style>
    body {{ background:#0a0e16; color:#d8e1ec; font-family:system-ui,sans-serif; padding:24px; margin:0; }}
    h1 {{ color:#23d18b; font-size:18px; letter-spacing:2px; text-transform:uppercase; margin:0 0 8px; }}
    h1 .dev {{ background:#ff8800; color:#000; padding:2px 8px; border-radius:4px; font-size:12px; margin-left:8px; vertical-align:middle; }}
    h1 a {{ color:#0090ff; font-size:11px; letter-spacing:1px; margin-left:14px; text-decoration:none; }}
    p.lede {{ color:#9aa5b8; font-size:13px; max-width:980px; line-height:1.5; }}
    h2 {{ color:#0090ff; font-size:13px; text-transform:uppercase; letter-spacing:1.5px; margin:28px 0 8px; }}
    h2 small {{ color:#7a8497; font-weight:400; text-transform:none; letter-spacing:0; margin-left:8px; font-size:11px; }}

    .provenance {{ display:flex; gap:14px; flex-wrap:wrap; background:#141a26; border-radius:8px; padding:14px 18px; margin-top:8px; }}
    .prov-chip {{ display:flex; flex-direction:column; gap:2px; padding:8px 14px; border-radius:6px; min-width:140px; background:#1a2030; border:1px solid #2a3140; }}
    .prov-chip .top {{ font-size:11px; font-weight:700; letter-spacing:1px; text-transform:uppercase; }}
    .prov-chip .age {{ font-size:11px; color:#9aa5b8; }}
    .prov-chip .tag {{ font-size:10px; color:#9aa5b8; }}
    .faa-approved .top {{ color:#23d18b; }}
    .faa-approved .tag {{ color:#23d18b; }}
    .calibrated .top {{ color:#ffbb00; }}
    .calibrated .tag {{ color:#ffbb00; }}
    .open .top {{ color:#88ccff; }}
    .open .tag {{ color:#88ccff; }}
    .composite .top {{ color:#cc44ff; }}
    .composite .tag {{ color:#cc44ff; }}

    table.cmp {{ width:100%; border-collapse:collapse; margin-top:14px; background:#141a26; border-radius:8px; overflow:hidden; }}
    table.cmp th {{ background:#1f2738; color:#9aa5b8; text-transform:uppercase; letter-spacing:1px; font-size:10px; padding:10px; text-align:left; border-bottom:1px solid #2a3140; }}
    table.cmp th.src {{ width:18%; }}
    table.cmp th.metric {{ width:10%; }}
    table.cmp th.anchor {{ width:18%; }}
    table.cmp td {{ padding:8px 12px; font-size:12px; border-bottom:1px solid #1a2030; vertical-align:middle; }}
    table.cmp td.anchor {{ background:#0d121d; vertical-align:middle; border-right:2px solid #2a3140; }}
    table.cmp td.anchor strong {{ color:#23d18b; font-size:14px; }}
    table.cmp td.anchor .aname {{ color:#d8e1ec; font-size:11px; }}
    table.cmp td.anchor .anote {{ color:#7a8497; font-size:10px; font-style:italic; }}
    table.cmp td.metric {{ color:#0090ff; font-weight:700; font-size:11px; }}
    table.cmp td.empty {{ color:#445; text-align:center; }}
    table.cmp td.baseline {{ color:#fff; background:#1f2738; font-weight:700; }}
    table.cmp td.ok {{ color:#23d18b; }}
    table.cmp td.warn {{ color:#ffbb00; }}
    table.cmp td.bad {{ color:#ff5040; font-weight:700; }}
    table.cmp td.na {{ color:#445; }}
    table.cmp tr.row-end td {{ border-bottom:2px solid #2a3140; padding-bottom:14px; }}
    table.cmp tr.anchor-row td {{ border-top:6px solid #0a0e16; padding-top:14px; }}
    table.cmp .faa {{ background:#1a4030; color:#23d18b; font-size:8px; padding:1px 4px; border-radius:3px; margin-left:4px; }}
    table.cmp .cal {{ background:#3a3520; color:#ffbb00; font-size:8px; padding:1px 4px; border-radius:3px; margin-left:4px; }}
    table.cmp .mdl {{ background:#2a3140; color:#88ccff; font-size:8px; padding:1px 4px; border-radius:3px; margin-left:4px; }}
    table.cmp th.cert {{ background:#2a1a3a; color:#cc88ff; border-left:2px solid #cc44ff; }}
    table.cmp .cert-tag {{ background:#cc44ff; color:#0a0e16; font-size:8px; padding:1px 4px; border-radius:3px; margin-left:4px; font-weight:700; }}
    table.cmp td.cert-cell {{ border-left:2px solid #cc44ff; background:#1a1228; color:#cc88ff; font-weight:700; }}
    table.cmp td.cert-cell.ok {{ color:#cc88ff; }}
    table.cmp td.cert-cell.warn {{ color:#ffbb88; }}
    table.cmp td.cert-cell.bad {{ color:#ff88aa; }}
    .weights {{ background:#141a26; border-radius:8px; padding:14px 18px; margin-top:8px; display:flex; gap:18px; flex-wrap:wrap; align-items:center; font-size:12px; }}
    .weights .wlabel {{ color:#9aa5b8; font-weight:700; letter-spacing:1px; text-transform:uppercase; font-size:10px; }}
    .weight-pill {{ display:inline-flex; gap:6px; align-items:center; background:#1a2030; padding:5px 10px; border-radius:6px; border:1px solid #2a3140; font-size:11px; }}
    .weight-pill .src-name {{ color:#d8e1ec; font-weight:700; }}
    .weight-pill .src-w {{ color:#ffd24c; }}

    table.summary {{ border-collapse:collapse; margin-top:14px; background:#141a26; border-radius:8px; overflow:hidden; min-width:580px; }}
    table.summary th {{ background:#1f2738; color:#9aa5b8; padding:10px 14px; font-size:10px; text-transform:uppercase; letter-spacing:1px; }}
    table.summary td {{ padding:10px 14px; font-size:12px; border-bottom:1px solid #2a3140; }}
    table.summary .bias {{ color:#ffbb00; font-size:10px; }}
    table.summary .n {{ color:#7a8497; font-size:10px; margin-left:6px; }}

    .legend {{ display:flex; gap:14px; flex-wrap:wrap; font-size:11px; color:#9aa5b8; align-items:center; margin:6px 0; }}
    .legend span {{ display:flex; align-items:center; gap:5px; }}
    .legend .swatch {{ width:14px; height:14px; border-radius:3px; }}
    .picker {{ display:flex; gap:14px; align-items:center; flex-wrap:wrap; background:#141a26; border-radius:8px; padding:14px 18px; margin-top:8px; font-size:12px; }}
    .picker-controls {{ display:flex; gap:8px; align-items:center; }}
    .picker input[type=datetime-local] {{ background:#0a0e16; color:#d8e1ec; border:1px solid #2a3140; border-radius:6px; padding:6px 10px; font-family:inherit; font-size:12px; color-scheme:dark; }}
    .btn {{ background:#1a2030; color:#9aa5b8; border:1px solid #2a3140; border-radius:6px; padding:6px 12px; cursor:pointer; font-family:inherit; font-size:12px; text-decoration:none; }}
    .btn:hover {{ background:#2a3140; color:#d8e1ec; }}
    .btn.active {{ background:#0090ff; color:#0a0e16; border-color:#0090ff; }}
    code {{ background:#1f2738; padding:1px 5px; border-radius:3px; color:#23d18b; font-size:11px; }}
    .note {{ background:#1f2738; padding:14px 18px; border-radius:8px; margin-top:24px; font-size:12px; line-height:1.6; color:#9aa5b8; max-width:1100px; }}
    </style></head><body>

    <h1>Weather Validate <span class="dev">DEV — COMPOSITE</span>
        <a href="/icons-preview">→ icons-preview</a>
        <a href="/wx-icons-preview">→ wx-icons-preview</a></h1>

    <p class="lede">Side-by-side comparison of every weather source feeding the kneeboard. The point: prove SkyBridge agrees with the FAA-authoritative sources where they exist, and show where it adds new value through calibrated MWOS observations and open-data model fill. This is the seed artifact for a future FAA accuracy-attestation submission.</p>

    <h2>Source Provenance <small>which upstreams are authoritative, which are calibrated private, which are open-data</small></h2>
    <div class="provenance">
      <div class="prov-chip faa-approved">
        <div class="top">🟢 NWS METAR</div>
        <div class="age">{age_str(metar_age)}</div>
        <div class="tag">✅ FAA-approved observation</div>
      </div>
      <div class="prov-chip faa-approved">
        <div class="top">🟢 NWS Gridpoint</div>
        <div class="age">{age_str(nws_age)}</div>
        <div class="tag">✅ FAA-approved forecast model</div>
      </div>
      <div class="prov-chip calibrated">
        <div class="top">🟡 MWOS</div>
        <div class="age">{age_str(mwos_age)}</div>
        <div class="tag">⚙️ Montis-calibrated, private</div>
      </div>
      <div class="prov-chip open">
        <div class="top">⚪ Open-Meteo</div>
        <div class="age">{age_str(om_age)}</div>
        <div class="tag">⚙️ Open-data model backfill</div>
      </div>
      <div class="prov-chip composite">
        <div class="top">🟣 SkyBridge IDW</div>
        <div class="age">realtime</div>
        <div class="tag">⚙️ Composite (METAR + MWOS + model)</div>
      </div>
    </div>

    <h2>Time Travel <small>replay any snapshot from the last 30 days · DB logs every 10 min</small></h2>
    <div class="picker">
      <span style="color:#9aa5b8">{"📡 LIVE — now" if historical is None else f"⏪ Historical: showing snapshot at ts={ts_q}"}</span>
      <a class="btn{' active' if historical is None else ''}" href="/wx-validate">📡 Live</a>
      <span class="picker-controls">
        <input type="datetime-local" id="tsPicker" />
        <button class="btn" onclick="(function(){{var v=document.getElementById('tsPicker').value;if(!v)return;var t=Math.floor(new Date(v).getTime()/1000);location.href='/wx-validate?ts='+t;}})()">Go</button>
        <button class="btn" onclick="fetch('/api/wx-validate/snapshot-now').then(r=>r.json()).then(d=>{{alert('Wrote '+d.rows_written+' rows at ts='+d.ts);location.reload();}})">Snapshot now →</button>
        <a class="btn" href="/api/wx-validate/timeline" target="_blank">Timeline JSON</a>
      </span>
    </div>

    <h2>SkyBridge Composite Weights <small>weighted-mean ensemble of authoritative sources · not a published or attested value · tunable from observed historical agreement</small></h2>
    <div class="weights">
      <span class="wlabel">Weights →</span>
      {''.join(f'<span class="weight-pill"><span class="src-name">{_WX_CERT_LABEL.get(tag, tag)}</span><span class="src-w">×{w:.2f}</span></span>' for tag, w in _WX_CERT_WEIGHTS.items())}
    </div>

    <h2>Per-Anchor Side-by-Side <small>METAR is the baseline; deltas highlight where each source agrees / drifts</small></h2>
    <div class="legend">
      <span><span class="swatch" style="background:#1f2738"></span>baseline (METAR)</span>
      <span><span class="swatch" style="background:#23d18b"></span>OK</span>
      <span><span class="swatch" style="background:#ffbb00"></span>marginal</span>
      <span><span class="swatch" style="background:#ff5040"></span>drift</span>
      <span><span class="swatch" style="background:#445"></span>not reported</span>
    </div>
    <table class="cmp">
      <tr>
        <th class="anchor">Anchor</th>
        <th class="metric">Field</th>
        <th class="src">NWS METAR <span class="faa">FAA</span></th>
        <th class="src">MWOS <span class="cal">cal</span></th>
        <th class="src">NWS Grid <span class="faa">FAA</span></th>
        {''.join(f'<th class="src">{label} <span class="mdl" title="{note}">model</span></th>' for _om_id, _src_tag, label, note in _OM_MODELS)}
        <th class="src cert">SkyBridge <span class="cert-tag">COMPOSITE</span></th>
      </tr>
      {rows_html}
    </table>

    <h2>Aggregate Agreement vs METAR Baseline <small>mean absolute error · (signed bias) · n=samples</small></h2>
    <table class="summary">
      <tr><th>Source</th><th>Wind speed (kt)</th><th>Temp (°C)</th><th>Pressure (mb)</th></tr>
      {summary_rows}
    </table>

    <div class="note">
      <strong>How to read this:</strong> the highlighted cell in each row is the METAR baseline (FAA-authoritative observation). Each adjacent cell is a different source reporting <em>the same field at the same lat/lon at roughly the same time</em>. Color = how close the source is to the METAR.
      <ul style="margin:8px 0 0 18px;padding:0;">
        <li><strong>OK thresholds:</strong> wind ±3kt, temp ±1°C, vis ±1sm, pressure ±1mb, cloud ±20%</li>
        <li><strong>Marginal:</strong> wind ±7kt, temp ±3°C, vis ±3sm, pressure ±3mb, cloud ±40%</li>
        <li><strong>Drift</strong> (red): everything beyond marginal — sensor or model is meaningfully out of agreement</li>
      </ul>
      <p style="margin:14px 0 0"><strong>Roadmap toward FAA cert:</strong> add 24/7 historical logging of these deltas so we can produce a 90-day report card per station per field. That report card is the kind of document that goes into an FAA accuracy-attestation package. Architecture is already in place — same <code>/api/wx/grid</code> endpoint, same unified shape, plus a periodic snapshot writer.</p>
      <p><strong>Other anchors to consider:</strong> high-elevation MWOS (Anaktuvuk Pass, Thompson Pass) — when their station-pressure differs from sea-level pressure by >50mb, that's a calibration consistency check, not a drift. We can add an elevation-aware mode for those.</p>
    </div>
    </body></html>'''


# ── /api/wx-lens — multi-source per-source grid for the shootout map ──────
@app.route("/api/wx-lens")
def api_wx_lens():
    """Returns per-source grid data so the /wx-shootout map can render each
    source as its own color layer. Sources:
      metar, mwos      — sparse station observations
      om_gfs, om_gem,
      om_ecmwf, om_jma — model values across the local 81-pt grid
      certified        — weighted-mean composite at every grid point
    """
    # Warm the obs caches if cold — METAR/MWOS pins won't appear otherwise.
    # /api/weather + /api/mwos populate the in-process caches that
    # _collect_observations() reads from.
    if not METAR_CACHE.get("data"):
        try:
            with app.test_request_context():
                api_weather()
        except Exception:
            pass
    has_mwos = any(k.startswith("mwos_") for k in _WX_CACHE)
    if not has_mwos:
        try:
            with app.test_request_context():
                api_mwos()
        except Exception:
            pass
    # Re-run the multi-model fetch but against the LOCAL 81-pt grid (not the
    # 7 cert anchors). _fetch_anchor_openmeteo already supports arbitrary
    # lat/lon lists; just feed it the model grid instead.
    grid_pts = _AK_WIND_GRID
    om_models_at_grid = _fetch_anchor_openmeteo(grid_pts)
    by_src = {tag: [] for _id, tag, _l, _n in _OM_MODELS}
    by_src["certified"] = []
    for i, (lat, lon) in enumerate(grid_pts):
        models_at_pt = om_models_at_grid[i] if i < len(om_models_at_grid) else []
        # Per-model: route each model's pt into its source bucket
        per_model = {}
        for j, (_om_id, src_tag, _label, _note) in enumerate(_OM_MODELS):
            pt = models_at_pt[j] if j < len(models_at_pt) else None
            if pt is not None:
                by_src[src_tag].append(pt)
                per_model[src_tag] = pt
        # Certified composite at this grid point — model-only since we have
        # no station obs at arbitrary grid points. Obs contribute through
        # their proximity-weighted IDW on the kneeboard, but here for the
        # shootout map we want each source's pure rendering.
        by_source_dict = dict(per_model)
        cert_pt = _compute_certified(by_source_dict)
        if cert_pt:
            cert_pt["lat"] = lat
            cert_pt["lon"] = lon
            by_src["certified"].append(cert_pt)
    # Add the obs sources (sparse; come from existing caches)
    obs = _collect_observations()
    by_src["metar"] = [p for p in obs if p["source"].startswith("metar:")]
    by_src["mwos"]  = [p for p in obs if p["source"].startswith("mwos:")]
    return jsonify({
        "anchor": list(_DIST_ANCHOR),
        "radius_nm": _LOCAL_MODEL_RADIUS_NM,
        "grid_step_nm": _LOCAL_MODEL_GRID_STEP_NM,
        "sources": by_src,
        "weights": dict(_WX_CERT_WEIGHTS),
        "ts": int(time.time()),
    })


@app.route("/wx-shootout")
def wx_shootout():
    """Multi-source weather visualization map. Each source renders as its
    own color layer (toggleable) over the same Leaflet base. Watch the
    models disagree spatially — GFS streamlines diverge from ECMWF
    streamlines in the gaps where neither has ground truth, and the
    SkyBridge Composite splits the difference.

    Per-source colors are baked in below — distinct + glanceable. Toggle
    each source on/off; multiple can stack so divergence is visible.
    """
    return '''<!doctype html><html><head><meta charset="utf-8"><title>Weather Shootout — DEV</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  body { margin:0; background:#0a0e16; color:#d8e1ec; font-family:system-ui,sans-serif; overflow:hidden; }
  #wrap { position:fixed; inset:0; display:flex; flex-direction:column; }
  header { background:#0d121d; border-bottom:1px solid #2a3140; padding:10px 18px; display:flex; gap:14px; align-items:center; flex-wrap:wrap; }
  h1 { color:#23d18b; font-size:14px; letter-spacing:2px; text-transform:uppercase; margin:0; }
  h1 .dev { background:#ff8800; color:#000; padding:2px 6px; border-radius:3px; font-size:10px; margin-left:6px; }
  h1 a { color:#0090ff; font-size:10px; letter-spacing:1px; margin-left:10px; text-decoration:none; }
  .blurb { color:#9aa5b8; font-size:11px; }
  .src-bar { display:flex; gap:6px; flex-wrap:wrap; margin-left:auto; }
  .src-tog { display:flex; align-items:center; gap:6px; padding:6px 12px; border-radius:6px; cursor:pointer; user-select:none;
             border:1.5px solid; background:transparent; font-size:12px; font-weight:700; transition:all 0.15s; opacity:0.55; }
  .src-tog:hover { opacity:0.9; }
  .src-tog.on { opacity:1.0; }
  .src-tog .swatch { width:10px; height:10px; border-radius:2px; }
  #map { flex:1; background:#0a0e16; }
  .leaflet-container { background:#0a0e16; }
  /* Each source canvas overlays the same map */
  canvas.windCanvas { position:absolute; top:0; left:0; pointer-events:none; }
  /* Station pins */
  .pin-metar, .pin-mwos {
    width:12px; height:12px; border-radius:50%; border:2px solid;
    box-shadow:0 0 4px currentColor;
  }
  .pin-metar { background:#fff; border-color:#fff; color:#fff; }
  .pin-mwos  { background:#ffaa00; border-color:#ffaa00; color:#ffaa00; }
  .pin-label { position:absolute; top:14px; left:50%; transform:translateX(-50%); font-size:9px; color:currentColor; white-space:nowrap; text-shadow:0 0 2px #000; font-weight:700; pointer-events:none; }
  /* Status strip */
  .stat-strip { position:fixed; bottom:10px; left:10px; background:rgba(13,18,29,0.92); border:1px solid #2a3140; border-radius:8px; padding:10px 14px; font-size:11px; line-height:1.6; max-width:380px; }
  .stat-strip h3 { color:#cc88ff; font-size:10px; text-transform:uppercase; letter-spacing:1.5px; margin:0 0 4px; }
  .stat-strip .row { display:flex; justify-content:space-between; gap:10px; }
  .stat-strip .src-name { color:#d8e1ec; font-weight:700; }
  .stat-strip .src-cnt { color:#9aa5b8; }
</style>
</head><body>
<div id="wrap">
  <header>
    <h1>AK Weather Shootout <span class="dev">DEV</span>
        <a href="/wx-validate">→ wx-validate</a>
        <a href="/icons-preview">→ icons-preview</a>
        <a href="/wx-icons-preview">→ wx-icons-preview</a></h1>
    <span class="blurb">Each source = its own color layer. Toggle to compare. Click multiple to see disagreement spatially.</span>
    <div class="src-bar" id="srcBar"></div>
  </header>
  <div id="map"></div>
</div>
<div class="stat-strip" id="statStrip">
  <h3>Loading sources...</h3>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
// ── Per-source config ──────────────────────────────────────────────────────
// Each source has its own color, friendly label, default-on state, kind.
const SOURCES = [
  { tag:'metar',    label:'METAR · obs',     color:'#ffffff', on:true,  kind:'station' },
  { tag:'mwos',     label:'MWOS · obs',      color:'#ff9933', on:true,  kind:'station' },
  { tag:'om_gfs',   label:'NOAA GFS',        color:'#3399ff', on:false, kind:'grid' },
  { tag:'om_gem',   label:'GEM (Canada)',    color:'#cc4444', on:false, kind:'grid' },
  { tag:'om_ecmwf', label:'ECMWF (Europe)',  color:'#33cc66', on:false, kind:'grid' },
  { tag:'om_jma',   label:'JMA (Japan)',     color:'#ff66cc', on:false, kind:'grid' },
  { tag:'certified',label:'★ SkyBridge Composite', color:'#cc88ff', on:false, kind:'grid' },
];

// ── Map ─────────────────────────────────────────────────────────────────────
const map = L.map('map', { zoomControl:true, attributionControl:false }).setView([61.186, -150.039], 7);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
  maxZoom:14, opacity:0.55,
}).addTo(map);

// ── Per-source render state ────────────────────────────────────────────────
const SRC_DATA = {};                // tag → {points: [...]}
const SRC_RENDERER = {};             // tag → renderer object
let DATA_LOADED_AT = 0;

// ── Wind streamline canvas (per source) + grid-point pin layer ────────────
// `radius_nm` controls how far a point's wind influences nearby particles.
// Models (full grid): 350nm — generous, fills the whole viewport.
// Stations (sparse obs): 1nm — tight bubble around each station, since a
// surface obs only legitimately speaks for its immediate vicinity.
// `bubble_mode` (true for stations): particles spawn near a randomly-chosen
// data point, stay within radius, recycle when they leave. Result: small
// "wind sock" effect at each station.
function makeWindRenderer(src) {
  const pane = map.createPane('wp_'+src.tag);
  pane.style.zIndex = 410;
  pane.style.pointerEvents = 'none';
  const canvas = document.createElement('canvas');
  canvas.className = 'windCanvas';
  pane.appendChild(canvas);
  const ctx = canvas.getContext('2d');

  // Grid-point pins: one circle marker per data point in the source's color.
  // Subtle (small radius, semi-transparent) so they reveal the sampling
  // grid without overpowering the streamlines.
  const pinLayer = L.layerGroup();
  let particles = [];
  const isBubble    = src.kind === 'station';
  const RADIUS_NM   = isBubble ? 3.0 : 350;
  const PARTICLE_COUNT = isBubble ? 60 : 90;
  const TRAIL_LEN = 5;
  const LIFESPAN = 90;

  const fit = () => {
    const m = document.getElementById('map');
    canvas.width = m.clientWidth || 1;
    canvas.height = m.clientHeight || 1;
  };
  fit();
  map.on('resize', fit);

  function spawn() {
    const data = SRC_DATA[src.tag] || [];
    if (isBubble) {
      // Pick a random reporting station (must have wind data) and spawn within
      // RADIUS_NM of it. Returns null if no usable stations — caller handles.
      const usable = data.filter(p =>
        p.dir_deg != null && p.dir_deg >= 0 &&
        p.speed_kt != null && p.speed_kt > 0);
      if (usable.length === 0) return null;
      const seed = usable[Math.floor(Math.random() * usable.length)];
      const r = Math.sqrt(Math.random()) * RADIUS_NM;     // uniform-area
      const theta = Math.random() * 2 * Math.PI;
      const dLat = r * Math.cos(theta) / 60.0;
      const dLon = r * Math.sin(theta) / (60.0 * Math.cos(seed.lat * Math.PI/180));
      return { lat: seed.lat + dLat, lon: seed.lon + dLon,
               age: Math.floor(Math.random() * LIFESPAN),
               seed_lat: seed.lat, seed_lon: seed.lon, trail: [] };
    } else {
      const b = map.getBounds();
      return {
        lat: b.getSouth() + Math.random() * (b.getNorth() - b.getSouth()),
        lon: b.getWest() + Math.random() * (b.getEast() - b.getWest()),
        age: Math.floor(Math.random() * LIFESPAN),
        trail: [],
      };
    }
  }

  function windAt(lat, lon) {
    const data = SRC_DATA[src.tag] || [];
    if (data.length === 0) return null;
    let u=0, v=0, w=0;
    for (const p of data) {
      if (p.dir_deg == null || p.speed_kt == null || p.dir_deg < 0 || p.speed_kt <= 0) continue;
      const d = gcDistNm([lat, lon], [p.lat, p.lon]);
      if (d > RADIUS_NM) continue;
      const weight = 1 / (d*d + 0.5);
      const rad = p.dir_deg * Math.PI/180;
      u += weight * (-p.speed_kt * Math.sin(rad));
      v += weight * (-p.speed_kt * Math.cos(rad));
      w += weight;
    }
    if (w === 0) return null;
    u /= w; v /= w;
    const speed = Math.sqrt(u*u + v*v);
    let dir = Math.atan2(-u, -v) * 180/Math.PI;
    if (dir < 0) dir += 360;
    return { dir_deg: dir, speed_kt: speed };
  }

  let alive = false;
  let raf = null;

  function frame() {
    if (!alive) return;
    try {
      const tl = map.containerPointToLayerPoint([0, 0]);
      L.DomUtil.setPosition(canvas, tl);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      const z = map.getZoom();
      const zoomScale = Math.pow(1.45, Math.max(0, z - 9));
      const activeCount = Math.max(20, Math.floor(particles.length * Math.pow(0.78, Math.max(0, z - 9))));
      for (let i = 0; i < activeCount; i++) {
        let p = particles[i];
        if (!p || p.age >= LIFESPAN) {
          p = particles[i] = spawn();
          if (!p) continue;
          p.age = 0;
        }
        const wd = windAt(p.lat, p.lon);
        if (!wd) { p.age = LIFESPAN; continue; }
        let stepMag = (wd.speed_kt * 7.7e-6 * 80) / zoomScale;
        const rad = wd.dir_deg * Math.PI/180;
        let dLat = -stepMag * Math.cos(rad);
        let dLon = -stepMag * Math.sin(rad) / Math.cos(p.lat * Math.PI/180);
        const here = map.latLngToContainerPoint({lat: p.lat, lng: p.lon});
        const next = map.latLngToContainerPoint({lat: p.lat + dLat, lng: p.lon + dLon});
        const px = Math.hypot(next.x - here.x, next.y - here.y);
        if (px > 4 && px > 0) { const k = 4/px; dLat *= k; dLon *= k; }
        p.lat += dLat; p.lon += dLon; p.age++;
        p.trail.push([p.lat, p.lon]);
        if (p.trail.length > TRAIL_LEN) p.trail.shift();
        if (p.trail.length >= 2) {
          const pts = [];
          for (let j = 0; j < p.trail.length; j++) {
            try { pts.push(map.latLngToContainerPoint({lat: p.trail[j][0], lng: p.trail[j][1]})); } catch(e){}
          }
          if (pts.length < 2) continue;
          for (let j = 1; j < pts.length; j++) {
            const t = j / (pts.length - 1);
            const a = 0.20 + 0.70 * t;
            ctx.strokeStyle = src.color + Math.round(a*255).toString(16).padStart(2,'0');
            ctx.lineWidth = 0.9 + 0.7 * t;
            ctx.beginPath();
            ctx.moveTo(pts[j-1].x, pts[j-1].y);
            ctx.lineTo(pts[j].x, pts[j].y);
            ctx.stroke();
          }
        }
      }
    } catch(err) { console.warn('[shootout '+src.tag+'] frame err:', err); }
    if (alive) raf = requestAnimationFrame(frame);
  }

  function rebuildPins() {
    pinLayer.clearLayers();
    // Station sources already get styled pins from makeStationRenderer; don't
    // duplicate. Only render grid-point dots for model/composite sources.
    if (isBubble) return;
    const data = SRC_DATA[src.tag] || [];
    for (const p of data) {
      if (p.lat == null || p.lon == null) continue;
      const dirStr = (p.dir_deg == null) ? '?' :
                     (p.dir_deg < 0 ? 'VRB' : Math.round(p.dir_deg) + '°');
      const spdStr = (p.speed_kt != null) ? p.speed_kt.toFixed(1) + 'kt' : '—';
      const popup = `<b style="color:${src.color}">${src.label}</b><br>
        ${p.lat.toFixed(3)}, ${p.lon.toFixed(3)}<br>
        wind: ${dirStr} / ${spdStr}<br>
        ${p.temp_c!=null?'temp: '+p.temp_c+' °C<br>':''}
        ${p.pressure_mb!=null?'press: '+p.pressure_mb+' mb<br>':''}
        ${p.cloud_pct!=null?'cloud: '+p.cloud_pct+'%<br>':''}
        ${p.visibility_sm!=null?'vis: '+p.visibility_sm+' sm':''}`;
      L.circleMarker([p.lat, p.lon], {
        radius: 3,
        color: src.color,
        weight: 1,
        fillColor: src.color,
        fillOpacity: 0.55,
        opacity: 0.85,
      }).bindPopup(popup).addTo(pinLayer);
    }
  }

  return {
    start() {
      if (alive) return;
      pane.style.display = '';
      rebuildPins();
      pinLayer.addTo(map);
      particles = [];
      for (let i = 0; i < PARTICLE_COUNT; i++) particles.push(spawn());
      alive = true;
      frame();
    },
    stop() {
      alive = false;
      if (raf) cancelAnimationFrame(raf);
      pane.style.display = 'none';
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      map.removeLayer(pinLayer);
    },
    refresh() { if (alive) rebuildPins(); },
    visible() { return alive; },
    color: src.color,
  };
}

// ── Station pin layer (METAR/MWOS) ─────────────────────────────────────────
function makeStationRenderer(src) {
  const layer = L.layerGroup();
  return {
    start() {
      layer.clearLayers();
      const pts = SRC_DATA[src.tag] || [];
      for (const p of pts) {
        if (p.lat == null || p.lon == null) continue;
        const html = `<div class="pin-${src.tag}">
          <div class="pin-label">${(p.source||'').split(':').pop().slice(0,12)}</div>
        </div>`;
        L.marker([p.lat, p.lon], {
          icon: L.divIcon({ html, className:'', iconSize:[12,12], iconAnchor:[6,6] }),
        }).bindPopup(`<b>${p.source}</b><br>
          ${p.dir_deg!=null?'wind: '+(p.dir_deg<0?'VRB':p.dir_deg.toFixed(0)+'°')+' / '+p.speed_kt+'kt':''}<br>
          ${p.temp_c!=null?'temp: '+p.temp_c+' °C':''}<br>
          ${p.pressure_mb!=null?'press: '+p.pressure_mb+' mb':''}<br>
          ${p.visibility_sm!=null?'vis: '+p.visibility_sm+' sm':''}`).addTo(layer);
      }
      layer.addTo(map);
    },
    stop() { map.removeLayer(layer); },
    visible() { return map.hasLayer(layer); },
    color: src.color,
  };
}

// ── Bar build + toggle ─────────────────────────────────────────────────────
const bar = document.getElementById('srcBar');
SOURCES.forEach(src => {
  const btn = document.createElement('button');
  btn.className = 'src-tog' + (src.on ? ' on' : '');
  btn.style.borderColor = src.color;
  btn.style.color = src.color;
  btn.innerHTML = `<span class="swatch" style="background:${src.color}"></span><span>${src.label}</span>`;
  btn.onclick = () => {
    btn.classList.toggle('on');
    src.on = btn.classList.contains('on');
    if (src.on) SRC_RENDERER[src.tag].start();
    else        SRC_RENDERER[src.tag].stop();
  };
  bar.appendChild(btn);
});

// ── Distance helper (great-circle nm) ──────────────────────────────────────
function gcDistNm(a, b) {
  const R = 3440.065;
  const lat1 = a[0]*Math.PI/180, lat2 = b[0]*Math.PI/180;
  const dLat = (b[0]-a[0])*Math.PI/180;
  const dLon = (b[1]-a[1])*Math.PI/180;
  const x = Math.sin(dLat/2)**2 + Math.cos(lat1)*Math.cos(lat2)*Math.sin(dLon/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1-x));
}

// ── Data loader + scoreboard ───────────────────────────────────────────────
async function loadData() {
  const r = await fetch('/api/wx-lens');
  const d = await r.json();
  DATA_LOADED_AT = Date.now();
  // Build SRC_DATA + counts for the strip
  const counts = {};
  for (const src of SOURCES) {
    SRC_DATA[src.tag] = d.sources[src.tag] || [];
    counts[src.tag] = SRC_DATA[src.tag].length;
  }
  // Build renderer for each source on first load.
  // Station sources get a COMPOUND renderer: both the styled pin layer
  // (white/orange divIcons) and the bubble-mode wind streamlines (3nm radius
  // around each station). Grid sources just get the wind+grid-pin renderer.
  for (const src of SOURCES) {
    const firstBuild = !SRC_RENDERER[src.tag];
    if (firstBuild) {
      if (src.kind === 'station') {
        // Compound: both the pin layer and the bubble-wind layer
        const pinR = makeStationRenderer(src);
        const windR = makeWindRenderer(src);
        SRC_RENDERER[src.tag] = {
          start()   { pinR.start(); windR.start(); },
          stop()    { pinR.stop();  windR.stop(); },
          refresh() { if (pinR.refresh) pinR.refresh(); else { pinR.stop(); pinR.start(); }
                      if (windR.refresh) windR.refresh(); },
          color: src.color,
        };
      } else {
        SRC_RENDERER[src.tag] = makeWindRenderer(src);
      }
    }
    // On refresh: if currently on, rebuild pins from new data; otherwise stop+restart
    if (src.on) {
      if (!firstBuild && SRC_RENDERER[src.tag].refresh) {
        SRC_RENDERER[src.tag].refresh();
      } else {
        SRC_RENDERER[src.tag].stop();
        SRC_RENDERER[src.tag].start();
      }
    } else {
      SRC_RENDERER[src.tag].stop();
    }
  }
  // Stat strip: per-source point counts + weight badge
  const strip = document.getElementById('statStrip');
  let html = '<h3>Sources loaded</h3>';
  for (const src of SOURCES) {
    const w = (d.weights||{})[src.tag];
    const wstr = (w !== undefined) ? ` <span style="color:#ffd24c">×${w.toFixed(2)}</span>` : '';
    html += `<div class="row">
      <span class="src-name" style="color:${src.color}">${src.label}</span>
      <span class="src-cnt">${counts[src.tag]||0} pts${wstr}</span>
    </div>`;
  }
  html += `<div class="row" style="margin-top:6px;color:#7a8497;font-size:10px">
    Refreshes every 5min · Local grid radius: ${d.radius_nm}nm</div>`;
  strip.innerHTML = html;
}
loadData();
setInterval(loadData, 5*60*1000);
</script>
</body></html>'''




# ───────────────────────── PUBLIC AUTH GATE ─────────────────────────────
# Soft password gate for /public/* HTML routes. Single shared password
# rotates via SKYBRIDGE_PUBLIC_PASSWORD env var (set in systemd unit).
# On successful login a hashed cookie is set; routes check the cookie via
# before_request. /api/* endpoints are NOT gated — they're the public data
# layer and remain accessible (mesh forwarder, third-party integrations).
import hashlib as _hashlib

_PUBLIC_PASSWORD   = os.environ.get("SKYBRIDGE_PUBLIC_PASSWORD", "skybridge-2026")
_PUBLIC_COOKIE     = "sb_pub"
_PUBLIC_COOKIE_TTL = 7 * 86400

def _public_token():
    """Hash + salt the password so the cookie value can't be reversed.
    Rotate by changing the env var + restart."""
    return _hashlib.sha256(("skybridge-public::" + _PUBLIC_PASSWORD).encode()).hexdigest()[:32]

def _public_authed(req):
    return req.cookies.get(_PUBLIC_COOKIE) == _public_token()

_PUBLIC_GATED_PATHS = {
    "/",
    "/kneeboard",
    "/public",
    "/public/",
    "/public/icons-preview",
    "/public/wx-icons-preview",
    "/public/wx-validate",
    "/public/wx-shootout",
}

@app.before_request
def _public_auth_before():
    p = request.path
    if p in _PUBLIC_GATED_PATHS and not _public_authed(request):
        return redirect("/public/login?next=" + p)


@app.route("/public/login", methods=["GET", "POST"])
def public_login():
    error = ""
    next_url = request.args.get("next", "/public")
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == _PUBLIC_PASSWORD:
            resp = make_response(redirect(next_url))
            resp.set_cookie(_PUBLIC_COOKIE, _public_token(),
                            max_age=_PUBLIC_COOKIE_TTL, httponly=True,
                            samesite="Lax")
            return resp
        error = "Wrong password"
    err_html = ('<div class="err">' + error + '</div>') if error else ''
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>SkyBridge Alaska — Sign In</title>'
            + _PUBLIC_NAV_CSS +
            '<style>'
            'body { background:#0a0e16; color:#d8e1ec; font-family:system-ui,sans-serif; '
            '       margin:0; padding:0; min-height:100vh; display:flex; flex-direction:column; '
            '       align-items:center; justify-content:center; }'
            '.box { background:#141a26; border:1px solid #2a3140; border-radius:12px; '
            '       padding:40px 48px; max-width:380px; width:100%; text-align:center; '
            '       box-shadow:0 8px 40px rgba(0,0,0,0.5); margin:20px; box-sizing:border-box; }'
            '.box h1 { color:#23d18b; font-size:18px; letter-spacing:2px; margin:0 0 8px; '
            '          text-transform:uppercase; }'
            '.box .star { color:#ffaa00; font-size:24px; margin-bottom:8px; }'
            '.box .tag { background:#ffaa00; color:#000; font-size:9px; padding:2px 6px; '
            '            border-radius:3px; font-weight:800; letter-spacing:1px; '
            '            display:inline-block; margin:0 0 18px; }'
            '.box p { color:#9aa5b8; font-size:12px; line-height:1.6; margin:0 0 24px; }'
            '.box input[type=password] { width:100%; padding:10px 14px; background:#0a0e16; '
            '       color:#d8e1ec; border:1px solid #2a3140; border-radius:6px; '
            '       font-family:inherit; font-size:14px; box-sizing:border-box; '
            '       margin-bottom:14px; color-scheme:dark; }'
            '.box input[type=password]:focus { outline:2px solid #0090ff; border-color:#0090ff; }'
            '.box button { width:100%; padding:10px 14px; background:#0090ff; color:#0a0e16; '
            '       border:none; border-radius:6px; font-family:inherit; font-size:13px; '
            '       font-weight:700; letter-spacing:1px; text-transform:uppercase; cursor:pointer; }'
            '.box button:hover { background:#23d18b; }'
            '.box .err { color:#ff5040; font-size:11px; margin-top:14px; }'
            '.box .footer-note { color:#445; font-size:10px; margin-top:24px; }'
            '</style></head><body>'
            '<div class="box">'
              '<div class="star">★</div>'
              '<h1>SkyBridge Alaska</h1>'
              '<span class="tag">BETA</span>'
              '<p>Sign in to continue. Public access password rotates periodically — request from your administrator.</p>'
              '<form method="post" action="/public/login?next=' + next_url + '">'
                '<input type="password" name="password" placeholder="Password" autofocus required>'
                '<button type="submit">Sign In</button>'
                + err_html +
              '</form>'
              '<div class="footer-note">Open source · github.com/SFETTAK/Skybridge-Alaska</div>'
            '</div></body></html>')


@app.route("/public/logout")
def public_logout():
    resp = make_response(redirect("/public/login"))
    resp.set_cookie(_PUBLIC_COOKIE, "", max_age=0)
    return resp

# ─────────────────────────────────────────────────────────────────────────


# ───────────────────── PUBLIC-FACING DUPLICATES ─────────────────────
# Frozen-fork copies of cert-lab routes at /public/<name>.
# Auto-generated — see /tmp/fork_public_v2.py
# ────────────────────────────────────────────────────────────────────

@app.route("/public/icons-preview")
def icons_preview_public():
    """Mirror of the live ADS-B icon renderer in kneeboard_dev.py. Updated to
    match every visual behavior on the dev kneeboard at :8084 — fill drives
    altitude band, outline drives operator class, brightness/drop-shadow filter
    matches live, emergency squawks paint red with a pulsing ring, stale-fade
    + age badge mirror the server's grace window. Use this page as a feedback
    surface; tune values in the JS block and refresh."""

    # ── Source-of-truth values mirrored from the live JS constants ──────────
    SVGS = {
      "ga_single":  '<path d="M12 3 L12.6 8 L20 11 L20 12 L12.6 11.5 L12.6 17 L14.5 19 L14.5 20 L9.5 20 L9.5 19 L11.4 17 L11.4 11.5 L4 12 L4 11 L11.4 8 Z"/>',
      "ga_twin":    '<path d="M12 3 L13 8 L20 12 L20 13 L13 12 L13 18 L15 20 L9 20 L11 18 L11 12 L4 13 L4 12 L11 8 Z"/><circle cx="6" cy="12" r="1"/><circle cx="18" cy="12" r="1"/>',
      "turboprop":  '<path d="M12 2 L13 7 L21 11 L21 13 L13 12 L13 19 L16 21 L16 22 L8 22 L8 21 L11 19 L11 12 L3 13 L3 11 L11 7 Z"/><line x1="11" y1="1" x2="13" y2="1" stroke="currentColor" stroke-width="1"/>',
      "jet":        '<path d="M12 2 L13 7 L22 14 L22 15 L13 13 L13 19 L17 22 L17 22.5 L7 22.5 L7 22 L11 19 L11 13 L2 15 L2 14 L11 7 Z"/>',
      "widebody":   '<path d="M12 1 L13 6 L23 14 L23 15 L13 13 L13 20 L18 22.5 L18 23 L6 23 L6 22.5 L11 20 L11 13 L1 15 L1 14 L11 6 Z"/><circle cx="6.5" cy="12" r="0.7"/><circle cx="9" cy="11" r="0.7"/><circle cx="15" cy="11" r="0.7"/><circle cx="17.5" cy="12" r="0.7"/>',
      "helicopter": '<circle cx="12" cy="12" r="11" fill="none" stroke="currentColor" stroke-width="0.5" opacity="0.5"/><path d="M10 6 L14 6 L14 17 L15 18 L9 18 L10 17 Z M11 18 L13 18 L13 22 L11 22 Z"/><rect x="2" y="11.5" width="20" height="1" opacity="0.7"/>',
      "military":   '<path d="M12 1 L13 7 L21 19 L18 19 L13 14 L13.5 21 L16 22.5 L8 22.5 L10.5 21 L11 14 L6 19 L3 19 L11 7 Z"/>',
    }

    # Mirrors ICON_SIZE_BY_WAKE + per-category mapping; at-rendered px = wake × ICON_SCALE
    LABELS = [
      ("ga_single",  "GA single",         "C172, PA28, SR22, DA40",                    "L",   "20 px"),
      ("ga_twin",    "GA twin / piston",  "BE58, PA34, P32R",                          "L",   "20 px"),
      ("turboprop",  "Turboprop",         "PC12, C208, BE20, AT72, DH8x",              "L–M", "20–28 px"),
      ("jet",        "Narrowbody jet",    "B737, A320, A220, B752",                    "M",   "28 px"),
      ("widebody",   "Widebody jet",      "B77x, B78x, A33x, A35x, B744",              "H–J", "34–40 px"),
      ("helicopter", "Helicopter",        "EC35, AS50, R44, B06, S92, MD52",           "L",   "20 px"),
      ("military",   "Military / fighter","RCH/PAT callsigns, F-16/F-22, C17, KC135",  "var", "28 px"),
    ]

    # ALT_BANDS in JS → mirrored exactly here. INVERTED scale: low = RED.
    ALTS = [
      ("Ground/Taxi",      "#cccccc"),
      ("0–1.5k (pattern)", "#ff2244"),
      ("1.5–3k (low VFR)", "#ff7722"),
      ("3–6k (mid VFR)",   "#ffbb00"),
      ("6–10k (high VFR)", "#ffee22"),
      ("10–18k (IFR)",     "#88dd22"),
      ("18k+ (jet)",       "#33cc88"),
      ("Unknown alt",      "#888888"),
    ]
    # OUTLINE_BY_CLASS in JS → mirrored exactly here.
    CLASSES = [
      ("GA / Private", "#ffffff"),
      ("Commercial",   "#3399ff"),
      ("Cargo",        "#cc44ff"),
      ("Military",     "#9aaa3a"),
      ("Medivac",      "#ff66cc"),
      ("Coast Guard",  "#5599ff"),
      ("Unknown",      "#000000"),
    ]
    # Live defaults that the slider tunes — show actual values
    ICON_SCALE       = 1.10
    ICON_BRIGHTNESS  = 1.15
    OUTLINE_W        = 0.9     # ICON_OUTLINE_WIDTH

    EMERGENCY_FILL = "#ff0033"  # squawks 7500/7600/7700 override the alt-band fill

    def icon_svg(svg_inner, fill, outline, size=28, rot=0,
                 brightness=ICON_BRIGHTNESS, opacity=1.0,
                 emergency=False, glow_color=None):
        """Match aircraftDivIcon() in the JS exactly: brightness filter,
        drop-shadow keyed to fill, stroke-linejoin=round, optional pulsing ring
        on emergency, optional opacity for stale-fade demonstration."""
        glow = glow_color or fill
        rotstyle = f"transform:rotate({rot}deg);" if rot else ""
        # Live applies brightness + 3px drop-shadow to fresh; brightness +
        # saturate(0.6) + 2px drop-shadow #000 when stale.
        if opacity < 1.0:
            filt = f"brightness({brightness}) saturate(0.6) drop-shadow(0 0 2px #000)"
        else:
            filt = f"brightness({brightness}) drop-shadow(0 0 3px {glow})"
        em_ring = ("<circle cx='12' cy='12' r='11' fill='none' stroke='" + EMERGENCY_FILL + "' "
                   "stroke-width='1.5' opacity='0.9'>"
                   "<animate attributeName='opacity' values='0.9;0.2;0.9' dur='1.2s' repeatCount='indefinite'/>"
                   "</circle>") if emergency else ""
        return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" '
                f'fill="{fill}" stroke="{outline}" stroke-width="{OUTLINE_W}" '
                f'stroke-linejoin="round" '
                f'style="{rotstyle}opacity:{opacity:.2f};filter:{filt}">'
                f'{svg_inner}{em_ring}</svg>')

    def cell(svg, fill, outline, size=28, rot=0, label=None,
             opacity=1.0, emergency=False, badge=None):
        """Cell wrapping icon_svg with optional age badge + caption."""
        ic = icon_svg(svg, fill, outline, size, rot, opacity=opacity, emergency=emergency)
        badge_html = f'<div class="badge">{badge}</div>' if badge else ''
        # Label is rendered on the live map below the icon in fill color,
        # mirrored here as a small caption.
        cap = ''
        if label:
            cap = f'<div class="cap" style="color:{fill}">{label}</div>'
        return f'<span class="ic" style="position:relative">{badge_html}{ic}{cap}</span>'

    # ── Per-silhouette rows: sizes / alt fills / class outlines / rotations ─
    rows = ""
    for key, name, examples, wake, size in LABELS:
        s = SVGS[key]
        sizes_demo = "".join(cell(s, "#ffbb00", "#ffffff", sz) for sz in (20, 28, 40))
        alt_demo   = "".join(cell(s, c, "#ffffff", 28) for _, c in ALTS)
        cls_demo   = "".join(cell(s, "#ffbb00", c, 28) for _, c in CLASSES)
        rot_demo   = "".join(cell(s, "#ffbb00", "#3399ff", 28, r) for r in (0, 45, 135, 225))
        rows += f'''
        <tr>
          <td class="cat">{name}</td>
          <td class="ex">{examples}</td>
          <td class="wk">{wake}</td>
          <td class="sz">{size}</td>
          <td class="icons">
            <div class="grp">{sizes_demo}</div>
            <div class="grp">{alt_demo}</div>
            <div class="grp">{cls_demo}</div>
            <div class="grp">{rot_demo}</div>
          </td>
        </tr>'''

    # ── Special-state rows: emergency squawk + stale-fade + ground/unknown ──
    em_demo = "".join([
        cell(SVGS["jet"], EMERGENCY_FILL, "#ffffff", 32, emergency=True, label="N123EM"),
        cell(SVGS["ga_single"], EMERGENCY_FILL, "#ffffff", 24, emergency=True, label="N456EM"),
        cell(SVGS["helicopter"], EMERGENCY_FILL, "#ff66cc", 28, emergency=True, label="LIFE7"),
    ])
    # Stale-fade: opacity steps + age badges mirroring the live formula
    # opacity = 1 - 0.75 * (stale_sec / 60). Badge appears at >=5s.
    stale_demo = "".join([
        cell(SVGS["jet"], "#ffbb00", "#3399ff", 30, label="t=0",  opacity=1.00),
        cell(SVGS["jet"], "#ffbb00", "#3399ff", 30, label="t=10", opacity=0.875, badge="10s"),
        cell(SVGS["jet"], "#ffbb00", "#3399ff", 30, label="t=30", opacity=0.625, badge="30s"),
        cell(SVGS["jet"], "#ffbb00", "#3399ff", 30, label="t=50", opacity=0.375, badge="50s"),
        cell(SVGS["jet"], "#ffbb00", "#3399ff", 30, label="t=60", opacity=0.250, badge="60s"),
    ])
    ground_demo = "".join([
        cell(SVGS["ga_single"], "#cccccc", "#ffffff",  24, label="GROUND"),
        cell(SVGS["jet"],       "#cccccc", "#3399ff", 30, label="TAXI"),
        cell(SVGS["ga_single"], "#888888", "#000000", 24, label="ALT?"),
    ])

    # ── Hybrid matrix: jet × every (alt × class) combo ──────────────────────
    matrix_head = "<th></th>" + "".join(f'<th class="mhdr" style="color:{c}">{n}</th>' for n, c in CLASSES)
    matrix_rows = ""
    for alt_name, alt_color in ALTS:
        cells = "".join(f'<td>{cell(SVGS["jet"], alt_color, cls_color, 30)}</td>'
                        for _, cls_color in CLASSES)
        matrix_rows += f'<tr><th class="mhdr-row" style="color:{alt_color}">{alt_name}</th>{cells}</tr>'

    return _publicize(f'''<!doctype html><html><head><meta charset="utf-8"><title>ADS-B Icon Preview — SkyBridge Alaska</title>
    <style>
    body {{ background:#0a0e16; color:#d8e1ec; font-family:system-ui,sans-serif; padding:24px; margin:0; }}
    h1 {{ color:#23d18b; font-size:18px; letter-spacing:2px; text-transform:uppercase; margin:0 0 8px; }}
    h1 .dev {{ background:#ff8800; color:#000; padding:2px 8px; border-radius:4px; font-size:12px; margin-left:8px; vertical-align:middle; }}
    h1 a {{ color:#0090ff; font-size:11px; letter-spacing:1px; margin-left:14px; text-decoration:none; }}
    p.lede {{ color:#9aa5b8; font-size:13px; max-width:980px; line-height:1.5; }}
    h2 {{ color:#0090ff; font-size:13px; text-transform:uppercase; letter-spacing:1.5px; margin:28px 0 8px; }}
    h2 small {{ color:#7a8497; font-weight:400; text-transform:none; letter-spacing:0; margin-left:8px; font-size:11px; }}
    table {{ border-collapse:collapse; margin-top:14px; background:#141a26; border-radius:8px; overflow:hidden; }}
    th, td {{ padding:10px 14px; text-align:left; border-bottom:1px solid #2a3140; vertical-align:middle; font-size:12px; }}
    th {{ background:#1f2738; color:#9aa5b8; text-transform:uppercase; letter-spacing:1px; font-size:10px; }}
    td.cat {{ color:#23d18b; font-weight:700; }}
    td.ex  {{ color:#9aa5b8; font-size:11px; max-width:220px; }}
    td.wk  {{ color:#ffbb00; font-weight:700; text-align:center; }}
    td.sz  {{ color:#9aa5b8; font-size:11px; text-align:center; }}
    td.icons {{ white-space:nowrap; }}
    .grp {{ display:inline-block; padding:6px 10px; margin:0 6px 0 0; border-right:1px dashed #2a3140; }}
    .grp:last-child {{ border-right:none; }}
    .ic {{ display:inline-flex; flex-direction:column; align-items:center; justify-content:center; min-width:54px; min-height:64px; margin:0 4px; vertical-align:top; }}
    .ic .cap {{ font-size:9px; font-weight:700; margin-top:2px; text-shadow:0 0 3px #000; white-space:nowrap; }}
    .badge {{ position:absolute; top:-4px; right:-4px; background:#1a2230; border:1px solid #888; color:#bbb; font-size:8px; font-weight:700; padding:0 3px; border-radius:3px; line-height:1.4; z-index:2; }}
    .legend {{ display:flex; gap:14px; flex-wrap:wrap; font-size:11px; color:#9aa5b8; align-items:center; }}
    .legend span {{ display:flex; align-items:center; gap:5px; }}
    .legend .swatch {{ width:14px; height:14px; border-radius:3px; }}
    .legend .ring {{ width:14px; height:14px; border-radius:50%; border:2px solid; background:transparent; }}
    .matrix {{ border-collapse:collapse; margin-top:14px; background:#141a26; border-radius:8px; }}
    .matrix th.mhdr {{ font-size:9px; padding:6px; text-transform:none; letter-spacing:0; }}
    .matrix th.mhdr-row {{ font-size:10px; padding:6px 12px; text-transform:none; letter-spacing:0; text-align:right; white-space:nowrap; }}
    .matrix td {{ padding:4px; text-align:center; }}
    .panel {{ background:#141a26; border-radius:8px; padding:14px 18px; margin-top:14px; }}
    .panel .row {{ display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start; }}
    code {{ background:#1f2738; padding:1px 5px; border-radius:3px; color:#23d18b; font-size:11px; }}
    .open-q {{ background:#1f2738; padding:14px 18px; border-radius:8px; margin-top:24px; font-size:12px; line-height:1.6; }}
    .kv {{ display:grid; grid-template-columns:auto 1fr; gap:4px 18px; font-size:12px; max-width:560px; }}
    .kv code {{ font-size:11px; }}
    .kv .v {{ color:#ffd24c; font-weight:700; }}
    </style><!--SBNAVCSS--></head><body><!--SBNAV-->

    <h1>ADS-B Icon Preview <span class="beta">BETA</span>
        <a href="/wx-icons-preview">→ wx-icons-preview</a></h1>

    <p class="lede">Pixel-faithful mirror of <code>aircraftDivIcon()</code> on the live dev kneeboard at <code>:8084</code>.
    <strong>Fill = altitude band</strong>, <strong>outline = operator class</strong>, <strong>shape = silhouette category</strong>,
    <strong>size = wake × <code>ICON_SCALE</code></strong>. Brightness filter, drop-shadow color, emergency pulse, stale-fade,
    and age badges all reproduced exactly.</p>

    <div class="panel">
      <h2 style="margin-top:0">Live tuning state <small>(values you'd see at this instant on :8084)</small></h2>
      <div class="kv">
        <code>ICON_SCALE</code>          <span class="v">{ICON_SCALE}</span>
        <code>ICON_BRIGHTNESS</code>     <span class="v">{ICON_BRIGHTNESS}</span>
        <code>ICON_OUTLINE_WIDTH</code>  <span class="v">{OUTLINE_W}</span>
        <code>COLOR_MODE</code>          <span class="v">altitude-fill</span>
        <code>EMERGENCY_SQUAWKS</code>   <span class="v">7500 · 7600 · 7700 → fill becomes red, ring pulses</span>
        <code>AIRCRAFT_GRACE_SEC</code>  <span class="v">60 (opacity fades 1.00 → 0.25)</span>
      </div>
    </div>

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
      <tr><th style="width:120px">Category</th><th style="width:220px">Examples</th><th>Wake</th><th>Size</th><th>Sizes (3) · Alt fills (8) · Class outlines (7) · Rotations (4)</th></tr>
      {rows}
    </table>

    <h2>Emergency squawks <small>fill → red + animated 1.2s pulsing ring</small></h2>
    <div class="panel"><div class="row">{em_demo}</div></div>

    <h2>Stale-fade + age badge <small>opacity = 1 − 0.75 × (stale_sec / 60); badge appears at ≥5s; saturate(0.6) kicks in once stale</small></h2>
    <div class="panel"><div class="row">{stale_demo}</div></div>

    <h2>Ground / Taxi / Unknown altitude <small>special grey fills outside the standard altitude bands</small></h2>
    <div class="panel"><div class="row">{ground_demo}</div></div>

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
        <li><code>EMERGENCY_SQUAWKS</code> — squawks that override fill to red + add pulsing ring</li>
        <li><code>AIRCRAFT_GRACE_SEC_CLIENT</code> — fade window length (default 60s)</li>
      </ul>
      <p style="margin:14px 0 0">Companion preview at <a href="/wx-icons-preview" style="color:#0090ff">/wx-icons-preview</a> covers the weather-HUD instrument family (ceiling, freezing-level, wind compass, plane-in-fog, stratus stack, pressure VSI, animated anemometer).</p>
    </div>
    <!--SBFOOTER--></body></html>''', "/public/icons-preview")




@app.route("/public/wx-icons-preview")
def wx_icons_preview_public():
    """Live preview of weather-HUD instrument icons we're brainstorming for the
    glanceable bottom strip + per-station overlays. Each row shows the same
    icon under multiple conditions so we can see how it 'reads' at a glance
    without a legend. Iterate freely — this is a sandbox.
    """
    # Each scene is a dict: name → list of (label, params) tuples.
    # Params drive the SVG generation per instrument family.

    # ── Mini attitude indicator → ceiling ─────────────────────────────
    # Renders a circular AI face. Horizon-line vertical position = ceiling AGL.
    # Sky color = cloud overcast tint. Ground stays the same.
    def ai_ceiling(ceil_ft, cov_pct, label):
        # ceil_ft maps 5000 → top (y=10), 0 → bottom (y=70)
        y = max(10, min(70, 70 - (ceil_ft / 5000.0) * 60))
        # Sky tint: blue at 0% cov → gray at 100%
        sky_r = int(38 + (170 - 38) * cov_pct / 100)
        sky_g = int(95 + (175 - 95) * cov_pct / 100)
        sky_b = int(150 + (180 - 150) * cov_pct / 100)
        sky = f"rgb({sky_r},{sky_g},{sky_b})"
        ground = "#3a2820"
        # Highlight if ceiling drops into hazardous range
        warn = "#ff5040" if ceil_ft < 1000 else ("#ffaa00" if ceil_ft < 3000 else "#23d18b")
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <defs>
              <clipPath id="aic_{label}"><circle cx="40" cy="40" r="34"/></clipPath>
            </defs>
            <g clip-path="url(#aic_{label})">
              <rect x="0" y="0" width="80" height="{y}" fill="{sky}"/>
              <rect x="0" y="{y}" width="80" height="80" fill="{ground}"/>
              <line x1="0" y1="{y}" x2="80" y2="{y}" stroke="#fff" stroke-width="1.5"/>
              <!-- altitude ticks at 1000, 3000, 5000 ft -->
              <line x1="68" y1="58" x2="76" y2="58" stroke="#fff" stroke-width="0.5" opacity="0.6"/>
              <line x1="68" y1="34" x2="76" y2="34" stroke="#fff" stroke-width="0.5" opacity="0.6"/>
              <line x1="68" y1="10" x2="76" y2="10" stroke="#fff" stroke-width="0.5" opacity="0.6"/>
              <text x="65" y="59" fill="#fff" font-size="6" text-anchor="end" opacity="0.7">1k</text>
              <text x="65" y="35" fill="#fff" font-size="6" text-anchor="end" opacity="0.7">3k</text>
              <text x="65" y="11" fill="#fff" font-size="6" text-anchor="end" opacity="0.7">5k</text>
              <!-- center reference (the airplane) -->
              <line x1="32" y1="40" x2="48" y2="40" stroke="#ffd24c" stroke-width="2"/>
              <circle cx="40" cy="40" r="2" fill="#ffd24c"/>
            </g>
            <circle cx="40" cy="40" r="34" fill="none" stroke="{warn}" stroke-width="2"/>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:{warn}">{ceil_ft if ceil_ft else "CLR"} ft / {cov_pct}%</div>
        </div>'''

    # ── Altimeter face → freezing level ───────────────────────────────
    def alt_freezing(fz_ft, label):
        # Needle: 0..12000 ft maps to 0..360 degrees. 3000 ft → 90°.
        ang = (fz_ft / 12000.0) * 360
        warn = "#ff5040" if fz_ft < 2000 else ("#ffaa00" if fz_ft < 4000 else "#23d18b")
        # Red arc 0..4000 ft = 0..120° on the dial
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <circle cx="40" cy="40" r="34" fill="#101626" stroke="#445" stroke-width="1.5"/>
            <!-- icing-risk arc: 0..4000 ft -->
            <path d="M 40 6 A 34 34 0 0 1 70.4 56.6" fill="none" stroke="#ff5040" stroke-width="3" opacity="0.4"/>
            <!-- tick marks every 30° (0,3,6,9 = 0,3,6,9 thousand) -->
            <g stroke="#fff" stroke-width="1" opacity="0.5">
              <line x1="40" y1="6" x2="40" y2="12"/>
              <line x1="74" y1="40" x2="68" y2="40"/>
              <line x1="40" y1="74" x2="40" y2="68"/>
              <line x1="6" y1="40" x2="12" y2="40"/>
            </g>
            <text x="40" y="20" fill="#fff" font-size="7" text-anchor="middle">0</text>
            <text x="62" y="42" fill="#fff" font-size="7" text-anchor="middle">3</text>
            <text x="40" y="65" fill="#fff" font-size="7" text-anchor="middle">6</text>
            <text x="18" y="42" fill="#fff" font-size="7" text-anchor="middle">9</text>
            <!-- needle -->
            <g transform="rotate({ang} 40 40)">
              <line x1="40" y1="40" x2="40" y2="12" stroke="{warn}" stroke-width="2"/>
              <polygon points="38,14 42,14 40,8" fill="{warn}"/>
            </g>
            <circle cx="40" cy="40" r="2.5" fill="{warn}"/>
            <text x="40" y="55" fill="#9aa5b8" font-size="5" text-anchor="middle">FRZ LVL</text>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:{warn}">{fz_ft} ft</div>
        </div>'''

    # ── Wind compass ──────────────────────────────────────────────────
    def wind_compass(deg, kt, gust, label):
        # Wind feather points FROM. Color by speed.
        if kt < 8:    color = "#88ccff"
        elif kt < 18: color = "#c8e88c"
        elif kt < 30: color = "#ffd24c"
        else:         color = "#ff5040"
        feather_len = 12 + min(kt, 40) * 0.4   # 12..28
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <circle cx="40" cy="40" r="34" fill="#101626" stroke="#445" stroke-width="1.5"/>
            <!-- cardinal letters -->
            <text x="40" y="13" fill="#9aa5b8" font-size="8" text-anchor="middle">N</text>
            <text x="68" y="43" fill="#9aa5b8" font-size="7" text-anchor="middle">E</text>
            <text x="40" y="71" fill="#9aa5b8" font-size="7" text-anchor="middle">S</text>
            <text x="13" y="43" fill="#9aa5b8" font-size="7" text-anchor="middle">W</text>
            <!-- 30° tick ring -->
            <g stroke="#445" stroke-width="0.5">
              {''.join(f'<line x1="40" y1="6" x2="40" y2="10" transform="rotate({a} 40 40)"/>' for a in range(0, 360, 30))}
            </g>
            <!-- Wind feather pointing FROM-direction (so it points away from where wind comes from) -->
            <g transform="rotate({deg} 40 40)">
              <line x1="40" y1="40" x2="40" y2="{40 - feather_len}" stroke="{color}" stroke-width="3" stroke-linecap="round"/>
              <polygon points="36,{40 - feather_len + 6} 44,{40 - feather_len + 6} 40,{40 - feather_len - 2}" fill="{color}"/>
              <!-- barbs for speed: every 10 kt = full barb, half barb for 5 -->
              {''.join(f'<line x1="40" y1="{34 - i*4}" x2="46" y2="{30 - i*4}" stroke="{color}" stroke-width="1.5"/>' for i in range(min(kt // 10, 4)))}
            </g>
            <circle cx="40" cy="40" r="2" fill="{color}"/>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:{color}">{deg:03d}° / {kt}{f"G{gust}" if gust else ""}KT</div>
        </div>'''

    # ── Plane-in-fog → visibility ─────────────────────────────────────
    def plane_fog(vis_sm, label):
        # Plane silhouette; fog overlay alpha = clamped (1 - vis/10)
        fog_alpha = max(0, min(0.85, 1 - vis_sm / 10.0))
        warn = "#ff5040" if vis_sm < 3 else ("#ffaa00" if vis_sm < 5 else "#23d18b")
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <rect x="0" y="0" width="80" height="80" fill="#0a0e16" rx="8"/>
            <!-- plane side profile -->
            <g transform="translate(8 24)" fill="#d8e1ec">
              <path d="M2 18 L20 14 L36 12 L52 14 L60 18 L52 20 L42 19 L36 24 L34 24 L34 19 L30 19 L26 22 L23 22 L23 19 L18 19 L14 22 L11 22 L13 19 L4 19 Z"/>
              <path d="M30 12 L34 4 L36 4 L33 12 Z"/>
              <!-- engine -->
              <ellipse cx="40" cy="18" rx="3" ry="2" fill="#a8b3c8"/>
            </g>
            <!-- fog wash -->
            <rect x="0" y="0" width="80" height="80" fill="#cfd6e0" opacity="{fog_alpha:.2f}" rx="8"/>
            <!-- distance scale ticks -->
            <line x1="6" y1="68" x2="74" y2="68" stroke="#445" stroke-width="0.5"/>
            <text x="6" y="75" fill="#666" font-size="5">0</text>
            <text x="40" y="75" fill="#666" font-size="5" text-anchor="middle">5sm</text>
            <text x="74" y="75" fill="#666" font-size="5" text-anchor="end">10+</text>
            <line x1="{6 + min(vis_sm, 10) * 6.8:.1f}" y1="64" x2="{6 + min(vis_sm, 10) * 6.8:.1f}" y2="72" stroke="{warn}" stroke-width="2"/>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:{warn}">VIS {vis_sm} SM</div>
        </div>'''

    # ── Stratus stack → cloud coverage by altitude ────────────────────
    def stratus_stack(low_pct, mid_pct, high_pct, label):
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <rect x="0" y="0" width="80" height="80" fill="#0a0e16" rx="8"/>
            <!-- HIGH band -->
            <rect x="8" y="14" width="64" height="6" fill="#cfd6e0" opacity="{high_pct/100:.2f}" rx="2"/>
            <text x="6" y="19" fill="#666" font-size="6" text-anchor="end">H</text>
            <!-- MID band -->
            <rect x="8" y="34" width="64" height="6" fill="#cfd6e0" opacity="{mid_pct/100:.2f}" rx="2"/>
            <text x="6" y="39" fill="#666" font-size="6" text-anchor="end">M</text>
            <!-- LOW band -->
            <rect x="8" y="54" width="64" height="6" fill="#cfd6e0" opacity="{low_pct/100:.2f}" rx="2"/>
            <text x="6" y="59" fill="#666" font-size="6" text-anchor="end">L</text>
            <!-- ground -->
            <rect x="0" y="68" width="80" height="12" fill="#3a2820" rx="0 0 8 8"/>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:#9aa5b8">L{low_pct} M{mid_pct} H{high_pct}</div>
        </div>'''

    # ── VSI needle → pressure tendency (ΔMSLP per 3hr in mb) ──────────
    def vsi_press(d_mb, label):
        # +/-3 mb / 3hr = full deflection. positive = up, negative = down.
        ang = max(-90, min(90, (d_mb / 3.0) * 90))
        warn = "#23d18b" if d_mb >= 0 else ("#ffaa00" if d_mb > -2 else "#ff5040")
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <circle cx="40" cy="40" r="34" fill="#101626" stroke="#445" stroke-width="1.5"/>
            <text x="40" y="13" fill="#9aa5b8" font-size="6" text-anchor="middle">+3</text>
            <text x="40" y="71" fill="#9aa5b8" font-size="6" text-anchor="middle">-3</text>
            <text x="13" y="43" fill="#9aa5b8" font-size="6" text-anchor="middle">0</text>
            <!-- arc up = green, arc down = red -->
            <path d="M 6 40 A 34 34 0 0 1 40 6" fill="none" stroke="#23d18b" stroke-width="2" opacity="0.4"/>
            <path d="M 6 40 A 34 34 0 0 0 40 74" fill="none" stroke="#ff5040" stroke-width="2" opacity="0.4"/>
            <!-- Needle: 0 = pointing left (W), +90 = up (N), -90 = down (S) -->
            <g transform="rotate({-ang} 40 40)">
              <line x1="40" y1="40" x2="10" y2="40" stroke="{warn}" stroke-width="2"/>
              <polygon points="12,38 12,42 6,40" fill="{warn}"/>
            </g>
            <circle cx="40" cy="40" r="2" fill="{warn}"/>
            <text x="40" y="62" fill="#9aa5b8" font-size="5" text-anchor="middle">ΔMSLP / 3h</text>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:{warn}">{d_mb:+.1f} mb</div>
        </div>'''

    # ── Animated anemometer → station wind speed ──────────────────────
    def anemo(kt, label, anim_id):
        if kt < 1:    speed = "0s"
        elif kt < 8:  speed = "3s"
        elif kt < 18: speed = "1.4s"
        elif kt < 30: speed = "0.7s"
        else:         speed = "0.35s"
        if kt < 8:    color = "#88ccff"
        elif kt < 18: color = "#c8e88c"
        elif kt < 30: color = "#ffd24c"
        else:         color = "#ff5040"
        return f'''<div class="card">
          <svg viewBox="0 0 80 80" width="100" height="100">
            <rect x="0" y="0" width="80" height="80" fill="#0a0e16" rx="8"/>
            <!-- post -->
            <line x1="40" y1="44" x2="40" y2="74" stroke="#445" stroke-width="2"/>
            <!-- 3 cup arms -->
            <g style="transform-origin: 40px 40px; animation: anemoSpin {speed} linear infinite">
              <line x1="40" y1="40" x2="40" y2="22" stroke="{color}" stroke-width="1.5"/>
              <circle cx="40" cy="20" r="3" fill="{color}"/>
              <line x1="40" y1="40" x2="55.6" y2="49" stroke="{color}" stroke-width="1.5"/>
              <circle cx="55.6" cy="49" r="3" fill="{color}"/>
              <line x1="40" y1="40" x2="24.4" y2="49" stroke="{color}" stroke-width="1.5"/>
              <circle cx="24.4" cy="49" r="3" fill="{color}"/>
            </g>
          </svg>
          <div class="lbl">{label}</div>
          <div class="val" style="color:{color}">{kt} KT</div>
        </div>'''

    # ── Demo conditions for each instrument ───────────────────────────
    rows_html = ""
    sections = [
        ("Mini Attitude Indicator → Ceiling",
         "Top half = sky/cloud tint, bottom = ground. Horizon line position encodes ceiling AGL. Tick marks at 1k/3k/5k. Ring color flushes warm when ceiling enters hazardous band.",
         [ai_ceiling(5000, 5, "CLR · 5sm vis"),
          ai_ceiling(3500, 35, "SCT 035"),
          ai_ceiling(1800, 75, "BKN 018"),
          ai_ceiling(600, 95, "OVC 006 — bad")]),
        ("Altimeter → Freezing Level",
         "Single needle on a 0-12k face. Red arc covers icing-risk band (0-4k AGL). Warm color when freezing layer drops into typical bush altitudes.",
         [alt_freezing(11000, "summer day"),
          alt_freezing(6500, "fall morning"),
          alt_freezing(3200, "freezing rain risk"),
          alt_freezing(1200, "icing — danger")]),
        ("Wind Compass → Surface Wind",
         "Feather points FROM. Length + color encode speed; barbs encode 10kt increments. Color palette matches the wind-flow streamlines.",
         [wind_compass(330, 5, 0, "calm"),
          wind_compass(180, 14, 21, "moderate gusty"),
          wind_compass(75, 28, 38, "strong cross"),
          wind_compass(220, 42, 55, "severe")]),
        ("Plane-in-Fog → Visibility",
         "Side-profile plane behind a fog wash. Alpha grows with reducing visibility — the icon physically demonstrates how much you'll see. Tick on horizontal scale shows actual sm.",
         [plane_fog(10, "10+ SM"),
          plane_fog(6, "6 SM"),
          plane_fog(3, "3 SM marginal"),
          plane_fog(1, "1 SM IFR")]),
        ("Stratus Stack → Cloud Coverage by Altitude",
         "Three horizontal bars (low / mid / high). Each bar's opacity = % coverage in that altitude band. SCT/BKN/OVC reads instantly without parsing abbreviations.",
         [stratus_stack(0, 0, 0, "CLR all levels"),
          stratus_stack(30, 15, 0, "FEW low / SCT mid"),
          stratus_stack(75, 30, 0, "BKN low"),
          stratus_stack(95, 90, 75, "OVC layered")]),
        ("VSI Needle → Pressure Tendency",
         "Needle deflection = ΔMSLP over 3 hours. Up = pressure climbing (weather improving), down = falling (storm approaching).",
         [vsi_press(2.5, "rising — clearing"),
          vsi_press(0.3, "steady"),
          vsi_press(-1.5, "falling"),
          vsi_press(-2.8, "plummeting — storm")]),
        ("Animated Anemometer → Per-Station Wind",
         "Tiny 3-cup spinning anemometer. Spin rate proportional to wind speed; eye catches motion before reading. For map-pin overlays at each MWOS.",
         [anemo(2, "calm", "a1"),
          anemo(12, "moderate", "a2"),
          anemo(24, "strong", "a3"),
          anemo(40, "gale", "a4")]),
    ]
    for title, blurb, cards in sections:
        rows_html += f'''
        <section>
          <h2>{title}</h2>
          <p class="blurb">{blurb}</p>
          <div class="cards">{"".join(cards)}</div>
        </section>'''

    # The proposed bottom strip — all instruments together at "current" condition
    strip_cards = "".join([
        ai_ceiling(2200, 65, "CEILING"),
        alt_freezing(4800, "FREEZING"),
        wind_compass(170, 13, 21, "WIND"),
        plane_fog(5, "VIS"),
        stratus_stack(45, 20, 5, "CLOUDS"),
        vsi_press(-0.8, "PRESSURE"),
    ])

    return _publicize(f'''<!doctype html><html><head><meta charset="utf-8"><title>WX Icon Preview — SkyBridge Alaska</title>
    <style>
    @keyframes anemoSpin {{ from {{ transform: rotate(0deg); }} to {{ transform: rotate(360deg); }} }}
    body {{ background:#0a0e16; color:#d8e1ec; font-family:system-ui,sans-serif; padding:24px; margin:0; }}
    h1 {{ color:#23d18b; font-size:18px; letter-spacing:2px; text-transform:uppercase; margin:0 0 8px; }}
    h1 .dev {{ background:#ff8800; color:#000; padding:2px 8px; border-radius:4px; font-size:12px; margin-left:8px; vertical-align:middle; }}
    p.lede {{ color:#9aa5b8; font-size:13px; max-width:980px; line-height:1.5; }}
    h2 {{ color:#0090ff; font-size:13px; text-transform:uppercase; letter-spacing:1.5px; margin:28px 0 4px; }}
    p.blurb {{ color:#9aa5b8; font-size:11px; max-width:780px; margin:0 0 12px; line-height:1.5; }}
    section {{ background:#141a26; padding:16px 20px; border-radius:10px; margin-bottom:14px; }}
    .cards {{ display:flex; gap:18px; flex-wrap:wrap; }}
    .card {{ background:#1a2030; border:1px solid #2a3140; border-radius:8px; padding:10px 12px 8px; text-align:center; min-width:120px; }}
    .lbl {{ color:#9aa5b8; font-size:10px; text-transform:uppercase; letter-spacing:1px; margin-top:4px; }}
    .val {{ font-size:11px; font-weight:600; margin-top:2px; }}
    .strip {{ background:linear-gradient(180deg, #0a0e16 0%, #0d121d 100%); border:1px solid #2a3140; border-radius:12px; padding:12px; margin-top:24px; }}
    .strip h2 {{ margin:0 0 10px; }}
    .strip .cards {{ justify-content:space-around; }}
    .note {{ background:#1f2738; padding:14px 18px; border-radius:8px; margin-top:24px; font-size:12px; line-height:1.6; color:#9aa5b8; }}
    code {{ background:#1f2738; padding:1px 5px; border-radius:3px; color:#23d18b; font-size:11px; }}
    </style><!--SBNAVCSS--></head><body><!--SBNAV-->
    <h1>Weather HUD Icons <span class="beta">BETA</span></h1>
    <p class="lede">Aviation 6-pack vocabulary repurposed for weather. Pilots already parse these shapes by reflex — free cognition. Each row shows the same icon under four conditions so we can see how it 'reads' at a glance without a legend. Iterate freely; nothing here is wired into the live kneeboard yet.</p>

    {rows_html}

    <div class="strip">
      <h2>Proposed bottom-strip layout — all instruments at current viewport conditions</h2>
      <p class="blurb">This is what the persistent ribbon along the bottom of the kneeboard map could look like. Six glanceable instruments showing viewport-averaged conditions.</p>
      <div class="cards">{strip_cards}</div>
    </div>

    <div class="note">
      <strong>Source:</strong> All icons are inline SVG generated server-side in <code>kneeboard_dev.py</code> at <code>/wx-icons-preview</code>. Edit and reload — no JS framework, no build step.<br>
      <strong>Next moves once we pick a starting set:</strong> wire them to <code>/api/wx/grid</code> (viewport-averaged) and to per-station data, port the SVG into the live kneeboard, and add a toggle to show/hide the bottom strip.
    </div>
    <!--SBFOOTER--></body></html>''', "/public/wx-icons-preview")


# ── /wx-validate historical logging (SQLite, 30-day rolling retention) ────
# Every 10 min, snapshot every (anchor × source) into wx_obs. Lets the dashboard
# replay any past hour and produce per-station per-field 30-day report cards
# for the FAA cert path. NVMe-resident so the SD card doesn't take write churn.
_WX_DB_PATH    = "/mnt/nvme/skybridge/wx-validate.db"
_WX_DB_LOCK    = threading.Lock()
_WX_LOG_PERIOD = 600        # 10 min between snapshots
_WX_RETENTION  = 30 * 86400 # 30 days

# Every MWOS station + a handful of supplemental airports without MWOS that
# we still want to track (PANC/PAED/PAJN, etc.). Each anchor: a stable id, a
# human name, lat/lon, optional ICAO if it's METAR-paired, and a short note.
# The whole point of the cert dashboard is to expose every node in our
# observation network — Walter Combs at Montis runs the calibrated MWOS fleet
# and this page is the public proof that his data agrees with NWS METARs.
# Default weights for the SkyBridge Composite weighted-ensemble. Higher =
# more trusted in the composite. Hand-tuned starting values; over time the
# historical-logging table lets us compute optimal weights from observed
# accuracy vs METAR baseline. These are TUNABLE — adjust as the data
# accumulates and we see which sources track METAR most closely.
_WX_CERT_WEIGHTS = {
    "metar":    1.00,   # FAA observation — ground truth (when present, dominates)
    "mwos":     0.90,   # Montis calibrated observation
    "nws":      0.50,   # NWS gridded forecast
    "om_gfs":   0.30,   # NOAA GFS via Open-Meteo
    "om_gem":   0.30,   # Canadian GEM
    "om_ecmwf": 0.40,   # ECMWF — historically high skill
    "om_jma":   0.20,   # Japan Met Agency
}

# Tag-to-display-name lookup for the certified composite annotation.
_WX_CERT_LABEL = {
    "metar": "METAR", "mwos": "MWOS", "nws": "NWS Grid",
    "om_gfs": "GFS", "om_gem": "GEM", "om_ecmwf": "ECMWF", "om_jma": "JMA",
}

def _compute_certified(by_source):
    """Weighted-ensemble composite from the 7 sources we collect. Input is a
    dict of {source_tag: unified-point or None}. Returns a unified-shape point
    with weighted-mean values per scalar field, plus a 'contributors' list
    identifying which sources actually fed each field.

    Wind direction is averaged via vector decomposition (atan2 of weighted u/v
    components) so 350° + 10° → 0°, not 180°.
    """
    if not by_source:
        return None
    out = {
        "source": "certified:skybridge",
        "label": "SKYBRIDGE COMPOSITE",
        "ts": "",
        "dir_deg": None, "speed_kt": None, "gust_kt": None,
        "temp_c": None, "freezing_level_ft": None,
        "cloud_pct": None, "cloud_low_pct": None,
        "cloud_mid_pct": None, "cloud_high_pct": None,
        "visibility_sm": None, "precip_mm": None, "pressure_mb": None,
        "contributors": {},   # per-field list of (source_tag, weight) used
        "weights_used":  dict(_WX_CERT_WEIGHTS),  # snapshot of weights at compute time
    }

    # Scalar-field weighted mean
    for field in ("speed_kt", "gust_kt", "temp_c", "freezing_level_ft",
                  "cloud_pct", "cloud_low_pct", "cloud_mid_pct",
                  "cloud_high_pct", "visibility_sm", "precip_mm", "pressure_mb"):
        weighted_sum = 0.0
        weight_sum = 0.0
        used = []
        for tag, pt in by_source.items():
            if pt is None: continue
            v = pt.get(field)
            if v is None or not isinstance(v, (int, float)): continue
            w = _WX_CERT_WEIGHTS.get(tag, 0)
            if w <= 0: continue
            weighted_sum += w * float(v)
            weight_sum += w
            used.append((tag, w))
        if weight_sum > 0:
            out[field] = round(weighted_sum / weight_sum, 2)
            out["contributors"][field] = used

    # Wind direction: vector mean (decompose to u/v, weight, recompose)
    u_sum = v_sum = w_sum = 0.0
    used_dir = []
    for tag, pt in by_source.items():
        if pt is None: continue
        d = pt.get("dir_deg")
        s = pt.get("speed_kt")
        if d is None or s is None or d < 0: continue   # skip VRB (-1)
        w = _WX_CERT_WEIGHTS.get(tag, 0)
        if w <= 0: continue
        rad = float(d) * math.pi / 180.0
        # Standard vector decomposition: u = sin, v = cos (north-up convention)
        u_sum += w * float(s) * math.sin(rad)
        v_sum += w * float(s) * math.cos(rad)
        w_sum += w
        used_dir.append((tag, w))
    if w_sum > 0:
        u_avg = u_sum / w_sum
        v_avg = v_sum / w_sum
        dir_avg = math.atan2(u_avg, v_avg) * 180.0 / math.pi
        if dir_avg < 0: dir_avg += 360
        out["dir_deg"] = round(dir_avg, 1)
        out["contributors"]["dir_deg"] = used_dir

    return out


_WX_VALIDATE_ANCHORS = [
    # ── Montis MWOS network (calibrated, private) ──
    {"id": "MWOS:133", "name": "Lake Hood MWOS",        "lat": 61.1776,    "lon": -149.9615,  "icao": "PALH", "note": "Float capital of the world / co-located METAR PALH"},
    {"id": "MWOS:1",   "name": "Merrill Field MWOS",   "lat": 61.2167,    "lon": -149.8337,  "icao": "PAMR", "note": "GA Class D / co-located METAR PAMR"},
    {"id": "MWOS:265", "name": "Merrill Field MWOS 2", "lat": 61.2148,    "lon": -149.8396,  "icao": "PAMR", "note": "Second MWOS at PAMR — internal cross-check"},
    {"id": "MWOS:166", "name": "Fairbanks Intl MWOS",  "lat": 64.813056,  "lon": -147.8737,  "icao": "PAFA", "note": "Interior / co-located METAR PAFA"},
    {"id": "MWOS:67",  "name": "Thompson Pass MWOS",   "lat": 61.141065,  "lon": -145.749145,"icao": "",     "note": "Mountain pass — no nearby METAR"},
    {"id": "MWOS:101", "name": "Whittier Harbor MWOS", "lat": 60.7775,    "lon": -148.6862,  "icao": "",     "note": "Coastal — Prince William Sound"},
    {"id": "MWOS:595", "name": "Anaktuvuk Pass MWOS",  "lat": 68.137126,  "lon": -151.741023,"icao": "",     "note": "Brooks Range pass — high elevation"},
    {"id": "MWOS:496", "name": "Atqasuk MWOS",         "lat": 70.4697,    "lon": -157.4307,  "icao": "PATK", "note": "North Slope / METAR PATK"},
    {"id": "MWOS:562", "name": "Wainwright MWOS",      "lat": 70.638167,  "lon": -160.018044,"icao": "",     "note": "Arctic coast"},
    {"id": "MWOS:529", "name": "Nuiqsut MWOS",         "lat": 70.2129,    "lon": -150.9998,  "icao": "PAQT", "note": "North Slope village"},
    {"id": "MWOS:430", "name": "Kaktovik MWOS",        "lat": 70.1101,    "lon": -143.635,   "icao": "",     "note": "Arctic Refuge / Barter Island"},
    {"id": "MWOS:2",   "name": "Rampart MWOS",         "lat": 65.51125,   "lon": -150.15225, "icao": "PRMP", "note": "Yukon River"},
    {"id": "MWOS:694", "name": "Port Graham MWOS",     "lat": 59.350842,  "lon": -151.827721,"icao": "",     "note": "Lower Kenai Peninsula"},
    {"id": "MWOS:232", "name": "Port Townsend MWOS",   "lat": 48.106887,  "lon": -122.77775, "icao": "",     "note": "WA state — out-of-region reference"},
    # ── Supplemental airports without MWOS (METAR + model only) ──
    {"id": "PANC", "name": "Anchorage Intl",     "lat": 61.1744, "lon": -149.9964, "icao": "PANC", "note": "Class C / Pacific gateway"},
    {"id": "PAED", "name": "Elmendorf AFB",      "lat": 61.2510, "lon": -149.8063, "icao": "PAED", "note": "JBER military / Anchorage Bowl"},
    {"id": "PAAQ", "name": "Palmer",             "lat": 61.5949, "lon": -149.0887, "icao": "PAAQ", "note": "Mat-Su valley"},
    {"id": "PAJN", "name": "Juneau",             "lat": 58.3547, "lon": -134.5762, "icao": "PAJN", "note": "SE AK / coastal terrain"},
    {"id": "PADQ", "name": "Kodiak",             "lat": 57.7500, "lon": -152.4939, "icao": "PADQ", "note": "Kodiak Island"},
    {"id": "PAEN", "name": "Kenai Municipal",    "lat": 60.5731, "lon": -151.2450, "icao": "PAEN", "note": "Kenai Peninsula"},
]

def _wx_db_init():
    """Create the wx_obs table + indexes if missing. Idempotent."""
    os.makedirs(os.path.dirname(_WX_DB_PATH), exist_ok=True)
    with sqlite3.connect(_WX_DB_PATH) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS wx_obs (
              ts             INTEGER NOT NULL,    -- unix epoch seconds (snapshot bucket)
              anchor         TEXT    NOT NULL,    -- ICAO
              source         TEXT    NOT NULL,    -- 'metar' | 'mwos' | 'nws' | 'om'
              dir_deg        REAL,
              speed_kt       REAL,
              gust_kt        REAL,
              temp_c         REAL,
              visibility_sm  REAL,
              pressure_mb    REAL,
              cloud_pct      REAL,
              raw_json       TEXT,                -- full unified-shape blob, for forensics
              PRIMARY KEY (ts, anchor, source)
            );
            CREATE INDEX IF NOT EXISTS idx_wx_obs_ts ON wx_obs(ts);
            CREATE INDEX IF NOT EXISTS idx_wx_obs_anchor_ts ON wx_obs(anchor, ts);
        """)
        con.commit()

def _wx_db_insert(ts, anchor, source_tag, p):
    """Insert (or replace) one row. p is a unified-shape point dict."""
    if p is None:
        return
    with _WX_DB_LOCK, sqlite3.connect(_WX_DB_PATH) as con:
        con.execute("""
            INSERT OR REPLACE INTO wx_obs
              (ts, anchor, source, dir_deg, speed_kt, gust_kt, temp_c,
               visibility_sm, pressure_mb, cloud_pct, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            int(ts), anchor, source_tag,
            p.get("dir_deg"), p.get("speed_kt"), p.get("gust_kt"),
            p.get("temp_c"), p.get("visibility_sm"), p.get("pressure_mb"),
            p.get("cloud_pct"), json.dumps(p, default=str)
        ))
        con.commit()

def _wx_db_purge():
    """Delete rows older than _WX_RETENTION."""
    cutoff = int(time.time()) - _WX_RETENTION
    with _WX_DB_LOCK, sqlite3.connect(_WX_DB_PATH) as con:
        con.execute("DELETE FROM wx_obs WHERE ts < ?", (cutoff,))
        con.commit()

def _wx_db_take_snapshot():
    """Pull every (anchor × source) right now and write to the DB. Designed
    to be called from the background thread every 10 min; also reusable from
    a debug endpoint."""
    try:
        # Trigger any uncached upstream fetches by calling the resolution
        # paths (these all cache internally).
        _ = _fetch_model_grid()
        anchor_lls = [(a["lat"], a["lon"]) for a in _WX_VALIDATE_ANCHORS]
        om_pts = _fetch_anchor_openmeteo(anchor_lls)

        # Warm METAR_CACHE / mwos_* via direct call paths if they haven't yet.
        # Note: no jsonify here — we just need the side effect of populating cache.
        try:
            with app.test_request_context():
                api_weather()  # warms METAR_CACHE
                api_mwos()     # warms _WX_CACHE['mwos_*']
        except Exception:
            pass

        metar_cache = METAR_CACHE.get("data") or {}
        metar_raws = metar_cache.get("metars", {})
        metar_meta = metar_cache.get("meta", {})

        # Round to nearest 5-min bucket so concurrent snapshots align cleanly.
        ts_bucket = int(time.time() // 300) * 300

        # Build MWOS proximity lookup once
        mwos_obs = []
        for hx, entry in _WX_CACHE.items():
            if not hx.startswith("mwos_"):
                continue
            d = entry.get("data") or {}
            obs_list = d.get("observations", []) or []
            if obs_list:
                mwos_obs.append((d, obs_list[0]))

        def nearest_mwos(lat, lon, max_nm=5):
            best = None; best_d = max_nm
            for d, latest in mwos_obs:
                dist = _great_circle_nm((lat, lon), (d.get("latitude", 0), d.get("longitude", 0)))
                if dist < best_d:
                    best = (d, latest); best_d = dist
            return best

        rows_written = 0
        for idx, anc in enumerate(_WX_VALIDATE_ANCHORS):
            anchor_id = anc["id"]
            lat, lon = anc["lat"], anc["lon"]
            icao = anc.get("icao") or ""
            m_raw = metar_raws.get(icao) if icao else None
            metar_pt = (_parse_metar_to_point(icao, m_raw, metar_meta.get(icao, {}).get("reportTime", ""))
                        if m_raw and m_raw != "(unavailable)" else None)
            # Specific MWOS for MWOS:N anchors, else closest-within-5nm
            mwos_pt = None
            if anchor_id.startswith("MWOS:"):
                site_id = anchor_id.split(":", 1)[1]
                ce = _WX_CACHE.get(f"mwos_{site_id}", {}).get("data") or {}
                obs_l = ce.get("observations", []) or []
                if obs_l:
                    mwos_pt = _mwos_to_point(ce, obs_l[0])
            else:
                nearest = nearest_mwos(lat, lon)
                mwos_pt = _mwos_to_point(nearest[0], nearest[1]) if nearest else None
            nws_pt = _fetch_nws_gridpoint(lat, lon)
            anchor_models = om_pts[idx] if idx < len(om_pts) else []
            # Build by-source map for the certified composite
            by_source = {"metar": metar_pt, "mwos": mwos_pt, "nws": nws_pt}
            for j, (_om_id, src_tag, _label, _note) in enumerate(_OM_MODELS):
                by_source[src_tag] = anchor_models[j] if j < len(anchor_models) else None

            # Log each individual source
            for tag, pt in by_source.items():
                if pt is not None:
                    _wx_db_insert(ts_bucket, anchor_id, tag, pt)
                    rows_written += 1
            # Compute and log the SkyBridge Composite (weighted-ensemble)
            cert_pt = _compute_certified(by_source)
            if cert_pt:
                _wx_db_insert(ts_bucket, anchor_id, "certified", cert_pt)
                rows_written += 1
        _wx_db_purge()
        print(f"[wx-validate] snapshot ts={ts_bucket} wrote {rows_written} rows")
        return rows_written
    except Exception as e:
        print(f"[wx-validate] snapshot error: {e}")
        return 0

def _wx_db_query_at(ts_target):
    """Return rows nearest to ts_target (within ±15 min). Grouped by
    (anchor, source). Returns dict of {(anchor, source): unified_pt_dict}."""
    with sqlite3.connect(_WX_DB_PATH) as con:
        con.row_factory = sqlite3.Row
        # Pick the row per (anchor,source) whose ts is closest to ts_target.
        # Simple approach: pull all rows in ±15 min window, then in Python
        # pick nearest per group.
        win = 15 * 60
        rows = con.execute("""
            SELECT * FROM wx_obs
            WHERE ts BETWEEN ? AND ?
        """, (ts_target - win, ts_target + win)).fetchall()
    grouped = {}
    for r in rows:
        key = (r["anchor"], r["source"])
        prev = grouped.get(key)
        if prev is None or abs(r["ts"] - ts_target) < abs(prev["ts"] - ts_target):
            grouped[key] = dict(r)
    # Re-hydrate to unified-shape dicts
    out = {}
    for (anchor, source), r in grouped.items():
        try:
            blob = json.loads(r["raw_json"]) if r.get("raw_json") else None
        except Exception:
            blob = None
        out[(anchor, source)] = blob or {
            "dir_deg": r["dir_deg"], "speed_kt": r["speed_kt"], "gust_kt": r["gust_kt"],
            "temp_c": r["temp_c"], "visibility_sm": r["visibility_sm"],
            "pressure_mb": r["pressure_mb"], "cloud_pct": r["cloud_pct"],
            "source": source + ":hist",
        }
    return out

def _wx_db_available_hours():
    """Returns sorted list of distinct hour-bucket epochs that have at least
    one row. Used by the date+hour picker to grey out unavailable hours."""
    with sqlite3.connect(_WX_DB_PATH) as con:
        rows = con.execute("""
            SELECT DISTINCT (ts / 3600) * 3600 AS h FROM wx_obs ORDER BY h DESC
        """).fetchall()
    return [r[0] for r in rows]

def _wx_logger_loop():
    """Background snapshot loop. Started once at import time."""
    while True:
        try:
            _wx_db_take_snapshot()
        except Exception as e:
            print(f"[wx-validate] loop error: {e}")
        time.sleep(_WX_LOG_PERIOD)

# Init DB + start the background snapshot thread
try:
    _wx_db_init()
    _wx_logger_thread = threading.Thread(target=_wx_logger_loop, daemon=True, name="wx-validate-logger")
    _wx_logger_thread.start()
except Exception as e:
    print(f"[wx-validate] failed to start logger: {e}")




@app.route("/public/wx-validate")
def wx_validate_public():
    """Multi-source weather comparison dashboard. Side-by-side rendering of
    the same observation across:
        NWS METAR     — FAA-approved authoritative observation
        MWOS          — Montis Corp calibrated automated weather (private)
        NWS Grid      — FAA-approved gridded forecast model
        Open-Meteo    — open-data model used as backfill
    For each anchor station we render: wind, temp, vis, pressure, cloud cover,
    plus per-field deltas vs the METAR baseline. The whole point is to show
    exactly where SkyBridge agrees with the canonical sources, where it
    enriches them with MWOS, and where any drift indicates a sensor needing
    calibration. This page is the seed of the FAA certification artifact.
    """
    # Pull every anchor — full MWOS network + supplemental airports. List is
    # the module-level _WX_VALIDATE_ANCHORS so the historical logger uses
    # exactly the same set.
    ANCHORS = _WX_VALIDATE_ANCHORS

    # Optional ?ts=<epoch> query param to replay a past hour from the DB.
    # If absent, we render the live now.
    from flask import request as _flask_request
    ts_q = _flask_request.args.get("ts", "").strip()
    historical = None
    if ts_q.isdigit():
        try:
            historical = _wx_db_query_at(int(ts_q))
        except Exception:
            historical = None

    # Fetch the live sources for all anchors. MWOS + METAR already in cache.
    # Open-Meteo: one batched call returns ALL models in one shot. NWS
    # gridpoint: one or two calls per anchor (cached). Historical mode pulls
    # everything from the DB instead.
    if historical is None:
        anchor_lls = [(a["lat"], a["lon"]) for a in ANCHORS]
        om_pts = _fetch_anchor_openmeteo(anchor_lls)
        # om_pts is list-of-list: [anchor_idx][model_idx]
        om_by_id = {}
        for i in range(len(ANCHORS)):
            anc_id = ANCHORS[i]["id"]
            anc_models = om_pts[i] if i < len(om_pts) else []
            om_by_id[anc_id] = {}
            for j, (_om_id, src_tag, _label, _note) in enumerate(_OM_MODELS):
                om_by_id[anc_id][src_tag] = anc_models[j] if j < len(anc_models) else None
    else:
        om_by_id = {}

    # Find the closest MWOS station to each anchor (within 5 nm = "co-located").
    mwos_obs = []
    for hx, entry in _WX_CACHE.items():
        if not hx.startswith("mwos_"):
            continue
        d = entry.get("data") or {}
        obs_list = d.get("observations", []) or []
        if not obs_list:
            continue
        mwos_obs.append((d, obs_list[0]))

    def nearest_mwos(lat, lon, max_nm=5):
        best = None
        best_d = max_nm
        for d, latest in mwos_obs:
            mlat = d.get("latitude", 0)
            mlon = d.get("longitude", 0)
            dist = _great_circle_nm((lat, lon), (mlat, mlon))
            if dist < best_d:
                best = (d, latest)
                best_d = dist
        return best

    # Pull METAR raw for each anchor & parse into unified shape.
    metar_cache = METAR_CACHE.get("data") or {}
    metar_raws = metar_cache.get("metars", {})
    metar_meta = metar_cache.get("meta", {})

    # ── Render helpers ────────────────────────────────────────────────────
    def fmt_wind(p):
        if p is None or p.get("dir_deg") is None or p.get("speed_kt") is None: return "—"
        d = p["dir_deg"]
        s = p["speed_kt"]
        g = p.get("gust_kt") or 0
        d_str = "VRB" if d == -1 else f"{int(d):03d}°"
        return f'{d_str} / {s:.0f}{f"G{g:.0f}" if g >= 1 else ""} kt'

    def fmt_temp(p):
        v = p.get("temp_c") if p else None
        return f'{v:.1f} °C' if isinstance(v, (int, float)) else "—"

    def fmt_vis(p):
        v = p.get("visibility_sm") if p else None
        return f'{v:.1f} sm' if isinstance(v, (int, float)) else "—"

    def fmt_press(p):
        v = p.get("pressure_mb") if p else None
        return f'{v:.1f} mb' if isinstance(v, (int, float)) else "—"

    def fmt_cloud(p):
        v = p.get("cloud_pct") if p else None
        return f'{int(v)}%' if isinstance(v, (int, float)) else "—"

    def delta_class(value, baseline, ok, marginal):
        """Return a CSS class based on |delta| vs thresholds."""
        if value is None or baseline is None: return "na"
        try:
            d = abs(float(value) - float(baseline))
        except (TypeError, ValueError):
            return "na"
        if d <= ok: return "ok"
        if d <= marginal: return "warn"
        return "bad"

    def cell_html(p, baseline, field_key, fmt_fn,
                  ok_thresh=None, marginal_thresh=None, extra_class=""):
        """Render a comparison cell — value + colored delta-class background.
        extra_class allows the certified column to add a distinguishing border."""
        ec = (" " + extra_class) if extra_class else ""
        if p is None:
            return f'<td class="empty{ec}">—</td>'
        if baseline is None or baseline is p:
            cls = "baseline"
        else:
            cls = delta_class(p.get(field_key), baseline.get(field_key), ok_thresh or 1, marginal_thresh or 3)
        return f'<td class="{cls}{ec}">{fmt_fn(p)}</td>'

    rows_html = ""
    summary_acc = {"wind_spd": [], "temp": [], "press": []}
    for anc in ANCHORS:
        anchor_id = anc["id"]
        name = anc["name"]
        note = anc["note"]
        lat = anc["lat"]
        lon = anc["lon"]
        icao = anc.get("icao") or ""
        # Build per-source unified-shape points. Historical mode reads from DB;
        # live mode pulls from current caches + makes any needed upstream calls.
        if historical is not None:
            metar_pt = historical.get((anchor_id, "metar"))
            mwos_pt  = historical.get((anchor_id, "mwos"))
            nws_pt   = historical.get((anchor_id, "nws"))
            om_models = {tag: historical.get((anchor_id, tag)) for _id, tag, _l, _n in _OM_MODELS}
        else:
            m_raw = metar_raws.get(icao) if icao else None
            metar_pt = (_parse_metar_to_point(icao, m_raw, metar_meta.get(icao, {}).get("reportTime", ""))
                        if m_raw and m_raw != "(unavailable)" else None)
            mwos_pt = None
            if anchor_id.startswith("MWOS:"):
                site_id = anchor_id.split(":", 1)[1]
                cache_entry = _WX_CACHE.get(f"mwos_{site_id}", {}).get("data") or {}
                obs_list = cache_entry.get("observations", []) or []
                if obs_list:
                    mwos_pt = _mwos_to_point(cache_entry, obs_list[0])
            else:
                nearest = nearest_mwos(lat, lon)
                mwos_pt = _mwos_to_point(nearest[0], nearest[1]) if nearest else None
            nws_pt = _fetch_nws_gridpoint(lat, lon)
            om_models = om_by_id.get(anchor_id, {})

        # Build the source list: 3 fixed (METAR, MWOS, NWS Grid) + N models
        sources = [
            ("METAR",     metar_pt, "FAA-approved obs"),
            ("MWOS",      mwos_pt,  "Montis-calibrated"),
            ("NWS Grid",  nws_pt,   "FAA-approved fcst"),
        ]
        for _om_id, src_tag, label, _note in _OM_MODELS:
            sources.append((label, om_models.get(src_tag), "model"))

        # Compute the SkyBridge Composite — weighted mean across all
        # sources that reported. Appears as the rightmost column.
        if historical is not None:
            cert_pt = historical.get((anchor_id, "certified"))
        else:
            by_source = {"metar": metar_pt, "mwos": mwos_pt, "nws": nws_pt}
            for _om_id, src_tag, _label, _note in _OM_MODELS:
                by_source[src_tag] = om_models.get(src_tag)
            cert_pt = _compute_certified(by_source)
        sources.append(("SkyBridge", cert_pt, "certified"))

        # Track per-field deltas vs METAR for the summary table at the bottom.
        # Skip METAR itself (sources[0]); evaluate every other source including
        # the SkyBridge composite at the end.
        if metar_pt:
            for label, src, _kind in sources[1:]:
                if not src: continue
                src_id = src.get("source", "")
                if metar_pt.get("speed_kt") is not None and src.get("speed_kt") is not None:
                    summary_acc["wind_spd"].append((anchor_id, src_id, src["speed_kt"] - metar_pt["speed_kt"]))
                if metar_pt.get("temp_c") is not None and src.get("temp_c") is not None:
                    summary_acc["temp"].append((anchor_id, src_id, src["temp_c"] - metar_pt["temp_c"]))
                if metar_pt.get("pressure_mb") is not None and src.get("pressure_mb") is not None:
                    summary_acc["press"].append((anchor_id, src_id, src["pressure_mb"] - metar_pt["pressure_mb"]))

        # Display id: ICAO if METAR-paired, otherwise the MWOS:nnn id
        display_id = icao if icao else anchor_id
        # Helper: render one row; last source (certified composite) gets cert-cell
        def _row_cells(field, fmt_fn, ok_t, marg_t):
            html_acc = ""
            for i, (lbl, pt, kind) in enumerate(sources):
                ec = "cert-cell" if kind == "certified" else ""
                html_acc += cell_html(pt, metar_pt, field, fmt_fn, ok_t, marg_t, extra_class=ec)
            return html_acc

        rows_html += f'''
        <tr class="anchor-row">
          <td class="anchor" rowspan="5"><strong>{display_id}</strong><br><span class="aname">{name}</span><br><span class="anote">{note}</span></td>
          <td class="metric">Wind</td>{_row_cells("speed_kt", fmt_wind, 3, 7)}</tr>
        <tr><td class="metric">Temp</td>{_row_cells("temp_c", fmt_temp, 1, 3)}</tr>
        <tr><td class="metric">Visibility</td>{_row_cells("visibility_sm", fmt_vis, 1, 3)}</tr>
        <tr><td class="metric">Pressure</td>{_row_cells("pressure_mb", fmt_press, 1, 3)}</tr>
        <tr class="row-end"><td class="metric">Cloud</td>{_row_cells("cloud_pct", fmt_cloud, 20, 40)}</tr>
'''

    # Summary stats: per-source MAE vs METAR
    def mae(deltas, src_filter):
        vals = [abs(d) for icao, src, d in deltas if src.startswith(src_filter)]
        if not vals: return ("—", 0)
        return (f"{sum(vals)/len(vals):.2f}", len(vals))
    def bias(deltas, src_filter):
        vals = [d for icao, src, d in deltas if src.startswith(src_filter)]
        if not vals: return "—"
        b = sum(vals)/len(vals)
        return f"{b:+.2f}"

    src_keys = [("MWOS", "mwos:"), ("NWS Grid", "nws:")]
    for _om_id, src_tag, label, _note in _OM_MODELS:
        src_keys.append((label, "model:" + src_tag))
    src_keys.append(("SkyBridge Composite", "certified:"))
    summary_rows = ""
    for label, prefix in src_keys:
        ws_mae, ws_n = mae(summary_acc["wind_spd"], prefix)
        ws_bi = bias(summary_acc["wind_spd"], prefix)
        t_mae, t_n  = mae(summary_acc["temp"], prefix)
        t_bi = bias(summary_acc["temp"], prefix)
        p_mae, p_n  = mae(summary_acc["press"], prefix)
        p_bi = bias(summary_acc["press"], prefix)
        summary_rows += f'''
        <tr>
          <td><strong>{label}</strong></td>
          <td>{ws_mae} <span class="bias">({ws_bi})</span> <span class="n">n={ws_n}</span></td>
          <td>{t_mae} <span class="bias">({t_bi})</span> <span class="n">n={t_n}</span></td>
          <td>{p_mae} <span class="bias">({p_bi})</span> <span class="n">n={p_n}</span></td>
        </tr>'''

    # Provenance strip — which upstreams are FAA-approved, calibrated, or open
    metar_age = int(time.time() - METAR_CACHE.get("ts", 0)) if METAR_CACHE.get("ts") else None
    om_cache = _WX_CACHE.get("openmeteo_anchors") or {}
    om_age = int(time.time() - om_cache.get("ts", 0)) if om_cache.get("ts") else None
    nws_cache_keys = [k for k in _WX_CACHE if k.startswith("nws_grid_")]
    nws_age = None
    if nws_cache_keys:
        ages = [int(time.time() - _WX_CACHE[k].get("ts", 0)) for k in nws_cache_keys]
        nws_age = min(ages) if ages else None
    mwos_cache_keys = [k for k in _WX_CACHE if k.startswith("mwos_")]
    mwos_age = None
    if mwos_cache_keys:
        ages = [int(time.time() - _WX_CACHE[k].get("ts", 0)) for k in mwos_cache_keys]
        mwos_age = min(ages) if ages else None

    def age_str(s):
        if s is None: return "—"
        if s < 60:  return f"{s}s ago"
        if s < 3600: return f"{s//60}m ago"
        return f"{s//3600}h ago"

    return _publicize(f'''<!doctype html><html><head><meta charset="utf-8"><title>Weather Validate — SkyBridge Alaska</title>
    <style>
    body {{ background:#0a0e16; color:#d8e1ec; font-family:system-ui,sans-serif; padding:24px; margin:0; }}
    h1 {{ color:#23d18b; font-size:18px; letter-spacing:2px; text-transform:uppercase; margin:0 0 8px; }}
    h1 .dev {{ background:#ff8800; color:#000; padding:2px 8px; border-radius:4px; font-size:12px; margin-left:8px; vertical-align:middle; }}
    h1 a {{ color:#0090ff; font-size:11px; letter-spacing:1px; margin-left:14px; text-decoration:none; }}
    p.lede {{ color:#9aa5b8; font-size:13px; max-width:980px; line-height:1.5; }}
    h2 {{ color:#0090ff; font-size:13px; text-transform:uppercase; letter-spacing:1.5px; margin:28px 0 8px; }}
    h2 small {{ color:#7a8497; font-weight:400; text-transform:none; letter-spacing:0; margin-left:8px; font-size:11px; }}

    .provenance {{ display:flex; gap:14px; flex-wrap:wrap; background:#141a26; border-radius:8px; padding:14px 18px; margin-top:8px; }}
    .prov-chip {{ display:flex; flex-direction:column; gap:2px; padding:8px 14px; border-radius:6px; min-width:140px; background:#1a2030; border:1px solid #2a3140; }}
    .prov-chip .top {{ font-size:11px; font-weight:700; letter-spacing:1px; text-transform:uppercase; }}
    .prov-chip .age {{ font-size:11px; color:#9aa5b8; }}
    .prov-chip .tag {{ font-size:10px; color:#9aa5b8; }}
    .faa-approved .top {{ color:#23d18b; }}
    .faa-approved .tag {{ color:#23d18b; }}
    .calibrated .top {{ color:#ffbb00; }}
    .calibrated .tag {{ color:#ffbb00; }}
    .open .top {{ color:#88ccff; }}
    .open .tag {{ color:#88ccff; }}
    .composite .top {{ color:#cc44ff; }}
    .composite .tag {{ color:#cc44ff; }}

    table.cmp {{ width:100%; border-collapse:collapse; margin-top:14px; background:#141a26; border-radius:8px; overflow:hidden; }}
    table.cmp th {{ background:#1f2738; color:#9aa5b8; text-transform:uppercase; letter-spacing:1px; font-size:10px; padding:10px; text-align:left; border-bottom:1px solid #2a3140; }}
    table.cmp th.src {{ width:18%; }}
    table.cmp th.metric {{ width:10%; }}
    table.cmp th.anchor {{ width:18%; }}
    table.cmp td {{ padding:8px 12px; font-size:12px; border-bottom:1px solid #1a2030; vertical-align:middle; }}
    table.cmp td.anchor {{ background:#0d121d; vertical-align:middle; border-right:2px solid #2a3140; }}
    table.cmp td.anchor strong {{ color:#23d18b; font-size:14px; }}
    table.cmp td.anchor .aname {{ color:#d8e1ec; font-size:11px; }}
    table.cmp td.anchor .anote {{ color:#7a8497; font-size:10px; font-style:italic; }}
    table.cmp td.metric {{ color:#0090ff; font-weight:700; font-size:11px; }}
    table.cmp td.empty {{ color:#445; text-align:center; }}
    table.cmp td.baseline {{ color:#fff; background:#1f2738; font-weight:700; }}
    table.cmp td.ok {{ color:#23d18b; }}
    table.cmp td.warn {{ color:#ffbb00; }}
    table.cmp td.bad {{ color:#ff5040; font-weight:700; }}
    table.cmp td.na {{ color:#445; }}
    table.cmp tr.row-end td {{ border-bottom:2px solid #2a3140; padding-bottom:14px; }}
    table.cmp tr.anchor-row td {{ border-top:6px solid #0a0e16; padding-top:14px; }}
    table.cmp .faa {{ background:#1a4030; color:#23d18b; font-size:8px; padding:1px 4px; border-radius:3px; margin-left:4px; }}
    table.cmp .cal {{ background:#3a3520; color:#ffbb00; font-size:8px; padding:1px 4px; border-radius:3px; margin-left:4px; }}
    table.cmp .mdl {{ background:#2a3140; color:#88ccff; font-size:8px; padding:1px 4px; border-radius:3px; margin-left:4px; }}
    table.cmp th.cert {{ background:#2a1a3a; color:#cc88ff; border-left:2px solid #cc44ff; }}
    table.cmp .cert-tag {{ background:#cc44ff; color:#0a0e16; font-size:8px; padding:1px 4px; border-radius:3px; margin-left:4px; font-weight:700; }}
    table.cmp td.cert-cell {{ border-left:2px solid #cc44ff; background:#1a1228; color:#cc88ff; font-weight:700; }}
    table.cmp td.cert-cell.ok {{ color:#cc88ff; }}
    table.cmp td.cert-cell.warn {{ color:#ffbb88; }}
    table.cmp td.cert-cell.bad {{ color:#ff88aa; }}
    .weights {{ background:#141a26; border-radius:8px; padding:14px 18px; margin-top:8px; display:flex; gap:18px; flex-wrap:wrap; align-items:center; font-size:12px; }}
    .weights .wlabel {{ color:#9aa5b8; font-weight:700; letter-spacing:1px; text-transform:uppercase; font-size:10px; }}
    .weight-pill {{ display:inline-flex; gap:6px; align-items:center; background:#1a2030; padding:5px 10px; border-radius:6px; border:1px solid #2a3140; font-size:11px; }}
    .weight-pill .src-name {{ color:#d8e1ec; font-weight:700; }}
    .weight-pill .src-w {{ color:#ffd24c; }}

    table.summary {{ border-collapse:collapse; margin-top:14px; background:#141a26; border-radius:8px; overflow:hidden; min-width:580px; }}
    table.summary th {{ background:#1f2738; color:#9aa5b8; padding:10px 14px; font-size:10px; text-transform:uppercase; letter-spacing:1px; }}
    table.summary td {{ padding:10px 14px; font-size:12px; border-bottom:1px solid #2a3140; }}
    table.summary .bias {{ color:#ffbb00; font-size:10px; }}
    table.summary .n {{ color:#7a8497; font-size:10px; margin-left:6px; }}

    .legend {{ display:flex; gap:14px; flex-wrap:wrap; font-size:11px; color:#9aa5b8; align-items:center; margin:6px 0; }}
    .legend span {{ display:flex; align-items:center; gap:5px; }}
    .legend .swatch {{ width:14px; height:14px; border-radius:3px; }}
    .picker {{ display:flex; gap:14px; align-items:center; flex-wrap:wrap; background:#141a26; border-radius:8px; padding:14px 18px; margin-top:8px; font-size:12px; }}
    .picker-controls {{ display:flex; gap:8px; align-items:center; }}
    .picker input[type=datetime-local] {{ background:#0a0e16; color:#d8e1ec; border:1px solid #2a3140; border-radius:6px; padding:6px 10px; font-family:inherit; font-size:12px; color-scheme:dark; }}
    .btn {{ background:#1a2030; color:#9aa5b8; border:1px solid #2a3140; border-radius:6px; padding:6px 12px; cursor:pointer; font-family:inherit; font-size:12px; text-decoration:none; }}
    .btn:hover {{ background:#2a3140; color:#d8e1ec; }}
    .btn.active {{ background:#0090ff; color:#0a0e16; border-color:#0090ff; }}
    code {{ background:#1f2738; padding:1px 5px; border-radius:3px; color:#23d18b; font-size:11px; }}
    .note {{ background:#1f2738; padding:14px 18px; border-radius:8px; margin-top:24px; font-size:12px; line-height:1.6; color:#9aa5b8; max-width:1100px; }}
    </style><!--SBNAVCSS--></head><body><!--SBNAV-->

    <h1>Weather Validate <span class="beta">BETA</span>
        <a href="/icons-preview">→ icons-preview</a>
        <a href="/wx-icons-preview">→ wx-icons-preview</a></h1>

    <p class="lede">Side-by-side comparison of every weather source feeding the kneeboard. The point: prove SkyBridge agrees with the FAA-authoritative sources where they exist, and show where it adds new value through calibrated MWOS observations and open-data model fill. This is the seed artifact for a future FAA accuracy-attestation submission.</p>

    <h2>Source Provenance <small>which upstreams are authoritative, which are calibrated private, which are open-data</small></h2>
    <div class="provenance">
      <div class="prov-chip faa-approved">
        <div class="top">🟢 NWS METAR</div>
        <div class="age">{age_str(metar_age)}</div>
        <div class="tag">✅ FAA-approved observation</div>
      </div>
      <div class="prov-chip faa-approved">
        <div class="top">🟢 NWS Gridpoint</div>
        <div class="age">{age_str(nws_age)}</div>
        <div class="tag">✅ FAA-approved forecast model</div>
      </div>
      <div class="prov-chip calibrated">
        <div class="top">🟡 MWOS</div>
        <div class="age">{age_str(mwos_age)}</div>
        <div class="tag">⚙️ Montis-calibrated, private</div>
      </div>
      <div class="prov-chip open">
        <div class="top">⚪ Open-Meteo</div>
        <div class="age">{age_str(om_age)}</div>
        <div class="tag">⚙️ Open-data model backfill</div>
      </div>
      <div class="prov-chip composite">
        <div class="top">🟣 SkyBridge IDW</div>
        <div class="age">realtime</div>
        <div class="tag">⚙️ Composite (METAR + MWOS + model)</div>
      </div>
    </div>

    <h2>Time Travel <small>replay any snapshot from the last 30 days · DB logs every 10 min</small></h2>
    <div class="picker">
      <span style="color:#9aa5b8">{"📡 LIVE — now" if historical is None else f"⏪ Historical: showing snapshot at ts={ts_q}"}</span>
      <a class="btn{' active' if historical is None else ''}" href="/wx-validate">📡 Live</a>
      <span class="picker-controls">
        <input type="datetime-local" id="tsPicker" />
        <button class="btn" onclick="(function(){{var v=document.getElementById('tsPicker').value;if(!v)return;var t=Math.floor(new Date(v).getTime()/1000);location.href='/wx-validate?ts='+t;}})()">Go</button>
        <button class="btn" onclick="fetch('/api/wx-validate/snapshot-now').then(r=>r.json()).then(d=>{{alert('Wrote '+d.rows_written+' rows at ts='+d.ts);location.reload();}})">Snapshot now →</button>
        <a class="btn" href="/api/wx-validate/timeline" target="_blank">Timeline JSON</a>
      </span>
    </div>

    <h2>SkyBridge Composite Weights <small>weighted-mean ensemble of authoritative sources · not a published or attested value · tunable from observed historical agreement</small></h2>
    <div class="weights">
      <span class="wlabel">Weights →</span>
      {''.join(f'<span class="weight-pill"><span class="src-name">{_WX_CERT_LABEL.get(tag, tag)}</span><span class="src-w">×{w:.2f}</span></span>' for tag, w in _WX_CERT_WEIGHTS.items())}
    </div>

    <h2>Per-Anchor Side-by-Side <small>METAR is the baseline; deltas highlight where each source agrees / drifts</small></h2>
    <div class="legend">
      <span><span class="swatch" style="background:#1f2738"></span>baseline (METAR)</span>
      <span><span class="swatch" style="background:#23d18b"></span>OK</span>
      <span><span class="swatch" style="background:#ffbb00"></span>marginal</span>
      <span><span class="swatch" style="background:#ff5040"></span>drift</span>
      <span><span class="swatch" style="background:#445"></span>not reported</span>
    </div>
    <table class="cmp">
      <tr>
        <th class="anchor">Anchor</th>
        <th class="metric">Field</th>
        <th class="src">NWS METAR <span class="faa">FAA</span></th>
        <th class="src">MWOS <span class="cal">cal</span></th>
        <th class="src">NWS Grid <span class="faa">FAA</span></th>
        {''.join(f'<th class="src">{label} <span class="mdl" title="{note}">model</span></th>' for _om_id, _src_tag, label, note in _OM_MODELS)}
        <th class="src cert">SkyBridge <span class="cert-tag">COMPOSITE</span></th>
      </tr>
      {rows_html}
    </table>

    <h2>Aggregate Agreement vs METAR Baseline <small>mean absolute error · (signed bias) · n=samples</small></h2>
    <table class="summary">
      <tr><th>Source</th><th>Wind speed (kt)</th><th>Temp (°C)</th><th>Pressure (mb)</th></tr>
      {summary_rows}
    </table>

    <div class="note">
      <strong>How to read this:</strong> the highlighted cell in each row is the METAR baseline (FAA-authoritative observation). Each adjacent cell is a different source reporting <em>the same field at the same lat/lon at roughly the same time</em>. Color = how close the source is to the METAR.
      <ul style="margin:8px 0 0 18px;padding:0;">
        <li><strong>OK thresholds:</strong> wind ±3kt, temp ±1°C, vis ±1sm, pressure ±1mb, cloud ±20%</li>
        <li><strong>Marginal:</strong> wind ±7kt, temp ±3°C, vis ±3sm, pressure ±3mb, cloud ±40%</li>
        <li><strong>Drift</strong> (red): everything beyond marginal — sensor or model is meaningfully out of agreement</li>
      </ul>
      <p style="margin:14px 0 0"><strong>Roadmap toward FAA cert:</strong> add 24/7 historical logging of these deltas so we can produce a 90-day report card per station per field. That report card is the kind of document that goes into an FAA accuracy-attestation package. Architecture is already in place — same <code>/api/wx/grid</code> endpoint, same unified shape, plus a periodic snapshot writer.</p>
      <p><strong>Other anchors to consider:</strong> high-elevation MWOS (Anaktuvuk Pass, Thompson Pass) — when their station-pressure differs from sea-level pressure by >50mb, that's a calibration consistency check, not a drift. We can add an elevation-aware mode for those.</p>
    </div>
    <!--SBFOOTER--></body></html>''', "/public/wx-validate")


# ── /api/wx-lens — multi-source per-source grid for the shootout map ──────


@app.route("/public/wx-shootout")
def wx_shootout_public():
    """Multi-source weather visualization map. Each source renders as its
    own color layer (toggleable) over the same Leaflet base. Watch the
    models disagree spatially — GFS streamlines diverge from ECMWF
    streamlines in the gaps where neither has ground truth, and the
    SkyBridge Composite splits the difference.

    Per-source colors are baked in below — distinct + glanceable. Toggle
    each source on/off; multiple can stack so divergence is visible.
    """
    return _publicize('''<!doctype html><html><head><meta charset="utf-8"><title>Weather Shootout — SkyBridge Alaska</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  body { margin:0; background:#0a0e16; color:#d8e1ec; font-family:system-ui,sans-serif; overflow:hidden; }
  #wrap { position:fixed; inset:0; display:flex; flex-direction:column; }
  header { background:#0d121d; border-bottom:1px solid #2a3140; padding:10px 18px; display:flex; gap:14px; align-items:center; flex-wrap:wrap; }
  h1 { color:#23d18b; font-size:14px; letter-spacing:2px; text-transform:uppercase; margin:0; }
  h1 .dev { background:#ff8800; color:#000; padding:2px 6px; border-radius:3px; font-size:10px; margin-left:6px; }
  h1 a { color:#0090ff; font-size:10px; letter-spacing:1px; margin-left:10px; text-decoration:none; }
  .blurb { color:#9aa5b8; font-size:11px; }
  .src-bar { display:flex; gap:6px; flex-wrap:wrap; margin-left:auto; }
  .src-tog { display:flex; align-items:center; gap:6px; padding:6px 12px; border-radius:6px; cursor:pointer; user-select:none;
             border:1.5px solid; background:transparent; font-size:12px; font-weight:700; transition:all 0.15s; opacity:0.55; }
  .src-tog:hover { opacity:0.9; }
  .src-tog.on { opacity:1.0; }
  .src-tog .swatch { width:10px; height:10px; border-radius:2px; }
  #map { flex:1; background:#0a0e16; }
  .leaflet-container { background:#0a0e16; }
  /* Each source canvas overlays the same map */
  canvas.windCanvas { position:absolute; top:0; left:0; pointer-events:none; }
  /* Station pins */
  .pin-metar, .pin-mwos {
    width:12px; height:12px; border-radius:50%; border:2px solid;
    box-shadow:0 0 4px currentColor;
  }
  .pin-metar { background:#fff; border-color:#fff; color:#fff; }
  .pin-mwos  { background:#ffaa00; border-color:#ffaa00; color:#ffaa00; }
  .pin-label { position:absolute; top:14px; left:50%; transform:translateX(-50%); font-size:9px; color:currentColor; white-space:nowrap; text-shadow:0 0 2px #000; font-weight:700; pointer-events:none; }
  /* Status strip */
  .stat-strip { position:fixed; bottom:10px; left:10px; background:rgba(13,18,29,0.92); border:1px solid #2a3140; border-radius:8px; padding:10px 14px; font-size:11px; line-height:1.6; max-width:380px; }
  .stat-strip h3 { color:#cc88ff; font-size:10px; text-transform:uppercase; letter-spacing:1.5px; margin:0 0 4px; }
  .stat-strip .row { display:flex; justify-content:space-between; gap:10px; }
  .stat-strip .src-name { color:#d8e1ec; font-weight:700; }
  .stat-strip .src-cnt { color:#9aa5b8; }
</style>
<!--SBNAVCSS--></head><body><!--SBNAV-->
<div id="wrap">
  <header>
    <h1>AK Weather Shootout <span class="beta">BETA</span>
        <a href="/wx-validate">→ wx-validate</a>
        <a href="/icons-preview">→ icons-preview</a>
        <a href="/wx-icons-preview">→ wx-icons-preview</a></h1>
    <span class="blurb">Each source = its own color layer. Toggle to compare. Click multiple to see disagreement spatially.</span>
    <div class="src-bar" id="srcBar"></div>
  </header>
  <div id="map"></div>
</div>
<div class="stat-strip" id="statStrip">
  <h3>Loading sources...</h3>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
// ── Per-source config ──────────────────────────────────────────────────────
// Each source has its own color, friendly label, default-on state, kind.
const SOURCES = [
  { tag:'metar',    label:'METAR · obs',     color:'#ffffff', on:true,  kind:'station' },
  { tag:'mwos',     label:'MWOS · obs',      color:'#ff9933', on:true,  kind:'station' },
  { tag:'om_gfs',   label:'NOAA GFS',        color:'#3399ff', on:false, kind:'grid' },
  { tag:'om_gem',   label:'GEM (Canada)',    color:'#cc4444', on:false, kind:'grid' },
  { tag:'om_ecmwf', label:'ECMWF (Europe)',  color:'#33cc66', on:false, kind:'grid' },
  { tag:'om_jma',   label:'JMA (Japan)',     color:'#ff66cc', on:false, kind:'grid' },
  { tag:'certified',label:'★ SkyBridge Composite', color:'#cc88ff', on:false, kind:'grid' },
];

// ── Map ─────────────────────────────────────────────────────────────────────
const map = L.map('map', { zoomControl:true, attributionControl:false }).setView([61.186, -150.039], 7);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
  maxZoom:14, opacity:0.55,
}).addTo(map);

// ── Per-source render state ────────────────────────────────────────────────
const SRC_DATA = {};                // tag → {points: [...]}
const SRC_RENDERER = {};             // tag → renderer object
let DATA_LOADED_AT = 0;

// ── Wind streamline canvas (per source) + grid-point pin layer ────────────
// `radius_nm` controls how far a point's wind influences nearby particles.
// Models (full grid): 350nm — generous, fills the whole viewport.
// Stations (sparse obs): 1nm — tight bubble around each station, since a
// surface obs only legitimately speaks for its immediate vicinity.
// `bubble_mode` (true for stations): particles spawn near a randomly-chosen
// data point, stay within radius, recycle when they leave. Result: small
// "wind sock" effect at each station.
function makeWindRenderer(src) {
  const pane = map.createPane('wp_'+src.tag);
  pane.style.zIndex = 410;
  pane.style.pointerEvents = 'none';
  const canvas = document.createElement('canvas');
  canvas.className = 'windCanvas';
  pane.appendChild(canvas);
  const ctx = canvas.getContext('2d');

  // Grid-point pins: one circle marker per data point in the source's color.
  // Subtle (small radius, semi-transparent) so they reveal the sampling
  // grid without overpowering the streamlines.
  const pinLayer = L.layerGroup();
  let particles = [];
  const isBubble    = src.kind === 'station';
  const RADIUS_NM   = isBubble ? 3.0 : 350;
  const PARTICLE_COUNT = isBubble ? 60 : 90;
  const TRAIL_LEN = 5;
  const LIFESPAN = 90;

  const fit = () => {
    const m = document.getElementById('map');
    canvas.width = m.clientWidth || 1;
    canvas.height = m.clientHeight || 1;
  };
  fit();
  map.on('resize', fit);

  function spawn() {
    const data = SRC_DATA[src.tag] || [];
    if (isBubble) {
      // Pick a random reporting station (must have wind data) and spawn within
      // RADIUS_NM of it. Returns null if no usable stations — caller handles.
      const usable = data.filter(p =>
        p.dir_deg != null && p.dir_deg >= 0 &&
        p.speed_kt != null && p.speed_kt > 0);
      if (usable.length === 0) return null;
      const seed = usable[Math.floor(Math.random() * usable.length)];
      const r = Math.sqrt(Math.random()) * RADIUS_NM;     // uniform-area
      const theta = Math.random() * 2 * Math.PI;
      const dLat = r * Math.cos(theta) / 60.0;
      const dLon = r * Math.sin(theta) / (60.0 * Math.cos(seed.lat * Math.PI/180));
      return { lat: seed.lat + dLat, lon: seed.lon + dLon,
               age: Math.floor(Math.random() * LIFESPAN),
               seed_lat: seed.lat, seed_lon: seed.lon, trail: [] };
    } else {
      const b = map.getBounds();
      return {
        lat: b.getSouth() + Math.random() * (b.getNorth() - b.getSouth()),
        lon: b.getWest() + Math.random() * (b.getEast() - b.getWest()),
        age: Math.floor(Math.random() * LIFESPAN),
        trail: [],
      };
    }
  }

  function windAt(lat, lon) {
    const data = SRC_DATA[src.tag] || [];
    if (data.length === 0) return null;
    let u=0, v=0, w=0;
    for (const p of data) {
      if (p.dir_deg == null || p.speed_kt == null || p.dir_deg < 0 || p.speed_kt <= 0) continue;
      const d = gcDistNm([lat, lon], [p.lat, p.lon]);
      if (d > RADIUS_NM) continue;
      const weight = 1 / (d*d + 0.5);
      const rad = p.dir_deg * Math.PI/180;
      u += weight * (-p.speed_kt * Math.sin(rad));
      v += weight * (-p.speed_kt * Math.cos(rad));
      w += weight;
    }
    if (w === 0) return null;
    u /= w; v /= w;
    const speed = Math.sqrt(u*u + v*v);
    let dir = Math.atan2(-u, -v) * 180/Math.PI;
    if (dir < 0) dir += 360;
    return { dir_deg: dir, speed_kt: speed };
  }

  let alive = false;
  let raf = null;

  function frame() {
    if (!alive) return;
    try {
      const tl = map.containerPointToLayerPoint([0, 0]);
      L.DomUtil.setPosition(canvas, tl);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      const z = map.getZoom();
      const zoomScale = Math.pow(1.45, Math.max(0, z - 9));
      const activeCount = Math.max(20, Math.floor(particles.length * Math.pow(0.78, Math.max(0, z - 9))));
      for (let i = 0; i < activeCount; i++) {
        let p = particles[i];
        if (!p || p.age >= LIFESPAN) {
          p = particles[i] = spawn();
          if (!p) continue;
          p.age = 0;
        }
        const wd = windAt(p.lat, p.lon);
        if (!wd) { p.age = LIFESPAN; continue; }
        let stepMag = (wd.speed_kt * 7.7e-6 * 80) / zoomScale;
        const rad = wd.dir_deg * Math.PI/180;
        let dLat = -stepMag * Math.cos(rad);
        let dLon = -stepMag * Math.sin(rad) / Math.cos(p.lat * Math.PI/180);
        const here = map.latLngToContainerPoint({lat: p.lat, lng: p.lon});
        const next = map.latLngToContainerPoint({lat: p.lat + dLat, lng: p.lon + dLon});
        const px = Math.hypot(next.x - here.x, next.y - here.y);
        if (px > 4 && px > 0) { const k = 4/px; dLat *= k; dLon *= k; }
        p.lat += dLat; p.lon += dLon; p.age++;
        p.trail.push([p.lat, p.lon]);
        if (p.trail.length > TRAIL_LEN) p.trail.shift();
        if (p.trail.length >= 2) {
          const pts = [];
          for (let j = 0; j < p.trail.length; j++) {
            try { pts.push(map.latLngToContainerPoint({lat: p.trail[j][0], lng: p.trail[j][1]})); } catch(e){}
          }
          if (pts.length < 2) continue;
          for (let j = 1; j < pts.length; j++) {
            const t = j / (pts.length - 1);
            const a = 0.20 + 0.70 * t;
            ctx.strokeStyle = src.color + Math.round(a*255).toString(16).padStart(2,'0');
            ctx.lineWidth = 0.9 + 0.7 * t;
            ctx.beginPath();
            ctx.moveTo(pts[j-1].x, pts[j-1].y);
            ctx.lineTo(pts[j].x, pts[j].y);
            ctx.stroke();
          }
        }
      }
    } catch(err) { console.warn('[shootout '+src.tag+'] frame err:', err); }
    if (alive) raf = requestAnimationFrame(frame);
  }

  function rebuildPins() {
    pinLayer.clearLayers();
    // Station sources already get styled pins from makeStationRenderer; don't
    // duplicate. Only render grid-point dots for model/composite sources.
    if (isBubble) return;
    const data = SRC_DATA[src.tag] || [];
    for (const p of data) {
      if (p.lat == null || p.lon == null) continue;
      const dirStr = (p.dir_deg == null) ? '?' :
                     (p.dir_deg < 0 ? 'VRB' : Math.round(p.dir_deg) + '°');
      const spdStr = (p.speed_kt != null) ? p.speed_kt.toFixed(1) + 'kt' : '—';
      const popup = `<b style="color:${src.color}">${src.label}</b><br>
        ${p.lat.toFixed(3)}, ${p.lon.toFixed(3)}<br>
        wind: ${dirStr} / ${spdStr}<br>
        ${p.temp_c!=null?'temp: '+p.temp_c+' °C<br>':''}
        ${p.pressure_mb!=null?'press: '+p.pressure_mb+' mb<br>':''}
        ${p.cloud_pct!=null?'cloud: '+p.cloud_pct+'%<br>':''}
        ${p.visibility_sm!=null?'vis: '+p.visibility_sm+' sm':''}`;
      L.circleMarker([p.lat, p.lon], {
        radius: 3,
        color: src.color,
        weight: 1,
        fillColor: src.color,
        fillOpacity: 0.55,
        opacity: 0.85,
      }).bindPopup(popup).addTo(pinLayer);
    }
  }

  return {
    start() {
      if (alive) return;
      pane.style.display = '';
      rebuildPins();
      pinLayer.addTo(map);
      particles = [];
      for (let i = 0; i < PARTICLE_COUNT; i++) particles.push(spawn());
      alive = true;
      frame();
    },
    stop() {
      alive = false;
      if (raf) cancelAnimationFrame(raf);
      pane.style.display = 'none';
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      map.removeLayer(pinLayer);
    },
    refresh() { if (alive) rebuildPins(); },
    visible() { return alive; },
    color: src.color,
  };
}

// ── Station pin layer (METAR/MWOS) ─────────────────────────────────────────
function makeStationRenderer(src) {
  const layer = L.layerGroup();
  return {
    start() {
      layer.clearLayers();
      const pts = SRC_DATA[src.tag] || [];
      for (const p of pts) {
        if (p.lat == null || p.lon == null) continue;
        const html = `<div class="pin-${src.tag}">
          <div class="pin-label">${(p.source||'').split(':').pop().slice(0,12)}</div>
        </div>`;
        L.marker([p.lat, p.lon], {
          icon: L.divIcon({ html, className:'', iconSize:[12,12], iconAnchor:[6,6] }),
        }).bindPopup(`<b>${p.source}</b><br>
          ${p.dir_deg!=null?'wind: '+(p.dir_deg<0?'VRB':p.dir_deg.toFixed(0)+'°')+' / '+p.speed_kt+'kt':''}<br>
          ${p.temp_c!=null?'temp: '+p.temp_c+' °C':''}<br>
          ${p.pressure_mb!=null?'press: '+p.pressure_mb+' mb':''}<br>
          ${p.visibility_sm!=null?'vis: '+p.visibility_sm+' sm':''}`).addTo(layer);
      }
      layer.addTo(map);
    },
    stop() { map.removeLayer(layer); },
    visible() { return map.hasLayer(layer); },
    color: src.color,
  };
}

// ── Bar build + toggle ─────────────────────────────────────────────────────
const bar = document.getElementById('srcBar');
SOURCES.forEach(src => {
  const btn = document.createElement('button');
  btn.className = 'src-tog' + (src.on ? ' on' : '');
  btn.style.borderColor = src.color;
  btn.style.color = src.color;
  btn.innerHTML = `<span class="swatch" style="background:${src.color}"></span><span>${src.label}</span>`;
  btn.onclick = () => {
    btn.classList.toggle('on');
    src.on = btn.classList.contains('on');
    if (src.on) SRC_RENDERER[src.tag].start();
    else        SRC_RENDERER[src.tag].stop();
  };
  bar.appendChild(btn);
});

// ── Distance helper (great-circle nm) ──────────────────────────────────────
function gcDistNm(a, b) {
  const R = 3440.065;
  const lat1 = a[0]*Math.PI/180, lat2 = b[0]*Math.PI/180;
  const dLat = (b[0]-a[0])*Math.PI/180;
  const dLon = (b[1]-a[1])*Math.PI/180;
  const x = Math.sin(dLat/2)**2 + Math.cos(lat1)*Math.cos(lat2)*Math.sin(dLon/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1-x));
}

// ── Data loader + scoreboard ───────────────────────────────────────────────
async function loadData() {
  const r = await fetch('/api/wx-lens');
  const d = await r.json();
  DATA_LOADED_AT = Date.now();
  // Build SRC_DATA + counts for the strip
  const counts = {};
  for (const src of SOURCES) {
    SRC_DATA[src.tag] = d.sources[src.tag] || [];
    counts[src.tag] = SRC_DATA[src.tag].length;
  }
  // Build renderer for each source on first load.
  // Station sources get a COMPOUND renderer: both the styled pin layer
  // (white/orange divIcons) and the bubble-mode wind streamlines (3nm radius
  // around each station). Grid sources just get the wind+grid-pin renderer.
  for (const src of SOURCES) {
    const firstBuild = !SRC_RENDERER[src.tag];
    if (firstBuild) {
      if (src.kind === 'station') {
        // Compound: both the pin layer and the bubble-wind layer
        const pinR = makeStationRenderer(src);
        const windR = makeWindRenderer(src);
        SRC_RENDERER[src.tag] = {
          start()   { pinR.start(); windR.start(); },
          stop()    { pinR.stop();  windR.stop(); },
          refresh() { if (pinR.refresh) pinR.refresh(); else { pinR.stop(); pinR.start(); }
                      if (windR.refresh) windR.refresh(); },
          color: src.color,
        };
      } else {
        SRC_RENDERER[src.tag] = makeWindRenderer(src);
      }
    }
    // On refresh: if currently on, rebuild pins from new data; otherwise stop+restart
    if (src.on) {
      if (!firstBuild && SRC_RENDERER[src.tag].refresh) {
        SRC_RENDERER[src.tag].refresh();
      } else {
        SRC_RENDERER[src.tag].stop();
        SRC_RENDERER[src.tag].start();
      }
    } else {
      SRC_RENDERER[src.tag].stop();
    }
  }
  // Stat strip: per-source point counts + weight badge
  const strip = document.getElementById('statStrip');
  let html = '<h3>Sources loaded</h3>';
  for (const src of SOURCES) {
    const w = (d.weights||{})[src.tag];
    const wstr = (w !== undefined) ? ` <span style="color:#ffd24c">×${w.toFixed(2)}</span>` : '';
    html += `<div class="row">
      <span class="src-name" style="color:${src.color}">${src.label}</span>
      <span class="src-cnt">${counts[src.tag]||0} pts${wstr}</span>
    </div>`;
  }
  html += `<div class="row" style="margin-top:6px;color:#7a8497;font-size:10px">
    Refreshes every 5min · Local grid radius: ${d.radius_nm}nm</div>`;
  strip.innerHTML = html;
}
loadData();
setInterval(loadData, 5*60*1000);
</script>
<!--SBFOOTER--></body></html>''', "/public/wx-shootout")




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
    # Default station list — broad AK coverage so /wx-shootout has METAR
    # density to compare against MWOS + the four models. Caller can override
    # with ?stations= for narrower views.
    stations = request.args.get("stations",
        "PANC,PALH,PAMR,PAED,PAAQ,PAFA,PAJN,PADQ,PAEN,PABE,PAOM,PABR,"
        "PAKN,PAVD,PAEI,PAKT,PAOT,PAGA,PAUN,PAHO,PAWG,PAIL")
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


@app.route("/api/health")
def api_health():
    """Comms-degraded indicator. Aggregates the health of every feed the
    kneeboard depends on so the client can render a status strip.
    Each entry: {name, status: ok|partial|down, age_s, detail}.
    """
    now = time.time()
    feeds = []

    # WX (aviationweather.gov) — lean on METAR_CACHE
    if METAR_CACHE.get("data"):
        m_age = now - METAR_CACHE.get("ts", 0)
        m_up = METAR_CACHE["data"].get("upstream", "ok")
        feeds.append({
            "name": "WX",
            "status": "ok" if (m_up == "ok" and m_age < 600) else
                      "partial" if (m_up == "partial" or m_age < 1800) else "down",
            "age_s": int(m_age),
            "detail": f"aviationweather.gov · {m_up}",
        })
    else:
        feeds.append({"name": "WX", "status": "down", "age_s": 999999, "detail": "no data yet"})

    # ADS-B — based on _AIRCRAFT_BY_HEX recency + ratio of fresh vs stale
    ac_count = len(_AIRCRAFT_BY_HEX)
    fresh = sum(1 for a in _AIRCRAFT_BY_HEX.values() if a.get("stale_sec", 0) == 0)
    if ac_count == 0:
        feeds.append({"name": "ADSB", "status": "down", "age_s": 999999, "detail": "no aircraft"})
    else:
        ratio = fresh / ac_count
        feeds.append({
            "name": "ADSB",
            "status": "ok" if ratio > 0.5 else "partial" if ratio > 0.1 else "down",
            "age_s": 0,
            "detail": f"{ac_count} aircraft, {fresh} fresh",
        })

    # MWOS (Montis Corp) — pull cache age
    mwos_entry = _WX_CACHE.get("mwos_133", {})
    if mwos_entry:
        mwos_age = int(now - mwos_entry.get("ts", 0))
        feeds.append({
            "name": "MWOS",
            "status": "ok" if mwos_age < 600 else "partial" if mwos_age < 3600 else "down",
            "age_s": mwos_age,
            "detail": "Montis Corp",
        })
    else:
        feeds.append({"name": "MWOS", "status": "partial", "age_s": -1, "detail": "no cache yet"})

    # VHF pipeline — check systemd state
    try:
        out = subprocess.run(["systemctl", "is-active", "vhf-pipeline"],
                             capture_output=True, text=True, timeout=2).stdout.strip()
        feeds.append({
            "name": "VHF",
            "status": "ok" if out == "active" else "down",
            "age_s": 0,
            "detail": f"vhf-pipeline.service: {out}",
        })
    except Exception:
        feeds.append({"name": "VHF", "status": "down", "age_s": 0, "detail": "check failed"})

    # OpenClaw / Blaze gateway
    try:
        out = subprocess.run(["systemctl", "is-active", "openclaw-gateway"],
                             capture_output=True, text=True, timeout=2).stdout.strip()
        feeds.append({
            "name": "BLAZE",
            "status": "ok" if out == "active" else "down",
            "age_s": 0,
            "detail": f"openclaw-gateway: {out}",
        })
    except Exception:
        feeds.append({"name": "BLAZE", "status": "down", "age_s": 0, "detail": "check failed"})

    # Mesh (Tailscale)
    try:
        out = subprocess.run(["tailscale", "status", "--peers=false"],
                             capture_output=True, text=True, timeout=2).stdout.strip()
        is_up = bool(out and "100." in out.split()[0] if out else False)
        feeds.append({
            "name": "MESH",
            "status": "ok" if is_up else "down",
            "age_s": 0,
            "detail": "tailscale " + ("up" if is_up else "down"),
        })
    except Exception:
        feeds.append({"name": "MESH", "status": "partial", "age_s": 0, "detail": "no tailscale CLI"})

    return jsonify({"ts": int(now), "feeds": feeds})


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


@app.route("/api/wx/field")
def api_wx_field():
    """Aggregate scalar weather observations (temp, dewpoint, pressure) from
    METARs + MWOS into a single point list for the heatmap canvas.
    Returns: {points: [{lat, lon, temp_c, dew_c, alt_inhg, source, ts}, ...]}
    """
    points = []

    # METARs — parse "08/M04" temp/dewpoint and "A2995" altimeter
    for stn, raw in (METAR_CACHE.get("data") or {}).get("metars", {}).items():
        if not raw or raw == "(unavailable)":
            continue
        ll = _STATION_LL.get(stn)
        if not ll:
            continue
        td_match = re.search(r"\b(M?\d{1,2})/(M?\d{1,2})\b", raw)
        a_match = re.search(r"\bA(\d{4})\b", raw)
        temp_c = dew_c = None
        if td_match:
            try:
                temp_c = -int(td_match.group(1)[1:]) if td_match.group(1).startswith("M") else int(td_match.group(1))
                dew_c = -int(td_match.group(2)[1:]) if td_match.group(2).startswith("M") else int(td_match.group(2))
            except ValueError:
                pass
        alt_inhg = None
        if a_match:
            try:
                alt_inhg = int(a_match.group(1)) / 100.0
            except ValueError:
                pass
        if temp_c is None and alt_inhg is None:
            continue
        points.append({
            "lat": ll[0], "lon": ll[1],
            "temp_c": temp_c, "dew_c": dew_c, "alt_inhg": alt_inhg,
            "source": "metar:" + stn,
        })

    # MWOS observations
    for hx, entry in _WX_CACHE.items():
        if not hx.startswith("mwos_"):
            continue
        d = entry.get("data") or {}
        obs_list = d.get("observations", []) or []
        if not obs_list:
            continue
        latest = obs_list[0]
        t = latest.get("tempC")
        if t is None:
            continue
        points.append({
            "lat": d.get("latitude", 0), "lon": d.get("longitude", 0),
            "temp_c": float(t),
            "dew_c": float(latest.get("dewpointC")) if latest.get("dewpointC") is not None else None,
            "alt_inhg": float(latest.get("altimeterInHg")) if latest.get("altimeterInHg") is not None else None,
            "source": "mwos:" + str(d.get("siteName", hx)),
        })

    return jsonify({
        "points": points,
        "count": len(points),
        "ts": int(time.time()),
    })


# ── Open-Meteo model-grid fetch (HRRR/GFS-equivalent fill) ────────────────
# Mesh-philosophy: each node only fetches model data for ITS local area
# (radius around _DIST_ANCHOR). Far-away weather is meant to arrive over the
# mesh from neighbouring nodes that own those areas. Default radius is set to
# cover the typical operating viewport for this node — Anchorage Bowl + Mat-Su
# + upper Kenai + half of Prince William Sound from PALH.
_OPENMETEO_TTL = 1800             # 30 min — model only updates hourly
_LOCAL_MODEL_RADIUS_NM = 200      # how far around the node we own
_LOCAL_MODEL_GRID_STEP_NM = 40    # spacing between sampled points

def _build_local_model_grid(anchor, radius_nm, step_nm):
    """Return a list of (lat, lon) points evenly spaced around `anchor`,
    inside a circle of `radius_nm`. step_nm controls density. ~80-100 points
    at the defaults — enough for visual streamline fill, light enough to fit
    in one Open-Meteo HTTP call and one LoRa frame group."""
    a_lat, a_lon = anchor
    # 1° lat ≈ 60 nm; 1° lon shrinks with cos(lat)
    dlat = step_nm / 60.0
    dlon = step_nm / (60.0 * max(0.1, math.cos(math.radians(a_lat))))
    n = int(radius_nm / step_nm) + 1
    pts = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            lat = a_lat + i * dlat
            lon = a_lon + j * dlon
            # keep points inside the circle, not the bounding square
            if math.hypot(i * step_nm, j * step_nm) <= radius_nm:
                pts.append((round(lat, 4), round(lon, 4)))
    return pts

_AK_WIND_GRID = _build_local_model_grid(_DIST_ANCHOR, _LOCAL_MODEL_RADIUS_NM, _LOCAL_MODEL_GRID_STEP_NM)

def _fetch_model_grid():
    """Returns per-point model fields from Open-Meteo across the local AK grid.
    One HTTP call, six families of data. Cached for _OPENMETEO_TTL; falls back
    to last good fetch on transient upstream failure.

    Per point: lat, lon, source, ts, plus:
      wind:       dir_deg, speed_kt, gust_kt
      thermo:     temp_c, freezing_level_ft
      sky:        cloud_pct, cloud_low_pct, cloud_mid_pct, cloud_high_pct
      vis/wx:     visibility_sm, precip_mm
      pressure:   pressure_mb

    The wind subset stays compatible with the previous _fetch_model_winds()
    return shape so /api/wind keeps working unchanged.
    """
    import time
    now = time.time()
    cached = _WX_CACHE.get("openmeteo_grid")
    if cached and (now - cached["ts"]) < _OPENMETEO_TTL:
        return cached["data"]
    lats = ",".join(f"{p[0]}" for p in _AK_WIND_GRID)
    lons = ",".join(f"{p[1]}" for p in _AK_WIND_GRID)
    # `current` carries the right-now values; `hourly=freezing_level_height` is
    # needed because Open-Meteo doesn't expose freezing level in the `current`
    # block. We grab the [0] hour entry per point.
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lons}"
        "&current=wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
        "temperature_2m,cloud_cover,cloud_cover_low,cloud_cover_mid,"
        "cloud_cover_high,visibility,precipitation,pressure_msl"
        "&hourly=freezing_level_height"
        "&forecast_hours=1"
        "&wind_speed_unit=kn&timezone=UTC"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SkyBridge/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = json.loads(resp.read())
        forecasts = raw if isinstance(raw, list) else [raw]
        out = []
        for i, fc in enumerate(forecasts):
            if i >= len(_AK_WIND_GRID):
                break
            cur = (fc or {}).get("current") or {}
            hourly = (fc or {}).get("hourly") or {}
            fz_arr = hourly.get("freezing_level_height") or []
            spd = cur.get("wind_speed_10m")
            dr = cur.get("wind_direction_10m")
            if spd is None or dr is None:
                continue
            lat, lon = _AK_WIND_GRID[i]
            # Visibility comes back in meters; convert to sm. Cap at 10 sm to
            # match aviation reporting convention (Open-Meteo can return e.g.
            # 24140 m in clear conditions).
            vis_m = cur.get("visibility")
            vis_sm = round(min(vis_m / 1609.34, 10.0), 1) if isinstance(vis_m, (int, float)) else None
            fz_m = fz_arr[0] if fz_arr else None
            fz_ft = round(fz_m * 3.281) if isinstance(fz_m, (int, float)) else None
            out.append({
                "lat": lat, "lon": lon,
                "source": "model:openmeteo",
                "ts": cur.get("time", ""),
                # wind
                "dir_deg": float(dr),
                "speed_kt": float(spd),
                "gust_kt": float(cur.get("wind_gusts_10m") or 0),
                # thermo
                "temp_c": cur.get("temperature_2m"),
                "freezing_level_ft": fz_ft,
                # sky
                "cloud_pct": cur.get("cloud_cover"),
                "cloud_low_pct": cur.get("cloud_cover_low"),
                "cloud_mid_pct": cur.get("cloud_cover_mid"),
                "cloud_high_pct": cur.get("cloud_cover_high"),
                # vis / wx
                "visibility_sm": vis_sm,
                "precip_mm": cur.get("precipitation"),
                # pressure
                "pressure_mb": cur.get("pressure_msl"),
            })
        _WX_CACHE["openmeteo_grid"] = {"data": out, "ts": now}
        return out
    except Exception:
        return (cached or {}).get("data", [])


# Backwards-compat alias — /api/wind still calls this name and only reads the
# wind subset. Will remove once /api/wind is updated to call _fetch_model_grid
# directly.
_fetch_model_winds = _fetch_model_grid


# ── NWS Gridded Forecast (api.weather.gov) ────────────────────────────────
# FAA-approved authoritative source. Two-step API:
#   1. /points/{lat,lon} → resolves to forecastGridData URL (per-(lat,lon),
#      stable forever — cache aggressively)
#   2. /gridpoints/{office}/{x},{y} → returns the actual hourly forecast,
#      refresh ~hourly when the next model run lands
_NWS_TTL = 1800   # 30 min — gridded forecast updates hourly

def _fetch_nws_gridpoint(lat, lon):
    """Resolves lat/lon to NWS gridpoint and returns its current-hour values
    in our unified shape. Returns None if the API path 404s (off-CONUS) or
    something else fails."""
    import time
    now = time.time()
    cache_k = f"nws_grid_{lat:.4f}_{lon:.4f}"
    cached = _WX_CACHE.get(cache_k)
    if cached and (now - cached["ts"]) < _NWS_TTL:
        return cached["data"]
    hdrs = {"User-Agent": "SkyBridge/1.0 (steven.fett@alaska.gov)",
            "Accept": "application/geo+json"}
    try:
        # Step 1: resolve to gridpoint URL — cache the URL forever once we have it
        url_k = f"nws_url_{lat:.4f}_{lon:.4f}"
        if url_k in _WX_CACHE:
            grid_url = _WX_CACHE[url_k]["data"]
        else:
            req = urllib.request.Request(
                f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}", headers=hdrs)
            with urllib.request.urlopen(req, timeout=10) as resp:
                pts = json.loads(resp.read())
            grid_url = (pts.get("properties") or {}).get("forecastGridData")
            if not grid_url:
                return None
            _WX_CACHE[url_k] = {"data": grid_url, "ts": now}
        # Step 2: fetch the actual gridpoint forecast
        req = urllib.request.Request(grid_url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=10) as resp:
            grid = json.loads(resp.read())
        prop = grid.get("properties") or {}
        # NWS returns {windSpeed: {uom, values:[{validTime, value}]}, ...}
        # We grab the [0] (current-hour) value and convert units.
        def first(name):
            f = prop.get(name) or {}
            vs = f.get("values") or []
            return (vs[0].get("value") if vs else None), f.get("uom", "")
        ws_val, ws_uom = first("windSpeed")
        spd_kt = None
        if isinstance(ws_val, (int, float)):
            # NWS returns km/h — convert to kt
            spd_kt = round(ws_val * 0.539957, 1) if "km_h" in ws_uom else round(ws_val, 1)
        wd_val, _ = first("windDirection")
        wg_val, wg_uom = first("windGust")
        gust_kt = None
        if isinstance(wg_val, (int, float)):
            gust_kt = round(wg_val * 0.539957, 1) if "km_h" in wg_uom else round(wg_val, 1)
        t_val, _ = first("temperature")
        sky_val, _ = first("skyCover")
        precip_val, _ = first("quantitativePrecipitation")
        out = {
            "lat": lat, "lon": lon,
            "source": "nws:gridpoint",
            "ts": prop.get("updateTime", ""),
            "dir_deg": int(wd_val) if isinstance(wd_val, (int, float)) else None,
            "speed_kt": spd_kt,
            "gust_kt": gust_kt,
            "temp_c": round(t_val, 1) if isinstance(t_val, (int, float)) else None,
            "freezing_level_ft": None,
            "cloud_pct": int(sky_val) if isinstance(sky_val, (int, float)) else None,
            "cloud_low_pct": None, "cloud_mid_pct": None, "cloud_high_pct": None,
            "visibility_sm": None,    # gridpoint doesn't expose vis for AK
            "precip_mm": round(precip_val, 2) if isinstance(precip_val, (int, float)) else None,
            "pressure_mb": None,      # gridpoint doesn't expose surface pressure
        }
        _WX_CACHE[cache_k] = {"data": out, "ts": now}
        return out
    except Exception:
        return (cached or {}).get("data")


# Models we pull via Open-Meteo for the cert dashboard. Each (open-meteo id,
# our internal source tag, friendly label, agency note). All four cover AK.
# Adding more is one-line — just append below.
_OM_MODELS = [
    ("gfs_seamless",  "om_gfs",   "NOAA GFS",   "USA / NCEP — global 25km blended w/ regional"),
    ("gem_seamless",  "om_gem",   "GEM",        "Canada / CMC — Environment Canada"),
    ("ecmwf_ifs025",  "om_ecmwf", "ECMWF",      "Europe — high-skill global"),
    ("jma_seamless",  "om_jma",   "JMA",        "Japan Met Agency — global"),
]


def _fetch_anchor_openmeteo(anchors):
    """Multi-model batched Open-Meteo call for the cert anchor list. Returns
    list-of-list: out[anchor_idx][model_idx] is one unified-shape point.
    All models in one HTTP call (cheap; same payload regardless of count).
    Cached separately from the local-grid call so /wx-validate doesn't
    perturb that cache."""
    import time
    now = time.time()
    cached = _WX_CACHE.get("openmeteo_anchors_multi")
    if cached and (now - cached["ts"]) < _OPENMETEO_TTL:
        return cached["data"]
    if not anchors:
        return []
    lats = ",".join(f"{a[0]}" for a in anchors)
    lons = ",".join(f"{a[1]}" for a in anchors)
    models_param = ",".join(m[0] for m in _OM_MODELS)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lons}"
        "&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
        "temperature_2m,cloud_cover,visibility,precipitation,pressure_msl"
        f"&models={models_param}&forecast_hours=1"
        "&wind_speed_unit=kn&timezone=UTC"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SkyBridge/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        forecasts = raw if isinstance(raw, list) else [raw]
        out = []
        for i, fc in enumerate(forecasts):
            if i >= len(anchors):
                break
            hourly = (fc or {}).get("hourly") or {}
            lat, lon = anchors[i]
            anchor_models = []
            for om_id, src_tag, label, _note in _OM_MODELS:
                def hv(name):
                    arr = hourly.get(f"{name}_{om_id}") or []
                    return arr[0] if arr else None
                spd = hv("wind_speed_10m")
                dr  = hv("wind_direction_10m")
                # Skip model entirely if no AK coverage at this point
                if spd is None and dr is None:
                    anchor_models.append(None)
                    continue
                vis_m = hv("visibility")
                vis_sm = round(min(vis_m / 1609.34, 10.0), 1) if isinstance(vis_m, (int, float)) else None
                anchor_models.append({
                    "lat": lat, "lon": lon,
                    "source": "model:" + src_tag,
                    "label": label,
                    "ts": (hourly.get("time") or [""])[0],
                    "dir_deg": dr,
                    "speed_kt": spd,
                    "gust_kt": hv("wind_gusts_10m") or 0,
                    "temp_c": hv("temperature_2m"),
                    "freezing_level_ft": None,
                    "cloud_pct": hv("cloud_cover"),
                    "cloud_low_pct": None, "cloud_mid_pct": None, "cloud_high_pct": None,
                    "visibility_sm": vis_sm,
                    "precip_mm": hv("precipitation"),
                    "pressure_mb": hv("pressure_msl"),
                })
            out.append(anchor_models)
        _WX_CACHE["openmeteo_anchors_multi"] = {"data": out, "ts": now}
        return out
    except Exception:
        return (cached or {}).get("data", [])


# ── Unified per-point shape ───────────────────────────────────────────────
# Every weather datum the kneeboard cares about — model OR observation —
# normalizes to this shape. Missing fields are None. The tag in `source`
# distinguishes "model:openmeteo" vs "metar:PALH" vs "mwos:Lake Hood MWOS"
# so the client can up-weight obs in IDW and an "obs only" toggle filters
# everything model-tagged out.

# METAR cloud abbreviations → coverage % (using midpoint of the oktas range)
_METAR_CLOUD_PCT = {"SKC": 0, "CLR": 0, "NSC": 0, "FEW": 13, "SCT": 38, "BKN": 75, "OVC": 100}

def _parse_metar_to_point(stn, raw, ts):
    """Pull the unified-shape fields out of a raw METAR string.
    Returns dict or None if the station has no lat/lon mapping."""
    ll = _STATION_LL.get(stn)
    if not ll:
        return None
    p = {
        "lat": ll[0], "lon": ll[1],
        "source": "metar:" + stn,
        "ts": ts or "",
        "dir_deg": None, "speed_kt": None, "gust_kt": None,
        "temp_c": None, "freezing_level_ft": None,
        "cloud_pct": None, "cloud_low_pct": None,
        "cloud_mid_pct": None, "cloud_high_pct": None,
        "visibility_sm": None, "precip_mm": None,
        "pressure_mb": None,
    }
    # Wind: e.g. "14013KT", "14013G23KT", "VRB05KT"
    m = re.search(r"\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT\b", raw)
    if m:
        d, s, g = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        p["dir_deg"] = -1 if d == "VRB" else int(d)
        p["speed_kt"] = float(s)
        p["gust_kt"] = float(g)
    # Temp/dew: e.g. "08/M04"
    m = re.search(r"\s(M?\d{1,2})/(M?\d{1,2})\s", " " + raw + " ")
    if m:
        try:
            t = m.group(1)
            p["temp_c"] = -int(t[1:]) if t.startswith("M") else int(t)
        except ValueError:
            pass
    # Pressure: "A2992" → 29.92 inHg → mb
    m = re.search(r"\bA(\d{4})\b", raw)
    if m:
        try:
            inhg = int(m.group(1)) / 100.0
            p["pressure_mb"] = round(inhg * 33.8639, 1)
        except ValueError:
            pass
    # Visibility: "10SM", "1 1/2SM", "1/2SM", "M1/4SM"
    m = re.search(r"\b(M)?(\d{1,2})(?:\s+(\d)/(\d))?SM\b", raw)
    if m:
        whole = int(m.group(2))
        frac = (int(m.group(3)) / int(m.group(4))) if m.group(3) and m.group(4) else 0
        v = whole + frac
        if m.group(1) == "M":  # less than indicator
            v *= 0.75
        p["visibility_sm"] = min(round(v, 1), 10.0)
    else:
        m = re.search(r"\b(M)?(\d)/(\d)SM\b", raw)
        if m:
            v = int(m.group(2)) / int(m.group(3))
            if m.group(1) == "M":
                v *= 0.75
            p["visibility_sm"] = round(v, 2)
    # Cloud layers: scan every "{cov}{alt}" token, take max coverage = total,
    # bucket by altitude (low <6500ft, mid 6500-20000ft, high >20000ft).
    layers = re.findall(r"\b(SKC|CLR|NSC|FEW|SCT|BKN|OVC)(\d{3})?\b", raw)
    if layers:
        max_pct = 0
        low = mid = high = 0
        for cov, alt in layers:
            pct = _METAR_CLOUD_PCT.get(cov, 0)
            max_pct = max(max_pct, pct)
            if alt:
                ft = int(alt) * 100
                if ft < 6500:
                    low = max(low, pct)
                elif ft < 20000:
                    mid = max(mid, pct)
                else:
                    high = max(high, pct)
            else:
                low = max(low, pct)
        p["cloud_pct"] = max_pct
        p["cloud_low_pct"] = low
        p["cloud_mid_pct"] = mid
        p["cloud_high_pct"] = high
    return p


def _mwos_to_point(d, latest):
    """Convert one MWOS observation into the unified shape. d is the per-station
    cache entry; latest is the most-recent observation dict."""
    # MWOS upstream returns pressure in inHg despite the field name `pressureHpa`
    # (sea-level values around 29-30 are unmistakably inHg, not mb). Detect and
    # convert: any value < 200 is inHg → mb via *33.8639.
    raw_press = latest.get("pressureHpa")
    pressure_mb = None
    if isinstance(raw_press, (int, float)) and raw_press > 0:
        pressure_mb = round(raw_press * 33.8639, 1) if raw_press < 200 else round(raw_press, 1)
    return {
        "lat": d.get("latitude", 0),
        "lon": d.get("longitude", 0),
        "source": "mwos:" + str(d.get("siteName", "?")),
        "ts": latest.get("observationTime", ""),
        "dir_deg": float(latest.get("windDirDegrees")) if latest.get("windDirDegrees") is not None else None,
        "speed_kt": float(latest.get("windSpeedKt")) if latest.get("windSpeedKt") is not None else None,
        "gust_kt": float(latest.get("windGustKt") or 0),
        "temp_c": float(latest.get("tempC")) if latest.get("tempC") is not None else None,
        "freezing_level_ft": None,
        "cloud_pct": None,
        "cloud_low_pct": None,
        "cloud_mid_pct": None,
        "cloud_high_pct": None,
        "visibility_sm": None,
        "precip_mm": None,
        "pressure_mb": pressure_mb,
    }


def _collect_observations():
    """Returns a list of station observations (METAR + MWOS) in the unified
    per-point shape. Pulled from in-memory caches; no upstream calls."""
    points = []
    cache = METAR_CACHE.get("data") or {}
    metars = cache.get("metars", {})
    meta = cache.get("meta", {})
    for stn, raw in metars.items():
        if not raw or raw == "(unavailable)":
            continue
        ts = meta.get(stn, {}).get("reportTime", "")
        p = _parse_metar_to_point(stn, raw, ts)
        if p:
            points.append(p)
    for hx, entry in _WX_CACHE.items():
        if not hx.startswith("mwos_"):
            continue
        d = entry.get("data") or {}
        obs_list = d.get("observations", []) or []
        if not obs_list:
            continue
        points.append(_mwos_to_point(d, obs_list[0]))
    return points


@app.route("/api/wx/grid")
def api_wx_grid():
    """Unified per-point grid combining METAR + MWOS observations + Open-Meteo
    model fill, all in the same flat schema. The single endpoint that drives
    every weather visualization on the kneeboard — bottom-strip HUD gauges,
    cloud-coverage overlays, vis-haze, precip blobs, freezing-level threshold
    lines — read from this one source. Tagged by `source` so the client can
    up-weight obs in IDW and the MWOS-only toggle filters out model rows.
    """
    obs_pts = _collect_observations()
    model_pts = _fetch_model_grid() or []
    pts = obs_pts + model_pts
    metar_n = sum(1 for p in obs_pts if p["source"].startswith("metar:"))
    mwos_n  = sum(1 for p in obs_pts if p["source"].startswith("mwos:"))
    return jsonify({
        "anchor": list(_DIST_ANCHOR),
        "radius_nm": _LOCAL_MODEL_RADIUS_NM,
        "step_nm": _LOCAL_MODEL_GRID_STEP_NM,
        "points": pts,
        "count": len(pts),
        "metar_count": metar_n,
        "mwos_count": mwos_n,
        "model_count": len(model_pts),
        "ts": int(time.time()),
        "ttl_s": _OPENMETEO_TTL,
    })


@app.route("/api/wind")
def api_wind():
    """Aggregate wind observations from METARs + MWOS into a single point list,
    backfilled with Open-Meteo model points across an AK grid so streamlines
    fill the entire viewport (Wundermap-style). Stations are tagged so the
    client can up-weight them in IDW (real obs override model in their vicinity)
    and so a future "MWOS only" toggle can filter to ground truth alone.
    Returns:
      {points: [{lat, lon, dir_deg, speed_kt, gust_kt, source, ts}, ...], anchor: [lat,lon]}
    Direction is FROM-direction in degrees (meteorological convention).
    """
    points = []

    # ── METAR-based winds (call /api/weather's underlying logic via cache) ──
    cache = METAR_CACHE.get("data") or {}
    metars = cache.get("metars", {})
    meta = cache.get("meta", {})
    for stn, raw in metars.items():
        if not raw or raw == "(unavailable)":
            continue
        ll = _STATION_LL.get(stn)
        if not ll:
            continue
        # METAR wind: e.g. "14013KT" or "14013G23KT" or "VRB05KT" or "00000KT"
        m = re.search(r"\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT\b", raw)
        if not m:
            continue
        dir_str, spd, gust = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        if dir_str == "VRB":
            dir_deg = -1   # variable
        else:
            dir_deg = int(dir_str)
        points.append({
            "lat": ll[0], "lon": ll[1],
            "dir_deg": dir_deg, "speed_kt": spd, "gust_kt": gust,
            "source": "metar:" + stn,
            "ts": meta.get(stn, {}).get("reportTime", ""),
        })

    # ── MWOS-based winds (already in _WX_CACHE per station) ──
    for hx, entry in _WX_CACHE.items():
        if not hx.startswith("mwos_"):
            continue
        d = entry.get("data") or {}
        obs_list = d.get("observations", []) or []
        if not obs_list:
            continue
        latest = obs_list[0]
        wind_dir = latest.get("windDirDegrees")
        wind_kt = latest.get("windSpeedKt")
        if wind_dir is None or wind_kt is None:
            continue
        points.append({
            "lat": d.get("latitude", 0), "lon": d.get("longitude", 0),
            "dir_deg": float(wind_dir), "speed_kt": float(wind_kt),
            "gust_kt": float(latest.get("windGustKt") or 0),
            "source": "mwos:" + str(d.get("siteName", hx)),
            "ts": latest.get("observationTime", ""),
        })

    # ── Open-Meteo model fill (statewide grid) ──
    model_points = _fetch_model_winds() or []
    points.extend(model_points)

    return jsonify({
        "anchor": list(_DIST_ANCHOR),
        "points": points,
        "count": len(points),
        "stations": sum(1 for p in points if not p["source"].startswith("model:")),
        "model": sum(1 for p in points if p["source"].startswith("model:")),
        "ts": int(time.time()),
    })


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
@app.route("/kneeboard")
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
  /* Comms status strip — small chips per feed, hover for detail */
  .comms-strip { display:flex; gap:5px; align-items:center; margin-left:14px; }
  .comms-chip {
    display:flex; align-items:center; gap:4px;
    background:rgba(255,255,255,0.04); border:1px solid var(--border);
    border-radius:10px; padding:2px 7px; font-size:9px; font-weight:700;
    letter-spacing:0.6px; text-transform:uppercase; cursor:help;
  }
  .comms-chip .dot { width:7px; height:7px; border-radius:50%; }
  .comms-chip .dot.ok      { background:var(--green); }
  .comms-chip .dot.partial { background:#ffaa00; box-shadow:0 0 4px #ffaa00; }
  .comms-chip .dot.down    { background:#ff3333; box-shadow:0 0 6px #ff3333; animation:commsPulse 1.4s infinite; }
  @keyframes commsPulse { 0%,100%{opacity:1;} 50%{opacity:0.4;} }
  .comms-chip .lbl { color:var(--text2); }
  .comms-chip[data-status=down] .lbl { color:#ff8888; }
  .comms-chip[data-status=partial] .lbl { color:#ffaa00; }

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
  <a class="logo" href="/public" title="Project landing page" style="text-decoration:none;color:inherit;">SKYBRIDGE</a>
  <span class="sub">Kneeboard</span>
  <span style="background:#ff8800;color:#000;font-size:10px;font-weight:800;padding:2px 6px;border-radius:4px;letter-spacing:1px;">DEV</span>
  <!-- Cross-links to public dashboards. Clean text style matches the top-bar density. -->
  <span class="kb-dash-nav" style="display:inline-flex;gap:6px;margin-left:14px;align-items:center;">
    <a href="/public/wx-shootout"    style="color:#9aa5b8;text-decoration:none;font-size:11px;font-weight:600;letter-spacing:0.5px;padding:4px 8px;border-radius:4px;transition:all 0.15s;" onmouseover="this.style.color='#23d18b';this.style.background='rgba(35,209,139,0.08)';" onmouseout="this.style.color='#9aa5b8';this.style.background='transparent';">Wx Shootout</a>
    <a href="/public/wx-validate"    style="color:#9aa5b8;text-decoration:none;font-size:11px;font-weight:600;letter-spacing:0.5px;padding:4px 8px;border-radius:4px;transition:all 0.15s;" onmouseover="this.style.color='#23d18b';this.style.background='rgba(35,209,139,0.08)';" onmouseout="this.style.color='#9aa5b8';this.style.background='transparent';">Comp Validate</a>
    <a href="/public/wx-icons-preview" style="color:#9aa5b8;text-decoration:none;font-size:11px;font-weight:600;letter-spacing:0.5px;padding:4px 8px;border-radius:4px;transition:all 0.15s;" onmouseover="this.style.color='#23d18b';this.style.background='rgba(35,209,139,0.08)';" onmouseout="this.style.color='#9aa5b8';this.style.background='transparent';">HUD Icons</a>
    <a href="/public/icons-preview"  style="color:#9aa5b8;text-decoration:none;font-size:11px;font-weight:600;letter-spacing:0.5px;padding:4px 8px;border-radius:4px;transition:all 0.15s;" onmouseover="this.style.color='#23d18b';this.style.background='rgba(35,209,139,0.08)';" onmouseout="this.style.color='#9aa5b8';this.style.background='transparent';">ADS-B Icons</a>
  </span>
  <div class="gps-strip">
    <span id="gpsStatus">GPS: acquiring</span>
    <span>GS: <span class="v" id="gsSpd">--</span>kt</span>
    <span>HDG: <span class="v" id="gsHdg">---</span></span>
    <span>ALT: <span class="v" id="gsAlt">----</span>ft</span>
    <span>TFC: <span class="v" id="tfcCt">0</span></span>
  </div>
  <div class="comms-strip" id="commsStrip" title="Feed status">
    <!-- chips populated by loadComms() -->
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
    <div class="tog" id="togRadar"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('wind')">
    <div class="dot" style="background:#88ccff"></div>
    <span class="lname">L3a Wind Flow</span>
    <div class="tog on" id="togWind"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('windObsOnly')" style="padding-left:24px">
    <div class="dot" style="background:#3a7"></div>
    <span class="lname" style="opacity:0.85">└ MWOS / METAR only (no model)</span>
    <div class="tog" id="togWindObsOnly"></div>
  </div>
  <div class="layer-row" onclick="toggleLayer('tempfield')">
    <div class="dot" style="background:#ff8866"></div>
    <span class="lname">L3b Temperature Field</span>
    <div class="tog" id="togTempfield"></div>
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
  <div class="size-row">
    <div class="size-hdr">
      <span class="size-label">L3 NEXRAD Opacity</span>
      <span class="size-value" id="radarOpValue">45 %</span>
    </div>
    <input type="range" id="radarOpSlider" min="0" max="100" step="5" value="45" oninput="applyChartOp('radar', this.value)" />
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
let RADAR_OPACITY    = _loadChartOp('skybridge-radar-opacity',   0.45);

// RainViewer manifest fetcher. Pulls the latest radar timestamp and updates
// the layers.radar tile URL. Called on init + every 5 min via setInterval.
async function _refreshRainviewerRadar() {
  try {
    const r = await fetch('https://api.rainviewer.com/public/weather-maps.json');
    const m = await r.json();
    const frames = (m?.radar?.past || []).concat(m?.radar?.nowcast || []);
    if (!frames.length) return;
    const latest = frames[frames.length - 1];
    const host = m.host || 'https://tilecache.rainviewer.com';
    // Color scheme 2 = "Universal Blue" (classic radar look). 512px = crisp.
    // smooth=1 (anti-aliased), snow=1 (separate snow shading).
    const tpl = host + latest.path + '/512/{z}/{x}/{y}/2/1_1.png';
    if (layers.radar) layers.radar.setUrl(tpl);
  } catch(e) { /* keep stale tiles */ }
}

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

  // L3: NEXRAD Radar — RainViewer tiles. Anti-aliased + nicely styled, much
  // friendlier than the blocky IEM N0Q tiles. Source uses a time-indexed
  // manifest, so we fetch the latest radar timestamp on init and refresh
  // every 5 minutes. Color scheme 2 = "Universal Blue", 512px tiles for crisp
  // rendering, smooth=1 and snow=1.
  if (!map.getPane('radarPane')) {
    const pane = map.createPane('radarPane');
    pane.style.zIndex = 250;
    pane.style.mixBlendMode = 'screen';
  }
  // Created NOT-added — pilot opts in via L3 toggle. NEXRAD raster is heavy
  // and the kneeboard now leans on the wind/temp vector layers as the primary
  // weather story.
  layers.radar = L.tileLayer('', {
    maxZoom:14, opacity:RADAR_OPACITY,
    attribution:'<a href="https://www.rainviewer.com/api.html" target="_blank">RainViewer</a>',
    pane:'radarPane',
  });
  _refreshRainviewerRadar();
  setInterval(_refreshRainviewerRadar, 5 * 60 * 1000);

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
  } else if (name === 'wind') {
    if (isOn) { _windStart(); } else { _windStop(); }
  } else if (name === 'windObsOnly') {
    // Filter the in-memory wind set to MWOS/METAR observations only — kills
    // the model fill so the user can see what they get with ground truth alone
    window._WIND_OBS_ONLY = isOn;
    loadWindData();   // re-pull and re-filter
  } else if (name === 'tempfield') {
    if (isOn) { _tempFieldStart(); } else { _tempFieldStop(); }
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
    radar:   { vname:'RADAR_OPACITY',    lbl:'radarOpValue',   ls:'skybridge-radar-opacity'   },
  }[name];
  if (!meta) return;
  // Update the layer + state
  if (layers[name] && typeof layers[name].setOpacity === 'function') {
    layers[name].setOpacity(opacity);
  }
  if (name === 'sect')         VFR_OPACITY      = opacity;
  else if (name === 'ifrLow')  IFR_LOW_OPACITY  = opacity;
  else if (name === 'ifrHigh') IFR_HIGH_OPACITY = opacity;
  else if (name === 'radar')   RADAR_OPACITY    = opacity;
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
  loadComms();      // top-bar feed-health chips
  loadWindData();   // refresh wind-flow station data
  loadTempData();   // refresh temperature-field station data
  // After polygons re-render, reapply user's chosen fill opacity
  setTimeout(_applyPolyAlphaToAll, 1500);
}

// ═══════════════════════════════════════════════════════════════════
// L3a — Wind-Waker-style wind flow animation
// ═══════════════════════════════════════════════════════════════════
// Canvas overlay that draws ~250 short flowing particles. Each particle
// finds the wind direction at its lat/lon by inverse-distance-weighted
// interpolation across station observations, walks one step in that
// direction, fades after a fixed lifespan, then respawns elsewhere.
// All in vanilla canvas — no external libs, friendly to slow devices.

let _WIND_DATA = [];          // array of {lat, lon, dir_deg, speed_kt}
let _windCanvas = null;
let _windCtx = null;
let _windParticles = [];
let _windRAF = null;
let _windAlive = false;

const WIND_PARTICLE_COUNT = 240;
const WIND_PARTICLE_LIFESPAN = 90;   // animation frames before respawn
const WIND_TRAIL_LEN = 6;            // trailing tail length per particle

async function loadWindData(){
  try {
    const r = await fetch('/api/wind');
    const d = await r.json();
    let pts = (d.points || []).filter(p => p.dir_deg >= 0 && p.speed_kt > 0);
    if (window._WIND_OBS_ONLY) {
      pts = pts.filter(p => !p.source.startsWith('model:'));
    }
    _WIND_DATA = pts;
    // Surface counts for the layer panel hint
    const obs = pts.filter(p => !p.source.startsWith('model:')).length;
    const mod = pts.length - obs;
    const lbl = document.querySelector('#togWindObsOnly')?.parentElement?.querySelector('.lname');
    if (lbl) lbl.textContent = '└ MWOS / METAR only (' + obs + ' obs / ' + mod + ' model)';
  } catch(e){}
}

// Inverse-distance-weighted wind at a given lat/lon. Returns {dir_deg, speed_kt}
// or null if no usable observations are reachable. The kneeboard is a grounded
// dashboard — we want to render whatever wind data we have *everywhere*, not
// just within a tight radius of the nearest station. So the influence radius
// is generous (statewide-ish), and we fall back to a global IDW across all
// stations when no station is within range, so distant areas still get a
// blended hint of the macro flow.
function _windAt(lat, lon) {
  if (_WIND_DATA.length === 0) return null;
  const MAX_INFLUENCE_NM = 350;   // covers most AK gaps between stations
  let u = 0, v = 0, w = 0;
  for (const p of _WIND_DATA) {
    const d = _gcDistNm([lat, lon], [p.lat, p.lon]);
    if (d > MAX_INFLUENCE_NM) continue;
    // Real observations (METAR/MWOS) get a 30× weight bump over model points
    // so within their vicinity ground truth dominates and the model just fills
    // the gaps between stations.
    const isObs = !p.source.startsWith("model:");
    const obsBoost = isObs ? 30 : 1;
    const weight = obsBoost / (d * d + 0.5);
    const rad = p.dir_deg * Math.PI / 180;
    // Wind FROM-direction: u (east) = -speed*sin(dir), v (north) = -speed*cos(dir)
    u += weight * (-p.speed_kt * Math.sin(rad));
    v += weight * (-p.speed_kt * Math.cos(rad));
    w += weight;
  }
  // Fallback: if nothing within range, blend across the global field with
  // softer weights so the user still sees *something* (e.g. far-north view
  // when only a southern station is in cache). Visually communicates "we're
  // extrapolating" via the lower resulting speed.
  if (w === 0) {
    for (const p of _WIND_DATA) {
      const d = _gcDistNm([lat, lon], [p.lat, p.lon]);
      const weight = 1 / (d * d * 0.001 + 1);
      const rad = p.dir_deg * Math.PI / 180;
      u += weight * (-p.speed_kt * Math.sin(rad));
      v += weight * (-p.speed_kt * Math.cos(rad));
      w += weight;
    }
    if (w === 0) return null;
  }
  u /= w; v /= w;
  const speed = Math.sqrt(u*u + v*v);
  let dir = Math.atan2(-u, -v) * 180 / Math.PI;
  if (dir < 0) dir += 360;
  return { dir_deg: dir, speed_kt: speed };
}

function _windSpawnParticle() {
  if (!map) return null;
  const b = map.getBounds();
  return {
    lat: b.getSouth() + Math.random() * (b.getNorth() - b.getSouth()),
    lon: b.getWest() + Math.random() * (b.getEast() - b.getWest()),
    age: Math.floor(Math.random() * WIND_PARTICLE_LIFESPAN),
    trail: [],
  };
}

function _windEnsureCanvas() {
  if (_windCanvas) return;
  // Use Leaflet's pane API so we stack correctly above tile/radar but below
  // markers/popups. Pane gets z-index 410 (above overlayPane 400, below
  // shadowPane 500). Counter the pan/zoom transform so the canvas stays
  // viewport-aligned and we redraw particles each frame from current latLngs.
  if (!map.getPane('windPane')) {
    const pane = map.createPane('windPane');
    pane.style.zIndex = 410;
    pane.style.pointerEvents = 'none';
  }
  _windCanvas = document.createElement('canvas');
  _windCanvas.id = 'windCanvas';
  Object.assign(_windCanvas.style, {
    position: 'absolute', top: '0', left: '0',
    pointerEvents: 'none',
    transform: 'none',  // counter Leaflet's pane transform
    willChange: 'auto',
  });
  map.getPane('windPane').appendChild(_windCanvas);
  _windCtx = _windCanvas.getContext('2d');

  const fit = () => {
    const mEl = document.getElementById('map');
    if (!mEl) return;
    _windCanvas.width = mEl.clientWidth || 1;
    _windCanvas.height = mEl.clientHeight || 1;
  };
  fit();
  map.on('resize', fit);
  // Counter the pane's pan-translate by re-positioning the canvas at viewport (0,0)
  // every frame. We do this inside _windFrame via `_windCanvas.style.transform`.
  map.on('moveend zoomend', () => {
    _windParticles = [];
    for (let i = 0; i < WIND_PARTICLE_COUNT; i++) _windParticles.push(_windSpawnParticle());
  });
}

function _windFrame() {
  try {
    if (!_windAlive || !map || !_windCtx) return;
    // Auto-resize the canvas if the map div has changed dimensions (chat collapse, window resize, etc.)
    const mEl = document.getElementById('map');
    if (mEl && (_windCanvas.width !== mEl.clientWidth || _windCanvas.height !== mEl.clientHeight)) {
      _windCanvas.width = mEl.clientWidth || 1;
      _windCanvas.height = mEl.clientHeight || 1;
    }
    if (_windCanvas.width < 4 || _windCanvas.height < 4) {
      // Map div not yet measured — try again next frame
    } else {
      // Counter the windPane's pan-translate so the canvas stays anchored to
      // the viewport top-left. Without this, Leaflet's translate3d on
      // .leaflet-map-pane drags the canvas with the map while our particles
      // are drawn in container coords — they appear to drift on every pan.
      try {
        const tl = map.containerPointToLayerPoint([0, 0]);
        L.DomUtil.setPosition(_windCanvas, tl);
      } catch(e) {}
      // Hard-clear each frame so the canvas stays truly transparent over the
      // map. (Old approach used a low-alpha dark fill for "trail fade", but on
      // a transparent canvas that builds up to an opaque wash that obscures
      // the tile layers underneath.) Trails are stored on each particle, so
      // we just redraw them from scratch — and fade older segments per-stroke.
      _windCtx.clearRect(0, 0, _windCanvas.width, _windCanvas.height);
      _windCtx.lineCap = 'round';
      _windCtx.lineJoin = 'round';

      // Density curve: same idea as speed — each zoom level past REF reduces
      // the active-particle count by a gentle 0.78 factor so the field doesn't
      // turn into solid glow at city zooms but still feels populated.
      const _z = (typeof map.getZoom === 'function') ? map.getZoom() : 9;
      const _densityScale = Math.pow(0.78, Math.max(0, _z - 9));
      const _activeCount = Math.max(40, Math.floor(_windParticles.length * _densityScale));

      for (let i = 0; i < _activeCount; i++) {
        let p = _windParticles[i];
        if (!p || p.age >= WIND_PARTICLE_LIFESPAN) {
          p = _windParticles[i] = _windSpawnParticle();
          if (!p) continue;
          p.age = 0;   // fresh respawn always starts at 0 (no random-old respawns)
        }
        const w = _windAt(p.lat, p.lon);
        if (!w) { p.age = WIND_PARTICLE_LIFESPAN; continue; }
        // Base step is in lat/lon degrees per frame — invariant to zoom. Each
        // zoom level doubles the px-per-degree, so without compensation the
        // pixel speed doubles per level too (city-view supersonic). Fully
        // compensating with /2^delta kills the "faster up close" feel; use a
        // gentler base of 1.45 so px-velocity grows mildly with zoom but never
        // explodes. Clamp final px/frame as a final safety net.
        const REF_ZOOM = 9;
        const z = (typeof map.getZoom === 'function') ? map.getZoom() : REF_ZOOM;
        const zoomDelta = Math.max(0, z - REF_ZOOM);
        const zoomScale = Math.pow(1.45, zoomDelta);
        let stepMag = (w.speed_kt * 7.7e-6 * 80) / zoomScale;
        const rad = w.dir_deg * Math.PI / 180;
        let dLat = -stepMag * Math.cos(rad);
        let dLon = -stepMag * Math.sin(rad) / Math.cos(p.lat * Math.PI / 180);
        // Hard cap: convert step to px/frame at current zoom; if it exceeds
        // MAX_PX_PER_FRAME, scale dLat/dLon down proportionally.
        const MAX_PX_PER_FRAME = 4;
        try {
          const here = map.latLngToContainerPoint({lat:p.lat, lng:p.lon});
          const next = map.latLngToContainerPoint({lat:p.lat + dLat, lng:p.lon + dLon});
          const pxStep = Math.hypot(next.x - here.x, next.y - here.y);
          if (pxStep > MAX_PX_PER_FRAME && pxStep > 0) {
            const k = MAX_PX_PER_FRAME / pxStep;
            dLat *= k; dLon *= k;
          }
        } catch(e) {}
        p.lat += dLat;
        p.lon += dLon;
        p.age++;
        p.trail.push([p.lat, p.lon]);
        if (p.trail.length > WIND_TRAIL_LEN) p.trail.shift();

        if (p.trail.length >= 2) {
          const s = w.speed_kt;
          // Wind-Waker palette: cool blue → green → amber → red as speed climbs
          const rgb = s < 8  ? '140,200,255' :
                      s < 18 ? '200,230,140' :
                      s < 30 ? '255,200,60'  : '255,90,40';
          // Convert trail to screen pts once per particle
          const pts = [];
          for (let j = 0; j < p.trail.length; j++) {
            try {
              const sp = map.latLngToContainerPoint({lat:p.trail[j][0], lng:p.trail[j][1]});
              pts.push(sp);
            } catch(e) {}
          }
          if (pts.length < 2) continue;
          // Draw each segment with alpha that fades with age within the trail
          // (older end of trail is more transparent, head is brightest).
          for (let j = 1; j < pts.length; j++) {
            const t = j / (pts.length - 1);   // 0..1, head-of-trail = 1
            const a = 0.15 + 0.75 * t;        // 0.15 → 0.90
            _windCtx.strokeStyle = 'rgba(' + rgb + ',' + a.toFixed(2) + ')';
            _windCtx.lineWidth = 0.9 + 0.8 * t;
            _windCtx.beginPath();
            _windCtx.moveTo(pts[j-1].x, pts[j-1].y);
            _windCtx.lineTo(pts[j].x, pts[j].y);
            _windCtx.stroke();
          }
        }
      }
    }
  } catch(err) {
    // Don't let one bad frame kill the whole animation
    console.warn('[wind] frame error:', err);
  }
  // Always reschedule while alive — even after a thrown frame
  if (_windAlive) _windRAF = requestAnimationFrame(_windFrame);
}

window._windStart = function() {
  if (_windAlive) return;
  _windEnsureCanvas();
  _windParticles = [];
  for (let i = 0; i < WIND_PARTICLE_COUNT; i++) _windParticles.push(_windSpawnParticle());
  _windAlive = true;
  _windFrame();
};

window._windStop = function() {
  _windAlive = false;
  if (_windRAF) cancelAnimationFrame(_windRAF);
  if (_windCtx && _windCanvas) {
    _windCtx.clearRect(0, 0, _windCanvas.width, _windCanvas.height);
  }
};

// ═══════════════════════════════════════════════════════════════════
// L3b — Temperature Field heatmap (TV-weather-map style)
// ═══════════════════════════════════════════════════════════════════
// Low-res canvas rendered with per-pixel IDW interpolation across station
// observations, then CSS-scaled to map size. Re-renders on view change or
// new data, NOT every frame (it's a static field, not animated).
let _TEMP_DATA = [];
let _tempCanvas = null, _tempCtx = null;
const TEMP_GRID_W = 120, TEMP_GRID_H = 80;

async function loadTempData(){
  try {
    const r = await fetch('/api/wx/field');
    const d = await r.json();
    _TEMP_DATA = (d.points || []).filter(p => typeof p.temp_c === 'number');
    if (_tempCanvas && document.getElementById('togTempfield')?.classList.contains('on')) {
      _tempFieldRender();
    }
  } catch(e) {}
}

// Map temp °C → RGB color (cool blue → warm orange → hot red)
function _tempColor(t) {
  // Stops: -30°C deep blue, -10 cyan, 0 sky, 10 green, 20 yellow, 30 orange, 40 red
  const stops = [
    [-30, [10, 30, 90]],   [-10, [40, 110, 200]],
    [0,   [110, 180, 230]],[10,  [120, 220, 130]],
    [20,  [240, 230, 90]], [30,  [240, 150, 60]], [40, [220, 50, 30]],
  ];
  if (t <= stops[0][0]) return stops[0][1];
  if (t >= stops[stops.length-1][0]) return stops[stops.length-1][1];
  for (let i = 1; i < stops.length; i++) {
    if (t < stops[i][0]) {
      const a = stops[i-1], b = stops[i];
      const f = (t - a[0]) / (b[0] - a[0]);
      return [
        Math.round(a[1][0] + f*(b[1][0]-a[1][0])),
        Math.round(a[1][1] + f*(b[1][1]-a[1][1])),
        Math.round(a[1][2] + f*(b[1][2]-a[1][2])),
      ];
    }
  }
  return stops[stops.length-1][1];
}

function _tempFieldEnsureCanvas() {
  if (_tempCanvas) return;
  // Same trick as the wind canvas: use a dedicated Leaflet pane so we stack
  // above tiles/radar but below the wind streams. zIndex 395 puts us just
  // under overlayPane (400) and well under windPane (410).
  if (!map.getPane('tempPane')) {
    const pane = map.createPane('tempPane');
    pane.style.zIndex = 395;
    pane.style.pointerEvents = 'none';
    pane.style.mixBlendMode = 'screen';
  }
  _tempCanvas = document.createElement('canvas');
  _tempCanvas.id = 'tempCanvas';
  _tempCanvas.width = TEMP_GRID_W;
  _tempCanvas.height = TEMP_GRID_H;
  Object.assign(_tempCanvas.style, {
    position: 'absolute', top: '0', left: '0',
    width: '100%', height: '100%',
    pointerEvents: 'none',
    opacity: '0.45',
    imageRendering: 'auto',
    transform: 'none',  // counter Leaflet's pane transform
  });
  map.getPane('tempPane').appendChild(_tempCanvas);
  _tempCtx = _tempCanvas.getContext('2d');
  // Anchor the canvas to viewport top-left so it doesn't slide with Leaflet's
  // map-pane translate during a pan. We listen to `move` (continuous) and
  // `zoom` (during animation) so the temp polygons stay registered with the
  // map under interaction. Re-render only at end-of-pan.
  const _tempReanchor = () => {
    try {
      const tl = map.containerPointToLayerPoint([0, 0]);
      L.DomUtil.setPosition(_tempCanvas, tl);
    } catch(e) {}
  };
  map.on('move zoom viewreset', _tempReanchor);
  _tempReanchor();
  map.on('moveend zoomend resize', () => { _tempReanchor(); _tempFieldRender(); });
}

function _tempFieldRender() {
  if (!_tempCtx || _TEMP_DATA.length === 0) return;
  const b = map.getBounds();
  const dLat = (b.getNorth() - b.getSouth()) / TEMP_GRID_H;
  const dLon = (b.getEast() - b.getWest()) / TEMP_GRID_W;
  const img = _tempCtx.createImageData(TEMP_GRID_W, TEMP_GRID_H);
  // Generous radius: this is a grounded dashboard and we want to fill the
  // viewport with whatever data we have rather than leave dead zones.
  const MAX_INFLUENCE_NM = 400;
  for (let yi = 0; yi < TEMP_GRID_H; yi++) {
    const lat = b.getNorth() - yi * dLat;
    for (let xi = 0; xi < TEMP_GRID_W; xi++) {
      const lon = b.getWest() + xi * dLon;
      let sum = 0, w = 0;
      for (const p of _TEMP_DATA) {
        const d = _gcDistNm([lat, lon], [p.lat, p.lon]);
        if (d > MAX_INFLUENCE_NM) continue;
        const ww = 1 / (d * d + 1.5);
        sum += ww * p.temp_c;
        w += ww;
      }
      const idx = (yi * TEMP_GRID_W + xi) * 4;
      if (w === 0) {
        img.data[idx + 3] = 0;  // transparent — out of range
      } else {
        const t = sum / w;
        const rgb = _tempColor(t);
        img.data[idx]     = rgb[0];
        img.data[idx + 1] = rgb[1];
        img.data[idx + 2] = rgb[2];
        img.data[idx + 3] = 230;
      }
    }
  }
  _tempCtx.putImageData(img, 0, 0);
}

window._tempFieldStart = function() {
  _tempFieldEnsureCanvas();
  _tempCanvas.style.display = '';
  if (_TEMP_DATA.length === 0) { loadTempData(); }
  else { _tempFieldRender(); }
};
window._tempFieldStop = function() {
  if (_tempCanvas) _tempCanvas.style.display = 'none';
};

// Comms status strip: small chips in the top bar showing feed health.
async function loadComms(){
  let data;
  try {
    const r = await fetch('/api/health');
    data = await r.json();
  } catch(e) { return; }
  const strip = document.getElementById('commsStrip');
  if (!strip) return;
  const html = (data.feeds || []).map(f => {
    const detail = (f.detail || '') + (f.age_s > 0 ? ` (${f.age_s}s ago)` : '');
    return `<span class="comms-chip" data-status="${f.status}" title="${detail.replace(/"/g,'&quot;')}">
      <span class="dot ${f.status}"></span>
      <span class="lbl">${f.name}</span>
    </span>`;
  }).join('');
  strip.innerHTML = html;
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
    ['radarOpSlider',   'radarOpValue',   RADAR_OPACITY],
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
// Bootstrap wind data + flow animation (the default toggle is ON)
loadWindData().then(() => { if (document.getElementById('togWind')?.classList.contains('on')) _windStart(); });
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
    app.run(host=os.environ.get('KNEEBOARD_HOST', '0.0.0.0'), port=8084, debug=False)
