# SkyBridge Alaska — Project Journal

Decision log and milestones. For detailed system documentation, see [docs/project-handoff/](docs/project-handoff/).

---

## 2024-09-17 — Repository Created for NASAO 2025

Prepared and published the initial GitHub repository for presentation at the National Association of State Aviation Officials (NASAO) 2025 conference.

**Key actions:**
- Moved private files (patents, strategy, photos) to PRIVATE/ folder
- Added NASA TAIGA ASN.1 reference (public domain)
- Created documentation suite: whitepaper, gap analysis, elevator pitch, media coverage, partnership docs
- Published to https://github.com/SFETTAK/Skybridge-Alaska

**Positioning achieved:** Government validation (Alaska DOT&PF Gap Analysis), media credibility (Washington Post investigation), technical legitimacy (NASA TAIGA protocol), patent protection (3 provisional applications filed).

---

## 2025-03-04 — DOT-VHF Ground Station: Design Decisions

Started building the first physical ground station on a Raspberry Pi 5 (hostname: DOT-VHF).

**Hardware decisions:**
- 3x RTL-SDR dongles: Blog V4 (VHF), two FlyCatchers (1090 MHz ADS-B + 978 MHz UAT)
- FlyCatcher can't do analog VHF and ADS-B simultaneously — dedicated dongle per band
- Hailo-8 AI HAT+ present but **disabled** — NVMe SSD takes M.2 slot priority. CPU Whisper is sufficient.
- 2 TB NVMe SSD for all data archival (audio, transcripts, ADS-B history)

**Software stack chosen:**
- OpenWebRX (jketterl) for web SDR interface + shared IQ via rtl_tcp compat port
- Custom VHF pipeline: AM demod (numpy) → energy VAD → FLAC archive → Whisper STT → Meshtastic
- readsb for 1090 MHz ADS-B, dump978-fa for 978 MHz UAT
- tar1090 for combined aircraft tracking web map

**Design doc:** `docs/dot_vhf_software_stack.md`

---

## 2025-03-04 — DOT-VHF: Full Stack Deployed

All core software built from source and running as systemd services:

- **OpenWebRX** — 3 VHF aviation profiles (119.05, 121.8, 127.0 MHz), 19 Anchorage bookmarks, port 8073
- **VHF pipeline** — AM demod → VAD → FLAC archive → Whisper tiny.en (CPU int8) → NVMe transcripts. Meshtastic TX ready but hardware not yet connected
- **readsb + tar1090** — 1090 MHz ADS-B with globe-history to NVMe, web map on port 8504
- **dump978 + skyaware978** — 978 MHz UAT decoder, JSON overlay on tar1090
- **Status dashboard** — HTML health page refreshing every 30s, served on port 8080
- **NVMe backup** — rclone sync every 6h via systemd timer (remote pending central server)
- **SSH hardening** — Ed25519 key-only, fail2ban (5 retries → 1h ban)
- **Test suite** — 17/17 tests passing, runs without SDR hardware

USB serial numbers programmed into all 3 dongles (BLOGV4, ADSB1090, UAT978) for persistent device ID.

---

## 2026-03-12 — Repository Refactor and Documentation

Restructured the GitHub repository to include the actual deployed ground station code (previously only existed on the Pi).

**Changes:**
- Added `ground-station/` directory with all scripts, systemd units, and config files
- Created `docs/project-handoff/` with full inventory and operational documentation
- Rewrote README.md to reflect the real deployed system
- Rewrote ARCHITECTURE.md and SPECIFICATIONS.md to match actual hardware
- Moved NASAO 2025 pitch materials to `docs/nasao-2025/`
- Deleted redundant summary docs (gap analysis summary, whitepaper summary)
- Added implementation status notes to TAIGA protocol doc
- Properly git-cloned the repo (was previously curl-downloaded)

---

## Open Items

Hardware-dependent (require onsite access):
- [ ] Reboot Pi to apply USB serial numbers, then update dump978-fa to use serial=UAT978
- [ ] Connect Meshtastic LoRa node (USB serial or set MESH_HOST in vhf-pipeline.service)
- [ ] Configure rclone remote `skybridge-central` when central server is available
- [ ] Tune VAD threshold with real RF signal on antenna
- [ ] Set readsb location: `sudo readsb-set-location 61.2181 -149.9003`
- [ ] Weather API integration (on-site weather system, vendor TBD)
