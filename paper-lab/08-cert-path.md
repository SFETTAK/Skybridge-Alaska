# 08 — Certification Path

> How SkyBridge moves from "interesting open-source project" toward "auditable, defensible source of supplementary information." The audit log and continuous accuracy comparison against METAR are the load-bearing pieces today; peer review, public mirror, and FAA AAAI engagement are longer-term goals, not near-term commitments.

## What "certified" actually means

The FAA does not currently certify community weather products. There is no checklist that grants SkyBridge official status. Pilots are free to use any weather information; the regulatory question is what they're *required* to use as the basis for go/no-go decisions.

For SkyBridge to be useful to dispatchers and pilots — including regulated operations (Part 121 airlines, Part 135 charter) and Part 91 general aviation — we need:

1. **Provenance** — every value SkyBridge displays must be traceable to its source(s)
2. **Accuracy attestation** — quantitative agreement statistics vs FAA-approved baselines (NWS METARs)
3. **Auditability** — every weight, threshold, and tuning parameter logged
4. **Open methodology** — the math is published, not proprietary
5. **Independent verification** — third parties can verify our claims at any time

These are the legs of the certification stool. None of them is the FAA stamping a paper, but together they constitute the kind of evidence package the FAA requires when evaluating any new tool for operational use.

## Leg 1 — The audit log

Every meaningful event is logged to `audit_log` with timestamp, actor, action, and reason. The schema (already drafted in [`02-protocol-stack.md`](02-protocol-stack.md)):

```sql
CREATE TABLE audit_log (
  ts          INTEGER NOT NULL,         -- Unix epoch
  who         TEXT,                     -- authenticated user, or 'system'
  what        TEXT NOT NULL,            -- e.g. 'config.refresh_cadence'
  old_value   TEXT,                     -- previous value
  new_value   TEXT,                     -- new value
  reason      TEXT,                     -- short explanation
  source      TEXT NOT NULL,            -- 'admin-ui' / 'config-file' / 'startup'
  ip_address  TEXT,                     -- if applicable
  session_id  TEXT                      -- auth gateway session, for correlation
);
CREATE INDEX idx_audit_ts ON audit_log(ts);
CREATE INDEX idx_audit_what ON audit_log(what);
```

Events that get logged:

| Category | Examples |
|---|---|
| Config changes | Refresh cadence adjusted, anchor added/removed, source enabled/disabled |
| Schema events | Version skew detected, dual-emit triggered, unknown future-version received |
| Auth events | Login success, login failure, password rotated, 2FA enabled |
| Network events | Backhaul transition, regional Pi outage, Dell partition |
| Data events | New observation source online, source dropped, sensor anomaly flagged |
| Operational events | Pi reboot, service restart, queue overflow, drop of high-priority packet (alarm) |

**Retention**: forever. Storage is cheap (SQLite, ~100 bytes per event); the audit value is permanent.

**Access**: read-only for authenticated `auditors` group; write for `admins` only via the admin API (which itself gets audit-logged).

**Backup**: nightly export to encrypted bundle, mirrored to Dell + offsite.

## Leg 2 — Accuracy attestation

Continuous comparison of every ingested source against the METAR baseline runs on the `wx-shootout` and `wx-validate` dashboards in the development kneeboard fork. These dashboards are not yet exposed on the public mirror; they will be made available as the project matures. Each source is shown as itself; SkyBridge does not modify or republish authoritative observations. The dashboards produce:

```
Per anchor, per source, per field, last 30 days:
  - Sample count (n)
  - Mean absolute error (MAE) vs METAR
  - Signed bias
  - Median error
  - 95th percentile error
  - Drift trend (MAE this week vs last week)
```

This is research transparency, not a derived product. A reader can see which sources agree closely with METAR at which anchors over what windows, and draw their own conclusions.

**Publication**: the dashboards are auth-gated today and intended to become read-only public on the project domain.

**Reproducibility**: any snapshot of the underlying SQLite database can be replayed to produce the same statistics. The query and the data are both public.

## Leg 3 — Peer review

The peer-review pathway is the academic-credibility leg.

### Target venue

Realistic candidates, in order of fit:

1. **AMS Weather and Forecasting** journal — operational meteorology, focused on observations and short-range forecasting
2. **NASA Technical Memorandum** series — TAIGA was published here (Rios 2015); our addendum could follow the same path
3. **Journal of Atmospheric and Oceanic Technology** — instrument and observation-network methods
4. **AIAA / Aerospace Research Central** — aviation-specific
5. **ACM Computer Communication Review** — for the protocol-stack contributions specifically
6. **Open Source Society / Foundation papers** — for community-engineering aspects

Realistically: **NASA TM is the most natural home**. TAIGA is there. Our addendum is incremental work on a NASA-published protocol. NASA Ames has institutional interest in TAIGA evolution.

### Manuscript structure (when ready to submit)

The Paper Lab is the manuscript source. Compiling via pandoc:

```
Title:    Multi-Model Ensemble Weather Aggregation for
          Alaskan General Aviation: A Mesh-Network Approach
Authors:  Steven Fett (Alaska DOT&PF), [optional co-authors]
Section:  Joseph L. Rios (NASA Ames) referenced; addendum proposed jointly

Abstract  (300 words from paper-lab/00-overview.md)
1.        Introduction (the AK gap, gap analysis, prior solutions)
2.        Architecture overview (paper-lab/01-network-topology.md)
3.        Protocol stack (paper-lab/02 + 03)
4.        Quality of service and routing (paper-lab/04)
5.        Versioning and compatibility (paper-lab/05)
6.        Worked scenarios (paper-lab/06)
7.        Empirical results (paper-lab/07 simulator outputs + 12 months production data)
8.        Proposed TAIGA extensions (paper-lab/09)
9.        Discussion: limits, future work, comparison with FAA AAAI
10.       Conclusion
11.       Acknowledgments
References (paper-lab/10)
Appendix A: ASN.1 schemas
Appendix B: Configuration reference
Appendix C: Data dictionary
```

