# SkyBridge Technical Architecture

## Overview
SkyBridge is a distributed mesh network system providing aviation safety data to general aviation pilots in Alaska using LoRa radio, efficient data encoding, and mobile applications.

## System Components

### 1. Hardware Layer

#### Aircraft Node (Simple)
- **Processor**: RAK4631 (nRF52840)
- **Radio**: LoRa 902-928 MHz ISM band
- **Sensors**: 
  - BME680 (temperature, humidity, pressure)
  - GPS module
  - Voltage monitor (12V/24V aircraft systems)
- **Power**: Aircraft power with battery backup
- **Cost target**: <$100

#### Ground Station (Complex)
- **Processors**: RAK4631 + RP2040 (dual processor)
- **Radio**: LoRa + optional VHF SDR
- **Network**: WiFi/Ethernet for internet gateway
- **Power**: Mains or solar with battery
- **Additional**: Weather sensors, camera interface

### 2. Protocol Layer

#### TAIGA ASN.1 (NASA/Joseph Rios)
- Efficient aviation data encoding
- 80% compression vs raw text
- Supports: PIREPs, METARs, NOTAMs, weather polygons
- Packed Encoding Rules (PER) for minimal bandwidth

#### Meshtastic Base
- Proven mesh routing protocol
- Encryption support
- Store-and-forward capability
- Auto-discovery and healing

### 3. Network Layer

#### Mesh Topology
```
Aircraft → Aircraft (peer relay)
    ↓
Ground Station → Internet → Cloud Services
    ↓
Aircraft (in range)
```

#### Coverage Model
- Line-of-sight: 50+ miles at altitude
- Ground: 10-15 miles typical
- Mountain repeaters: 100+ mile coverage circles

### 4. Application Layer

#### Mobile App (React Native)
- iOS/Android support
- Offline-first architecture
- Map layers (weather, traffic, terrain)
- Message center
- Emergency beacon

#### State Services Integration
- Weather data ingestion (NOAA/NWS)
- NOTAM distribution (FAA)
- Flight plan filing (future)

## Data Flow

1. **Weather Station** → METAR encoded (TAIGA ASN.1) → Ground node
2. **Ground node** → Broadcasts via LoRa mesh
3. **Aircraft node** → Receives, relays to other aircraft
4. **Pilot tablet** → Bluetooth from node → Decodes and displays
5. **Pilot** → Creates PIREP → Node → Mesh → All aircraft in range

## Security Model

- **Mesh layer**: Meshtastic AES encryption
- **Identity**: Anonymous mesh IDs (no tail numbers)
- **Trust**: Community reputation system
- **Privacy**: Opt-in for all data sharing

## Scalability

### Phase 1 (2024-2025)
- 10-20 ground stations
- 50-100 aircraft nodes
- Core Alaska corridors

### Phase 2 (2025-2026)
- 50+ ground stations
- 500+ aircraft
- Statewide coverage

### Phase 3 (2026+)
- 100+ ground stations
- 1000+ aircraft
- Integration with commercial operators

## Performance Targets

- **Latency**: <2 seconds for 1-hop message
- **Reliability**: 99% delivery within coverage
- **Bandwidth**: 300-1200 bps per node
- **Power**: <2W average (aircraft node)
- **MTBF**: >8760 hours (1 year)

## Compliance

- **FCC Part 15**: ISM band compliance
- **FAA**: Supplemental information only (not primary navigation)
- **Privacy**: No mandatory registration or tracking

## Open Questions

1. VHF voice-to-text integration approach
2. Optimal repeater placement algorithm
3. Winter power management for solar sites
4. Integration with existing avionics

## Development Roadmap

### Q4 2024
- [ ] Van telemetry prototype
- [ ] File provisional patents
- [ ] Initial GitHub release

### Q1 2025
- [ ] First 5 ground stations
- [ ] Mobile app alpha
- [ ] FAA coordination meeting

### Q2 2025
- [ ] Pilot testing program
- [ ] 10 aircraft equipped
- [ ] Gather safety metrics

### Q3 2025
- [ ] Public beta launch
- [ ] 50 nodes deployed
- [ ] Grant applications

## Contact
- Technical: dev@skybridgealaska.net
- General: info@skybridgealaska.net
