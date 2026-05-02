# 00 — Overview

## The problem

Alaska is the most dangerous place to fly a small aircraft in the United States. General-aviation pilots in Alaska are statistically 36 times more likely to die on the job than the average American worker [CDC/NIOSH via Washington Post]. Roughly 80% of the state has no reliable real-time weather coverage and significant gaps in VHF radio coverage [Alaska DOT&PF Aviation Gap Analysis, March 2024]. Cellular coverage drops out within a few miles of population centers. Mountain passes, coastal villages, and the entire Arctic Slope have observation gaps where the official FAA AWOS / ASOS network has no coverage at all. Where it does have coverage, AWOS/ASOS outages are common: per FAA 2023 data summarized on Alaska's [AWOS Outages dashboard](https://experience.arcgis.com/experience/e14014a78d6f4fb29a91c8e4fe25d22c), approximately one in three weather stations was experiencing some type of outage on an average day, an issue significant enough that the Alaska State Legislature has petitioned Congress to address it.

Aviation weather products on the consumer market (ForeFlight, Garmin Pilot, FltPlan Go) are built for the lower 48. They subscribe to FAA-approved feeds, render them well, and charge $80 to $300 per pilot per year. They do not solve the underlying coverage gap. They cannot, because the data simply is not collected at the resolution Alaska needs.

## The thesis

Three claims, each independent, together unlocking the system:

1. Calibrated private observations cost a fraction of FAA AWOS and can be deployed with a small crew. The Montis Corp MWOS network has demonstrated this with 14 stations across Alaska. SkyBridge contemplates extending that idea with low-cost distributed weather nodes at additional sites. Sensor designs, vendors, and supply chains are intentionally not committed in this paper-lab; the project is prototyping and treats the BOM as an open design question.
2. Aggregating multiple authoritative weather sources, side by side, gives a pilot more information than any single source alone. SkyBridge ingests METARs, MWOS observations, NWS gridded forecasts, and the major global models (NOAA GFS, Environment Canada GEM, ECMWF, JMA) and shows them as themselves on the kneeboard. SkyBridge does not modify, replace, or re-publish authoritative data. It surfaces it. The continuous record produced by recording all sources together is, separately, a research dataset; whether that dataset eventually supports better Alaska-specific weather modeling is an open research question, not a SkyBridge product claim today.
3. A delay-tolerant mesh network can extend weather and traffic data into regions where cellular and satellite are unreliable. Meshtastic's LoRa primary-channel encryption and self-healing routing, combined with opportunistic store-and-forward via overflying aircraft (the data-mule pattern), give a path to last-mile delivery at $50 per pilot radio in regions that traditional infrastructure cannot economically reach.

SkyBridge synthesizes the three. It is open-source (AGPL-3.0 with commercial option), runs on commodity hardware ($470 Raspberry Pi 5 plus $30 SDR dongles plus $50 Meshtastic radio), and the first ground station is operational at Alaska DOT&PF.

## What this paper covers

The Paper Lab is the architecture document for the SkyBridge mesh-protocol stack. It covers:

- How regional weather data flows through the system, from authoritative source (METAR, MWOS, model) through ingestion, storage, encoding, transmission, decoding, and rendering.
- How nodes communicate. Meshtastic LoRa within a region (~50 nm diameter), MQTT bridging across regional Pis, internet or cellular backhaul to the central aggregator on a Dell server.
- How data is encoded efficiently for delivery over LoRa. TAIGA ASN.1 protocol from NASA Ames as the proposed wire format.
- How priority is enforced. Five quality-of-service classes from SAFETY (SIGMETs, severe alerts) to BULK (historical replay), each with its own queue and TTL.
- How compatibility is preserved. Wire format never breaks. Every packet self-describes its schema version. Decoders are forward-tolerant.
- How accuracy is tracked. Each source is compared against METAR ground truth on internal development dashboards (`wx-shootout` / `wx-validate`), with rolling agreement statistics. These dashboards live on the development kneeboard fork today and will be made available on the public mirror as the project matures. Each source is shown as itself; the comparison is research transparency, not a derived product.

What the Paper Lab does not cover yet: user-interface design, business model, deployment economics, multi-state expansion. These are addressed elsewhere in the repository.

## System diagram (high level)

<!-- TODO: render `topology.svg` — regional clusters, backhaul, pilot trajectory -->

```
[FIGURE: SkyBridge System Topology]
Regional mesh clusters (Anchorage Bowl, Mat-Su, Kenai, Bristol Bay, Interior,
Aleutian, Arctic Coast, Yukon, Southeast), each ~50 nm radius, exchanging
data over LoRa Meshtastic locally and MQTT-over-internet to a central Dell
server. Pilots and vehicles roam between clusters, pulling fresh data on
arrival and forwarding cached data on departure.
```

## Goals (what success looks like)

