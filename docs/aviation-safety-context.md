# Aviation Safety Context and Talking Points

*Reference material for SkyBridge stakeholder conversations. Consolidates load-bearing statistics, quotes, and citations for use in grant proposals, NASAO and state aviation pitches, and press conversations.*

*Last updated: May 2026.*

---

## 1. The problem (verifiable statistics)

### From the Washington Post 2014 investigation
**"Alaska's outdated maps make flying a peril, but a high-tech fix is slowly gaining ground"**, Lori Montgomery, October 14, 2014.

- Alaska pilots are **36 times** as likely to die as the average US worker (CDC/NIOSH via Washington Post)
- Alaska has roughly **6 times** as many pilots per capita as the rest of the nation
- Since 2008, **15 controlled-flight-into-terrain crashes** killed 16 people and seriously injured 7
- Terrain mapping errors of **263+ feet** contributed to fatal crashes (Stack/Beane incident)

### From the Alaska DOT&PF Aviation Gap Analysis
*Alaska Aviation Weather Coverage Gap Analysis*, March 28, 2024. Approximately 84 pages, 29 recommendations.

- **29 Remote Communications Outlet (RCO) sites** with unscheduled outages in a single June 2023 snapshot
- Approximately **171 NOTAMs** within 100 miles of Anchorage at any given time
- **$350K to $400K** per traditional weather radar (NEXRAD) unit
- **Less than 30% ADS-B equipage** in Alaska's general aviation fleet
- The 90% federal target equipage was **never achieved**
- AWOS / ASOS outages significantly affect Part 135 dispatch decisions
- A discrepancy exists between FAA-reported equipment availability metrics and pilot-experienced reliability

For an official copy, contact Alaska DOT&PF Statewide Aviation Division.

---

## 2. The Stack/Beane case (referenced in pitches)

The 2010 crash that killed pilot Alex Stack (38) and FAA inspector Aric Beane (33), as documented in the Washington Post article:

> "Stack, 38, and Beane, 33, died on impact, leaving behind three small children. Ifsar later measured the final ridge 263 feet higher than Stack's GPS would have shown that day. The plane slammed into rock about 300 feet below the ridgeline, rescuers said, close enough to suggest the bad map may have made a difference."

Use this case carefully. It is illustrative of the problem space SkyBridge addresses indirectly (better situational awareness for pilots) but SkyBridge does not itself correct terrain elevation data.

---

## 3. Quotes (widely cited in AK aviation safety advocacy)

All quotes from the 2014 Washington Post investigation by Lori Montgomery.

> "Mars is better mapped than the state of Alaska."
> — Steve Colligan, E-Terra Aviation Safety

> "I told them [FAA], this is not the same as the lower 48. You'll kill people here."
> — Steve Colligan, E-Terra Aviation Safety

> "There was a crucial four or five minutes where we didn't know where we were. I have lost 25 friends in plane crashes."
> — Lt. Gov. Mead Treadwell

> "He was probably doing a really good job, because he navigated quite a ways in the clouds. If he had better tools, maybe he would still be around."
> — Dr. James Eule, crash survivor

> "We lobby. I'm sure Fugro lobbies. But as soon as they go to a CR [continuing resolution], you're screwed. We're talking about $30 million to finish the state. Thirty million dollars. When you consider all the benefits of the program, it seems like a no-brainer."
> — Ian Wosiski, Intermap Technologies

### Infrastructure framing from the article

> "Alaska has never been mapped to modern standards. While the U.S. Geological Survey is constantly refining its work in the lower 48 states, the terrain data in Alaska is more than 50 years old, much of it hand-sketched from black-and-white stereo photos shot from World War II reconnaissance craft and U-2 spy planes."

### Cost figures from the article (2014 dollars)
- $30 million to finish mapping Alaska to modern standards
- $150 million per year for the nationwide 3D elevation program
- $13 billion annual estimated economic benefits of complete mapping

Current costs are likely higher.

---

## 4. Gap Analysis recommendations that align with SkyBridge

These are specific recommendations from the DOT&PF Gap Analysis where SkyBridge's design proposes a response.

### Recommendation 1: Digital data sharing
> "Digitize all data and make it more accessible to pilots."

SkyBridge response: ingestion of NWS, MWOS, model, and FAA comms data into the kneeboard's API surface, with each source rendered side by side. Mesh distribution of compact TAIGA-encoded data is the design proposal for delivering this where internet is unavailable.

### Recommendation 7.2: Alternative weather systems
> "Evaluate alternative procurements for lower cost, possibly non-certified alternatives to AWOS installations."

SkyBridge response: low-cost distributed sensor nodes proposed for sites the FAA cannot economically cover with traditional AWOS installations. Specific node designs are TBD as the project matures.

### Recommendation 10.3: Real-time outage information
> "Consider agreement with FAA to receive automated daily updates on outages to be used in GIS application."

SkyBridge response: per-source health monitoring is part of the cert-validation dashboard. Audit-logging of source availability and accuracy creates the data this recommendation describes.

When citing recommendations, reference the Gap Analysis itself rather than this summary.

---

## 5. Existing solutions cost reference

For comparison context in stakeholder conversations.

### Satellite emergency / tracking
| Device | Hardware | Subscription |
|---|---|---|
| SPOT | $150+ | $150+ per year |
| Garmin inReach | $300 to $500 | $15 to $65 per month |
| Spidertracks | $1000+ | $30+ per month |

