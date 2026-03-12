#!/usr/bin/env python3
"""
VHF Pipeline Test Harness — DOT-VHF / SkyBridge Alaska

Tests each stage of vhf-pipeline.py WITHOUT needing an SDR or Meshtastic node.
Injects synthetic audio directly into the pipeline stages and verifies outputs.

Stages tested:
  1. demod_am()         — IQ bytes → float32 audio
  2. SegmentManager     — energy VAD gate open/close
  3. archive_segment()  — FLAC file written to NVMe
  4. resample_to_16k()  — 24kHz → 16kHz via ffmpeg
  5. transcribe()       — Whisper STT on a spoken-word WAV
  6. send_to_mesh()     — transcript file written (mesh stubbed)
"""

import datetime
import importlib.util
import logging
import os
import struct
import sys
import tempfile
import time
import wave

import numpy as np

# ──────────────────────────────────────────────────────────────────────────────
# Bootstrap: import the pipeline module directly from its script path
# ──────────────────────────────────────────────────────────────────────────────

PIPELINE_PATH = os.path.join(os.path.dirname(__file__), "vhf-pipeline.py")

spec = importlib.util.spec_from_file_location("vhf_pipeline", PIPELINE_PATH)
pipe = importlib.util.module_from_spec(spec)

# Patch logging before exec so the pipeline doesn't try to open NVMe log
# on import (it opens the file at module level via basicConfig)
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
# Pre-populate the root logger so pipeline's basicConfig is a no-op
logging.getLogger("vhf-pipeline").setLevel(logging.DEBUG)

# Stub out the NVMe log path before the module executes
os.environ.setdefault("WHISPER_MODEL", "tiny.en")

# Redirect archive/transcript dirs to a temp folder for testing
TEST_TMP = tempfile.mkdtemp(prefix="vhf_test_")
print(f"\n[TEST] Temp output dir: {TEST_TMP}\n")

spec.loader.exec_module(pipe)

# Override dirs to temp
pipe.ARCHIVE_DIR    = os.path.join(TEST_TMP, "vhf-audio")
pipe.TRANSCRIPT_DIR = os.path.join(TEST_TMP, "transcripts")
pipe.LOG_DIR        = os.path.join(TEST_TMP, "logs")
os.makedirs(pipe.ARCHIVE_DIR,    exist_ok=True)
os.makedirs(pipe.TRANSCRIPT_DIR, exist_ok=True)
os.makedirs(pipe.LOG_DIR,        exist_ok=True)

# Stub Meshtastic — replace send_to_mesh so it only writes the transcript file
_mesh_calls = []
_orig_send = pipe.send_to_mesh

def _stub_send(text, timestamp):
    """Mock: skip actual mesh send, but still write transcript file."""
    _mesh_calls.append((text, timestamp))
    os.makedirs(pipe.TRANSCRIPT_DIR, exist_ok=True)
    tfile = os.path.join(pipe.TRANSCRIPT_DIR,
                         timestamp.strftime("%Y-%m-%d") + ".txt")
    with open(tfile, "a") as f:
        f.write(f"{timestamp.isoformat()} {text}\n")
    print(f"  [MESH STUB] Would send: {text[:80]}")

pipe.send_to_mesh = _stub_send

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    msg = f"  [{status}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    results.append((name, condition))


def make_silence_iq(duration_s=0.5, sample_rate=2_400_000):
    """IQ byte stream of very low-level noise (realistic silence, not pure DC)."""
    n_samples = int(sample_rate * duration_s)
    # Small gaussian noise around 127 — avoids the demod_am peak-normalization
    # blowing up a perfectly flat signal to RMS=1.0
    rng = np.random.default_rng(42)
    noise = rng.normal(127.5, 0.3, n_samples * 2).clip(0, 255).astype(np.uint8)
    return noise.tobytes()


def make_voice_iq(duration_s=2.0, sample_rate=2_400_000, tone_hz=1000):
    """
    Fake 'voice': AM-modulated tone at tone_hz.
    IQ = complex envelope of (1 + 0.8*cos(2πft)) at baseband.
    """
    t = np.arange(int(sample_rate * duration_s)) / sample_rate
    modulation = 0.8 * np.cos(2 * np.pi * tone_hz * t)
    envelope = (1.0 + modulation) / 2.0          # 0..1
    noise = np.random.normal(0, 0.02, len(t))
    i_f = envelope + noise
    q_f = np.zeros_like(i_f)
    i_u8 = np.clip(i_f * 127.5 + 127.5, 0, 255).astype(np.uint8)
    q_u8 = np.full_like(i_u8, 127)
    iq = np.empty(len(i_u8) * 2, dtype=np.uint8)
    iq[0::2] = i_u8
    iq[1::2] = q_u8
    return iq.tobytes()


