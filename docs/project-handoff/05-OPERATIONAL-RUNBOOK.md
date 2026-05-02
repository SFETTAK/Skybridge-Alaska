# Operational Runbook — DOT-VHF Ground Station

**Project:** SkyBridge Alaska
**Station ID:** DOT-VHF
**Date:** 2026-03-12

---

## 1. Accessing the Station

### SSH
```bash
ssh <operator>@<station-host>        # Ethernet
ssh blastly@<station-host-alt>        # WiFi (backup)
```
- Key-only auth (Ed25519). No password login.
- If locked out by fail2ban, wait 1 hour or access via physical console.

### Web Interfaces
| URL | Service |
|-----|---------|
| http://<station-host>:8073 | OpenWebRX (SDR spectrum viewer) |
| http://<station-host>:8504 | tar1090 (ADS-B aircraft map) |
| http://<station-host>:8080 | Status Dashboard |

---

## 2. Checking Station Health

### Quick Status
```bash
# All services at a glance
systemctl status openwebrx readsb dump978-fa skyaware978 tar1090 status-dashboard vhf-pipeline

# Dashboard (also viewable at :8080)
cat /mnt/nvme/skybridge/status.html
```

### NVMe Health
```bash
sudo smartctl -a /dev/nvme0n1         # Full SMART report
df -h /mnt/nvme                        # Disk usage
ls -lh /mnt/nvme/skybridge/            # Data directories
```

### SDR Devices
```bash
lsusb                                  # Should show 3x RTL2838
rtl_test -d 0 -t                       # Test BLOGV4
rtl_test -d 1 -t                       # Test UAT978
rtl_test -d 2 -t                       # Test ADSB1090
```

### Network
```bash
ip addr show eth0                      # Should be <station-host>
ip addr show wlan0                     # Should be <station-host-alt>
ping -c 3 8.8.8.8                      # Internet connectivity
```

---

## 3. Service Management

### Start / Stop / Restart Individual Services
```bash
sudo systemctl start <service>
sudo systemctl stop <service>
sudo systemctl restart <service>
sudo systemctl status <service>
```

### Service Names
- `openwebrx` — WebSDR receiver
- `readsb` — ADS-B 1090 MHz decoder
- `dump978-fa` — UAT 978 MHz decoder
- `skyaware978` — UAT web map
- `tar1090` — ADS-B web map
- `status-dashboard` — Health dashboard
- `vhf-pipeline` — VHF voice transcription
- `nvme-backup.timer` — Backup scheduler

### Restart All Station Services
```bash
sudo systemctl restart openwebrx readsb dump978-fa skyaware978 tar1090 status-dashboard
```

### View Service Logs
```bash
journalctl -u openwebrx -f            # Follow live
journalctl -u readsb --since "1 hour ago"
journalctl -u vhf-pipeline -n 100     # Last 100 lines
```

---

## 4. VHF Pipeline Operations

### Start the Pipeline
```bash
sudo systemctl start vhf-pipeline
```
Requires: OpenWebRX running (provides rtl_tcp on :1235)

### Stop the Pipeline
```bash
sudo systemctl stop vhf-pipeline
```

### Manual Run (for testing/debugging)
```bash
source ~/vhf-pipeline-venv/bin/activate
python ~/scripts/vhf-pipeline.py --freq 121800000 --model tiny.en --vad-threshold 0.005
```

### Run Test Suite
```bash
source ~/vhf-pipeline-venv/bin/activate
python ~/scripts/test-pipeline.py
```
Expected: 17/17 tests pass. Runs without SDR hardware.

### Tune VAD Sensitivity
- **More sensitive** (catch quieter transmissions): lower `--vad-threshold` (e.g., 0.003)
- **Less sensitive** (reduce false positives): raise `--vad-threshold` (e.g., 0.01)

### Check Captured Audio
```bash
ls -lh /mnt/nvme/skybridge/vhf-audio/$(date +%Y-%m-%d)/
```

### Check Transcripts
```bash
cat /mnt/nvme/skybridge/transcripts/$(date +%Y-%m-%d).txt
```

---

## 5. ADS-B Operations

### Check Aircraft Count
```bash
# From readsb JSON
python3 -c "import json; d=json.load(open('/run/readsb/aircraft.json')); print(f'Aircraft: {len(d[\"aircraft\"])}')"
```

### Set Station Location
```bash
sudo readsb-set-location 61.2181 -149.9003
```

