# 06 — Data-Mule Scenarios

> Worked examples of how data physically moves through the SkyBridge network, with timing, byte counts, and contact-window math. These scenarios drive the bench-test specification in [`07-bench-test-spec.md`](07-bench-test-spec.md).

## Why this section exists

The previous sections describe the protocol. This section describes **what it actually feels like to use it.** Every scenario here is a real situation an Alaska pilot or remote resident encounters, traced through the protocol stack so the design can be checked against the friction of reality.

## Scenario A — The cabin north of Dillingham

> *Thought experiment. A resident living on a lake north of Dillingham, no cellular, no satellite. They have power from a generator, a Meshtastic radio, a small SBC controller, and an antenna. They want current weather before going out on the lake. Specific hardware is illustrative; SkyBridge does not currently ship a cabin kit.*

```
[FIGURE: data-mule.svg — to render]
Map showing Dillingham (PADL) with cellular coverage indicated, lake to the
north with cabin marker (no coverage), and a Cessna trajectory from PADL
flying north. Contact window highlighted as the aircraft passes overhead —
~4 minutes of LoRa range. Data exchange visualized as a packet flow during
the contact window.
```

### Setup

| Element | Value |
|---|---|
| **Cabin location** | ~20 nm north of PADL on Lake Aleknagik |
| **Cabin equipment** | Mesh radio, small SBC controller, optional weather sensor, cabin-mounted antenna. Specifics TBD per deployment. |
| **Cabin connectivity** | None except LoRa |
| **Pilot equipment** | Standard Cessna 206 + Meshtastic radio + cellular-connected phone (loaded with cached SkyBridge data from Dillingham) |
| **Pilot trajectory** | Departs PADL, climbs to 3000 ft AGL, transits the lake region heading north |

### Contact window math

```
Cessna at 3000 ft AGL has LoRa line-of-sight to:
  Free-space horizon = 1.23 × √(altitude_ft) = 67 nm
  Practical LoRa SF7 range at altitude ≈ 5–10 km radius around aircraft
  
For a 120-kt aircraft passing within 5 km of cabin:
  Contact slant range  = 5 km
  Ground-track length within radio range:
    chord ≈ 2 × √(slant² - altitude²) ≈ 2 × √(25 - 0.84) = ~9.8 km
    time_in_range = 9.8 km / (120 kt × 0.514 m/s) = ~4.0 minutes
  
At ~1500 bps practical LoRa SF7 throughput:
  bits exchanged in 4 min = 360 kbits = 45 KB
  
TAIGA-encoded payload: ~270 KB possible if optimal SF used (SF6 short range
but 5500 bps); realistic budget ~30–50 KB
```

### What gets exchanged

In the 4-minute window, ordered by priority:

| Class | Items | Approximate bytes (TAIGA-encoded) |
|---|---|---|
| SAFETY | Active SIGMETs / TFRs / weather alerts in region | 200 B / item × 5 items = 1 KB |
| CRITICAL | METARs / SPECIs from PADL, PAII, PAEW (Dillingham + nearby) | 30 B / METAR × 6 = 180 B |
| ROUTINE | MWOS observations from regional sites | 20 B / obs × 10 = 200 B |
| BULK | Time-lapse / historical / forecast trends | bandwidth-dependent |

Total for SAFETY + CRITICAL + ROUTINE: ~1.4 KB. Easily fits in the contact window with overhead. The pilot's device transmits these as a single bundled session.

The cabin's controller processes incoming bundle, deduplicates, stores in local SQLite, updates its small display.

### What the cabin user sees

Before overflight:
> *Last update: 47 minutes ago (from previous overflight)*

During overflight (cabin radio LED blinks):
> *Receiving update from N123AB...*

After overflight:
> *Last update: 30 seconds ago (data: METAR PADL 011953Z 14008KT...)*

If 4+ hours pass with no overflight, UI shows a "data stale" warning. If 12+ hours pass, alerts the user to seek another data source.

### What the pilot sees

Before contact:
> *(no indicator — passive transmission)*

Cabin radio detected nearby:
> *Mesh contact: 1 stationary node detected (cabin-PADL-N01)*
> *Sharing 1.5 KB of regional data... done.*

After contact:
> *(no indicator — pilot continues without interaction)*

The pilot does not need to know they served as a data mule. The exchange happens automatically in the background. They benefit secondarily: the cabin user, when they later visit Dillingham, may have observations to contribute back.

### What the SkyBridge backend learns

Dell hub eventually receives (when pilot lands at PADL with cellular):

```
Audit log entry:
  ts: 1777670400
  event: data_mule_pickup
  pilot_node: pilot-N123AB
  cabin_node: cabin-PADL-N01
  region: bristol_bay
  bytes_exchanged_to_cabin: 1480
  bytes_exchanged_from_cabin: 380
  contact_duration_s: 240
  cabin_local_observation_uploaded: temp=8.2C, pressure=1014.3mb (cabin sensor)
```

