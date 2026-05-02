# 05 — Versioning & Compatibility

> The wire format never breaks. Every node, regardless of how stale, can always understand a subset of every newer message. Translation is the receiver's job, never the sender's.

## The compatibility law

This is the most-important rule in the SkyBridge protocol. It is non-negotiable:

> **Wire format never breaks. Schema versions only grow.**

An implementation of SkyBridge v1, deployed in 2026, must still successfully decode SkyBridge messages emitted in 2046, 2056, and beyond. The reverse must also hold: a 2056-era implementation must still decode 2026 messages.

If we can't make this guarantee, deploying SkyBridge to bush-pilot phones is irresponsible. A pilot in 2030 with an unupdated app should not lose access to weather just because the protocol has evolved.

## Three principles

### Principle 1 — Every packet self-describes its schema version

The first byte of every SkyBridge envelope is `ver`. It identifies the schema version of the payload that follows. Receivers can:
- Know they're looking at a message they recognize → decode normally
- Know they're looking at a newer message → best-effort decode of the prefix they understand
- Know they're looking at an older message → decode normally, just with fewer fields

```
Receiver logic per packet:
  if ver in supported_versions:
      decode_per_version(payload, ver)
  elif ver > max_supported:
      best_effort_decode(payload, max_supported)  # ignore future-only fields
      log "future_version_received"
  elif ver < min_supported:
      log "obsolete_version_received"
      drop  # or attempt v_min decode, depending on policy
```

### Principle 2 — Decoders are forward-tolerant

A decoder that encounters a field it does not recognize ignores it. Never crashes. Never rejects the message. Logs the unknown field for telemetry but keeps going.

This is the same discipline that makes Protocol Buffers work for 20+ years across thousands of protocols. We follow it.

```
ASN.1 has explicit forward-compat support via:
  
  Sequence ::= SEQUENCE {
    field1 INTEGER,
    field2 BOOLEAN,
    ...,                       -- extension marker; new fields added below this line
    new-field-v2 [0] IMPLICIT OCTET STRING OPTIONAL,
    new-field-v3 [1] IMPLICIT BOOLEAN OPTIONAL
  }

A v1 decoder reads field1, field2, sees the extension marker, and stops.
A v3 decoder reads everything. Both succeed without errors.
```

### Principle 3 — Encoders never break old fields

When evolving the schema:
- Adding new optional fields at the end of a payload: **OK**
- Adding new payload types: **OK**
- Removing a field: **NOT OK** (breaks readers)
- Reordering fields: **NOT OK**
- Changing a field's type or unit: **NOT OK**
- Reusing a deprecated field for a different purpose: **NOT OK**

