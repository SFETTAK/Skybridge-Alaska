#!/bin/bash
# vhf-cleanup.sh — SkyBridge Alaska
# Prune oldest VHF audio recordings when total exceeds MAX_SIZE.
# Runs via systemd timer. Keeps transcripts (tiny), only deletes FLAC.

AUDIO_DIR="/mnt/nvme/skybridge/vhf-audio"
LOG="/mnt/nvme/skybridge/logs/vhf-cleanup.log"
MAX_BYTES=$((1024 * 1024 * 1024 * 1024))  # 1 TB

current_bytes() {
    du -sb "$AUDIO_DIR" 2>/dev/null | awk '{print $1}'
}

USED=$(current_bytes)
if [ "$USED" -le "$MAX_BYTES" ]; then
    echo "$(date -Iseconds) OK: ${USED} bytes used, under 1TB limit" >> "$LOG"
    exit 0
fi

echo "$(date -Iseconds) CLEANUP: ${USED} bytes exceeds 1TB, pruning oldest..." >> "$LOG"

# Delete oldest date directories first (FIFO)
for DATE_DIR in $(ls -d "$AUDIO_DIR"/????-??-?? 2>/dev/null | sort); do
    if [ "$(current_bytes)" -le "$MAX_BYTES" ]; then
        break
    fi
    DIR_SIZE=$(du -sh "$DATE_DIR" | awk '{print $1}')
    echo "$(date -Iseconds) Removing $DATE_DIR ($DIR_SIZE)" >> "$LOG"
    rm -rf "$DATE_DIR"
done

FINAL=$(current_bytes)
echo "$(date -Iseconds) DONE: ${FINAL} bytes after cleanup" >> "$LOG"
