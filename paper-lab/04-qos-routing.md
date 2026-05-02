# 04 — Quality of Service & Routing

> Five priority classes, AIMD-driven congestion control per link, CoDel-style queue aging, and how they interact across mesh + backhaul.

## The five priority classes

Every SkyBridge message belongs to exactly one class. Class is set by the sender, encoded in the envelope's `prio` byte, and respected end-to-end.

| Class | Code | Examples | Behavior |
|---|---|---|---|
| **SAFETY** | 0 | SIGMETs (volcanic ash, severe icing, severe turbulence), TFRs, severe wind shear, urgent PIREPs ("UA /SK BKN040-OVCXXX TURB EXTRM") | Reliable transmission with retry until ack, max-hops broadcast, persists 24 hr TTL, never dropped |
| **CRITICAL** | 1 | METAR SPECIs (off-cycle reports), ceiling/visibility falling below VFR thresholds | At-least-once delivery, retry up to 3×, 1 hr TTL, broadcast to region |
| **ROUTINE** | 2 | Regular METARs, MWOS observations, hourly NWS updates | Best-effort broadcast, no ack, 30 min TTL, dropped if congested |
| **MODEL** | 3 | Forecast snapshots (GFS, GEM, ECMWF, JMA), gridded model fields, time-lapse frames | Fire-and-forget, 1 hr TTL, lowest priority that's still actively delivered |
| **BULK** | 4 | Historical replay, time-series exports, debug data | Only forwarded when bandwidth idle, 24 hr TTL, may be dropped indefinitely |

### Why exactly 5

- **SAFETY vs CRITICAL** distinguishes "must ack receipt" from "best-effort but guaranteed delivery." A SIGMET in flight is life-threatening; a SPECI METAR is operationally critical but not fatal if missed.
- **CRITICAL vs ROUTINE** distinguishes "I will retry" from "I will not retry." Retries cost bandwidth; we limit them to high-value messages.
- **ROUTINE vs MODEL** distinguishes "ground-truth observation" from "model prediction." Observations are scarce and authoritative; models are abundant and probabilistic. Different cache sizes, different freshness expectations.
- **BULK vs anything** is the carve-out for non-urgent data. Bandwidth budget for BULK is whatever's left.

## Behavior per class

### SAFETY

- **Delivery semantics**: exactly-once via MQTT QoS 2; reliable transmission with ack on Meshtastic
- **TTL**: 86400 s (24 hr)
- **Max hops**: 7 (Meshtastic max)
- **Inter-send minimum**: 0 s (send as fast as possible)
- **Retry policy**: up to ack, with exponential backoff (1s, 2s, 4s, 8s, ...) capped at 60s
- **Storage**: persisted in the ROUTING table until ack or expiration
- **Forwarding**: cross-region SAFETY messages override geofencing — pushed statewide
- **Drop policy**: never voluntarily dropped

### CRITICAL

- **Delivery semantics**: at-least-once (MQTT QoS 1); broadcast on Meshtastic with up to 3 retries
- **TTL**: 3600 s (1 hr)
- **Max hops**: 5
- **Inter-send minimum**: 1 s (light throttle to avoid bursts)
- **Retry policy**: 3 retries with backoff
- **Storage**: persisted until ack or expiration
- **Forwarding**: broadcast to message's region; cross-region only if explicitly multi-region tagged
- **Drop policy**: dropped only if buffer overflow + after retries exhausted

### ROUTINE

- **Delivery semantics**: fire-and-forget (MQTT QoS 0); broadcast on Meshtastic
- **TTL**: 1800 s (30 min)
- **Max hops**: 4
- **Inter-send minimum**: 5 s
- **Retry policy**: none — receiver re-requests if needed
- **Storage**: not persisted on sender after transmission
- **Forwarding**: regional only; cross-region via MQTT bridge
- **Drop policy**: dropped on buffer overflow without alarm

### MODEL

