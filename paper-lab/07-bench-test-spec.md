# 07 — Bench Test Specification

> The simulator design that proves the protocol works before we deploy it. Discrete-event simulation in Python via simpy. All scenarios from [`06-data-mule-scenarios.md`](06-data-mule-scenarios.md) become deterministic, reproducible tests.

## Goals of the bench test

1. **Reproducibility**: anyone with the SkyBridge repo can run the simulator and get identical results to the paper. Bit-for-bit deterministic given a seed.
2. **Coverage**: every scenario from §06 must run as a test, with measured outcomes.
3. **Pre-deployment validation**: changes to the protocol or QoS settings get tested in simulation before reaching real radios.
4. **Cert evidence**: simulator outputs become referenced data in the FAA accuracy attestation package.
5. **Empirical grounding for [§09 TAIGA addendum](09-taiga-addendum.md)**: bake-off results are simulator-validated.

The bench test is **not**:
- Performance benchmarking of real Pi hardware (that's separate)
- A debugger replacement (production logs are still primary)
- A network-layer simulator replacement for ns-3 / OMNeT (we are simpler)

## Tooling choice

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **simpy** | Pure Python, lightweight, clear API, deterministic | Single-threaded, no built-in radio model | **Chosen** |
| ns-3 | Industry-standard, full PHY models | C++ heavy, weeks to learn, overkill | Pass |
| OMNeT++ | Full network simulator | Same as above | Pass |
| Custom from scratch | No deps, total control | Reinventing wheels | Pass |

**simpy** is right because:
- Discrete-event semantics match LoRa transmission exactly (each packet is an event with a duration)
- Python lets us share data structures with production code (TAIGA encoder, SkyBridge envelope)
- Visualization via matplotlib animation is good-enough for paper figures
- Total dependency footprint: simpy + numpy + matplotlib

## Architecture

```
simulator/
├── core/
│   ├── world.py         # The simulated world: time, regions, nodes, channels
│   ├── node.py          # A single SkyBridge node (Pi, mesh radio, phone, cabin)
│   ├── channel.py       # Radio propagation: range, loss, latency, interference
│   ├── packet.py        # SkyBridge envelope + TAIGA payload
│   └── audit.py         # Simulated audit log
├── scenarios/
│   ├── A_one_hop_direct.py
│   ├── B_three_hop_relay.py
│   ├── C_data_mule_cabin.py
│   ├── D_regional_handoff.py
│   ├── E_congestion_50_nodes.py
│   ├── F_compat_v1_to_v3.py
│   ├── G_pi_outage_recovery.py
│   ├── H_partition_anchorage_dell.py
│   ├── I_bake_off_taiga_vs_l1l2.py
│   └── J_safety_message_propagation.py
├── reports/
│   ├── packet_trace.json
│   ├── per_link_stats.csv
│   ├── timeline.svg
│   └── histograms.svg
├── viz/
│   ├── topology_animation.py    # matplotlib animation of packet flow
│   └── render_svg.py            # final SVG output for paper figures
└── tests/
    ├── test_protocol_round_trip.py
    └── test_compat_skew.py
```

## Core primitives

### `world.py` — the simulation environment

```python
import simpy

class World:
    def __init__(self, seed=42):
        self.env = simpy.Environment()
        self.rng = random.Random(seed)
        self.nodes = []
        self.regions = []
        self.audit_log = []
        self.packet_traces = []
    
    def add_node(self, node): ...
    def add_region(self, region): ...
    def run(self, until_seconds): ...
```

Time is simulated; one `env.timeout(60)` advances 60 simulated seconds in microseconds of real time.

### `node.py` — a SkyBridge participant

```python
class Node:
    """
    A single network participant. Could be:
      - Regional Pi (PALH-pi-01)
      - Mesh radio (cabin-PADL-N01)
      - Phone (pilot-N123AB)
      - Vehicle/aircraft mounted (truck-dalton-7)
    """
    def __init__(self, id, role, location, region):
        self.id = id
        self.role = role  # 'pi', 'mesh', 'phone', 'aircraft'
        self.location = location  # (lat, lon, altitude_ft)
        self.region = region
        self.queue = {prio: [] for prio in PRIORITY_CLASSES}
        self.cache = {}
        self.last_seen_data_ts = 0
        self.send_rate_pps = 1.0
    
    def transmit(self, packet, world):
        # Uses world.channel to deliver to nearby nodes
    
    def receive(self, packet, world):
        # Dedup, validate, store, possibly forward
```

### `channel.py` — radio propagation

The honest model:

```python
class LoRaChannel:
    """SF7, 915 MHz, simplified propagation."""
    
    def transmission_duration_ms(self, packet_bytes):
        # SF7 = 5470 bps practical
        # 237B Meshtastic frame ≈ 350 ms airtime
        return (packet_bytes * 8 / 5470) * 1000
    
    def can_reach(self, sender_pos, receiver_pos):
        # Free-space horizon + practical antenna pattern
        # At ground level: ~5 km
        # At 3000 ft AGL: ~67 km horizon, ~10 km practical SF7
        # At FL360: 220 km
        slant_range_km = ...
        return slant_range_km < self.max_range_for_altitude(...)
    
    def loss_probability(self, slant_range_km, altitude_ft):
        # Empirical model: distance + altitude → loss probability
        # Calibrated from actual Meshtastic field reports
        if slant_range_km < self.range_50pct: return 0.05  # 5% loss
        elif slant_range_km < self.range_99pct: return 0.30
        else: return 1.0  # out of range
    
    def latency_ms(self, slant_range_km):
        return slant_range_km * 1000 / 300_000  # speed of light, ms
```

### `packet.py` — wraps real TAIGA encoder

```python
class SimulatedPacket:
    def __init__(self, envelope, payload):
        self.envelope = envelope
        self.payload_bytes = payload  # actual TAIGA-encoded bytes
        self.byte_size = len(payload) + 10  # +envelope
        self.encode_time = time.perf_counter() - encode_start
    
    @classmethod
    def from_observation(cls, obs, version=1):
        payload = real_taiga_encode(obs, version)
        envelope = build_envelope(...)
        return cls(envelope, payload)
```

Critically: **the simulator uses the PRODUCTION TAIGA encoder**. No mocking. The bytes that flow in simulation are the same bytes that flow on real radios.

## Scenario specs

Each scenario is a self-contained Python file declaring:
- Topology setup (nodes, regions, mobility)
- Stimulus (what messages to inject and when)
- Observable assertions (what should happen)
- Output (what to log/render)

### Scenario A: One-hop direct delivery

```
Setup: 2 nodes 5 km apart, both stationary, both on Anchorage Bowl mesh
Stimulus: Send 100 ROUTINE METARs from A
Expected:
  - Delivery rate > 95%
  - End-to-end latency < 500 ms median
Output: per-packet trace, RTT histogram
```

### Scenario B: Three-hop relay

```
Setup: 4 nodes in a line, 5 km apart each. A → B → C → D
Stimulus: Send 100 ROUTINE METARs from A targeting D
Expected:
  - Delivery rate > 80%
  - Latency: 1.5 s median (3 × 500 ms)
  - Some packets may be lost due to mesh routing inefficiency
Output: hop counts, drop reasons
```

### Scenario C: Data mule (cabin)

```
Setup:
  - Cabin node at lake (lat=60.0, lon=-159.0, fixed)
  - Pilot in plane, trajectory:
      Start: PADL ground (lat=59.04, lon=-158.45, 0 ft)
      Climbs to 3000 ft AGL
      Crosses lake region heading north at 120 kt
      Lands at PAII

Pre-conditions:
  - Pilot has fresh data from PADL (5 min old)
  - Cabin has stale data (4 hours old)

Stimulus:
  - Time advances; pilot's plane moves per kinematic model
  - When pilot's plane within 5 km of cabin and cabin in radio LOS:
    - Pilot device announces HELLO
    - Cabin announces HELLO
    - Pilot sends fresh region data; cabin acks per packet
    - Cabin uploads its local cabin sensor reading (T/P/H)

Expected:
  - Contact window: ~4 minutes
  - Bytes exchanged in window: 1500-3000 bytes (TAIGA-compressed)
  - Cabin's data freshness post-contact: <10 minutes old
  - Pilot's local cache gains the cabin's cabin sensor reading

Output: timeline.svg showing aircraft position vs cabin contact
```

### Scenario D: Regional handoff

```
Setup:
  - Three regions adjacent on a map: Anchorage Bowl, Mat-Su, Interior
  - Each with 5 mesh nodes
  - Pilot starts at PALH (Anchorage Bowl)

Stimulus:
  - Pilot's phone reports GPS every 10s; flying north at 250 kt
  - Phone advances through regions over 90 minutes

Expected:
  - Region transitions at known boundaries (with hysteresis)
  - Subscription changes follow region (MQTT topic re-subscribe)
  - Cellular drops between regions: tier downshifts
  - No data loss; cache merging across handoffs is correct

Output: per-second region affiliation, tier transitions, audit_log entries
```

### Scenario E: Congestion (50 nodes)

```
Setup:
  - 50 mesh nodes in a small region (4x4 km cluster)
  - All within radio range of each other
  - 1 regional Pi anchor

Stimulus:
  - Each node emits a ROUTINE observation every 30 s
  - Total: 50 nodes × 2 packets/min = 100 packets/min
  - Plus regional Pi emits MODEL data every 5 min

Expected (the question we're answering):
  - At what packet rate does delivery_rate fall below 80%?
  - Does AIMD adjust correctly to prevent collapse?
  - Are SAFETY messages still delivered when ROUTINE backs off?

Output: delivery_rate timeseries per priority class
```

### Scenario F: Schema-skew compatibility

```
Setup: 3 nodes:
  - A: emits and decodes v1 only
  - B: emits and decodes v1, v2
  - C: emits and decodes v1, v2, v3

Stimulus:
  - A emits an Observation
  - B emits an Observation with v2-only field
  - C emits an Observation with v2 and v3 fields

Expected:
  - All 3 messages decoded successfully on every other node
  - A's decoder of B/C messages: only v1 fields populated
  - C's decoder of A: v2/v3 fields are null
  - audit_log has zero crash events
  - audit_log has "newer_version_received" entries on A

Output: per-decode field-population histograms
```

### Scenario G: Pi outage + recovery

```
Setup: Anchorage Bowl region with 6 nodes
Stimulus:
  - At T+30 min, regional Pi powers off
  - At T+5 hours, Pi powers back on
  - Other nodes continue mesh traffic throughout

Expected:
  - During outage: mesh traffic continues among nodes
  - Mesh-only delivery: degraded but functional
  - Pi reboots: catches up on missed observations from cache + ingestion replay
  - Within 5 minutes of reboot: full statewide synthesis resumes
  
Output: outage timeline, recovery duration, packet-loss attributable to outage
```

### Scenario H: Partition (Anchorage cluster vs Dell)

```
Setup: All Anchorage Bowl nodes lose internet to Dell
Stimulus: 30 min partition, then restore
Expected:
  - Bowl nodes communicate among themselves (mesh up)
  - Dell sees Bowl as "offline" (HELLO timeout)
  - Anchorage observations queue locally
  - On restore: queued observations flush to Dell
  - No SAFETY messages missed (those came from Dell originally; system fails open)
  
Output: queue_size timeseries, partition_duration, recovery_messages
```

### Scenario I: Compression bake-off

```
Setup: 1000 real METARs from production database
Stimulus: For each, encode 3 ways:
  - Raw text + zstd-9
  - TAIGA + envelope
  - SkyBridge L1+2 + envelope

Expected:
  - Bytes per encoding (mean, median, p99)
  - Encode time per encoding
  - Decode time per encoding
  - Round-trip fidelity (bit-exact for valid encodings)

Output: bake-off histogram + per-encoding stats table for [§09 addendum](09-taiga-addendum.md)
```

### Scenario J: SAFETY message propagation

```
Setup:
  - 4 regions, full mesh + backhaul
  - 100 nodes total
  - 1 SAFETY message issued at T+60 sec (Volcanic Ash SIGMET)

Stimulus: SAFETY emitted from PALH-pi-01

Expected:
  - All 100 nodes receive within 60 seconds (cellular path)
  - All mesh-only nodes receive within 10 minutes
  - Acknowledgments come back to original sender
  - SAFETY message is retained in MQTT (later subscribers receive it)
  - audit_log records every receive

Output: cumulative-distribution function of receive times
```

## Packet trace format (per scenario)

Every scenario emits a `packet_trace.json`:

```json
[
  {
    "ts": 0.000,
    "event": "emit",
    "msg_id": "abc123",
    "from": "PALH-pi-01",
    "to": "broadcast",
    "priority": "ROUTINE",
    "payload_size": 30,
    "envelope_size": 10,
    "scenario": "A"
  },
  {
    "ts": 0.085,
    "event": "receive",
    "msg_id": "abc123",
    "from": "PALH-pi-01",
    "at": "PAMR-pi-01",
    "via": "meshtastic_lora",
    "rssi_dbm": -85,
    "delivery": "success"
  },
  {
    "ts": 0.123,
    "event": "drop",
    "msg_id": "abc123",
    "at": "PAED-pi-01",
    "reason": "ttl_expired",
    "queue_age_ms": 1800100
  },
  ...
]
```

This is the source-of-truth output. Visualizations + statistics are derivable from it.

## Visualization

For the paper, three SVGs:
- **`topology_animated.svg`** — static frame from animation showing scenario C with packet flow visible
- **`packet_walk.svg`** — single packet's journey through the layer cake
- **`bake_off.svg`** — bar chart from scenario I

Live animation (matplotlib) for development. Static SVGs for the paper.

## Performance budget

The simulator should run on a developer laptop / CI runner without GPU:

```
Target: scenario A (1 hour simulated, 100 packets) runs in <10 seconds wall clock
Target: scenario E (1 hour simulated, ~6000 packets) runs in <60 seconds wall clock
Target: scenario I (1000 METARs encoded 3 ways) runs in <30 seconds wall clock
```

If we exceed these budgets, profile and optimize. simpy itself is fast; expensive parts are likely in the channel propagation calculations or packet-trace serialization.

## Reproducibility

```
Scenario invocations all take a --seed:
  python -m simulator scenario A --seed 42
  python -m simulator scenario A --seed 42  # → identical output bytes

CI runs every scenario with seeds [42, 100, 1234] and compares outputs to recorded baselines.
Any regression that changes outcomes triggers a CI failure.
```

This makes the bench test useful as a regression suite, not just a "we wrote it once" artifact.

## What lives in tests/ vs scenarios/

- **scenarios/** — full simulations meant to validate complex protocol behaviors. Run on demand or in nightly CI.
- **tests/** — small unit + integration tests of individual protocol pieces (encode/decode, envelope, dedup). Run on every commit.

Test pyramid: lots of small tests, a handful of scenario-tests.

## Open simulator questions

- **Should we model adversarial behavior?** (e.g., a malicious node injecting fake METARs.) Useful for security paper sections; out of scope for v0.1.
- **Should we model real LoRa PHY (Tx power, antenna pattern)?** Currently using a simplified range-based model. ns-3 has detailed models; we don't. Trade-off: realism vs complexity. Default: simplified.
- **Should we expose the simulator as a web app for demos?** (Click "Scenario C" → see animation.) Useful as a NASAO conference demo. Low priority.
- **Should scenarios include real weather replays?** (Replay actual data from the historical SQLite over a simulated network.) Compelling for cert: "here's how this storm system would have propagated." Higher complexity. Defer.

These remain open as of v0.1.
