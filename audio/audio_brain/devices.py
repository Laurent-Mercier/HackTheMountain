"""PortAudio device selection and Pulse/PipeWire routing helpers.

When ``PULSE_SERVER`` is set (Docker on Linux/macOS/Windows), producers must
**not** open raw ALSA ``hw:*`` devices — that bypasses Bluetooth and split
routing. Use :func:`resolve_pulse_capture_device` /
:func:`resolve_pulse_playback_device`, which prefer PortAudio ``default`` when
``PULSE_SOURCE`` / ``PULSE_SINK`` are set, otherwise score ``pulse``/``default``
entries from :func:`find_pulse_input` / :func:`find_pulse_output`.

:func:`open_best_stream` and :func:`open_best_duplex_stream` try multiple sample
rates and channel layouts (Bluetooth often needs stereo @ 48 kHz).
"""
from __future__ import annotations

import ctypes
import os
import re
from typing import Literal, Optional

import pyaudio


def silence_alsa_noise() -> None:
    """Mute libasound's stderr probing on Linux.

    ALSA spams a dozen ``Cannot open shared library`` lines on every
    ``pyaudio.PyAudio()`` construction. They are harmless but make the
    dashboard unreadable. We register a no-op error handler with
    libasound directly; the function is a silent no-op on non-Linux or
    if libasound is not loadable.

    The handler is stored on the function object to keep it alive for
    the lifetime of the process — otherwise the C side would dereference
    a freed callback.
    """
    try:
        asound = ctypes.cdll.LoadLibrary("libasound.so.2")
    except OSError:
        return
    ERROR_HANDLER = ctypes.CFUNCTYPE(
        None, ctypes.c_char_p, ctypes.c_int,
        ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
    )
    handler = ERROR_HANDLER(lambda *_: None)
    asound.snd_lib_error_set_handler(handler)
    silence_alsa_noise._handler = handler  # type: ignore[attr-defined]


def routes_through_pulse() -> bool:
    """True when the process should use the host Pulse/PipeWire server."""
    return bool(os.environ.get("PULSE_SERVER", "").strip())


def pulse_env_routing() -> bool:
    """True when ``PULSE_SOURCE`` and/or ``PULSE_SINK`` pin host routes."""
    return bool(
        os.environ.get("PULSE_SOURCE", "").strip()
        or os.environ.get("PULSE_SINK", "").strip()
    )


def print_pulse_routing() -> None:
    """Log Pulse env vars (helps debug Bluetooth / wrong-sink issues)."""
    server = os.environ.get("PULSE_SERVER", "(unset)")
    sink = os.environ.get("PULSE_SINK") or "(system default)"
    source = os.environ.get("PULSE_SOURCE") or "(system default)"
    print(f"pulse: server={server}")
    print(f"pulse: sink={sink}  source={source}")
    if routes_through_pulse():
        print(
            "pulse: PortAudio will use Pulse/default devices only "
            "(not raw ALSA hw:*)"
        )
    if pulse_env_routing():
        print(
            "pulse: split routing — capture/playback use ALSA 'default' "
            "with the Pulse source/sink above"
        )


# Pulse/ALSA virtual capture devices — never use these as a microphone.
_MONITOR_SOURCE_NEEDLES = (
    "monitor of",
    "loopback",
    "snd-aloop",
    "remap",
)

_MIC_SOURCE_HINTS = (
    "microphone",
    "mic ",
    "headset",
    "webcam",
    "usb audio",
    "blue",
    "bluetooth",
    "airpods",
    "buds",
    "ear",
)


def is_monitor_source(name: str) -> bool:
    """True if the PortAudio/Pulse name is a speaker loopback, not a mic."""
    lower = name.lower()
    return any(n in lower for n in _MONITOR_SOURCE_NEEDLES)


def is_raw_alsa_hardware(name: str) -> bool:
    """True for direct ALSA cards (bypass host Pulse / Bluetooth routing)."""
    lower = name.lower()
    if "pulse" in lower or lower.strip() == "default":
        return False
    if "hw:" in lower or "hw," in lower:
        return True
    if "hdmi" in lower:
        return True
    if "generic:" in lower and "alsa" in lower:
        return True
    return False


def _score_io_device(name: str, *, direction: Literal["input", "output"]) -> int:
    """Higher score = better choice. Negative = reject."""
    if is_monitor_source(name):
        return -1000
    lower = name.lower()
    if routes_through_pulse() and is_raw_alsa_hardware(name):
        return -1000
    score = 0
    if lower == "default":
        score += 100
    if "pulse" in lower or "pipewire" in lower:
        score += 90
    if direction == "input" and any(h in lower for h in _MIC_SOURCE_HINTS):
        score += 15
    if not routes_through_pulse():
        if "built-in analog" in lower or "analog stereo" in lower:
            score += 8
    return score