The cabin's sensor reading is a new ground-truth observation in a previously dark zone. Over months, contributions like this would densify the multi-source archive SkyBridge maintains for research purposes.

## Scenario B — Regional handoff during cross-state flight

> *A pilot flies PALH → PAFA in a Cessna 208. Cellular coverage is spotty along the route. SkyBridge transparently transitions through 3 regional clusters.*

### The trajectory

```
PALH (Anchorage Bowl region) — strong LTE
     ↓
PAAQ → PATK (Mat-Su region) — partial LTE
     ↓
Hurricane Gulch (no coverage)
     ↓
PAEI (Interior region) — strong LTE near Fairbanks
     ↓
PAFA (Interior region)
```

### Tier transitions during flight

| Time | Phase | Tier | What changes in UI |
|---|---|---|---|
| T+0 | On ramp at PALH, LTE | FULL | Statewide map, all sources, time-lapse enabled |
| T+5 min | Climb-out, leaving Anchorage Bowl | FULL → REGIONAL | UI dims model layers slightly; "leaving Anchorage Bowl" toast |
| T+25 min | Mat-Su region, intermittent LTE | REGIONAL → ROUTE | Map auto-zooms to corridor; only obs along route render |
| T+45 min | Hurricane Gulch, no signal | ROUTE → MESH | UI greys non-essential; SAFETY+CRITICAL only; "mesh: 2 nodes visible" indicator |
| T+90 min | Approaching Fairbanks, LTE returns | MESH → REGIONAL | UI re-hydrates from cellular; missed data backfills |
| T+95 min | Landing PAFA | REGIONAL → FULL | Full Interior region data + post-flight weather replay available |

### What the pilot does manually

**Nothing.** The transitions are automatic. The pilot's job is to fly. The UI's job is to be useful at every tier without requiring the pilot's attention.

## Scenario C — Multiple passenger-as-relay on commercial flight

> *A passenger on Alaska Airlines flight from Seattle to Anchorage has SkyBridge installed on their phone. Inflight wifi is enabled. The aircraft is at FL360 with line-of-sight to dozens of mesh nodes simultaneously.*

### Why this matters

The passenger is **carrying SkyBridge data 1500 nm in a single hop** at jet altitude with broadband internet. Their device:

1. Subscribes to all Alaska region MQTT topics over inflight wifi
2. Caches the full statewide multi-source observation set + active alerts
3. Listens passively on the Meshtastic radio (if they have one) for any nodes within range
4. As the flight enters Alaska airspace, begins broadcasting cached data to nodes that come into LoRa view

A single Alaska Airlines flight from Seattle to Anchorage with one SkyBridge-equipped passenger effectively **provides backhaul to every regional node along the route**. Inflight wifi → AKL airspace mesh = a massive opportunistic data mule.

### The math on coverage

- A jet at FL360 has LoRa line-of-sight to a 220 nm radius
- A typical Anchorage approach flight path crosses 4–5 regions
- Each region has 5–15 mesh nodes
- Total nodes potentially served per flight: 30–60
- At 5–10 kbps practical LoRa from altitude, 90 min flight time = 100+ MB

This scales with **zero additional infrastructure** — every passenger with the app becomes a relay.

### What's required for this to work

| Element | Status |
|---|---|
| Phone Meshtastic capability | Native on Android via Bluetooth-paired radio; iOS support exists |
| App in passive-relay mode | Not yet built; documented in section 07 |
| Inflight wifi reliability | Alaska Air, Delta, United all offer; usability varies |
| User opt-in | Required (the app asks "share cached weather as you fly?") |

## Scenario D — Truck driver on the Dalton Highway

> *A truck driver hauling fuel north on the Dalton Hwy from Fairbanks to Prudhoe Bay. Cellular coverage is intermittent. They have SkyBridge installed on a tablet mounted in the truck.*

### The trajectory

```
PAFA (LTE) → Livengood (no signal) → Coldfoot (limited) → Atigun Pass (no signal)
→ Toolik Lake (no signal) → Deadhorse / PABR (LTE)
```

### Roles served

The truck functions as a **mobile data mule with periodic backhaul**:

- At PAFA: pulls full Alaska state weather + all alerts
- En route at Coldfoot: reaches Coldfoot's mesh node, exchanges data
- Atigun Pass: stationary mesh nodes (if installed) along the pipeline corridor get fresh data
- At Deadhorse: re-uplinks anything cached, picks up Arctic-region data
- Returns south reverses the pattern

The truck adds value to the network in proportion to how often it makes the trip. Dalton Hwy haulers run weekly; they become reliable rotating data mules.

## Scenario E — Volcanic ash event coordination

> *Bogoslof or Cleveland erupts (active AK volcanoes). Volcanic ash SIGMET issued. Aircraft within 200 nm need to reroute immediately.*

This is the canonical SAFETY-class scenario.

