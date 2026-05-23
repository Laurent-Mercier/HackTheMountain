#!/bin/sh
# Pick laptop built-in mic + Bluetooth playback for Docker/Pulse producers.
# Sets PULSE_SOURCE and PULSE_SINK in the environment (caller must export).

set -e

if ! command -v pactl >/dev/null 2>&1; then
    echo "[pulse-split] need pactl (PipeWire/Pulse on the host)" >&2
    exit 1
fi

# Playback: first non-monitor sink that looks like Bluetooth.
PULSE_SINK=$(pactl list sinks short 2>/dev/null | awk '
    $0 !~ /\.monitor$/ {
        n = tolower($0)
        if (n ~ /bluez|bluetooth|blue_tooth/) {
            print $2
            exit
        }
    }
')

# Capture: score built-in sources (PipeWire HiFi often has Mic1 + Mic2).
PULSE_SOURCE=$(pactl list sources short 2>/dev/null | awk '
    BEGIN { best = 0; pick = "" }
    $0 !~ /\.monitor$/ {
        n = tolower($0)
        id = $2
        if (n ~ /bluez|bluetooth|blue_tooth/) next
        if (n ~ /monitor of/) next
        if (n !~ /analog|built-in|built_in|internal|microphone|alsa_input|mic|hifi/) next
        score = 10
        if (n ~ /mic1|microphone builtin|internal mic/) score += 40
        if (n ~ /mic2|headset|line|aux|dock/) score -= 15
        if (n ~ /echo-cancel/) score -= 5
        if (score > best) {
            best = score
            pick = id
        }
    }
    END {
        if (pick != "") print pick
    }
')

if [ -z "$PULSE_SINK" ]; then
    echo "[pulse-split] no Bluetooth sink found — connect earbuds first" >&2
    echo "[pulse-split] sinks:" >&2
    pactl list sinks short >&2
    exit 1
fi

if [ -z "$PULSE_SOURCE" ]; then
    echo "[pulse-split] no built-in mic source found" >&2
    echo "[pulse-split] sources:" >&2
    pactl list sources short >&2
    exit 1
fi

export PULSE_SINK
export PULSE_SOURCE
echo "[pulse-split] capture (laptop mic): $PULSE_SOURCE"
echo "[pulse-split] playback (Bluetooth):   $PULSE_SINK"