| Goal | How it's measured |
|---|---|
| Data freshness in Alaska Bowl | <5 min lag between METAR issuance and pilot rendering, 99% of the time |
| Mesh-only delivery latency | <30 min from issuance to render, even without cellular or internet, 95% of the time |
| Per-source accuracy transparency | 30-day rolling agreement statistics for each source against METAR baseline, viewable on the project's internal `wx-validate` dashboard during development; planned for public mirror as the project matures |
| Coverage | At least one observation source within 30 nm of every populated point in Alaska |
| Open-source release | All ingestion, mesh, and rendering code published under the project license |

## Non-goals

What SkyBridge is not trying to be:

- Not a replacement for the FAA-approved weather observation network. SkyBridge ingests FAA-approved sources (NWS METARs, NWS gridded forecasts) and shows them on the kneeboard as themselves. SkyBridge does not modify, blend, or re-publish authoritative observations as if they were a SkyBridge product.
- Not a transmission service over VHF airbands. SkyBridge is VHF read-only. It listens, decodes, transcribes, and never keys up. Avoiding VHF transmission sidesteps FCC airband licensing entirely and respects the regulatory boundary FAA owns.
- Not a real-time flight-planning engine. SkyBridge displays weather. Pilots make go/no-go decisions using SkyBridge as one of multiple tools.
- Not a closed commercial product. The architecture is open-source so any state, organization, or pilot can deploy it. Commercial value, if it emerges, accrues from hardware sales, deployment services, and certification consulting, not from gating the data.

## What the architecture borrows from

The SkyBridge protocol stack is not invented from first principles. It is a synthesis of well-established protocols, each chosen because someone smarter has already solved a sub-problem:

| Component | Source | What we use |
|---|---|---|
| Wire encoding | TAIGA (NASA TM-2015-218427, Rios) | ASN.1 schema for PIREP, METAR, NOTAM, and polygon |
| Mesh transport | Meshtastic | LoRa SX1262 hardware, primary-channel encryption, NodeInfo discovery, AODV routing |
| Pub/sub backhaul | MQTT (MQ Telemetry Transport) | Topic hierarchy, QoS levels 0/1/2, retained messages, last-will testament |
| Delay-tolerant routing | DTN Bundle Protocol (RFC 5050) | Store-and-forward with TTL, opportunistic data-mule pattern |
| Congestion control | TCP CUBIC / BBR | AIMD per-link adjustment, backoff on loss |
| Queue management | Linux CoDel | Drop oldest in queue when congested |
| Authentication | Modern auth gateway with optional 2FA | Forward-auth at reverse proxy, file-backend users |
| Edge encryption | Edge-terminated tunnel | TLS terminated at the edge; outbound-only; no inbound port forwarding required |
| Compatibility model | Protocol Buffers (Google) | Forward-tolerant decoders, additive-only schema evolution |

Each is referenced in `10-references.md`. None is reinvented.

## Where SkyBridge claims original contribution

1. **Multi-source side-by-side display for Alaska.** No commercial pilot product currently aggregates METAR + MWOS + NWS Gridpoint + GFS + GEM + ECMWF + JMA on a single moving map for Alaska. SkyBridge does, on its internal development dashboards (`wx-shootout` and `wx-validate`). Each source is rendered as itself; the comparison is visible to anyone with access today and is planned for the public mirror.
2. **Geofenced data-tier degradation.** The same dashboard renders progressively less data as bandwidth drops, from full statewide model fields down to mesh-only TAIGA observations, without requiring a separate "offline mode."
3. **Cabin overflight as architecture.** Explicit support for stationary nodes that receive data from passing aircraft (not from cellular or satellite). This is a deployment topology that few mesh designs explicitly plan for.
4. **Continuous multi-source archive.** Recording every available authoritative source at high resolution produces a research dataset for Alaska that does not currently exist anywhere. Whether this dataset eventually supports better Alaska-specific modeling is an open research question, not a product claim.

These are written up further in their respective sections.

## Reading the rest of the lab

If you read only one more section, read [`01-network-topology.md`](01-network-topology.md). It locks the regional-mesh, backhaul, and geofence model that everything else depends on.

If you have time for three sections, add [`06-data-mule-scenarios.md`](06-data-mule-scenarios.md) (worked examples) and [`07-bench-test-spec.md`](07-bench-test-spec.md) (how we prove the design works).

If you are an FAA reviewer, focus on [`08-cert-path.md`](08-cert-path.md). That is the document that frames the supplementary-information posture and the audit-and-attestation approach.

If you are a NASA reviewer interested in TAIGA, see [`09-taiga-addendum.md`](09-taiga-addendum.md). That is the proposed extension to your protocol.

If you are a Meshtastic developer, [`02-protocol-stack.md`](02-protocol-stack.md) shows how SkyBridge layers on top of stock Meshtastic without forking the firmware.
