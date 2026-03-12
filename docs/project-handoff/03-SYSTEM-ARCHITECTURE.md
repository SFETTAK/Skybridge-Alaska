# System Architecture — DOT-VHF Ground Station

**Project:** SkyBridge Alaska
**Station ID:** DOT-VHF
**Date:** 2026-03-12

---

## Overview

DOT-VHF is a multi-band aviation ground station built on a Raspberry Pi 5. It simultaneously monitors three radio bands, processes the signals, and makes the data available via web interfaces and (when connected) a LoRa mesh network.

```
                    ANTENNAS
                   /    |    \
            VHF  1090  978
             |     |     |
        [BLOGV4] [ADSB] [UAT]     ← 3× RTL-SDR USB dongles
             |     |     |
     ┌───────┴─────┴─────┴───────┐
     │      Raspberry Pi 5       │
     │         (DOT-VHF)         │
     │                           │
     │  ┌─────────────────────┐  │
     │  │    OpenWebRX        │  │  ← Web SDR interface (:8073)
     │  │  (BLOGV4 @ 121.8)  │  │
     │  │    rtl_tcp :1235    │──┼──→ VHF Pipeline
     │  └─────────────────────┘  │
     │                           │
     │  ┌─────────────────────┐  │
     │  │  VHF Pipeline       │  │
     │  │  AM demod → VAD     │  │
     │  │  → FLAC archive     │──┼──→ NVMe (/mnt/nvme/skybridge/)
     │  │  → Whisper STT      │  │
     │  │  → Meshtastic TX    │──┼──→ LoRa Mesh (planned)
     │  └─────────────────────┘  │
     │                           │
     │  ┌─────────────────────┐  │
     │  │  readsb (ADSB1090)  │──┼──→ tar1090 map (:8504)
     │  │  1090 MHz ES        │──┼──→ NVMe globe-history
     │  └─────────────────────┘  │
     │                           │
     │  ┌─────────────────────┐  │
     │  │  dump978 (UAT978)   │  │
     │  │  978 MHz UAT/FIS-B  │──┼──→ skyaware978 JSON
     │  └─────────────────────┘  │
     │                           │
     │  ┌─────────────────────┐  │
     │  │  Status Dashboard   │──┼──→ lighttpd (:8080)
     │  └─────────────────────┘  │
     │                           │
     │  ┌─────────────────────┐  │
     │  │  nvme-backup.timer  │──┼──→ rclone → central server
     │  └─────────────────────┘  │
     │                           │
     └───────────┬───────────────┘
                 │
            [2 TB NVMe]
            /mnt/nvme/skybridge/
```

---

## Data Flow: VHF Voice Pipeline

This is the core innovation — converting analog aviation radio to text for mesh distribution.

```
RTL-SDR (BLOGV4)
    │
    ▼ IQ samples (2.4 MSps, uint8)
OpenWebRX rtl_tcp (:1235)
    │
    ▼ TCP stream
vhf-pipeline.py: RtlTcpClient
    │
    ▼ 480K IQ samples per 0.1s chunk
AM Demodulator (numpy)
    │  envelope detection: sqrt(I² + Q²)
    │  decimate 100:1 → 24 kHz audio
    ▼
Voice Activity Detector (energy VAD)
    │  threshold: 0.005 RMS
    │  hold: 1.5s after last voice
    │  max segment: 30s
    │
    ├──→ [voice detected] → SegmentManager buffer
    │                            │
    │                     [gate closes]
    │                            │
    │                            ▼
    │                     Archive (sox → FLAC)
    │                     → /mnt/nvme/skybridge/vhf-audio/
    │                            │
    │                            ▼
    │                     STT Worker Thread
    │                       │
    │                       ▼
    │                  Resample 24k→16k (ffmpeg)
    │                       │
    │                       ▼
    │                  Whisper tiny.en (faster-whisper, CPU int8)
    │                       │
    │                       ▼
    │                  Transcript text
    │                    /         \
    │                   ▼           ▼
    │            Meshtastic TX    Log to NVMe
    │            (ch 0, 200 char) transcripts/YYYY-MM-DD.txt
    │
    └──→ [silence] → discard, continue listening
```

## Data Flow: ADS-B Tracking

```
RTL-SDR (ADSB1090)          RTL-SDR (UAT978)
    │                            │
    ▼                            ▼
readsb                      dump978-fa
    │                            │
    ├→ JSON (/run/readsb/)       ├→ JSON (:30978)
    ├→ SBS (:30003)              │
    ├→ Beast (:30005)            ▼
    │                       skyaware978
    ▼                            │
tar1090                          ▼
    │                       /run/dump978/aircraft.json
    ▼
Web map (:8504)  ←── merges 1090 + 978
    │
    ▼
NVMe globe-history (/mnt/nvme/skybridge/adsb/)
```

## Data Flow: Monitoring & Backup

```
                   ┌─────────────────┐
                   │ status-dashboard │ (every 30s)
                   └────────┬────────┘
                            │ reads:
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        systemctl     /run/readsb/    smartctl
        is-active     status.json    /dev/nvme0n1
              │             │             │
              └─────────────┼─────────────┘
                            ▼
                    status.html → lighttpd (:8080)


nvme-backup.timer (every 6h)
    │
    ▼
rclone sync /mnt/nvme/skybridge/ → skybridge-central (remote)
    │
    ▼
Logs → /mnt/nvme/skybridge/logs/rclone-backup-YYYY-MM-DD.log
```

---

## Service Dependency Chain

```
network.target
    │
    ├→ openwebrx.service
    │       │
    │       └→ vhf-pipeline.service (after 5s delay)
    │
    ├→ readsb.service
    │       │
    │       ├→ tar1090.service
    │       │
    │       └→ status-dashboard.service
    │
    ├→ dump978-fa.service
    │       │
    │       └→ skyaware978.service
    │
    ├→ lighttpd.service
    │
    └→ nvme-backup.timer
```

---

## Storage Architecture

```
/dev/mmcblk0p2 (SD Card, 57 GB)
└── / (root filesystem)
    └── /home/blastly/
        ├── scripts/          ← Custom pipeline code
        ├── openwebrx/        ← OpenWebRX (built from source)
        ├── dump978/          ← dump978 (built from source)
        ├── rtl-sdr-build/    ← RTL-SDR drivers (built from source)
        ├── csdr/             ← DSP library (built from source)
        ├── pycsdr/           ← Python DSP bindings
        ├── owrx_connector/   ← OpenWebRX connectors
        ├── vhf-pipeline-venv/← Python venv for VHF pipeline
        └── Skybridge-Alaska/ ← Project repo

/dev/nvme0n1p2 (NVMe, 1.8 TB)
└── /mnt/nvme/
    └── skybridge/
        ├── vhf-audio/        ← FLAC voice segments by date
        ├── transcripts/      ← Daily transcript logs
        ├── adsb/             ← Globe-history + state snapshots
        ├── uat/              ← UAT data (placeholder)
        ├── logs/             ← All operational logs
        ├── backup-staging/   ← Excluded from remote sync
        └── status.html       ← Live dashboard
```

---

## Security Model

| Layer | Implementation |
|-------|---------------|
| SSH Access | Ed25519 key-only, no password, no root login |
| Brute Force | fail2ban: 5 attempts/10min → 1h ban |
| SSH Hardening | MaxAuthTries 3, LoginGraceTime 30s, no X11/agent forwarding |
| Network | Private LAN only (192.168.1.0/24) |
| Services | All run as unprivileged users (blastly, readsb, tar1090) |
| Logs | Rotated weekly, 12 weeks retention, compressed |
