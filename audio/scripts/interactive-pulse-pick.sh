#!/bin/sh
# Interactive Pulse/PipeWire device picker (host pactl).
#
# Sourced by play.sh (--pick-route / ./play.sh pick). Sets PULSE_SINK and/or
# PULSE_SOURCE from numbered menus. PipeWire lists idle devices as SUSPENDED —
# that is normal until a stream starts.
#
# On success, sets PICK_RESULT_ID and exports PULSE_* variables.

: "${PICK_RESULT_ID:=}"

_pick_require_pactl() {
    if ! command -v pactl >/dev/null 2>&1; then
        echo "[pick] pactl not found — install PipeWire/Pulse on the host" >&2
        return 1
    fi
    return 0
}

# Description: from `pactl list sinks|sources` (device friendly name).
_pick_pactl_description() {
    _list_kind=$1
    _id=$2
    pactl list "$_list_kind" 2>/dev/null | awk -v target="Name: $_id" '
        $1 == "Name:" && $2 == target { on = 1; next }
        on && $1 == "Description:" {
            out = $2
            for (i = 3; i <= NF; i++) out = out " " $i
            print out
            exit
        }
        on && $1 == "Name:" { exit }
    '
}

_pick_device_kind() {
    case $1 in
        bluez_output.*|bluez_input.*) printf 'bluetooth' ;;
        alsa_output.*|alsa_input.*)   printf 'laptop' ;;
        *)                              printf 'other' ;;
    esac
}

# Human label with Bluetooth / laptop called out explicitly.
_pick_device_label() {
    _id=$1
    _list_kind=$2
    _desc=$(_pick_pactl_description "$_list_kind" "$_id")

    case "$_id" in
        bluez_output.*)
            if [ -n "$_desc" ]; then
                printf 'Bluetooth headphones · %s' "$_desc"
            else
                printf 'Bluetooth headphones · %s' "$(_pick_human_name "$_id")"
            fi
            ;;
        bluez_input.*)
            if [ -n "$_desc" ]; then
                printf 'Bluetooth headset mic · %s' "$_desc"
            else
                printf 'Bluetooth headset mic · %s' "$(_pick_human_name "$_id")"
            fi
            ;;
        alsa_input.*Mic1*)
            if [ -n "$_desc" ]; then
                printf 'Laptop microphone · %s' "$_desc"
            else
                printf 'Laptop microphone · Mic1 (built-in)'
            fi
            ;;
        alsa_input.*Mic2*)
            if [ -n "$_desc" ]; then
                printf 'Laptop microphone · %s' "$_desc"
            else
                printf 'Laptop microphone · Mic2'
            fi
            ;;
        alsa_output.*Speaker*|alsa_output.*speaker*)
            if [ -n "$_desc" ]; then
                printf 'Laptop speakers · %s' "$_desc"
            else
                printf 'Laptop speakers (not Bluetooth)'
            fi
            ;;
        *)
            if [ -n "$_desc" ]; then
                printf '%s' "$_desc"
            else
                _pick_human_name "$_id"
            fi
            ;;
    esac
}

# Fallback parse of Pulse id.
_pick_human_name() {
    printf '%s' "$1" | sed \
        -e 's/^alsa_input\.//' \
        -e 's/^alsa_output\.//' \
        -e 's/^bluez_input\.//' \
        -e 's/^bluez_output\.//' \
        -e 's/\.monitor$//' \
        -e 's/__/ · /g' \
        -e 's/_/ /g' \
        -e 's/ · source$//' \
        -e 's/ · sink$//'
}

_pick_pulse_state() {
    _last=$(printf '%s' "$1" | awk '{print $NF}')
    case "$_last" in
        SUSPENDED|RUNNING|IDLE) printf '%s' "$_last" ;;
        *) printf '' ;;
    esac
}

# sort key: bluetooth first on playback, laptop mics before bt mic on capture
_pick_sort_key() {
    _kind=$1
    _role=$2
    case "$_kind" in
        bluetooth)
            [ "$_role" = "sink" ] && printf '0' || printf '9'
            ;;
        laptop)
            printf '1'
            ;;
        *)
            printf '5'
            ;;
    esac
}

# List into $2: sortkey<TAB>id<TAB>label<TAB>state
_pick_build_list() {
    _role=$1
    _out=$2
    _include_monitors=${3:-0}
    _list_kind=sinks
    [ "$_role" = "source" ] && _list_kind=sources

    _tmp=$(mktemp)
    : >"$_tmp"
    pactl list "$_list_kind" short 2>/dev/null | while IFS= read -r _line; do
        [ -n "$_line" ] || continue
        _id=$(printf '%s' "$_line" | awk '{print $2}')
        [ -n "$_id" ] || continue
        if [ "$_role" = "source" ] && [ "$_include_monitors" = 0 ]; then
            case "$_id" in *.monitor) continue ;; esac
        fi
        _state=$(_pick_pulse_state "$_line")
        _label=$(_pick_device_label "$_id" "$_list_kind")
        _kind=$(_pick_device_kind "$_id")
        _sort=$(_pick_sort_key "$_kind" "$_role")
        printf '%s\t%s\t%s\t%s\n' "$_sort" "$_id" "$_label" "$_state"
    done >>"$_tmp"
    sort -t "$(printf '\t')" -k1,1n -k2,2 "$_tmp" | cut -f2- >"$_out"
    rm -f "$_tmp"
}

