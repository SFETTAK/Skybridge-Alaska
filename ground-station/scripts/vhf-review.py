#!/usr/bin/env python3
"""
VHF Review — SkyBridge Alaska
Web UI for browsing archived VHF audio, playback, and transcript review.
Serves on port 8082.
"""

import datetime
import glob
import json
import os
import re
import subprocess
import tempfile

from flask import Flask, Response, jsonify, request, send_file

app = Flask(__name__)

AUDIO_DIR = "/mnt/nvme/skybridge/vhf-audio"
TRANSCRIPT_DIR = "/mnt/nvme/skybridge/transcripts"
# Cache converted wav files to avoid re-converting on every play
WAV_CACHE_DIR = "/tmp/vhf-review-cache"
os.makedirs(WAV_CACHE_DIR, exist_ok=True)


# ── API ──────────────────────────────────────────────────────────────────────

@app.route("/api/dates")
def api_dates():
    """List available recording dates."""
    dates = sorted(
        [d for d in os.listdir(AUDIO_DIR)
         if os.path.isdir(os.path.join(AUDIO_DIR, d)) and re.match(r"\d{4}-\d{2}-\d{2}", d)],
        reverse=True,
    )
    return jsonify(dates)


@app.route("/api/recordings")
def api_recordings():
    """List recordings for a given date, paired with transcripts."""
    date = request.args.get("date", datetime.date.today().isoformat())
    date_dir = os.path.join(AUDIO_DIR, date)
    if not os.path.isdir(date_dir):
        return jsonify([])

    # Load transcripts for this date
    tfile = os.path.join(TRANSCRIPT_DIR, f"{date}.txt")
    transcripts = {}
    if os.path.exists(tfile):
        with open(tfile) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Format: ISO_TIMESTAMP [FREQ] text
                # or old format: ISO_TIMESTAMP text
                m = re.match(r"(\S+)\s+(?:\[([^\]]+)\]\s+)?(.*)", line)
                if m:
                    ts_str, freq, text = m.groups()
                    # Extract just HHMMSS from the ISO timestamp for matching
                    try:
                        ts = datetime.datetime.fromisoformat(ts_str)
                        key = ts.strftime("%H%M%S")
                        transcripts[key] = {
                            "timestamp": ts_str,
                            "freq": freq or "",
                            "text": text,
                        }
                    except ValueError:
                        pass

    # List FLAC files
    files = glob.glob(os.path.join(date_dir, "*.flac"))
    recordings = []
    for fpath in files:
        fname = os.path.basename(fpath)
        size = os.path.getsize(fpath)
        # Parse filename: 121.800MHz_214734.flac or 211342.flac (old format)
        m = re.match(r"(?:(.+?)_)?(\d{6})\.flac", fname)
        if not m:
            continue
        freq_label = m.group(1) or ""
        time_str = m.group(2)
        # Format time for display
        display_time = f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]} UTC"

        # Match transcript by time key
        transcript = transcripts.get(time_str, {})

        recordings.append({
            "filename": fname,
            "date": date,
            "time": display_time,
            "time_key": time_str,
            "freq": freq_label,
            "size_kb": round(size / 1024, 1),
            "transcript": transcript.get("text", ""),
        })

    # Sort by time — newest first
    recordings.sort(key=lambda r: r["time_key"], reverse=True)
    return jsonify(recordings)


@app.route("/audio/<date>/<filename>")
def serve_audio(date, filename):
    """Serve a FLAC file converted to WAV for browser playback."""
    # Sanitize inputs
    if not re.match(r"\d{4}-\d{2}-\d{2}$", date):
        return "Bad date", 400
    if not re.match(r"[\w.]+\.flac$", filename):
        return "Bad filename", 400

    flac_path = os.path.join(AUDIO_DIR, date, filename)
    if not os.path.exists(flac_path):
        return "Not found", 404

    # Check cache
    wav_name = filename.replace(".flac", ".wav")
    wav_path = os.path.join(WAV_CACHE_DIR, f"{date}_{wav_name}")
    if not os.path.exists(wav_path):
        subprocess.run(
            ["sox", flac_path, "-r", "16000", "-c", "1", wav_path],
            check=True, capture_output=True,
        )

    return send_file(wav_path, mimetype="audio/wav")


@app.route("/api/stats")
def api_stats():
    """Pipeline statistics."""
    total_files = 0
    total_size = 0
    dates = []
    for d in sorted(os.listdir(AUDIO_DIR)):
        dpath = os.path.join(AUDIO_DIR, d)
        if os.path.isdir(dpath) and re.match(r"\d{4}-\d{2}-\d{2}", d):
            files = glob.glob(os.path.join(dpath, "*.flac"))
            total_files += len(files)
            total_size += sum(os.path.getsize(f) for f in files)
            dates.append(d)

    total_transcripts = 0
    for tf in glob.glob(os.path.join(TRANSCRIPT_DIR, "*.txt")):
        with open(tf) as f:
            total_transcripts += sum(1 for line in f if line.strip())

    return jsonify({
        "total_recordings": total_files,
        "total_size_mb": round(total_size / 1024 / 1024, 1),
        "total_transcripts": total_transcripts,
        "dates_available": len(dates),
        "oldest": dates[0] if dates else None,
        "newest": dates[-1] if dates else None,
    })


