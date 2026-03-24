#!/usr/bin/env python3
"""
discord_webhooks.py — Skybridge Alaska Discord Integration
Location: /home/blastly/Skybridge-Alaska/discord_integration/discord_webhooks.py

Long-lived daemon posting to Discord via webhooks (no bot token needed).
Covers:
  - ADS-B emergency squawk alerts (7700/7600/7500) → #skybridge-emergency
  - ADS-B filtered traffic → #skybridge-adsb  
  - VHF transcripts (tail from Whisper output) → #skybridge-vhf
  - Station status heartbeat + change alerts → #skybridge-status
  - Meshtastic mesh updates → #skybridge-mesh

Usage:
    python3 discord_webhooks.py [--config /path/to/config.yaml]

Dependencies: requests, PyYAML (both already installed on DOT-VHF)
"""

import json
import logging
import logging.handlers
import os
import sys
import time
import threading
import subprocess
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Set

import requests
import yaml

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = Path(__file__).parent / "config.yaml"


def load_config(path=None) -> dict:
    p = Path(path) if path else DEFAULT_CONFIG
    if not p.exists():
        print(f"[ERROR] Config not found: {p}", file=sys.stderr)
        sys.exit(1)
    with open(p) as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging(cfg: dict) -> logging.Logger:
    lcfg = cfg.get("logging", {})
    log_file = lcfg.get("file", "/home/blastly/logs/skybridge-discord.log")
    level = getattr(logging, lcfg.get("level", "INFO").upper(), logging.INFO)
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=lcfg.get("max_bytes", 5_242_880),
        backupCount=lcfg.get("backup_count", 3))
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(fh)
    root.addHandler(ch)
    return logging.getLogger("skybridge-discord")


# ─────────────────────────────────────────────────────────────────────────────
# WEBHOOK POSTER
# ─────────────────────────────────────────────────────────────────────────────

class Hook:
    def __init__(self, url: str, name: str, log: logging.Logger):
        self.url = url
        self.name = name
        self.log = log

    def is_live(self) -> bool:
        return bool(self.url) and "PLACEHOLDER" not in self.url

    def post(self, payload: dict, retries=3) -> bool:
        if not self.is_live():
            self.log.debug("Webhook '%s' skipped (placeholder)", self.name)
            return False
        for attempt in range(retries):
            try:
                r = requests.post(self.url, json=payload, timeout=10)
                if r.status_code in (200, 204):
                    return True
                if r.status_code == 429:
                    wait = r.json().get("retry_after", 5)
                    self.log.warning("Rate limited on '%s', waiting %.1fs", self.name, wait)
                    time.sleep(float(wait) + 0.5)
                else:
                    self.log.warning("Webhook '%s' HTTP %d", self.name, r.status_code)
                    return False
            except requests.RequestException as e:
                self.log.warning("Webhook '%s' error (try %d): %s", self.name, attempt+1, e)
                time.sleep(2 ** attempt)
        return False

    def embed(self, title: str, desc: str = "", color: int = 0x00B0F4,
              fields: list = None, footer: str = "") -> bool:
        e: Dict[str, Any] = {"title": title, "color": color,
                              "timestamp": datetime.now(timezone.utc).isoformat()}
        if desc:
            e["description"] = desc
        if fields:
            e["fields"] = fields
        if footer:
            e["footer"] = {"text": footer}
        return self.post({"embeds": [e]})


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def akst_now() -> str:
    utc = datetime.now(timezone.utc)
    dst = (utc.month > 3 or (utc.month == 3 and utc.day >= 8)) and (utc.month < 11)
    local = utc + timedelta(hours=-8 if dst else -9)
    return local.strftime(f"%H:%M {'AKDT' if dst else 'AKST'}")