interactive_pick_pulse() {
    _role=$1
    _title=$2
    _include_monitors=${3:-0}
    _hint=${4:-}

    _pick_require_pactl || return 1

    _list=$(mktemp)
    _pick_build_list "$_role" "$_list" "$_include_monitors"
    _n=0
    while IFS= read -r _row; do
        [ -n "$_row" ] || continue
        _n=$((_n + 1))
        _id=$(printf '%s' "$_row" | cut -f1)
        _label=$(printf '%s' "$_row" | cut -f2)
        _state=$(printf '%s' "$_row" | cut -f3)
        eval "PICK_ID_${_n}=\"\$_id\""
        eval "PICK_LABEL_${_n}=\"\$_label\""
        eval "PICK_STATE_${_n}=\"\$_state\""
    done <"$_list"

    if [ "$_n" -eq 0 ]; then
        echo "[pick] no ${_role} devices found" >&2
        rm -f "$_list"
        return 1
    fi

    if [ "$_role" = "sink" ]; then
        _default=$(pactl get-default-sink 2>/dev/null || true)
    else
        _default=$(pactl get-default-source 2>/dev/null || true)
    fi

    printf '\n%s\n' "$_title"
    [ -n "$_hint" ] && printf '  %s\n' "$_hint"
    printf '  (SUSPENDED = idle until an app records/plays — normal before you start)\n\n'
    _i=1
    while [ "$_i" -le "$_n" ]; do
        eval "_id=\$PICK_ID_${_i}"
        eval "_label=\$PICK_LABEL_${_i}"
        eval "_state=\$PICK_STATE_${_i}"
        _mark=
        [ "$_id" = "$_default" ] && _mark=' · system default'
        _state_note=
        [ -n "$_state" ] && _state_note=" · $_state"
        printf '  %2d) %s%s%s\n' "$_i" "$_label" "$_mark" "$_state_note"
        printf '      %s\n' "$_id"
        _i=$((_i + 1))
    done
    printf '\n'
    [ -n "$_default" ] && printf '  (d) use system default\n'
    printf '  (q) cancel\n\n'

    while true; do
        printf 'Enter number'
        [ -n "$_default" ] && printf ', d'
        printf ', or q: '
        IFS= read -r _choice </dev/tty 2>/dev/null || IFS= read -r _choice
        case $_choice in
            q|Q)
                rm -f "$_list"
                return 1
                ;;
            d|D)
                if [ -n "$_default" ]; then
                    PICK_RESULT_ID=$_default
                    rm -f "$_list"
                    return 0
                fi
                echo '[pick] no system default set'
                ;;
            ''|*[!0-9]*)
                echo '[pick] invalid choice'
                ;;
            *)
                if [ "$_choice" -ge 1 ] 2>/dev/null && [ "$_choice" -le "$_n" ]; then
                    eval "PICK_RESULT_ID=\$PICK_ID_${_choice}"
                    rm -f "$_list"
                    return 0
                fi
                echo '[pick] invalid choice'
                ;;
        esac
    done
}

interactive_pick_sink() {
    interactive_pick_pulse sink \
        "Playback output — where you HEAR audio" \
        0 \
        "For earbuds: pick Bluetooth headphones (bluez_output…), not Laptop speakers." \
        && export PULSE_SINK="$PICK_RESULT_ID"
}

interactive_pick_source() {
    interactive_pick_pulse source \
        "Microphone / capture input — where audio is RECORDED from" \
        0 \
        "Laptop mic: pick Laptop microphone · Mic1. Earbud mic: pick Bluetooth headset mic." \
        && export PULSE_SOURCE="$PICK_RESULT_ID"
}

interactive_pick_loopback_source() {
    interactive_pick_pulse source \
        "Capture input (loopback / DAW)" \
        1 \
        "" \
        && export PULSE_SOURCE="$PICK_RESULT_ID"
}

run_interactive_audio_route() {
    _mode=$1
    _pick_require_pactl || return 1

    case "$_mode" in
        mic)
            interactive_pick_source || return 1
            echo "[pick] capture: $PULSE_SOURCE"
            interactive_pick_sink || return 1
            echo "[pick] playback: $PULSE_SINK"
            ;;
        midi)
            interactive_pick_loopback_source || return 1
            echo "[pick] capture: $PULSE_SOURCE"
            interactive_pick_sink || return 1
            echo "[pick] playback: $PULSE_SINK"
            ;;
        music|*)
            interactive_pick_sink || return 1
            echo "[pick] playback: $PULSE_SINK"
            ;;
    esac

    if [ "${SAVE_PULSE_ROUTE:-0}" = 1 ]; then
        _env_file="${PULSE_ROUTE_FILE:?PULSE_ROUTE_FILE not set}"
        {
            echo "# Written by ./play.sh pick / --pick-route"
            echo "PULSE_SINK=$PULSE_SINK"
            [ -n "${PULSE_SOURCE:-}" ] && echo "PULSE_SOURCE=$PULSE_SOURCE"
        } >"$_env_file"
        echo "[pick] saved to $_env_file"
    fi
    return 0
}

run_interactive_pick_menu() {
    _pick_require_pactl || return 1
    printf '\nAudio route picker (host Pulse/PipeWire)\n\n'
    printf '  1) Playback output only (music / general)\n'
    printf '  2) Microphone input only\n'
    printf '  3) Playback + microphone (mic monitor / capture)\n'
    printf '  4) Playback + loopback capture (midi / DAW)\n'
    printf '  q) Quit\n\n'
    printf 'Choice: '
    IFS= read -r _menu </dev/tty 2>/dev/null || IFS= read -r _menu
    case $_menu in
        1) run_interactive_audio_route music ;;
        2) interactive_pick_source ;;
        3) run_interactive_audio_route mic ;;
        4) run_interactive_audio_route midi ;;
        q|Q) return 1 ;;
        *) echo '[pick] invalid choice' >&2; return 1 ;;
    esac
}