- **Delivery semantics**: fire-and-forget
- **TTL**: 3600 s (1 hr)
- **Max hops**: 3 (don't waste mesh capacity)
- **Inter-send minimum**: 30 s
- **Retry policy**: none
- **Forwarding**: prefers MQTT backhaul over mesh
- **Drop policy**: dropped first when congested

### BULK

- **Delivery semantics**: fire-and-forget, only sent when bandwidth idle
- **TTL**: 86400 s (24 hr)
- **Max hops**: 2
- **Inter-send minimum**: 60 s
- **Retry policy**: none
- **Forwarding**: never via Meshtastic (too expensive); MQTT-only
- **Drop policy**: dropped before any other class

## AIMD per-link congestion control

Each peer-to-peer link tracks its own delivery success rate and adjusts the send rate. The algorithm is **Additive Increase, Multiplicative Decrease** — the same family as TCP's CUBIC.

```
Each link maintains:
  send_rate_pps      packets per second this link is allowed
  delivery_rate      success rate over last 100 attempts (rolling window)
  rtt_estimate       round-trip time (Meshtastic ack timing)
  last_loss_event    when loss was last detected

Per-tick (1s):
  if delivery_rate > 0.9:
    send_rate_pps += 1                # additive increase
  elif delivery_rate < 0.7:
    send_rate_pps *= 0.5               # multiplicative decrease
    last_loss_event = now
  
  send_rate_pps = clamp(send_rate_pps, 0.1, 50)  # never less than 1 every 10s, never more than 50/s

Per-packet send:
  if pps_used_this_second >= send_rate_pps:
    queue the packet, do not transmit yet
  else:
    transmit
```

This automatically converges: when many senders share a link, all back off proportionally; when bandwidth opens up, all ramp up gradually. No hand-tuning required per deployment.

**Per-class send_rate budgeting**: each priority class has its own budget. SAFETY can borrow from CRITICAL/ROUTINE/MODEL/BULK budgets when needed. Lower classes cannot borrow up.

```
allocate_send_capacity(link, total_pps):
    safety_budget   = total_pps × 1.0   # whatever it needs
    critical_budget = max(total_pps × 0.4, total_pps - safety_actual)
    routine_budget  = max(total_pps × 0.3, ...)
    model_budget    = max(total_pps × 0.2, ...)
    bulk_budget     = whatever's left
```

In practice for SkyBridge: SAFETY rarely uses >1% of bandwidth; the budgets above are conservative.

## CoDel-style queue management

When the egress queue fills up (link can't drain as fast as inputs arrive), CoDel decides what to drop:

```
Each priority class has its own egress queue.

Per packet enqueued:
  packet.enqueue_ts = now
  push to class queue

Per drain tick:
  for class in [SAFETY, CRITICAL, ROUTINE, MODEL, BULK]:
    while link has bandwidth and class queue not empty:
      packet = peek(class queue)
      age = now - packet.enqueue_ts
      
      if age > class.ttl:
        drop_with_log(packet, reason="ttl_expired")
        continue
      
      if class queue depth > class.max_depth:
        drop_oldest(class queue, reason="codel_overflow")
        # CoDel principle: drop the oldest (most stale), not the newest
      
      transmit(packet)
```

Two key features:
1. **Drop oldest, not newest** — gives newer packets fairer chance to deliver. A 5-min-old observation is worth less than the same data 1 second old.
2. **Per-class TTL enforcement** — a CRITICAL message that sits in queue past its 1-hour TTL gets dropped, not retried. The receiver can request a fresh one if still relevant.

Drop events emit audit log entries:

```
ts:        1777670400
event:     packet_dropped
reason:    codel_overflow
class:     ROUTINE
msg_id:    0x4a8b9c
sender:    PALH-pi-01
recipient: regional_broadcast
queue_depth: 64
queue_max:   50
```

Log informs operator tuning: if BULK drops exceed 10% routinely, increase BULK queue depth. If ROUTINE drops exceed 5%, demote some sources from ROUTINE to MODEL class.

## Subscription-based filtering

A receiving node tells the broker (MQTT) or its mesh neighbors (Meshtastic) what topics/regions it cares about. The sender side filters based on this.

### MQTT subscription

```
# Phone in Anchorage subscribes to:
sb/wx/obs/+/+         # all observations from anchorage_bowl region
sb/safety/+           # all safety alerts statewide
sb/wx/research/+      # multi-source research feed (raw side-by-side aggregation)
```

Wildcard `+` matches one segment. The MQTT broker filters server-side; phone only receives matching messages. Saves cellular bandwidth massively.

### Mesh subscription (proposed)

Mesh has no central broker, so subscription is implicit:

- Each mesh node periodically broadcasts a "interest list" packet — what regions + classes it cares about
- Senders cache this; if no listening node on the mesh expressed interest in a region, the sender doesn't broadcast
- Reduces flood traffic

This is a SkyBridge addition above stock Meshtastic. It costs ~1 small interest-list packet per node per minute, in exchange for dramatic flood reduction in mesh-dense regions.

## Backpressure (when receiver can't keep up)

If a receiver is overwhelmed (CPU pegged, queue full), it can ask the sender to slow:

```
BACKPRESSURE message:
  envelope: ver=1, prio=CRITICAL, msg_id=...
  payload: [target_sender:6B][throttle_factor:1B][duration_s:2B]
```

Sender on receipt:

```
if backpressure for me:
    my_send_rate_pps *= throttle_factor / 256
    note expiration after duration_s
    return to normal after expiration
```

Used sparingly; aggressive backpressure starves senders. In practice: regional Pi might emit backpressure to Dell when its own SQLite is slow; Dell respects, slows the requested anchor's update rate temporarily.

## Round-trip example: SAFETY message lifetime

NWS issues a Volcanic Ash SIGMET. Trace through the system:

```
T+0:00 NWS publishes VAA
T+0:30 SkyBridge ingest at PALH-pi-01 fetches; encodes as TAIGA + envelope SAFETY
T+0:31 Published to MQTT topic sb/safety/sigmet/<id> with QoS 2 (retained)
T+0:32 MQTT broker stores; bridges to Dell broker
T+0:33 Dell broker re-publishes to all subscribers
T+0:34 Pilot in Anchorage Bowl: phone subscribed; receives via cellular
T+0:34 Pilot's phone shows alert; map renders polygon
       
       Concurrently on Meshtastic:
T+0:33 PALH-pi-01 broadcasts SIGMET on Anchorage Bowl mesh, hop limit 4
T+0:34 PAMR-pi-01 receives, re-broadcasts (already different msg_id at hop 2)
T+0:34 PAED-pi-01 receives via PAMR
T+0:35 PAED-pi-01 acks back to PALH (CONFIRM packet)
T+0:35 PALH-pi-01 sees ack from all 3 mesh peers; stops retrying
       
       For pilots NOT in Anchorage Bowl, on cellular:
T+0:33 MQTT delivery via Dell bridge
       
       For pilots in mountains with no signal:
T+? On next mesh contact (regional Pi or other pilot)
```

End-to-end latency for SAFETY: **<1 minute** for cellular-equipped users, **<10 minutes** for mesh-only mountain pilots, **24 hours max** before TTL expiry.

## Auto-tuning the QoS settings (the meta layer)

The tunable parameters above (TTL per class, retry counts, queue depths, AIMD aggression) are stored in `config.yml` and exposed via the admin API. Auto-tuning is the goal, but starts manual.

Once we have 30+ days of operation:

```
Track per-link metrics:
  delivery_rate by priority class
  drop rate by reason (ttl_expired, codel_overflow, retries_exhausted)
  average queue depth
  RTT distribution
  
Auto-adjust:
  If SAFETY drops > 0:
      → critical alarm, halt auto-tuning, page operator
  If CRITICAL drops > 5% over 24 hr:
      → +1 retry budget, audit-log the change
  If ROUTINE drops > 20%:
      → demote half of ROUTINE to MODEL
  If MODEL drops > 50%:
      → reduce model fetch frequency
  If BULK drops to zero indefinitely:
      → expand BULK queue depth (we have headroom)
```

The audit log records every adjustment. The cert path uses these logs as evidence of network-self-management.

## Per-link metrics tracked

Each peer-to-peer link (Pi ↔ Dell, Pi ↔ mesh node, mesh node ↔ phone) maintains:

```
Link {
  remote_node_id
  link_type           // mqtt | mesh | direct
  link_state          // up | degraded | down
  
  // Counters (rolling 100-packet window)
  packets_sent
  packets_acked
  packets_dropped
  packets_retried
  
  // Computed
  delivery_rate       // acked / sent
  rtt_p50, rtt_p99    // round-trip times
  bandwidth_bps       // bits per second observed
  congestion_state    // healthy | yellow | red
  
  // AIMD state
  send_rate_pps
  last_loss_event
}
```

Exposed via `/api/network/links` admin API. Useful for the network-uptime dashboard ([`08-cert-path.md`](08-cert-path.md)).

## What this section does not yet specify

- **PIREPs and their priority assignment**: a PIREP is observation data (ROUTINE) but can include critical content ("severe icing"). Should we promote based on parsing, or leave at ROUTINE? **Open**.
- **Emergency squawks (7700, 7600, 7500)**: ADS-B detects these locally; should they emit a SAFETY message to the mesh? **Open** — this is more aircraft-tracking than weather, and may belong in a different message type entirely.
- **Class up/down at admin discretion**: should an FAA dispatcher be able to escalate a CRITICAL message to SAFETY? **Open**, related to authorization model.

These are open as of v0.1.
