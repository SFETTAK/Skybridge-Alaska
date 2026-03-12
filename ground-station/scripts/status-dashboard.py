#!/usr/bin/env python3
"""
DOT-VHF Station Status Dashboard
Writes /mnt/nvme/skybridge/status.html every 30 seconds.
Served by lighttpd on port 8504 alongside tar1090.
"""

import json
import os
import shutil
import subprocess
import time
import datetime

SERVICES = [
    ("openwebrx",    "OpenWebRX SDR"),
    ("readsb",       "ADSB Receiver"),
    ("tar1090",      "tar1090 Map"),
    ("vhf-pipeline", "VHF Pipeline"),
    ("nvme-backup",  "NVMe Backup Timer"),
]

STATUS_JSON  = "/run/readsb/status.json"
STATS_JSON   = "/run/readsb/stats.json"
NVME_PATH    = "/mnt/nvme"
TRANSCRIPT_DIR = "/mnt/nvme/skybridge/transcripts"
AUDIO_DIR    = "/mnt/nvme/skybridge/vhf-audio"
OUTPUT_HTML  = "/mnt/nvme/skybridge/status.html"
SMART_DEV    = "/dev/nvme0n1"


def svc_status(name):
    r = subprocess.run(["systemctl", "is-active", name],
                       capture_output=True, text=True)
    s = r.stdout.strip()
    if s == "active":
        return "active", "#2ecc71"
    elif s == "activating":
        return "activating", "#f39c12"
    else:
        return s, "#e74c3c"


def nvme_health():
    try:
        r = subprocess.run(["smartctl", "-j", "-a", SMART_DEV],
                           capture_output=True, text=True)
        d = json.loads(r.stdout)
        ata = d.get("nvme_smart_health_information_log", {})
        return {
            "temp":         ata.get("temperature", {}).get("current", "?"),
            "spare":        ata.get("available_spare", "?"),
            "used_pct":     ata.get("percentage_used", "?"),
            "media_errors": ata.get("media_errors", "?"),
            "power_hours":  ata.get("power_on_hours", "?"),
            "passed":       d.get("smart_status", {}).get("passed", False),
        }
    except Exception as e:
        return {"error": str(e)}


def disk_usage():
    try:
        u = shutil.disk_usage(NVME_PATH)
        used_gb  = u.used  / 1e9
        total_gb = u.total / 1e9
        free_gb  = u.free  / 1e9
        pct      = u.used  / u.total * 100
        return used_gb, total_gb, free_gb, pct
    except Exception:
        return None, None, None, None


def readsb_status():
    try:
        with open(STATUS_JSON) as f:
            return json.load(f)
    except Exception:
        return {}


def last_transcript():
    try:
        today = datetime.date.today().isoformat()
        path = os.path.join(TRANSCRIPT_DIR, today + ".txt")
        if os.path.exists(path):
            with open(path) as f:
                lines = f.readlines()
            if lines:
                return lines[-1].strip()[:120]
        return "No transcripts today yet"
    except Exception:
        return "?"


def last_backup():
    try:
        log_dir = "/mnt/nvme/skybridge/logs"
        logs = sorted([f for f in os.listdir(log_dir)
                       if f.startswith("rclone-backup-")], reverse=True)
        if logs:
            path = os.path.join(log_dir, logs[0])
            mtime = os.path.getmtime(path)
            dt = datetime.datetime.fromtimestamp(mtime)
            return dt.strftime("%Y-%m-%d %H:%M")
        return "Never (remote not configured)"
    except Exception:
        return "?"


def audio_count():
    try:
        count = 0
        today = datetime.date.today().isoformat()
        day_dir = os.path.join(AUDIO_DIR, today)
        if os.path.exists(day_dir):
            count = len([f for f in os.listdir(day_dir) if f.endswith(".flac")])
        return count
    except Exception:
        return 0


