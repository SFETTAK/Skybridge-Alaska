# SkyBridge Alaska

**Aviation safety mesh network for general aviation in Alaska.**

SkyBridge is a system built by Alaska DOT&PF that captures VHF aviation radio, ADS-B aircraft transponders, and UAT weather data using low-cost software-defined radios, transcribes pilot voice communications with AI, and distributes critical flight information over a Meshtastic LoRa mesh network — no cell towers or satellites required.

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
|   |   |-- kneeboard.py           Pilot kneeboard: 12-layer moving map with ADS-B,
|   |   |                           weather, VHF transcripts, MWOS cameras
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
|   |-- nasao-2025/          <-- NASAO presentation materials and research
|   +-- TAIGA_ASN1_Reference.pdf   NASA TAIGA protocol spec
|
|-- app/                     <-- Mobile app scaffold (React Native, early stage)
|-- hardware/                <-- Hardware specifications and cost targets
|-- protocol/                <-- TAIGA ASN.1 protocol documentation
|-- ARCHITECTURE.md          <-- System design overview
|-- JOURNAL.md               <-- Project decision log
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
| RTL-SDR Blog V4 | VHF aviation radio (118-137 MHz) |
| RTL-SDR FlyCatcher x2 | ADS-B 1090 MHz + UAT 978 MHz |
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
- Tablet-optimized 12-layer moving map designed for cockpit use
- ADS-B traffic (local + statewide), VHF transcripts, weather overlays
- METAR dots (30 stations), SIGMETs/AIRMETs, PIREPs, G-AIRMETs, volcanic ash, NWS alerts
- MWOS automated weather stations with camera images (Montis Corp API)
- GPS tracking via HTTPS (self-signed cert on port 8443)

**Station Monitoring**
- Live status dashboard showing service health, NVMe SMART, aircraft count, latest transcripts
- VHF Review web UI for browsing archived audio and transcripts (port 8082)
- Automated backup to central server every 6 hours via rclone
- Log rotation, NVMe health checks

### Services Running (13)

| Service | Port | Purpose |
|---------|------|---------|
| OpenWebRX | 8073 | Web SDR spectrum viewer |
| Status Dashboard | 8080 | Station health metrics |
| VHF Review | 8082 | Audio/transcript browser |
| Kneeboard | 8443 (HTTPS) | Pilot 12-layer moving map |
| tar1090 | 8504 | ADS-B map (local receiver) |
| tar1090-combo | 8505 / 8506 (HTTPS) | ADS-B map (local + statewide) |
| readsb | 30002-30005 | ADS-B 1090 decoder |
| dump978-fa | 30978 | UAT 978 decoder |
| adsb-combine | -- | ADS-B feed merger (background) |
| vhf-pipeline | -- | VHF transcription (background) |
| lighttpd | -- | Web server / HTTPS proxy |
| nvme-backup | -- | rclone sync every 6 hours |

---

## The Problem

Alaska pilots face a **36x higher fatality rate** than the average US worker. Remote areas covering 80% of the state have no reliable weather updates, NOTAMs, or VHF radio coverage. Traditional ground station infrastructure costs $200K+ per site. Terrain maps contain errors up to 263 feet — directly contributing to fatal crashes.

SkyBridge addresses this with a **$50-per-pilot mesh network** backed by AI-powered ground stations that cost under $500 each.

## Technology

- **SDR**: RTL-SDR dongles for multi-band reception (VHF, 1090 MHz, 978 MHz)
- **DSP**: OpenWebRX + csdr for signal processing
- **STT**: OpenAI Whisper (faster-whisper, CPU int8 quantization)
- **Mesh**: Meshtastic LoRa (902-928 MHz ISM band, 50+ mile range at altitude)
- **Protocol**: NASA TAIGA ASN.1 for 80% data compression
- **Platform**: Raspberry Pi 5 + NVMe, all open source

## Current Status (March 2026)

The DOT-VHF ground station is fully operational in Anchorage with 13 services running on a Raspberry Pi 5. Key capabilities deployed and working:

