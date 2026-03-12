#!/usr/bin/env python3
"""
VHF Pipeline — DOT-VHF / SkyBridge Alaska
Connects to OpenWebRX's rtl_tcp-compat port, demodulates AM aviation audio,
archives compressed FLAC to NVMe, runs Whisper STT on voice segments,
and publishes transcripts to Meshtastic.
"""

import argparse
import datetime
import logging
import os
import queue
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import wave
import tempfile

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/mnt/nvme/skybridge/logs/vhf-pipeline.log"),
    ],
)
log = logging.getLogger("vhf-pipeline")

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────

RTL_TCP_HOST = "127.0.0.1"
RTL_TCP_PORT = 1235

# Tune to centre of General Aviation window (Guard/Ground/FSS/Unicom)
CENTER_FREQ_HZ = 121_800_000
SAMPLE_RATE = 2_400_000       # IQ sample rate from rtl_tcp
AUDIO_RATE = 24_000           # output audio sample rate (for Whisper: 16kHz; we resample)
WHISPER_RATE = 16_000

ARCHIVE_DIR = "/mnt/nvme/skybridge/vhf-audio"
TRANSCRIPT_DIR = "/mnt/nvme/skybridge/transcripts"
LOG_DIR = "/mnt/nvme/skybridge/logs"

# VAD: energy threshold (RMS of int16 normalised 0-1)
VAD_THRESHOLD = 0.005         # tune up to reduce false positives
VAD_HOLD_S = 1.5              # seconds to keep gate open after last voice
SEGMENT_MAX_S = 30            # max segment length before forced Whisper run

# Meshtastic
MESH_HOST = os.environ.get("MESH_HOST", "")      # set to node IP, or leave blank for serial
MESH_PORT = int(os.environ.get("MESH_PORT", "4403"))
MESH_CHANNEL = int(os.environ.get("MESH_CHANNEL", "0"))

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "tiny.en")

# ──────────────────────────────────────────────────────────────────────────────
# RTL-TCP CLIENT
# ──────────────────────────────────────────────────────────────────────────────

class RtlTcpClient:
    """Minimal rtl_tcp client — sets freq/rate then streams IQ bytes."""

    def __init__(self, host, port, freq, rate):
        self.host = host
        self.port = port
        self.freq = freq
        self.rate = rate
        self.sock = None

    def _cmd(self, cmd_id, value):
        self.sock.sendall(struct.pack(">BI", cmd_id, value))

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        # Read 12-byte magic header
        magic = self.sock.recv(12)
        log.info("rtl_tcp magic: %s", magic)
        self._cmd(0x01, self.freq)    # set centre freq
        self._cmd(0x02, self.rate)    # set sample rate
        self._cmd(0x04, 0)            # set tuner gain mode = auto
        log.info("Connected to rtl_tcp %s:%d freq=%d rate=%d",
                 self.host, self.port, self.freq, self.rate)

    def read(self, n_bytes):
        data = b""
        while len(data) < n_bytes:
            chunk = self.sock.recv(n_bytes - len(data))
            if not chunk:
                raise ConnectionError("rtl_tcp socket closed")
            data += chunk
        return data

    def close(self):
        if self.sock:
            self.sock.close()


# ──────────────────────────────────────────────────────────────────────────────
# AM DEMODULATOR
# ──────────────────────────────────────────────────────────────────────────────

