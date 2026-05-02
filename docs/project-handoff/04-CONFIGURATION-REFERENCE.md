# Configuration Reference — DOT-VHF Ground Station

**Project:** SkyBridge Alaska
**Station ID:** DOT-VHF
**Date:** 2026-03-12

---

## 1. Network

| Parameter | Value |
|-----------|-------|
| Hostname | DOT-VHF |
| Ethernet (eth0) | <station-host>/24 (LAN) (DHCP) |
| WiFi (wlan0) | <station-host-alt>/24 (LAN) (DHCP) |
| DNS | Via DHCP / NetworkManager |

## 2. OpenWebRX

**Config location:** `/var/lib/openwebrx/settings.json`

| Parameter | Value |
|-----------|-------|
| Station Name | DOT-VHF SkyBridge Alaska |
| Location | Anchorage, Alaska |
| Coordinates | 61.2181N, -149.9003W |
| Elevation | 40m ASL |
| SDR Device | RTL-SDR Blog V4 (serial: BLOGV4) |
| Sample Rate | 2,400,000 Hz |
| rtl_tcp Port | 1235 (127.0.0.1) |
| Web Port | 8073 |

### SDR Profiles

| Profile | Center Freq | Band | Modulation | RF Gain |
|---------|------------|------|------------|---------|
| VHF Air Low | 119.05 MHz | 117.9-120.2 MHz | AM | 29 |
| VHF Air Mid | 121.80 MHz | 120.6-123.0 MHz | AM | 29 |
| VHF Air High | 127.00 MHz | 125.8-128.2 MHz | AM | 29 |

### Aviation Bookmarks (19 frequencies)

**File:** `/var/lib/openwebrx/bookmarks.json` (also `~/openwebrx-anchorage-bookmarks.json`)

| Frequency | Label | Modulation |
|-----------|-------|------------|
| 114.300 MHz | PANC VOR/DME | AM |
| 118.600 MHz | PANC Tower | AM |
| 119.050 MHz | Merrill Tower | AM |
| 119.100 MHz | Lake Hood Tower | AM |
| 120.400 MHz | Merrill Ground | AM |
| 121.500 MHz | Emergency Guard | AM |
| 121.700 MHz | PANC Ground | AM |
| 121.800 MHz | PANC Unicom | AM |
| 121.900 MHz | PANC Ground 2 | AM |
| 122.200 MHz | FSS Kenai | AM |
| 123.600 MHz | PANC ATIS | AM |
| 124.700 MHz | PANC Approach N | AM |
| 125.200 MHz | PANC Approach S | AM |
| 125.700 MHz | PANC Approach E | AM |
| 126.400 MHz | PANC Departure | AM |
| 127.600 MHz | PANC AWOS | AM |
| 128.200 MHz | PANC Clearance | AM |
| 132.600 MHz | PANC ATIS Dep | AM |
| 135.150 MHz | Approach W | AM |

## 3. VHF Pipeline

**Config location:** `~/scripts/vhf-pipeline.py` (constants at top of file)

**Systemd overrides:** `/etc/systemd/system/vhf-pipeline.service`

| Parameter | Value | Source |
|-----------|-------|--------|
| RTL_TCP_HOST | 127.0.0.1 | hardcoded |
| RTL_TCP_PORT | 1235 | hardcoded |
| CENTER_FREQ_HZ | 121,800,000 (121.8 MHz) | CLI --freq |
| SAMPLE_RATE | 2,400,000 | hardcoded |
| AUDIO_RATE | 24,000 Hz | hardcoded |
| WHISPER_RATE | 16,000 Hz | hardcoded |
| WHISPER_MODEL | tiny.en | env WHISPER_MODEL |
| VAD_THRESHOLD | 0.005 | CLI --vad-threshold |
| VAD_HOLD_S | 1.5 seconds | hardcoded |
| SEGMENT_MAX_S | 30 seconds | hardcoded |
| ARCHIVE_DIR | /mnt/nvme/skybridge/vhf-audio | hardcoded |
| TRANSCRIPT_DIR | /mnt/nvme/skybridge/transcripts | hardcoded |
| LOG_DIR | /mnt/nvme/skybridge/logs | hardcoded |
| MESH_HOST | (empty = serial) | env MESH_HOST |
| MESH_PORT | 4403 | env MESH_PORT |
| MESH_CHANNEL | 0 | env MESH_CHANNEL |