def load_state(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: str, state: dict):
    try:
        with open(path, "w") as f:
            json.dump(state, f)
    except Exception as e:
        logging.getLogger("skybridge-discord").warning("State save failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# ADS-B MONITOR
# ─────────────────────────────────────────────────────────────────────────────

SQUAWK_INFO = {
    "7700": ("🚨 EMERGENCY", "General emergency declared", 0xFF0000),
    "7600": ("📵 RADIO FAILURE", "Loss of communications", 0xFF8C00),
    "7500": ("🏴‍☠️ HIJACK", "Unlawful interference declared", 0x8B0000),
}


class AdsbMonitor:
    def __init__(self, cfg: dict, hooks: dict, log: logging.Logger):
        acfg = cfg.get("adsb", {})
        self.json_path = acfg.get("json_path", "/run/readsb/aircraft.json")
        self.poll_interval = acfg.get("poll_interval", 10)
        self.emergency_squawks: Set[str] = set(acfg.get("emergency_squawks", ["7700","7600","7500"]))
        self.cooldown = acfg.get("alert_cooldown", 300)
        self.min_msgs = acfg.get("min_messages", 5)
        self.station = cfg.get("station", {})

        self.hook_emergency = hooks.get("emergency")
        self.hook_adsb = hooks.get("adsb")
        self.log = log.getChild("adsb")

        self._alerted: Dict[str, float] = {}
        self._prev_squawk: Dict[str, str] = {}

    def _load(self) -> List[dict]:
        try:
            with open(self.json_path) as f:
                return json.load(f).get("aircraft", [])
        except Exception:
            return []

    def _build_embed(self, ac: dict, title: str, desc: str, color: int) -> dict:
        flight = ac.get("flight", "").strip() or "Unknown"
        hex_id = ac.get("hex", "???").upper()
        alt = ac.get("alt_baro", ac.get("alt_geom", "?"))
        gs = ac.get("gs", "?")
        track = ac.get("track", "?")
        lat = ac.get("lat")
        lon = ac.get("lon")
        squawk = ac.get("squawk", "?")
        reg = ac.get("r", "")
        ac_type = ac.get("t", ac.get("desc", ""))
        vert = ac.get("baro_rate", ac.get("geom_rate", None))

        ident = f"`{flight}`"
        if reg:
            ident += f" / `{reg}`"
        if ac_type:
            ident += f" ({ac_type})"

        alt_str = f"`{alt} ft`" if isinstance(alt, (int, float)) else f"`{alt}`"
        if isinstance(vert, (int, float)):
            arrow = "⬆️" if vert > 100 else ("⬇️" if vert < -100 else "➡️")
            alt_str += f" {arrow} {abs(vert):.0f} fpm"

        fields = [
            {"name": "✈️ Flight", "value": ident, "inline": True},
            {"name": "🔢 ICAO", "value": f"`{hex_id}`", "inline": True},
            {"name": "🔷 Squawk", "value": f"`{squawk}`", "inline": True},
            {"name": "📊 Altitude", "value": alt_str, "inline": True},
        ]
        if isinstance(gs, (int, float)):
            fields.append({"name": "💨 GS", "value": f"`{gs:.0f} kt`", "inline": True})
        if isinstance(track, (int, float)):
            fields.append({"name": "🧭 Track", "value": f"`{track:.0f}°`", "inline": True})
        if lat is not None and lon is not None:
            fields.append({"name": "📍 Position", "value": f"`{lat:.4f}, {lon:.4f}`", "inline": True})

        footer = f"DOT-VHF • {akst_now()} • tar1090: http://192.168.1.81:8504"
        return {"title": title, "description": desc, "color": color, "fields": fields,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": footer}}

    def check_once(self):
        now = time.time()
        for ac in self._load():
            hx = ac.get("hex", "")
            squawk = ac.get("squawk", "")
            if not hx or ac.get("messages", 0) < self.min_msgs:
                continue

            if squawk in self.emergency_squawks:
                prev = self._prev_squawk.get(hx, "")
                last = self._alerted.get(hx, 0)
                if squawk != prev or (now - last) > self.cooldown:
                    label, sq_desc, color = SQUAWK_INFO[squawk]
                    title = f"{label} — Squawk {squawk}"
                    embed = self._build_embed(ac, title, sq_desc, color)
                    if self.hook_emergency:
                        self.hook_emergency.post({"embeds": [embed]})
                    if self.hook_adsb:
                        self.hook_adsb.post({"embeds": [embed]})
                    self._alerted[hx] = now
                    self._prev_squawk[hx] = squawk
                    self.log.info("Emergency squawk %s: %s", squawk, hx)
            else:
                self._prev_squawk.pop(hx, None)

        # Prune old entries
        cutoff = now - self.cooldown * 3
        self._alerted = {k: v for k, v in self._alerted.items() if v > cutoff}

    def run(self, stop: threading.Event):
        self.log.info("ADS-B monitor started (poll=%ds)", self.poll_interval)
        while not stop.is_set():
            try:
                self.check_once()
            except Exception as e:
                self.log.exception("ADS-B error: %s", e)
            stop.wait(self.poll_interval)
        self.log.info("ADS-B monitor stopped")


# ─────────────────────────────────────────────────────────────────────────────
# VHF TRANSCRIPT MONITOR
# ─────────────────────────────────────────────────────────────────────────────

class VhfMonitor:
    """Tails today's transcript file, batches lines, posts to Discord."""

    def __init__(self, cfg: dict, hooks: dict, log: logging.Logger):
        vcfg = cfg.get("vhf", {})
        self.transcript_dir = Path(vcfg.get("transcript_dir", "/mnt/nvme/skybridge/transcripts"))
        self.poll_interval = vcfg.get("poll_interval", 15)
        self.batch_window = vcfg.get("batch_window", 30)
        self.max_lines = vcfg.get("max_lines_per_post", 8)
        self.freq = vcfg.get("frequency", "118.600 MHz")
        self.station = cfg.get("station", {})

        self.hook = hooks.get("vhf")
        self.log = log.getChild("vhf")

        self._pos: Dict[str, int] = {}  # filename → byte offset
        self._batch: List[str] = []
        self._batch_start: float = 0.0

    def _today_file(self) -> Path:
        return self.transcript_dir / (datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".txt")

    def _tail_new_lines(self) -> List[str]:
        f = self._today_file()
        if not f.exists():
            return []
        key = str(f)
        pos = self._pos.get(key, 0)
        with open(f, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            if size < pos:
                # File rolled
                pos = 0
            fh.seek(pos)
            new_bytes = fh.read()
            self._pos[key] = fh.tell()
        if not new_bytes:
            return []
        lines = new_bytes.decode("utf-8", errors="replace").splitlines()
        return [l for l in lines if l.strip()]

    def _flush(self):
        if not self._batch:
            return
        chunk = self._batch[:self.max_lines]
        self._batch = self._batch[self.max_lines:]

        text_block = "\n".join(f"> {l}" for l in chunk)
        embed = {
            "title": f"📻 VHF Transcript — {self.freq}",
            "description": text_block,
            "color": 0x00B0F4,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": f"DOT-VHF • {akst_now()} • Whisper STT"},
        }
        if self.hook:
            self.hook.post({"embeds": [embed]})
            self.log.info("Posted %d transcript lines to Discord", len(chunk))

    def check_once(self):
        now = time.time()
        new_lines = self._tail_new_lines()
        if new_lines:
            if not self._batch:
                self._batch_start = now
            self._batch.extend(new_lines)

        # Flush if batch window elapsed or batch is full
        if self._batch and (
            (now - self._batch_start) >= self.batch_window
            or len(self._batch) >= self.max_lines
        ):
            self._flush()

    def run(self, stop: threading.Event):
        self.log.info("VHF monitor started (poll=%ds, batch=%ds)", self.poll_interval, self.batch_window)
        while not stop.is_set():
            try:
                self.check_once()
            except Exception as e:
                self.log.exception("VHF error: %s", e)
            stop.wait(self.poll_interval)
        self.log.info("VHF monitor stopped")


# ─────────────────────────────────────────────────────────────────────────────
# STATION STATUS MONITOR
# ─────────────────────────────────────────────────────────────────────────────

class StatusMonitor:
    def __init__(self, cfg: dict, hooks: dict, log: logging.Logger):
        scfg = cfg.get("status", {})
        self.interval = scfg.get("interval", 900)
        self.services: List[str] = scfg.get("services", ["openwebrx","readsb","vhf-pipeline"])
        self.state_file = scfg.get("state_file", "/tmp/skybridge_discord_status_state.json")
        self.alert_on_change = scfg.get("alert_on_change", True)
        self.station = cfg.get("station", {})

        self.hook = hooks.get("status")
        self.log = log.getChild("status")
        self._last_post = 0.0
        self._prev_state: Dict[str, str] = load_state(self.state_file)

    def _svc_status(self, name: str) -> str:
        try:
            r = subprocess.run(["systemctl", "is-active", name],
                               capture_output=True, text=True, timeout=5)
            return r.stdout.strip()
        except Exception:
            return "unknown"

    def _adsb_count(self) -> int:
        try:
            with open("/run/readsb/aircraft.json") as f:
                return len(json.load(f).get("aircraft", []))
        except Exception:
            return -1

    def _transcript_count_today(self) -> int:
        try:
            path = f"/mnt/nvme/skybridge/transcripts/{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.txt"
            with open(path) as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    def _build_status_embed(self, state: Dict[str, str]) -> dict:
        STATUS_EMOJI = {"active": "🟢", "inactive": "🔴", "failed": "🔴",
                        "activating": "🟡", "unknown": "⚪"}
        svc_lines = []
        for svc, status in state.items():
            emoji = STATUS_EMOJI.get(status, "⚪")
            svc_lines.append(f"{emoji} **{svc}**: {status}")

        ac_count = self._adsb_count()
        tx_count = self._transcript_count_today()

        fields = [
            {"name": "🛠️ Services", "value": "\n".join(svc_lines) or "None", "inline": False},
            {"name": "✈️ ADS-B Aircraft", "value": str(ac_count) if ac_count >= 0 else "N/A", "inline": True},
            {"name": "📝 Transcripts Today", "value": str(tx_count), "inline": True},
            {"name": "📍 Station", "value": f"{self.station.get('name','DOT-VHF')} — {self.station.get('location','AK')}", "inline": True},
        ]

        all_ok = all(v == "active" for v in state.values())
        color = 0x2ECC71 if all_ok else (0xFF8C00 if any(v in ("activating","inactive") for v in state.values()) else 0xFF0000)
        title = "✅ Station Status — All Systems OK" if all_ok else "⚠️ Station Status — Degraded"

        return {"title": title, "color": color, "fields": fields,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": f"DOT-VHF • {akst_now()} • Skybridge Alaska"}}

    def check_once(self):
        now = time.time()
        state = {svc: self._svc_status(svc) for svc in self.services}

        # Check for changes
        changed = {k: v for k, v in state.items()
                   if self._prev_state.get(k) != v}

        if changed and self.alert_on_change:
            changes_text = "\n".join(
                f"{'🔴' if state[k]!='active' else '🟢'} **{k}**: {self._prev_state.get(k,'?')} → {state[k]}"
                for k in changed)
            embed = {"title": "⚡ Service State Change", "description": changes_text,
                     "color": 0xFF8C00,
                     "timestamp": datetime.now(timezone.utc).isoformat(),
                     "footer": {"text": f"DOT-VHF • {akst_now()}"}}
            if self.hook:
                self.hook.post({"embeds": [embed]})
            self.log.info("Service state change: %s", changed)

        # Scheduled heartbeat
        if (now - self._last_post) >= self.interval:
            embed = self._build_status_embed(state)
            if self.hook:
                self.hook.post({"embeds": [embed]})
            self._last_post = now
            self.log.info("Status heartbeat posted")

        self._prev_state = state
        save_state(self.state_file, state)

    def run(self, stop: threading.Event):
        self.log.info("Status monitor started (interval=%ds)", self.interval)
        while not stop.is_set():
            try:
                self.check_once()
            except Exception as e:
                self.log.exception("Status error: %s", e)
            stop.wait(60)  # Check every minute, post every interval
        self.log.info("Status monitor stopped")


# ─────────────────────────────────────────────────────────────────────────────
# MESHTASTIC MESH MONITOR
# ─────────────────────────────────────────────────────────────────────────────

class MeshMonitor:
    """Reads /tmp/skybridge_mesh_status.json written by meshtastic_status.py."""

    def __init__(self, cfg: dict, hooks: dict, log: logging.Logger):
        mcfg = cfg.get("mesh", {})
        self.status_json = mcfg.get("status_json", "/tmp/skybridge_mesh_status.json")
        self.poll_interval = mcfg.get("poll_interval", 60)
        self.alert_threshold = mcfg.get("alert_threshold", 1800)
        self.state_file = mcfg.get("state_file", "/tmp/skybridge_discord_mesh_state.json")
        self.station = cfg.get("station", {})

        self.hook = hooks.get("mesh")
        self.log = log.getChild("mesh")
        self._prev: dict = load_state(self.state_file)
        self._alerted_silent: Set[str] = set()

    def _load_mesh(self) -> dict:
        try:
            with open(self.status_json) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def check_once(self):
        data = self._load_mesh()
        if not data:
            return

        nodes = data.get("nodes", {})
        now_ts = time.time()

        for node_id, info in nodes.items():
            prev_info = self._prev.get(node_id, {})
            name = info.get("long_name", info.get("short_name", node_id))
            last_heard = info.get("last_heard", 0)
            snr = info.get("snr", None)
            battery = info.get("battery_level", None)

            # Node came online (wasn't seen before)
            if node_id not in self._prev:
                fields = [
                    {"name": "📡 Node ID", "value": f"`{node_id}`", "inline": True},
                    {"name": "🔋 Battery", "value": f"{battery}%" if battery is not None else "?", "inline": True},
                    {"name": "📶 SNR", "value": f"{snr} dB" if snr is not None else "?", "inline": True},
                ]
                embed = {"title": f"🟢 Mesh Node Online: {name}",
                         "color": 0x2ECC71, "fields": fields,
                         "timestamp": datetime.now(timezone.utc).isoformat(),
                         "footer": {"text": f"DOT-VHF Mesh • {akst_now()}"}}
                if self.hook:
                    self.hook.post({"embeds": [embed]})
                self.log.info("Mesh node online: %s (%s)", name, node_id)
                self._alerted_silent.discard(node_id)

            # Node went silent
            elif last_heard and (now_ts - last_heard) > self.alert_threshold:
                if node_id not in self._alerted_silent:
                    silent_for = int((now_ts - last_heard) / 60)
                    embed = {"title": f"🔴 Mesh Node Silent: {name}",
                             "description": f"No signal for **{silent_for} minutes**",
                             "color": 0xFF0000,
                             "timestamp": datetime.now(timezone.utc).isoformat(),
                             "footer": {"text": f"DOT-VHF Mesh • {akst_now()}"}}
                    if self.hook:
                        self.hook.post({"embeds": [embed]})
                    self._alerted_silent.add(node_id)
                    self.log.warning("Mesh node silent: %s (%dmin)", name, silent_for)

            # Battery low alert
            if battery is not None and battery < 20:
                prev_bat = prev_info.get("battery_level", 100)
                if prev_bat >= 20:  # Just crossed threshold
                    embed = {"title": f"🪫 Low Battery: {name}",
                             "description": f"Battery at **{battery}%** — node may go offline soon",
                             "color": 0xFF8C00,
                             "timestamp": datetime.now(timezone.utc).isoformat(),
                             "footer": {"text": f"DOT-VHF Mesh • {akst_now()}"}}
                    if self.hook:
                        self.hook.post({"embeds": [embed]})
                    self.log.warning("Low battery: %s (%d%%)", name, battery)

        # Detect nodes that dropped out
        for node_id in list(self._prev.keys()):
            if node_id not in nodes:
                name = self._prev[node_id].get("long_name", node_id)
                embed = {"title": f"⚫ Mesh Node Lost: {name}",
                         "description": "Node no longer seen in mesh status",
                         "color": 0x95A5A6,
                         "timestamp": datetime.now(timezone.utc).isoformat(),
                         "footer": {"text": f"DOT-VHF Mesh • {akst_now()}"}}
                if self.hook:
                    self.hook.post({"embeds": [embed]})
                self.log.info("Mesh node lost: %s", name)

        self._prev = nodes
        save_state(self.state_file, {k: dict(v) for k, v in nodes.items()})

    def run(self, stop: threading.Event):
        self.log.info("Mesh monitor started (poll=%ds)", self.poll_interval)
        while not stop.is_set():
            try:
                self.check_once()
            except Exception as e:
                self.log.exception("Mesh error: %s", e)
            stop.wait(self.poll_interval)
        self.log.info("Mesh monitor stopped")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Skybridge Alaska Discord Webhook Integration")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    log = setup_logging(cfg)
    log.info("Skybridge Discord Integration starting up")

    # Build hooks
    wh = cfg.get("webhooks", {})
    hooks = {k: Hook(url, k, log) for k, url in wh.items()}

    # Warn about placeholders
    for name, hook in hooks.items():
        if not hook.is_live():
            log.warning("Webhook '%s' has placeholder URL — posts will be skipped. "
                        "Fill in config.yaml to enable.", name)

    stop = threading.Event()

    monitors = [
        AdsbMonitor(cfg, hooks, log),
        VhfMonitor(cfg, hooks, log),
        StatusMonitor(cfg, hooks, log),
        MeshMonitor(cfg, hooks, log),
    ]

    threads = []
    for m in monitors:
        t = threading.Thread(target=m.run, args=(stop,), daemon=True)
        t.start()
        threads.append(t)

    # Startup announcement
    station = cfg.get("station", {})
    startup_embed = {
        "title": "🚀 Skybridge Discord Integration Online",
        "description": (
            f"**{station.get('name','DOT-VHF')}** — {station.get('location','Anchorage, AK')}\n"
            "Monitoring: ADS-B emergencies · VHF transcripts · Station status · Mesh nodes"
        ),
        "color": 0x00B0F4,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Skybridge Alaska • {akst_now()}"},
    }
    hooks.get("status", Hook("", "status", log)).post({"embeds": [startup_embed]})

    import signal as sig
    def shutdown(signum, frame):
        log.info("Shutdown signal received")
        stop.set()

    sig.signal(sig.SIGTERM, shutdown)
    sig.signal(sig.SIGINT, shutdown)

    log.info("All monitors running. Waiting for stop signal...")
    stop.wait()
    log.info("Stopping all monitors...")
    for t in threads:
        t.join(timeout=10)
    log.info("Skybridge Discord Integration stopped")


if __name__ == "__main__":
    main()
