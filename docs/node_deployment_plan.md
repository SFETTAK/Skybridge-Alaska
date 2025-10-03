# SkyBridge Alaska Node Deployment Plan

## Phase 1: Initial Test Deployment

### Fixed Node Locations
1. **Alaska Airmen's Association** (Host: Adam)
   - Primary coverage node for Anchorage area
   - High visibility location for pilot community
   
2. **DOT Headquarters** 
   - Co-located with server infrastructure
   - Central monitoring and management hub

### Hardware Specifications

#### Fixed Nodes (Raspberry Pi Based)
- **Compute**: Raspberry Pi 4B (4GB minimum) or Pi 5
- **Radio**: 
  - Primary: RAK WisBlock or Heltec V3 (LoRa 915MHz)
  - Alternative: T-Beam Supreme (GPS included)
- **Antenna**: 
  - Outdoor: 6-8 dBi omnidirectional (915MHz)
  - Lightning protection required
- **Power**: 
  - Primary: POE HAT or 12V DC with backup battery
  - UPS recommended for critical nodes
- **Enclosure**: 
  - IP65 rated outdoor enclosure
  - Ventilation for heat dissipation
- **Storage**: 32GB+ SD card (industrial grade)

#### Handheld Radios (For Pilots)
Awaiting Rokland recommendations, but likely candidates:
- **T-Echo**: Budget-friendly, good battery life
- **T-Beam Supreme**: GPS, good range, proven reliability
- **Heltec V3**: Excellent performance, built-in display
- **RAK WisBlock**: Modular, expandable

### Remote Management Architecture

```
┌─────────────────┐     ┌──────────────────┐
│  Management PC  │────▶│  VPN/SSH Tunnel  │
└─────────────────┘     └──────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Central MQTT      │
                    │   Broker/Server     │
                    └─────────────────────┘
                         │         │
                         ▼         ▼
            ┌────────────────┐  ┌────────────────┐
            │  Node 1: AAA   │  │ Node 2: DOTHQ  │
            │  Raspberry Pi   │  │ Raspberry Pi   │
            └────────────────┘  └────────────────┘
```

### Software Stack

#### On Raspberry Pi Nodes:
1. **Raspbian OS Lite** (headless)
2. **Meshtastic Python CLI**
3. **MQTT Bridge** (meshtastic-mqtt)
4. **Remote Access**:
   - SSH with key-based auth
   - WireGuard VPN for secure tunnel
   - Optional: Tailscale for easier management
5. **Monitoring**:
   - Prometheus node exporter
   - Custom telemetry scripts
   - Automatic updates via cron

#### Central MQTT Server:
1. **Mosquitto MQTT Broker**
2. **InfluxDB** for time-series telemetry
3. **Grafana** for visualization
4. **Node-RED** for automation/alerts

### Configuration Management

#### Channel Configuration:
```yaml
# Standard Alaska Aviation Channel
channel:
  name: "AK-Aviation"
  psk: [Generated PSK]
  settings:
    region: US
    modem_preset: LONG_FAST
    hop_limit: 7
    
# Node-specific settings
node:
  role: ROUTER_CLIENT
  telemetry:
    device_update_interval: 900
    environment_update_interval: 900
  mqtt:
    enabled: true
    address: "your-mqtt-server.com"
    username: "node-xxx"
    encryption_enabled: true
```

### Deployment Checklist

#### Pre-Deployment:
- [ ] Flash latest Meshtastic firmware
- [ ] Configure channel settings
- [ ] Set up MQTT credentials
- [ ] Test radio range at location
- [ ] Prepare mounting hardware
- [ ] Configure Pi with remote access

#### Installation:
- [ ] Mount antenna with proper grounding
- [ ] Connect radio to Pi via USB
- [ ] Verify MQTT connection
- [ ] Test message flow
- [ ] Document GPS coordinates
- [ ] Photo documentation

#### Post-Installation:
- [ ] Monitor first 24h telemetry
- [ ] Verify remote access
- [ ] Share connection details with pilots
- [ ] Schedule regular health checks

### Testing Protocol

#### Phase 1: Basic Messaging (Weeks 1-2)
- Test group messaging between pilots
- Monitor message delivery rates
- Collect user feedback
- Test range limits

#### Phase 2: Telemetry Collection (Weeks 3-4)
- Verify MQTT data flow
- Build telemetry dashboard
- Monitor node health
- Analyze traffic patterns

#### Phase 3: Weather Integration (Week 5+)
- Deploy weather data injection
- Test TAF/METAR delivery
- Integrate with map display
- Pilot training on weather features

### Security Considerations

1. **Network Security**:
   - Change default passwords
   - Use strong PSK for channels
   - Enable MQTT TLS/SSL
   - Firewall rules on Pi

2. **Physical Security**:
   - Lockable enclosures
   - Tamper-evident seals
   - GPS location monitoring

3. **Data Privacy**:
   - Anonymous node IDs
   - No PII in telemetry
   - Encrypted MQTT traffic

### Budget Estimate

#### Per Fixed Node:
- Raspberry Pi 4B Kit: $150
- LoRa Radio Module: $50-120
- Antenna & Cable: $50-100
- Enclosure & Mount: $100
- Power/Battery: $75
- **Total: ~$425-545**

#### Per Handheld Radio:
- Device: $35-150 (depending on model)
- Case/Accessories: $20
- **Total: ~$55-170**

### Next Steps

1. **Immediate Actions**:
   - Review Rokland's radio recommendations
   - Order hardware for 2 fixed nodes
   - Set up MQTT server infrastructure
   - Create admin dashboard

2. **This Week**:
   - Finalize channel configuration
   - Write installation guides
   - Contact Adam and DOT for install scheduling
   - Create pilot onboarding materials

3. **Next Month**:
   - Deploy both fixed nodes
   - Distribute radios to test pilots
   - Begin telemetry collection
   - Iterate based on feedback

### Remote Management Scripts

Need to create:
- Node health monitoring script
- Automatic update script
- Channel management tool
- Telemetry export utility
- Emergency reset procedure

Would you like me to start developing any of these components first?
