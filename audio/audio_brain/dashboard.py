"""ANSI-driven in-place dashboard renderer for the producer side."""
from __future__ import annotations

import sys
from typing import Any, Mapping

import numpy as np

from .config import BAND_LABELS

#: Number of lines drawn by :meth:`Dashboard.render`. The ANSI cursor-up
#: trick relies on this being constant — keep it in sync with the
#: ``lines`` list inside :meth:`Dashboard.render`.
DASHBOARD_HEIGHT = 8


class Dashboard:
    """Multi-line, in-place dashboard renderer.

    The first call prints an 8-line block. Subsequent calls move the
    cursor up :data:`DASHBOARD_HEIGHT` lines and overwrite each line, so
    the values appear to update in place rather than scrolling off the
    top of the terminal.

    Parameters
    ----------
    title
        Text shown after ``─── `` on the top border. Lets the receiver
        differentiate between the file / mic / loopback senders at a
        glance.
    """

    def __init__(self, title: str = "audio (sender)") -> None:
        self.title = title
        self.first = True

    def render(
        self,
        t: float,
        features: Mapping[str, Any],
        seq: int,
        *,
        total_time: float | None = None,
    ) -> None:
        """Re-paint the dashboard for a single feature snapshot.

        Parameters
        ----------
        t
            Stream-relative seconds since playback / capture started.
        features
            One mapping as returned by
            :meth:`audio_brain.extractor.FeatureExtractor.__call__`.
        seq
            Frame counter to display next to ``Time:``.
        total_time
            File duration or elapsed time (see :attr:`BasePipeline.total_time_s`).
        """
        total = float(t if total_time is None else total_time)
        bands = [features["bass"], features["mid"], features["treble"]]
        dom_band = BAND_LABELS[int(np.argmax(bands))]

        bpm_str = f"{features['bpm']:.1f}" if features["bpm"] > 0 else "---"

        if total > t + 0.05:
            time_line = f"  Time:        {t:7.2f} / {total:7.2f} s  (frame #{seq})"
        else:
            time_line = f"  Time:        {t:7.2f} s     (frame #{seq})"
        lines = [
            f"─── {self.title} {'─' * (44 - len(self.title))}",
            time_line,
            f"  Volume:      {features['volume_db']:+6.1f} dB",
            f"  Frequency:   {dom_band:<6}      "
            f"(bass={bands[0]:.2f} mid={bands[1]:.2f} treble={bands[2]:.2f})",
            f"  Centroid:    {features['centroid_hz']:6.0f} Hz    "
            f"({features['centroid_n']*100:3.0f} % perceptual)",
            f"  Smoothness:  {features['smoothness']:.2f}         (0 = spiky, 1 = smooth)",
            f"  Speed:       {bpm_str:>5} BPM",
            "─" * 50,
        ]
        assert len(lines) == DASHBOARD_HEIGHT, (
            "DASHBOARD_HEIGHT does not match the number of rendered lines"
        )

        out = sys.stdout
        if not self.first:
            out.write(f"\x1b[{DASHBOARD_HEIGHT}A")    # cursor up N lines
        else:
            self.first = False
        for line in lines:
            out.write("\r\x1b[2K" + line + "\n")      # clear line + redraw
        out.flush()
