# Alaska Aviation Weather Data Sources

## Overview
For SkyBridge to provide valuable weather information through the mesh network, you'll need reliable access to aviation weather data (TAF/METAR, NOTAMs, etc.). Here are your options for Alaska:

## Primary Weather Data Sources

### 1. NOAA/National Weather Service (FREE - Recommended)
The best starting point - all US government weather data is public domain.

#### Aviation Weather Center (AWC)
- **URL**: https://aviationweather.gov
- **Data Available**: 
  - METARs (current conditions)
  - TAFs (forecasts)
  - AIRMETs/SIGMETs
  - PIREPs
- **API Access**: 
  - Text Data Server: https://aviationweather.gov/dataserver
  - New REST API: https://aviationweather.gov/data/api/
- **Format**: XML, CSV, JSON
- **Cost**: FREE
- **Rate Limits**: Reasonable for non-commercial use

**Example API Call**:
```bash
# Get METAR for Anchorage
curl "https://aviationweather.gov/api/data/metar?ids=PANC&format=json"

# Get TAFs for multiple Alaska airports
curl "https://aviationweather.gov/api/data/taf?ids=PANC,PAFA,PAJN&format=json"
```

#### Alaska Aviation Weather Unit (AAWU)
- **Specializes in Alaska weather**
- **Products**: Alaska-specific forecasts, volcanic ash warnings
- **Contact**: https://www.weather.gov/aawu/
- **Direct data feed available**

### 2. FAA SWIM (System Wide Information Management)
Professional-grade data feed from FAA.

- **What**: Real-time operational data feed
- **Includes**: Weather, NOTAMs, airport status, traffic
- **Access**: Requires registration and approval
- **Cost**: Free for qualifying organizations
- **Best for**: Official operations
- **Application**: https://www.faa.gov/air_traffic/technology/swim

### 3. Commercial Weather APIs

#### CheckWX
- **URL**: https://www.checkwxapi.com/
- **Pros**: Simple API, good documentation
- **Cost**: Free tier (3000 requests/day), Paid plans available
- **Alaska Coverage**: Excellent

#### AVWX (Aviation Weather)
- **URL**: https://avwx.rest/
- **Pros**: Modern REST API, Python library
- **Cost**: Free tier (4000/day), $10/month unlimited
- **Format**: JSON, very clean

#### OpenWeatherMap Aviation
- **URL**: https://openweathermap.org/api/aviation
- **Cost**: Contact for pricing
- **Pros**: Global coverage, reliable infrastructure

### 4. Direct AWOS/ASOS Phone Numbers
For areas with no internet weather:

- **Format**: Automated phone systems
- **Access**: Call to get current METAR
- **List**: Available from Alaska DOT&PF
- **Use Case**: Backup data source

### 5. Alaska-Specific Sources

#### Alaska DOT&PF Weather Cameras
- **URL**: https://511.alaska.gov/
- **Data**: Visual conditions at airports
- **API**: Available for integration

#### University of Alaska Weather
- **URL**: https://weather.gi.alaska.edu/
- **Research-grade data**
- **May provide special access**

## Implementation Strategy

### Phase 1: Basic Weather (Start Here)
```python
import requests
import json

def get_alaska_weather():
    """Fetch weather for major Alaska airports"""
    
    # List of Alaska airports to monitor
    airports = ['PANC', 'PAFA', 'PAJN', 'PAOM', 'PABE', 
                'PADQ', 'PAEN', 'PASC', 'PAKT', 'PADU']
    
    # NOAA API endpoint
    base_url = "https://aviationweather.gov/api/data/metar"
    
    weather_data = []
    for airport in airports:
        response = requests.get(f"{base_url}?ids={airport}&format=json")
        if response.status_code == 200:
            weather_data.append(response.json())
    
    return weather_data
```

### Phase 2: Enhanced Data
- Add TAFs for forecast information
- Include AIRMETs/SIGMETs
- Integrate PIREPs from pilots

### Phase 3: Value-Added Services
- Weather trend analysis
- Route-specific weather
- Automated alerts

## Cost Comparison

| Source | Monthly Cost | Pros | Cons |
|--------|-------------|------|------|
| NOAA/NWS | FREE | Official source, reliable | Rate limits |
| CheckWX | $0-29 | Good API, easy setup | Request limits |
| AVWX | $0-10 | Modern API, Python lib | Basic features only |
| FAA SWIM | FREE* | Real-time, comprehensive | Approval process |

*Requires qualification

## Recommended Approach

1. **Start with NOAA/NWS** (FREE)
   - Implement basic METAR/TAF retrieval
   - Test with pilot community
   - Monitor usage patterns

2. **Add Alaska-Specific Sources**
   - AAWU products
   - DOT camera feeds
   - Local AWOS phones as backup

3. **Consider Commercial API** 
   - If you hit rate limits
   - Need guaranteed uptime
   - Want simplified integration

## Data Flow Architecture

```
NOAA/NWS API → Your Server → TAIGA Encoding → Mesh Network
     ↓              ↓                             ↓
   METARs      Compression                  Pilot Devices
   TAFs        Filtering                    (Meshtastic)
   NOTAMs      Caching
```

## Legal Considerations

- **NOAA Data**: Public domain, free to redistribute
- **FAA Data**: Check specific feed agreements
- **Commercial APIs**: Review terms of service
- **Liability**: Always include disclaimers about supplemental use

## Next Steps

1. **Register for NOAA API key** (optional but recommended)
2. **Set up data ingestion server**
3. **Implement TAIGA encoding**
4. **Test with sample data**
5. **Deploy to mesh network**

## Alaska-Specific Weather Challenges

- **Sparse reporting stations**: Many areas have no AWOS/ASOS
- **Rapid weather changes**: Cached data expires quickly  
- **Unique phenomena**: Ice fog, williwaws, volcanic ash
- **Remote locations**: Consider satellite weather data

## Contact for Alaska Weather

**NOAA Alaska Region**
- Forecast Office: 907-266-5105
- AAWU: 907-266-5185
- Email: aawu.operations@noaa.gov

**Alaska DOT&PF Aviation**
- Weather Systems: 907-269-0730
- Your contact: Already established!

Ready to start integrating weather data into SkyBridge!
