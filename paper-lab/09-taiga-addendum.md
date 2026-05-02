# 09 — TAIGA Addendum: Proposed Extensions

> Where SkyBridge's empirical work has identified gaps in the original TAIGA (NASA TM-2015-218427, Joseph L. Rios) and drafts specific extensions. Offered as a draft addendum under the same public-domain terms as the original protocol. Whether and when the addendum is formally adopted is not on SkyBridge's critical path; the wire format ships either way.

## NASA adoption is a nice-to-have, not a dependency

To be explicit upfront: SkyBridge ships with these extensions whether NASA Ames adopts them or not.

- **If adopted**: the extensions become part of TAIGA proper. SkyBridge's payloads are standard TAIGA, decodable by any TAIGA-compliant tool the broader aviation community develops.
- **If not adopted**: SkyBridge uses the extensions as proprietary payload types under its own L5 envelope (see [`02-protocol-stack.md`](02-protocol-stack.md)). The wire format is the same; only the formal owner of the spec changes. SkyBridge's compatibility law (see [`05-versioning-compat.md`](05-versioning-compat.md)) keeps the extensions stable across versions either way.

This decoupling is intentional. The extensions exist because they solve real problems we have hit in deployment. NASA's adoption process is separate from SkyBridge's shipping schedule. The addendum is offered as collaborative work; the engineering does not block on it.

## Context

TAIGA was published in 2015 to address Alaska aviation's bandwidth-constrained data transmission needs. It defined PIREP, METAR, NOTAM, weather polygon, emergency, and system-message payload types. It was visionary work that solved 80% of the problem for its target use case.

Ten years later, deployment of multi-source weather aggregation systems (SkyBridge being one) has identified specific TAIGA gaps. The aviation community would benefit from extending TAIGA rather than forking it. This section proposes concrete extensions, designed to be backward-compatible with TAIGA v1 implementations.

We propose the extensions as a collaborative addendum to the original NASA TM, with Joseph L. Rios listed as senior author and SkyBridge contributors as co-authors. SkyBridge will continue to use the same wire format whether the addendum is formally adopted or shipped as our own L5 payload definitions.

## What's proposed (overview)

| Extension | What it adds | Backward-compatible? |
|---|---|---|
| **B. Volatility flag in observations** | Tag rate-of-change as calm/changing/dangerous | Optional field at end of Observation |
| **C. Source-confidence tag** | Per-record quality indicator (sensor health, calibration) | Optional field |
| **D. Region-tag for routing** | Explicit geofence region identifier | Optional field |
| **E. Multi-precision geohash** | Variable precision per payload type | Length field already in geohash |

All extensions follow ASN.1 extension marker convention. v1-only readers see them as unknown fields and skip without error.

## Extension B: Volatility flag

### Motivation

