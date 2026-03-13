# SkyBridge Kneeboard — Pilot Guide

**Tablet-optimized moving map with ADS-B traffic, weather overlays, and live VHF transcripts.**

Designed for one-handed operation in the cockpit. Runs as a Flask web app on the DOT-VHF ground station and is accessed via any browser on the same network.

---

## Access

| Endpoint | URL | Notes |
|----------|-----|-------|
| HTTP (no GPS) | `http://192.168.1.81:8083` | Works but no GPS tracking |
| HTTPS (GPS-enabled) | `https://192.168.1.81:8443` | Accept cert warning once, then GPS works |

GPS tracking requires HTTPS because browsers restrict the Geolocation API to secure contexts. The HTTPS endpoint uses a lighttpd reverse proxy with a self-signed certificate.

---

## Map Layers (12)

The layer control panel (grid icon, bottom-right) has 12 toggleable layers:

| # | Layer | Source | Refresh | Default |
|---|-------|--------|---------|---------|
| 1 | **Satellite** | ESRI World Imagery | tile cache | OFF |
| 2 | **VFR Sectional** | ArcGIS US VFR Sectional Charts | tile cache | OFF |
| 3 | **NEXRAD Radar** | Iowa State Mesonet (NEXRAD N0Q) | 5 min | OFF |
| 4 | **METAR Dots** | aviationweather.gov METAR API | 5 min | ON |
| 5 | **SIGMETs/AIRMETs** | aviationweather.gov airsigmet API | 5 min | ON |
| 6 | **PIREPs** | aviationweather.gov PIREP API (3hr, 300nm) | 5 min | ON |
| 7 | **ADS-B Traffic** | Local readsb + ADSB.fi statewide | 5 sec | ON |
| 8 | **VHF Radio** | Local vhf-pipeline transcripts | 5 sec | ON |
| 9 | **MWOS Stations** | Montis Corp MWOS API | 5 min | ON |
| 10 | **G-AIRMETs** | aviationweather.gov G-AIRMET API | 5 min | ON |
| 11 | **Volcanic Ash** | aviationweather.gov intl SIGMET (VA) | 5 min | ON |
| 12 | **NWS Alerts** | api.weather.gov active alerts (AK) | 5 min | ON |

### Layer Details

**METAR Dots (Layer 4)** — Color-coded dots at 30 Alaska airports showing flight category:
- Green = VFR, Blue = MVFR, Red = IFR, Magenta = LIFR
- Tap a dot to see raw METAR text, temperature, wind, visibility, altimeter
- Stations: PANC, PAMR, PAED, PALH, PAFA, PAJN, PABE, PAOM, PADQ, PABR, and 20 more

**SIGMETs/AIRMETs (Layer 5)** — Polygon overlays with color coding:
- Red = SIGMET, Amber = AIRMET, Cyan = Icing, Orange = Turbulence, Magenta = IFR/MTN OBSC

**PIREPs (Layer 6)** — Pilot weather reports within 300nm and 3 hours:
- Markers show position, altitude, aircraft type, turbulence, and icing
- Urgent PIREPs are highlighted

**ADS-B Traffic (Layer 7)** — Aircraft positions from two sources:
- Local readsb (RTL-SDR at station) for nearby traffic
- ADSB.fi statewide feed (two 250nm circles) for all of Southcentral/Interior Alaska
- Aircraft show hex, flight/callsign, registration, type, altitude, ground speed, track
- Local aircraft enriched with ADSB.fi metadata (registration, type, operator)

**VHF Radio (Layer 8)** — Last 30 transcripts from the VHF pipeline, newest first:
- Shows frequency, timestamp, and transcript text
- Radio panel expands/collapses at bottom of screen

**MWOS Stations (Layer 9)** — Live observations and camera images from Montis Corp automated weather stations:
- Currently monitoring: Lake Hood (PALH), Merrill Field (PAMR), Nuiqsut (PAQT), Kaktovik, Port Graham
- Each station shows: temperature, dewpoint, humidity, wind, pressure, precipitation
- Camera images from cardinal directions where available

**G-AIRMETs (Layer 10)** — Graphical AIRMETs with polygon overlays:
- IFR (red), mountain obscuration (magenta), turbulence (orange), icing (cyan), freezing level (blue), surface wind (yellow)
- Shows base/top altitudes and forecast hour

**Volcanic Ash (Layer 11)** — International volcanic ash SIGMETs:
- Critical for Alaska with multiple active volcanoes
- Shows ash cloud polygon, source volcano, movement, altitude band

