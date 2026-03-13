# SkyBridge Alaska — Technical Architecture

## Overview

SkyBridge is a distributed aviation safety system that combines software-defined radio, AI speech recognition, and LoRa mesh networking to deliver flight-critical information to general aviation pilots in remote areas.

## Current Deployment: DOT-VHF Ground Station

The first operational ground station runs on a Raspberry Pi 5 in Anchorage, Alaska.

### System Diagram

```
                   ANTENNAS
                  /    |    \
            VHF  1090  978              3 frequency bands
             |     |     |
        [BLOGV4] [ADSB] [UAT]          3x RTL-SDR USB dongles
             |     |     |
┌────────────┴─────┴─────┴────────────────────────────────────────┐
│                    Raspberry Pi 5 (DOT-VHF)                     │
│                                                                 │
│  ┌─ VHF VOICE ─────────────────────────────────────────────┐    │
│  │  OpenWebRX (:8073) → rtl_tcp :1235                      │    │
│  │    └→ VHF Pipeline                                       │    │
│  │        ├→ Channelized AM demod (freq shift → LPF → 16k) │    │
│  │        ├→ Adaptive squelch (8dB SNR, noise floor EMA)    │    │
│  │        ├→ Speech quality gate (peak/mean + energy CoV)   │    │
│  │        ├→ FLAC archive ──────────────────→ NVMe (2 TB)   │    │
│  │        ├→ Whisper STT (base.en, CPU int8)                │    │
│  │        │    └→ aviation_lexicon.py (11 normalizers)      │    │
│  │        ├→ ADS-B correlation (callsign → position)        │    │
│  │        └→ Meshtastic TX ──────────────→ LoRa mesh (TBD)  │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌─ ADS-B ──────────────────────────────────────────────────┐    │
│  │  readsb (1090 MHz) ──→ tar1090 local (:8504)            │    │
│  │  dump978-fa (978 MHz) → skyaware978 JSON                 │    │
│  │       │                                                  │    │
│  │  adsb-combine.py ←── ADSB.fi API (2× 250nm circles)     │    │
│  │       │                  ~100-200 aircraft statewide     │    │
│  │       └→ /run/combine1090/aircraft.json                  │    │
│  │            └→ tar1090-combo (:8505 HTTP, :8506 HTTPS+GPS)│    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌─ PILOT KNEEBOARD (:8083 HTTP, :8443 HTTPS+GPS) ─────────┐    │
│  │  12-layer moving map (Leaflet.js + ESRI satellite)       │    │
│  │  ├─ L1  ESRI Satellite        L7  ADS-B Traffic          │    │
│  │  ├─ L2  VFR Sectional Charts  L8  VHF Radio Log          │    │
│  │  ├─ L3  NEXRAD Radar          L9  MWOS Weather Stations  │    │
│  │  ├─ L4  METAR Stations (30)   L10 G-AIRMETs              │    │
│  │  ├─ L5  SIGMETs / AIRMETs     L11 Volcanic Ash SIGMETs   │    │
│  │  ├─ L6  PIREPs                L12 NWS Alerts              │    │
│  │  │                                                       │    │
│  │  Data sources: aviationweather.gov, api.weather.gov,     │    │
│  │  ADSB.fi, Montis Corp MWOS API (7 stations, 28 cameras) │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌─ MONITORING ─────────────────────────────────────────────┐    │
│  │  Status Dashboard (:8080)    VHF Review (:8082)          │    │
│  │  lighttpd (HTTPS proxy)      nvme-backup (6h rclone)     │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                  [2 TB NVMe]
                  vhf-audio/ transcripts/ adsb/ logs/ uat/
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
2. Channelized AM demodulation: frequency shift → low-pass filter → decimate to 16 kHz
3. Adaptive squelch: 2-second calibration phase, median noise floor, 8 dB SNR threshold, EMA tracking
4. Speech quality gate: peak-to-mean ratio (>3.0) + energy coefficient of variation (>0.3) rejects noise
5. Voice segments archived as FLAC to NVMe
6. Transcribed by Whisper (base.en, CPU int8, PANC-tuned aviation prompt)
7. Post-processed by aviation_lexicon.py: phonetic alphabet → letters, 11 number normalizers (tail numbers, callsigns, runways, altitudes, speeds, altimeter, frequencies, squawk, headings, traffic clock), hallucination filter
8. Correlated against live ADS-B: callsign extraction → position/altitude annotation
9. Published to Meshtastic mesh (pending hardware) and logged to NVMe

**ADS-B Tracking:**
- Local: 1090 MHz decoded by readsb, 978 MHz decoded by dump978-fa
- Statewide: adsb-combine.py merges local feed with ADSB.fi API (two 250nm circles centered on Anchorage + Interior, ~100-200 aircraft)
- Local wins on position conflicts; remote enriches with registration, type, operator, description
- tar1090 local (:8504) shows receiver-only data; tar1090-combo (:8505/:8506) shows merged statewide view
- Full aircraft database: ICAO type codes → descriptions, silhouette icons, country flags, Planespotters.net photos

**Weather Integration:**
- aviationweather.gov: METARs (30 AK stations), TAFs, SIGMETs/AIRMETs, G-AIRMETs, PIREPs, volcanic ash SIGMETs, winds aloft
- api.weather.gov: NWS alerts for Alaska (winter storms, wind advisories)
- Montis Corp MWOS API: 7 automated weather stations with live temp/wind/humidity + 4 camera views each (Lake Hood, Merrill Field ×2, Nuiqsut, Kaktovik, Port Graham, Port Townsend)
- Iowa State Mesonet: NEXRAD radar tile overlay
- ArcGIS: VFR sectional chart overlay, ESRI satellite base map

**Monitoring:**
- Status dashboard polls service health, NVMe SMART, aircraft count, latest transcripts every 30 seconds
- VHF Review web UI for browsing archived audio + transcripts with playback
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
