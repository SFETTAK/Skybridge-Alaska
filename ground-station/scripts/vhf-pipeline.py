#!/usr/bin/env python3
"""
VHF Pipeline — DOT-VHF / SkyBridge Alaska
Connects to OpenWebRX's rtl_tcp-compat port, demodulates AM aviation audio,
archives compressed FLAC to NVMe, runs Whisper STT on voice segments,
and publishes transcripts to Meshtastic.
"""

import argparse
import datetime
import json
import logging
import os
import queue
import re
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
from scipy.signal import butter, sosfilt

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

# SDR center freq is set by OpenWebRX (rtl_tcp retune commands are ignored)
SDR_CENTER_HZ = 119_050_000   # OpenWebRX vhf_air_low profile center
CHANNEL_FREQ_HZ = 118_600_000 # ANC Tower (Ted Stevens Anchorage Intl)
SAMPLE_RATE = 2_400_000       # IQ sample rate from rtl_tcp
CHANNEL_BW = 12_500           # AM channel half-bandwidth (±12.5 kHz)
AUDIO_RATE = 16_000           # output audio sample rate (native Whisper rate)
WHISPER_RATE = 16_000

ARCHIVE_DIR = "/mnt/nvme/skybridge/vhf-audio"
TRANSCRIPT_DIR = "/mnt/nvme/skybridge/transcripts"
LOG_DIR = "/mnt/nvme/skybridge/logs"

# VAD / Squelch
VAD_THRESHOLD = 0.02          # absolute minimum RMS floor (safety net)
VAD_HOLD_S = 1.0              # seconds to keep gate open after last voice
SEGMENT_MAX_S = 30            # max segment length before forced Whisper run
SEGMENT_MIN_S = 0.5           # minimum segment length to bother transcribing
NO_SPEECH_THRESH = 0.6        # drop Whisper segments above this no_speech_probability
SQUELCH_SNR_DB = 8            # signal must be this many dB above noise floor
NOISE_ALPHA = 0.01            # noise floor EMA smoothing factor (slow adaptation)
PEAK_RATIO_MIN = 3.0          # segment peak/mean ratio for speech-likeness check

# Meshtastic
MESH_HOST = os.environ.get("MESH_HOST", "")      # set to node IP, or leave blank for serial
MESH_PORT = int(os.environ.get("MESH_PORT", "4403"))
MESH_CHANNEL = int(os.environ.get("MESH_CHANNEL", "0"))

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base.en")

# Aviation lexicon — PANC-specific prompt and post-processor
from aviation_lexicon import WHISPER_PROMPT_PANC as WHISPER_PROMPT
from aviation_lexicon import post_process as atc_post_process

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
# CHANNELIZED AM DEMODULATOR
# ──────────────────────────────────────────────────────────────────────────────

class ChannelDemod:
    """Single-channel AM demodulator with frequency shift + narrowband filter.

    Pipeline: IQ @ 2.4 MSPS → freq shift to baseband → LPF 12.5 kHz →
              decimate to 48 kHz → envelope detect → audio BPF 300-3400 Hz →
              decimate to 16 kHz (native Whisper rate).
    """

    def __init__(self, sdr_center_hz, channel_hz, sample_rate, audio_rate=16000):
        self.offset_hz = channel_hz - sdr_center_hz  # e.g. -450000
        self.sample_rate = sample_rate
        self.audio_rate = audio_rate

        # Stage 1: LPF for channel isolation (12.5 kHz cutoff at 2.4 MSPS)
        self.lpf_sos = butter(6, CHANNEL_BW, btype='low', fs=sample_rate, output='sos')

        # Stage 1 decimation: 2.4M → 48k (factor 50)
        self.decim1 = sample_rate // 48000  # 50
        self.rate1 = sample_rate // self.decim1  # 48000

        # Stage 2: audio bandpass 300-3400 Hz at 48 kHz
        self.bp_sos = butter(4, [300, 3400], btype='bandpass', fs=self.rate1, output='sos')

        # Stage 2 decimation: 48k → 16k (factor 3)
        self.decim2 = self.rate1 // audio_rate  # 3

        # Pre-compute mixer phasor increment
        self._phase = 0.0
        self._phase_inc = 2.0 * np.pi * self.offset_hz / self.sample_rate

        log.info("ChannelDemod: offset=%+d Hz, decim=%dx%d, output=%d Hz",
                 self.offset_hz, self.decim1, self.decim2, audio_rate)

    def process(self, iq_bytes):
        """Demodulate IQ bytes → float32 audio at self.audio_rate."""
        # Parse IQ
        raw = np.frombuffer(iq_bytes, dtype=np.uint8).astype(np.float32)
        raw = (raw - 127.5) / 127.5
        iq = raw[0::2] + 1j * raw[1::2]

        # Frequency shift to channel baseband
        n = len(iq)
        t = np.arange(n) * self._phase_inc + self._phase
        self._phase = t[-1] + self._phase_inc  # carry phase across chunks
        iq = iq * np.exp(-1j * t)

        # Low-pass filter (channel isolation)
        iq_real = sosfilt(self.lpf_sos, iq.real)
        iq_imag = sosfilt(self.lpf_sos, iq.imag)

        # Decimate stage 1: 2.4M → 48k
        iq_real = iq_real[::self.decim1]
        iq_imag = iq_imag[::self.decim1]

        # AM envelope detection
        envelope = np.sqrt(iq_real**2 + iq_imag**2)

        # Remove DC
        envelope -= envelope.mean()

        # Audio bandpass (300-3400 Hz voice band)
        audio = sosfilt(self.bp_sos, envelope)

        # Decimate stage 2: 48k → 16k
        audio = audio[::self.decim2]

        # Do NOT normalise here — preserve amplitude for squelch/VAD.
        # Normalisation happens on completed segments before archive/transcribe.
        return audio.astype(np.float32)


