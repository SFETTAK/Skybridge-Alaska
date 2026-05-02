# Glossary

> Acronyms and terms used throughout the SkyBridge Paper Lab. Add as new vocabulary appears.

## A

**AAAI** — Alaska Aviation Weather Improvement Initiative (FAA-led). The interagency working group focused on closing AK weather coverage gaps.

**ACARS** — Aircraft Communications Addressing and Reporting System. Aviation messaging protocol over VHF/HF/satellite. Reference for some of our QoS thinking.

**ADS-B** — Automatic Dependent Surveillance-Broadcast. Aircraft self-report position/altitude on 1090 MHz (international) and 978 MHz (US UAT). SkyBridge ingests ADS-B for aircraft tracking.

**AIMD** — Additive Increase, Multiplicative Decrease. The congestion-control algorithm pattern used by TCP and SkyBridge for per-link send-rate adjustment.

**AIRMET** — Airmen's Meteorological Information. FAA-issued moderate-severity weather alert. Short for Tango (turbulence) / Sierra (IFR conditions) / Zulu (icing).

**Anchor** — A specific point on the map that has a weather observation source (METAR station, MWOS sensor, etc.). The unit at which SkyBridge tracks data.

**ASN.1** — Abstract Syntax Notation One. ITU-T X.680 specification language for structured data. TAIGA uses ASN.1 for wire encoding.

**ASOS** — Automated Surface Observing System. FAA-funded weather station network. The "official" observation source for METARs.

**AWOS** — Automated Weather Observing System. Similar to ASOS but typically lower-tier; widely deployed at smaller airports.

## B

**Backhaul** — The transport that connects regional mesh networks to the central data hub (Dell server). Cellular, wifi, satellite, edge-terminated tunnel.

**Bowl** — Anchorage Bowl. The valley containing PALH/PANC/PAMR/PAED, surrounded by Chugach + Talkeetna mountains.

## C

**CBOR** — Concise Binary Object Representation. RFC 8949. A schema-light alternative to ASN.1; used in some subsections.

**CoDel** — Controlled Delay (queue management). A bufferbloat-mitigation algorithm; reference for our queue management.

**CWA** — Center Weather Advisory. Short-fuse 1-2 hour weather hazard alert from ARTCCs.

## D

**DOT&PF** — Alaska Department of Transportation and Public Facilities. The state agency operating SkyBridge in Phase 1.

**DTN** — Delay Tolerant Networking. RFC 5050 Bundle Protocol. The pattern for opportunistic store-and-forward (e.g., data-mule).

## E

**ECMWF** — European Centre for Medium-Range Weather Forecasts. Producer of the IFS forecast model widely regarded as the world's best general-purpose forecast model.

**Ensemble** — A collection of forecast model outputs, usually combined for improved skill (the "wisdom of the crowd" approach to weather forecasting).

**eSRS** — Enhanced Special Reporting Service. Alaska's existing aviation operations dispatch system.

## F

**FIS-B** — Flight Information Services-Broadcast. Free aviation data on 978 MHz UAT (in US). Includes graphical METARs, NEXRAD, AIRMETs.

**FL** — Flight Level. Altitude in hundreds of feet, e.g. FL360 = 36,000 ft pressure altitude.

## G

**GA** — General Aviation. Pilots flying for personal/commercial reasons not in scheduled airline service.

**GEM** — Global Environmental Multiscale model. Environment Canada's global forecast model.

**Geohash** — A base32 encoding mapping lat/lon to a string. TAIGA uses geohash for compact location encoding.

**GFS** — Global Forecast System. NOAA NCEP's primary global forecast model.

## H

**HRRR** — High-Resolution Rapid Refresh. NOAA's 3 km high-resolution forecast model. HRRR-AK is the Alaska variant.

## I

**ICAO** — International Civil Aviation Organization. Source of the 4-letter airport codes (PALH, PANC, etc.) and standardized weather coding.

**IDW** — Inverse Distance Weighting. Spatial interpolation algorithm; used in SkyBridge to estimate wind/temp at any lat/lon from nearby observations.

**IFR** — Instrument Flight Rules. Operations under instrument-meteorological-conditions; requires instrument rating + filed flight plan.

