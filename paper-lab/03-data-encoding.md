# 03 — Data Encoding

> How weather data is represented internally vs on the wire. Where TAIGA is enough; where SkyBridge proposes hand-packed extensions; where they bake off head-to-head.

## The two-format split

```
SOURCE DATA (METAR text, JSON, GRIB2, etc.)
              │
              ▼ parse + normalize
INTERNAL STORAGE (SQLite, decimal, queryable)
              │
              ▼ encode for transmission
WIRE FORMAT (TAIGA + SkyBridge envelope, compact)
              │
              ▼ transmit (LoRa / MQTT / etc.)
              │
              ▼ decode at receiver
INTERNAL STORAGE (SQLite, identical fields)
              │
              ▼ render
USER PRESENTATION (kt or m/s or mph, °C or °F, etc.)
```

**Internal format**: universal, queryable, ints + floats with full precision, units in their native canonical form.
**Wire format**: compact, integer-quantized, lossy where lossless precision is wasteful.
**User format**: whatever the pilot prefers. Conversion happens at render time.

This separation matters because:
- Storage queries (`SELECT * FROM wx_obs WHERE pressure_mb < 1000`) must work on the canonical form, not on encoded bytes
- Mesh transmissions must be small at any cost
- User preferences shouldn't pollute the database

## Canonical units (locked, forever)

| Field | Aviation unit | Internal SQLite type | Wire encoding |
|---|---|---|---|
| Wind direction | degrees true (FROM) | INTEGER 0–359, -1 = VRB | 9-bit, 511 = VRB |
| Wind speed | knots | INTEGER | 7-bit (0–127) |
| Wind gust offset | knots above sustained | INTEGER | 6-bit (0–63 above sustained) |
| Temperature | °C | REAL | 8-bit (–60 to +60, 0.5° step) |
| Dewpoint depression | °C below temp | INTEGER | 6-bit (0–32 °C spread) |
| Altimeter setting | millibars | REAL | 11-bit (900–1100, 0.1 mb step) |
| Visibility | statute miles | REAL | 6-bit log scale |
| Cloud cover | percent | INTEGER 0–100 | 5-bit (4% step) |
| Cloud base altitude | feet AGL | INTEGER | 12-bit (0–4095, ×100 ft) |
| Altitude | feet MSL | INTEGER | 16-bit |
| Latitude | WGS84 decimal degrees | REAL | geohash (variable) |
| Longitude | WGS84 decimal degrees | REAL | geohash (variable) |
| Timestamp | Unix epoch seconds | INTEGER | TAIGA reference + 10-min ticks |

**Aviation conventions are the canonical** because:
- Every FAA chart, every METAR, every aviation radio uses them
- Conversion to user prefs is lossless one-way (convert at render only)
- All current pilots already understand them; no retraining

User preferences (kt → m/s, mb → inHg, ft → m) are display-layer only.

## Quantization — why some loss is fine

Aviation uses these units because they have inherent precision boundaries that match operational need:

- **Wind direction in degrees true**: pilots talk in 10° increments ("two-eight-zero") for ATC, never tighter than 5°. Our 9-bit (0.7° resolution) is 7× tighter than human-meaningful precision.
- **Wind speed in knots**: METARs report integer knots. Storing 0.1-kt resolution would invent precision the source doesn't have.
- **Pressure in mb**: altimeters resolve to 0.01 inHg ≈ 0.34 mb. Our 0.1 mb resolution is finer than altimeter calibration.
- **Visibility**: METARs report ¼-mile increments. Our 6-bit log scale captures every reportable value plus a few extra granularity bits.
- **Cloud cover**: METARs report SKC/CLR/FEW/SCT/BKN/OVC — six discrete categories. Our 4% step over 0–100 captures more granularity than the source.

Quantization is not lossy in any operationally meaningful sense — it discards precision that wasn't there to begin with.

## Time encoding

### Internal format

```
ts: INTEGER (Unix epoch seconds, 4 bytes)
```

Universal SQL queries, every language has it, converts cleanly to:

- ISO 8601 (`2026-05-01T19:53:00Z`) for human display
- GPS time (Unix - 315964800 + leap_seconds) for aviation receivers
- Local time + tz for pilot UI display

### Wire format

TAIGA's reference-time + 10-minute ticks:

```
TAIGAMessage ::= SEQUENCE {
    reference-time Time,           -- "2026-05-01 19:00 Z" — sent once per message group
    day Day,                       -- monday, tuesday, ...
    payload-sequence SEQUENCE OF Payload   -- each payload has a 7-bit time offset
}

Payload time encoding:
  0  = at reference-time
  1  = +10 min from reference
  2  = +20 min
  ...
  143 = +1430 min (≈23h 50m)
```

**Storage**: 7 bits per per-event timestamp. A burst of 10 observations sharing one reference = 10 × 7 bits + 1 reference timestamp ≈ ~12 bytes total time information.

**Limit**: each TAIGA message group covers a 24-hour window. For long-term replay, send a new reference time per day or per session.

