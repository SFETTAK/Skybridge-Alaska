# DOT-VHF Ground Station

Deployed code, service files, and configurations for the SkyBridge Alaska ground station running on a Raspberry Pi 5 in Anchorage, AK.

## Directory Layout

```
scripts/
  vhf-pipeline.py       Core pipeline: SDR IQ -> AM demod -> adaptive squelch -> FLAC -> Whisper STT -> Meshtastic
  aviation_lexicon.py    ATC vocabulary corrections and number normalization (PANC-tuned)
  kneeboard.py           Pilot-facing kneeboard web app (12-layer moving map)
  adsb-combine.py        Merges local readsb + ADSB.fi statewide ADS-B feed
  vhf-review.py          Web UI for browsing archived VHF audio and transcripts
  test-pipeline.py       17-test validation suite (runs without SDR hardware)
  status-dashboard.py    Generates live HTML status page every 30 seconds
  nvme-backup.sh         rclone sync to central backup server

systemd/
  openwebrx.service           WebSDR receiver (port 8073)
  readsb.service              ADS-B 1090 MHz decoder
  dump978-fa.service          UAT 978 MHz decoder
  skyaware978.service         UAT web map JSON writer
  tar1090.service             ADS-B web map + history (local only, port 8504)
  tar1090-combo.service       Combined ADS-B map — local + ADSB.fi (port 8505/8506)
  adsb-combine.service        ADS-B combiner daemon (writes /run/combine1090/)
  kneeboard.service           Pilot kneeboard web app (port 8083, HTTPS 8443)
  status-dashboard.service    Status dashboard generator
  vhf-pipeline.service        VHF transcription pipeline
  nvme-backup.service         Backup oneshot
  nvme-backup.timer           Triggers backup every 6 hours

config/
  openwebrx-settings.json     SDR profiles (3 VHF aviation bands)
  openwebrx-bookmarks.json    19 Anchorage aviation frequencies
  readsb.conf                 ADS-B receiver options
  tar1090.conf                Web map settings (local-only instance)
  tar1090-combo.conf          Combined ADS-B view settings (INTERVAL=8, PTRACKS=8, 978 merge)
  tar1090-combo-config.js     tar1090-combo browser config (centered on ANC, GPS follow, ESRI sat)
  lighttpd-combo.conf         lighttpd vhost for tar1090-combo on port 8505
  lighttpd-combo-ssl.conf     HTTPS for tar1090-combo on port 8506 (GPS requires HTTPS)
  lighttpd-kneeboard-ssl.conf HTTPS proxy for kneeboard on port 8443
  ssh-hardening.conf          SSH security (key-only, no root)
  fail2ban-ssh.conf           Brute-force protection
  logrotate-skybridge.conf    Log rotation
  tmpfiles-dump978.conf       Boot-time runtime directory
```

## Services (13 total)

| Service | Port | Protocol | Purpose |
|---------|------|----------|---------|
| OpenWebRX | 8073 | HTTP | Web SDR spectrum viewer |
| lighttpd | 8080 | HTTP | Status dashboard host |
| vhf-review | 8082 | HTTP | VHF audio/transcript browser |
| kneeboard | 8083 | HTTP | Pilot kneeboard app (internal) |
| kneeboard (SSL) | 8443 | HTTPS | Pilot kneeboard app (GPS-enabled) |
| tar1090 | 8504 | HTTP | ADS-B map (local receiver only) |
| tar1090-combo | 8505 | HTTP | ADS-B map (local + ADSB.fi statewide) |
| tar1090-combo (SSL) | 8506 | HTTPS | ADS-B map combined (GPS-enabled) |
| readsb | 30002-30005 | TCP | ADS-B decoder (Beast/SBS/raw feeds) |
| dump978-fa | 30978 | TCP | UAT decoder |
| vhf-pipeline | -- | -- | VHF transcription (background) |
| adsb-combine | -- | -- | ADS-B feed combiner (background) |
| nvme-backup.timer | -- | -- | rclone sync every 6 hours |

## Port Map

```
Port   Proto  Service                 Access
----   -----  -------                 ------
1235   TCP    rtl_tcp (OpenWebRX)     internal only
8073   HTTP   OpenWebRX               LAN
8080   HTTP   lighttpd (dashboard)    LAN
8082   HTTP   vhf-review              LAN
8083   HTTP   kneeboard (Flask)       internal (proxied via 8443)
8443   HTTPS  kneeboard (lighttpd)    LAN — GPS-enabled
8504   HTTP   tar1090 (local)         LAN
8505   HTTP   tar1090-combo           LAN
8506   HTTPS  tar1090-combo           LAN — GPS-enabled
30001  TCP    readsb raw input        internal
30002  TCP    readsb raw output       internal
30003  TCP    readsb SBS              internal
30004  TCP    readsb Beast input      internal
30005  TCP    readsb Beast output     internal
30978  TCP    dump978-fa JSON         internal
```

## HTTPS / GPS Setup

Browser GPS geolocation (the Geolocation API) requires a secure context (HTTPS or localhost). Since the ground station serves over plain HTTP on the LAN, self-signed certificates are used to enable GPS features in the kneeboard and tar1090-combo.

- Certificate: `/etc/lighttpd/certs/server.pem` (self-signed)
- Kneeboard: lighttpd reverse-proxies Flask :8083 through HTTPS :8443
- tar1090-combo: lighttpd serves static files directly over HTTPS :8506
- Pilots accept the browser certificate warning once, then GPS tracking works

## Deployment

These files are reference copies from the running DOT-VHF station. On the Pi, scripts live at `~/scripts/`, service files under `/etc/systemd/system/`, and configs in their respective system locations.

See the [Operational Runbook](../docs/project-handoff/05-OPERATIONAL-RUNBOOK.md) for how to manage the live station.
