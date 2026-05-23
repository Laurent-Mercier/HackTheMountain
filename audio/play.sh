#!/bin/sh
# play.sh — build/start ht-receiver + ht-audio (music / mic / midi).
#
# Host audio goes through Pulse/PipeWire (see README.md). Use --pick-audio,
# --use-route, or --split-audio to choose devices. Flags may appear before
# or after the song path:  ./play.sh music "a b.mp3" --use-route
set -e

# Resolve play.sh directory absolutely (symlink-safe) so .pulse-route.env
# is always found next to this script, regardless of caller cwd.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"
PICK_SCRIPT="$SCRIPT_DIR/scripts/interactive-pulse-pick.sh"
ROUTE_FILE="${PULSE_ROUTE_FILE:-$SCRIPT_DIR/.pulse-route.env}"
export PULSE_ROUTE_FILE="$ROUTE_FILE"

print_help() {
    cat <<'EOF'
Usage:
  ./play.sh                           # music, default song
  ./play.sh music ["song.mp3"]
  ./play.sh music "song.mp3" --use-route   # play on saved Bluetooth sink
  ./play.sh music "song.mp3" --pick-audio  # pick playback device first
  ./play.sh mic --monitor              # live monitor (no replay on stop)
  ./play.sh mic --monitor --pick-audio # interactive mic + output selection
  ./play.sh mic --monitor --split-audio
                                       # auto laptop mic → Bluetooth out
  ./play.sh midi
  ./play.sh pick                       # interactive audio device menu
  ./play.sh list-devices               # PortAudio indices (in container)
  ./play.sh pulse-routes               # host Pulse sinks/sources (pactl)
  ./play.sh help

Audio selection (host Pulse/PipeWire via pactl):
  --pick-audio, -i     prompt for playback output; mic/midi also pick capture
  --save-route         with --pick-audio, write audio/.pulse-route.env
  --use-route          load audio/.pulse-route.env (skip prompts)

Cross-platform audio:
  Linux     — PipeWire/Pulse Unix socket (automatic)
  macOS/Win — run ./scripts/host-pulse-tcp.sh once, then ./play.sh
EOF
}

# Source audio/.pulse-route.env (PULSE_SINK / PULSE_SOURCE) written by --save-route.
load_saved_route() {
    if [ ! -f "$ROUTE_FILE" ]; then
        echo "[play] no saved route at:" >&2
        echo "       $ROUTE_FILE" >&2
        echo "[play] create one with:  ./play.sh mic --monitor --pick-audio --save-route" >&2
        return 1
    fi
    # shellcheck disable=SC1090
    set -a
    # shellcheck source=/dev/null
    . "$ROUTE_FILE"
    set +a
    echo "[play] loaded route from $ROUTE_FILE"
    [ -n "${PULSE_SINK:-}" ] && echo "[play]   sink:   $PULSE_SINK"
    [ -n "${PULSE_SOURCE:-}" ] && echo "[play]   source: $PULSE_SOURCE"
    return 0
}

sync_pulse_defaults() {
    if command -v pactl >/dev/null 2>&1; then
        if [ -z "${PULSE_SINK:-}" ]; then
            _sink=$(pactl get-default-sink 2>/dev/null || true)
            [ -n "$_sink" ] && export PULSE_SINK="$_sink"
        fi
        if [ -z "${PULSE_SOURCE:-}" ]; then
            _src=$(pactl get-default-source 2>/dev/null || true)
            [ -n "$_src" ] && export PULSE_SOURCE="$_src"
        fi
    fi
}