### High-precision events (lightning, ADS-B)

For sub-second accuracy, use 32-bit GPS time (week + time-of-week):

```
gps_time = (week:16, tow_x10:20)  -- 4.5 bytes, 0.1 second resolution
```

Reserved for events where 1-min precision is too coarse (lightning strikes, ADS-B position-time-stamps for collision avoidance, radar sweep returns).

## Location encoding

### Internal format

```
lat: REAL  (WGS84 decimal degrees, e.g. 61.1860)
lon: REAL  (WGS84 decimal degrees, e.g. -150.0390)
```

All databases speak this. Every GPS speaks this.

### Wire format — geohash

TAIGA uses geohash, a base32 encoding that maps lat/lon to a string with variable precision:

| Geohash chars | Precision | Bytes (ASCII) | Bits |
|---|---|---|---|
| 4 | ±20 km | 4 | 20 |
| 5 | ±2.4 km | 5 | 25 |
| 6 | ±610 m | 6 | 30 |
| 7 | ±76 m | 7 | 35 |
| 8 | ±19 m | 8 | 40 |
| 9 | ±2.4 m | 9 | 45 |

**Default for SkyBridge wire format**: **geohash-7** (76 m accuracy). That's:

- Tighter than the typical METAR siting tolerance (sensor at the airport reference point can be 100s of feet away from runway)
- Tighter than aviation-relevant decisions (a wind reported within 76 m of a runway end is functionally at the runway end)
- Tighter than terrain effects on local wind (winds vary on 100 m scales in mountainous terrain)
- Coarser than GPS receiver fix accuracy (modern receivers do 2 m), but we don't need that

**Use cases for higher precision**:

- **geohash-8** (19 m) for lightning strike location, runway-specific wind, airport center markers
- **geohash-9** (2.4 m) for ADS-B aircraft position when collision avoidance matters

**Use cases for lower precision**:

- **geohash-5** (2.4 km) for SIGMETs and TFRs — they're area-bounded, not point-localized
- **geohash-4** (20 km) for AIRMETs and large-region forecasts

The wire format carries the geohash as a base32 string (7 ASCII bytes for 76 m) or, when minimizing further, as 35 raw bits packed.

## SkyBridge envelope encoding