def render(now):
    health   = nvme_health()
    used, total, free, pct = disk_usage()
    adsb     = readsb_status()
    ac_pos   = adsb.get("aircraft_with_pos", "?")
    ac_total = (adsb.get("aircraft_with_pos", 0) +
                adsb.get("aircraft_without_pos", 0))
    uptime_s = adsb.get("uptime", 0)
    uptime_h = f"{int(uptime_s // 3600)}h {int((uptime_s % 3600) // 60)}m"

    disk_color = "#2ecc71" if pct and pct < 70 else "#f39c12" if pct < 85 else "#e74c3c"
    smart_ok   = health.get("passed", False)
    smart_color = "#2ecc71" if smart_ok else "#e74c3c"
    smart_label = "PASSED" if smart_ok else "FAILED"

    svc_rows = ""
    for svc_id, svc_label in SERVICES:
        state, color = svc_status(svc_id)
        svc_rows += f"""
        <tr>
          <td>{svc_label}</td>
          <td><span class="badge" style="background:{color}">{state}</span></td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="30">
  <title>DOT-VHF Station Status</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Courier New', monospace; background: #0d1117; color: #c9d1d9; padding: 24px; }}
    h1 {{ color: #58a6ff; font-size: 1.4rem; margin-bottom: 4px; }}
    .ts {{ color: #6e7681; font-size: 0.8rem; margin-bottom: 24px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }}
    .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }}
    .card h2 {{ color: #58a6ff; font-size: 0.9rem; text-transform: uppercase;
                letter-spacing: .08em; margin-bottom: 12px; border-bottom: 1px solid #21262d; padding-bottom: 6px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
    td {{ padding: 5px 4px; border-bottom: 1px solid #21262d; }}
    td:first-child {{ color: #8b949e; width: 55%; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
              color: #0d1117; font-weight: bold; font-size: 0.78rem; }}
    .bar-bg {{ background: #21262d; border-radius: 4px; height: 8px; margin-top: 6px; }}
    .bar-fill {{ height: 8px; border-radius: 4px; background: {disk_color}; width: {min(pct or 0, 100):.1f}%; }}
    .transcript {{ font-size: 0.78rem; color: #8b949e; word-break: break-word;
                   background: #0d1117; padding: 8px; border-radius: 4px; margin-top: 4px; }}
    .links a {{ color: #58a6ff; text-decoration: none; margin-right: 16px; font-size: 0.85rem; }}
    .links a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>&#x1F4E1; DOT-VHF &mdash; SkyBridge Alaska</h1>
  <div class="ts">Last updated: {now.strftime("%Y-%m-%d %H:%M:%S AKST")} &nbsp;|&nbsp;
    <span class="links">
      <a href="http://192.168.1.81:8073" target="_blank">OpenWebRX</a>
      <a href="http://192.168.1.81:8504" target="_blank">tar1090 ADSB</a>
    </span>
  </div>

  <div class="grid">

    <div class="card">
      <h2>Services</h2>
      <table>{svc_rows}
      </table>
    </div>

    <div class="card">
      <h2>ADSB</h2>
      <table>
        <tr><td>Aircraft with position</td><td>{ac_pos}</td></tr>
        <tr><td>Aircraft total</td><td>{ac_total}</td></tr>
        <tr><td>Receiver uptime</td><td>{uptime_h}</td></tr>
        <tr><td>Globe-history</td><td>&#x2714; /mnt/nvme/skybridge/adsb</td></tr>
      </table>
    </div>

    <div class="card">
      <h2>NVMe Storage</h2>
      <table>
        <tr><td>Used / Total</td><td>{used:.1f} GB / {total:.0f} GB</td></tr>
        <tr><td>Free</td><td>{free:.1f} GB ({100-pct:.1f}%)</td></tr>
        <tr><td>SMART</td>
            <td><span class="badge" style="background:{smart_color}">{smart_label}</span></td></tr>
        <tr><td>Temp</td><td>{health.get('temp','?')} &deg;C</td></tr>
        <tr><td>Available spare</td><td>{health.get('spare','?')}%</td></tr>
        <tr><td>Percentage used</td><td>{health.get('used_pct','?')}%</td></tr>
        <tr><td>Power-on hours</td><td>{health.get('power_hours','?')}</td></tr>
        <tr><td>Media errors</td><td>{health.get('media_errors','?')}</td></tr>
      </table>
      <div class="bar-bg"><div class="bar-fill"></div></div>
    </div>

    <div class="card">
      <h2>VHF Pipeline</h2>
      <table>
        <tr><td>Audio files today</td><td>{audio_count()}</td></tr>
        <tr><td>Last backup</td><td>{last_backup()}</td></tr>
        <tr><td>Meshtastic node</td><td>Pending onsite</td></tr>
      </table>
      <div class="transcript">Last transcript: {last_transcript()}</div>
    </div>

  </div>
</body>
</html>"""


def main():
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    while True:
        try:
            now = datetime.datetime.now()
            html = render(now)
            tmp = OUTPUT_HTML + ".tmp"
            with open(tmp, "w") as f:
                f.write(html)
            os.replace(tmp, OUTPUT_HTML)
        except Exception as e:
            print(f"render error: {e}")
        time.sleep(30)


if __name__ == "__main__":
    main()
