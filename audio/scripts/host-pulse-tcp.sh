#!/bin/sh
# Start PulseAudio on the host with TCP port 4713 for Docker producers.
# Required on macOS / Windows before ./play.sh (Linux uses a Unix socket).
#
#   brew install pulseaudio    # macOS
#   ./scripts/host-pulse-tcp.sh
#
# Producers connect with:  PULSE_SERVER=tcp:host.docker.internal:4713
# (play.sh sets this automatically on non-Linux hosts.)
set -e

PORT="${PULSE_TCP_PORT:-4713}"

if ! command -v pulseaudio >/dev/null 2>&1; then
    echo "error: pulseaudio not found." >&2
    echo "  macOS:  brew install pulseaudio" >&2
    echo "  Linux:  use ./play.sh without this script (Unix socket)" >&2
    exit 1
fi

if pulseaudio --check 2>/dev/null; then
    echo "[pulse] stopping existing pulseaudio..."
    pulseaudio -k || true
    sleep 0.3
fi

echo "[pulse] starting pulseaudio..."
pulseaudio --daemonize --exit-idle-time=-1

if ! command -v pactl >/dev/null 2>&1; then
    echo "error: pactl not found (install pulseaudio package)" >&2
    exit 1
fi

# Remove stale module if we are re-running.
pactl unload-module module-native-protocol-tcp 2>/dev/null || true

pactl load-module module-native-protocol-tcp \
    "port=${PORT} auth-anonymous=1 listen=0.0.0.0"

echo "[pulse] TCP ready on port ${PORT} (auth-anonymous=1 — local dev only)"
echo "[pulse] Docker producers use:  PULSE_SERVER=tcp:host.docker.internal:${PORT}"