If a field truly must be deprecated:
1. Mark it `DEPRECATED` in the schema documentation
2. Stop writing to it in new emitters
3. Continue accepting it from old senders for ≥2 years
4. After 2 years, audit: are any nodes still emitting it? If no: free the field number forever (still don't reuse)

## Capability negotiation — HELLO packets

Every node periodically broadcasts its capabilities. Other nodes use this to know what to encode:

```
HelloPayload ::= SEQUENCE {
    node-id              UTF8String,         -- e.g. "skybridge-pi-palh-01"
    schema-version       INTEGER,             -- what I emit by default
    schema-supported     SEQUENCE OF INTEGER, -- list of versions I can decode, e.g. [1,2,3,4]
    app-version          UTF8String,          -- e.g. "0.7.2"
    capabilities         SEQUENCE OF UTF8String,  -- ["statewide_grid", "auto_squelch", ...]
    region-id            UTF8String,          -- e.g. "anchorage_bowl"
    anchors-served       SEQUENCE OF UTF8String,  -- ["PALH", "PAMR"] for nodes that own anchors
    last-fresh-data-ts   INTEGER,             -- Unix epoch of last fresh observation
    backhaul-state       ENUMERATED { online, degraded, offline },
    location             Geohash OPTIONAL,    -- for stationary nodes only
    
    ...                                       -- extension marker
}
```

Sent every 5 minutes on `sb/node/hello/{node_id}` (MQTT, retained) and broadcast on Meshtastic at SAFETY priority (so reaches everyone reliably).

When node A wants to send to node B:
1. Look up B's last received HELLO
2. Use B's `schema-supported` to pick an encoding both can speak
3. Use B's `capabilities` to know which optional features to include
4. Encode + transmit

If B has never been seen, default to schema v1 (the lowest common denominator).

## Worked example: schema evolution over 5 years

Imagine a deployment timeline:

```
2026  v1.0   Initial schema. Defines:
              Observation { ts, location, wind, temp, pressure, ... }
              Composite   { contributors, weights, derived_values }
              Alert       { type, polygon, valid_from, valid_to }
              Hello       { node_id, schema_version, capabilities }

2027  v2.0   Add optional fields to Observation:
              + freezing_level_ft
              + cloud_layers (3-band breakdown vs single max)
              + visibility_obstructions ("FG", "BR", "HZ", etc.)
              All optional. v1 decoders ignore.

2028  v3.0   Add optional fields to Composite:
              + volatility_score
              + cert_revision_hash (for tamper detection)
              v1 + v2 decoders ignore.

2029  v3.5   Add new payload type:
              + EmergencyBeacon (aircraft transponder distress, ELT activation)
              v1, v2, v3 decoders see "unknown payload type" and skip.
              v3.5+ decoders show as urgent UI alert.

2030  v4.0   Realize cloud_layers field needs more granularity. Add:
              + cloud_layers_v2 (5-band breakdown)
              cloud_layers (the original) STILL EMITTED for v1-v3 readers.
              Both fields populated for ≥2 years.

2032  v4.5   Audit: 0.05% of nodes still emit only cloud_layers (3-band).
              0.005% emit only v1 (no cloud breakdown at all).
              Continue dual-emit; cost is trivial.

2033  v5.0   Add new payload: SBSensorTelemetry (uptime, battery, cell signal)
              for fleet management. v1-v4 decoders skip.
```

At any point in this 7-year arc, a node from any year can talk to a node from any other year. They negotiate down to whichever version both speak. The conversation has fewer fields, but is never silent.

## Per-version dual-emit windows

When a major version increases, encoders dual-emit (one packet in old, one in new) for a deprecation window:

```
Window 1: First 6 months after v_new release
  → Always dual-emit. Anyone, regardless of subscribed version, gets both.
  → New version is opt-in for receivers.

Window 2: Months 6-12
  → Default to v_new. Emit v_old when peer's HELLO declares only v_old support.
  → Audit log: count how often dual-emit triggers.

Window 3: Months 12-24
  → Default to v_new. Suppress v_old unless receiver explicitly asks.
  → Audit log: what fraction of traffic still requests v_old?

Window 4: Months 24+
  → Stop emitting v_old by default. Old nodes get a warning.
  → Don't actually drop v_old; readers still try to decode.
```

Storage cost: in dual-emit, payloads ~2× larger. We accept this for the deprecation window because cert credibility depends on graceful evolution.

## Bandwidth audit during version transitions

Per the audit log, every version-skew event is recorded. Reports:

```
30-day report: schema version distribution

Version    % of traffic    Median age of nodes
v1         0.005%          7 years (a few legacy fixed installs)
v2         0.2%            5 years (most retired)
v3         12%             3 years (slow-update phones)
v4         85%             1 year (current default)
v5         3%              <30 days (early adopters)

Recommended actions:
  - Continue dual-emitting v3 alongside v4 for at least 6 more months
  - v1 / v2: write off, log warning to legacy node operators if reachable
  - v5: check stability before promoting to default
```

This becomes part of the cert artifact: documented evidence that SkyBridge is intentional and disciplined about evolution.

## Schema version tags in TAIGA payloads

TAIGA's ASN.1 schema has its own version markers (the extension marker `...`). SkyBridge's envelope `ver` byte is **independent** of TAIGA's internal version. So we can:

- Bump SkyBridge envelope version when adding new envelope fields (e.g., adding a `signature` field for tamper detection)
- Bump TAIGA payload version when adding new observation fields (NASA's responsibility, with our addendum proposals)

Two version namespaces, both forward-compatible. Receivers handle both.

## The "drop or interpret" decision

When a receiver sees a packet from `ver_remote > ver_local_max`:

**Option A: drop the packet**
- Pros: certain to never misinterpret data
- Cons: lose information unnecessarily; data the receiver could have used is silently discarded

**Option B: best-effort decode the prefix**
- Pros: gets the data the receiver understands
- Cons: risk of misinterpretation if the new version restructured the prefix (which it shouldn't, but bugs happen)

**SkyBridge default: Option B with safety guardrails**
1. Decode the prefix matching `ver_local_max`
2. Check for an embedded "structure changed below this version" flag (a SkyBridge convention)
3. If flag present, reject; else accept the prefix
4. Log every such event to audit_log for monitoring

This converges with TAIGA's behavior (best-effort decode is standard for ASN.1).

## Tamper-resistance and signing (deferred to v2)

SkyBridge v1 envelopes are not cryptographically signed. Anyone with the AES-256 mesh primary-channel key can emit a message that decodes successfully. This is acceptable for v1 because:

- Mesh keys are pre-shared per region, only known to authorized regional members
- MQTT auth gateway gates topic publication
- Audit log records who published what, when

For SkyBridge v2 (out of scope for this paper-lab v0.1), we plan to add per-message signatures:

```
SignedEnvelope ::= SEQUENCE {
    envelope    Envelope,
    signature   BIT STRING,      -- Ed25519 signature over envelope
    signer-id   OCTET STRING     -- public-key fingerprint
}
```

Out-of-band signer-id verification (e.g., signed by FAA-AAAI-trusted root) becomes the path to "FAA-attestable" individual messages. That's a future paper-lab section.

## Configuration for compatibility

In `config.yml` (per [`02-protocol-stack.md`](02-protocol-stack.md)):

```yaml
schema:
  current_version: 1                    # what new packets carry
  emit_for_legacy: true                 # also dual-emit for older peers when known
  decode_min_version: 1                 # reject below this (v1 is forever)
  decode_max_version: 99                # accept future versions, best-effort
  unknown_field_policy: ignore          # never reject; log to audit
  unknown_type_policy:  ignore_log      # log to audit, attempt v1 fallback

compatibility:
  hello_interval_s: 300                 # capability announcements every 5 min
  peer_version_cache_ttl_s: 3600        # forget a peer's version after 1hr silent
  legacy_window_months: 24              # dual-emit window after major bump
  log_version_events: true              # writes to audit_log
```

Operators tune `legacy_window_months` longer for higher-stability deployments (hospitals, search-and-rescue) and shorter for active development (research nodes).

## Test suite for compatibility

Every release has a compatibility test suite covering:

```
test_v1_receives_v2_packet:
    encode an Observation with v2 fields
    decode with v1 decoder
    assert: succeeded; only v1 fields populated
    assert: log contains "newer_version_received"
    
test_v2_receives_v1_packet:
    encode an Observation with only v1 fields
    decode with v2 decoder
    assert: succeeded; v2-only fields are None/null
    assert: no error logged
    
test_v1_receives_unknown_payload_type:
    encode an EmergencyBeacon (v3.5)
    decode with v1 decoder
    assert: succeeded; "unknown_payload_type" logged
    assert: receiver did not crash
    
test_unknown_future_version:
    encode with version=99 (future)
    decode with v1 decoder
    assert: best-effort decode succeeded for v1 fields
    assert: "future_version_received" logged
    
test_malformed_packet:
    arbitrary garbage bytes
    decode
    assert: rejected; "malformed_packet" logged
    assert: did not crash
    
test_round_trip_through_3_hops:
    encode at A, route through B and C, receive at D
    assert: byte-for-byte equivalence
```

These tests run on every commit. Versioning regressions are caught at PR time, not in the field.

## What this section does NOT yet specify

- **Cryptographic signing** (deferred to SkyBridge v2)
- **Versioning across major paradigm shifts** (e.g., if we replace TAIGA with something else — what's the migration path?)
- **Database schema migrations** for SQLite — separate from wire format, has its own forward-compat rules (Alembic-style migrations recommended)

Open as of v0.1.