def find_portaudio_default(
    p: pyaudio.PyAudio, direction: Literal["input", "output"],
) -> Optional[int]:
    """PortAudio device named ``default`` (routes via Pulse env vars)."""
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if str(info["name"]).lower() != "default":
            continue
        if direction == "input" and info["maxInputChannels"] > 0:
            return i
        if direction == "output" and info["maxOutputChannels"] > 0:
            return i
    return None


def resolve_pulse_capture_device(p: pyaudio.PyAudio) -> Optional[int]:
    """Return the PortAudio index to use for microphone capture.

    If ``PULSE_SOURCE`` is set, returns the ``default`` input (libpulse applies
    the env var). Otherwise scores Pulse-routed inputs and skips ``hw:*``.
    """
    if routes_through_pulse() and pulse_env_routing():
        return find_portaudio_default(p, "input")
    return find_microphone_input(p)


def resolve_pulse_playback_device(p: pyaudio.PyAudio) -> Optional[int]:
    """Return the PortAudio index to use for speaker/headphone playback.

    If ``PULSE_SINK`` is set, returns the ``default`` output. Otherwise scores
    Pulse-routed outputs and skips ``hw:*``.
    """
    if routes_through_pulse() and pulse_env_routing():
        return find_portaudio_default(p, "output")
    return find_pulse_output(p)


def find_pulse_input(p: pyaudio.PyAudio) -> Optional[int]:
    """Pick the best Pulse-routed capture device (host default mic / BT)."""
    best_score = -1
    best_index: Optional[int] = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] <= 0:
            continue
        score = _score_io_device(str(info["name"]), direction="input")
        if score > best_score:
            best_score = score
            best_index = i
    return best_index if best_score >= 0 else None


def find_pulse_output(p: pyaudio.PyAudio) -> Optional[int]:
    """Pick the best Pulse-routed playback device (host default sink / BT)."""
    best_score = -1
    best_index: Optional[int] = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxOutputChannels"] <= 0:
            continue
        score = _score_io_device(str(info["name"]), direction="output")
        if score > best_score:
            best_score = score
            best_index = i
    return best_index if best_score >= 0 else None


def find_microphone_input(p: pyaudio.PyAudio) -> Optional[int]:
    """Pick a capture device index, respecting :func:`routes_through_pulse`."""
    if routes_through_pulse():
        return find_pulse_input(p)
    best_score = -1
    best_index: Optional[int] = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] <= 0:
            continue
        name = str(info["name"])
        if is_monitor_source(name):
            continue
        score = _score_io_device(name, direction="input")
        if score > best_score:
            best_score = score
            best_index = i
    return best_index


def _device_label(name: str) -> str:
    """Normalize a PortAudio device name for fuzzy matching."""
    label = name.lower()
    for sep in (": ", " on ", " — "):
        if sep in label:
            label = label.split(sep, 1)[-1]
    label = re.sub(r"\(.*?\)", "", label)
    label = re.sub(r"\s+", " ", label).strip()
    for token in (
        "mono", "stereo", "duplex", "input", "output",
        "pulse", "default", "hw:",
    ):
        label = label.replace(token, " ")
    return re.sub(r"\s+", " ", label).strip()


def find_matching_output(p: pyaudio.PyAudio, input_index: int) -> Optional[int]:
    """Return an output device that likely pairs with ``input_index``.

    Not used when ``PULSE_SERVER`` is set — Pulse already routes both
    sides to the host default sink/source (e.g. Bluetooth).
    """
    if routes_through_pulse():
        return None
    in_name = str(p.get_device_info_by_index(input_index)["name"])
    if is_raw_alsa_hardware(in_name):
        return None
    key = _device_label(in_name)
    if len(key) < 4:
        return None

    best_score = 0
    best_index: Optional[int] = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxOutputChannels"] <= 0:
            continue
        out_name = str(info["name"])
        if is_monitor_source(out_name):
            continue
        out_key = _device_label(out_name)
        score = 0
        if key in out_key or out_key in key:
            score += 20
        # Longest shared word chunk (e.g. "galaxy buds").
        for n in (20, 12, 8):
            if len(key) >= n and key[:n] in out_key:
                score += 10
                break
        if score > best_score:
            best_score = score
            best_index = i
    return best_index if best_score >= 10 else None


def _rate_candidates(requested: int, info: dict) -> list[int]:
    """Sample rates to try, preferring 48 kHz when ``PULSE_SINK`` is Bluetooth."""
    rates: list[int] = []
    prefer: list[int] = [requested, int(info.get("defaultSampleRate", requested))]
    if "bluez" in os.environ.get("PULSE_SINK", "").lower():
        prefer = [48000, *prefer]
    for r in (*prefer, 44100, 32000, 16000):
        if r > 0 and r not in rates:
            rates.append(r)
    return rates


