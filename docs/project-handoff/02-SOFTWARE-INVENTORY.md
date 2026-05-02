# Software Inventory — DOT-VHF Ground Station

**Project:** SkyBridge Alaska
**Station ID:** DOT-VHF
**Inventory Date:** 2026-03-12

---

## 1. Operating System

| Component | Version |
|-----------|---------|
| OS | Debian GNU/Linux 13 (trixie) |
| Kernel | 6.12.47+rpt-rpi-2712 (aarch64, SMP PREEMPT) |
| Architecture | ARM64 (aarch64) |

## 2. Custom Scripts (~/scripts/)

| Script | Size | Purpose |
|--------|------|---------|
| `vhf-pipeline.py` | 18 KB | Core pipeline: RTL-TCP IQ → channelized AM demod → adaptive squelch → speech quality gate → FLAC archive → Whisper STT → ADS-B correlation → Meshtastic publish |
| `aviation_lexicon.py` | 14 KB | ATC vocabulary corrections: Whisper prompt (PANC-tuned), phonetic fixes, ATC command normalization, 11 number normalizers (squawk, heading, altitude, FL, frequency, tail number, speed, altitude thousands, runway, callsign, altimeter, traffic), hallucination filter, relevance scorer |
| `kneeboard.py` | 38 KB | Pilot-facing kneeboard web app: 12-layer Leaflet moving map, ADS-B traffic (local + ADSB.fi), weather overlays (METAR, SIGMET, PIREP, G-AIRMET, volcanic ash, NWS alerts), MWOS stations (Montis Corp API), VHF transcript feed, GPS tracking. Port 8083 / HTTPS 8443 |
| `adsb-combine.py` | 4 KB | Merges local readsb ADS-B with ADSB.fi statewide feed (two 250nm circles). Writes combined aircraft.json to /run/combine1090/ every 8 seconds for tar1090-combo |
| `vhf-review.py` | 12 KB | Web UI for browsing archived VHF audio segments and transcripts. FLAC→WAV conversion, waveform display, date/search filtering. Port 8082 |
| `test-pipeline.py` | 16 KB | 17-test validation harness for vhf-pipeline (runs without SDR hardware) |
| `status-dashboard.py` | 8.6 KB | Generates live HTML status dashboard every 30 seconds |
| `nvme-backup.sh` | 1.2 KB | rclone sync of NVMe data to central backup server |

## 3. SDR & Aviation Stack (Built from Source)

| Software | Version | Source | Location | Purpose |
|----------|---------|--------|----------|---------|
| OpenWebRX | 1.2.2 | jketterl/openwebrx | ~/openwebrx/ | Web-based SDR receiver interface |
| csdr | — | jketterl/csdr | ~/csdr/ | DSP signal processing library |
| pycsdr | 0.18.2 | jketterl/pycsdr | ~/pycsdr/ | Python bindings for csdr |
| owrx_connector | — | jketterl/owrx_connector | ~/owrx_connector/ | SDR device connectors for OpenWebRX |
| rtl-sdr | — | osmocom/rtl-sdr | ~/rtl-sdr-build/ | RTL-SDR USB drivers and utilities |
| dump978-fa | — | FlightAware/dump978 | ~/dump978/ | UAT 978 MHz ADS-B decoder |
| readsb | — | wiedehopf script | /usr/bin/readsb | 1090 MHz ADS-B decoder |
| tar1090 | — | wiedehopf script | /usr/local/share/tar1090/ | ADS-B web map and history compressor |
| skyaware978 | — | FlightAware/dump978 | /usr/local/bin/skyaware978 | UAT web map JSON writer |

### RTL-SDR Utilities Installed (/usr/local/bin/)
`rtl_tcp`, `rtl_fm`, `rtl_sdr`, `rtl_eeprom`, `rtl_test`, `rtl_power`, `rtl_adsb`, `rtl_biast`, `rtl_connector`, `rtl_tcp_connector`

## 4. AI / Speech-to-Text

| Software | Version | Location | Notes |
|----------|---------|----------|-------|
| faster-whisper | 1.2.1 | vhf-pipeline-venv | CTranslate2-based Whisper inference |
| CTranslate2 | 4.7.1 | vhf-pipeline-venv | Optimized transformer runtime |
| ONNX Runtime | 1.24.2 | vhf-pipeline-venv | Neural network inference |
| Whisper Model | tiny.en | Downloaded on first run | 5.1M params, CPU int8 quantization |

## 5. Mesh Networking

| Software | Version | Location | Notes |
|----------|---------|----------|-------|
| meshtastic (Python) | 2.7.8 | vhf-pipeline-venv | Meshtastic serial/TCP interface |
| meshtastic (Python) | 2.7.7 | system pip | System-level install |
| bleak | 2.1.1 | vhf-pipeline-venv | Bluetooth LE (future Meshtastic BLE) |

## 6. System Utilities

| Software | Purpose |
|----------|---------|
| sox | Audio format conversion (FLAC archiving) |
| ffmpeg | Audio resampling (24kHz → 16kHz for Whisper) |
| rclone 1.73.1 | Remote backup sync |
| smartmontools | NVMe SMART health monitoring |
| nvme-cli | NVMe management |
| lighttpd | Lightweight web server (dashboard) |
| fail2ban | SSH brute-force protection |
| logrotate | Log rotation and compression |

