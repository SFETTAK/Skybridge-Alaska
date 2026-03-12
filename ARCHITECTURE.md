# SkyBridge Alaska — Technical Architecture

## Overview

SkyBridge is a distributed aviation safety system that combines software-defined radio, AI speech recognition, and LoRa mesh networking to deliver flight-critical information to general aviation pilots in remote areas.

## Current Deployment: DOT-VHF Ground Station

The first operational ground station runs on a Raspberry Pi 5 in Anchorage, Alaska.

### System Diagram

```
              ANTENNAS
             /    |    \
       VHF  1090  978         3 frequency bands
        |     |     |
   [BLOGV4] [ADSB] [UAT]     3x RTL-SDR USB dongles
        |     |     |
┌───────┴─────┴─────┴───────┐
│      Raspberry Pi 5        │
│         (DOT-VHF)          │
│                            │
│  OpenWebRX (:8073)         │──→ Web SDR spectrum viewer
│    └→ rtl_tcp :1235        │
│        └→ VHF Pipeline     │
│           ├→ AM demod      │
│           ├→ VAD           │
│           ├→ FLAC archive ─┼──→ NVMe (2 TB)
│           ├→ Whisper STT   │
│           └→ Meshtastic TX─┼──→ LoRa mesh (planned)
│                            │
│  readsb (ADSB1090)        ─┼──→ tar1090 map (:8504)
│  dump978 (UAT978)         ─┼──→ skyaware978 JSON
│  Status Dashboard         ─┼──→ lighttpd (:8080)
│  nvme-backup.timer        ─┼──→ rclone to central
└────────────┬───────────────┘
             │
        [2 TB NVMe]
```

### Hardware

| Component | Role |
|-----------|------|
| Raspberry Pi 5 (16 GB) | Compute |
| 2 TB NVMe SSD | Audio, transcript, and ADS-B archive |
| RTL-SDR Blog V4 | VHF aviation radio (118-137 MHz) |
| RTL-SDR FlyCatcher #1 | ADS-B 1090 MHz Extended Squitter |
| RTL-SDR FlyCatcher #2 | UAT 978 MHz / FIS-B weather |
| Meshtastic LoRa radio | 902-928 MHz mesh relay (pending hardware) |

**Total cost: ~$470 deployed**

See [Hardware Inventory](docs/project-handoff/01-HARDWARE-INVENTORY.md) for serial numbers, part costs, and NVMe health data.

### Data Flows

**VHF Voice Transcription** (the core innovation):
1. RTL-SDR captures IQ samples at 2.4 MSps via OpenWebRX's rtl_tcp
2. AM envelope detection + decimation produces 24 kHz audio
3. Energy-based VAD detects voice transmissions (threshold: 0.005 RMS)
4. Voice segments archived as FLAC to NVMe
5. Resampled to 16 kHz and transcribed by Whisper (tiny.en, CPU int8)
6. Transcripts published to Meshtastic mesh and logged to NVMe

**ADS-B Tracking:**
- 1090 MHz Extended Squitter decoded by readsb with globe-history archival
- 978 MHz UAT decoded by dump978-fa for GA traffic and FIS-B weather
- Combined in tar1090 web map with 8-hour track persistence

**Monitoring:**
- Status dashboard polls service health, NVMe SMART, aircraft count, latest transcripts every 30 seconds
- rclone syncs all data to central server every 6 hours

### Service Dependencies

```
network.target
  ├→ openwebrx
  │    └→ vhf-pipeline (after 5s)
  ├→ readsb
  │    ├→ tar1090
  │    └→ status-dashboard
  ├→ dump978-fa
  │    └→ skyaware978
  ├→ lighttpd
  └→ nvme-backup.timer
```

## Planned: Aircraft Nodes

Simple Meshtastic radios carried by pilots. No custom hardware needed.

| Component | Est. Cost |
|-----------|-----------|
| Meshtastic LoRa radio (off-the-shelf) | $35-50 |
| External antenna | $15 |
| **Total per aircraft** | **~$50** |

Pilots receive ground station data (VHF transcripts, weather) on their phones via Bluetooth from the Meshtastic radio. No app store install required for basic text messaging; the SkyBridge app (in development) will add map layers and structured data display.

## Planned: TAIGA Protocol Integration

Current system transmits plain text transcripts over Meshtastic. Future versions will use NASA's TAIGA ASN.1 encoding for 80% compression, enabling:
- Structured PIREPs, METARs, NOTAMs over the mesh
- 5x more messages in the same bandwidth
- Greater effective range

See [TAIGA Protocol](protocol/TAIGA_PROTOCOL.md) for the encoding specification.

## Planned: Multi-Station Network

```
Ground Station A ──→ LoRa mesh ──→ Aircraft
       │                              ↕
       │                         Aircraft relay
       │                              ↕
Ground Station B ──→ LoRa mesh ──→ Aircraft
       │
  Internet gateway
       │
  Central server (backup, analytics)
```

### Coverage Model
- Line-of-sight at altitude: 50+ miles
- Ground-to-ground: 10-15 miles typical
- Mountain repeaters: 100+ mile coverage circles

### Scaling Targets
- Phase 1 (current): 1 ground station (DOT-VHF), proof of concept
- Phase 2: 5-10 stations along core Alaska corridors
- Phase 3: 50+ stations, statewide coverage, multi-state expansion

## Security

| Layer | Implementation |
|-------|---------------|
| SSH | Ed25519 key-only, fail2ban |
| Mesh | Meshtastic AES encryption |
| Privacy | Opt-in location sharing, no mandatory tracking |
| Network | Private LAN, no public-facing services |
| Compliance | FCC Part 15 ISM band, FAA supplemental only |

## Technology Stack

| Layer | Technology |
|-------|-----------|
| SDR Hardware | RTL-SDR (RTL2838) USB dongles |
| Signal Processing | OpenWebRX, csdr, pycsdr |
| ADS-B Decoding | readsb (1090), dump978-fa (978) |
| Speech-to-Text | faster-whisper (CTranslate2, CPU int8) |
| Mesh Network | Meshtastic (LoRa 902-928 MHz) |
| Data Compression | NASA TAIGA ASN.1 (planned) |
| Platform | Raspberry Pi 5, Debian 13, Python 3, systemd |
| Web | lighttpd, tar1090, OpenWebRX |
| Storage | NVMe SSD, rclone backup |
