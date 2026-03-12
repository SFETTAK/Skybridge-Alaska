# DOT-VHF Software Stack

**Purpose:** Define the software choices for the VHF Radio Pi (DOT-VHF): own ADSB tracking/logging (with optional sharing), VHF record/clean/transcribe, local weather → Meshtastic, and use of the Flycatcher’s second antenna.

**Hardware context:** Raspberry Pi 5 Model B; Nooelec Flycatcher (dual-channel: 1090 + 978); 3× RTL2838 (RTL-SDR) dongles; ROKland Meshtastic nodes; 2 TB NVMe for logging. Optional: Hailo-8 via Pi 5 AI HAT+ (currently disabled).

---

## DOT-VHF: What we want (requirements)

| # | Want | Notes |
|---|------|--------|
| 1 | **Own ADSB tracking and logging** | Local capture and storage on 2 TB NVMe; full control of our data. |
| 2 | **Option to share ADSB to online services** | If we choose: feed to FlightAware, FlightRadar24, ADSBexchange, etc. (e.g. Beast/MLAT); opt-in, not required. |
| 3 | **VHF audio: recorded, cleaned up, transcribed** | Capture aviation VHF (118–137 MHz); clean/denoise; run STT (CPU or Hailo when enabled); transcripts to mesh + NVMe. |
| 4 | **Local weather on Meshtastic stream** | Weather system at install location; get API for that weather; add weather data into the Meshtastic stream. (API TBD.) |
| 5 | **Flycatcher 2nd channel** | Same radio as ch1 (SAW filter); **can’t do analogue**. Use for 978 UAT (see below). |

### Current DOT-VHF hardware (as of 2025-03)

| Item | Status |
|------|--------|
| **Hailo-8 AI HAT+** | **Disabled** — not in use at the moment; can be re-enabled later for accelerated STT. |
| **Storage** | **2 TB NVMe** — used for logging (ADSB, SDR/OpenWebRX, transcript logs, etc.). Plenty of space for long-term capture and replay. |

Until the Hailo HAT is re-enabled, STT can run on **CPU Whisper** (slower but works) or the pipeline can focus on **logging VHF audio** to NVMe and transcribe later (or when Hailo is on). The 2 TB NVMe is well-suited for that.

---

## Scope (agreed)

