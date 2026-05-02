# 10 — References

> Citations for SkyBridge Paper Lab. Maintained inline as the lab evolves; new citations added as they are referenced.

## Aviation weather and policy

- **NASA TM-2015-218427** — Rios, J. L. *A Formal Messaging Notation for Alaskan Aviation Data*. NASA Ames Research Center, 2015. The original TAIGA protocol specification. Public domain. <https://ntrs.nasa.gov/citations/20150019975>

- **FAA Order 6560.20** — *Siting Criteria for Automated Surface Observing Systems (ASOS)*. Federal Aviation Administration. Defines representative-area requirements for ASOS placement.

- **FAA Advisory Circular 150/5340-30** — *Airport Marking, Lighting, Signs, and Surface Friction Treatment*. Includes wind-sensor placement guidance.

- **NTSB Aviation Statistics** — *Alaska Aviation Safety Report* (annual). Source of FAA AWOS reliability data.

- **FAA AAAI** — *Alaska Aviation Weather Improvement Initiative*. <https://www.faa.gov/about/office_org/headquarters_offices/avs/offices/afs/afs400/afs410/aaai>

- **AK DOT&PF Gap Analysis** (March 28, 2024) — *Alaska Aviation Weather Coverage Gap Analysis*. Internal document; obtain copy from Alaska DOT&PF Statewide Aviation Division.

- **NWS Anchorage Forecast Office** — operational coordination point for SIGMET / VAA / TAF authorities. <https://www.weather.gov/aer>

## Protocols and encoding

- **ASN.1** — *Abstract Syntax Notation One*. ITU-T X.680 series. Used by TAIGA for wire format.

- **CBOR** — *Concise Binary Object Representation*. RFC 8949. Schema-light alternative to ASN.1.

- **Protocol Buffers** — Google's binary encoding. Reference for forward-compatible schema evolution patterns.

- **Geohash** — Niemeyer, G. "Geohash" (2008). <http://geohash.org/>

## Network protocols

- **Meshtastic** — Open-source LoRa mesh networking firmware. <https://meshtastic.org/>

- **LoRaWAN 1.1 specification** — LoRa Alliance. <https://lora-alliance.org/wp-content/uploads/2020/11/lorawantm_specification_-v1.1.pdf>

- **MQTT 5.0** — *Message Queuing Telemetry Transport*. OASIS standard. <https://docs.oasis-open.org/mqtt/mqtt/v5.0/>

- **DTN Bundle Protocol** — RFC 5050. Delay-Tolerant Networking. NASA's protocol for satellite/intermittent links.

- **TCP CUBIC** — RFC 8312. Congestion control algorithm; reference for our AIMD design.

- **CoDel** — Nichols, K., Jacobson, V. *Controlling Queue Delay*. ACM Queue, 2012. <https://queue.acm.org/detail.cfm?id=2209336>

## Weather data sources

- **aviationweather.gov API** — NWS aviation weather data including METARs, TAFs, SIGMETs, AIRMETs, PIREPs. <https://aviationweather.gov/data/api>

- **api.weather.gov** — NWS general public API; includes gridded forecast service. <https://www.weather.gov/documentation/services-web-api>

- **NOAA NCEP HRRR / GFS** — Operational global and regional forecast models. <https://nomads.ncep.noaa.gov/>

- **ECMWF IFS** — European Centre for Medium-Range Weather Forecasts integrated forecast system. Available via Open-Meteo. <https://www.ecmwf.int/>

- **Environment Canada GEM** — Canadian global environmental model. Available via Open-Meteo.

- **Japan Meteorological Agency** — Global Spectral Model. Available via Open-Meteo.

- **Open-Meteo** — Free aggregator providing model data without API key. <https://open-meteo.com/>

- **Synoptic Data** — Aggregator of MesoNet networks (SNOTEL, RAWS, AK DEC). <https://synopticdata.com/>

- **NOAA RTMA** — Real-Time Mesoscale Analysis. <https://www.weather.gov/mdl/rtma>

- **NOAA Volcanic Ash Advisory Center (Anchorage)** — Issues VAA SIGMETs. <https://www.weather.gov/aawu/>

- **Montis Corp MWOS** — Calibrated private weather network in Alaska. <https://montiscorp.com/>

## Hardware

- **Semtech SX1262** — LoRa radio chip. Datasheet: <https://www.semtech.com/products/wireless-rf/lora-connect/sx1262>

- **Heltec V3 LoRa boards** — Reference Meshtastic-compatible hardware. <https://heltec.org/>

- **Raspberry Pi 5** — Current SkyBridge ground-station platform. <https://www.raspberrypi.com/products/raspberry-pi-5/>

