# Hardware Specifications

## Current Deployment: DOT-VHF Ground Station

The operational ground station in Anchorage, Alaska.

### Compute

| Component | Specification | Est. Cost |
|-----------|--------------|-----------|
| Raspberry Pi 5 | 16 GB RAM, BCM2712 Cortex-A76, aarch64 | $80 |
| MicroSD Card | 64 GB, boot drive | $12 |
| Crucial P3 Plus NVMe | CT2000P3PSSD8, 2 TB, PCIe Gen4 | $120 |
| Power Supply | Official USB-C 27W (5.1V/5A) | $12 |
| Case | Active cooling fan enclosure | $10 |
| Hailo-8 AI HAT+ | Present but disabled (NVMe takes M.2 priority) | $70 |

### SDR Receivers

| Device | Serial | Frequency | Role | Est. Cost |
|--------|--------|-----------|------|-----------|
| RTL-SDR Blog V4 | BLOGV4 | 118-137 MHz | VHF aviation (OpenWebRX + pipeline) | $35 |
| RTL-SDR FlyCatcher | ADSB1090 | 1090 MHz | ADS-B Extended Squitter (readsb) | $30 |
| RTL-SDR FlyCatcher | UAT978 | 978 MHz | UAT / FIS-B (dump978-fa) | $30 |

USB serial numbers are programmed into each dongle for persistent identification across reboots.

### Antennas

| Band | Type | Connected To | Est. Cost |
|------|------|-------------|-----------|
| VHF 118-137 MHz | Aviation band antenna | BLOGV4 | $25 |
| 1090 MHz | ADS-B antenna | ADSB1090 | $20 |
| 978 MHz | UAT antenna | UAT978 | $20 |

### Cost Summary

| Category | Cost |
|----------|------|
| Compute + storage | $304 |
| SDR receivers (3x) | $95 |
| Antennas (3x) | $65 |
| Networking | $5 |
| **Total deployed** | **~$470** |

---

## Aircraft Node (Pilot Equipment)

Off-the-shelf Meshtastic radio — no custom hardware required.

| Component | Specification | Est. Cost |
|-----------|--------------|-----------|
| Meshtastic LoRa radio | 902-928 MHz ISM, any compatible device | $35-50 |
| External antenna | 915 MHz, SMA connector | $15 |
| **Total per aircraft** | | **~$50-65** |

### Installation
1. Plug radio into aircraft USB power (12V/24V via adapter)
2. Pair with pilot's phone/tablet via Bluetooth
3. Receive ground station data automatically

No avionics bay mounting required. No FAA TSO certification needed (supplemental equipment only).

---

## Ground Station Antenna Guidelines

### VHF Aviation (118-137 MHz)
- Type: Discone or broadband vertical
- Gain: 2-3 dBi
- Placement: Roof or mast, clear line of sight to runway/approach

### ADS-B / UAT (978/1090 MHz)
- Type: Collinear vertical
- Gain: 5-8 dBi
- Placement: Highest available point, 20+ feet recommended

### LoRa Mesh (902-928 MHz)
- Type: Omnidirectional collinear or directional Yagi
- Gain: 6-12 dBi
- Height: 20+ feet recommended
- For mountain repeaters: lightning protection required

---

## Planned: Custom Hardware

The following custom board design has been explored but is **not in production**. The current deployment uses commercial off-the-shelf components (Raspberry Pi + RTL-SDR).

### Proposed Dual-Processor IoT Board

A compact board combining Meshtastic mesh networking with edge compute:

- **Primary**: nRF52840 (Cortex-M4F, runs Meshtastic firmware)
- **Secondary**: RP2040 (dual Cortex-M0+, handles sensor processing)
- **Radio**: SX1262 LoRa (902-928 MHz, +22dBm, -148dBm sensitivity)
- **Sensors**: 12x I2C ports (BME680, LIS3DH, GPS, INA219)
- **Power**: 6-30V input, 18650 battery, MPPT solar, <2W average
- **Physical**: 100x60mm, IP67 capable, -40C to +85C
- **Cost target**: $50-75 prototype, $20-25 at 1000+ units

This design is documented for future development if the project scales beyond Pi-based stations.

---

## Regulatory Notes

- **FCC Part 15**: All radios operate in ISM bands, no license required
- **FAA**: Supplemental information system only, not primary navigation
- **No mandatory tracking**: Privacy by design, opt-in location sharing