## 4. readsb (ADS-B 1090 MHz)

**Config location:** `/etc/default/readsb`

| Parameter | Value |
|-----------|-------|
| Device | ADSB1090 (rtlsdr, by serial) |
| Gain | auto |
| PPM | 0 |
| Max Range | 450 nmi |
| JSON Write Interval | 1 second |
| Globe History | /mnt/nvme/skybridge/adsb/ |
| State Write Interval | 300 seconds |
| Range Outline | 24 hours |

### Network Ports

| Port | Protocol | Direction |
|------|----------|-----------|
| 30001 | Raw input | listen |
| 30002 | Raw output | listen |
| 30003 | SBS (BaseStation) | listen |
| 30004 | Beast input | listen |
| 30005 | Beast output | listen |
| 30104 | Beast input (alt) | listen |

## 5. dump978 (UAT 978 MHz)

**Config location:** `/etc/systemd/system/dump978-fa.service`

| Parameter | Value |
|-----------|-------|
| SDR Driver | rtlsdr |
| Device | device=1 (should be serial=UAT978 after reboot) |
| Gain | auto |
| JSON Port | 30978 |

## 6. skyaware978

**Config location:** `/etc/systemd/system/skyaware978.service`

| Parameter | Value |
|-----------|-------|
| Connect | 127.0.0.1:30978 |
| JSON Directory | /run/dump978 |
| Latitude | 61.2181 |
| Longitude | -149.9003 |

## 7. tar1090

**Config location:** `/etc/default/tar1090`

| Parameter | Value |
|-----------|-------|
| Update Interval | 8 seconds |
| History Size | 450 entries |
| 978 UAT Enabled | yes |
| 978 URL | http://127.0.0.1:8504/skyaware978 |
| GZIP Level | 1 |
| Position Tracks | 8 hours |

## 8. Backup (rclone)

**Config location:** `~/.config/rclone/rclone.conf`

| Parameter | Value |
|-----------|-------|
| Remote | skybridge-central (not yet configured) |
| Destination | dot-vhf/skybridge/ |
| Schedule | Every 6 hours (systemd timer) |
| Bandwidth Limit | 2 MB/s |
| Max Transfers | 4 |
| Max Checkers | 8 |
| Retries | 3 |
| Excludes | backup-staging/** |

## 9. SSH / Security

**Config location:** `/etc/ssh/sshd_config.d/60-skybridge-hardening.conf`

| Parameter | Value |
|-----------|-------|
| Authentication | Public key only (Ed25519) |
| Password Auth | disabled |
| Root Login | disabled |
| Max Auth Tries | 3 |
| Login Grace Time | 30 seconds |
| X11 Forwarding | disabled |
| Agent Forwarding | disabled |

**fail2ban:** `/etc/fail2ban/jail.d/skybridge-ssh.conf`

| Parameter | Value |
|-----------|-------|
| Max Retry | 5 |
| Find Time | 10 minutes |
| Ban Time | 1 hour |

## 10. Log Rotation

**Config location:** `/etc/logrotate.d/skybridge`

| Parameter | Value |
|-----------|-------|
| Scope | /mnt/nvme/skybridge/logs/*.log |
| Frequency | weekly |
| Retention | 12 rotations |
| Compression | yes (delayed) |
| Owner | blastly:blastly (0640) |

## 11. NVMe Mount

**Config location:** `/etc/fstab`

| Device | Mount | Filesystem | Options |
|--------|-------|-----------|---------|
| /dev/nvme0n1p2 | /mnt/nvme | ext4 | defaults,nofail |

## 12. tmpfiles.d

**Config location:** `/etc/tmpfiles.d/dump978.conf`

```
d /run/dump978 0755 blastly blastly -
```
Creates runtime directory for dump978 JSON output on every boot.