def open_best_duplex_stream(
    pa: pyaudio.PyAudio,
    *,
    device_index: int,
    rate: int,
    frames_per_buffer: int,
) -> tuple[pyaudio.Stream, int, int]:
    """Open one full-duplex stream on ``default`` (split ``PULSE_*`` routing).

    Returns ``(stream, actual_rate, channels)``. Used for mic live monitor
    when capture and playback share the same PortAudio device index.
    """
    info = pa.get_device_info_by_index(device_index)
    cap = min(int(info["maxInputChannels"]), int(info["maxOutputChannels"]))
    channel_order = [c for c in (2, 1) if c <= cap] or [1]
    last_exc: Optional[OSError] = None
    for channels in channel_order:
        for try_rate in _rate_candidates(rate, info):
            try:
                stream = pa.open(
                    format=pyaudio.paFloat32,
                    channels=channels,
                    rate=try_rate,
                    frames_per_buffer=frames_per_buffer,
                    input=True,
                    output=True,
                    input_device_index=device_index,
                    output_device_index=device_index,
                )
                return stream, try_rate, channels
            except OSError as exc:
                last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise OSError("could not open duplex stream on this device")


def open_best_stream(
    pa: pyaudio.PyAudio,
    *,
    direction: Literal["input", "output"],
    device_index: int,
    rate: int,
    frames_per_buffer: int,
) -> tuple[pyaudio.Stream, int, int]:
    """Open a PortAudio stream, trying channel counts and sample rates.

    Bluetooth devices often need **stereo output** and **48 kHz** even when
    the caller asked for mono @ 44.1 kHz.
    """
    info = pa.get_device_info_by_index(device_index)
    if direction == "input":
        cap = int(info["maxInputChannels"])
        channel_order = [c for c in (1, 2) if c <= cap] or [1]
        stream_kwargs = {"input": True, "input_device_index": device_index}
    else:
        cap = int(info["maxOutputChannels"])
        # Prefer stereo first — many BT sinks reject mono opens.
        channel_order = [c for c in (2, 1) if c <= cap] or [1]
        stream_kwargs = {"output": True, "output_device_index": device_index}

    last_exc: Optional[OSError] = None
    for channels in channel_order:
        for try_rate in _rate_candidates(rate, info):
            try:
                stream = pa.open(
                    format=pyaudio.paFloat32,
                    channels=channels,
                    rate=try_rate,
                    frames_per_buffer=frames_per_buffer,
                    **stream_kwargs,
                )
                return stream, try_rate, channels
            except OSError as exc:
                last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise OSError("could not open audio stream on this device")


def list_devices() -> None:
    """Pretty-print every PortAudio device + the default output device.

    Output columns: index, max input channels, max output channels,
    default sample rate, name. Mirrors the format of the legacy CLI so
    existing scripts keep parsing it.
    """
    print_pulse_routing()
    p = pyaudio.PyAudio()
    try:
        print(f"{'idx':>3}  {'in':>2} {'out':>3}  {'rate':>6}  kind  name")
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            name = str(info["name"])
            if routes_through_pulse() and is_raw_alsa_hardware(name):
                kind = "hw  "
            elif "pulse" in name.lower() or name.lower() == "default":
                kind = "puls"
            else:
                kind = "    "
            print(
                f"{i:>3}  "
                f"{info['maxInputChannels']:>2} "
                f"{info['maxOutputChannels']:>3}  "
                f"{int(info['defaultSampleRate']):>6}  "
                f"{kind}  {name}"
            )
        if routes_through_pulse():
            rec_in = find_pulse_input(p)
            rec_out = find_pulse_output(p)
            if rec_in is not None:
                n = p.get_device_info_by_index(rec_in)["name"]
                print(f"\nrecommended input (pulse):  [{rec_in}] {n}")
            if rec_out is not None:
                n = p.get_device_info_by_index(rec_out)["name"]
                print(f"recommended output (pulse): [{rec_out}] {n}")
        try:
            default_in = p.get_default_input_device_info()
            print(
                f"\ndefault input:  [{default_in['index']}] "
                f"{default_in['name']}"
            )
        except OSError:
            print("\nno default input device found")
        try:
            default = p.get_default_output_device_info()
            print(
                f"default output: [{default['index']}] {default['name']}"
            )
        except OSError:
            print("no default output device found")
    finally:
        p.terminate()


def find_loopback_input(p: pyaudio.PyAudio) -> Optional[int]:
    """Return the index of a virtual-loopback input device.

    Looks for common names of virtual audio drivers — BlackHole and
    Soundflower on macOS, ``loopback`` / ``monitor of`` on Linux
    (PulseAudio). Returns ``None`` if nothing matches; callers should
    fall back to an explicit ``--input-device`` flag.
    """
    needles = ("blackhole", "soundflower", "loopback", "monitor of")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] <= 0:
            continue
        if any(n in info["name"].lower() for n in needles):
            return i
    return None