Estimated final manuscript: 25-40 pages with figures.

### Submission timing

Not before:
- 12 months of production deployment data (Jan 2027 earliest)
- Bench-test simulator passes all scenarios
- TAIGA addendum proposals validated empirically
- Co-authors confirmed (Joseph Rios at NASA, FAA AAAI representative)

This is intentional. Submitting earlier risks publishing claims that don't survive contact with reality.

## Leg 4 — Public read-only mirror

The public mirror at `lab.skybridgealaska.net` (or similar) operates without authentication for read-only access:

```
Allowed without auth:
  GET /api/wx/grid                           (current state)
  GET /api/wx-validate/timeline              (historical)
  GET /api/wx-validate/snapshot/<ts>         (specific moment)
  GET /api/audit/public                      (anonymized audit log)

Auth-gated:
  Everything else (write APIs, admin UI, internal logs)
```

Anyone can:
- Query current state
- Query history
- Verify SkyBridge claims against their own data
- Build alternative front-ends or analysis tools

This is the strongest possible "we have nothing to hide" signal. Cert reviewers can see the system in operation without our participation.

## Cert artifact composition

When the time comes to submit a cert package (to FAA, to a state agency, to an aviation insurance underwriter), the package contains:

```
SkyBridge Alaska Accuracy Attestation Package
─────────────────────────────────────────────

§1. System overview (paper-lab/00-overview.md)
§2. Network architecture (paper-lab/01-network-topology.md)
§3. Methodology (paper-lab sections 02-05)
§4. Empirical results
    §4a. 12-month accuracy attestation (auto-generated PDF)
    §4b. Public mirror metrics (uptime, query rate, observed accuracy)
§5. Audit log
    §5a. Schema description
    §5b. Sample dump (last 100 events, redacted as appropriate)
§6. Open-source license (AGPL-3.0 with commercial option)
§7. Hardware specifications (hardware/SPECIFICATIONS.md)
§8. Reproducibility instructions
    §9a. How a reviewer verifies any claim themselves
    §9b. Data export formats
§10. References (paper-lab/10-references.md)

Appendices:
  A. ASN.1 schemas (TAIGA + SkyBridge extensions)
  B. Configuration reference (config.yml schema)
  C. Glossary (paper-lab/glossary.md)
  D. Contact information for follow-up
```

The cert package is itself versioned and reissued quarterly. Old versions remain accessible for comparison.

## Cert pathway timeline (informational)

```
Phase 0  (current, May 2026)
  - SkyBridge runs as research at Alaska DOT&PF
  - Paper Lab v0.1
  - Pi station deployed, kneeboard operational
  - Auth gateway + edge-tunnel incoming

Phase 1  (Q4 2026 - Q1 2027)
  - Statewide anchor expansion (90+ stations)
  - Dell migration
  - 12 months of production data
  - First quarterly attestation report
  - First public mirror release

Phase 2  (Q2-Q3 2027)
  - Bench test simulator complete
  - First peer-review submission to NASA TM (TAIGA addendum)
  - First Part 135 / DOT&PF formal pilot deployment

Phase 3  (2028+)
  - Independent third-party audit of accuracy attestations
  - FAA AAAI working group engagement
  - Recognition as "supplementary information source"
  - Recognition as "authoritative for specific anchors" (per-anchor cert)
  - Multi-state deployment (other northern states with similar gaps)
```

Each phase has explicit gates. We don't advance until the prior phase has validating data.

## What this paper does not promise

Honest framing:

- SkyBridge does **not** claim to be a substitute for FAA-approved sources
- SkyBridge does **not** claim 100% accuracy or zero error
- SkyBridge does **not** promise availability during all conditions (mesh networks have failure modes)
- SkyBridge does **not** replace pilot weather briefing requirements; it supplements them

The cert path is about being a **credible supplementary source**, not replacing the official network. Anyone who interprets it otherwise has misread the project.

## Coordination with FAA AAAI

The Alaska Aviation Weather Improvement Initiative (AAAI) is the FAA working group focused on the AK weather coverage gap. Our work directly supports their stated mission. We are not adversarial.

When ready (Phase 1 timeline above), we engage with:
- FAA AAAI lead representative for Alaska
- NWS Anchorage Forecast Office (for METAR/SIGMET partnership)
- NWS Alaska Region Headquarters
- DOT&PF Aviation Division (already partnered)
- Alaska Air National Guard (search-and-rescue use case)
- Bristol Bay Native Corporation (rural community deployment)
- Native Village of Anaktuvuk Pass (and similar village governments)

The cert path is also a relationship-building path. We don't ship a cert package and walk away; we build the relationships that make adoption possible.

## Open cert questions

- **Liability framing**: when SkyBridge data influences a flight decision and something goes wrong, who's accountable? (Our license disclaims; open question how cert packages address this.)
- **Per-anchor vs systemwide certification**: should we propose individual anchors get certified separately (e.g., MWOS Lake Hood individually attestable) or only the system as a whole?
- **What happens at Phase 4 if FAA declines partnership**: do we continue independently? (Yes, but coordination is preferred.)

Open as of v0.1.