- **SDR:** Multiple RTL-SDRs — one dedicated to ADSB (1090 MHz), other(s) for VHF (118–137 MHz aviation).
- **VHF → text:** Capture VHF voice, run **speech-to-text (STT)** on-device, send **historical transcript text** over the Meshtastic mesh.
- **Accelerator:** **Hailo-8** (Pi 5 AI HAT+) for Whisper STT when enabled; **currently disabled** — use CPU Whisper or log-only until Hailo is on.
- **Web SDR UI:** **OpenWebRX** for the “nice web terminal” (tuning, waterfall, listening) — [jketterl/openwebrx](https://github.com/jketterl/openwebrx).

---

## 1. OpenWebRX (web SDR terminal)

**What it is:** Web-based SDR receiver; one or more RTL-SDRs; waterfall, demodulation (NFM, WFM, etc.), optional digital decoders. Access via browser — no desktop needed.

**Why here:** Gives operators a single “web terminal” to tune VHF (and optionally other bands), see activity, and listen. Fits SkyBridge ground-station “optional VHF SDR” role.

**Relevant details:**
- Supports **multiple receivers** on the same host (e.g. different ports/profiles per RTL-SDR).
- **RTL-SDR** is supported; Pi 5 is sufficient (Pi 3B+ minimum for some digital voice).
- Config: `/etc/openwebrx`, web UI under “Settings”; devices and profiles for each dongle.
- **Limitation:** Demodulated audio is streamed to the browser. OpenWebRX does **not** expose a local audio pipe for an external STT process. So the **STT pipeline needs its own SDR path** (see below).

**Install options:**  
- [Setup Guide (jketterl)](https://github.com/jketterl/openwebrx/wiki/Setup-Guide): package repo (Debian/Ubuntu, aarch64), Docker, or SD card image.  
- On Pi 5 with existing OS, package or Docker is appropriate.

**Suggested use on DOT-VHF:**  
- Reserve **one RTL-SDR** for OpenWebRX (e.g. VHF aviation profile 118–137 MHz, or split across profiles).  
- Use for monitoring and tuning; do **not** rely on it to feed STT.

---

## 2. VHF → STT pipeline (Whisper; Hailo-8 when enabled)

**Goal:** VHF RF → demodulated audio → **on-device STT** → text → **Meshtastic** (historical transcript messages). Audio and transcripts can be logged to the 2 TB NVMe.

**Flow:**
```
RTL-SDR (VHF) → rtl_fm (or similar) → demod audio → [optional VAD] → Whisper (CPU or Hailo-8) → text → Meshtastic
                                                                   → log to NVMe (raw + transcripts)
```

**Components:**

| Layer        | Role                         | Options |
|-------------|------------------------------|---------|
| **SDR capture** | Dedicated dongle, VHF band   | `rtl_fm` (narrow FM, 118–137 MHz), or `csdr`-based pipeline; fixed device by serial/bus. |
| **Audio**       | Demodulated audio to STT     | Pipe (stdin) or ringbuffer; 16 kHz mono typical for Whisper. Optionally write to NVMe for replay. |
| **STT**         | Speech → text                | **Whisper** (tiny/small): **CPU** for now (Hailo disabled), or **Hailo-8** when Hailo HAT is re-enabled ([hailocs/hailo-whisper](https://github.com/hailocs/hailo-whisper)). |
| **Delivery**    | Text onto mesh               | Meshtastic (serial or network API) — send transcript lines as mesh messages. |
| **Logging**     | Persistent storage           | **2 TB NVMe** — ADSB logs, VHF audio segments, transcripts, OpenWebRX recordings. |

**Hailo-8 (when re-enabled):**
- **Hardware:** Pi 5 + AI HAT+ (Hailo-8 / Hailo-8L). Currently **disabled** on DOT-VHF; slot may be used for NVMe.
- **Software:** [hailo-ai/hailo-rpi5-examples](https://github.com/hailo-ai/hailo-rpi5-examples); [hailocs/hailo-whisper](https://github.com/hailocs/hailo-whisper) for Whisper on Hailo-8. When the HAT is on again, STT can switch from CPU to Hailo for lower latency.
- **Model:** Whisper-tiny (or small) for latency vs accuracy on the edge.

**Current mode (Hailo disabled):** Run Whisper on CPU, and/or **log VHF audio to NVMe** and transcribe in batches or when Hailo is re-enabled. The 2 TB NVMe makes “record now, transcribe later” practical.

**VHF “cleaned up”:** Denoise/clean the demodulated audio before STT (e.g. high-pass filter, noise gate, or light spectral subtraction) so transcripts are better; keep raw and cleaned copies on NVMe if desired.

**Why separate SDR for STT:**  
OpenWebRX doesn’t provide a local audio stream. Using a **dedicated RTL-SDR + rtl_fm** (or equivalent) keeps the STT pipeline simple and independent of the web UI.

---

## 3. Local weather → Meshtastic stream

**What we want:** Weather from the install-location weather system added to the Meshtastic stream so the mesh carries local conditions (temp, wind, pressure, etc.) for pilots and other nodes.

**Status:** API for the on-site weather system is TBD. Once we have the API:
- Poll or subscribe to the weather API.
- Format data for the mesh (e.g. TAIGA-style or simple text).
- Send updates to the Meshtastic node (serial or network API) on a schedule (e.g. every 5–15 min) or on change.

**Next step:** Get the weather system’s API (vendor, endpoint, auth, units). Then we can wire a small service (e.g. Python or Node) that fetches → formats → sends to Meshtastic.

---

## 4. ADSB: own tracking, logging, optional sharing

**What we want:**
- **Own tracking and logging** — All ADSB data captured and stored locally on the 2 TB NVMe (readsb / dump1090-fa, or similar; JSON/CSV logs, history).
- **Optional sharing** — If we choose, feed to online services (e.g. FlightAware, FlightRadar24, ADSBexchange) via their feeder apps (Beast format, MLAT, etc.). Opt-in; not required for the stack to work.

**Hardware:** Flycatcher handles ADSB only. **Both channels are tied to the same radio hardware (SAW filter)** — they can’t do analogue VHF. That’s why we added a **separate RTL-SDR** (or more) for analogue VHF (118–137 MHz). So: Flycatcher = 1090 + 978; standalone dongle(s) = VHF.

**Verified (Nooelec):** The [Nooelec Flycatcher](https://www.nooelec.com/store/flycatcher.html) is specified only for **ADS-B (1090 MHz) and UAT (978 MHz)** — "Designed to receive both ADS-B (1090 MHz) signals and UAT (978 MHz)." No mention of 118–137 MHz or voice; the product is for airplane tracking, flight tracking, and weather monitoring (FIS-B over UAT). The 118–137 MHz aviation voice band is a different frequency range and would require different front-end hardware. **The Flycatcher cannot do voice traffic**; analogue VHF requires a separate SDR.

**Flycatcher 2nd channel: 978 MHz UAT (only option)**

The Flycatcher is built for **1090 MHz (ADS-B ES) + 978 MHz (UAT)**. Both channels are SAW-filtered for those bands — **no analogue**, so the 2nd channel isn’t “extra” to repurpose; it’s the 978 UAT channel. Use it for that:

| Channel | Use | Why |
|---------|-----|-----|
| **Channel 1** | **1090 MHz ADS-B ES** | Commercial + international; primary traffic. |
| **Channel 2** | **978 MHz UAT** | US general aviation; FIS-B weather over UAT; same radio type as ch1, can’t do analogue. |

That gives you full ADSB (1090 + 978) on the Flycatcher; **analogue VHF is on the additional SDR(s)** we added because the Flycatcher can’t read it.

---

**VHF SDR on DOT-VHF: RTL-SDR.com Blog V4 (R828D, RTL2832U)**  
- **Seen on Pi:** `lsusb` shows `iManufacturer: RTLSDRBlog`, `iProduct: Blog V4` (one of the three RTL2838 devices; the other two are FlyCatcher_ADS_B and FlyCatcher_UAT).  
- **Verified for VHF analogue voice:** The [RTL-SDR Blog V4](https://www.rtl-sdr.com/v4/) is a **wideband SDR** (500 kHz–1.766 GHz) with an **R828D tuner** and triplexed inputs for HF, VHF, and UHF. The aviation band **118–137 MHz** is within range; no SAW filter locking it to 1090/978, so it can demodulate analogue NFM voice. RTL-SDR.com's V4 guide lists "Airband Radio" as a supported use. **Conclusion: the Blog V4 can read VHF analogue voice.**  
- **Linux note:** The V4 needs **updated drivers** (Osmocom or RTL-SDR Blog branch); standard librtlsdr may give wrong frequencies or no signal. See [RTL-SDR.com V4 Users Guide](https://www.rtl-sdr.com/v4/) for Linux (build from osmocom/rtl-sdr, blacklist DVB-T).

---

## 5. Device assignment

**Flycatcher (HAT, 2 channels):**
| Channel | Antenna / use   | Software / role                    |
|---------|-----------------|-------------------------------------|
| 1       | 1090 MHz ADSB   | readsb / dump1090-fa — tracking, logging, optional feed |
| 2       | **978 MHz UAT** | 978 decoder (e.g. dump978, or readsb with 978) — same logging/sharing pipeline |

**Standalone RTL-SDR dongles:**  
| Dongle | Role              | Software                    |
|--------|-------------------|-----------------------------|
| **RTL-SDR.com Blog V4** (RTLSDRBlog / Blog V4) | Web SDR terminal and/or VHF → STT | OpenWebRX (VHF 118–137 MHz) and/or rtl_fm → clean → Whisper → Meshtastic + NVMe |

(Other USB RTL-SDRs if present can be spare or second VHF instance; pin by udev using manufacturer/product.)

Use udev rules (e.g. by `idVendor`/`idProduct` and USB port/serial) so each device gets a stable name and the right service uses the right dongle.

---

## 6. Software list (summary)

| Component | Purpose | Status |
|-----------|---------|--------|
| **OpenWebRX** | Web SDR terminal (VHF waterfall/tuning) | Running on port 8073; 3 aviation band profiles |
| **rtl_connector + rtltcp_compat** | Shared IQ access for Blog V4 | Port `127.0.0.1:1235` active; pipeline + OpenWebRX share one dongle |
| **vhf-pipeline.py** | VHF AM capture -> archive -> STT -> Meshtastic | `/home/blastly/scripts/vhf-pipeline.py`; systemd service enabled |
| **faster-whisper 1.2.1** | CPU STT for voice segments | `tiny.en` model; venv `/home/blastly/vhf-pipeline-venv/` |
| **sox + ffmpeg** | Audio conversion (PCM->FLAC, 24k->16k resample) | System packages installed |
| **meshtastic 2.7.8** | Mesh delivery of transcripts | In pipeline venv; `MESH_HOST` env var -- node connection pending onsite |
| **readsb + tar1090** | ADSB 1090 MHz tracking + web map | Pinned to device 2 (FlyCatcher_ADS_B); tar1090 at `/tar1090` |
| **rclone 1.73.1** | NVMe -> central server backup | Script + systemd timer (6h); remote `skybridge-central` pending config |
| **1.8 TB NVMe** | Primary data storage | Mounted `/mnt/nvme`; `{vhf-audio,transcripts,adsb,logs,backup-staging}` |

---

## 7. Open items (pending onsite)

1. **Meshtastic node:** Plug into Pi USB or set `MESH_HOST=<node-ip>` in `/etc/systemd/system/vhf-pipeline.service`. Pipeline logs transcripts to NVMe now; mesh delivery activates when node is connected.
2. **rclone remote:** `rclone config` -> create remote named `skybridge-central` (SFTP/S3/B2). Timer picks it up automatically.
3. **Antenna on roof:** VAD threshold (default 0.005 RMS) may need tuning with real RF. Adjust `--vad-threshold` in service `ExecStart` or run manually first to test.
4. **Receiver location:** `sudo readsb-set-location <lat> <lon>` once onsite.
5. **dump978 / 978 UAT:** FlyCatcher_UAT (device 1) free; install `dump978` when UAT reception wanted.
6. **Weather API:** On-site weather system vendor/endpoint TBD; wire to Meshtastic stream when available.
7. **ADSB sharing:** Optional feeders (FlightAware, ADSBexchange, FR24) not installed; add if desired.
8. **Hailo-8:** Disabled. When re-enabled, change `device="cpu"` to `"hailo"` in `WhisperModel()` call in `vhf-pipeline.py`.

---

*Pipeline is built and services are enabled. Remaining items are all "connect hardware onsite" or "configure credentials" -- no more software to write for core VHF -> STT -> Meshtastic flow.*
