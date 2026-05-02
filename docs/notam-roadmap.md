# NOTAM / FAA Comms Roadmap

What flight-information services SkyBridge currently pulls, what's still
missing, and the concrete paths to fill those gaps.

This is a living document — update as upstream APIs change or as paid feeds
get evaluated.

---

## What the kneeboard renders today

| Layer | Source | Refresh | Status |
|---|---|---|---|
| **METARs** (raw + JSON) | `aviationweather.gov/api/data/metar` | 5 min | ✅ Live, sorted by distance from PALH, age-stamped |
| **TAFs** | `aviationweather.gov/api/data/taf` | 5 min | ✅ Live, paired with METARs |
| **SIGMETs / AIRMETs** | `aviationweather.gov/api/data/airsigmet` | 5 min | ✅ Live, polygons rendered |
| **G-AIRMETs** | `aviationweather.gov/api/data/gairmet` | 5 min | ✅ Live, polygons rendered |
| **PIREPs** | `aviationweather.gov/api/data/pirep` | 5 min | ✅ Live, point markers |
| **Volcanic-ash SIGMETs** | `aviationweather.gov/api/data/airsigmet?hazard=ASH` | 5 min | ✅ Live |
| **NWS alerts (Alaska)** | `api.weather.gov/alerts/active?area=AK` | 5 min | ✅ Live, polygon-based |
| **CWAs** (Center Weather Advisories — short-fuse 1–2 h hazards from ARTCCs) | `aviationweather.gov/api/data/cwa` | 5 min | ✅ Live, ZAN-only filter, polygons |
| **TFRs** (Temporary Flight Restrictions — security, space-ops, fires) | `tfr.faa.gov/tfrapi/exportTfrList` | 5 min | ⚠️ List only — **polygon shapes not exposed by public FAA API** |
| **Local VHF airband transcripts** | onboard RTL-SDR + Whisper STT | live | ✅ Live, archived to NVMe |
| **MWOS** (Montis Corp automated weather observation stations) | `api.montiscorp.com/mwos` (authed) | 5 min | ✅ 14 stations, 4 cameras each |

## What's NOT yet on the kneeboard

| Layer | Why missing | Path to enable |
|---|---|---|
| **FAA NOTAMs** (Notices to Airmen — runway closures, navaid outages, lighting) | FAA NOTAM API requires registration | See "FAA NOTAM API" below |
| **TFR polygon shapes** | The public TFR list endpoint returns only metadata; shape KML was removed in the FAA's `tfr3` UI rewrite | See "TFR polygons" below |
| **FIS-B graphical products** (graphical NOTAMs, regional NEXRAD, winds aloft, AIRMET-T turbulence) | We receive the 978 UAT signal but `dump978-fa` only decodes position; the FIS-B uplink frames are not yet parsed | See "FIS-B uplink" below |
| **AHRS / wake-turbulence airspace** | Not historically used by GA in AK; low priority | — |
| **Approach charts / TPP** | Out of scope for the moving-map; pilots use ForeFlight / Garmin Pilot for plates | — |
| **ATIS / AWOS audio recording** | We monitor adjacent VHF freqs; ATIS loops aren't decoded structurally | Future: dedicate one RTL-SDR to ATIS-loop recording, parse for METAR-equivalent text |

---

## FAA NOTAM API

**Endpoint scaffolded:** `/api/notams` on the kneeboard returns either the
NOTAM list (when configured) or `{status: "disabled", reason: "..."}` when
the API key isn't set. The kneeboard's FAA-Comms sidebar already shows the
disabled-state info card so the path is wired end-to-end; only the upstream
auth is missing.

### To enable

1. Register at **<https://api.faa.gov>** for an API key. Two values are issued:
   `client_id` and `client_secret`.
2. Set them as environment variables on the kneeboard service:
   ```ini
   # /etc/systemd/system/kneeboard.service.d/notam-key.conf
   [Service]
   Environment=FAA_NOTAM_CLIENT_ID=<your-client-id>
   Environment=FAA_NOTAM_KEY=<your-client-secret>
   ```
3. `sudo systemctl daemon-reload && sudo systemctl restart kneeboard kneeboard-dev`
4. Reload `/api/notams` — it will start returning real data.

The endpoint is wired to the FAA's `external-api.faa.gov/notamapi/v1/notams`
service. Default ICAO scope is the 15 major Alaska airports we care about
(PANC, PAFA, PAJN, PADQ, PAOM, PABE, PAEN, PAMR, PALH, PABR, PAOT, PAKN,
PAVD, PAEI, PATK). Override with `?icao=PANC,PAFA` query param.

### Schema (already serving)

