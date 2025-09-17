# Hardware Specifications

## Universal IoT Board Design (Proposed)

### Overview
Dual-processor board supporting both Meshtastic mesh networking and complex edge computing for aviation applications.

### Processors
- **Primary**: nRF52840 (Nordic Semi)
  - ARM Cortex-M4F @ 64MHz
  - 1MB Flash, 256KB RAM
  - Bluetooth 5.0 / 802.15.4
  - Ultra-low power consumption
  - Runs Meshtastic firmware

- **Secondary**: RP2040 (Raspberry Pi)
  - Dual ARM Cortex-M0+ @ 133MHz
  - 2MB Flash (external)
  - 264KB SRAM
  - USB host capability
  - Handles complex processing

### Communication
- **LoRa Radio**: SX1262 or SX1276
  - 902-928 MHz ISM band (US)
  - +22dBm transmit power
  - -148dBm sensitivity
  - 50+ mile range at altitude

### Sensor Interfaces
- **12x I2C ports** (6 per processor)
  - JST-SH 4-pin connectors
  - 3.3V, GND, SDA, SCL
  - Hot-swappable sensors

### Supported Sensors
- BME680: Temperature, humidity, pressure, gas
- LIS3DH: 3-axis accelerometer
- GPS: GNSS positioning
- INA219: Voltage/current monitoring
- Any I2C compatible sensor

### Power Management
- **Input**: 6-30V (automotive/aviation)
- **Battery**: 18650 Li-ion support
- **Solar**: MPPT charging capability
- **Consumption**: <2W average

### Physical
- **Size**: 100x60mm (estimated)
- **Mounting**: 4x M2.5 holes
- **Enclosure**: IP67 capable
- **Temperature**: -40°C to +85°C

### Connectivity
- USB-C for programming/power
- U.FL antenna connectors
- Inter-processor: I2C/SPI/UART
- MicroSD slot (optional)

### Cost Target
- **Prototype**: $50-75
- **Production (100+)**: $30-40
- **Production (1000+)**: $20-25

## Aircraft Node (Simplified)

### Minimum Viable Configuration
- RAK4631 WisBlock Core
- RAK19001 Base Board
- RAK1906 BME680 Environment Sensor
- GPS Module
- External antenna
- **Total Cost**: ~$50

### Installation
1. Mount in avionics bay
2. Connect to aircraft power (12V/24V)
3. Route antenna to windshield/belly
4. Pair with pilot's tablet via Bluetooth

## Ground Station

### Enhanced Configuration
- Dual-processor board (as above)
- Weather station sensors
- VHF SDR for voice monitoring (optional)
- Ethernet/WiFi gateway
- Solar panel + battery
- High-gain antenna on mast
- **Total Cost**: ~$500

### Deployment Locations (Priority)
1. Remote airports without weather stations
2. Mountain passes (Rainy Pass, Mystic Pass)
3. High-traffic corridors
4. Communities with active pilots

## Antenna Specifications

### Aircraft
- Type: 1/4 wave whip or dipole
- Gain: 2-3 dBi
- Mounting: Magnetic or adhesive

### Ground Station
- Type: Collinear or Yagi
- Gain: 6-12 dBi
- Height: 20+ feet recommended

### Mountain Repeater
- Type: Omnidirectional collinear
- Gain: 6-9 dBi
- Lightning protection required

## Certification Notes

- FCC Part 15: ISM band operation
- No FAA TSO required (supplemental equipment)
- Recommend professional installation for permanent aircraft mounting
