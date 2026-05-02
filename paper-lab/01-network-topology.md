# 01 — Network Topology

> The single most important architectural decision in SkyBridge. Read this section carefully; everything else depends on it.

## Summary

SkyBridge is a hybrid network, not a mesh network with internet on the side. The internet (cellular, wifi, edge-terminated tunnels, Starlink, Iridium) is the spine that connects regions to the central hub. LoRa mesh is the last-mile distribution within a region, and the fallback when the spine is cut. Mesh is not the primary delivery channel; it is the safety net.

Concretely: about 8 regional clusters across Alaska, each ~30 to 50 nm in radius. Within a cluster, LoRa mesh covers the last mile to pilot tablets. Between clusters, traditional internet carries data to and from a central hub. Pilots roam between regions, picking up local data on arrival.

This architecture deliberately rejects two simpler models:

- **"One big mesh covers Alaska"** is physically impossible. Cold Bay to Barrow over LoRa would require >100 hops with compounding packet loss, and Meshtastic's flood-routing would saturate the channel. The math does not work at any bandwidth.
- **"Pure cloud, no mesh"** fails the actual problem. Cellular drops out across 80% of the state. Without the mesh, when the spine cuts, pilots see nothing.

The architecture is hybrid because the operating environment is hybrid. Most of the time, most pilots are on cellular and seeing the FULL data tier. Some of the time, in remote terrain or after backhaul failure, they fall back to MESH or CACHED. The dashboard renders the same shape at every tier; it just renders less as bandwidth thins.

## The regional model

```
[FIGURE: topology.svg — to render]
Geographic clusters drawn over an Alaska base map. Each cluster outlined as
a polygon; cluster members shown as nodes; LoRa radio range circles drawn
around each node. Dashed lines indicate inter-cluster backhaul (internet,
cellular, satellite). One pilot trajectory shown traversing 3 clusters
sequentially with timestamps for entry/exit and data picked up at each.
```

| Region | Anchor stations | Notes |
|---|---|---|
| Anchorage Bowl | PALH, PANC, PAMR, PAED, PAAQ, MWOS:Lake Hood, MWOS:Merrill ×2 | Densest cluster; Pi at PALH is currently the lead node |
| Mat-Su | PATK, PAAQ, surroundings | Bridges Anchorage to Interior; mountain shadows complicate LoRa |
| Kenai Peninsula | PAEN, PAHO, MWOS:Port Graham, MWOS:Whittier | Coastal terrain; sea-breeze cycles make weather unique |
| Bristol Bay | PADQ, PAKN, PAIL, PADM | Salmon fishery and Aleutian gateway; high traffic in summer |
| Aleutians | PADU, PASN, PAEH | Maritime / island chain; mostly satellite backhaul |
| Interior (Tanana) | PAFA, PAEI, MWOS:Fairbanks Intl | Continental climate; ice fog dominates winter |
| Yukon River | PAGA, PAUN, MWOS:Rampart, MWOS:Anaktuvuk | Sparse coverage; data-mule scenarios most relevant |
| Arctic Coast | PABR, MWOS:Atqasuk, MWOS:Wainwright, MWOS:Nuiqsut, MWOS:Kaktovik | Dark winter; oil-and-gas operational support |
| Southeast | PAJN, PAWG, PAYA, PAKT | Coastal rain forest; weather closes airports for days |

## Why regional, not statewide

The argument from network physics:

- LoRa node-to-node range is ~5 to 15 km depending on terrain at typical ground/tower heights, and often <2 km at ground level in mountains. (Air-to-ground range from a pilot's aircraft at altitude can be substantially longer because line of sight improves with altitude; tests of Meshtastic at altitude have shown 50+ mile contacts in clear conditions.) Even with the altitude bonus, a statewide mesh from Cold Bay to Barrow would require many hops with packet loss compounding at each hop.
- Meshtastic's flood-routing broadcasts every packet to every node within range. Each hop adds traffic everywhere. A 100-node mesh with 20 packets/min/node is 24,000 transmissions/min. That saturates the channel within seconds.
- Pareto-distributed information demand. Pilots flying out of PALH care almost exclusively about Anchorage Bowl plus their planned route. They do not need real-time observations from Bristol Bay unless they are flying there. Federated regions match this demand pattern.

The argument from social topology:

- Pilots fly within regions more than across them. A bush pilot based at PAEN flies the Kenai/Cook Inlet circuit; a Bristol Bay fisherman flies that bay. Daily ops are local.
- Cross-region travel uses backhaul. When a pilot flies PALH to PAFA, they have cellular at the start and end, and accept satellite or mesh-mule along the way.
- Operational responsibility maps to region. DOT&PF dispatchers, Part 135 charter operators, and FAA flight service stations operate in defined geographic sectors.

## The five tiers of data availability

What the user sees in the dashboard depends on what bandwidth is currently available:

| Tier | Display label | Connection | Per-snapshot payload | Render |
|---|---|---|---|---|
| FULL | `[ ◐ All Alaska ]` | wifi / LTE / Pi-local | ~50 KB JSON | Statewide, every model and obs, live animations, time-lapse, vector layers |
| REGIONAL | `[ ● 200 nm Local ]` | throttled cellular | ~10 KB | Local 200 nm grid, multi-source weather, simplified streamlines |
| ROUTE | `[ ↗ Along Route ]` | spotty cellular w/ throttle | ~3 KB | Route corridor plus nearest stations plus nearest model points |
| MESH | `[ ⬢ Mesh Only ]` | LoRa / Meshtastic | ~200 bytes | Compact CBOR / TAIGA, station obs only, no models |
| CACHED | `[ ⏸ Cached ]` | offline | 0 bytes new | Last snapshot, age-stamped, "12 min stale" indicator |

The same dashboard renders at every tier. It just renders less as data thins. Tier downshifts automatically based on connection quality. The user can manually downshift for testing but cannot manually upshift past what the connection allows.

## The backhaul layer

Backhaul connects regional mesh clusters to the central data hub:

| Transport | Used by | Latency | Bandwidth | Notes |
|---|---|---|---|---|
| Wifi | Pi nodes in office buildings, hangars | <50 ms | 100+ Mbps | Always preferred when available |
| Cellular (LTE / 5G) | Mobile devices, vehicle-mounted nodes | 30 to 200 ms | 5 to 50 Mbps practical in AK | Coverage drops sharply outside Anchorage / Fairbanks / Juneau |
| Edge-terminated tunnel | Pi nodes behind NAT | 100 to 300 ms | Wifi-limited | Outbound-only; TLS terminates at the edge; no port forwarding required |
| Iridium / Starlink | Remote installations, vessels, aircraft | 100 ms (Starlink), 1500 ms (Iridium) | 1 to 250 Mbps (Starlink) or 1.5 kbps (Iridium) | Expensive; reserved for SAFETY-class data |
| Meshtastic LoRa | Last-mile within region | 100 to 500 ms per hop | 1 to 3 kbps practical | Fallback when no cellular |
| Data-mule (overflight) | Stationary remote nodes | minutes to hours | ~270 KB per overflight | Opportunistic; assumes cellular-equipped pilot passes within LoRa range |

Each region's primary node selects the best available backhaul automatically and fails over without user action. The audit log records every backhaul transition.

## Geofencing and roaming

A device's "current region" is determined by:

1. GPS coordinate mapped to a region polygon (config-defined; we use simplified convex hulls for performance).
2. Mesh node visibility. If the device's Meshtastic radio sees node `PALH-pi-01`, it joins the Anchorage Bowl group.
3. Manual override. The user can pin region in the UI for testing.

Region transitions trigger:

- Inbound (entering region): subscribe to that region's MQTT topics, request a catch-up bundle of recent observations and alerts.
- Outbound (leaving region): unsubscribe, snapshot the current state to local cache for offline use, mark cached data as "from previous region, do not display in current."

Regional mesh clusters do not forward each other's observations over LoRa. Cold Bay's METAR never traverses LoRa to Barrow. It would route via:

```
Cold Bay node → Cold Bay regional Pi → backhaul (cellular/Iridium) → 
Dell hub → backhaul → Barrow regional Pi → Barrow LoRa mesh
```

This keeps mesh traffic regional and bounded. Dell sees the whole state.

## Routing within a region

Within a regional mesh, packets follow Meshtastic's stock routing:

- Up to 7 hops (Meshtastic default; SkyBridge configures to 3 or 4 typical for tighter regions)
- Reliable transmission (with ack) for SAFETY-class messages only
- Broadcast (no ack) for ROUTINE class
- Primary-channel AES-256 encryption (key shared per region)

The regional Pi (or Dell, when bridging) handles aggregation:

- Listens on Meshtastic for incoming observations (e.g., a pilot reporting wind).
- De-duplicates by `msg_id`.
- Validates signature (if signed).
- Stores in regional SQLite.
- Republishes to MQTT (so subscribers get it).
- Forwards to Dell over backhaul.

## The data-mule pattern

The hard case: a stationary node (cabin, remote weather station) with no cellular and no satellite, only a Meshtastic radio and intermittent overflights.

Mechanism:

1. The node beacons. The stationary node periodically transmits a `HELLO` packet announcing its presence and last-known-data freshness.
2. Aircraft hears beacon. The overflying pilot's Meshtastic radio (or Pi-equipped phone) receives the HELLO.
3. Aircraft replies. The pilot's device sends recent ROUTINE and CRITICAL data for that region (whatever it cached when last on cellular).
4. Node receives and acks. The stationary node updates its local cache, marks data as fresh.
5. Aircraft passes out of range. Contact ends; no further exchange until next overflight.

Worked example with timing math is in [`06-data-mule-scenarios.md`](06-data-mule-scenarios.md).

## Network ownership boundaries

| Layer | Ownership | Maintained by |
|---|---|---|
| Sensor (anchor) hardware | Site owner (FAA / Montis / DOT&PF / private) | Hardware vendor and operator |
| Regional Pi | Site host (DOT, hangar operator, individual) | SkyBridge community |
| Mesh radios | Pilot / individual | Pilot |
| Backhaul | ISP / Iridium / Starlink / cellular carrier | Carrier |
| Central Dell hub | Alaska DOT&PF (DOTHQ) | DOT&PF or successor Alaska aviation consortium |
| Public dashboard | published at the project domain via edge-terminated tunnel | SkyBridge maintainers |

No single layer is single-source-of-failure. The system degrades step-by-step as components drop out.

## What survives when the hub dies

This is the most important architectural property of SkyBridge. **Regions are independent.** The Dell hub aggregates and synthesizes; it does not gate.

If the central Dell hub goes offline:

- Every regional Pi keeps aggregating its local observations.
- Every pilot tablet in mesh range of a regional Pi keeps receiving the regional dashboard.
- VHF transcription, ADS-B traffic, MWOS observations, regional METARs continue to flow.
- What pauses: cross-region synthesis. A pilot in Anchorage cannot pull the latest Bristol Bay observation while the hub is down. They get whatever was cached at their regional Pi before the hub dropped.

If a regional Pi goes offline:

- Mesh radios continue to route peer-to-peer for ROUTINE messages within the region.
- Other regions are unaffected.
- The Pi's 24-hour cache is unavailable until it returns; mesh-only nodes hold their last good cached data and age-stamp it.

If the entire backhaul cuts (no internet, no cellular, no satellite, statewide):

- Each region runs as an island. Pilots get whatever their regional Pi has.
- Mesh-relayed ROUTINE traffic continues within each region.
- The system gracefully degrades to "local observations only" rather than failing entirely.

This is not a contingency plan to be implemented later. This is the steady-state architecture as designed; with one site live today, regional independence is a design property of the system that becomes visible once additional regional Pis are deployed. Every region owns its own data flow; the hub only adds cross-region synthesis on top.

## Specific failure modes and what the user sees

What happens when:

- Cellular drops. Backhaul fails over to satellite or LoRa-mesh-mule. UI tier downshifts to ROUTE or MESH.
- Regional Pi goes offline. Meshtastic mesh continues to route peer-to-peer; regional aggregation pauses; data accumulates in node-local caches and pushes when Pi returns.
- Dell hub goes offline. Pi nodes operate independently per region. Statewide synthesis pauses but regional dashboards still work.
- Edge-tunnel drops. The public dashboard becomes unreachable; internal VPN-overlay access still works for SkyBridge ops folks.
- Internet goes out across the state (mainline outage). Every region runs independently. Mesh traffic continues. Cross-region data flow ceases.
- All four major models go down (NWS, GFS, GEM, ECMWF, JMA all offline simultaneously). Extremely improbable, but if it happens, the kneeboard renders only the available observations and clearly labels the missing model layers.

Each failure mode has an audit-log entry and a UI indicator chip ("backhaul: cellular degraded", "Dell hub: offline 12 min").

## Open questions

- Region polygon definition. Convex hull of anchor stations vs hand-drawn natural boundaries (mountain passes, watershed divides). The hand-drawn version maps better to operational reality but requires periodic revision.
- Inter-region edge cases. Pilots flying along regional boundaries may toggle between regions repeatedly. Hysteresis (don't switch until N km past boundary) helps but is not yet locked in.
- Mesh node identity over a pilot's career. If a pilot's phone has a stable Meshtastic ID, can we use it as a per-pilot reputation marker for SAFETY messages? A SIGMET signed by `pilot-123` who has reported accurately for years would carry more weight than an unsigned random message.

These remain open as of v0.1 of this document and are revisited as deployment data accumulates.
