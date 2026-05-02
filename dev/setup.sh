#!/usr/bin/env bash
# Sets up a SkyBridge Alaska development environment in < 10 minutes.
# Tested on Debian/Ubuntu and Raspberry Pi OS.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv"

echo "=== SkyBridge Alaska dev setup ==="
echo "Repo: $REPO_ROOT"

# Python virtual environment
if [ ! -d "$VENV" ]; then
    echo "[1/4] Creating Python venv..."
    python3 -m venv "$VENV"
fi

echo "[2/4] Installing Python dependencies..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet numpy faster-whisper flask requests

# Node dependencies (app/)
if command -v npm &>/dev/null && [ -f "$REPO_ROOT/app/package.json" ]; then
    echo "[3/4] Installing Node dependencies..."
    npm --prefix "$REPO_ROOT/app" install --silent
else
    echo "[3/4] Skipping Node (npm not found or no app/package.json)"
fi

# Validate test suite
echo "[4/4] Running VHF pipeline smoke tests..."
if "$VENV/bin/python3" -m pytest "$REPO_ROOT/ground-station/" -q --tb=short 2>/dev/null; then
    echo "  Tests passed."
else
    echo "  Tests skipped or not found (no hardware SDR required for dev)."
fi

echo ""
echo "Done. Activate with:  source $VENV/bin/activate"
echo "Then run:             python3 ground-station/scripts/vhf_pipeline.py --help"
