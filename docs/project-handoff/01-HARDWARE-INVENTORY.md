# Hardware Inventory — DOT-VHF Ground Station

**Project:** SkyBridge Alaska
**Station ID:** DOT-VHF
**Location:** Anchorage, Alaska (61.2181N, -149.9003W, 40m ASL)
**Inventory Date:** 2026-03-12

---

## 1. Compute

| Item | Specification | Qty | Est. Cost |
|------|--------------|-----|-----------|
| Raspberry Pi 5 | 16 GB RAM, BCM2712 (Cortex-A76), aarch64 | 1 | $80 |
| MicroSD Card | 64 GB (57 GB usable), boot drive | 1 | $12 |
| Crucial P3 Plus NVMe SSD | CT2000P3PSSD8, 2 TB, PCIe Gen4, mounted via Pi 5 HAT/adapter | 1 | $120 |
| Hailo-8 AI HAT+ | Present but **disabled** (NVMe occupies M.2 slot priority) | 1 | $70 |
| Power Supply | Official Pi 5 USB-C 27W (5.1V/5A) | 1 | $12 |
| Case/Enclosure | Pi 5 case with active cooling fan | 1 | $10 |

## 2. Software-Defined Radio (SDR)

| Item | Serial/ID | Frequency | Role | Qty | Est. Cost |
|------|-----------|-----------|------|-----|-----------|
| RTL-SDR Blog V4 | `BLOGV4` | 118-137 MHz (VHF Aviation) | OpenWebRX + VHF pipeline | 1 | $35 |
| RTL-SDR (FlyCatcher) | `ADSB1090` | 1090 MHz | ADS-B Extended Squitter (readsb) | 1 | $30 |
| RTL-SDR (FlyCatcher) | `UAT978` | 978 MHz | UAT / FIS-B (dump978-fa) | 1 | $30 |

## 3. Antennas

| Item | Band | Connected To | Qty | Est. Cost |
|------|------|-------------|-----|-----------|
| VHF Aviation Antenna | 118-137 MHz | BLOGV4 | 1 | $25 |
| 1090 MHz ADS-B Antenna | 1090 MHz | ADSB1090 (FlyCatcher) | 1 | $20 |
| 978 MHz UAT Antenna | 978 MHz | UAT978 (FlyCatcher) | 1 | $20 |

## 4. Networking

| Item | Specification | Qty | Est. Cost |
|------|--------------|-----|-----------|
| Ethernet Cable | Cat5e/Cat6, connects to local network | 1 | $5 |
| WiFi | Built-in Pi 5 802.11ac, connected to local AP | — | — |

## 5. Meshtastic (Planned)

| Item | Specification | Qty | Est. Cost |
|------|--------------|-----|-----------|
| Meshtastic LoRa Radio | 902-928 MHz ISM, USB or serial connection | 1 | $35-50 |
| LoRa Antenna | 915 MHz, SMA connector | 1 | $15 |

**Status:** Not yet connected to this station. VHF pipeline is ready to publish transcripts once hardware arrives.

---

## Cost Summary

| Category | Subtotal |
|----------|----------|
| Compute (Pi 5 + storage + PSU) | ~$304 |
| SDR Receivers (3x) | ~$95 |
| Antennas (3x) | ~$65 |
| Networking | ~$5 |
| Meshtastic (planned) | ~$50-65 |
| **Total (deployed)** | **~$469** |
| **Total (with Meshtastic)** | **~$519-534** |

---

## USB Device Map

```
Bus 001 Device 002: RTL2838 — BLOGV4 (VHF)
Bus 003 Device 003: RTL2838 — ADSB1090 (1090 MHz)
Bus 003 Device 004: RTL2838 — UAT978 (978 MHz)
```

USB serial numbers are programmed into each dongle for persistent device identification across reboots.

## NVMe Drive Health (as of 2026-03-12)

| Metric | Value |
|--------|-------|
| Model | Crucial CT2000P3PSSD8 |
| Capacity | 1.8 TB usable |
| SMART Status | PASSED |
| Temperature | 30C |
| Available Spare | 100% |
| Percentage Used | 0% |
| Power-On Hours | 664 |
| Media Errors | 0 |
| Data Written | ~20 MB (station recently deployed) |