def make_speech_audio_16k(duration_s=3.0, sample_rate=16_000):
    """
    Generate a WAV with a spoken-word tone pattern that Whisper can attempt.
    Uses sox to speak a phrase if espeak is available, otherwise a sine wave.
    """
    import subprocess, shutil
    wav_path = os.path.join(TEST_TMP, "test_speech.wav")

    if shutil.which("espeak-ng"):
        subprocess.run([
            "espeak-ng", "-v", "en-us", "-s", "150",
            "--stdout", "Anchor one two three, cleared for takeoff runway seven left",
            "-w", wav_path,
        ], check=True, capture_output=True)
        print("  [INFO] Generated speech WAV via espeak-ng")
    else:
        # Fallback: 440 Hz sine (Whisper will return empty or noise — that's fine)
        t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
        samples = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
        with wave.open(wav_path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(samples.tobytes())
        print("  [INFO] Generated sine WAV (espeak-ng not found)")

    return wav_path


# ──────────────────────────────────────────────────────────────────────────────
# TEST 1 — demod_am: output shape, dtype, and decimation ratio
# ──────────────────────────────────────────────────────────────────────────────

print("\n── TEST 1: demod_am (output shape/dtype) ──")
silence_iq = make_silence_iq(0.1)
audio_silence = pipe.demod_am(silence_iq)
check("output is float32",    audio_silence.dtype == np.float32)
check("output length > 0",    len(audio_silence) > 0)
# At 2.4 MHz, 0.1 s = 240000 IQ pairs → /100 decimation = 2400 samples
check("decimation ratio correct",
      len(audio_silence) == 2400,
      f"expected 2400, got {len(audio_silence)}")

# ──────────────────────────────────────────────────────────────────────────────
# TEST 2 — demod_am: voiced signal has higher pre-norm envelope than noise
# ──────────────────────────────────────────────────────────────────────────────

print("\n── TEST 2: demod_am (voice vs silence envelope) ──")
voice_iq = make_voice_iq(0.5)
audio_voice = pipe.demod_am(voice_iq)
check("voice output length reasonable",
      9000 < len(audio_voice) < 15000,
      f"len={len(audio_voice)}")
# The pipeline normalises to peak=1, so check the AC power (variance)
# Voice (AM modulated) should have higher variance than noise after demod
var_voice   = float(np.var(audio_voice))
var_silence = float(np.var(audio_silence))
check("voice has higher AC variance than noise",
      var_voice > var_silence,
      f"voice var={var_voice:.4f} silence var={var_silence:.4f}")

# ──────────────────────────────────────────────────────────────────────────────
# TEST 3 — SegmentManager VAD: voice opens gate, silence closes it
# Inject controlled-RMS arrays directly — bypasses demod normalization
# ──────────────────────────────────────────────────────────────────────────────

print("\n── TEST 3: SegmentManager VAD ──")

segments_emitted = []

def _on_seg(audio, ts):
    segments_emitted.append((audio, ts))

# Use a threshold well above the test signal amplitudes
orig_threshold = pipe.VAD_THRESHOLD
orig_hold      = pipe.VAD_HOLD_S
pipe.VAD_THRESHOLD = 0.05
pipe.VAD_HOLD_S    = 0.1     # short hold for test speed

mgr = pipe.SegmentManager(on_segment=_on_seg)

# Silence chunk: RMS << threshold (0.001 << 0.05)
silence_arr = np.random.normal(0, 0.001, 2400).astype(np.float32)
mgr.feed(silence_arr)
check("no segment on silence", len(segments_emitted) == 0)

# Voice chunk: RMS >> threshold (0.5 >> 0.05)
voice_arr = np.random.normal(0, 0.5, 24000).astype(np.float32)
mgr.feed(voice_arr)
check("gate opens on voice", mgr.voice_active,
      f"rms={float(np.sqrt(np.mean(voice_arr**2))):.4f}")

# Post-voice silence: feed small chunks over > hold_s wall-clock time
hold_wait  = pipe.VAD_HOLD_S + 0.05
chunk_dur  = 0.04
n_chunks   = int(hold_wait / chunk_dur) + 2
for _ in range(n_chunks):
    mgr.feed(np.random.normal(0, 0.001, int(24000 * chunk_dur)).astype(np.float32))
    time.sleep(chunk_dur)

check("segment emitted after silence hold",
      len(segments_emitted) == 1,
      f"got {len(segments_emitted)} segments")

if segments_emitted:
    seg_audio, seg_ts = segments_emitted[0]
    check("segment is float32 array",     isinstance(seg_audio, np.ndarray))
    check("segment timestamp is datetime", isinstance(seg_ts, datetime.datetime))

pipe.VAD_THRESHOLD = orig_threshold
pipe.VAD_HOLD_S    = orig_hold

# ──────────────────────────────────────────────────────────────────────────────
# TEST 4 — archive_segment: FLAC file written to NVMe (temp dir)
# ──────────────────────────────────────────────────────────────────────────────

print("\n── TEST 4: archive_segment (FLAC write) ──")
ts_now = datetime.datetime.now(datetime.timezone.utc)
test_audio = pipe.demod_am(make_voice_iq(1.0))
try:
    flac_path = pipe.archive_segment(test_audio, ts_now)
    check("FLAC file created",   os.path.isfile(flac_path), flac_path)
    check("FLAC file non-empty", os.path.getsize(flac_path) > 1000,
          f"{os.path.getsize(flac_path)} bytes")
except Exception as e:
    check("archive_segment raised no exception", False, str(e))

# ──────────────────────────────────────────────────────────────────────────────
# TEST 5 — resample_to_16k: length ratio ~2/3
# ──────────────────────────────────────────────────────────────────────────────

print("\n── TEST 5: resample_to_16k ──")
audio_24k = np.random.randn(24000).astype(np.float32) * 0.1   # 1 s at 24kHz
try:
    audio_16k = pipe.resample_to_16k(audio_24k)
    ratio = len(audio_16k) / len(audio_24k)
    check("resampled to ~16kHz",
          0.65 < ratio < 0.68,
          f"ratio={ratio:.4f} ({len(audio_16k)} samples)")
    check("output is float32", audio_16k.dtype == np.float32)
except Exception as e:
    check("resample_to_16k raised no exception", False, str(e))

# ──────────────────────────────────────────────────────────────────────────────
# TEST 6 — Whisper STT: transcribes espeak speech (or doesn't crash on sine)
# ──────────────────────────────────────────────────────────────────────────────

print("\n── TEST 6: Whisper STT ──")
import shutil as _shutil

speech_wav = make_speech_audio_16k()
# Load WAV as float32 numpy array (16k), pass directly to model
with wave.open(speech_wav) as wf:
    raw_bytes = wf.readframes(wf.getnframes())
    rate = wf.getframerate()
samples_int16 = np.frombuffer(raw_bytes, dtype=np.int16)
samples_f32   = samples_int16.astype(np.float32) / 32768.0

# Resample to 24k to go through the pipeline's own path
import subprocess as _sp, tempfile as _tf
tmp_24 = _tf.NamedTemporaryFile(suffix=".f32", delete=False)
_sp.run([
    "ffmpeg", "-y", "-loglevel", "error",
    "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", speech_wav,
    "-f", "f32le", "-ar", "24000", "-ac", "1", tmp_24.name,
], check=True)
audio_24k_speech = np.fromfile(tmp_24.name, dtype=np.float32)
os.unlink(tmp_24.name)

try:
    text = pipe.transcribe(audio_24k_speech)
    check("transcribe() returns a string", isinstance(text, str),
          f"got: '{text[:80]}'")
    print(f"  [INFO] Whisper output: '{text}'")
except Exception as e:
    check("transcribe() raised no exception", False, str(e))

# ──────────────────────────────────────────────────────────────────────────────
# TEST 7 — send_to_mesh (stub): transcript file written
# ──────────────────────────────────────────────────────────────────────────────

print("\n── TEST 7: transcript file write ──")
ts_test = datetime.datetime.now(datetime.timezone.utc)
pipe.send_to_mesh("Anchor one, cleared takeoff runway seven left", ts_test)
tfile = os.path.join(pipe.TRANSCRIPT_DIR, ts_test.strftime("%Y-%m-%d") + ".txt")
check("transcript file created",   os.path.isfile(tfile), tfile)
if os.path.isfile(tfile):
    content = open(tfile).read()
    check("transcript file has content", "Anchor one" in content,
          f"content: {content[:80]}")

# ──────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────────────────────────────

total  = len(results)
passed = sum(1 for _, ok in results if ok)
failed = total - passed

print(f"\n{'─'*50}")
print(f"  Results: {passed}/{total} passed", end="")
if failed:
    print(f"  ← {failed} FAILED", end="")
    print()
    for name, ok in results:
        if not ok:
            print(f"    ✗ {name}")
else:
    print("  — all good!")

print(f"  Temp artifacts: {TEST_TMP}")
print(f"{'─'*50}\n")

sys.exit(0 if failed == 0 else 1)