### Flow

1. **NWS Anchorage Volcanic Ash Advisory Center** issues a VAA
2. SkyBridge ingests it via `aviationweather.gov` API within 30 seconds
3. Encoded as TAIGA polygon payload, SAFETY priority class
4. Published to MQTT topic `sb/safety/sigmet/<id>` with retained flag (anyone subscribing later gets it immediately)
5. Bridged to every regional Pi via backhaul
6. Each regional Pi rebroadcasts on its Meshtastic mesh with reliable transmission (acked)
7. Pilots' devices receive within seconds (via cellular) or within minutes (via mesh)
8. UI alerts the pilot with audio + visual; map renders the polygon with cross-hatching
9. SAFETY class persists for the duration of the alert; rebroadcast every 10 minutes until expiry

### Latency budget

| Step | Target | Worst case |
|---|---|---|
| NWS issues VAA | T+0 | T+0 |
| SkyBridge ingests | T+30 s | T+5 min |
| Published to MQTT | T+35 s | T+5 min |
| Reaches regional Pis | T+1 min | T+10 min |
| Reaches mesh nodes | T+2 min | T+15 min |
| Reaches pilot at FL250 over Cook Inlet | T+1 min (cellular) | T+15 min (mesh-only) |
| Pilot sees alert | T+1 min | T+15 min |

Compare to the existing FAA system (Flight Service phone briefing or HIWAS broadcast on VOR): typical 5–30 min from issuance to pilot. SkyBridge is competitive at worst, dramatically faster at best.

## Scenario F — Two-pilot in-air mesh handshake

> *Two pilots flying near each other in the Wrangell Mountains, both with SkyBridge phones + Meshtastic radios. Pilot A has fresh data from a recent cellular touch-down at McCarthy. Pilot B has been in the mountains for 2 hours with no signal.*

When their LoRa radios come within range:

1. Both devices announce HELLO on the SkyBridge channel
2. Each declares schema version + last-data freshness
3. Pilot A's device sees it has fresher region data than Pilot B
4. A sends B a delta bundle: only what's changed since B's stale timestamp
5. B applies the delta, UI shows the new SIGMET that came out 30 minutes ago

A second use case: **Pilot A reports a PIREP** (e.g., "moderate icing FL080 over Mt. Sanford"). The PIREP travels:

```
Pilot A's app → Pilot A's Meshtastic radio
  → broadcast to Pilot B's radio (peer-to-peer)
  → Pilot B's app cache + UI
Both pilots: when next near a regional Pi or cellular: 
  → upload PIREP to MQTT
  → Dell receives + republishes to all subscribers
  → other pilots in the region see it
```

The PIREP enters the network immediately for nearby pilots, propagates outward as carrier nodes regain backhaul.

## Scenario G — Power outage at regional Pi

> *Regional Pi at PALH loses power for 4 hours. What happens?*

### During outage

- Mesh nodes continue communicating peer-to-peer (Meshtastic handles routing without the Pi)
- Observations from PALH and PAMR mesh radios continue to be exchanged among mesh devices
- However, ingestion of FAA/NWS sources stops (no Pi running scripts)
- MWOS Lake Hood data continues but isn't broadcast to mesh
- Dell hub notices PALH-pi-01 is silent (HELLO packets stop) and marks region-Anchorage-Bowl as "primary_pi: offline"
- Dashboard shows "regional Pi: offline" warning
- Statewide aggregation continues to render cached observations; the PALH region is flagged "stale"

### When power restores

- Pi boots, services start (systemd order: kneeboard, authelia, caddy, mosquitto, sb-ingest)
- Regional Pi catches up on FAA/NWS observations missed during outage (replay if archive available)
- Republishes everything to MQTT
- Dell receives the catchup batch
- Dashboards refresh
- Audit log records the outage with start/end timestamps and recovery byte count

### Downstream impact

For 4 hours, Bowl pilots saw stale-flagged data. No SAFETY messages were missed (those came from Dell directly, bypassing the regional Pi). No mesh nodes failed. No data was lost — observations buffered in memory + cached on each Pi-adjacent device.

Architecture grade: **resilient under partial failure.**

## Lessons that drive the bench test

These scenarios are not hypotheticals — they are the test matrix for [`07-bench-test-spec.md`](07-bench-test-spec.md). Every scenario above must be reproducible in simulation before SkyBridge ships v1.0.

The simulator answers questions like:

- "If 50 pilots simultaneously enter the Anchorage Bowl mesh, does it congest?"
- "If the cabin scenario averages one overflight per 6 hours, what is the worst-case data staleness?"
- "If two SAFETY messages arrive at a node during a CRITICAL message transmission, which preempts which?"
- "When a regional Pi reboots, how long until full state recovery?"
- "What is the practical bandwidth ceiling per regional cluster before packet loss exceeds 10%?"

If the bench-test answers are acceptable, the protocol design is acceptable. If not, the design changes.
