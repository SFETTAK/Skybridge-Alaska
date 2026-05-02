# SkyBridge Alaska — Paper Lab

> **This is research and architecture exploration for contributors.** It is not a description of the shipping product. The shipping product is described in the [top-level README](../README.md). Everything in this folder is draft work: subject to change, partly forward-looking, and represents thinking-in-progress rather than committed implementation. Current operational reality may differ from anything in here. We work out of local development directories; this paper-lab is published to GitHub as a snapshot to give contributors enough context to get started, not as a complete specification. If you want cutting-edge state, contact the maintainers.

The Paper Lab is the architectural notebook for SkyBridge Alaska. It captures decisions, alternatives considered, and worked examples for the network, protocol, and data flow.

It exists to do three things:

1. **Internal architecture log** — meaningful decisions with the alternatives weighed and the reason chosen
2. **Contributor onboarding** — enough context to start reading the code and contribute meaningfully
3. **TAIGA addendum draft** — proposed extensions to the NASA TAIGA protocol, with attribution if adopted

## Reading order

| File | What's inside |
|---|---|
| [`00-overview.md`](00-overview.md) | Problem statement, system summary, goals |
| [`01-network-topology.md`](01-network-topology.md) | Regional mesh model, backhaul, geofencing |
| [`02-protocol-stack.md`](02-protocol-stack.md) | Layered stack: TAIGA, SkyBridge envelope, MQTT, Meshtastic |
| [`03-data-encoding.md`](03-data-encoding.md) | Quantization, time, location, units |
| [`04-qos-routing.md`](04-qos-routing.md) | Priority classes, AIMD, queue management |
| [`05-versioning-compat.md`](05-versioning-compat.md) | Forward / backward compatibility rules |
| [`06-data-mule-scenarios.md`](06-data-mule-scenarios.md) | Worked examples: cabin overflight, regional handoff |
| [`07-bench-test-spec.md`](07-bench-test-spec.md) | Simulator design + test scenarios |
| [`08-cert-path.md`](08-cert-path.md) | Audit log, accuracy attestation against METAR, peer review pathway |
| [`09-taiga-addendum.md`](09-taiga-addendum.md) | Proposed TAIGA extensions for submission |
| [`10-references.md`](10-references.md) | Citations: NASA, FAA, RFC, prior art |
| [`glossary.md`](glossary.md) | Acronyms + terms |

## Figures

Diagrams live alongside the markdown as standalone SVG files so they can be embedded in markdown, LaTeX, or HTML uniformly:

- `topology.svg` — regional mesh + backhaul + a representative pilot trajectory
- `protocol-stack.svg` — the layer cake from hardware to data semantics
- `packet-walk.svg` — one METAR's full journey, byte-by-byte through the layers
- `compression-bake-off.svg` — TAIGA vs SkyBridge L1+2 vs gzipped baseline, sized bar chart
- `data-mule.svg` — cabin overflight scenario with the contact-window math
- `geofence.svg` — region polygons across Alaska with backhaul links

> Several SVGs are **placeholder** at v0.1 — the markdown references them with `<!-- TODO: render -->` markers. They are drafted collaboratively as architecture solidifies.

## Building the paper

```bash
# Single-file markdown for review
cat *.md > /tmp/skybridge-paper-flat.md

# PDF (academic format) — pandoc
pandoc *.md -o paper.pdf \
  --toc --number-sections \
  --pdf-engine=xelatex \
  --metadata title="SkyBridge Alaska: Architecture notebook" \
  --metadata author="Steven Fett (Alaska DOT&PF)"

# HTML (web-readable artifact)
pandoc *.md -o paper.html --toc --self-contained
```

## Status by section (May 2026)

| Section | Status | Next action |
|---|---|---|
| 00 Overview | Drafted | Review |
| 01 Network topology | Drafted | Render `topology.svg` |
| 02 Protocol stack | Drafted | Render `protocol-stack.svg` |
| 03 Data encoding | Drafted | Empirical bake-off |
| 04 QoS routing | Skeleton | Flesh out AIMD math |
| 05 Versioning | Skeleton | Schema-evolution worked example |
| 06 Data-mule scenarios | Drafted | Render `data-mule.svg` |
| 07 Bench test spec | Skeleton | Lock simulator scope |
| 08 Cert path | Skeleton | Coordinate with FAA AAAI |
| 09 TAIGA addendum | Skeleton | Empirical results required first |
| 10 References | Drafted | Add as new citations land |
| Glossary | Drafted | Add as new terms emerge |

## Companion repositories

The Paper Lab references but does not reproduce code from:

- `ground-station/` — DOT-VHF Pi station scripts (production)
- `app/` — pilot client app (in development)
- `protocol/TAIGA_PROTOCOL.md` — NASA-published TAIGA reference

## License

Same dual license as the parent repository (AGPL-3.0 with commercial option, per `LICENSE.md`). Citations, figures, and worked examples may be reproduced under AGPL-3.0 terms with attribution to *SkyBridge Alaska* and *Steven Fett, Alaska DOT&PF*.

The TAIGA protocol referenced throughout is NASA-published (TM-2015-218427, Joseph L. Rios) and is in the public domain. Our addendum proposals are offered to NASA Ames Research Center for incorporation under the same public-domain terms.
