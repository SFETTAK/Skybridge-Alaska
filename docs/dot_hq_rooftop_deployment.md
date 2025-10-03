# DOT HQ Rooftop Deployment Plan

## POE-Powered Multi-Antenna Array

### System Overview

```
Building Interior                     Rooftop
                                     
Network ─── 90W POE ─── CAT6 ───┬─── POE L2 Switch ───┬─── Pi + Radio 1 + Antenna (8 dBi)
           Injector            │   (in cabinet)     ├─── Pi + Radio 2 + Antenna (5 dBi)
                              │                     └─── Pi + Radio 3 + Antenna (3 dBi)
                              │
                              └─── Weatherproof Cabinet
```

## Power Budget Calculation

### Per Node Power Requirements:
- **Raspberry Pi 4B**: 5W idle, 7W peak
- **LoRa Radio**: 2W average, 5W transmit
- **POE HAT inefficiency**: 15% loss
- **Safety margin**: 20%

**Per node**: (7W + 5W) × 1.15 × 1.20 = 16.6W

### Total System:
- **3 nodes**: 3 × 16.6W = 50W
- **POE switch**: 10W
- **Cabinet fan/heater**: 20W
- **Total draw**: 80W (within 90W budget! ✓)

## Equipment List

### Network Infrastructure
```yaml
POE Injector:
  Model: Ubiquiti EP-54V-150W or similar
  Output: 90W+ at 48V
  Location: Network closet/IDF
  
POE Switch (Rooftop):
  Model: Mikrotik CRS112-8P-4S-IN
  Features:
    - 8 POE+ ports
    - Industrial temp range
    - Layer 2 management
    - Outdoor rated enclosure
  Power: 48V POE input, distributes to nodes
```

### Node Hardware (×3)
```yaml
Raspberry Pi Setup:
  - Pi 4B (4GB)
  - POE+ HAT (802.3at)
  - Industrial SD card
  - Aluminum heatsink case
  
Radio Options:
  - RAK WisBlock with USB
  - Heltec V3
  - T-Beam Supreme
  
Weatherproofing:
  - IP67 enclosure per node
  - Breather valve
  - Desiccant pack
```

### Antenna Mounting (Rubber Membrane Safe)

```yaml
Non-Penetrating Mounts:
  Model: CommScope FRB-series or similar
  
  Features:
    - Weighted base (no roof penetration)
    - Rubber pads (membrane protection)
    - 1.5-2" mast pipe
    - Wind rated to 100mph
    
  Ballast Required:
    - 8 dBi (tall): 200 lbs concrete blocks
    - 5 dBi (medium): 150 lbs
    - 3 dBi (short): 100 lbs
```

## Rooftop Layout

```
DOT HQ Roof Plan (not to scale):

         N ↑
         
    [Cabinet]
        |
        |---- 25ft CAT6 ----[Node 1: 8 dBi]
        |                          ○
        |                         /|\
        |                        / | \
        |                    40ft  |  40ft
        |                      /   |   \
        |---- 50ft CAT6 ------○----+----○
        |                  [Node 2]  [Node 3]
        |                  5 dBi     3 dBi
        |
    [Building Entry]
```

## Weatherproof Cabinet Configuration

### Cabinet Specifications
```yaml
Model: Hoffman A242412LP or similar
Size: 24"W × 24"H × 12"D
Rating: NEMA 4X (IP66)
Material: Fiberglass or aluminum

Internal Layout:
  Top: POE switch with ventilation
  Middle: 3× Pi mounting rails
  Bottom: Power distribution
  Side: Cable entry glands
```

### Environmental Control
```yaml
Cooling:
  - Filtered fan (thermostat controlled)
  - Activate at 95°F internal
  
Heating:
  - 50W enclosure heater
  - Activate at 32°F internal
  - Prevents condensation
  
Monitoring:
  - Temperature/humidity sensor
  - Connected to Pi GPIO
  - MQTT alerts
```

## Cable Management

### Ethernet Runs
```yaml
Cable Spec: CAT6A outdoor-rated
Features:
  - UV resistant jacket
  - Gel-filled (waterproof)
  - Shielded (EMI protection)
  - Messenger wire (support)

Routing:
  - Flexible conduit on roof
  - Cable tray where possible
  - Service loops at each end
  - Lightning arrestor at entry
```

### Antenna Cables
```yaml
Type: LMR-400 or equivalent
Connectors: N-type (weatherproof)
Lengths:
  - Node 1: 15ft
  - Node 2: 20ft
  - Node 3: 15ft
  
Protection:
  - Rubber roof boots
  - Coax seal tape
  - Drip loops
  - Grounding kits
```

## Network Architecture

### VLAN Configuration
```yaml
VLAN 100: Management
  - Pi SSH access
  - SNMP monitoring
  - Switch management
  
VLAN 200: Mesh Data
  - MQTT traffic
  - Node communication
  - Internet gateway
  
VLAN 300: Guest/Test
  - Isolated test radios
  - No production access
```

### IP Addressing
```yaml
Management (VLAN 100):
  Switch: 10.100.1.1
  Node 1: 10.100.1.11
  Node 2: 10.100.1.12
  Node 3: 10.100.1.13
  
Mesh Data (VLAN 200):
  Gateway: 10.200.1.1
  MQTT: 10.200.1.10
```

## Installation Checklist

### Pre-Installation
- [ ] Roof structural survey
- [ ] Network cable pathway
- [ ] Power capacity check
- [ ] Equipment staging

### Mounting Phase
- [ ] Install non-penetrating mounts
- [ ] Add concrete ballast
- [ ] Mount antennas at different heights
- [ ] Verify 40ft triangle spacing

### Cabling Phase
- [ ] Run CAT6 to cabinet location
- [ ] Install cabinet with ventilation
- [ ] Connect POE switch
- [ ] Run cables to antenna locations

### Node Installation
- [ ] Mount Pi units in enclosures
- [ ] Connect radios via USB
- [ ] Attach antenna cables
- [ ] Weatherproof all connections

### Testing Phase
- [ ] Power on via POE
- [ ] Verify network connectivity
- [ ] Check MQTT data flow
- [ ] Test antenna patterns
- [ ] Measure coverage

## Maintenance Access

### Remote Monitoring
```bash
# SSH to any node
ssh pi@10.100.1.11

# Check status
meshtastic --info
sudo systemctl status mesh-monitor

# View logs
journalctl -u mesh-monitor -f
```

### Physical Access Plan
- Roof hatch location: ___
- Safety equipment required
- Two-person rule
- Weather restrictions

## Budget Estimate

### One-Time Costs
- 90W POE Injector: $150
- POE L2 Switch: $300
- Weatherproof Cabinet: $400
- 3× Pi + POE HAT: $450
- 3× Radio modules: $300
- 3× Antennas: $200
- 3× Non-pen mounts: $600
- Cables & misc: $300
- **Total Hardware: $2,700**

### Installation
- Professional install: $1,500
- Or DIY with facilities help

### Annual Costs
- Power (80W × 24/7): $70
- Maintenance: $200
- **Total Annual: $270**

## Special Considerations for Alaska

### Winter Operations
- Cabinet heater essential
- Ice accumulation on antennas
- Snow load on mounts
- Thermal cycling stress

### Summer Operations  
- Cooling for 24hr sun
- UV degradation
- Thermal expansion

### Wildlife
- Bird deterrent on antennas
- Secure cabinet latches
- No exposed wiring

## Future Expansion

The POE switch has 5 spare ports for:
- Weather station
- Camera
- Additional radios
- ADS-B receiver
- Backup systems

This setup gives you professional-grade reliability with easy maintenance!
