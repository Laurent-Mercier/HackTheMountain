"""Real-time audio feature extractor + OSC broadcaster.

The :mod:`audio_brain` package is the producer side of the audio pipeline.
It plays a song (or captures from a microphone / virtual loopback device)
through PortAudio (via host Pulse in Docker), extracts perceptual features
per chunk, and pushes ``/audio/frame`` OSC packets to a UDP receiver.
Use ``./play.sh`` in ``audio/`` for the full receiver + sender workflow.

Module map
----------

* :mod:`audio_brain.config`     — shared tunables and label tables
* :mod:`audio_brain.osc`        — :class:`OscSender` (wire schema)
* :mod:`audio_brain.normalizer` — :class:`RollingPeak`
* :mod:`audio_brain.extractor`  — :class:`FeatureExtractor`
* :mod:`audio_brain.dashboard`  — :class:`Dashboard` (ANSI in-place render)
* :mod:`audio_brain.devices`    — PortAudio device helpers + ALSA mute
* :mod:`audio_brain.loaders`    — file-streaming loader (soundfile/librosa)
* :mod:`audio_brain.pipelines`  — :class:`BasePipeline` + concrete
  :class:`FilePlaybackPipeline`, :class:`MicCapturePipeline`,
  :class:`LoopbackPipeline`
* :mod:`audio_brain.cli`        — :func:`main` CLI entry point

Public API: the same classes the legacy ``main.py`` exposed plus the new
pipeline classes, re-exported here for convenience.
"""
from __future__ import annotations

from .config import BAND_LABELS, BANDS, CHUNK, PRINT_HZ
from .dashboard import Dashboard
from .devices import find_loopback_input, list_devices, silence_alsa_noise
from .extractor import FeatureExtractor
from .loaders import load_streamer
from .normalizer import RollingPeak
from .osc import OscSender
from .pipelines import (
    BasePipeline,
    FilePlaybackPipeline,
    LoopbackPipeline,
    MicCapturePipeline,
)

__all__ = [
    "BANDS",
    "BAND_LABELS",
    "CHUNK",
    "PRINT_HZ",
    "BasePipeline",
    "Dashboard",
    "FeatureExtractor",
    "FilePlaybackPipeline",
    "LoopbackPipeline",
    "MicCapturePipeline",
    "OscSender",
    "RollingPeak",
    "find_loopback_input",
    "list_devices",
    "load_streamer",
    "silence_alsa_noise",
]