**NWS Alerts (Layer 12)** — National Weather Service active alerts for Alaska:
- Winter storms, wind advisories, freezing rain, etc.
- Color-coded by severity: Extreme (red), Severe (dark red), Moderate (orange), Minor (yellow)
- Shows polygon if available, headline, description, and instructions

---

## Data Sources and Refresh Rates

| Data | Source | Refresh | Cache TTL |
|------|--------|---------|-----------|
| ADS-B traffic | Local readsb + ADSB.fi API | 5 sec | 8 sec |
| VHF transcripts | Local NVMe transcript files | 5 sec | none |
| METARs/TAFs | aviationweather.gov API | 5 min | 5 min |
| METAR map dots | aviationweather.gov API | 5 min | 5 min |
| SIGMETs/AIRMETs | aviationweather.gov API | 5 min | 5 min |
| PIREPs | aviationweather.gov API | 5 min | 5 min |
| G-AIRMETs | aviationweather.gov API | 5 min | 5 min |
| Volcanic ash | aviationweather.gov API | 5 min | 5 min |
| NWS alerts | api.weather.gov | 5 min | 5 min |
| MWOS stations | Montis Corp API | 5 min | 5 min |
| Station status | Local systemctl | on load | none |

---

## API Endpoints

All endpoints return JSON. The kneeboard HTML page calls these from the browser.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Kneeboard HTML page (single-page app) |
| `/api/traffic` | GET | Merged ADS-B: local + ADSB.fi. Returns `{aircraft, total, local, adsbfi}` |
| `/api/radio?limit=N` | GET | Recent VHF transcripts (default 20). Returns `[{ts, freq, text}]` |
| `/api/weather?stations=PANC,PAMR` | GET | METAR + TAF for given stations |
| `/api/metarmap` | GET | METARs for 30 Alaska stations with lat/lon for map dots |
| `/api/sigmets` | GET | Active SIGMETs and AIRMETs with polygon coordinates |
| `/api/pireps` | GET | PIREPs within 300nm/3hr of Anchorage |
| `/api/gairmet` | GET | Graphical AIRMETs with polygon coordinates |
| `/api/volash` | GET | Volcanic ash international SIGMETs |
| `/api/nwsalerts` | GET | NWS active alerts for Alaska |
| `/api/mwos` | GET | MWOS automated weather stations (Montis Corp) |
| `/api/station` | GET | Ground station health (service status, location) |

---

## MWOS Integration (Montis Corp)

The kneeboard integrates with Montis Corp's MWOS (Meteorological Weather Observation System) stations via their REST API. This was reverse-engineered from their web interface.

- API base: `https://api.montiscorp.com/mwos/{station_id}`
- Authentication: Static API key in request header (`authorization` header)
- Returns: Site metadata, current observations, camera image URLs

### Monitored Stations

| Station ID | Name | ICAO |
|-----------|------|------|
| 133 | Lake Hood | PALH |
| 1 | Merrill Field | PAMR |
| 265 | Merrill Field 2 | PAMR |
| 529 | Nuiqsut | PAQT |
| 430 | Kaktovik | -- |
| 694 | Port Graham | -- |
| 232 | Port Townsend | -- |

Each station provides:
- Observations: temp, dewpoint, humidity, wind direction/speed/gust, pressure, precipitation
- Cameras: up to 4 cardinal-direction images with timestamps
- Site metadata: lat/lon, status, maintenance messages

---

## GPS / HTTPS Requirements

The browser Geolocation API requires a secure context. Without HTTPS, the map works but cannot track the pilot's position.

**Setup:**
1. Self-signed certificate at `/etc/lighttpd/certs/server.pem`
2. lighttpd config at `/etc/lighttpd/conf-enabled/98-kneeboard-ssl.conf`
3. Reverse proxies Flask :8083 through HTTPS :8443
4. Pilot opens `https://192.168.1.81:8443` and accepts the certificate warning once
5. Map centers on pilot GPS position and updates continuously

The tar1090-combo map has the same setup on port 8506.

---

## UI Controls

- **Floating action buttons** (bottom-right): Destination, Layers, Weather panels
- **Radio panel** (bottom bar): Tap to expand, shows recent VHF transcripts
- **Weather panel**: METAR/TAF for nearby stations (PANC, PAMR, PAED, PALH)
- **Destination panel**: Enter a destination for distance/bearing calculation
- **Layer panel**: Toggle all 12 layers on/off with visual indicators
