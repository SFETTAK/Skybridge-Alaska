# ADS-B Integration Architecture

**Local receiver + ADSB.fi statewide feed, merged for 500nm coverage of Alaska.**

---

## Overview

The DOT-VHF ground station has a single RTL-SDR dongle (ADSB1090) receiving 1090 MHz ADS-B from aircraft within radio line-of-sight (~100-200nm depending on altitude). To provide statewide coverage, the system supplements this with the ADSB.fi open data API, which aggregates ADS-B from hundreds of feeders worldwide.

Two components handle this:
- **adsb-combine.py** — Background daemon that merges local + remote data
- **tar1090-combo** — Second tar1090 instance reading the merged feed

---

## Data Flow

```
                    ┌─────────────────────────────┐
                    │   RTL-SDR (ADSB1090 dongle)  │
                    │   1090 MHz Extended Squitter  │
                    └──────────┬──────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │      readsb         │
                    │  /run/readsb/       │
                    │  aircraft.json      │
                    └───────┬─────────────┘
                            │
              ┌─────────────┼──────────────────────┐
              │             │                      │
              ▼             ▼                      ▼
     tar1090 (local)   adsb-combine.py       vhf-pipeline.py
     port 8504         (every 8 sec)         (callsign lookup)
     local only             │
                            │
                  ┌─────────┴──────────┐
                  │                    │
                  ▼                    ▼
       /run/readsb/            ADSB.fi API
       aircraft.json           (two 250nm circles)
       (LOCAL — wins              │
        on conflicts)             │
                  │               │
                  └───────┬───────┘
                          │ merge
                          ▼
               ┌─────────────────────┐
               │  /run/combine1090/  │
               │  aircraft.json      │
               │  (merged output)    │
               └──────────┬──────────┘
                          │
                          ▼
               ┌─────────────────────┐
               │   tar1090-combo     │
               │   port 8505 (HTTP)  │
               │   port 8506 (HTTPS) │
               └──────────┬──────────┘
                          │
                ┌─────────┴──────────┐
                │                    │
                ▼                    ▼
          Web browser          kneeboard.py
          (pilot tablet)       /api/traffic
```

---

## ADSB.fi Statewide Feed

ADSB.fi provides a free, open API for ADS-B data. The combiner fetches from two overlapping 250nm-radius circles that together cover approximately 500nm of Southcentral and Interior Alaska.

### Coverage Circles

| Circle | Center Lat | Center Lon | Radius | Coverage Area |
|--------|-----------|-----------|--------|---------------|
| 1 (Southcentral) | 61.17 | -150.0 | 250 nm | Anchorage, Kenai, Kodiak, Valdez |
| 2 (Interior) | 63.5 | -150.0 | 250 nm | Fairbanks, Denali, North Slope overlap |

API endpoint format:
```
https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{dist}
```

Rate limiting: minimum 10 seconds between requests per circle. User-Agent: `SkyBridge-AK/1.0`.

---

## Merging Strategy (adsb-combine.py)

The combiner runs in an infinite loop, writing merged data every 8 seconds (matching tar1090's INTERVAL setting).

### Priority Rules

1. **Remote data loaded first** (lower priority baseline)
2. **Local readsb data overwrites** on ICAO hex collision
3. **Metadata enrichment**: When an aircraft exists in both local and remote, the local position/altitude data is kept, but missing metadata fields are filled from ADSB.fi:
   - `r` (registration)
   - `t` (aircraft type)
   - `desc` (type description)
   - `ownOp` (owner/operator)
   - `year` (manufacture year)

### Output Format

The merged `aircraft.json` matches readsb's format so tar1090 can read it natively:

```json
{
  "now": 1710000000.0,
  "messages": 12345,
  "aircraft": [
    {"hex": "a12345", "flight": "ASA527", "lat": 61.17, "lon": -149.99, ...},
    ...
  ]
}
```

### Atomic Writes

To prevent tar1090 from reading a partially-written file, the combiner writes to a `.tmp` file first, then uses `os.replace()` for an atomic rename.

---

## tar1090-combo Setup

A second tar1090 instance reads from `/run/combine1090/` instead of `/run/readsb/`.

### Service Chain

```
readsb.service
    │
    ▼
adsb-combine.service     (After=readsb, Wants=network-online)
    │                     Writes /run/combine1090/aircraft.json
    ▼
tar1090-combo.service    (After=adsb-combine, Requires=adsb-combine)
    │                     Reads /run/combine1090/
    ▼
lighttpd                 Serves on :8505 (HTTP) and :8506 (HTTPS)
```

### Configuration

- `/etc/default/tar1090-combo` — Environment: INTERVAL=8, HISTORY_SIZE=450, PTRACKS=8, 978 merge enabled
- `/usr/local/share/tar1090/html-combo/config.js` — Browser config: centered on Anchorage, ESRI satellite tiles, dark mode, GPS follow, range rings at 50/100/200/300nm, 8-hour tracks, aircraft photos enabled

### HTTPS for GPS

Browser geolocation requires HTTPS. lighttpd serves the combo view over SSL on port 8506:
- Certificate: `/etc/lighttpd/certs/server.pem` (self-signed)
- Config: `/etc/lighttpd/conf-enabled/97-tar1090-combo-ssl.conf`
- Pilot accepts cert warning once, then map follows GPS position

---

## Kneeboard Integration

The kneeboard app (`kneeboard.py`) has its own ADS-B fetcher that does the same merge independently (local readsb + ADSB.fi). This is separate from `adsb-combine.py` because:

1. The kneeboard needs the data in a different format (enriched with `src` field to distinguish local vs remote)
2. The kneeboard serves merged data directly via `/api/traffic` without writing to disk
3. Refresh rate is 8 seconds (matching ADSB.fi cache TTL)

---

## Comparison: Local vs Combined

| Feature | tar1090 (local, :8504) | tar1090-combo (:8505/:8506) |
|---------|----------------------|---------------------------|
| Data source | /run/readsb/ only | /run/combine1090/ (merged) |
| Coverage | ~100-200nm (line of sight) | ~500nm (Southcentral + Interior AK) |
| Aircraft count | 5-30 typical | 50-200+ typical |
| Metadata | Limited (local decode only) | Enriched (registration, type, operator) |
| Latency | Real-time (<1s) | 8-10s (API polling interval) |
| GPS follow | No (HTTP only) | Yes (HTTPS on :8506) |
