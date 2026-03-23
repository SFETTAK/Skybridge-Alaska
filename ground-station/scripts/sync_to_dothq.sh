#!/bin/bash
SRC_DIR="/mnt/nvme/skybridge/vhf-audio"
DEST_HOST="blastly@192.168.100.5"
DEST_DIR="~/vhf-audio"
LOG="/var/log/skybridge/sync.log"
mkdir -p /var/log/skybridge

# Check if inotifywait is available, use polling fallback if not
if command -v inotifywait &>/dev/null; then
    inotifywait -m -e close_write,moved_to --format '%f' "$SRC_DIR" | while read FILENAME; do
        [[ "$FILENAME" != *.wav ]] && [[ "$FILENAME" != *.flac ]] && continue
        sleep 1
        echo "$(date -u): syncing $FILENAME" >> "$LOG"
        rsync -az --timeout=30 "$SRC_DIR/$FILENAME" "$DEST_HOST:$DEST_DIR/" \
            && echo "$(date -u): OK $FILENAME" >> "$LOG" \
            || echo "$(date -u): FAIL $FILENAME" >> "$LOG"
    done
else
    # Polling fallback every 30s
    while true; do
        rsync -az --timeout=30 "$SRC_DIR/" "$DEST_HOST:$DEST_DIR/" >> "$LOG" 2>&1
        sleep 30
    done
fi
