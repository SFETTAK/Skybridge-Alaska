# DOT-VHF Ground Station

Deployed code, service files, and configurations for the SkyBridge Alaska ground station running on a Raspberry Pi 5 in Anchorage.

## Directory Layout

```
scripts/
  vhf-pipeline.py       Core pipeline: SDR IQ -> AM demod -> VAD -> FLAC -> Whisper STT -> Meshtastic
  test-pipeline.py      17-test validation suite (runs without SDR hardware)
  status-dashboard.py   Generates live HTML status page every 30 seconds
  nvme-backup.sh        rclone sync to central backup server

systemd/
  openwebrx.service     WebSDR receiver (port 8073)
  readsb.service        ADS-B 1090 MHz decoder
  dump978-fa.service    UAT 978 MHz decoder
  skyaware978.service   UAT web map JSON writer
  tar1090.service       ADS-B web map + history
  status-dashboard.service
  vhf-pipeline.service  VHF transcription pipeline
  nvme-backup.service   Backup oneshot
  nvme-backup.timer     Triggers backup every 6 hours

config/
  openwebrx-settings.json    SDR profiles (3 VHF aviation bands)
  openwebrx-bookmarks.json   19 Anchorage aviation frequencies
  readsb.conf                ADS-B receiver options
  tar1090.conf               Web map settings
  ssh-hardening.conf         SSH security (key-only, no root)
  fail2ban-ssh.conf          Brute-force protection
  logrotate-skybridge.conf   Log rotation
  tmpfiles-dump978.conf      Boot-time runtime directory
```

## Deployment

These files are reference copies from the running DOT-VHF station. On the Pi, scripts live at `~/scripts/`, service files under `/etc/systemd/system/`, and configs in their respective system locations.

See the [Operational Runbook](../docs/project-handoff/05-OPERATIONAL-RUNBOOK.md) for how to manage the live station.
