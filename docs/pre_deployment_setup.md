# Pre-Deployment Setup Tasks

## While Waiting for Hardware Delivery

### 1. MQTT Server Setup (Priority)

#### Option A: Quick Cloud Setup (Recommended to start)
```bash
# DigitalOcean/Vultr/Linode - $10-20/month
# Ubuntu 22.04 LTS, 2GB RAM minimum

# One-liner setup script:
curl -sSL https://get.docker.com | sh
docker run -d \
  --name mosquitto \
  -p 1883:1883 \
  -p 8883:8883 \
  -v mosquitto-data:/mosquitto/data \
  -v mosquitto-logs:/mosquitto/log \
  eclipse-mosquitto

# Or use our full setup from mqtt_telemetry_setup.md
```

#### Option B: Local Server at DOT
- Coordinate with IT for VM or physical server
- Static IP allocation
- Firewall rules for ports 1883/8883
- SSL certificate from DOT CA

### 2. Channel Configuration

Generate secure channel settings:
```python
# generate_channel.py
import secrets
import base64

# Generate random PSK
psk_bytes = secrets.token_bytes(32)
psk_base64 = base64.b64encode(psk_bytes).decode('utf-8')

print(f"Channel Name: AK-Aviation")
print(f"PSK (save this): {psk_base64}")
print(f"Region: US")
print(f"Modem Preset: LONG_FAST")
```

Save configuration in password manager!

### 3. Device Management Spreadsheet

Create tracking sheet with columns:
- Device ID (will be on device)
- Device Type (T-Echo, T-Beam, etc.)
- Assigned To (pilot name/location)
- Node Role (ROUTER_CLIENT, CLIENT)
- MAC Address
- Firmware Version
- Deployment Date
- Notes

### 4. Monitoring Dashboard

Set up basic Grafana dashboard:
```yaml
# docker-compose.yml
version: '3.8'
services:
  influxdb:
    image: influxdb:2.7
    ports:
      - "8086:8086"
    volumes:
      - influxdb-data:/var/lib/influxdb2

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    volumes:
      - grafana-data:/var/lib/grafana
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=changeme

volumes:
  influxdb-data:
  grafana-data:
```

### 5. Documentation Portal

Consider setting up simple docs site:
- GitHub Pages with pilot guides
- FAQ section
- Contact form for feedback
- Coverage map placeholder

### 6. Testing Environment

Prepare testing setup:
1. **Range Testing Protocol**
   - GPS app for recording locations
   - Signal strength logging
   - Message delivery confirmation

2. **Test Messages Library**
   - Weather updates format
   - Position reports
   - Emergency scenarios

3. **Feedback Forms**
   - Google Form for pilot feedback
   - Issue tracking spreadsheet
   - Feature request list

### 7. Legal/Compliance Prep

- [ ] Part 15 compliance documentation
- [ ] User agreement draft
- [ ] Privacy policy (no PII storage)
- [ ] Alaska DOT approval for nodes

### 8. Training Materials

Prepare for pilot training:
- Quick reference cards (print-ready)
- 5-minute video tutorial script
- Common issues troubleshooting
- Best practices handout

### 9. Flashing Station Setup

Prepare computer for device configuration:
```bash
# Install Python and tools
pip install meshtastic esptool adafruit-nrfutil

# Download firmware files
mkdir ~/meshtastic-firmware
cd ~/meshtastic-firmware
wget https://github.com/meshtastic/firmware/releases/latest/download/firmware-1.3.zip
```

### 10. Network Architecture Diagram

Create visual for stakeholders:
```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Pilots    │────▶│ Fixed Nodes  │────▶│ MQTT Server │
│  (Radios)   │     │  (AAA/DOT)   │     │ (Telemetry) │
└─────────────┘     └──────────────┘     └─────────────┘
                            │                     │
                            ▼                     ▼
                    ┌──────────────┐     ┌─────────────┐
                    │   Weather    │     │  Dashboard  │
                    │   Gateway    │     │  (Grafana)  │
                    └──────────────┘     └─────────────┘
```

## Week 1 Priorities

1. **Monday**: Set up MQTT server
2. **Tuesday**: Configure monitoring dashboard  
3. **Wednesday**: Create channel config and docs
4. **Thursday**: Prepare flashing station
5. **Friday**: Test end-to-end flow with simulator

## Success Metrics Setup

Configure tracking for:
- Messages per day
- Active nodes count
- Network uptime percentage
- Coverage area (sq miles)
- Pilot engagement rate

## Contact List

Prepare communications for:
- Node hosts (Adam at AAA, DOT contact)
- Initial pilot testers
- Technical support team
- Stakeholder updates

Ready to start the pre-deployment tasks! The MQTT server setup is the most critical - once that's running, you can start testing with Meshtastic simulators even before the hardware arrives.
