"""Command-line entry point for the :mod:`audio_brain` pipelines.

The flag interface is *identical* to the legacy ``main.py`` so existing
scripts and the README cheat sheet keep working::

    uv run python -m audio_brain.cli path/to/song.mp3
    uv run python -m audio_brain.cli --mic
    uv run python -m audio_brain.cli --midi
    uv run python -m audio_brain.cli --list-devices

The Docker entrypoint (``docker-entrypoint.sh``) wraps these flags into
friendlier subcommands (``music``, ``mic``, ``midi``, ``list-devices``).
Host audio routing is normally set by ``play.sh`` via ``PULSE_SINK`` /
``PULSE_SOURCE``; ``--pulse-sink`` / ``--pulse-source`` override them when
running the CLI directly.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from .config import DEFAULT_OSC_HOST, DEFAULT_OSC_PORT, DEFAULT_RATE
from .devices import list_devices, silence_alsa_noise
from .osc import OscSender
from .pipelines import (
    FilePlaybackPipeline,
    LoopbackPipeline,
    MicCapturePipeline,
)


def build_parser() -> argparse.ArgumentParser:
    """Construct the ``argparse`` parser used by :func:`main`.

    Exposed as a separate function so tests can introspect the flag
    surface without having to invoke :func:`main`.
    """
    parser = argparse.ArgumentParser(
        prog="audio-brain",
        description=(
            "Play an audio file (or capture from mic / virtual loopback) "
            "while broadcasting real-time features over OSC."
        ),
    )
    parser.add_argument(
        "song", type=Path, nargs="?",
        help="audio file (wav/mp3/flac/ogg/...); omit when using --mic / --midi",
    )
    parser.add_argument(
        "--mic", action="store_true",
        help="capture from the microphone instead of playing a file",
    )
    parser.add_argument(
        "--midi", action="store_true",
        help="capture from a virtual loopback device (e.g. BlackHole on "
             "macOS) and pass through to speakers — for analyzing live "
             "GarageBand/DAW output",
    )
    parser.add_argument(
        "--no-monitor", action="store_true",
        help="in --midi mode, don't pass captured audio through to speakers",
    )
    parser.add_argument(
        "--input-device", type=int, default=None,
        help="input device index (with --mic / --midi; see --list-devices)",
    )
    parser.add_argument(
        "--device", type=int, default=None,
        help="output device index for file playback (see --list-devices)",
    )
    parser.add_argument(
        "--rate", type=int, default=DEFAULT_RATE,
        help=f"sample rate to request from the mic (default {DEFAULT_RATE})",
    )
    parser.add_argument(
        "--monitor", action="store_true",
        help="in --mic mode, pass captured audio through to speakers in real "
             "time (use headphones to avoid feedback)",
    )
    parser.add_argument(
        "--replay", action="store_true",
        help="with --mic --monitor, also replay the full recording after "
             "Ctrl-C (default: live monitor only, no second playback)",
    )
    parser.add_argument(
        "--no-playback", action="store_true",
        help="don't replay the recording when --mic is stopped with Ctrl-C",
    )
    parser.add_argument(
        "--no-osc", action="store_true",
        help="disable OSC broadcasting (default: send /audio/frame to "
             f"{DEFAULT_OSC_HOST}:{DEFAULT_OSC_PORT})",
    )
    parser.add_argument(
        "--osc-host", type=str, default=DEFAULT_OSC_HOST,
        help=f"OSC destination host (default {DEFAULT_OSC_HOST})",
    )
    parser.add_argument(
        "--osc-port", type=int, default=DEFAULT_OSC_PORT,
        help=f"OSC destination port (default {DEFAULT_OSC_PORT})",
    )
    parser.add_argument(
        "--list-devices", action="store_true",
        help="list audio devices and exit",
    )
    parser.add_argument(
        "--alsa-noise", action="store_true",
        help="don't silence libasound stderr probing on Linux",
    )
    parser.add_argument(
        "--pulse-source", metavar="NAME", default=None,
        help="Pulse/PipeWire capture source (e.g. laptop mic). With Docker, "
             "set PULSE_SOURCE in the environment instead.",
    )
    parser.add_argument(
        "--pulse-sink", metavar="NAME", default=None,
        help="Pulse/PipeWire playback sink (e.g. Bluetooth earbuds). With "
             "Docker, set PULSE_SINK in the environment instead.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse args, optionally set Pulse env vars, run a pipeline.

    Dispatches to :class:`FilePlaybackPipeline`, :class:`MicCapturePipeline`,
    or :class:`LoopbackPipeline`. With OSC enabled (default), the C++
    receiver should display the dashboard — the sender does not.

    Parameters
    ----------
    argv
        Arguments to parse. ``None`` uses ``sys.argv[1:]``.

    Returns
    -------
    int
        ``0`` on success, non-zero on file/device errors.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.alsa_noise:
        silence_alsa_noise()

    if args.pulse_source:
        os.environ["PULSE_SOURCE"] = args.pulse_source
    if args.pulse_sink:
        os.environ["PULSE_SINK"] = args.pulse_sink

    if args.list_devices:
        list_devices()
        return 0

    osc: Optional[OscSender] = None
    if not args.no_osc:
        osc = OscSender(args.osc_host, args.osc_port)
        visual_host = os.environ.get("VISUAL_OSC_HOST")
        visual_port = os.environ.get("VISUAL_OSC_PORT")
        if visual_host and visual_port:
            osc.add_target(visual_host, int(visual_port))

    if args.midi:
        if args.mic or args.song is not None:
            print(
                "[info] --midi takes precedence over --mic / song path",
                file=sys.stderr,
            )
        pipeline = LoopbackPipeline(
            input_device=args.input_device,
            output_device=args.device,
            rate=args.rate,
            monitor=not args.no_monitor,
            osc=osc,
        )
        pipeline.run()
        return 0

    if args.mic:
        if args.song is not None:
            print("[info] --mic given; ignoring song path", file=sys.stderr)
        # Live monitor already plays audio; replay after stop causes echo.
        want_playback = not args.no_playback
        if args.monitor and not args.replay:
            want_playback = False
        pipeline = MicCapturePipeline(
            input_device=args.input_device,
            output_device=args.device,
            rate=args.rate,
            monitor=args.monitor,
            playback=want_playback,
            osc=osc,
        )
        pipeline.run()
        return 0

    if args.song is None:
        parser.error("a song path is required (or use --mic, or --list-devices)")
    if not args.song.is_file():
        print(f"error: file not found: {args.song}", file=sys.stderr)
        return 1

    file_pipeline = FilePlaybackPipeline(
        args.song, device_index=args.device, osc=osc,
    )
    file_pipeline.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