```json
{
  "status": "ok",
  "count": 12,
  "ts": 1777594800,
  "notams": [
    {
      "id": "07/123",
      "icao": "PANC",
      "type": "RWY",
      "issued": "2026-04-30T17:00:00Z",
      "effective": "2026-04-30T18:00:00Z",
      "expires": "2026-05-15T06:00:00Z",
      "text": "RWY 7L/25R CLSD ALL OPS"
    }
  ]
}
```

---

## TFR polygons

The TFR list endpoint at `tfr.faa.gov/tfrapi/exportTfrList` is public and
returns metadata for every active TFR nationwide (notam_id, type, facility,
state, description, creation_date). **It does not return polygon coordinates.**

The previous `/save_pages/<id>.xml` endpoints that used to serve KML have
been removed in the FAA's `tfr3` UI rewrite. Inspecting the live `tfr3`
Nuxt JS bundle reveals only `getTfrList` is called publicly; the shape data
is inlined into the bundle at deploy time, not fetched per-request.

### What we do today

For each AK/ZAN TFR, the kneeboard parses the description string for
patterns like `"17NM NE OF FAIRBANKS, AK"` or `"CLEAR, AK"` and places a
red pin at the approximated location. The popup notes that the pin is an
approximation. Result: pins are in the right neighborhood (within ~5 nm)
but no actual area boundary is drawn.

### Unlock paths

| Path | Cost | Effort |
|---|---|---|
| **Scrape the `tfr3` Nuxt bundle** for inlined shape data per build | free | high — fragile, breaks on any FAA UI redeploy |
| **FlightAware AeroAPI** — `flightxml/3/operationActivity?…` includes TFR shapes | ~$0.01–0.05 per call, free tier exists | low — well-documented JSON |
| **ADSBExchange API** — has TFR polygon data alongside ADS-B | $5–10/mo | low |
| **OpenAIP TFR layer** — community-maintained | free, self-host data | medium — needs daily sync |
| **FAA's own SWIM TFR feed** — NOTAM XML over JMS | free with FAA-NESG account | high — JMS infra non-trivial |

Recommendation: start with **FlightAware AeroAPI** if budget allows. The
free tier (1000 calls/mo) is enough for a development node.

---

## FIS-B uplink

We physically receive the 978 MHz UAT signal via the second FlyCatcher
RTL-SDR. `dump978-fa` decodes position (ADS-B Out from GA aircraft) but
does NOT parse the FIS-B uplink frames that contain:

- Graphical METARs and TAFs (12 weather products)
- NEXRAD CONUS + regional radar tiles
- Graphical AIRMETs (Tango / Sierra / Zulu)
- Winds aloft
- Special-use airspace status
- Convective SIGMETs

### Unlock path

Add `dump978-fa --decode-uplink` plus a parser. The reference implementation
is `978tools` (or the newer `dump978-faa-uplink-decoder`). Output can be:

- Live overlay layer on the kneeboard map (NEXRAD tiles)
- Server-side cache that mirrors aviationweather.gov when internet is down
  (the FIS-B station is the canonical "off-grid" weather source)

Effort: moderate — needs a parser + a dedicated tile server for the radar
imagery. Worth pursuing because it gives the system the
**no-internet weather** capability it advertises.

---

## Architecture notes

- All FAA-comms data is **server-cached** with `_wx_fetch()` (5 min TTL by
  default). Endpoints serve from cache so the kneeboard UI is responsive
  even when upstream is slow.
- All endpoints follow the same flat-JSON shape so the future LoRa-mesh
  forwarder can pack them into compact binary (CBOR / protobuf) without
  schema changes.
- Sort order in the FAA-comms sidebar is **distance from the Pi (Lake
  Hood / PALH)** so closest hazards always render at the top.
- Stale data is age-stamped and color-fades from green (<60 min) →
  yellow (1–2 h) → orange (2 h+) so a pilot can immediately see when an
  upstream feed is slow or offline.

---

## Open questions for future iterations

1. Should NOTAMs be persisted to disk? Currently in-memory cache only;
   loss on service restart is acceptable for short-fuse data but might
   be useful for compliance / replay.
2. Do we need a separate "Class B / Class C" airspace overlay distinct
   from the VFR Sectional? The sectional renders airspace boundaries but
   they're hard to see at low zoom levels.
3. Should the FIS-B uplink data take priority over internet-fetched weather
   when both are available, or be an explicit fallback only?
4. AeroAPI / ADSBExchange: which one becomes the primary paid feed?
5. How do we surface "comms degraded" — when we lose ADSB.fi, lose
   internet, lose VPN-overlay access, lose central-server agent gateway? A status strip somewhere?
