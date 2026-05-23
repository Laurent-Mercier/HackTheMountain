"""Rolling-peak normaliser used for visual / perceptual scaling.

The pipeline emits per-band energies that vary by orders of magnitude
between songs. To keep the visual bars comparable across tracks, every
band is divided by its recent peak (with exponential decay).
"""
from __future__ import annotations

import numpy as np


class RollingPeak:
    """Per-element running peak with exponential decay.

    Each call to :meth:`normalize` returns ``x / peak(x)`` clipped to
    ``[0, 1]``. The internal peak slowly decays by ``decay`` per call so
    a quiet section eventually grows visible bars while a loud section
    still hits full scale immediately.

    Parameters
    ----------
    n
        Number of independent channels (typically the count of frequency
        bands — 3 in this project).
    decay
        Multiplicative decay applied to the stored peak on every call.
        ``0.995`` ≈ ~200 calls (~20 s @ 10 Hz) to halve.
    floor
        Minimum stored peak value; prevents division by zero on cold
        start-up and keeps the very first frame from going to ``inf``.
    """

    __slots__ = ("peaks", "decay", "floor")

    def __init__(self, n: int, decay: float = 0.995, floor: float = 1e-6) -> None:
        self.peaks = np.full(n, floor, dtype=np.float32)
        self.decay = decay
        self.floor = floor

    def normalize(self, x: np.ndarray) -> np.ndarray:
        """Scale ``x`` against the running peak; update the peak.

        ``x`` is treated element-wise — band 0 is normalised against
        peak 0, etc. The returned array is always in ``[0, 1]``.
        """
        self.peaks = np.maximum(self.peaks * self.decay, x.astype(np.float32))
        return np.clip(x / np.maximum(self.peaks, self.floor), 0.0, 1.0)