Detailed in [`02-protocol-stack.md`](02-protocol-stack.md#l5--skybridge-envelope). Recap:

```
[ver:1][prio:1][msg_id:4][ttl_s:2][payload_len:2][payload:N]
```

10 bytes overhead before the TAIGA payload. Little-endian for all multi-byte fields (modern hardware native).

## Bake-off: TAIGA vs SkyBridge L1+2 vs gzip

A test METAR:

```
METAR PALH 011953Z 13008KT 10SM FEW040 SCT100 08/M04 A2992 RMK AO2 SLP076=
```

Encoded three ways:

### Option A: Raw text + gzip (baseline)

```
Plain text:    96 bytes
gzip-9:        72 bytes  (24% reduction)
brotli-9:      63 bytes  (34% reduction)
zstd-9:        69 bytes  (28% reduction)
```

### Option B: TAIGA ASN.1 (NASA-published)

```
Reference time + day:   3 bytes
Station (PALH → 4 chars LL bytes): 2 bytes
Wind (130°/8kt):        2 bytes (9-bit dir + 7-bit spd)
Visibility (10sm):      1 byte (log scale)
Clouds (FEW040, SCT100): 4 bytes (2 layers, type+alt)
Temp/dewpoint (08/M04): 2 bytes
Altimeter (29.92):      2 bytes (mb-quantized)
Time offset:            1 byte (7 bits)
ASN.1 framing overhead: 5 bytes
─────────────────────────────────
TOTAL TAIGA:            22 bytes  (77% reduction vs raw text)
```

### Option C: SkyBridge hand-packed L1+2

```
Bit-packed (no ASN.1 overhead):
  Geohash-7 location:     35 bits
  Time (10-min ticks):     7 bits
  Wind direction:          9 bits
  Wind speed:              7 bits
  Wind gust offset:        6 bits
  Visibility:              6 bits
  Cloud cover (max):       5 bits
  Cloud low layer alt:    12 bits
  Cloud mid layer alt:    12 bits
  Temperature:             8 bits
  Dewpoint depression:     6 bits
  Pressure:               11 bits
  Quality flags:           4 bits
  ─────────────────────────────
  TOTAL bits:            128 bits = 16 bytes
                                  
With SkyBridge envelope (+10 B): 26 bytes total
```

### Comparison summary

| Encoding | Bytes | Ratio vs raw |
|---|---|---|
| Raw METAR text | 96 | baseline |
| gzip | 72 | 75% |
| brotli | 63 | 66% |
| zstd | 69 | 72% |
| **TAIGA + envelope** | **32** (22 + 10) | **33%** |
| **SkyBridge L1+2 + envelope** | **26** (16 + 10) | **27%** |

Both purpose-built encodings beat general-purpose compressors by 2–3×. SkyBridge L1+2 beats TAIGA by ~20% for this single METAR. **For the empirical bake-off in [`07-bench-test-spec.md`](07-bench-test-spec.md)**, we'll measure across 1000 real METARs; the winner depends on payload variety.

The hypothesis: TAIGA has ASN.1 framing overhead (~5 B) that fixed-cost amortizes well over batched messages. SkyBridge L1+2 has no framing but pays per-message envelope cost. **TAIGA likely wins for batched transmissions**; SkyBridge L1+2 likely wins for one-off mesh broadcasts.

The empirical answer goes in [`09-taiga-addendum.md`](09-taiga-addendum.md).

## Storage schema (SQLite)

```sql
CREATE TABLE wx_obs (
  -- Identity
  ts              INTEGER NOT NULL,    -- Unix epoch seconds
  anchor          TEXT NOT NULL,        -- 'PALH' or 'MWOS:133'
  source          TEXT NOT NULL,        -- 'metar', 'mwos', 'nws_grid', 'om_gfs', etc.
  
  -- Location (decimal degrees, WGS84)
  lat             REAL,
  lon             REAL,
  
  -- Quantized canonical fields
  dir_deg         REAL,                 -- wind from-direction, -1 = VRB
  speed_kt        REAL,                 -- wind speed knots
  gust_kt         REAL,                 -- gust speed knots above sustained
  temp_c          REAL,                 -- Celsius
  dewpoint_c      REAL,                 -- Celsius
  pressure_mb     REAL,                 -- millibars
  visibility_sm   REAL,                 -- statute miles
  cloud_pct       REAL,                 -- percent
  cloud_low_pct   REAL,
  cloud_mid_pct   REAL,
  cloud_high_pct  REAL,
  freezing_ft     INTEGER,              -- freezing level feet AGL
  precip_mm       REAL,                 -- mm/hr
  
  -- Provenance
  raw_json        TEXT,                 -- full original record for forensic
  
  PRIMARY KEY (ts, anchor, source)
);
CREATE INDEX idx_wx_obs_ts ON wx_obs(ts);
CREATE INDEX idx_wx_obs_anchor_ts ON wx_obs(anchor, ts);
```

This is the schema currently in production at the Pi (see `kneeboard_dev.py` `_wx_db_init()`).

## Conversion at render time

The user picks display preferences in their UI; conversion is one-way and lossless within the precision of the storage format:

```javascript
// Aviation → user preferences (display-only)

speedToUserUnit(kt, prefs) {
  switch (prefs.speed_unit) {
    case 'kt':   return kt;
    case 'mph':  return kt * 1.15078;
    case 'kmh':  return kt * 1.852;
    case 'm/s':  return kt * 0.51444;
  }
}

pressureToUserUnit(mb, prefs) {
  switch (prefs.pressure_unit) {
    case 'mb':   return mb;
    case 'inHg': return mb * 0.02953;
    case 'hPa':  return mb;  // identical
    case 'psi':  return mb * 0.014504;
  }
}

altitudeToUserUnit(ft, prefs) {
  switch (prefs.altitude_unit) {
    case 'ft':   return ft;
    case 'm':    return ft * 0.3048;
  }
}

tempToUserUnit(c, prefs) {
  switch (prefs.temp_unit) {
    case 'C':    return c;
    case 'F':    return c * 9/5 + 32;
    case 'K':    return c + 273.15;
  }
}
```

Conversion preserves 6+ significant figures, well beyond the storage precision. Round-trip kt → m/s → kt is exact at integer knots.

## What to never store

Anti-patterns we explicitly avoid:

- **Storing in user-preferred units** — DB queries break when one user wants kt and another wants mph
- **Storing as text strings** — "13008KT" is harder to query than `dir_deg=130, speed_kt=8`
- **Storing geohash in DB** — slower than indexed lat/lon REAL; geohash is wire-only
- **Storing TAIGA-encoded bytes in main records** — they're storage of *one rendering* of the data; keep the canonical fields decoded
- **Storing absolute timestamps as ISO 8601 text** — convert at display, store epoch
- **Storing local time** — always UTC

The exception: a `raw_json` column in `wx_obs` keeps the full original record for forensic reconstruction. Lets us recover from a broken parser without losing source data.

## Open encoding questions

- Should the wire format support a `delta` payload that encodes only the fields that changed since a reference snapshot? (Saves bytes on routine updates; complicates dedup logic.)
- Should geohash precision be per-payload (METARs at 7, SIGMETs at 5)? Currently planned: **yes, encoded inline**.
- For multi-layer cloud cover: should we store all layers individually or just `low/mid/high` percentages? Source data has up to 5 layers (FEW010 SCT040 BKN080 OVC150 + cumulus mention). Currently planned: 3-band percentages, which loses some structure but is dramatically simpler.
- TAIGA's geohash precision is fixed at 7 chars in the spec. Our addendum proposes variable precision per-payload — needs explicit ASN.1 schema change.

These remain open as of v0.1.