### View ADS-B Data Feed
- Web map: http://<station-host>:8504
- SBS output: `nc 127.0.0.1 30003` (BaseStation format)
- Beast output: `nc 127.0.0.1 30005` (binary)

---

## 6. OpenWebRX Administration

### Access Admin Panel
Navigate to http://<station-host>:8073 → Settings (login required)

### Create/Reset Admin User
```bash
cd ~/openwebrx
OPENWEBRX_CONFIG_DIR=/var/lib/openwebrx ./venv/bin/openwebrx admin adduser admin
```

### Change SDR Profile
Edit `/var/lib/openwebrx/settings.json` or use the web admin panel.

---

## 7. Backup Operations

### Check Backup Status
```bash
systemctl status nvme-backup.timer     # Timer schedule
systemctl status nvme-backup.service   # Last run result
ls -lt /mnt/nvme/skybridge/logs/rclone-backup-*.log | head -3
```

### Trigger Manual Backup
```bash
sudo systemctl start nvme-backup.service
```

### Configure Backup Remote
The rclone remote `skybridge-central` needs to be configured:
```bash
rclone config
# Choose: New remote → name: skybridge-central
# Choose protocol (SFTP, S3, etc.) based on central server setup
```

---

## 8. Troubleshooting

### SDR Device Not Found
```bash
# Check USB devices are present
lsusb | grep RTL2838

# Check kernel module isn't grabbing device
lsmod | grep dvb_usb_rtl28xxu
# If loaded: sudo modprobe -r dvb_usb_rtl28xxu
# Blacklist is at /etc/modprobe.d/ (should already be configured)
```

### OpenWebRX Won't Start
```bash
journalctl -u openwebrx -n 50
# Common: SDR device busy (another process using it)
# Fix: stop vhf-pipeline first, then restart openwebrx
```

### VHF Pipeline Can't Connect
```bash
# Check rtl_tcp is available
nc -z 127.0.0.1 1235 && echo "OK" || echo "NOT LISTENING"

# OpenWebRX must be running first (provides rtl_tcp)
sudo systemctl restart openwebrx
sleep 3
sudo systemctl restart vhf-pipeline
```

### No ADS-B Aircraft Showing
```bash
# Check readsb is receiving
journalctl -u readsb -n 20
# Check antenna connection
rtl_test -d ADSB1090 -t
# Check JSON output
cat /run/readsb/aircraft.json | python3 -m json.tool | head
```

### NVMe Not Mounted
```bash
mount | grep nvme
# If not mounted:
sudo mount /mnt/nvme
# Check fstab entry exists:
grep nvme /etc/fstab
```

### fail2ban Locked You Out
- Wait 1 hour (ban duration)
- Or access via physical console / different IP
- Check bans: `sudo fail2ban-client status sshd`
- Unban specific IP: `sudo fail2ban-client set sshd unbanip <IP>`

### High CPU Usage
```bash
top -o %CPU
# Whisper STT can spike CPU during transcription — this is normal
# If persistent: check vhf-pipeline isn't in a crash loop
journalctl -u vhf-pipeline --since "10 min ago"
```

---

## 9. System Maintenance

### Update System Packages
```bash
sudo apt update && sudo apt upgrade -y
```

### Restart the Pi
```bash
sudo reboot
```
All services are systemd-enabled and will start automatically on boot.

### Check Disk Usage
```bash
df -h /mnt/nvme /
du -sh /mnt/nvme/skybridge/*/
```

### Rotate Logs Manually
```bash
sudo logrotate -f /etc/logrotate.d/skybridge
```

### Check NVMe Wear
```bash
sudo smartctl -a /dev/nvme0n1 | grep -E "(Percentage Used|Available Spare|Media Errors)"
```

---

## 10. Known Issues & Open Items

| Issue | Status | Notes |
|-------|--------|-------|
| dump978 uses device=1 not serial=UAT978 | Pending reboot | Update service file after reboot to use serial |
| Meshtastic node not connected | Awaiting hardware | Pipeline ready; set MESH_HOST or connect USB serial |
| rclone remote not configured | Awaiting central server | Run `rclone config` when server is ready |
| Hailo-8 AI HAT+ disabled | By design | NVMe takes M.2 priority; CPU Whisper sufficient for now |
| readsb location not set | Minor | Run `sudo readsb-set-location 61.2181 -149.9003` |
