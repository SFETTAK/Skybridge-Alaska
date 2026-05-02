# SkyBridge Alaska

**Low-cost aviation safety network for general aviation in Alaska.**

Alaska pilots are **36x more likely to die** than the average US worker. Eighty percent of the state has no reliable weather updates, no VHF radio coverage, and terrain maps with errors up to 263 feet. Traditional ground-station infrastructure costs $200K+ per site. SkyBridge changes the math.

A single **$470 Raspberry Pi ground station** with low-cost SDR dongles monitors local aviation VHF radio, transcribes voice traffic with on-device speech recognition (ML, not LLM), receives ADS-B locally, and merges that with the public ADSB.fi statewide feed for ~500 nm of statewide aircraft visibility. It pulls live weather from NOAA and automated weather-camera stations and renders the combined picture to a **free, GPS-enabled moving map on any pilot's tablet** within network range.

The first station, designated **DOT-VHF**, is operational at Alaska DOT&PF in Anchorage. A **$50 Meshtastic LoRa radio** is wired into the Pi and the architecture for multi-node mesh extension (50+ mile range at altitude, peer-to-peer, off-grid) is documented in the [paper-lab](paper-lab/). The mesh carries compact text and numeric observations only; imagery, audio, and chart tiles are internet-delivered. Multi-station deployment is the next phase.