- **RTL-SDR** — Software-defined radio dongles. <https://www.rtl-sdr.com/>

## Comparable solutions (existing)

- **ForeFlight** — Commercial pilot weather + flight planning app. <https://foreflight.com/> ($300/yr)

- **Garmin Pilot** — Commercial pilot app, integrated with Garmin avionics. <https://www.garmin.com/en-US/c/aviation/pilot-apps-services/>

- **FltPlan Go** — Free pilot app from FlightAware. Uses FAA-published data.

- **AviPlan** — European flight planning + weather integration.

- **DTN Aviation** — Subscription weather/data product. <https://www.dtn.com/aviation/>

- **eSRS** — Alaska's Enhanced Special Reporting Service. State-operated; serves dispatch operations.

## Delay-tolerant networking and data mules

- **DakNet** — Pentland, A., Fletcher, R., Hasson, A. *DakNet: Rethinking Connectivity in Developing Nations*. IEEE Computer, 2004. The original published "data mule" deployment.

- **Karuturi, K. et al.** — *Data MULEs: Modeling and Analysis of a Three-tier Architecture for Sparse Sensor Networks*. Ad Hoc Networks Journal, 2003.

- **Demmer, M. et al.** — *Implementing Delay Tolerant Networking*. UC Berkeley, 2004.

- **NASA SCaN program** — Space Communications and Navigation. Deep-space DTN deployments.

## Cryptographic primitives (for future v2)

- **Ed25519** — RFC 8032. EdDSA signature scheme. Reference for future per-message signing.

- **ChaCha20-Poly1305** — RFC 8439. AEAD cipher; more efficient than AES-GCM on ARM Cortex-M.

- **Argon2id** — RFC 9106. Password hashing for modern auth gateways.

## Aviation domain references

- **ICAO Annex 3** — *Meteorological Service for International Air Navigation*. Defines METAR / TAF / SIGMET codes.

- **NOAA ARC-GIS Aviation Weather** — operational charts. <https://aviationweather.gov/>

- **Garmin G1000** — Reference avionics; relevant for future CAN-bus / ARINC-429 ingestion design.

## Software libraries

- **asn1tools** — Python ASN.1 encoding/decoding. <https://github.com/eerimoq/asn1tools>

- **simpy** — Python discrete-event simulation framework. <https://simpy.readthedocs.io/>

- **Caddy** — HTTP server with automatic HTTPS. <https://caddyserver.com/>

- **Auth gateways** — Modern reverse-proxy forward-auth pattern. SkyBridge's deployment uses commodity auth-gateway software; specific vendor selection is per-deployment.

- **VPN overlay** — WireGuard-based managed mesh VPN for operator access. SkyBridge's deployment uses a commodity overlay; specific provider is per-deployment.

- **Edge-terminated tunnels** — Outbound-only tunneling pattern that puts TLS termination at a CDN/edge provider rather than at the station, eliminating the need for inbound port forwarding. SkyBridge's deployment uses a commodity provider; specific provider is per-deployment.

- **Mosquitto** — MQTT broker. <https://mosquitto.org/>

- **paho-mqtt** — Python MQTT client.

- **Cesium** — Open-source 3D geospatial visualization framework. <https://cesium.com/cesiumjs/>

- **Leaflet** — Open-source 2D map library. <https://leafletjs.com/>

- **Open-Meteo** — Free model aggregator API. <https://open-meteo.com/>

## Datasets and reports

- **Washington Post 2014 series** — *"Death in the Skies: Why Pilots in Alaska Die at 36 Times the Rate of US Workers."* The 36× statistic origin.

- **AK DOT&PF Aviation Statistics** — annual reports. <https://dot.alaska.gov/aviation/>

- **NWS NOMADS** — gridded forecast data archive. <https://nomads.ncep.noaa.gov/>

## Where to find what

| Topic | Best starting point |
|---|---|
| TAIGA protocol | `protocol/TAIGA_PROTOCOL.md` + the NASA TM |
| Meshtastic specifics | meshtastic.org docs |
| FAA AWOS standards | FAA Order 6560.20 |
| Alaska-specific aviation weather | NWS Anchorage Forecast Office + DOT&PF Aviation |
| Compression algorithm comparison | Section 03 of this Paper Lab |
| Worked deployment scenarios | Section 06 of this Paper Lab |
| Cert path | Section 08 of this Paper Lab |

## Reference policy

This lab assumes a reader can chase any reference. Where a citation is broken, ambiguous, or stale, an issue should be opened in the repository. The lab is a research artifact; correctness of citations is part of the deliverable.

Citations added to this list as new sections reference them.