# ──────────────────────────────────────────────────────────────────────────────
# ARCHIVE
# ──────────────────────────────────────────────────────────────────────────────

def freq_label(hz):
    """121800000 → '121.800MHz'"""
    return f"{hz/1e6:.3f}MHz"


def archive_segment(audio_f32, timestamp, freq_hz):
    """Save audio segment as FLAC via sox."""
    date_dir = os.path.join(ARCHIVE_DIR, timestamp.strftime("%Y-%m-%d"))
    os.makedirs(date_dir, exist_ok=True)
    filename = f"{freq_label(freq_hz)}_{timestamp.strftime('%H%M%S')}.flac"
    path = os.path.join(date_dir, filename)

    # Write raw float32 → sox → FLAC
    raw_path = path + ".raw"
    audio_f32.astype(np.float32).tofile(raw_path)
    subprocess.run([
        "sox", "-t", "raw", "-r", str(AUDIO_RATE), "-e", "floating-point",
        "-b", "32", "-c", "1", raw_path,
        path,
    ], check=True, capture_output=True)
    os.unlink(raw_path)
    log.info("Archived %s (%.1f s)", path, len(audio_f32) / AUDIO_RATE)
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


def transcribe(audio_f32):
    """Transcribe float32 audio at AUDIO_RATE (16kHz) → text string."""
    duration = len(audio_f32) / AUDIO_RATE
    if duration < SEGMENT_MIN_S:
        log.debug("Segment too short (%.2fs), skipping STT", duration)
        return ""
    model = get_whisper()
    segments, info = model.transcribe(
        audio_f32,
        language="en",
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        initial_prompt=WHISPER_PROMPT,
    )
    # Filter out low-confidence segments (Whisper hallucinations)
    parts = []
    for s in segments:
        if s.no_speech_prob < NO_SPEECH_THRESH:
            parts.append(s.text.strip())
        else:
            log.debug("Dropped segment (no_speech=%.2f): %s", s.no_speech_prob, s.text.strip())
    raw_text = " ".join(parts).strip()
    # Aviation post-processing: correct vocabulary, suppress hallucinations
    text, relevance = atc_post_process(raw_text)
    log.info("Transcript (rel=%.2f): %s", relevance, text)
    if relevance < 0.05:
        log.debug("Discarded low-relevance transcript: %s", text)
        return ""
    return text


# ──────────────────────────────────────────────────────────────────────────────
# MESHTASTIC SENDER
# ──────────────────────────────────────────────────────────────────────────────

def send_to_mesh(text, timestamp, freq_hz):
    if not text:
        return
    msg = f"[VHF {freq_label(freq_hz)} {timestamp.strftime('%H:%M')}] {text[:200]}"
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
        f.write(f"{timestamp.isoformat()} [{freq_label(freq_hz)}] {text}\n")


# ──────────────────────────────────────────────────────────────────────────────
# ADS-B CORRELATION
# ──────────────────────────────────────────────────────────────────────────────

ADSB_JSON = "/run/readsb/aircraft.json"

# Spoken word → digit mapping for ATC number readbacks
_SPOKEN_DIGITS = {
    "zero": "0", "one": "1", "two": "2", "tree": "3", "three": "3",
    "four": "4", "fife": "5", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "niner": "9", "nine": "9",
}