setup_compose_env() {
    COMPOSE="docker compose -f docker-compose.yml"
    export OSC_HOST="${OSC_HOST:-receiver}"
    export OSC_PORT="${OSC_PORT:-9000}"

    case "$(uname -s)" in
        Linux)
            COMPOSE="$COMPOSE -f docker-compose.linux.yml"
            export PULSE_SERVER="${PULSE_SERVER:-unix:/run/pulse/native}"
            if [ "${SPLIT_AUDIO:-0}" != 1 ] && [ "${PICK_AUDIO:-0}" != 1 ] \
                && [ "${USE_SAVED_ROUTE:-0}" != 1 ]; then
                sync_pulse_defaults
            fi
            audio_note="Linux: Pulse Unix socket"
            ;;
        Darwin)
            export PULSE_SERVER="${PULSE_SERVER:-tcp:host.docker.internal:4713}"
            audio_note="macOS: Pulse TCP (run ./scripts/host-pulse-tcp.sh first)"
            ;;
        MINGW*|MSYS*|CYGWIN*)
            export PULSE_SERVER="${PULSE_SERVER:-tcp:host.docker.internal:4713}"
            audio_note="Windows: Pulse TCP (run ./scripts/host-pulse-tcp.sh first)"
            ;;
        *)
            export PULSE_SERVER="${PULSE_SERVER:-tcp:host.docker.internal:4713}"
            audio_note="Pulse TCP (see ./scripts/host-pulse-tcp.sh)"
            ;;
    esac
    export COMPOSE
}

remove_stale_containers() {
    docker rm -f audio-receiver-1 audio-music-1 audio-mic-1 audio-midi-1 2>/dev/null || true
}

cleanup_done=0
cleanup() {
    [ "$cleanup_done" = 1 ] && return
    cleanup_done=1
    trap '' EXIT INT TERM HUP
    printf '\n[play] stopping containers...\n'
    remove_stale_containers
    $COMPOSE kill music mic midi receiver 2>/dev/null || true
    $COMPOSE rm -f --remove-orphans music mic midi receiver 2>/dev/null || true
}

MODE="${1:-music}"
[ "$#" -gt 0 ] && shift

# Flags may appear before or after the song path (e.g. song.mp3 --use-route).
PICK_AUDIO=0
SAVE_PULSE_ROUTE=0
USE_SAVED_ROUTE=0
SPLIT_AUDIO=0
_POSFILE=$(mktemp)
for _arg in "$@"; do
    case "$_arg" in
        --pick-audio|-i|--interactive|--pick-route)
            PICK_AUDIO=1
            ;;
        --save-route)
            SAVE_PULSE_ROUTE=1
            ;;
        --use-route)
            USE_SAVED_ROUTE=1
            ;;
        --split-audio|--laptop-mic-bt-out)
            SPLIT_AUDIO=1
            ;;
        *)
            printf '%s\n' "$_arg" >>"$_POSFILE"
            ;;
    esac
done
set --
if [ -s "$_POSFILE" ]; then
    while IFS= read -r _line; do
        set -- "$@" "$_line"
    done <"$_POSFILE"
fi
rm -f "$_POSFILE"

case "$MODE" in
    help|-h|--help) print_help; exit 0 ;;
    pick|pick-audio|interactive)
        if [ ! -f "$PICK_SCRIPT" ]; then
            echo "[play] missing $PICK_SCRIPT" >&2
            exit 1
        fi
        # shellcheck source=scripts/interactive-pulse-pick.sh
        . "$PICK_SCRIPT"
        SAVE_PULSE_ROUTE=${SAVE_PULSE_ROUTE:-1}
        run_interactive_pick_menu
        exit $?
        ;;
    list-devices|devices|list)
        setup_compose_env
        remove_stale_containers
        exec $COMPOSE run --rm list-devices ;;
    pulse-routes|pulse|pactl)
        if ! command -v pactl >/dev/null 2>&1; then
            echo "[play] pactl not found (install PipeWire/Pulse)" >&2
            exit 1
        fi
        echo "=== sinks (playback) ==="
        pactl list sinks short
        echo ""
        echo "=== sources (capture) ==="
        pactl list sources short
        echo ""
        echo "Interactive picker:  ./play.sh pick"
        echo "Use with mic:        ./play.sh mic --monitor --pick-audio"
        exit 0 ;;
esac

case "$MODE" in
    music|file|play|song) SENDER=music ;;
    mic|microphone)       SENDER=mic ;;
    midi|loopback|daw)    SENDER=midi ;;
    *)
        echo "[play] unknown mode: $MODE" >&2
        exit 2 ;;
esac

if [ "$SAVE_PULSE_ROUTE" = 1 ] && [ "$PICK_AUDIO" != 1 ]; then
    echo "[play] --save-route requires --pick-audio (or run ./play.sh pick)" >&2
    exit 2
fi