## J

**JMA** — Japan Meteorological Agency. Producer of a global forecast model SkyBridge ingests for side-by-side display.

## L

**LoRa** — Long Range. The radio modulation used by Meshtastic. Sub-GHz, low-power, long-range, low-bandwidth.

**LoRaWAN** — Long Range Wide Area Network. The LoRa Alliance's protocol stack atop LoRa. Reference architecture, not what SkyBridge uses (we use Meshtastic).

## M

**MAE** — Mean Absolute Error. Statistical measure of prediction accuracy used in our continuous accuracy attestation.

**Mesh** — A peer-to-peer network where every node can route for every other node. SkyBridge uses Meshtastic for regional mesh.

**Meshtastic** — Open-source LoRa mesh networking firmware. SkyBridge is layered on top of stock Meshtastic; we don't fork the firmware.

**METAR** — Meteorological Aerodrome Report. Hourly aviation weather observation, ICAO-coded.

**MQTT** — MQ Telemetry Transport. Pub/sub messaging protocol used by SkyBridge for backhaul transport.

**MWOS** — (Montis Corp) Mountain Weather Observation System. Calibrated private weather stations, ~14 deployed across Alaska.

## N

**NCEP** — National Centers for Environmental Prediction. NOAA division producing operational forecast models.

**NEXRAD** — Next-Generation Radar. NOAA's weather radar network.

**NOAA** — National Oceanic and Atmospheric Administration. US federal weather agency.

**NOTAM** — Notice to Airmen. Aviation alerts (e.g., runway closures).

**NWS** — National Weather Service. Operational division of NOAA.

## P

**PIREP** — Pilot Weather Report. Pilot-submitted in-flight weather observation. Coded as `UA /OV ANC270031 /TM 1342 /...`.

**Polygon** — Geographic area definition. Used in SIGMETs, AIRMETs, TFRs.

## Q

**QoS** — Quality of Service. Differential treatment of messages by priority. SkyBridge defines 5 priority classes.

**Quantization** — Reducing precision to compact representation (e.g., wind direction in degrees stored as 9-bit integer instead of float64).

## R

**Region** — A geographic area handled by a single mesh cluster (e.g., Anchorage Bowl, Mat-Su, Bristol Bay).

**RTMA** — Real-Time Mesoscale Analysis. NOAA's hourly current-state weather analysis (vs forecast).

## S

**SIGMET** — Significant Meteorological Information. FAA-issued severe weather alert (icing, turbulence, volcanic ash).

**SkyBridge envelope** — The 10-byte header SkyBridge wraps around every TAIGA payload (priority, msg_id, ttl).

**SPECI** — Special METAR. Off-cycle issuance when conditions change rapidly. FAA's 20-minute trigger.

## T

**TAF** — Terminal Aerodrome Forecast. Airport-specific 24-hour forecast.

**TAIGA** — Traffic and Atmospheric Information for General Aviation. NASA-published protocol (TM-2015-218427) for aviation weather data encoding.

**TFR** — Temporary Flight Restriction. FAA-issued airspace closure (firefighting, security, presidential movement).

**TTL** — Time To Live. Seconds until a message becomes ineligible to forward.

## U

**UAT** — Universal Access Transceiver. The US 978 MHz ADS-B band; carries FIS-B uplink.

## V

**VAA** — Volcanic Ash Advisory. NWS-issued SIGMET for volcanic ash hazards.

**VFR** — Visual Flight Rules. Operations under visual meteorological conditions; pilot navigates by sight.

**Volatility** — Rate of change in observed weather, used to trigger faster refresh cadence and to flag values that are changing fast in the kneeboard display.

**VOR** — VHF Omnidirectional Range. Aviation navigation aid; stations broadcast direction/distance information.

## W

**WGS84** — World Geodetic System 1984. Standard global coordinate reference frame; used by GPS and SkyBridge.

**Williwaw** — Sudden, violent downslope wind events common in coastal Alaska. Develops in <10 minutes; gusts 60-80+ kt.

## X

(none yet)

## Y

(none yet)

## Z

(none yet)
