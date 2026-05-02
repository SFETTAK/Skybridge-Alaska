# 02 — Protocol Stack

> The layer cake from raw weather observation up to the rendered pixel on a pilot's tablet, and back down to a LoRa packet at -130 dBm. Each layer borrows from a battle-tested protocol; nothing here is invented from scratch.

## The stack

<!-- TODO: render `protocol-stack.svg` — vertical layer cake with each box labeled, arrows showing data flow direction -->

```
[FIGURE: protocol-stack.svg]
┌─────────────────────────────────────────────────────────────────┐
│  L7  Application semantics                                      │
│      • Observation (METAR, MWOS reading, PIREP)                 │
│      • Forecast (NWS Gridpoint, GFS, GEM, ECMWF, JMA)            │
│      • Alert (SIGMET, TFR, severe weather)                      │
│      • Capability announcement (HELLO)                          │
│      • Audit event                                              │
├─────────────────────────────────────────────────────────────────┤
│  L6  TAIGA wire encoding (NASA TM-2015-218427)                  │
│      • ASN.1 schema, geohash locations, 10-min ticks            │
│      • PIREP / METAR / NOTAM / Polygon payload types            │
├─────────────────────────────────────────────────────────────────┤
│  L5  SkyBridge envelope (this project)                          │
│      • [ver:1B][priority:1B][msg_id:4B][ttl_s:2B][payload:N B]  │
│      • Forward-compatible by design                             │
│      • Audit-logged on every emit/receive                       │
├─────────────────────────────────────────────────────────────────┤
│  L4a  MQTT (when on backhaul / internet / VPN overlay)           │
│       • Topic hierarchy: sb/wx/obs/{anchor}/{source}             │
│       • QoS 0/1/2 maps to ROUTINE/CRITICAL/SAFETY                │
│       • Retained messages, last-will-testament, bridges          │
│  L4b  Meshtastic LoRa (when on regional mesh)                    │
│       • Custom PortNum 320 reserved for SkyBridge                │
│       • App-layer fragmentation for >237B payloads               │
│       • Primary-channel AES-256 (per-region key)                 │
│       • AODV routing, max 7 hops, configured 3-4 typical         │
├─────────────────────────────────────────────────────────────────┤
│  L3  Geofence-aware forwarding                                  │
│      • Region polygon resolution from lat/lon                    │
│      • Inbound: subscribe to region topics                       │
│      • Outbound: snapshot to local cache                         │
│      • Cross-region: backhaul-only, never mesh                   │
├─────────────────────────────────────────────────────────────────┤
│  L2  Forward-compatibility rules                                │
│      • Wire format never breaks                                 │
│      • Decoders are forward-tolerant                            │
│      • Encoders never remove fields                             │
│      • Capability negotiation via HELLO packets                 │
├─────────────────────────────────────────────────────────────────┤
│  L1  Hardware                                                   │
│      • LoRa radio (915 MHz US ISM band, FCC Part 15)             │
│      • Commodity microcontroller / SBC for sensor nodes          │
│      • Environmental sensors (TBD per deployment)                │
│      • Pi 5 (current ground station) / Dell server (production)  │
└─────────────────────────────────────────────────────────────────┘
```

## L7 — Application semantics

The data the user actually cares about. SkyBridge's L7 vocabulary:

| Type | What it represents | Source |
|---|---|---|
| `Observation` | A real measurement at a specific lat/lon at a specific time | METAR, MWOS, PIREP, citizen sensor |
| `Forecast` | A model prediction for a lat/lon + time | NWS Gridpoint, GFS, GEM, ECMWF, JMA |
| `Alert` | An aviation safety message valid for a region + time window | SIGMET, AIRMET, TFR, NOTAM, PIREP |
| `Hello` | Capability + region announcement from a node | Periodic, every node |
| `Audit` | An event for the audit-log (config change, weight rebalance, etc.) | Internal |

L7 is intentionally minimal. Anything more specific (e.g., "severe icing AIRMET valid 1200-1800Z over Cook Inlet") is a structured field within an `Alert`, not a separate type. Keeps the schema tractable.

## L6 — TAIGA wire encoding

The NASA-published Traffic and Atmospheric Information for General Aviation protocol. Reference: `protocol/TAIGA_PROTOCOL.md` in this repo.

What TAIGA gives us out-of-the-box:

