#!/bin/sh
# docker-entrypoint.sh — friendly subcommand wrapper around audio_brain.cli.
#
# Usage inside the container:
#   audio music /music/song.mp3        # play a file from a mounted volume
#   audio mic                          # capture from the microphone
#   audio midi                         # capture from a virtual loopback device
#   audio list-devices                 # enumerate PortAudio devices
#   audio shell                        # drop into /bin/sh for debugging
#
# Anything after the subcommand is forwarded verbatim to the Python CLI,
# so e.g. `audio music song.mp3 --no-osc` works the same as
# `python -m audio_brain.cli song.mp3 --no-osc`.
#
# Environment variables the entrypoint understands:
#   OSC_HOST          override --osc-host (compose default: receiver)
#   OSC_PORT          override --osc-port (default 9000)
#   PULSE_SERVER      Pulse socket (play.sh sets this)
#   PULSE_SINK        playback device name (Bluetooth, speakers, …)
#   PULSE_SOURCE      capture device name (mic; unused for music)
#   SENDER_EXTRA_ARGS extra flags for mic/midi (set by play.sh)

set -e

cmd="${1:-list-devices}"
if [ "$#" -gt 0 ]; then
    shift
fi

# Inject OSC overrides from env vars if the user already didn't pass them.
osc_extra=""
case " $* " in
    *" --osc-host "*) ;;
    *)
        if [ -n "${OSC_HOST:-}" ]; then
            osc_extra="$osc_extra --osc-host $OSC_HOST"
        fi
        ;;
esac
case " $* " in
    *" --osc-port "*) ;;
    *)
        if [ -n "${OSC_PORT:-}" ]; then
            osc_extra="$osc_extra --osc-port $OSC_PORT"
        fi
        ;;
esac

case "$cmd" in
    music|file|play|song)
        # Resolve relative paths against /music (the bind-mount target),
        # so the user can pass a bare filename like `song.mp3` instead
        # of the full container path `/music/song.mp3`. Falls back to
        # /app if /music isn't mounted.
        if [ -d /music ]; then
            cd /music
        fi
        exec python -m audio_brain.cli "$@" $osc_extra
        ;;
    mic|microphone)
        # SENDER_EXTRA_ARGS comes from play.sh (e.g. --monitor --replay).
        exec python -m audio_brain.cli --mic ${SENDER_EXTRA_ARGS:-} "$@" $osc_extra
        ;;
    midi|loopback|daw)
        exec python -m audio_brain.cli --midi ${SENDER_EXTRA_ARGS:-} "$@" $osc_extra
        ;;
    list-devices|devices|list|ls)
        exec python -m audio_brain.cli --list-devices
        ;;
    shell|bash|sh)
        exec /bin/sh
        ;;
    help|--help|-h)
        exec python -m audio_brain.cli --help
        ;;
    *)
        # Unknown subcommand → forward as-is so power users keep the
        # legacy flag interface (e.g. `docker run … /audio/song.mp3`).
        exec python -m audio_brain.cli "$cmd" "$@" $osc_extra
        ;;
esac
