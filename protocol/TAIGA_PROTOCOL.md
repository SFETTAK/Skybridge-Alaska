# TAIGA ASN.1 Protocol Integration

## Overview
The TAIGA (Traffic and Atmospheric Information for General Aviation) protocol was developed by NASA Ames Research Center (Joseph L. Rios) specifically for efficient aviation data transmission in bandwidth-limited environments like Alaska.

## Key Features
- **80% compression** compared to raw text
- **Lossy compression** optimized for aviation use cases
- **Unified format** for PIREPs, METARs, NOTAMs, weather polygons
- **Time-efficient** encoding using reference time + offsets

## Message Structure

### Base Message Format
```asn1
TAIGAMessage ::= SEQUENCE {
    reference-time Time,
    day Day,
    payload-sequence SEQUENCE (SIZE(0..3)) OF Payload
}
```

### Supported Payload Types
- PIREPs (Pilot Weather Reports)
- METARs (Aviation Routine Weather Reports)
- Weather Polygons (Turbulence, Icing, etc.)
- NOTAMs (Notices to Airmen)
- Emergency Messages
- System Messages

## Location Encoding (Geohashing)

Instead of traditional aviation references (VOR radials, airport identifiers), TAIGA uses geohashing:

- **4 characters**: ±20km accuracy
- **5 characters**: ±2.4km accuracy  
- **7 characters**: ±76m accuracy

Example: Anchorage Airport (PANC)
- Lat/Lon: 61.1744°N, 149.9964°W
- Geohash-7: `bdq8p4r`

## Time Encoding

All times are encoded as offsets from a reference time in 10-minute increments:

```
Reference: 1200Z
PIREP at 1342Z = 10 ticks (100 minutes after reference)
```

This encoding uses only 7 bits for 24 hours of time range.

## Compression Examples

### Traditional PIREP
```
UA /OV ANC270031 /TM 1342 /FL110 /TP C208 
/SK BKN040-TOP065 /TA M15 /WV 27045KT /TB MOD
```
**Size: 96 bytes**

### TAIGA Encoded (Binary)
```
[Binary representation]
```
**Size: 18 bytes (81% reduction)**

## Implementation for SkyBridge

### Encoding (Ground Station)
```python
import asn1tools

# Load TAIGA schema
compiler = asn1tools.compile_files('taiga.asn1')

# Create METAR message
metar_data = {
    'reference_time': {'hour': 12, 'minute': 0},
    'day': 'monday',
    'payload': {
        'metar': {
            'station': 'panc',
            'time': 0,  # At reference time
            'temperature': -15,
            'wind': {'direction': 'w', 'speed': 45},
            'visibility': 10
        }
    }
}

# Encode to binary
encoded = compiler.encode('TAIGAMessage', metar_data)
# Result: ~20 bytes vs 80+ for text METAR
```

### Decoding (Aircraft/App)
```javascript
// React Native app
import { TAIGADecoder } from './protocol/taiga';

const decoder = new TAIGADecoder();

// Receive from Bluetooth/mesh
const binaryData = await bluetooth.receive();

// Decode TAIGA message
const weatherData = decoder.decode(binaryData);

// Display to pilot
updateWeatherDisplay(weatherData);
```

## Benefits for Alaska Aviation

1. **Bandwidth Efficiency**: 80% less data to transmit
2. **Battery Savings**: Shorter transmission times
3. **Greater Range**: Lower data rates = better sensitivity
4. **More Updates**: Can send 5x more messages in same bandwidth

## Integration with Meshtastic

Meshtastic channels can be configured for TAIGA payloads:

```python
# Meshtastic configuration
channel.psk = generate_psk("skybridge-alaska")
channel.compression = CompressionType.NONE  # TAIGA handles compression
channel.data_rate = ChannelSettings.DataRate.LONG_SLOW  # Max range
```

## Testing Tools

### Encoder Test
```bash
python tools/taiga_encoder.py --input weather.txt --output weather.bin
# Compression ratio: 0.19 (81% savings)
```

### Range Calculator
```bash
python tools/range_calc.py --power 22 --data-size 20 --terrain mountain
# Estimated range: 47 miles at 10,000ft AGL
```

## License Note
The TAIGA ASN.1 specification is open source (NASA publication).
Credit: Joseph L. Rios, NASA Ames Research Center

## References
- NASA Technical Memorandum: NASA/TM—2015–218427
- "A Formal Messaging Notation for Alaskan Aviation Data"
- Available at: https://ntrs.nasa.gov
