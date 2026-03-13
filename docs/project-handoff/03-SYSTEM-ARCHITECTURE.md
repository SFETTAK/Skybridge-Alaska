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
     │  │  adsb-combine.py    │  │  ← Merges readsb + ADSB.fi
     │  │  /run/combine1090/  │──┼──→ tar1090-combo (:8505/:8506)
     │  └─────────────────────┘  │
     │                           │
     │  ┌─────────────────────┐  │
     │  │  Kneeboard          │  │  ← 12-layer pilot moving map
     │  │  Flask (:8083)      │──┼──→ lighttpd HTTPS (:8443)
     │  └─────────────────────┘  │
     │                           │
     │  ┌─────────────────────┐  │
     │  │  VHF Review         │──┼──→ Audio browser (:8082)
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
    ▼ TCP stream (960KB per 0.1s chunk)
vhf-pipeline.py: RtlTcpClient
    │
    ▼ 480K IQ samples per chunk
ChannelDemod (channelized AM demodulator)
    │  freq shift to channel baseband (e.g. 118.6 MHz ANC Tower)
    │  LPF 12.5 kHz channel isolation (6th-order Butterworth)
    │  decimate 2.4M → 48k (50:1)
    │  AM envelope detection: sqrt(I² + Q²)
    │  audio BPF 300-3400 Hz (4th-order Butterworth)
    │  decimate 48k → 16k (3:1) — native Whisper rate
    ▼
SegmentManager (adaptive squelch)
    │  calibrate noise floor from first 2s (median RMS)
    │  adaptive threshold: max(0.02, noise_floor × 10^(8dB/20))
    │  noise floor tracks via EMA (alpha=0.01) when gate closed
    │  hold: 1.0s after last voice
    │  max segment: 30s, min segment: 0.5s
    │
    ├──→ [squelch opens] → buffer audio chunks
    │                            │
    │                     [gate closes]
    │                            │
    │                            ▼
    │                     Speech Quality Gate
    │                       peak/mean ratio ≥ 3.0?
    │                       energy CoV > 0.3?
    │                            │
    │                     ├── [noise] → discard
    │                     │
    │                     ▼ [speech]
    │                     Normalize audio [-1, 1]
    │                            │
    │                     ┌──────┴──────┐
    │                     ▼             ▼
    │              Archive (sox)   STT Worker Thread
    │              → FLAC to NVMe       │
    │                                   ▼
    │                          Whisper base.en (faster-whisper, CPU int8)
    │                          initial_prompt = PANC vocabulary
    │                          beam_size=5, VAD filter on
    │                                   │
    │                                   ▼
    │                          aviation_lexicon.py post_process()
    │                            suppress repetitions
    │                            fix phonetic alphabet
    │                            correct ATC commands
    │                            normalize numbers (11 types)
    │                            score aviation relevance
    │                                   │
    │                            ├── [relevance < 0.05] → discard
    │                            │
    │                            ▼
    │                     ADS-B Correlation
    │                       extract callsigns from text
    │                       match against /run/readsb/aircraft.json
    │                       annotate transcript with position/alt/squawk
    │                                   │
    │                            ┌──────┴──────┐
    │                            ▼             ▼
    │                     Meshtastic TX   Log to NVMe
    │                     (ch 0, 200 char) transcripts/YYYY-MM-DD.txt
    │
    └──→ [below threshold] → update noise floor, continue listening
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
    │                            │
    │                            ▼
    │                       /run/dump978/aircraft.json
    │
    ├──────────────────────────────────────────────┐
    │                                              │
    ▼                                              ▼
tar1090 (local only)                        adsb-combine.py
    │                                    ┌─────────┴──────────┐
    ▼                                    │                    │
Web map (:8504)                   /run/readsb/         ADSB.fi API
    │                             (local, wins)     (2x 250nm circles)
    ▼                                    │                    │
NVMe globe-history                       └─────┬─────────────┘
(/mnt/nvme/skybridge/adsb/)                     │ merge (every 8s)
                                                ▼
                                     /run/combine1090/aircraft.json
                                                │
                                     ┌──────────┴──────────┐
                                     ▼                     ▼
                              tar1090-combo          kneeboard.py
                              :8505 (HTTP)           /api/traffic
                              :8506 (HTTPS+GPS)      :8083/:8443
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
    │               │
    │               └→ kneeboard.service (After=vhf-pipeline)
    │
    ├→ readsb.service
    │       │
    │       ├→ tar1090.service (local, :8504)
    │       │
    │       ├→ adsb-combine.service (After=readsb, Wants=network-online)
    │       │       │
    │       │       └→ tar1090-combo.service (Requires=adsb-combine)
    │       │
    │       └→ status-dashboard.service
    │
    ├→ dump978-fa.service
    │       │
    │       └→ skyaware978.service
    │
    ├→ lighttpd.service
    │       serves: dashboard (:8080), tar1090 (:8504),
    │       tar1090-combo (:8505/:8506), kneeboard HTTPS (:8443)
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

## Data Flow: Kneeboard

```
                        ┌─────────────────────────────────────┐
                        │        kneeboard.py (Flask)          │
                        │          port 8083                   │
                        │                                     │
  /api/traffic ─────────┤  Local readsb + ADSB.fi merge       │
                        │  (8s cache, two 250nm circles)      │
                        │                                     │
  /api/radio ───────────┤  VHF transcripts from NVMe          │
                        │  (last 30, newest first)            │
                        │                                     │
  /api/weather ─────────┤  aviationweather.gov METAR/TAF      │
  /api/metarmap         │  (30 Alaska stations, 5min cache)   │
  /api/sigmets          │                                     │
  /api/pireps           │  Weather polygons + PIREPs          │
  /api/gairmet          │  (5min cache each)                  │
  /api/volash           │                                     │
  /api/nwsalerts        │  NWS alerts for Alaska              │
                        │                                     │
  /api/mwos ────────────┤  Montis Corp MWOS API               │
                        │  (7 stations, obs + cameras)        │
                        │                                     │
  /api/station ─────────┤  systemctl service health check     │
                        └──────────────┬──────────────────────┘
                                       │
                               lighttpd HTTPS proxy
                               :8443 (self-signed cert)
                                       │
                                       ▼
                               Pilot tablet browser
                               (Leaflet map, 12 layers)
                               GPS position tracking
```

## HTTPS Layer

Browser GPS geolocation requires a secure context (HTTPS). Two services need GPS:

```
                   ┌──────────────────────────────┐
                   │  /etc/lighttpd/certs/         │
                   │  server.pem (self-signed)     │
                   └──────────┬───────────────────┘
                              │
               ┌──────────────┴──────────────┐
               ▼                             ▼
    98-kneeboard-ssl.conf         97-tar1090-combo-ssl.conf
    :8443 → proxy :8083           :8506 → static files
    (Flask reverse proxy)         (tar1090 html-combo/)
```

## Security Model

| Layer | Implementation |
|-------|---------------|
| SSH Access | Ed25519 key-only, no password, no root login |
| Brute Force | fail2ban: 5 attempts/10min → 1h ban |
| SSH Hardening | MaxAuthTries 3, LoginGraceTime 30s, no X11/agent forwarding |
| Network | Private LAN only (192.168.1.0/24) |
| Services | All run as unprivileged users (blastly, readsb, tar1090) |
| HTTPS | Self-signed certs for GPS-enabled endpoints (8443, 8506) |
| Logs | Rotated weekly, 12 weeks retention, compressed |