# Phonetic alphabet for callsign letters
_PHONETIC = {
    "alpha": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E",
    "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I", "juliet": "J",
    "kilo": "K", "lima": "L", "mike": "M", "november": "N", "oscar": "O",
    "papa": "P", "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T",
    "uniform": "U", "victor": "V", "whiskey": "W", "xray": "X", "x-ray": "X",
    "yankee": "Y", "zulu": "Z",
}


def extract_callsigns(text):
    """Extract possible callsigns from ATC transcript text.
    Returns list of candidate callsign strings (e.g. ['N734CB', 'AAL67']).
    """
    candidates = set()
    lower = text.lower()

    # Pattern 1: Explicit N-numbers in text (N followed by digits/letters)
    for m in re.finditer(r'\b[Nn]\d{1,5}[A-Za-z]{0,2}\b', text):
        candidates.add(m.group().upper())

    # Pattern 2: "November" followed by phonetic/digit sequences
    # e.g. "November seven three four Charlie Bravo" → N734CB
    words = lower.split()
    for i, w in enumerate(words):
        if w == "november":
            callsign = "N"
            for j in range(i + 1, min(i + 7, len(words))):
                nw = words[j]
                if nw in _SPOKEN_DIGITS:
                    callsign += _SPOKEN_DIGITS[nw]
                elif nw in _PHONETIC:
                    callsign += _PHONETIC[nw]
                elif nw.isdigit() and len(nw) == 1:
                    callsign += nw
                else:
                    break
            if len(callsign) >= 3:
                candidates.add(callsign)

    # Pattern 3: Airline callsigns — "six seven heavy", "delta four two one"
    # Look for common airline words followed by numbers
    airline_map = {
        "alaska": "ASA", "united": "UAL", "delta": "DAL", "american": "AAL",
        "southwest": "SWA", "fedex": "FDX", "ups": "UPS", "jet blue": "JBU",
        "ravn": "RVF", "pen air": "PEN", "era": "ERA",
    }
    for airline, icao in airline_map.items():
        idx = lower.find(airline)
        if idx >= 0:
            rest = lower[idx + len(airline):].split()
            fnum = ""
            for nw in rest:
                if nw in _SPOKEN_DIGITS:
                    fnum += _SPOKEN_DIGITS[nw]
                elif nw.isdigit():
                    fnum += nw
                elif nw == "heavy" or nw == "super":
                    continue
                else:
                    break
            if fnum:
                candidates.add(icao + fnum)

    return list(candidates)


def lookup_adsb(callsigns):
    """Match callsign candidates against live ADS-B data.
    Returns dict of {callsign: {hex, alt, lat, lon, gs, track}} or empty.
    """
    if not callsigns:
        return {}
    try:
        with open(ADSB_JSON) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    matches = {}
    for ac in data.get("aircraft", []):
        flight = ac.get("flight", "").strip().upper()
        reg = ac.get("r", "").strip().upper()  # registration if available
        for cs in callsigns:
            cs_upper = cs.upper()
            if cs_upper == flight or cs_upper == reg:
                matches[cs] = {
                    "hex": ac.get("hex", ""),
                    "flight": flight,
                    "alt": ac.get("alt_baro", ""),
                    "lat": ac.get("lat", ""),
                    "lon": ac.get("lon", ""),
                    "gs": ac.get("gs", ""),
                    "track": ac.get("track", ""),
                    "squawk": ac.get("squawk", ""),
                }
    return matches


def annotate_transcript(text, adsb_matches):
    """Append ADS-B position info to transcript if matches found."""
    if not adsb_matches:
        return text
    annotations = []
    for cs, info in adsb_matches.items():
        parts = [cs]
        if info["alt"]:
            parts.append(f"alt:{info['alt']}")
        if info["lat"] and info["lon"]:
            parts.append(f"pos:{info['lat']:.4f},{info['lon']:.4f}")
        if info["squawk"]:
            parts.append(f"sq:{info['squawk']}")
        annotations.append(" ".join(parts))
    return f"{text} [ADSB: {'; '.join(annotations)}]"


# ──────────────────────────────────────────────────────────────────────────────
# VAD + SEGMENT MANAGER
# ──────────────────────────────────────────────────────────────────────────────