> ## Status: in active development
>
> SkyBridge is a working research project, not a finished product.
>
> - **Operational today**: one ground station in Anchorage. Pilots in the Anchorage Bowl with network connectivity can use the kneeboard moving map.
> - **In development**: multi-station mesh deployment, mobile app, sensor expansion, NOTAM/TFR feeds, TAIGA encoding integration. See the [milestone table](#project-status) below for what's done, pending, and blocked.
> - **What this is**: a *supplementary* tool intended to give pilots more situational awareness in places where FAA-approved sources have coverage gaps. Better than the nothing that currently exists in 80% of Alaska.
> - **What this is not**: an FAA-certified or TSO-approved system. Pilots make go/no-go decisions using FAA-approved sources; SkyBridge sits alongside, not in front of, those sources.
>
> Outside the Anchorage Bowl, this is currently a documented architecture, not a deployed service. If you fly out of Bethel, Fairbanks, Juneau, or anywhere else, SkyBridge does not yet have hardware on the ground in your region. The roadmap is open and contributions are welcome.

![Aviation Mesh Network](docs/SDR-Mesh-GeneralAviation.png)

---

## What's Here

This repository contains the complete SkyBridge Alaska project: deployed ground station code, system configurations, project documentation, and reference materials.

```
Skybridge-Alaska/
|
|-- ground-station/          <-- Deployed code running on DOT-VHF (Raspberry Pi 5)
|   |-- scripts/
|   |   |-- vhf-pipeline.py        VHF radio -> channelized AM demod -> adaptive squelch
|   |   |                           -> Whisper STT -> ADS-B correlation -> Meshtastic
|   |   |-- aviation_lexicon.py    ATC vocabulary corrections + 11 number normalizers
|   |   |-- kneeboard.py           Pilot kneeboard: 17-layer moving map with ADS-B,
|   |   |                           weather, VHF transcripts, MWOS cameras, FAA comms,
|   |   |                           server-side trails, rewind scrubber, hybrid icons
|   |   |-- adsb-combine.py        Merges local readsb + ADSB.fi statewide feed
|   |   |-- vhf-review.py          Web UI for archived VHF audio + transcripts
|   |   |-- test-pipeline.py       17-test validation suite (runs without hardware)
|   |   |-- status-dashboard.py    Live HTML station health dashboard
|   |   +-- nvme-backup.sh         Automated rclone backup to central server
|   |-- systemd/                   13 service/timer unit files
|   +-- config/                    OpenWebRX, readsb, tar1090, SSL, lighttpd configs
|
|-- docs/
|   |-- project-handoff/     <-- Full project documentation package
|   |   |-- 00-INDEX.md            Master index
|   |   |-- 01-HARDWARE-INVENTORY.md
|   |   |-- 02-SOFTWARE-INVENTORY.md
|   |   |-- 03-SYSTEM-ARCHITECTURE.md
|   |   |-- 04-CONFIGURATION-REFERENCE.md
|   |   +-- 05-OPERATIONAL-RUNBOOK.md
|   |-- aviation-safety-context.md  <-- Statistics, citations, stakeholder talking points
|   +-- TAIGA_ASN1_Reference.pdf    NASA TAIGA protocol spec
|
|-- app/                     <-- Mobile app scaffold (React Native, early stage)
|-- hardware/                <-- Hardware specifications and cost targets
|-- protocol/                <-- TAIGA ASN.1 protocol documentation
|-- ARCHITECTURE.md          <-- System design overview
|-- LICENSE.md               <-- Dual AGPL-3.0 / Commercial
+-- CONTRIBUTING.md
```

---

## DOT-VHF Ground Station

The first deployed ground station, running on a Raspberry Pi 5 in Anchorage, Alaska.

### Hardware (~$470 deployed)

| Component | Purpose |
|-----------|---------|
| Raspberry Pi 5 (16 GB) | Compute |
| 2 TB NVMe SSD | Audio/ADS-B/transcript archive |
| Multi-band SDR dongles (3) | VHF aviation receive + ADS-B 1090 MHz + UAT 978 MHz (specific models per deployment) |
| Meshtastic LoRa radio | Mesh network relay (planned) |

### What It Does

**VHF Voice Pipeline** (`ground-station/scripts/vhf-pipeline.py`)
1. Receives IQ samples from RTL-SDR via OpenWebRX's rtl_tcp interface
2. Channelized AM demodulation: freq shift, LPF, decimate 2.4M to 16kHz (Whisper-native)
3. Adaptive squelch: auto-calibrating noise floor, 8 dB SNR threshold, EMA tracking
4. Speech quality gate: peak/mean ratio + energy variance rejects noise segments
5. Archives voice segments as FLAC to NVMe
6. Transcribes with Whisper base.en (faster-whisper, CPU int8), PANC-tuned prompt
7. Post-processes with aviation_lexicon.py: phonetic fixes, ATC corrections, 11 number normalizers, hallucination filter
8. Correlates callsigns against live ADS-B — annotates transcripts with aircraft position
9. Publishes to Meshtastic mesh network + logs to NVMe

**ADS-B Tracking**
- 1090 MHz Extended Squitter via readsb with globe-history archival
- 978 MHz UAT via dump978-fa for GA traffic and FIS-B weather
- tar1090 local map (receiver-only, port 8504)
- **adsb-combine.py** merges local readsb + ADSB.fi statewide feed (two 250nm circles covering 500nm of Alaska)
- **tar1090-combo** serves the merged view (port 8505 HTTP, 8506 HTTPS with GPS)

**Pilot Kneeboard** (`ground-station/scripts/kneeboard.py`)
- Tablet-optimized 17-layer moving map designed for cockpit use
- Server-side aircraft trails (24 h retention, per-segment altitude coloring) and heading-vector lookahead
- ADS-B traffic (local + statewide), VHF transcripts, weather overlays
- METAR dots (30 stations), SIGMETs/AIRMETs, PIREPs, G-AIRMETs, volcanic ash, NWS alerts
- VFR Sectional + IFR Low/High Enroute chart overlays (FAA via ArcGIS)
- MWOS automated weather stations — 14 stations × 4 cameras each (Montis Corp API)
- FAA Comms: Center Weather Advisories (CWAs), Temporary Flight Restrictions (TFRs); NOTAMs scaffolded (requires FAA NOTAM API key — see [docs/notam-roadmap.md](docs/notam-roadmap.md))
- Aircraft icon system: silhouettes by category (GA single, GA twin, turboprop, jet, widebody, helicopter, military), wake-class sizing, hybrid altitude-fill / operator-class outline coloring
- Live tunables in the layer panel: icon size & brightness, lookahead minutes, trail render length, rewind scrubber (0–60 min), polygon fill opacity, panel background opacity
- Stale-fade on aircraft icons during feed gaps (60 s server-side grace window)
- Web served on `:8083` (HTTP, dev fork on `:8084`); HTTPS/GPS via lighttpd on `:8443`

**Station Monitoring**
- Live status dashboard showing service health, NVMe SMART, aircraft count, latest transcripts
- VHF Review web UI for browsing archived audio and transcripts (port 8082)
- Automated backup to central server every 6 hours via rclone
- Log rotation, NVMe health checks

### Services Running (40+)

Web-facing services (HTTP/HTTPS or WebSocket):

| Service | Port | Purpose |
|---------|------|---------|
| Status Dashboard | 8080 | Station health metrics |
| VHF Review | 8082 | Audio/transcript browser |
| **Kneeboard (production)** | **8083 (HTTP)** | Pilot 17-layer moving map |
| **Kneeboard (dev fork)** | **8084 (HTTP)** | UX-experiment fork (FAA comms, server-side trails, scrubber, hybrid icons) |
| Lighttpd dashboard | 8443 (HTTPS) | DOT-VHF station dashboard |
| tar1090 | 8504 | ADS-B map (local receiver) |
| tar1090-combo | 8505 / 8506 (HTTPS) | ADS-B map (local + statewide) |
| Blaze Claw fleet cockpit | 8090 (VPN-only) | Mesh-fleet admin |
| OpenClaw gateway | 18789 | Local agent orchestration (WebSocket + HTTP control UI) |

Binary / non-web services:

| Service | Port | Purpose |
|---------|------|---------|
| rtl-tcp-vhf | 1235 | rtl_tcp IQ server feeding the VHF pipeline |
| readsb | 30001-30005, 30104 | ADS-B 1090 decoder feeds (BEAST/Beast/SBS) |
| dump978-fa | 30978 | UAT 978 decoder |
| skyaware978 | 30978 | UAT web-map JSON writer |
| VPN overlay | — | Encrypted mesh VPN for operator access |
| OpenClaw browser-control | 18791 (loopback) | Plugin server, token auth |

Background services (systemd, no public port):

| Service | Purpose |
|---------|---------|
| vhf-pipeline | VHF transcription (Whisper STT → archive → Meshtastic) |
| adsb-combine | Merges local readsb + ADSB.fi statewide feed |
| dump978-fa | UAT decoding for FIS-B |
| blaze-claw-admin / -alerts / -ingester | Mesh fleet cockpit + 60s rule alerts + ingester (subscribes to BLAZE radio over TCP, logs packets to state.db) |
| skybridge-discord | Webhook integration for ADS-B / VHF / status / mesh updates |
| skybridge-sync | Periodic VHF-audio sync to DOTHQ central |
| openclaw-gateway | Local AI agent gateway (Ollama @ dothq) |
| dr-backup, skybridge-backup | DR + 6 h rclone snapshots |
| adsb-archive-cleanup.timer | Daily 02:00 prune of `/mnt/nvme/skybridge/adsb/` (24 h retention) |

The Pi is reachable on its station LAN (configured per-deployment) and over the operator VPN overlay. Specific addressing is configured at install time and is not published.

---

## The Problem

Alaska pilots face a **36x higher fatality rate** than the average US worker. Remote areas covering 80% of the state have no reliable weather updates, NOTAMs, or VHF radio coverage. Traditional ground station infrastructure costs $200K+ per site. Terrain maps contain errors up to 263 feet — directly contributing to fatal crashes.

SkyBridge's design response: a **$50-per-pilot mesh network** backed by ~$500 ground stations running open-source software. The first ground station is operational; the multi-node mesh extension is the next phase.

## Technology

- **SDR**: RTL-SDR dongles for multi-band reception (VHF, 1090 MHz, 978 MHz)
- **DSP**: OpenWebRX + csdr for signal processing
- **STT**: OpenAI Whisper (faster-whisper, CPU int8 quantization)
- **Mesh**: Meshtastic LoRa (902-928 MHz ISM band, 50+ mile range at altitude — single radio wired in; multi-node deployment pending)
- **Protocol**: NASA TAIGA ASN.1 for ~80% data compression (documented and prototyped in the [paper-lab](paper-lab/); not yet integrated in production)
- **Platform**: Raspberry Pi 5 + NVMe, all open source

## Current Status (April 2026)

The DOT-VHF ground station is fully operational in Anchorage with 40+ systemd services running on a Raspberry Pi 5. Beyond the original VHF + ADS-B + kneeboard stack, recent additions:

**Receive / capture**
- VHF voice pipeline with adaptive squelch, speech quality gating, and ADS-B-correlated transcripts
- Aviation lexicon post-processor with 11 number normalizers tuned for PANC ATC
- Statewide ADS-B coverage merging local RTL-SDR receiver with ADSB.fi (500 nm, ~100-200 aircraft)
- Local readsb globe-history archive at `/mnt/nvme/skybridge/adsb/` with daily 24 h retention prune

**Pilot kneeboard (17-layer moving map)**
- Server-side aircraft trails with 24 h retention, per-segment altitude coloring, in-memory cache + on-disk persistence
- Heading-vector lookahead (3-min default, slider-adjustable 1–15 min) — line length scales with groundspeed
- Aircraft icon system with category silhouettes (GA single, GA twin, turboprop, jet, widebody, helicopter, military), wake-class sizing, hybrid altitude-fill / operator-class outline coloring
- Stale-fade on aircraft during feed gaps (60 s server-side grace window)
- Rewind scrubber bar (0–60 min) for timeline-style playback of recent traffic
- VFR Sectional + IFR Low/High Enroute chart overlays
- FAA Comms layer: Center Weather Advisories (live polygons) + TFRs (parsed from FAA TFR API; polygon shapes pending API access)
- MWOS expansion to 14 stations × 4 cameras each (was 7), bad-coords filtered server-side
- Live-tunable sliders: icon size, brightness, lookahead, trail render length, rewind, polygon fill opacity, panel background opacity
- 4 collapsible right-side sections (closest METAR + scrollable FAA-comms list + docked MWOS), 1-button collapse for the entire left column (VHF + Blaze chat)

**Connectivity / failover**
- Pi reachable on its station LAN; specific addressing is per-deployment
- Operator access via VPN overlay

**Agent / outbound**
- OpenClaw gateway integrating Telegram bot ("Blaze") + Discord webhooks
- Daily pilot briefing cron (06:00 AKST) + system-health pings + gap analyzer

**DOT&PF Research Need 2027–2028 — proposal submitted**
A research-program funding proposal was submitted on 2026-04-30 to expand SkyBridge from one ground station to a multi-node Alaska mesh validation. Contact the project maintainers for details.

### Known limitations / pending external dependencies
- **NOTAMs** require an FAA NOTAM API key — see [docs/notam-roadmap.md](docs/notam-roadmap.md). Endpoint `/api/notams` is scaffolded; set `FAA_NOTAM_KEY` env var on the kneeboard service to enable.
- **TFR polygon shapes** are not exposed by the public FAA TFR API — pins land at parsed-from-description locations until either FAA exposes shape data or a paid alternative (FlightAware AeroAPI / ADSBExchange) is adopted.
- **Meshtastic/LoRa hardware** wired into Blaze ingester, but full node-mesh deployment (the 7-site research-program proposal) is the next phase.
- **Whisper transcription quality** depends on antenna gain — currently noise-floor limited, antenna repositioning planned alongside outdoor PVC enclosure deployment.

## Project Status

| Milestone | Status |
|-----------|--------|
| Ground station hardware deployed | ✅ Done |
| VHF pipeline (channelized demod + adaptive squelch + archive) | ✅ Done |
| Whisper STT with aviation lexicon post-processing | ✅ Done |
| ADS-B correlation (callsign extraction + position annotation) | ✅ Done |
| ADS-B 1090 + 978 UAT tracking (local) | ✅ Done |
| ADSB.fi statewide feed integration (500 nm coverage) | ✅ Done |
| tar1090-combo merged ADS-B map | ✅ Done |
| Pilot kneeboard — moving map (now 17 layers) | ✅ Done |
| MWOS weather station integration — 14 stations × 4 cameras | ✅ Done |
| VHF review web UI | ✅ Done |
| HTTPS/GPS support for cockpit tablets | ✅ Done |
| Status dashboard + monitoring | ✅ Done |
| NVMe archival + backup automation | ✅ Done |
| Discord webhook integration (ADS-B / VHF / status) | ✅ Done |
| OpenClaw gateway integration ("Blaze" agent on Telegram) | ✅ Done |
| Static-IP failover alias on station LAN | ✅ Done |
| Server-side aircraft trail collection (24 h retention) | ✅ Done |
| Aircraft persistence + stale-fade during feed gaps | ✅ Done |
| Hybrid icon system (silhouettes by category, wake-class sizing) | ✅ Done |
| FAA Comms layer (CWAs live, TFRs scaffolded, NOTAMs API-key-gated) | ✅ Done |
| VFR Sectional + IFR Low/High chart overlays | ✅ Done |
| Live-tunable UI sliders (size, brightness, lookahead, opacity, etc.) | ✅ Done |
| Rewind scrubber bar | ✅ Done |
| Test suite (17 tests, no hardware needed) | ✅ Done |
| FAA NOTAM API key registration | ⏳ Pending — requires `api.faa.gov` registration |
| TFR polygon shapes | ⏳ Blocked — not in FAA public API; needs paid feed (e.g. AeroAPI) |
| FIS-B uplink decoding (978 UAT graphical NOTAMs / NEXRAD) | ⏳ Planned |
| Meshtastic mesh relay (LoRa) | ⏳ Hardware connected; full multi-node deploy pending |
| Mobile app (React Native) | ⏳ Early scaffold |
| Multi-station deployment | ⏳ Planning |
| NASA TAIGA protocol encoding | 📋 Documented, not yet implemented |
| Outdoor PVC enclosure + antenna upgrade | 📋 Planned |

## Quick Start (Ground Station)

Operator-only. Specific addressing and credentials are configured per-deployment and not published. See the [Operational Runbook](docs/project-handoff/05-OPERATIONAL-RUNBOOK.md) for full setup and troubleshooting once you have authorized access.

```bash
# SSH into the station
ssh <operator>@<station-host>

# Check core services
systemctl status openwebrx readsb dump978-fa tar1090 tar1090-combo \
  adsb-combine kneeboard status-dashboard vhf-pipeline lighttpd

# Run the test suite (no hardware needed)
source ~/vhf-pipeline-venv/bin/activate
python ~/scripts/test-pipeline.py

# Live interfaces are exposed on standard ports per the service table above.
# Operators with VPN access reach them via the station hostname.
```

## Documentation

| Document | Description |
|----------|-------------|
| [Ground Station README](ground-station/README.md) | Service inventory, port map, HTTPS/GPS setup |
| [Kneeboard Guide](docs/kneeboard-guide.md) | 17-layer map, API endpoints, MWOS integration |
| [ADS-B Integration](docs/adsb-integration.md) | Local + ADSB.fi merge architecture, tar1090-combo |
| [NOTAM / FAA Comms Roadmap](docs/notam-roadmap.md) | What FAA comms exist on the dashboard today, what's blocked, and the unlock paths |
| [Hardware Inventory](docs/project-handoff/01-HARDWARE-INVENTORY.md) | Every component, serial number, and cost |
| [Software Inventory](docs/project-handoff/02-SOFTWARE-INVENTORY.md) | All services, packages, and versions |
| [System Architecture](docs/project-handoff/03-SYSTEM-ARCHITECTURE.md) | Data flow diagrams and service dependencies |
| [Configuration Reference](docs/project-handoff/04-CONFIGURATION-REFERENCE.md) | Every config file, parameter, and port |
| [Operational Runbook](docs/project-handoff/05-OPERATIONAL-RUNBOOK.md) | Access, monitoring, troubleshooting, maintenance |
| [Aviation Safety Context](docs/aviation-safety-context.md) | Statistics, citations, stakeholder talking points |
| [Paper-lab](paper-lab/) | Protocol architecture, network topology, encoding, QoS, versioning, scenarios, cert path |

## License

Dual-licensed under **AGPL-3.0** and a separate commercial license. See [LICENSE.md](LICENSE.md) for full terms, including the categories of users for which the commercial-license option is intended and which are eligible for free use under AGPL.

## Contact

Steven Fett, Alaska DOT&PF — [steven.fett@alaska.gov](mailto:steven.fett@alaska.gov)
Ryan Marlow, Alaska DOT&PF — [ryan.marlow@alaska.gov](mailto:ryan.marlow@alaska.gov)

**Repository**: https://github.com/SFETTAK/Skybridge-Alaska
