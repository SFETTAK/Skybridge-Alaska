# SkyBridge Alaska — Project Documentation Package

**Station:** DOT-VHF Ground Station
**Location:** Anchorage, Alaska
**Organization:** Alaska Department of Transportation & Public Facilities
**Date:** 2026-03-12

---

## Project Summary

SkyBridge Alaska is an open-source aviation safety system that provides critical flight information (weather, NOTAMs, VHF radio transcripts) to general aviation pilots via a low-cost LoRa mesh network. The DOT-VHF station is a deployed ground station prototype built on a Raspberry Pi 5 with three software-defined radios, monitoring VHF aviation voice (118-137 MHz), ADS-B aircraft transponders (1090 MHz), and UAT traffic/weather (978 MHz).

The station captures VHF pilot communications, transcribes them using AI (OpenAI Whisper), and is designed to relay critical information over Meshtastic mesh radios — enabling pilots in remote Alaska to receive real-time aviation data without cell or satellite coverage.

---

## Documentation Index

| # | Document | Description |
|---|----------|-------------|
| 01 | [Hardware Inventory](01-HARDWARE-INVENTORY.md) | All physical components, serial numbers, costs |
| 02 | [Software Inventory](02-SOFTWARE-INVENTORY.md) | All installed software, services, versions, Python packages |
| 03 | [System Architecture](03-SYSTEM-ARCHITECTURE.md) | Data flow diagrams, service dependencies, storage layout |
| 04 | [Configuration Reference](04-CONFIGURATION-REFERENCE.md) | Every config file, parameter, port, and tuning value |
| 05 | [Operational Runbook](05-OPERATIONAL-RUNBOOK.md) | How to access, monitor, troubleshoot, and maintain the station |

---

## Quick Reference

| What | Where |
|------|-------|
| SSH | `ssh <operator>@<station-host>` (key-only) |
| SDR Spectrum | http://<station-host>:8073 |
| Aircraft Map | http://<station-host>:8504 |
| Status Dashboard | http://<station-host>:8080 |
| VHF Audio Archive | /mnt/nvme/skybridge/vhf-audio/ |
| Transcripts | /mnt/nvme/skybridge/transcripts/ |
| ADS-B History | /mnt/nvme/skybridge/adsb/ |
| Pipeline Code | ~/scripts/vhf-pipeline.py |
| GitHub | https://github.com/SFETTAK/Skybridge-Alaska |

---

## Estimated Total Station Cost: ~$470 (deployed) / ~$530 (with Meshtastic)