Routine METARs are issued hourly. SPECIs (off-cycle reports) are issued when conditions change rapidly (the FAA's 20-minute SPECI threshold). But neither encoding tells the receiver "this value is changing fast — your decisions should weight uncertainty."

SkyBridge tracks rate-of-change continuously. Encoding the volatility into observations lets downstream consumers prioritize fresh ones over stale-and-still-changing ones.

### Schema

```asn1
-- Add to existing TAIGA Observation type (METAR, MWOS, etc.) at the extension marker

Observation ::= SEQUENCE {
    location              Geohash,
    time-offset           INTEGER (0..143),
    -- ... existing v1 fields ...
    
    ...,                                        -- extension marker (v1)
    volatility            INTEGER (0..3) OPTIONAL,    -- proposed v2
    
    -- 0 = STABLE      (Δ within ±1 hour smaller than threshold)
    -- 1 = CHANGING    (Δ small but non-zero)
    -- 2 = ELEVATED    (Δ above SPECI threshold)
    -- 3 = DANGEROUS   (Δ above safety threshold; auto-escalates to CRITICAL class)
}
```

### Where the value comes from

Computed from rate-of-change in the source's own observations:

```
volatility(anchor, field, lookback_min):
    deltas = [abs(val_now - val_prev) for val_prev in last lookback_min observations]
    if deltas.max() / threshold[field] > 4: return DANGEROUS
    if deltas.max() / threshold[field] > 2: return ELEVATED
    if deltas.max() / threshold[field] > 1: return CHANGING
    return STABLE
```

Thresholds (from `04-qos-routing.md`):
- Wind direction: 30° per hour
- Wind speed: 10 kt per hour
- Ceiling: 500 ft per 30 min
- Visibility: 2 sm per 30 min
- Pressure: 2 mb per 30 min
- Temperature: 5 °C per hour

The audit log records every volatility transition with the trigger.

## Extension C: Source-confidence tag

### Motivation

A METAR from a known-functional ASOS is more trustworthy than a METAR from a sensor flagged as "intermittent" by the FAA's daily reliability report. A wind reading from a sensor at a known calibration tolerance is more trustworthy than from one whose calibration is unknown.

TAIGA doesn't carry per-record confidence. The receiver can't distinguish "data from a healthy sensor" from "data from a degraded one."

### Schema

```asn1
-- Add to existing Observation at extension marker

Observation ::= SEQUENCE {
    -- ... existing fields ...
    ...,
    source-confidence     INTEGER (0..15) OPTIONAL,     -- proposed v2
    
    -- 0  = unknown (no calibration data)
    -- 1-3 = degraded (sensor flagged)
    -- 4-7 = standard (in normal calibration)
    -- 8-11 = high (recently calibrated, no drift)
    -- 12-15 = within recent calibration window, no drift detected
}
```

The receiver can use the confidence tag to filter or annotate observations on the kneeboard. Audit log records calibration events.

## Extension D: Region-tag for mesh routing

### Motivation

SkyBridge's regional mesh model (per [`01-network-topology.md`](01-network-topology.md)) needs to identify which region a payload "belongs to" for routing decisions. TAIGA's geohash gives location, but doesn't say what region.

Computing region-from-geohash is feasible but expensive on each routing decision. Embedding the region tag explicitly in the payload skips that.

### Schema

```asn1
-- Add to envelope or payload metadata

RegionTag ::= UTF8String (SIZE(0..32))    -- e.g. "anchorage_bowl", "interior", "bristol_bay"
```

### Why a UTF8String

Could be an ENUMERATED. We chose UTF8String because:
- New regions (e.g., when SkyBridge expands beyond Alaska) require schema change for ENUM but only a config update for string
- 32 chars is plenty; storage cost negligible (8 chars typical)

The list of valid region tags is in `config.yml` and version-controlled.

## Extension E: Multi-precision geohash

### Motivation

While TAIGA supports variable-length geohashes, it lacks explicit per-payload-type defaults. For aircraft position (collision avoidance), geohash-7 (76 m) is too coarse. For SIGMET areas (covering 100s of km²), it is too fine.

Variable precision saves bandwidth on coarse-area payloads and provides accuracy where needed.

### Schema

TAIGA geohash already supports variable precision via length field. We propose explicit per-payload-type defaults:

```
PIREP                   geohash-7 (76 m)        - precise pilot position
METAR                   geohash-7               - station location
MWOS                    geohash-7               - station location
SIGMET                  geohash-5 (2.4 km)      - area-bounded
TFR                     geohash-5               - area-bounded
AIRMET                  geohash-4 (20 km)       - large area
EmergencyBeacon         geohash-9 (2.4 m)       - life-safety, max precision
```

Some tweaks to TAIGA's geohash encoding may be required to make precision genuinely variable; the addendum lays out the specific bit-packing changes.

## Submission strategy

### Step 1 (now → 12 months): build empirical evidence

- Deploy SkyBridge with the proposed extensions implemented
- 12 months of production data showing the extensions in use
- Bench-test simulator validates the extensions work as designed
- Per-source accuracy comparison against METAR baseline available on the public mirror

### Step 2 (12 months → 18 months): write the addendum manuscript

- Title proposal: "Extensions to TAIGA for Multi-Model Ensemble Aviation Weather"
- Authors: Joseph L. Rios (NASA Ames, original TAIGA author), Steven Fett (Alaska DOT&PF), [other contributors as project grows]
- Format: NASA Technical Memorandum, following the structure of TM-2015-218427
- Content:
  - Section 1: Original TAIGA recap
  - Section 2: Operational gaps identified through SkyBridge deployment
  - Section 3: Proposed extensions (B–E above with full ASN.1)
  - Section 4: Empirical results (compression, latency, accuracy)
  - Section 5: Backward-compatibility analysis
  - Section 6: Implementation guidance
  - Appendix A: Reference implementation in Python (asn1tools-based)

### Step 3 (18 months → 24 months): peer review + submission

- Internal review by NASA Ames team
- External review by FAA AAAI (Alaska Aviation Weather Improvement Initiative)
- External review by AMS Weather and Forecasting
- Submit to NASA TM series

### Step 4 (24 months → 30 months): publication + adoption

- Publication as NASA TM addendum
- Reference implementation in TAIGA upstream tools (asn1tools, JS decoder)
- Outreach to other AK aviation projects + adopters

### Realistic timeline

```
2026-Q3:  SkyBridge deploys extensions internally
2026-Q4:  First production data with extensions
2027-Q2:  6 months of empirical evidence accumulated
2027-Q3:  Manuscript drafting begins
2027-Q4:  Internal review with NASA Ames
2028-Q1:  External peer review
2028-Q2:  Submission to NASA TM
2028-Q3:  Publication
```

## Coordination protocol

We don't surprise NASA Ames with a submission. The collaboration starts early:

1. **Outreach email** (Q3 2026): Joseph Rios is informed of the project, the proposed extensions, the timeline. He's invited to be senior author of the addendum.
2. **Quarterly check-ins**: brief updates on deployment progress, any extension changes
3. **Co-authorship**: NASA Ames decides who from their team contributes; we adjust author list accordingly
4. **Reviewer recruitment**: NASA, FAA, MWOS/Montis Corp, Bristol Bay rural representative
5. **Open access**: addendum publishes under same public-domain terms as TM-2015-218427

If NASA Ames declines collaboration: we publish independently as Alaska DOT&PF technical report, citing TAIGA appropriately. Adoption is harder without NASA imprimatur but still feasible.

## What this addendum does NOT propose

Things we considered but decided against:

- **Replacing TAIGA's geohash with H3** (Uber's hexagonal grid) — more efficient for area queries, but breaks compatibility with every existing TAIGA implementation. Cost too high.
- **Replacing TAIGA's 10-min ticks with millisecond timestamps** — TAIGA's quantization is correct for routine weather; sub-second precision is in our high-precision events extension already.
- **Adding compression on top of ASN.1** (e.g., zstd of the encoded message) — TAIGA is already compact; an additional layer adds CPU cost for marginal gains.
- **Encryption layer** — Meshtastic provides primary-channel AES; MQTT can use TLS. TAIGA stays clear-text and compositional.

