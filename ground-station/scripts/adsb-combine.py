#!/usr/bin/env python3
"""
SkyBridge ADS-B Combiner — merges local readsb + ADSB.fi statewide feed.
Writes combined aircraft.json to /run/combine1090/ every 8 seconds.
tar1090 reads from this directory for the enhanced view.
"""

import json
import os
import time
import urllib.request

LOCAL_JSON = "/run/readsb/aircraft.json"
OUTPUT_DIR = "/run/combine1090"
OUTPUT_JSON = os.path.join(OUTPUT_DIR, "aircraft.json")
INTERVAL = 8  # seconds — matches tar1090 INTERVAL

# Two overlapping 250nm circles covering ~500nm of Southcentral Alaska
ADSB_FI_CIRCLES = [
    (61.17, -150.0, 250),   # Anchorage / Southcentral
    (63.5,  -150.0, 250),   # Fairbanks / Interior / North Slope overlap
]
ADSB_FI_CACHE = {"data": {}, "ts": 0}
ADSB_FI_TTL = 10  # don't hit API more than every 10s


def fetch_local():
    """Read local readsb aircraft."""
    try:
        with open(LOCAL_JSON) as f:
            data = json.load(f)
        return data.get("now", time.time()), data.get("messages", 0), data.get("aircraft", [])
    except Exception:
        return time.time(), 0, []


def fetch_adsbfi():
    """Fetch from ADSB.fi with rate limiting."""
    now = time.time()
    if ADSB_FI_CACHE["data"] and (now - ADSB_FI_CACHE["ts"]) < ADSB_FI_TTL:
        return ADSB_FI_CACHE["data"]

    remote = {}
    for lat, lon, dist in ADSB_FI_CIRCLES:
        try:
            url = f"https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{dist}"
            req = urllib.request.Request(url, headers={"User-Agent": "SkyBridge-AK/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            for ac in data.get("aircraft", []):
                hx = ac.get("hex", "")
                if hx and "lat" in ac and "lon" in ac:
                    # Mark as adsb_icao_nt (non-local / network) so tar1090
                    # knows it's not from our receiver
                    if hx not in remote:
                        ac["type"] = ac.get("type", "adsb_icao")
                        remote[hx] = ac
        except Exception as e:
            print(f"[adsb-combine] ADSB.fi error ({lat},{lon}): {e}")
        # Small delay between circles to avoid rate limits
        if len(ADSB_FI_CIRCLES) > 1:
            time.sleep(1)

    ADSB_FI_CACHE["data"] = remote
    ADSB_FI_CACHE["ts"] = time.time()
    return remote


def merge():
    """Merge local + ADSB.fi, local wins on conflicts."""
    now_ts, messages, local_ac = fetch_local()
    remote = fetch_adsbfi()

    merged = {}

    # Remote first (lower priority)
    for hx, ac in remote.items():
        merged[hx] = ac

    # Local overwrites, but enrich with remote metadata
    for ac in local_ac:
        hx = ac.get("hex", "")
        if not hx:
            continue
        if hx in merged:
            # Keep remote metadata fields that local doesn't have
            remote_ac = merged[hx]
            for key in ("r", "t", "desc", "ownOp", "year"):
                if not ac.get(key) and remote_ac.get(key):
                    ac[key] = remote_ac[key]
        merged[hx] = ac

    aircraft = list(merged.values())

    output = {
        "now": time.time(),
        "messages": messages,
        "aircraft": aircraft,
    }
    return output


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"[adsb-combine] Starting — writing to {OUTPUT_JSON}")
    print(f"[adsb-combine] Local: {LOCAL_JSON}")
    print(f"[adsb-combine] ADSB.fi circles: {len(ADSB_FI_CIRCLES)} x 250nm")

    while True:
        try:
            data = merge()
            ac_count = len(data["aircraft"])
            local_count = sum(1 for a in data["aircraft"]
                              if a.get("hex") in {ac.get("hex") for ac in fetch_local()[2]})

            # Atomic write
            tmp = OUTPUT_JSON + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, OUTPUT_JSON)

            print(f"[adsb-combine] {ac_count} aircraft ({local_count} local, "
                  f"{ac_count - local_count} ADSB.fi)")
        except Exception as e:
            print(f"[adsb-combine] Error: {e}")

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