These solve the emergency-communication problem. They do **not** provide weather, traffic, or general operational data.

### Commercial pilot apps
| Product | Subscription |
|---|---|
| ForeFlight | $80 to $300 per pilot per year |
| Garmin Pilot | $80 to $300 per pilot per year |
| FltPlan Go | Free |

Commercial pilot apps are professional-grade and well-suited to pilots with cellular/WiFi coverage. They are not designed to operate where Alaska's coverage gaps are.

### SkyBridge for comparison
- ~$500 per ground station (Pi 5 + 3 SDR dongles)
- ~$50 per pilot Meshtastic radio
- $0 subscription
- Open source (AGPL-3.0 with commercial option)

SkyBridge is positioned as **complementary** to satellite and commercial pilot products, not a replacement. A typical Alaska bush pilot might reasonably carry a Garmin inReach for emergency uplink and a SkyBridge tablet for weather and traffic.

---

## 6. Common questions and honest answers

### "Is this legal?"

Yes. LoRa mesh operates on FCC Part 15 ISM band (902 to 928 MHz), no licensing required for fixed or aircraft-mounted use. SkyBridge does not transmit on VHF airbands; it is read-only there. The system is supplementary information; pilots make decisions using SkyBridge alongside FAA-approved sources.

### "Is it deployed?"

The first ground station, designated DOT-VHF, is operational at Alaska DOT&PF in Anchorage. The full multi-node mesh deployment described in the architecture is proposed but not yet field-deployed. Funding for that expansion was proposed via DOT&PF research program in April 2026.

### "Who pays for it?"

The DOT-VHF Pi station is hosted by Alaska DOT&PF. Hardware costs are ~$500 per ground station and ~$300 per sensor node. Recurring costs are zero (no subscriptions, no satellite fees, no API tier fees for the public data sources used). Multi-state expansion economics are out of scope at this stage.

### "How is this different from ADS-B?"

ADS-B is one input into SkyBridge, not a competitor. SkyBridge ingests ADS-B traffic and combines it with weather, transcribed VHF, and other data on a single moving map. The Meshtastic mesh extension, when deployed, is designed to operate below ADS-B coverage altitude in mountains and remote areas.

### "What about liability?"

SkyBridge is licensed as supplementary information only. Pilot decision-making is unchanged. The license terms (AGPL-3.0 with commercial option) include standard disclaimers. The project is not currently TSO-certified and does not claim primary-source status.

### "What's the certification status?"

SkyBridge is not currently FAA-certified or TSO-approved. The certification path is described in `paper-lab/08-cert-path.md`: continuous accuracy attestation against METAR baseline, audit logging, peer-review submission to the NASA Technical Memorandum series, and eventually FAA AAAI coordination. This is multi-year work.

### "Can other states use this?"

Yes. The code is open source. States with similar terrain and coverage challenges (Montana, Idaho, Wyoming, Maine) face structurally similar problems. SkyBridge is portable in design. Whether other states have actually expressed interest is not yet established; outreach to state aviation departments is part of the Phase 2 roadmap.

### "Who is behind it?"

Steven Fett and Ryan Marlow at Alaska DOT&PF, with engineering and architecture work documented in the project journal and paper-lab. SkyBridge is a state-employee research project published as open source.

---

## 7. Stakeholder conversation guardrails

### Things to say
- One ground station (DOT-VHF) operational at Alaska DOT&PF in Anchorage
- Multi-node deployment proposed and architecturally documented in the paper-lab
- Open source, AGPL-3.0 dual-licensed, reproducible
- Aligned with several recommendations from the Alaska DOT&PF Aviation Gap Analysis
- Not a replacement for FAA-approved sources; supplementary information

### Things NOT to say
- "Alaska is deploying it" without clarifying that one Pi is operational, not statewide
- "Multiple states are interested" unless that has been verifiably established
- "We have partnerships with [vendor]" unless those have been formalized in writing
- "It's working today" for features that are designed but not built (multi-node mesh, mobile app, sensor network, pilot-to-pilot data muling)
- Specific performance numbers (CEP, latency, message-loss) without referencing the data behind them

### If asked about competitors
SkyBridge does not seek to replace ForeFlight, Garmin Pilot, satellite emergency services, or DTN Aviation. It addresses an Alaska-specific coverage gap those products do not solve. Frame existing solutions as complementary.

### If asked for a number you don't have
Say "I don't have that number on hand; it's in the journal / paper-lab / gap analysis and I can pull it." Don't invent.

---

## 8. Source documents (where to find authoritative copies)

- **Washington Post 2014**: archive.org or LexisNexis search for the Lori Montgomery byline.
- **Alaska DOT&PF Aviation Gap Analysis (March 28, 2024)**: Alaska DOT&PF Statewide Aviation Division.
- **NTSB Alaska Aviation Statistics**: annual reports at ntsb.gov.
- **FAA AAAI program documents**: faa.gov/about/office_org/headquarters_offices/avs/offices/afs/afs400/afs410/aaai
- **NWS Anchorage Forecast Office**: weather.gov/aer
- **NASA TM-2015-218427** (TAIGA spec): ntrs.nasa.gov/citations/20150019975

---

## 9. SkyBridge media coverage status

As of May 2026, SkyBridge itself has not received independent media coverage. Project materials reference the 2014 Washington Post coverage of the underlying problems but should not be misread as coverage of SkyBridge specifically.

If SkyBridge does receive independent press coverage in the future, references will be added at the bottom of this section with publication name, date, and URL.

---