- VHF voice pipeline with adaptive squelch, speech quality gating, and ADS-B-correlated transcripts
- Aviation lexicon post-processor with 11 number normalizers tuned for PANC ATC
- Statewide ADS-B coverage merging local RTL-SDR receiver with ADSB.fi (500nm, ~100-200 aircraft)
- Pilot kneeboard web app with 12 map layers including weather, traffic, MWOS cameras, and VHF transcripts
- VHF audio review interface for browsing archived recordings and transcripts
- HTTPS endpoints with GPS tracking for cockpit tablet use
- Automated NVMe archival and remote backup

## Project Status

| Milestone | Status |
|-----------|--------|
| Ground station hardware deployed | Done |
| VHF pipeline (channelized demod + adaptive squelch + archive) | Done |
| Whisper STT with aviation lexicon post-processing | Done |
| ADS-B correlation (callsign extraction + position annotation) | Done |
| ADS-B 1090 + 978 UAT tracking (local) | Done |
| ADSB.fi statewide feed integration (500nm coverage) | Done |
| tar1090-combo merged ADS-B map | Done |
| Pilot kneeboard (12-layer moving map) | Done |
| MWOS weather station integration (Montis Corp) | Done |
| VHF review web UI | Done |
| HTTPS/GPS support for cockpit tablets | Done |
| Status dashboard + monitoring | Done |
| NVMe archival + backup automation | Done |
| Test suite (17 tests, no hardware needed) | Done |
| Meshtastic mesh relay | Hardware pending |
| Mobile app (React Native) | Early scaffold |
| Multi-station deployment | Planning |
| NASA TAIGA protocol encoding | Documented, not yet implemented |

## Quick Start (Ground Station)

See the [Operational Runbook](docs/project-handoff/05-OPERATIONAL-RUNBOOK.md) for full setup and troubleshooting.

```bash
# SSH into the station
ssh blastly@192.168.1.81

# Check all services
systemctl status openwebrx readsb dump978-fa tar1090 tar1090-combo \
  adsb-combine kneeboard status-dashboard vhf-pipeline lighttpd

# Run the test suite (no hardware needed)
source ~/vhf-pipeline-venv/bin/activate
python ~/scripts/test-pipeline.py

# View live interfaces
open http://192.168.1.81:8080      # Status dashboard
open http://192.168.1.81:8082      # VHF review
open https://192.168.1.81:8443     # Pilot kneeboard (GPS)
open http://192.168.1.81:8504      # ADS-B map (local)
open https://192.168.1.81:8506     # ADS-B map (statewide + GPS)
open http://192.168.1.81:8073      # OpenWebRX SDR
```

## Documentation

| Document | Description |
|----------|-------------|
| [Ground Station README](ground-station/README.md) | Service inventory, port map, HTTPS/GPS setup |
| [Kneeboard Guide](docs/kneeboard-guide.md) | 12-layer map, API endpoints, MWOS integration |
| [ADS-B Integration](docs/adsb-integration.md) | Local + ADSB.fi merge architecture, tar1090-combo |
| [Hardware Inventory](docs/project-handoff/01-HARDWARE-INVENTORY.md) | Every component, serial number, and cost |
| [Software Inventory](docs/project-handoff/02-SOFTWARE-INVENTORY.md) | All services, packages, and versions |
| [System Architecture](docs/project-handoff/03-SYSTEM-ARCHITECTURE.md) | Data flow diagrams and service dependencies |
| [Configuration Reference](docs/project-handoff/04-CONFIGURATION-REFERENCE.md) | Every config file, parameter, and port |
| [Operational Runbook](docs/project-handoff/05-OPERATIONAL-RUNBOOK.md) | Access, monitoring, troubleshooting, maintenance |
| [NASAO 2025 Materials](docs/nasao-2025/) | Presentation research and pitch documents |
| [Project Journal](JOURNAL.md) | Chronological decision log |

## License

Dual licensed: **AGPL-3.0** for public/nonprofit use, commercial license available.

Free for: State/federal agencies, SAR organizations, educational institutions, small Part 135 operators (<5 aircraft), Alaska-serving nonprofits.

See [LICENSE.md](LICENSE.md) for full terms.

## Contact

Steven Fett, Alaska DOT&PF — [steven.fett@alaska.gov](mailto:steven.fett@alaska.gov)
Ryan Marlow, Alaska DOT&PF — [ryan.marlow@alaska.gov](mailto:ryan.marlow@alaska.gov)

**Repository**: https://github.com/SFETTAK/Skybridge-Alaska
