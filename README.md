# SkyBridge Alaska
## Aviation Safety Mesh Network
*Saving Lives Through Community-Powered Infrastructure*

![Aviation Mesh Network](docs/SDR-Mesh-GeneralAviation.png)

A decentralized, community-operated mesh network providing critical aviation safety data to pilots in Alaska's remote airspace. 

**The problem is real and deadly:** As [The Washington Post](https://www.adn.com/aviation/article/alaska-s-outdated-maps-make-flying-peril-high-tech-fix-gaining-ground/2014/10/15/) and [Anchorage Daily News reported](https://www.adn.com/aviation/article/alaska-s-outdated-maps-make-flying-peril-high-tech-fix-gaining-ground/2014/10/15/), terrain mapping errors of 263+ feet contributed to fatal crashes, with one expert noting *"Mars is better mapped than the state of Alaska."* Traditional government solutions remain stuck in budget gridlock after decades.

**SkyBridge is the breakthrough:** A $50 peer-to-peer solution that bypasses failed government infrastructure.

### The Crisis (Validated by The Washington Post, CDC, NTSB)
- **Alaska pilots are 36x more likely to die** than the average US worker *(CDC)*
- **Terrain maps contain errors up to 263+ feet** - directly contributing to fatal crashes *(Washington Post investigation)*
- **15 "controlled flight into terrain" crashes since 2008** killed 16 people, left 7 seriously injured *(NTSB)*
- **No reliable weather/NOTAM updates** in remote areas covering 80% of Alaska
- **Government mapping solutions stuck in budget gridlock** - $30M needed just to finish Alaska *(Washington Post)*
- **"Mars is better mapped than the state of Alaska"** - Steve Colligan, E-Terra Aviation Safety

### What is SkyBridge?
SkyBridge is a **$50 radio system** that creates a peer-to-peer mesh network using [Meshtastic](https://meshtastic.org) technology. Think of it as "walkie-talkies for pilots" that automatically relay messages between aircraft and ground stations - **even when you're out of cell phone and satellite coverage**.

**Key capabilities:**

🌤️ **State-curated weather updates** - Alaska DOT&PF provides reliable, official weather data  
✈️ **ADS-B traffic on your phone** - See nearby aircraft even without cellular or satellite  
📡 **VHF radio transcription** - Base stations convert radio chatter to text and share across network  
🚨 **Automatic crash detection** - iPhone-like fall detection publishes emergency location  
⛰️ **Real-time pilot reports** - Turbulence, icing, and visibility from pilots ahead of you  

**Why pilots want this:** Get critical safety information when cell towers and satellites can't reach you. **No monthly fees, no subscriptions** - just a one-time $50 radio purchase.

### Technology Stack
- **Hardware**: Affordable LoRa radios available from multiple vendors
- **Protocol**: NASA TAIGA ASN.1 for efficient data compression  
- **Network**: [Meshtastic](https://meshtastic.org) open-source mesh networking
- **Interface**: Mobile app for iOS and Android

### Project Status
✅ **Operational System** - Working Meshtastic devices and aviation app deployed  
🚧 **Active Expansion** - Alaska DOT&PF pilot program scaling statewide  
📋 **Three Provisional Patents Filed** - Protecting core mesh, collision avoidance, and emergency triangulation innovations  
🤝 **Industry Partnerships** - Active collaboration with Meshtastic and Rokland Technologies  
🌐 **Multi-State Interest** - Ready for coordinated deployment nationwide  
⚖️ **IP Protected** - State-owned patents enable confident commercial partnerships  

![Network Topology](docs/network.jpg)

**This is not just a concept - we have working prototypes.** We have [Meshtastic](https://meshtastic.org) radios deployed and tested, with pilots successfully exchanging text messages and status updates across the mesh network. The full aviation data integration is in active development.

### License Options

**Dual licensing supports both public safety and commercial innovation:**

#### **Free Use (AGPL-3.0)**
- ✅ **State and federal agencies** (Alaska DOT&PF, FAA, NOAA, NWS)
- ✅ **Search and rescue organizations** 
- ✅ **Educational institutions**
- ✅ **Small Part 135 operators** (fewer than 5 aircraft)
- ✅ **501(c)(3) nonprofits** serving Alaska aviation

#### **Commercial License Required**
- Part 135 operators with 5+ aircraft
- Part 121 air carriers  
- Oil & gas, mining, tourism companies
- Out-of-state commercial operators
- Technology companies creating derivative products

**Contact**: [commercial@skybridgealaska.net](mailto:commercial@skybridgealaska.net) for licensing inquiries

See [LICENSE.md](LICENSE.md) for complete terms.

### Primary Use Cases - Why Pilots Want This

**🛩️ See Traffic Without Cell Service**  
Your phone shows nearby aircraft using [Meshtastic](https://meshtastic.org) mesh network - even in remote areas where ADS-B ground stations don't reach.

**📻 Hear Radio Traffic as Text**  
Base stations transcribe VHF radio chatter and share it across the [Meshtastic](https://meshtastic.org) network, so you can "hear" what's happening even when you're out of VHF range.

**🌤️ Get Reliable Weather**  
Alaska DOT&PF curates official NOAA weather data and pushes it through the mesh - no more relying on outdated or commercial weather services.

**🚨 Automatic Emergency Alerts**  
iPhone-like crash detection automatically broadcasts your location and emergency status across the network if something goes wrong.

**💰 No Monthly Fees**  
One-time $50 radio purchase, no subscriptions, no satellite fees. The [Meshtastic](https://meshtastic.org) network is community-owned and operated.

### For State Aviation Officials

**Why SkyBridge Matters to Your State:**
- 🏔️ **Rural aviation challenges aren't unique to Alaska** - Montana, Idaho, Wyoming, Colorado face similar terrain and weather risks
- 💰 **$50 nodes vs $200K ground stations** - Economically viable for rural airports and pilot communities  
- 🏛️ **State control, not federal dependency** - Community-operated infrastructure under state oversight
- 📈 **Revenue potential** - Commercial licensing funds ongoing development while keeping core system open source
- 🤝 **Interstate cooperation** - Shared development costs, shared safety benefits

**Advanced Integration Capabilities:**
- **CANBUS/OBD2/Aero-CAN integration** - Connect to aircraft systems for automated reporting
- **VHF/ADS-B/SDR combination radios** - Partnership with [Rokland Technologies](https://rokland.com) for integrated solutions
- **RWIS highway weather support** - Integration with road weather information systems
- **Volcanic and seismic monitoring** - Support for UAF Volcanic Institute and USGS networks

**Ready for Multi-State Pilot Program**
- Working [Meshtastic](https://meshtastic.org) prototypes deployed and tested
- NASA TAIGA protocol integration proven
- Alaska DOT&PF partnership established
- Seeking 3-5 additional states for coordinated deployment

### Technical Resources
- 📋 **[Technical Architecture](ARCHITECTURE.md)** - Complete system specifications
- 🔬 **[NASA TAIGA Protocol](https://aviationsystems.arc.nasa.gov/publications/2015/NASA-TM-2015-218427.pdf)** - Official ASN.1 specification
- ⚙️ **[Hardware Specifications](hardware/SPECIFICATIONS.md)** - Component requirements and costs
- 📱 **[Use Cases](USE_CASES.md)** - Real-world application scenarios
- 📊 **[Alaska Aviation Gap Analysis](docs/alaska_aviation_gap_analysis_summary.md)** - Official state study validating SkyBridge's mission
- 📰 **[Media Coverage](docs/media_coverage.md)** - Washington Post investigation and key statistics
- 🔍 **[Existing Solutions Analysis](docs/existing_solutions_analysis.md)** - Why current satellite solutions prove SkyBridge's value
- 📄 **[Technical Whitepaper Summary](docs/technical_whitepaper_summary.md)** - Complete system capabilities and competitive analysis
- 📋 **[Complete Technical Whitepaper](docs/complete_technical_whitepaper.md)** - Full project documentation and deployment strategy
- ⚖️ **[Intellectual Property Overview](docs/intellectual_property_overview.md)** - Patent portfolio protecting core innovations
- 🤝 **[Industry Partnerships](docs/industry_partnerships.md)** - Active collaboration with Meshtastic and Rokland Technologies
- 🌐 **[IoT Sensor Integration](docs/iot_sensor_integration.md)** - Expanding safety through distributed environmental monitoring
- 🎯 **[NASAO Elevator Pitch](docs/elevator_pitch.md)** - Presentation materials for state officials

### Contact
- **Technical Lead**: Steven Fett, Alaska DOT&PF - [steven.fett@alaska.gov](mailto:steven.fett@alaska.gov)
- **Engineering**: Ryan Marlow, Alaska DOT&PF - [ryan.marlow@alaska.gov](mailto:ryan.marlow@alaska.gov)
- **Project**: https://skybridgealaska.net
- **Repository**: https://github.com/SFETTAK/Skybridge-Alaska

---
*"Alaska pilots are dying at 36 times the national rate because they can't get weather updates in remote areas. We're fixing this with $50 mesh radios that let pilots share critical safety data peer-to-peer. No satellites, no subscriptions, just pilots helping pilots."*
