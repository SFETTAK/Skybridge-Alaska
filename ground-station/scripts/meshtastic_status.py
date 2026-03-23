#!/usr/bin/env python3
"""
meshtastic_status.py — Skybridge Alaska Meshtastic Status Poller
Location: /opt/skybridge/vhf/meshtastic_status.py
Cron: */10 * * * * python3 /opt/skybridge/vhf/meshtastic_status.py >> /home/blastly/logs/meshtastic.log 2>&1

Logic:
  1. Run meshtastic --info --export-config
  2. Parse node list + channel status
  3. Alert if node silent >30min
  4. Write /tmp/skybridge_mesh_status.json for dashboard
  5. Post to Discord only on state change (new node, node silent, startup)
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")  # Set in /home/blastly/.env.skybridge
CHANNEL_ID    = "1485484585142980748"
STATUS_JSON   = "/tmp/skybridge_mesh_status.json"
STATE_FILE    = "/tmp/skybridge_mesh_state.json"   # persists last known state
ALERT_THRESH  = 30 * 60   # 30 minutes in seconds
CHANNELS      = ["Admin", "Weather", "ADSB", "Comms", "VHF"]

# Meshtastic binary — try venv first, then system
MESH_BIN_CANDIDATES = [
    os.path.expanduser("~/meshtastic_venv/bin/meshtastic"),
    "/home/blastly/meshtastic_venv/bin/meshtastic",
    "meshtastic",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def find_meshtastic():
    """Return path to meshtastic binary, or None if not found."""
    for candidate in MESH_BIN_CANDIDATES:
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def run_meshtastic_info(mesh_bin):
    """Run meshtastic --info and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            [mesh_bin, "--info"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return result.stdout
        print(f"[WARN] meshtastic --info returned {result.returncode}: {result.stderr[:200]}", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print("[WARN] meshtastic --info timed out", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[ERROR] meshtastic --info failed: {e}", file=sys.stderr)
        return None


def parse_nodes(info_text):
    """
    Parse node info from meshtastic --info output.
    Returns list of dicts: {id, short_name, last_heard_s, snr}
    """
    nodes = []
    now = int(time.time())

    # Match lines like:
    # !abcd1234: "SLYB1" last_heard: 1711234567 snr: 8.5
    # or the YAML-ish export-config format with lastHeard fields
    # meshtastic --info outputs a table-like format; parse conservatively.

    # Pattern 1: table row with node id, short name, last heard epoch, snr
    # Example: "!deadbeef  SLYB1  2m ago  8.5 dB"
    pattern_table = re.compile(
        r'(![\da-f]+)\s+(\S+)\s+.*?(\d+)\s*(?:s|m|h|d)?\s*ago.*?(-?\d+\.?\d*)\s*dB',
        re.IGNORECASE
    )

    # Pattern 2: YAML/JSON-ish from --export-config
    # Look for num: !xxxx, shortName: X, lastHeard: epoch
    current_node = {}
    for line in info_text.splitlines():
        line = line.strip()

        # YAML-style block parsing
        if line.startswith("num:") or line.startswith("- num:"):
            if current_node.get("id"):
                nodes.append(_finalize_node(current_node, now))
            current_node = {}
            m = re.search(r'num:\s*(![\da-f]+|\d+)', line, re.IGNORECASE)
            if m:
                raw_id = m.group(1)
                # Convert decimal to hex node id if needed
                if raw_id.startswith("!"):
                    current_node["id"] = raw_id
                else:
                    current_node["id"] = "!" + format(int(raw_id) & 0xFFFFFFFF, "08x")

        elif line.startswith("shortName:") or line.startswith("short_name:"):
            m = re.search(r':\s*(\S+)', line)
            if m:
                current_node["short_name"] = m.group(1).strip('"\'')

        elif line.startswith("lastHeard:") or line.startswith("last_heard:"):
            m = re.search(r':\s*(\d+)', line)
            if m:
                current_node["last_heard_epoch"] = int(m.group(1))

        elif line.startswith("snr:") or line.startswith("SNR:"):
            m = re.search(r':\s*(-?\d+\.?\d*)', line)
            if m:
                current_node["snr"] = float(m.group(1))

        # Table-style: try inline pattern
        else:
            m = pattern_table.search(line)
            if m:
                nid, sname, ago_val, snr_val = m.groups()
                # 'ago' is in seconds from pattern; crude approximation
                nodes.append({
                    "id": nid,
                    "short_name": sname,
                    "last_heard_s": int(ago_val),
                    "snr": float(snr_val)
                })

    # Flush last YAML node
    if current_node.get("id"):
        nodes.append(_finalize_node(current_node, now))

    return nodes


def _finalize_node(node_dict, now):
    last_heard_epoch = node_dict.get("last_heard_epoch")
    if last_heard_epoch:
        last_heard_s = max(0, now - last_heard_epoch)
    else:
        last_heard_s = 999999  # unknown = treat as very old

    return {
        "id": node_dict.get("id", "!unknown"),
        "short_name": node_dict.get("short_name", "???"),
        "last_heard_s": last_heard_s,
        "snr": node_dict.get("snr", 0.0)
    }


def load_state():
    """Load previous state JSON, or return empty dict."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[WARN] Could not save state: {e}", file=sys.stderr)


def post_discord(message):
    """Post a plain text message to Discord via bot token."""
    import urllib.request
    import urllib.error

    payload = json.dumps({"content": message}).encode("utf-8")
    url = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bot {DISCORD_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"[INFO] Discord post OK: HTTP {resp.status}", file=sys.stderr)
            return True
    except urllib.error.HTTPError as e:
        print(f"[WARN] Discord post failed: HTTP {e.code} {e.reason}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[WARN] Discord post error: {e}", file=sys.stderr)
        return False


def akst_time():
    """Return current time as HH:MM AKST string (UTC-9, no DST for simplicity)."""
    now_utc = datetime.now(timezone.utc)
    # AKST = UTC-9, AKDT = UTC-8 (March-November approx)
    # Simple: use UTC-9 for AKST label
    offset_h = -9
    ts = now_utc.timestamp() + offset_h * 3600
    dt = datetime.utcfromtimestamp(ts)
    return dt.strftime("%H:%M") + " AKST"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    now_iso = datetime.now(timezone.utc).isoformat()
    print(f"[{now_iso}] meshtastic_status.py starting", file=sys.stderr)

    # Find meshtastic binary
    mesh_bin = find_meshtastic()
    if not mesh_bin:
        status = {
            "timestamp": now_iso,
            "channels": CHANNELS,
            "nodes_heard": [],
            "alert": True,
            "alert_message": "meshtastic binary not found — hardware or install missing",
            "hardware_present": False,
        }
        with open(STATUS_JSON, "w") as f:
            json.dump(status, f, indent=2)

        # Check if this is startup or a known issue
        prev_state = load_state()
        if prev_state.get("hardware_present", True):
            # State change: was OK, now missing
            msg = (
                f"📡 **Meshtastic Alert** | {akst_time()}\n"
                f"⚠️ meshtastic binary not found — RAK4631 may be disconnected\n"
                f"Channels configured: {len(CHANNELS)}"
            )
            post_discord(msg)
            save_state({"hardware_present": False, "node_ids": [], "timestamp": now_iso})
        else:
            print("[INFO] Hardware still missing (no state change, no Discord post)", file=sys.stderr)
        return

    # Run meshtastic --info
    info_text = run_meshtastic_info(mesh_bin)
    if info_text is None:
        status = {
            "timestamp": now_iso,
            "channels": CHANNELS,
            "nodes_heard": [],
            "alert": True,
            "alert_message": "meshtastic --info failed — device may be offline",
            "hardware_present": True,
        }
        with open(STATUS_JSON, "w") as f:
            json.dump(status, f, indent=2)

        prev_state = load_state()
        if prev_state.get("device_ok", True):
            msg = (
                f"📡 **Meshtastic Alert** | {akst_time()}\n"
                f"⚠️ meshtastic --info failed — RAK4631 not responding\n"
                f"Channels configured: {len(CHANNELS)}"
            )
            post_discord(msg)
            save_state({"hardware_present": True, "device_ok": False, "node_ids": [], "timestamp": now_iso})
        return

    # Parse nodes
    nodes = parse_nodes(info_text)
    print(f"[INFO] Parsed {len(nodes)} nodes from meshtastic output", file=sys.stderr)

    # Detect alerts
    alerts = []
    for node in nodes:
        if node["last_heard_s"] > ALERT_THRESH:
            mins = node["last_heard_s"] // 60
            alerts.append(f"Node {node['short_name']} ({node['id']}) silent for {mins} minutes")

    alert_flag = len(alerts) > 0
    alert_msg = "; ".join(alerts) if alerts else ""

    status = {
        "timestamp": now_iso,
        "channels": CHANNELS,
        "nodes_heard": nodes,
        "alert": alert_flag,
        "alert_message": alert_msg,
        "hardware_present": True,
        "device_ok": True,
    }

    with open(STATUS_JSON, "w") as f:
        json.dump(status, f, indent=2)
    print(f"[INFO] Wrote {STATUS_JSON}", file=sys.stderr)

    # State change detection
    prev_state = load_state()
    prev_node_ids = set(prev_state.get("node_ids", []))
    curr_node_ids = set(n["id"] for n in nodes)

    new_nodes   = curr_node_ids - prev_node_ids
    lost_nodes  = prev_node_ids - curr_node_ids
    is_startup  = not prev_state  # first run

    should_post = False
    discord_lines = [f"📡 **Meshtastic Alert** | {akst_time()}"]

    if is_startup:
        should_post = True
        discord_lines = [
            f"📡 **Meshtastic Online** | {akst_time()}",
            f"✅ Poller started — {len(nodes)} node(s) heard",
            f"Channels active: {len(CHANNELS)}"
        ]
    elif new_nodes:
        should_post = True
        for nid in new_nodes:
            node = next((n for n in nodes if n["id"] == nid), None)
            name = node["short_name"] if node else nid
            discord_lines.append(f"✅ New node online: {name} ({nid})")
        discord_lines.append(f"Channels active: {len(CHANNELS)}")
    elif lost_nodes:
        should_post = True
        for nid in lost_nodes:
            discord_lines.append(f"⚠️ Node disappeared from mesh: {nid}")
        discord_lines.append(f"Channels active: {len(CHANNELS)}")
    elif alert_flag:
        # Per-node silent alerts only if alert state changed
        prev_alerts = set(prev_state.get("alerted_nodes", []))
        newly_silent = set()
        for node in nodes:
            if node["last_heard_s"] > ALERT_THRESH and node["id"] not in prev_alerts:
                newly_silent.add(node["id"])
                mins = node["last_heard_s"] // 60
                discord_lines.append(f"⚠️ Node {node['short_name']} silent for {mins} minutes")

        if newly_silent:
            should_post = True
            discord_lines.append(f"Channels active: {len(CHANNELS)}")

    if should_post:
        post_discord("\n".join(discord_lines))

    # Save new state
    alerted_nodes = [n["id"] for n in nodes if n["last_heard_s"] > ALERT_THRESH]
    save_state({
        "hardware_present": True,
        "device_ok": True,
        "node_ids": list(curr_node_ids),
        "alerted_nodes": alerted_nodes,
        "timestamp": now_iso,
    })

    print(f"[INFO] Done. Nodes: {len(nodes)}, Alerts: {len(alerts)}", file=sys.stderr)


if __name__ == "__main__":
    main()
