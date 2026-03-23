#!/usr/bin/env bash
# skybridge_status_feed.sh
# Posts Skybridge station status to #skybridge-status Discord channel
# Runs every 30 minutes via cron
# Updated: 2026-03-23 — SSH keys active, VHF via journal, ADS-B via readsb port 8504

DISCORD_TOKEN="${DISCORD_TOKEN:-}"  # Set in /home/blastly/.env.skybridge
CHANNEL_ID="1485484585142980748"  # #skybridge-status
PI_HOST="${PI_HOST:-blastly@localhost}"  # Override via env
SSH_OPTS="-o ConnectTimeout=8 -o BatchMode=yes -o StrictHostKeyChecking=no"

# --- ADS-B Summary (readsb on port 8504) ---
ADSB_RAW=$(ssh $SSH_OPTS $PI_HOST "curl -sf http://localhost:8504/data/aircraft.json 2>/dev/null && echo '---STATS---' && curl -sf http://localhost:8504/data/stats.json 2>/dev/null" 2>/dev/null)

if [ -n "$ADSB_RAW" ]; then
  AIRCRAFT_JSON=$(echo "$ADSB_RAW" | sed '/^---STATS---$/,$ d')
  STATS_JSON=$(echo "$ADSB_RAW" | sed '1,/^---STATS---$/d')

  AIRCRAFT_COUNT=$(echo "$AIRCRAFT_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('aircraft',[])))" 2>/dev/null || echo "?")
  POS_COUNT=$(echo "$AIRCRAFT_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len([a for a in d.get('aircraft',[]) if 'lat' in a]))" 2>/dev/null || echo "?")
  MSG_1MIN=$(echo "$STATS_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); l=d.get('last1min',{}); loc=l.get('local',{}); v=loc.get('accepted',0); print(sum(v) if isinstance(v,list) else v)" 2>/dev/null || echo "?")
  GAIN=$(echo "$STATS_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(round(d.get('gain_db',0),1))" 2>/dev/null || echo "?")
  ADSB_STATUS="✅ $AIRCRAFT_COUNT tracked | $POS_COUNT w/ position | $MSG_1MIN msgs/min | gain ${GAIN}dB"
else
  ADSB_STATUS="❌ ADS-B data unavailable (readsb port 8504)"
fi

# --- VHF Transcripts (from systemd journal, last 30 min) ---
VHF_LINES=$(ssh $SSH_OPTS $PI_HOST "
  journalctl -u vhf-pipeline --since '30 minutes ago' --no-pager 2>/dev/null \
    | grep 'Transcript' \
    | grep -v 'rel=0\.00\]' \
    | tail -3 \
    | sed 's/.*Transcript (.*): //'
" 2>/dev/null)

# Fall back to any transcripts (including rel=0.00) if nothing else
if [ -z "$VHF_LINES" ]; then
  VHF_LINES=$(ssh $SSH_OPTS $PI_HOST "
    journalctl -u vhf-pipeline --since '30 minutes ago' --no-pager 2>/dev/null \
      | grep 'Transcript' \
      | tail -3 \
      | sed 's/.*Transcript (.*): //'
  " 2>/dev/null)
fi

# Count audio files archived this period
VHF_AUDIO_COUNT=$(ssh $SSH_OPTS $PI_HOST "
  find /mnt/nvme/skybridge/vhf-audio/ -name '*.flac' -newer /tmp/.skybridge_last_check 2>/dev/null | wc -l
" 2>/dev/null || echo "?")

if [ -n "$VHF_LINES" ]; then
  VHF_STATUS="📻 ${VHF_AUDIO_COUNT} clips since last check | Recent:\n\`\`\`\n${VHF_LINES}\n\`\`\`"
else
  VHF_STATUS="📻 ${VHF_AUDIO_COUNT} clips | No recent VHF transcripts (quiet airwaves)"
fi

# Update timestamp marker
ssh $SSH_OPTS $PI_HOST "touch /tmp/.skybridge_last_check" 2>/dev/null

# --- Service Health ---
SERVICES=$(ssh $SSH_OPTS $PI_HOST "
  for svc in openwebrx tar1090 vhf-pipeline readsb; do
    state=\$(systemctl is-active \$svc 2>/dev/null)
    if [ \"\$state\" = \"active\" ]; then
      echo \"✅ \$svc\"
    else
      echo \"❌ \$svc (\$state)\"
    fi
  done
" 2>/dev/null)
if [ -z "$SERVICES" ]; then
  SERVICES="❌ Unable to check services (SSH failed)"
fi

# --- Build Discord Embed ---
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
PAYLOAD=$(python3 -c "
import json, sys

adsb = sys.argv[1]
vhf = sys.argv[2]
services = sys.argv[3]
ts = sys.argv[4]

embed = {
  'embeds': [{
    'title': '🛰️ Skybridge Station Status — DOT-VHF',
    'description': 'Anchorage, AK | 118.600 MHz | ADS-B lat: 61.22° / lon: -149.90°',
    'color': 0x00b0f4,
    'timestamp': ts,
    'fields': [
      {'name': '📡 ADS-B (readsb)', 'value': adsb, 'inline': False},
      {'name': '📻 VHF (118.6 MHz)', 'value': vhf, 'inline': False},
      {'name': '🔧 Services', 'value': services, 'inline': False}
    ],
    'footer': {'text': 'Auto-feed • every 30min • skybridge_status_feed.sh'}
  }]
}
print(json.dumps(embed))
" "$ADSB_STATUS" "$VHF_STATUS" "$SERVICES" "$TIMESTAMP" 2>/dev/null)

# --- Post to Discord ---
RESULT=$(curl -s -o /tmp/skybridge_discord_post.log -w "%{http_code}" \
  -X POST "https://discord.com/api/v10/channels/${CHANNEL_ID}/messages" \
  -H "Authorization: Bot ${DISCORD_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

echo "$(date -u) HTTP $RESULT" >> /tmp/skybridge_feed.log

exit 0