def demod_am(iq_bytes, decimate=100):
    """
    Convert raw uint8 IQ bytes → demodulated AM audio (float32, -1..1).
    decimate: SAMPLE_RATE / decimate = audio sample rate
              2400000 / 100 = 24000 Hz
    """
    raw = np.frombuffer(iq_bytes, dtype=np.uint8).astype(np.float32)
    raw = (raw - 127.5) / 127.5           # centre and normalise to -1..1
    i = raw[0::2]
    q = raw[1::2]
    # AM envelope detection
    envelope = np.sqrt(i**2 + q**2)
    # Decimate
    n = (len(envelope) // decimate) * decimate
    audio = envelope[:n].reshape(-1, decimate).mean(axis=1)
    # Remove DC
    audio -= audio.mean()
    # Normalise
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio /= peak
    return audio.astype(np.float32)


def resample_to_16k(audio_24k):
    """Simple 2:3 decimation from 24kHz to 16kHz using ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=".f32", delete=False) as tmp_in:
        tmp_in.write(audio_24k.astype(np.float32).tobytes())
        tmp_in_path = tmp_in.name
    tmp_out_path = tmp_in_path + ".out.f32"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "f32le", "-ar", "24000", "-ac", "1", "-i", tmp_in_path,
        "-f", "f32le", "-ar", "16000", "-ac", "1", tmp_out_path,
    ], check=True)
    data = np.fromfile(tmp_out_path, dtype=np.float32)
    os.unlink(tmp_in_path)
    os.unlink(tmp_out_path)
    return data


# ──────────────────────────────────────────────────────────────────────────────
# ARCHIVE
# ──────────────────────────────────────────────────────────────────────────────

def archive_segment(audio_f32, timestamp):
    """Save audio segment as FLAC via sox."""
    date_dir = os.path.join(ARCHIVE_DIR, timestamp.strftime("%Y-%m-%d"))
    os.makedirs(date_dir, exist_ok=True)
    filename = timestamp.strftime("%H%M%S") + ".flac"
    path = os.path.join(date_dir, filename)

    # Write raw float32 → sox → FLAC
    raw_path = path + ".raw"
    audio_f32.astype(np.float32).tofile(raw_path)
    subprocess.run([
        "sox", "-t", "raw", "-r", "24000", "-e", "floating-point",
        "-b", "32", "-c", "1", raw_path,
        path,
    ], check=True, capture_output=True)
    os.unlink(raw_path)
    log.info("Archived %s (%.1f s)", path, len(audio_f32) / 24000)
    return path


# ──────────────────────────────────────────────────────────────────────────────
# WHISPER STT
# ──────────────────────────────────────────────────────────────────────────────

_whisper_model = None

def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        log.info("Loading Whisper model '%s'...", WHISPER_MODEL)
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu",
                                      compute_type="int8")
        log.info("Whisper model loaded.")
    return _whisper_model


def transcribe(audio_f32_24k):
    """Transcribe 24kHz float32 audio → text string."""
    audio_16k = resample_to_16k(audio_f32_24k)
    model = get_whisper()
    segments, info = model.transcribe(audio_16k, language="en",
                                      beam_size=1, vad_filter=True)
    text = " ".join(s.text.strip() for s in segments).strip()
    log.info("Transcript: %s (lang=%.2f)", text, info.language_probability)
    return text


# ──────────────────────────────────────────────────────────────────────────────
# MESHTASTIC SENDER
# ──────────────────────────────────────────────────────────────────────────────

def send_to_mesh(text, timestamp):
    if not text:
        return
    msg = f"[VHF {timestamp.strftime('%H:%M')}] {text[:200]}"
    try:
        if MESH_HOST:
            import meshtastic.tcp_interface
            iface = meshtastic.tcp_interface.TCPInterface(
                MESH_HOST, portNumber=MESH_PORT, connectNow=True)
        else:
            import meshtastic.serial_interface
            iface = meshtastic.serial_interface.SerialInterface()
        iface.sendText(msg, channelIndex=MESH_CHANNEL)
        iface.close()
        log.info("Sent to mesh: %s", msg)
    except Exception as e:
        log.warning("Mesh send failed: %s", e)

    # Always log transcript to NVMe regardless of mesh success
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    tfile = os.path.join(TRANSCRIPT_DIR,
                         timestamp.strftime("%Y-%m-%d") + ".txt")
    with open(tfile, "a") as f:
        f.write(f"{timestamp.isoformat()} {text}\n")


# ──────────────────────────────────────────────────────────────────────────────
# VAD + SEGMENT MANAGER
# ──────────────────────────────────────────────────────────────────────────────

class SegmentManager:
    """Accumulates audio, detects voice via energy VAD, emits segments."""

    def __init__(self, on_segment):
        self.on_segment = on_segment
        self.buffer = []
        self.voice_active = False
        self.last_voice_time = 0
        self.segment_start = None

    def feed(self, chunk_f32):
        rms = float(np.sqrt(np.mean(chunk_f32**2)))
        now = time.monotonic()
        is_voice = rms > VAD_THRESHOLD

        if is_voice:
            self.last_voice_time = now
            if not self.voice_active:
                log.debug("VAD open (rms=%.4f)", rms)
                self.voice_active = True
                self.segment_start = datetime.datetime.now(datetime.timezone.utc)

        if self.voice_active:
            self.buffer.append(chunk_f32)
            duration = sum(len(c) for c in self.buffer) / 24000
            gate_closed = (now - self.last_voice_time) > VAD_HOLD_S
            too_long = duration >= SEGMENT_MAX_S

            if gate_closed or too_long:
                log.debug("VAD close (%.1f s, gate=%s, long=%s)",
                          duration, gate_closed, too_long)
                self.voice_active = False
                segment = np.concatenate(self.buffer)
                self.buffer = []
                self.on_segment(segment, self.segment_start)


# ──────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ──────────────────────────────────────────────────────────────────────────────

stt_queue = queue.Queue(maxsize=8)
shutdown = threading.Event()


def stt_worker():
    """Background thread: pull segments, transcribe, send to mesh."""
    while not shutdown.is_set():
        try:
            segment, ts = stt_queue.get(timeout=1)
        except queue.Empty:
            continue
        try:
            text = transcribe(segment)
            send_to_mesh(text, ts)
        except Exception as e:
            log.exception("STT/mesh error: %s", e)
        finally:
            stt_queue.task_done()


def on_segment(audio, timestamp):
    """Called from main thread when VAD emits a voice segment."""
    archive_segment(audio, timestamp)
    try:
        stt_queue.put_nowait((audio, timestamp))
    except queue.Full:
        log.warning("STT queue full, dropping segment")


def run(args):
    log.info("Starting VHF pipeline: freq=%d Hz, rtl_tcp=%s:%d",
             args.freq, RTL_TCP_HOST, RTL_TCP_PORT)

    client = RtlTcpClient(RTL_TCP_HOST, RTL_TCP_PORT, args.freq, SAMPLE_RATE)
    client.connect()

    seg_mgr = SegmentManager(on_segment=on_segment)
    stt_thread = threading.Thread(target=stt_worker, daemon=True,
                                  name="stt-worker")
    stt_thread.start()

    # Each read = 0.1s of IQ data at 2.4 MHz = 480000 samples = 960000 bytes
    chunk_iq_bytes = int(SAMPLE_RATE * 0.1) * 2   # *2 for I+Q bytes (uint8)

    def handle_signal(sig, frame):
        log.info("Shutting down...")
        shutdown.set()
        client.close()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while not shutdown.is_set():
            raw = client.read(chunk_iq_bytes)
            audio = demod_am(raw, decimate=100)   # 24 kHz audio
            seg_mgr.feed(audio)
    except ConnectionError as e:
        log.error("Connection lost: %s", e)
    finally:
        shutdown.set()
        client.close()
        stt_thread.join(timeout=10)
        log.info("Pipeline stopped.")


def main():
    global WHISPER_MODEL, VAD_THRESHOLD
    parser = argparse.ArgumentParser(description="DOT-VHF pipeline")
    parser.add_argument("--freq", type=int, default=CENTER_FREQ_HZ,
                        help="Centre frequency in Hz (default: %(default)s)")
    parser.add_argument("--model", default=WHISPER_MODEL,
                        help="Whisper model (default: %(default)s)")
    parser.add_argument("--vad-threshold", type=float, default=VAD_THRESHOLD,
                        help="VAD RMS threshold (default: %(default)s)")
    args = parser.parse_args()

    WHISPER_MODEL = args.model
    VAD_THRESHOLD = args.vad_threshold

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    run(args)


if __name__ == "__main__":
    main()
