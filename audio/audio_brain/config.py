"""Shared constants for the audio pipeline.

These values are imported by every pipeline and the CLI. Keep them in one
place so behavioural tweaks are a single-line edit and the C++ receiver
schema can be cross-referenced quickly.
"""
from __future__ import annotations

from typing import Final

import numpy as np

# --------------------------------------------------------------------------- #
# Streaming / timing                                                          #
# --------------------------------------------------------------------------- #

#: Samples per audio block. ``2048 @ 44.1 kHz`` is ~46 ms of latency, the
#: sweet spot between FFT resolution and real-time responsiveness.
CHUNK: Final[int] = 2048

#: Dashboard refresh rate (Hz). The OSC stream is *not* rate-limited; only
#: the on-screen render is throttled so the terminal can keep up.
PRINT_HZ: Final[int] = 10

#: How much onset history (in seconds) feeds the tempo estimator.
TEMPO_WINDOW_S: Final[float] = 4.0

#: How often (in seconds) the tempo estimator recomputes a BPM value.
TEMPO_UPDATE_S: Final[float] = 2.0

# --------------------------------------------------------------------------- #
# Perceptual frequency bands                                                  #
# --------------------------------------------------------------------------- #

#: Three perceptually-meaningful frequency bands as ``(label, lo_hz, hi_hz)``
#: triples. Each band gets its own :class:`RollingPeak` normaliser so the
#: bars adapt to recent energy, which matches what the human ear cares about.
BANDS: Final[list[tuple[str, int, int]]] = [
    ("bass",   20,    250),    # sub + bass
    ("mid",    250,   4000),   # vocals + body + presence
    ("treble", 4000,  16000),  # treble + air
]

#: Just the labels of :data:`BANDS`, in the same order.
BAND_LABELS: Final[list[str]] = [b[0] for b in BANDS]

# --------------------------------------------------------------------------- #
# Perceptual mapping ranges                                                   #
# --------------------------------------------------------------------------- #

#: Spectral-centroid log range, lower bound. ``50 Hz → 0.0`` perceptual.
CENTROID_LOG_MIN: Final[float] = float(np.log(50.0))

#: Spectral-centroid log range, upper bound. ``10 kHz → 1.0`` perceptual.
CENTROID_LOG_MAX: Final[float] = float(np.log(10000.0))

#: Crest-factor log range, lower bound. A pure sine has crest ≈ √2, which
#: maps to smoothness ``1.0``.
CREST_LOG_MIN: Final[float] = float(np.log(1.4))

#: Crest-factor log range, upper bound. Spiky transients hit crest ≈ 12.
CREST_LOG_MAX: Final[float] = float(np.log(12.0))

#: Floor for the dBFS readout. ``rms ≤ 1e-4`` reads as ``-80 dB``.
VOLUME_DB_FLOOR: Final[float] = -80.0

# --------------------------------------------------------------------------- #
# Pitch labelling                                                             #
# --------------------------------------------------------------------------- #

#: Pitch-class names indexed ``0..11`` (``C..B``). Used by the dashboard
#: when rendering the ``Note:`` line.
NOTES: Final[list[str]] = [
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
]

# --------------------------------------------------------------------------- #
# Network defaults                                                            #
# --------------------------------------------------------------------------- #

#: Default OSC destination host. Localhost matches the original behaviour
#: (and the C++ ``osc_receiver`` binds to ``INADDR_LOOPBACK``).
DEFAULT_OSC_HOST: Final[str] = "127.0.0.1"

#: Default OSC destination port (matches the C++ receiver's default).
DEFAULT_OSC_PORT: Final[int] = 9000

#: Default mic / loopback sample rate. ``44.1 kHz`` is universally accepted
#: by audio drivers on Linux, macOS, and Windows.
DEFAULT_RATE: Final[int] = 44100