## 7. Python Environments

### vhf-pipeline-venv (~/vhf-pipeline-venv/)

Primary packages:
```
faster-whisper    1.2.1
ctranslate2       4.7.1
onnxruntime       1.24.2
meshtastic        2.7.8
pyrtlsdr          0.4.0
pyserial          3.5
numpy             2.4.2
bleak             2.1.1
av                16.1.0
```

### OpenWebRX venv (~/openwebrx/venv/)
- Python 3.13.5
- OpenWebRX 1.2.2 (editable install)
- pycsdr 0.18.2

## 8. Systemd Services

| Service | ExecStart | User | Status |
|---------|-----------|------|--------|
| openwebrx | ~/openwebrx/venv/bin/python openwebrx.py | blastly | active |
| readsb | /usr/bin/readsb (ADSB1090, globe-history) | readsb | active |
| dump978-fa | /usr/local/bin/dump978-fa (device=1, port 30978) | blastly | active |
| skyaware978 | /usr/local/bin/skyaware978 (127.0.0.1:30978) | blastly | active |
| tar1090 | /usr/local/share/tar1090/tar1090.sh | tar1090 | active |
| tar1090-combo | /usr/local/share/tar1090/tar1090.sh /run/tar1090-combo /run/combine1090 | tar1090 | active |
| adsb-combine | ~/vhf-pipeline-venv/bin/python ~/scripts/adsb-combine.py | root | active |
| kneeboard | ~/vhf-pipeline-venv/bin/python ~/scripts/kneeboard.py | blastly | active |
| status-dashboard | /usr/bin/python3 ~/scripts/status-dashboard.py | blastly | active |
| lighttpd | system lighttpd | www-data | active |
| vhf-pipeline | ~/vhf-pipeline-venv/bin/python ~/scripts/vhf-pipeline.py | blastly | enabled, inactive |
| nvme-backup.timer | ~/scripts/nvme-backup.sh (every 6h) | blastly | active |

## 9. Systemd Timers

| Timer | Service | Schedule |
|-------|---------|----------|
| nvme-backup.timer | nvme-backup.service | Every 6 hours, first run 10 min after boot |

## 10. Cron Jobs

| Schedule | Script | Purpose |
|----------|--------|---------|
| Daily (root) | /etc/cron.daily/nvme-health-check | SMART snapshot → /mnt/nvme/skybridge/logs/nvme-health.log |

## 11. Web Interfaces

| Service | URL | Purpose |
|---------|-----|---------|
| OpenWebRX | http://<station-host>:8073 | SDR spectrum viewer and VHF tuning |
| Status Dashboard | http://<station-host>:8080 | Station health and metrics |
| VHF Review | http://<station-host>:8082 | Audio/transcript browser |
| Kneeboard | https://<station-host>:8443 | Pilot kneeboard (12-layer map, GPS) |
| tar1090 (local) | http://<station-host>:8504 | ADS-B map (local receiver only) |
| tar1090-combo | http://<station-host>:8505 | ADS-B map (local + ADSB.fi statewide) |
| tar1090-combo (HTTPS) | https://<station-host>:8506 | ADS-B map combined (GPS-enabled) |

## 12. Network Ports

| Port | Protocol | Service | Direction |
|------|----------|---------|-----------|
| 1235 | TCP | rtl_tcp (OpenWebRX compat) | internal |
| 8073 | HTTP | OpenWebRX | LAN |
| 8080 | HTTP | lighttpd (dashboard) | LAN |
| 8082 | HTTP | vhf-review | LAN |
| 8083 | HTTP | kneeboard (Flask, internal) | internal |
| 8443 | HTTPS | kneeboard (lighttpd SSL proxy) | LAN |
| 8504 | HTTP | tar1090 (local) | LAN |
| 8505 | HTTP | tar1090-combo | LAN |
| 8506 | HTTPS | tar1090-combo (SSL) | LAN |
| 30001 | TCP | readsb raw input | internal |
| 30002 | TCP | readsb raw output | internal |
| 30003 | TCP | readsb SBS (BaseStation) | internal |
| 30004 | TCP | readsb Beast input | internal |
| 30005 | TCP | readsb Beast output | internal |
| 30978 | TCP | dump978-fa JSON | internal |

## 13. HTTPS / SSL

Self-signed certificates enable browser GPS geolocation on LAN endpoints.

| Component | Certificate | Ports |
|-----------|-------------|-------|
| Kneeboard | /etc/lighttpd/certs/server.pem | 8443 (reverse proxy to Flask :8083) |
| tar1090-combo | /etc/lighttpd/certs/server.pem | 8506 (static file serving) |

## 14. External API Dependencies

| API | Used By | Purpose | Rate Limit |
|-----|---------|---------|------------|
| ADSB.fi opendata | adsb-combine.py, kneeboard.py | Statewide ADS-B positions | 10s between requests |
| aviationweather.gov | kneeboard.py | METAR, TAF, SIGMET, AIRMET, G-AIRMET, PIREP, volcanic ash | Cached 5 min |
| api.weather.gov | kneeboard.py | NWS active alerts for Alaska | Cached 5 min |
| Montis Corp MWOS | kneeboard.py | Automated weather stations + cameras | Cached 5 min |
