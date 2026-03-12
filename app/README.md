# SkyBridge Alaska — Mobile App

**Status: Early scaffold. No source code implemented yet.**

This directory contains the `package.json` defining the planned React Native mobile application. The app will serve as the pilot-facing interface for receiving ground station data (VHF transcripts, weather, NOTAMs) via Bluetooth from a Meshtastic LoRa radio.

## Planned Features
- Map display with weather overlays and traffic
- Text message center (mesh network)
- VHF transcript feed from ground stations
- TAIGA ASN.1 decoding for structured aviation data
- Offline-first architecture

## Dependencies (planned)
- React Native 0.72+
- MapLibre for map rendering
- Bluetooth Classic for Meshtastic radio pairing
- ASN1.js for TAIGA protocol decoding

## Development
No build or run instructions yet. This scaffold will be developed once the ground station network and Meshtastic integration are validated.