def segment_is_speech(audio_f32):
    """Quick check: does this segment look like speech (vs pure noise)?
    Speech has high peak-to-mean ratio and varying energy.
    Returns True if segment is likely speech.
    """
    if len(audio_f32) < int(AUDIO_RATE * SEGMENT_MIN_S):
        return False
    abs_audio = np.abs(audio_f32)
    mean_amp = float(np.mean(abs_audio))
    if mean_amp < 1e-6:
        return False
    peak_amp = float(np.max(abs_audio))
    peak_ratio = peak_amp / mean_amp

    # Speech has bursts (high peak/mean); flat noise is ~1.5-2.5
    if peak_ratio < PEAK_RATIO_MIN:
        return False

    # Check energy variance — split into 100ms frames, measure variance
    frame_len = int(AUDIO_RATE * 0.1)
    n_frames = len(audio_f32) // frame_len
    if n_frames < 3:
        return True
    frame_energies = [float(np.mean(audio_f32[i*frame_len:(i+1)*frame_len]**2))
                      for i in range(n_frames)]
    energy_std = float(np.std(frame_energies))
    energy_mean = float(np.mean(frame_energies))
    if energy_mean < 1e-8:
        return False
    # Speech has varying energy across frames (CoV > 0.3)
    cov = energy_std / energy_mean
    return cov > 0.3


class SegmentManager:
    """Accumulates audio with adaptive squelch and speech quality gating."""

    def __init__(self, on_segment, freq_hz):
        self.on_segment = on_segment
        self.freq_hz = freq_hz
        self.buffer = []
        self.voice_active = False
        self.last_voice_time = 0
        self.segment_start = None
        # Adaptive noise floor (RMS)
        self.noise_floor = None  # None = calibrating
        self._calibration_samples = []
        self._calibration_chunks = 20  # 2 seconds at 10 chunks/sec
        self._squelch_linear = 10 ** (SQUELCH_SNR_DB / 20)  # dB → linear multiplier
        self._chunk_count = 0
        log.info("Squelch: %d dB above noise floor (linear x%.1f), calibrating %d chunks...",
                 SQUELCH_SNR_DB, self._squelch_linear, self._calibration_chunks)

    def feed(self, chunk_f32):
        rms = float(np.sqrt(np.mean(chunk_f32**2)))
        now = time.monotonic()
        self._chunk_count += 1

        # Calibration phase — measure noise floor from first N chunks
        if self.noise_floor is None:
            self._calibration_samples.append(rms)
            if len(self._calibration_samples) >= self._calibration_chunks:
                # Use median to reject any speech that happened during cal
                self.noise_floor = float(np.median(self._calibration_samples))
                adaptive_thresh = max(VAD_THRESHOLD, self.noise_floor * self._squelch_linear)
                log.info("Noise floor calibrated: %.6f | Squelch threshold: %.6f on %.3f MHz",
                         self.noise_floor, adaptive_thresh, self.freq_hz / 1e6)
                self._calibration_samples = []
            return  # skip processing during calibration

        # Adaptive threshold = noise_floor * squelch_multiplier, but never below absolute minimum
        adaptive_thresh = max(VAD_THRESHOLD, self.noise_floor * self._squelch_linear)
        is_voice = rms > adaptive_thresh

        if not is_voice and not self.voice_active:
            # Update noise floor estimate (only when gate is closed)
            self.noise_floor = (1 - NOISE_ALPHA) * self.noise_floor + NOISE_ALPHA * rms
            # Log noise floor periodically (every ~30s at 10 chunks/sec)
            if self._chunk_count % 300 == 0:
                log.info("Noise floor: %.6f | Squelch threshold: %.6f | Current RMS: %.6f",
                         self.noise_floor, adaptive_thresh, rms)

        if is_voice:
            self.last_voice_time = now
            if not self.voice_active:
                log.info("SQUELCH OPEN (rms=%.4f, thresh=%.4f, noise=%.5f) on %.3f MHz",
                         rms, adaptive_thresh, self.noise_floor, self.freq_hz / 1e6)
                self.voice_active = True
                self.segment_start = datetime.datetime.now(datetime.timezone.utc)

        if self.voice_active:
            self.buffer.append(chunk_f32)
            duration = sum(len(c) for c in self.buffer) / AUDIO_RATE
            gate_closed = (now - self.last_voice_time) > VAD_HOLD_S
            too_long = duration >= SEGMENT_MAX_S

            if gate_closed or too_long:
                segment = np.concatenate(self.buffer)
                self.buffer = []
                self.voice_active = False
                seg_dur = len(segment) / AUDIO_RATE
                # Pre-transcription quality gate
                if segment_is_speech(segment):
                    log.info("SQUELCH CLOSE — speech segment %.1fs on %.3f MHz",
                             seg_dur, self.freq_hz / 1e6)
                    self.on_segment(segment, self.segment_start, self.freq_hz)
                else:
                    log.info("SQUELCH CLOSE — rejected noise segment %.1fs on %.3f MHz",
                             seg_dur, self.freq_hz / 1e6)