- **Geohash location**: 4–7 character strings at variable precision (20 km → 76 m)
- **10-minute tick timestamps**: 7 bits gets 24 hours of resolution
- **80% compression vs raw text**: 18 bytes for a typical PIREP that's 96 bytes plain
- **ASN.1 framing**: forward-compatible by design (extension markers)
- **Aviation-domain payloads**: PIREP / METAR / NOTAM / Weather Polygon / Emergency / System

What TAIGA does **not** cover (the gap our addendum fills, see [`09-taiga-addendum.md`](09-taiga-addendum.md)):

- Volatility / rate-of-change flags
- Per-source confidence / calibration tags
- Mesh-routing region tags

For compression: **TAIGA's quantization is one option in a bake-off** (see [`03-data-encoding.md`](03-data-encoding.md)). Where TAIGA wins for a payload type, we use it. Where our hand-packed Layer 1+2 wins, we use that and propose adding it to TAIGA as an addendum. Empirical measurement decides.

## L5 — SkyBridge envelope

Around every TAIGA payload, we wrap a small SkyBridge envelope:

```
0       1       2       3       4       5       6       7       8 ...
+-------+-------+-------+-------+-------+-------+-------+-------+
|  ver  | prio  |          msg_id (4 B unique per sender)        |
+-------+-------+-------+-------+-------+-------+-------+-------+
|     ttl_s     |  payload_len  |        payload (N bytes)        ...
+-------+-------+-------+-------+-------------------------------+
                                |  TAIGA-encoded record / typed payload
                                |  little-endian, additive per ver
                                +-------------------------------+
```

| Field | Bytes | Purpose |
|---|---|---|
| `ver` | 1 | SkyBridge envelope version (currently `0x01`) — distinct from TAIGA's internal versioning |
| `prio` | 1 | Priority class (0=SAFETY, 1=CRITICAL, 2=ROUTINE, 3=MODEL, 4=BULK) |
| `msg_id` | 4 | Sender-assigned unique ID for dedup; little-endian uint32 |
| `ttl_s` | 2 | Seconds until message becomes ineligible to forward; 0 = never expire |
| `payload_len` | 2 | Length in bytes of the TAIGA-encoded payload |
| `payload` | N | The TAIGA bytes (or other typed payload for extension types) |

Total envelope overhead: **10 bytes**. With a typical METAR's TAIGA payload of ~30 bytes, full envelope = 40 bytes.

Why an envelope at all (vs putting these fields in TAIGA):
- TAIGA's schema is NASA-published; we don't fork it. Envelope is ours to evolve.
- Mesh routing (priority, TTL) needs to be inspected without decoding TAIGA — saves CPU on relay nodes.
- The envelope version byte lets us evolve transport semantics independently of payload semantics.

## L4 — Transport: MQTT or Meshtastic, depending on link

### L4a — MQTT (preferred when bandwidth permits)

When a node has wifi, cellular, VPN-overlay, or edge-tunnel access, it speaks MQTT to a Mosquitto broker (Pi-local or Dell-central).

**Topic hierarchy:**

```
sb/wx/obs/{anchor_id}/{source}      e.g. sb/wx/obs/PALH/metar
sb/wx/obs/{anchor_id}/mwos          e.g. sb/wx/obs/MWOS:133/mwos
sb/wx/grid/{region}/{model}         e.g. sb/wx/grid/anchorage_bowl/om_ecmwf
sb/wx/cert/{anchor_id}              e.g. sb/wx/cert/PALH

sb/safety/sigmet/{id}               retained, expires per VAA validity
sb/safety/tfr/{id}                  retained, expires per TFR end
sb/safety/wind_shear/{id}           non-retained, ephemeral

sb/node/hello/{node_id}             retained, last-will = "offline" message
sb/node/health/{node_id}            non-retained, periodic

sb/admin/config/{key}               admin-only ACL, audit-log feed
sb/audit/{event_class}              admin-only ACL, audit-log feed
```

**QoS mapping:**

| MQTT QoS | SkyBridge class | Behavior |
|---|---|---|
| 2 (exactly-once) | SAFETY | Persistent until acknowledged; broker stores until delivered |
| 1 (at-least-once) | CRITICAL | At-least-once; possible duplicate, idempotency required |
| 0 (fire-and-forget) | ROUTINE, MODEL, BULK | No ack; may be lost in transit; resender's responsibility |

**Bridges**: regional Pi → Dell central via Mosquitto-to-Mosquitto bridge. When Pi loses internet, it queues locally; on reconnection, queue drains. Works in both directions.