These are all defensible alternatives. Our choices reflect "evolve TAIGA carefully" over "redesign TAIGA cleverly."

## Reference implementation

The reference implementation (Python via `asn1tools`) lives in:

```
protocol/sb-taiga-extensions/
├── schema.asn1                  # SkyBridge extensions to TAIGA
├── taiga.asn1                   # original NASA TAIGA (unchanged)
├── encoder.py                   # uses asn1tools.compile_files([taiga, schema])
├── decoder.py                   # forward-tolerant decoder
└── tests/
    ├── test_extension_b_volatility.py
    ├── test_extension_c_confidence.py
    └── test_compat_v1_decoder.py
```

The reference implementation is the executable specification. If a v1-only decoder fails to read our messages, the test suite reveals it before any field deployment.

## Open addendum questions

- **Will NASA Ames agree to co-author?** Outreach happens Q3 2026; we plan the alternative if not.
- **Are the proposed extension types stable enough for v2?** Hopefully yes after 12 months of deployment; if a major v3 redesign emerges from operational experience, the addendum may bundle multiple changes.
- **What's the right channel to gather feedback from other aviation projects?** AMS conferences? GitHub Discussions on `protocol/skybridge-extensions`? NASAO 2027? Some combination.
- **How do we coordinate with non-Alaskan aviation deployments** that adopt the extensions independently?

Open as of v0.1.
