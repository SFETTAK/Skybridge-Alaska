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
|   |   |-- vhf-pipeline.py        VHF radio -> AM demod -> Whisper STT -> Meshtastic
|   |   |-- test-pipeline.py       17-test validation suite (runs without hardware)
|   |   |-- status-dashboard.py    Live HTML station health dashboard
|   |   +-- nvme-backup.sh         Automated rclone backup to central server
|   |-- systemd/                   All service and timer unit files
|   +-- config/                    OpenWebRX, readsb, tar1090, SSH, fail2ban configs
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
2. Demodulates AM aviation audio (centered on 121.8 MHz guard/unicom)
3. Detects voice transmissions with energy-based VAD
4. Archives voice segments as FLAC to NVMe
5. Transcribes speech using Whisper (faster-whisper, tiny.en model, CPU)
6. Publishes transcripts to Meshtastic mesh network
7. Logs all transcripts to NVMe for historical record

**ADS-B Tracking**
- 1090 MHz Extended Squitter via readsb with globe-history archival
- 978 MHz UAT via dump978-fa for GA traffic and FIS-B weather
- Web map at tar1090 combining both feeds

**Station Monitoring**
- Live status dashboard showing service health, NVMe SMART, aircraft count, latest transcripts
- Automated backup to central server every 6 hours via rclone
- Log rotation, NVMe health checks

### Services Running

| Service | Port | Purpose |
|---------|------|---------|
| OpenWebRX | 8073 | Web SDR spectrum viewer |
| tar1090 | 8504 | ADS-B aircraft tracking map |
| Status Dashboard | 8080 | Station health metrics |
| readsb | 30002-30005 | ADS-B decoder (network feeds) |
| dump978-fa | 30978 | UAT decoder |
| vhf-pipeline | — | VHF transcription (background) |

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

## Project Status

| Milestone | Status |
|-----------|--------|
| Ground station hardware deployed | Done |
| VHF pipeline (demod + VAD + archive) | Done |
| Whisper STT integration | Done |
| ADS-B 1090 + 978 UAT tracking | Done |
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
systemctl status openwebrx readsb dump978-fa tar1090 status-dashboard vhf-pipeline

# Run the test suite (no hardware needed)
source ~/vhf-pipeline-venv/bin/activate
python ~/scripts/test-pipeline.py

# View live dashboard
open http://192.168.1.81:8080
```

## Documentation

| Document | Description |
|----------|-------------|
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