**Retained messages** are critical for newcomers: a phone joining the network gets the most recent value of every retained topic (active SIGMETs, latest METARs) without polling.

### L4b — Meshtastic LoRa (when on regional mesh)

When a node is on the regional mesh, it transmits over Meshtastic.

**Custom PortNum**: `320` reserved for SkyBridge. Other Meshtastic apps (default text chat, telemetry) use different PortNums and don't conflict.

**Frame structure**: stock Meshtastic packet with:
- `from`, `to` (or broadcast), `id`, `hop_limit` (we set 3-4)
- `decoded.portnum = 320`
- `decoded.payload` = SkyBridge envelope bytes

**Fragmentation**: Meshtastic max payload is ~237 B. SkyBridge envelopes >227 B (10 B header) are fragmented at the app layer:
- First fragment: full envelope header + payload prefix + frag header `[total_frags:1B][frag_idx:1B]`
- Subsequent: just frag header + payload chunk
- Receiver reassembles by `msg_id + frag_idx`

In practice, most TAIGA-encoded payloads (single METAR, single observation) fit in one Meshtastic packet. Fragmentation is rare and reserved for batched messages.

**Encryption**: Meshtastic's primary-channel AES-256 is on. Per-region keys are pre-distributed (in a Pi setup script or QR code at the regional Pi). All traffic on the regional mesh is encrypted to mesh members.

**Routing**: Meshtastic's default flood-based routing (AODV-like) handles local discovery. SkyBridge does not modify Meshtastic firmware; we live entirely above it.

### What the mesh does NOT carry

This is where casual readers tend to imagine more than the physics allows. To be explicit:

| Asset type | Mesh? | Why / where it goes instead |
|---|---|---|
| METAR strings, observation deltas, alert text | **Yes** | Compact text/numeric, ~50–500 B per item |
| SIGMET / AIRMET polygon vertices (decimated) | **Yes** | Encoded as a short list of (lat,lon) pairs |
| PIREPs (compact form) | **Yes** | Text + position, ~200 B |
| Camera images (MWOS site cameras) | **No** | 5–80 KB each; would take 13 seconds to 4 minutes per image at 1–3 kbps practical Meshtastic throughput. Internet-delivered to the kneeboard. |
| NEXRAD radar tiles | **No** | Hundreds of KB to MB. Internet-delivered. |
| Chart tiles (VFR Sectional, IFR Low/High) | **No** | Pre-cached on tablet from FAA / ArcGIS over internet. |
| Audio (VHF recordings, ATIS) | **No** | Multi-MB. Internet-delivered. |
| Video, time-lapse playback | **No** | Bandwidth-prohibitive on LoRa. |

LoRa is fundamentally a **text-and-numeric** medium for this project. When a pilot is on cellular/WiFi, they see imagery, audio, and chart layers via internet on the kneeboard. When a pilot drops to MESH tier, they see the *numerical distillation* of the same situational picture: the wind from the camera site, not the camera image; the AIRMET polygon and severity, not the vector chart; the PIREP text, not the audio recording.

This is not a limitation we're hiding; it is the architectural choice that makes the mesh tier actually work. A network that promised images would saturate within seconds and deliver nothing.

## L3 — Geofence-aware forwarding

The decision layer: which messages go to mesh, which go to MQTT, which stay regional, which traverse backhaul to other regions.

**Logic per outgoing message:**

```
def route(msg):
    region = resolve_region(msg.payload.lat, msg.payload.lon)
    
    # Always emit to local mesh and MQTT for the message's own region
    if region == self.current_region:
        emit_to_meshtastic(msg)
        emit_to_mqtt(f"sb/wx/.../{region}/...", msg)
    
    # Cross-region: only via backhaul (MQTT bridge to Dell, never mesh)
    else:
        emit_to_mqtt(...)  # backhaul handles inter-region
        # Do NOT mesh-broadcast a Cold Bay obs from Anchorage Pi
    
    # SAFETY messages override geofencing — broadcast statewide
    if msg.priority == SAFETY:
        emit_to_all_regions(msg)
```

**Logic per incoming message:**

```
def receive(msg, source):
    if seen_msg_id(msg.msg_id, msg.sender):
        return  # dedup
    
    audit_log("rx", msg, source)
    store(msg)
    
    if msg.priority <= CRITICAL and source == "mesh":
        # Forward to MQTT for backhaul to Dell
        emit_to_mqtt(...)
    
    if msg.priority == SAFETY:
        # Re-broadcast on local mesh to ensure delivery
        emit_to_meshtastic(msg)
```