# ── HTML UI ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return HTML_PAGE


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VHF Review — SkyBridge Alaska</title>
<style>
  :root {
    --bg: #0a0e14; --surface: #131920; --surface2: #1a2230;
    --border: #2a3545; --text: #c8d0da; --text2: #6b7a8d;
    --accent: #00d4aa; --accent2: #0090ff; --warn: #ff6b35;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    background: var(--bg); color: var(--text);
    min-height: 100vh;
  }
  header {
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 12px 20px; display: flex; align-items: center; gap: 16px;
  }
  header h1 { font-size: 16px; color: var(--accent); font-weight: 600; }
  header .stats { font-size: 11px; color: var(--text2); margin-left: auto; }

  .controls {
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 8px 20px; display: flex; align-items: center; gap: 12px;
  }
  .controls label { font-size: 11px; color: var(--text2); text-transform: uppercase; }
  .controls select, .controls input {
    background: var(--surface2); border: 1px solid var(--border);
    color: var(--text); padding: 4px 8px; border-radius: 3px; font-family: inherit;
    font-size: 12px;
  }
  .controls button {
    background: var(--accent); color: var(--bg); border: none;
    padding: 4px 12px; border-radius: 3px; cursor: pointer;
    font-family: inherit; font-size: 11px; font-weight: 600;
  }

  .main { display: flex; height: calc(100vh - 90px); }

  .timeline {
    width: 380px; min-width: 300px; border-right: 1px solid var(--border);
    overflow-y: auto; background: var(--surface);
  }
  .rec-item {
    padding: 10px 14px; border-bottom: 1px solid var(--border);
    cursor: pointer; transition: background 0.15s;
  }
  .rec-item:hover { background: var(--surface2); }
  .rec-item.active { background: var(--surface2); border-left: 3px solid var(--accent); }
  .rec-item .time { font-size: 13px; color: var(--accent2); font-weight: 600; }
  .rec-item .freq { font-size: 11px; color: var(--accent); margin-left: 8px; }
  .rec-item .size { font-size: 10px; color: var(--text2); float: right; }
  .rec-item .preview {
    font-size: 11px; color: var(--text2); margin-top: 4px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .rec-item .preview.empty { font-style: italic; color: #4a5568; }

  .detail {
    flex: 1; display: flex; flex-direction: column; padding: 20px;
    overflow-y: auto;
  }
  .player-box {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 6px; padding: 16px; margin-bottom: 16px;
  }
  .player-box .meta {
    display: flex; gap: 16px; margin-bottom: 12px; font-size: 12px;
  }
  .player-box .meta span { color: var(--text2); }
  .player-box .meta strong { color: var(--text); }
  .player-box audio { width: 100%; height: 40px; }

  .transcript-box {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 6px; padding: 16px; flex: 1;
  }
  .transcript-box h3 {
    font-size: 11px; color: var(--text2); text-transform: uppercase;
    margin-bottom: 8px; letter-spacing: 1px;
  }
  .transcript-text {
    font-size: 14px; line-height: 1.6; color: var(--text);
    white-space: pre-wrap; min-height: 60px;
  }
  .transcript-text.empty { color: var(--text2); font-style: italic; }

  .no-selection {
    display: flex; align-items: center; justify-content: center;
    flex: 1; color: var(--text2); font-size: 14px;
  }

  .filter-bar { display: flex; gap: 8px; align-items: center; }
  .filter-bar input {
    background: var(--surface2); border: 1px solid var(--border);
    color: var(--text); padding: 4px 8px; border-radius: 3px;
    font-family: inherit; font-size: 12px; width: 200px;
  }

  .count-badge {
    background: var(--surface2); color: var(--accent);
    padding: 2px 8px; border-radius: 10px; font-size: 11px;
  }

  #waveform {
    width: 100%; height: 60px; background: var(--bg);
    border-radius: 4px; margin-bottom: 8px; position: relative;
  }
  #waveform canvas { width: 100%; height: 100%; }
</style>
</head>
<body>

<header>
  <h1>VHF REVIEW</h1>
  <span style="font-size:12px;color:var(--text2)">SkyBridge Alaska</span>
  <div class="stats" id="stats">loading...</div>
</header>

<div class="controls">
  <label>Date</label>
  <select id="dateSelect"></select>
  <div class="filter-bar">
    <label>Search</label>
    <input id="searchBox" type="text" placeholder="filter transcripts...">
  </div>
  <span class="count-badge" id="countBadge">0 recordings</span>
  <button onclick="loadRecordings()" style="margin-left:auto">Refresh</button>
</div>

<div class="main">
  <div class="timeline" id="timeline"></div>
  <div class="detail" id="detail">
    <div class="no-selection">Select a recording from the timeline</div>
  </div>
</div>

<script>
let recordings = [];
let currentRec = null;

async function init() {
  const dates = await (await fetch('/api/dates')).json();
  const sel = document.getElementById('dateSelect');
  dates.forEach(d => {
    const opt = document.createElement('option');
    opt.value = d; opt.textContent = d;
    sel.appendChild(opt);
  });
  sel.addEventListener('change', loadRecordings);

  document.getElementById('searchBox').addEventListener('input', renderTimeline);

  loadStats();
  if (dates.length) loadRecordings();

  // Auto-refresh every 30s
  setInterval(() => { loadRecordings(); loadStats(); }, 30000);
}

async function loadStats() {
  const s = await (await fetch('/api/stats')).json();
  document.getElementById('stats').textContent =
    `${s.total_recordings} recordings | ${s.total_size_mb} MB | ${s.total_transcripts} transcripts`;
}

async function loadRecordings() {
  const date = document.getElementById('dateSelect').value;
  recordings = await (await fetch(`/api/recordings?date=${date}`)).json();
  renderTimeline();
}

function renderTimeline() {
  const query = document.getElementById('searchBox').value.toLowerCase();
  const filtered = query
    ? recordings.filter(r => r.transcript.toLowerCase().includes(query) || r.freq.toLowerCase().includes(query))
    : recordings;

  document.getElementById('countBadge').textContent = `${filtered.length} recordings`;

  const tl = document.getElementById('timeline');
  tl.innerHTML = filtered.map((r, i) => `
    <div class="rec-item ${currentRec && currentRec.filename === r.filename ? 'active' : ''}"
         onclick="selectRec(${recordings.indexOf(r)})">
      <span class="time">${r.time}</span>
      <span class="freq">${r.freq}</span>
      <span class="size">${r.size_kb} KB</span>
      <div class="preview ${r.transcript ? '' : 'empty'}">
        ${r.transcript ? escHtml(r.transcript.substring(0, 120)) : '(no transcript)'}
      </div>
    </div>
  `).join('');
}

function selectRec(idx) {
  currentRec = recordings[idx];
  renderTimeline();

  const audioUrl = `/audio/${currentRec.date}/${currentRec.filename}`;

  document.getElementById('detail').innerHTML = `
    <div class="player-box">
      <div class="meta">
        <span>Time: <strong>${currentRec.time}</strong></span>
        <span>Freq: <strong>${currentRec.freq || 'N/A'}</strong></span>
        <span>Size: <strong>${currentRec.size_kb} KB</strong></span>
      </div>
      <div id="waveform"><canvas id="waveCanvas"></canvas></div>
      <audio id="audioPlayer" controls src="${audioUrl}" preload="auto"></audio>
    </div>
    <div class="transcript-box">
      <h3>Transcript</h3>
      <div class="transcript-text ${currentRec.transcript ? '' : 'empty'}">
        ${currentRec.transcript ? escHtml(currentRec.transcript) : '(no transcript for this segment)'}
      </div>
    </div>
  `;

  // Draw waveform after audio loads
  const audio = document.getElementById('audioPlayer');
  audio.addEventListener('canplaythrough', () => drawWaveform(audioUrl), {once: true});
  // Playback position indicator
  audio.addEventListener('timeupdate', drawPlayhead);
}

async function drawWaveform(url) {
  try {
    const resp = await fetch(url);
    const buf = await resp.arrayBuffer();
    const actx = new (window.AudioContext || window.webkitAudioContext)();
    const decoded = await actx.decodeAudioData(buf);
    const data = decoded.getChannelData(0);
    const canvas = document.getElementById('waveCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    canvas.width = canvas.offsetWidth * 2;
    canvas.height = canvas.offsetHeight * 2;
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const step = Math.ceil(data.length / w);
    ctx.strokeStyle = '#00d4aa';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x < w; x++) {
      const start = x * step;
      let min = 1, max = -1;
      for (let j = 0; j < step && start + j < data.length; j++) {
        const v = data[start + j];
        if (v < min) min = v;
        if (v > max) max = v;
      }
      const yMin = (1 + min) * h / 2;
      const yMax = (1 + max) * h / 2;
      ctx.moveTo(x, yMin);
      ctx.lineTo(x, yMax);
    }
    ctx.stroke();
    actx.close();
  } catch(e) { console.log('waveform error:', e); }
}

function drawPlayhead() {
  const audio = document.getElementById('audioPlayer');
  const canvas = document.getElementById('waveCanvas');
  if (!audio || !canvas || !audio.duration) return;
  const ctx = canvas.getContext('2d');
  const x = (audio.currentTime / audio.duration) * canvas.width;
  // Redraw waveform would be expensive, just draw a line overlay
  // We'll use a simple overlay approach
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

init();
</script>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082, debug=False)