if [ "$SENDER" = "music" ]; then
    SONG="${1:-Darude - Sandstorm.mp3}"
    if [ $# -gt 1 ]; then
        echo "[play] error: music mode takes one song path, got: $*" >&2
        exit 2
    fi
    if [ ! -f "$SONG" ]; then
        echo "[play] song not found: $SONG" >&2
        echo "[play] (cwd: $SCRIPT_DIR — place files here or pass a path)" >&2
        exit 1
    fi
    export SONG
    export SENDER_EXTRA_ARGS=
    sender_label="music (song: $SONG)"
else
    export SENDER_EXTRA_ARGS="${*:-}"
    sender_label="$SENDER${SENDER_EXTRA_ARGS:+ ($SENDER_EXTRA_ARGS)}"
fi

if [ "$USE_SAVED_ROUTE" = 1 ]; then
    load_saved_route || exit 1
    # Music only needs playback sink; drop capture source from a mic route file.
    if [ "$SENDER" = "music" ]; then
        unset PULSE_SOURCE
    fi
fi

if [ "$PICK_AUDIO" = 1 ]; then
    if [ ! -f "$PICK_SCRIPT" ]; then
        echo "[play] missing $PICK_SCRIPT" >&2
        exit 1
    fi
    # shellcheck source=scripts/interactive-pulse-pick.sh
    . "$PICK_SCRIPT"
    SPLIT_AUDIO=0
    run_interactive_audio_route "$SENDER" || exit 1
fi

if [ "$SPLIT_AUDIO" = 1 ]; then
    if [ "$SENDER" != "mic" ] && [ "$SENDER" != "midi" ]; then
        echo "[play] --split-audio only applies to mic / midi" >&2
        exit 2
    fi
    # shellcheck source=scripts/pulse-split-routes.sh
    . "$SCRIPT_DIR/scripts/pulse-split-routes.sh"
fi

setup_compose_env
remove_stale_containers
trap cleanup EXIT INT TERM HUP

echo "[play] OSC → ${OSC_HOST}:${OSC_PORT}  |  audio → ${PULSE_SERVER}"
echo "[play] ($audio_note)"
if [ -n "${PULSE_SINK:-}" ]; then
    echo "[play] pulse sink (playback): ${PULSE_SINK}"
fi
if [ -n "${PULSE_SOURCE:-}" ]; then
    echo "[play] pulse source (capture): ${PULSE_SOURCE}"
fi
if [ "$SENDER" = "music" ] && [ -z "${PULSE_SINK:-}" ]; then
    echo "[play] tip: use --use-route or --pick-audio to target Bluetooth" >&2
fi
if [ "$SENDER" = "mic" ] || [ "$SENDER" = "midi" ]; then
    echo "[play] live audio = sender container (Pulse). Receiver = OSC dashboard only."
fi
echo

echo "[play] building ht-receiver..."
$COMPOSE build receiver

echo "[play] building ht-audio..."
$COMPOSE build "$SENDER"

echo "[play] starting receiver (UDP :${OSC_PORT} published on host)..."
$COMPOSE up -d --no-deps --force-recreate --remove-orphans receiver
sleep 0.5

echo "[play] starting sender: $sender_label"
$COMPOSE up -d --no-deps --force-recreate --remove-orphans "$SENDER"

CONTAINER=$($COMPOSE ps -q --status running receiver | head -n1)
if [ -z "$CONTAINER" ]; then
    echo "[play] receiver not running:" >&2
    $COMPOSE logs receiver >&2
    exit 1
fi

SENDER_CONTAINER=$($COMPOSE ps -q --status running "$SENDER" | head -n1)
if [ -n "$SENDER_CONTAINER" ]; then
    sleep 1.5
    echo "[play] sender startup:"
    docker logs "$SENDER_CONTAINER" 2>&1 | tail -30
    echo
fi

echo "[play] receiver dashboard (Ctrl-C to stop). Sender: docker logs audio-${SENDER}-1"
echo
docker logs -f --tail 0 "$CONTAINER" 2>&1 &
LOGS_PID=$!
trap 'kill "$LOGS_PID" 2>/dev/null; cleanup; exit 130' INT TERM

wait "$LOGS_PID" 2>/dev/null || true
