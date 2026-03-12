#!/bin/bash
# /home/blastly/scripts/nvme-backup.sh
# Sync /mnt/nvme/skybridge to the configured rclone remote.
# Run via systemd timer (nvme-backup.timer) or cron.
#
# Remote must be configured as "skybridge-central" via `rclone config`.
# Destination layout on remote: skybridge-central:dot-vhf/skybridge/

set -euo pipefail

REMOTE="skybridge-central:dot-vhf"
SOURCE="/mnt/nvme/skybridge"
LOG_DIR="/mnt/nvme/skybridge/logs"
LOG_FILE="$LOG_DIR/rclone-backup-$(date +%Y-%m-%d).log"
LOCK_FILE="/tmp/nvme-backup.lock"

# Prevent overlapping runs
if [ -e "$LOCK_FILE" ]; then
    echo "$(date): backup already running, exiting" >> "$LOG_FILE"
    exit 0
fi
touch "$LOCK_FILE"
trap "rm -f $LOCK_FILE" EXIT

mkdir -p "$LOG_DIR"

echo "$(date): starting rclone sync $SOURCE → $REMOTE/skybridge" >> "$LOG_FILE"

rclone sync "$SOURCE" "$REMOTE/skybridge" \
    --exclude "backup-staging/**" \
    --log-file "$LOG_FILE" \
    --log-level INFO \
    --transfers 4 \
    --checkers 8 \
    --bwlimit 2M \
    --retries 3 \
    --retries-sleep 10s \
    --stats 60s \
    --progress \
    2>&1 | tee -a "$LOG_FILE"

echo "$(date): rclone sync complete" >> "$LOG_FILE"