The dedup table is keyed on `(sender_id, msg_id)`. Entries expire after `ttl_s + buffer`.

## L2 — Forward-compatibility rules

Articulated fully in [`05-versioning-compat.md`](05-versioning-compat.md). At a glance:

- The wire format never changes incompatibly. Only additive evolution.
- Every packet carries a version byte (`ver` in the envelope).
- Decoders are forward-tolerant: unknown extension bytes are ignored, never reject.
- Encoders dual-emit during deprecation windows (dual-encoded for ≥6 months when retiring an old version).
- Capability negotiation: HELLO packets advertise schema versions supported.

## L1 — Hardware

The physical infrastructure SkyBridge runs on. Each layer in the stack maps to specific hardware.

| Component | Function |
|---|---|
| **Raspberry Pi 5** | Regional ground station; runs the full SkyBridge stack |
| **Dell PowerEdge** | Central aggregator for statewide synthesis |
| **RTL-SDR dongles** | VHF airband + ADS-B 1090 MHz + UAT 978 MHz reception |
| **LoRa radio + Meshtastic firmware** | Mesh networking; PortNum 320 reserved for SkyBridge |

A SkyBridge ground station, as currently deployed at DOT-VHF, is approximately a Pi 5 plus three SDR dongles plus a Meshtastic radio. Specific weather-sensor designs for distributed remote nodes are an open design question and are intentionally not committed in this paper-lab. SkyBridge is prototyping; the BOM for any future remote sensor node is TBD per deployment, partner relationship, and operating environment.

For comparison: an FAA AWOS site is in the $200,000+ range installed, plus annual maintenance contracts.

## What flows where: the round trip

A real PALH METAR's journey:

1. **L1**: aviationweather.gov publishes the METAR at 11:53 Z
2. **L7**: SkyBridge ingest job at PALH-pi-01 fetches it, parses, normalizes
3. **L7**: Stored in local SQLite (`wx_obs` table) as `{station: PALH, ts: 1777..., wind: {...}, ...}`
4. **L6**: TAIGA encoder produces a 30-byte ASN.1 PIREP/METAR record
5. **L5**: Wrapped in SkyBridge envelope: `[01][02][a3 b1 c2 d4][2c 01][1e 00][...30 bytes TAIGA...]`
6. **L4a**: Published to MQTT `sb/wx/obs/PALH/metar` with QoS 0 (ROUTINE)
7. **L4b**: Same envelope re-emitted on Meshtastic PortNum 320 (Anchorage Bowl mesh)
8. **L3**: Subscribers in Anchorage Bowl region receive via mesh; subscribers elsewhere receive via Dell-bridged MQTT
9. **L4** (peer): Pilot's phone, on cellular, hears it via MQTT subscription
10. **L5**: Envelope verified (version, dedup, TTL OK)
11. **L6**: TAIGA decoded back to record
12. **L7**: Record dropped into UI feed, dashboard re-renders

End-to-end latency from publish to render: typically **<10 seconds** with cellular, **<2 minutes** in mesh-only mode.

## Why we don't fork Meshtastic

It would be tempting to write a SkyBridge-specific firmware that natively understands TAIGA + envelope. We don't, because:

- **Maintenance burden**: forking means tracking upstream Meshtastic changes forever. Their dev pace is fast.
- **Hardware compatibility**: every Meshtastic-supported board "just works" with SkyBridge. No SkyBridge-specific board.
- **Community fit**: SkyBridge devices are interoperable with the existing Meshtastic ecosystem. A pilot using SkyBridge can also use stock Meshtastic chat or telemetry on the same radio.
- **SkyBridge logic belongs in the app layer anyway**: priorities, TAIGA decoding, region resolution — none of that needs to live in the radio.

We use Meshtastic the way TCP/IP applications use the kernel network stack: hands-off, unmodified, layered above.

## Open architectural questions

- Should SkyBridge eventually publish a `meshtastic-skybridge` PR proposing PortNum 320 as a reserved-and-documented allocation? (Helps avoid future PortNum collisions.)
- Should we embed a Mosquitto bridge inside the Pi setup script vs documenting it as a separate install step?
- Should we evaluate NanoMQ (lightweight broker, sub-1 MB) for low-power regional nodes vs Mosquitto?
- For the data-mule case: should the airborne node act as a mini-Mosquitto broker, or just relay messages stateless? (Statelessness is simpler; brokering allows retained messages but adds complexity.)

These are open as of v0.1.