# ──────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ──────────────────────────────────────────────────────────────────────────────

stt_queue = queue.Queue(maxsize=8)
shutdown = threading.Event()


def stt_worker():
    """Background thread: pull segments, transcribe, send to mesh."""
    while not shutdown.is_set():
        try:
            segment, ts, freq_hz = stt_queue.get(timeout=1)
        except queue.Empty:
            continue
        try:
            text = transcribe(segment)
            if text:
                callsigns = extract_callsigns(text)
                adsb_matches = lookup_adsb(callsigns)
                if adsb_matches:
                    log.info("ADS-B match: %s", adsb_matches)
                text = annotate_transcript(text, adsb_matches)
            send_to_mesh(text, ts, freq_hz)
        except Exception as e:
            log.exception("STT/mesh error: %s", e)
        finally:
            stt_queue.task_done()


def normalize_audio(audio_f32):
    """Normalise audio to [-1, 1] for archiving and transcription."""
    peak = np.max(np.abs(audio_f32))
    if peak > 1e-6:
        return audio_f32 / peak
    return audio_f32


def on_segment(audio, timestamp, freq_hz):
    """Called from main thread when VAD emits a voice segment."""
    audio = normalize_audio(audio)
    archive_segment(audio, timestamp, freq_hz)
    try:
        stt_queue.put_nowait((audio, timestamp, freq_hz))
    except queue.Full:
        log.warning("STT queue full, dropping segment")


def run(args):
    channels = args.channels
    log.info("Starting VHF pipeline: %d channels %s, sdr_center=%d Hz, rtl_tcp=%s:%d",
             len(channels), [c/1e6 for c in channels], SDR_CENTER_HZ,
             RTL_TCP_HOST, RTL_TCP_PORT)

    client = RtlTcpClient(RTL_TCP_HOST, RTL_TCP_PORT, SDR_CENTER_HZ, SAMPLE_RATE)
    client.connect()

    # Create a channelized demodulator + VAD for each channel
    demods = []
    seg_mgrs = []
    for ch_freq in channels:
        demod = ChannelDemod(SDR_CENTER_HZ, ch_freq, SAMPLE_RATE, AUDIO_RATE)
        seg_mgr = SegmentManager(on_segment=on_segment, freq_hz=ch_freq)
        demods.append(demod)
        seg_mgrs.append(seg_mgr)
        log.info("Channel: %.3f MHz (offset %+.1f kHz from SDR center)",
                 ch_freq / 1e6, (ch_freq - SDR_CENTER_HZ) / 1000)

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
            # Feed same IQ chunk to all channel demodulators
            for demod, seg_mgr in zip(demods, seg_mgrs):
                audio = demod.process(raw)
                seg_mgr.feed(audio)
    except ConnectionError as e:
        log.error("Connection lost: %s", e)
    finally:
        shutdown.set()
        client.close()
        stt_thread.join(timeout=10)
        log.info("Pipeline stopped.")


# Anchorage-area VHF channels within the vhf_air_low profile (117.85–120.25 MHz)
ANC_CHANNELS = {
    "ANC_TWR":   118_600_000,   # Anchorage Tower
    "ATIS":      118_400_000,   # ATIS
    "ANC_GND":   118_850_000,   # Anchorage Ground
    "ANC_CLR":   119_900_000,   # Clearance Delivery
}


def main():
    global WHISPER_MODEL, VAD_THRESHOLD
    parser = argparse.ArgumentParser(description="DOT-VHF pipeline")
    parser.add_argument("--channels", type=str,
                        default="118600000",
                        help="Comma-separated channel frequencies in Hz "
                             "(default: 118600000 = ANC Tower). "
                             f"Available: {', '.join(f'{k}={v}' for k,v in ANC_CHANNELS.items())}")
    parser.add_argument("--model", default=WHISPER_MODEL,
                        help="Whisper model (default: %(default)s)")
    parser.add_argument("--vad-threshold", type=float, default=VAD_THRESHOLD,
                        help="VAD RMS threshold (default: %(default)s)")
    args = parser.parse_args()

    WHISPER_MODEL = args.model
    VAD_THRESHOLD = args.vad_threshold

    # Parse channel list
    args.channels = [int(c.strip()) for c in args.channels.split(",")]

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    run(args)


if __name__ == "__main__":
    main()
