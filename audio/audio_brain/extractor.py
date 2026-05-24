"""Streaming feature extractor — one FFT, three bands, chroma, tempo, pYIN pitch.

Per chunk the extractor performs:

* one ``np.fft.rfft`` (the FFT we'd have to do anyway),
* :func:`librosa.feature.chroma_stft` for the 12-D harmony vector on the wire,
* :func:`librosa.pyin` for fundamental frequency → MIDI note (replaces the old
  chroma-argmax + octave-scan heuristic),
* everything else in plain NumPy — bands, centroid, crest factor,
  spectral-flux onset envelope.

State carried between calls:

* ``prev_mag``       — previous magnitude spectrum (for spectral flux),
* ``onset_history``  — bounded deque feeding the periodic tempo estimator,
* ``band_norm``      — :class:`RollingPeak` for the three EQ bands,
* ``tempo_bpm``      — last successful BPM estimate,
* ``_smooth_f0``     — EMA-smoothed fundamental (Hz) for stable note labels.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Optional

import librosa
import numpy as np

from .config import (
    BANDS,
    CENTROID_LOG_MAX,
    CENTROID_LOG_MIN,
    CHUNK,
    CREST_LOG_MAX,
    CREST_LOG_MIN,
    PITCH_CONFIDENCE_DECAY,
    PITCH_FMAX_HZ,
    PITCH_FMIN_HZ,
    PITCH_MIN_RMS,
    PITCH_SMOOTH_ALPHA,
    PITCH_VOICED_THRESH,
    TEMPO_UPDATE_S,
    TEMPO_WINDOW_S,
    VOLUME_DB_FLOOR,
)
from .normalizer import RollingPeak

# Tempo estimator API moved between librosa versions; pick whichever is
# available so the package keeps working on librosa < 0.10 and ≥ 0.10.
try:
    _tempo_fn = librosa.feature.tempo            # librosa >= 0.10
except AttributeError:                           # pragma: no cover
    _tempo_fn = librosa.beat.tempo               # older librosa


class FeatureExtractor:
    """Lean, stateful feature extractor for a single mono stream.

    The instance is callable: ``extractor(mono)`` returns a dictionary
    with the exact keys :class:`OscSender` expects.

    Parameters
    ----------
    rate
        Sample rate of the incoming mono buffer (Hz).
    chunk
        Length of every input buffer in samples. Must be constant across
        calls — internal scratch buffers are sized once at construction.
    tempo_window_s
        Onset-history length used for tempo estimation.
    tempo_update_s
        How often (in seconds) tempo is recomputed.
    """

    __slots__ = (
        "rate", "chunk", "win", "freqs", "band_masks", "band_norm",
        "prev_mag", "onset_history",
        "tempo_update_chunks", "chunks_since_tempo", "tempo_bpm",
        "_smooth_f0", "_last_midi", "_note_confidence",
        "_pyin_hop",
    )

    def __init__(
        self,
        rate: int,
        chunk: int = CHUNK,
        tempo_window_s: float = TEMPO_WINDOW_S,
        tempo_update_s: float = TEMPO_UPDATE_S,
    ) -> None:
        self.rate = rate
        self.chunk = chunk
        self.win = np.hanning(chunk).astype(np.float32)
        self.freqs = np.fft.rfftfreq(chunk, 1.0 / rate).astype(np.float32)
        self.band_masks = [
            (self.freqs >= lo) & (self.freqs < hi) for _, lo, hi in BANDS
        ]
        self.band_norm = RollingPeak(len(BANDS), decay=0.995)

        self.prev_mag: Optional[np.ndarray] = None

        n_frames = int(np.ceil(tempo_window_s * rate / chunk))
        self.onset_history: deque = deque(maxlen=n_frames)
        self.tempo_update_chunks = int(np.ceil(tempo_update_s * rate / chunk))
        self.chunks_since_tempo = 0
        self.tempo_bpm: Optional[float] = None

        self._smooth_f0: Optional[float] = None
        self._last_midi: int = -1
        self._note_confidence: float = 0.0
        # Several pYIN frames inside each CHUNK block stabilise the median.
        self._pyin_hop = max(256, chunk // 4)

    # ------------------------------------------------------------------ #
    # Lifecycle helpers                                                  #
    # ------------------------------------------------------------------ #

    def reset_state(self) -> None:
        """Wipe all rolling state.

        Used after the warm-up phase so the very first real chunk starts
        from a clean slate (no ghost spectral flux, no leftover BPM).
        """
        self.prev_mag = None
        self.onset_history.clear()
        self.tempo_bpm = None
        self.chunks_since_tempo = 0
        self._smooth_f0 = None
        self._last_midi = -1
        self._note_confidence = 0.0

    def warmup(self, n: int = 3) -> None:
        """Run a few low-noise chunks through the extractor.

        Triggers numba JIT compilation inside librosa and avoids a
        chroma-empty warning on the first real chunk. State is reset
        afterwards so warm-up output never leaks into real features.
        """
        warmup = np.random.randn(self.chunk).astype(np.float32) * 1e-4
        for _ in range(n):
            self(warmup)
        self.reset_state()

    # ------------------------------------------------------------------ #
    # Pitch (pYIN)                                                       #
    # ------------------------------------------------------------------ #

    def _estimate_pitch(self, mono: np.ndarray, rms: float) -> tuple[int, int, float]:
        """Return ``(midi_note, pitch_class, confidence)`` for this chunk."""
        if rms < PITCH_MIN_RMS:
            self._note_confidence *= PITCH_CONFIDENCE_DECAY
            if self._last_midi >= 0 and self._note_confidence >= 0.20:
                pc = self._last_midi % 12
                return self._last_midi, pc, self._note_confidence
            return 0, 0, self._note_confidence

        try:
            f0, voiced_flag, voiced_probs = librosa.pyin(
                mono.astype(np.float64),
                fmin=PITCH_FMIN_HZ,
                fmax=PITCH_FMAX_HZ,
                sr=self.rate,
                frame_length=self.chunk,
                hop_length=self._pyin_hop,
                fill_na=np.nan,
            )
        except Exception:
            self._note_confidence *= PITCH_CONFIDENCE_DECAY
            if self._last_midi >= 0 and self._note_confidence >= 0.20:
                pc = self._last_midi % 12
                return self._last_midi, pc, self._note_confidence
            return 0, 0, self._note_confidence

        mask = np.asarray(voiced_flag, dtype=bool) & (
            np.asarray(voiced_probs) >= PITCH_VOICED_THRESH
        )
        f0_voiced = np.asarray(f0, dtype=np.float64)[mask]
        f0_voiced = f0_voiced[np.isfinite(f0_voiced) & (f0_voiced > 0.0)]

        if f0_voiced.size == 0:
            self._note_confidence *= PITCH_CONFIDENCE_DECAY
            if self._last_midi >= 0 and self._note_confidence >= 0.20:
                pc = self._last_midi % 12
                return self._last_midi, pc, self._note_confidence
            return 0, 0, self._note_confidence

        f0_med = float(np.median(f0_voiced))
        conf = float(np.median(np.asarray(voiced_probs, dtype=np.float64)[mask]))

        if self._smooth_f0 is None:
            self._smooth_f0 = f0_med
        else:
            a = PITCH_SMOOTH_ALPHA
            self._smooth_f0 = a * f0_med + (1.0 - a) * self._smooth_f0

        midi = int(round(librosa.hz_to_midi(self._smooth_f0)))
        midi = int(np.clip(midi, 0, 127))
        self._last_midi = midi
        self._note_confidence = float(np.clip(conf, 0.0, 1.0))
        return midi, midi % 12, self._note_confidence

    # ------------------------------------------------------------------ #
    # Per-chunk feature pipeline                                         #
    # ------------------------------------------------------------------ #

    def __call__(self, mono: np.ndarray) -> dict[str, Any]:
        """Extract every feature for one mono ``CHUNK``-sized buffer."""
        rate = self.rate

        # Time-domain stats — RMS, peak, crest factor, dBFS, smoothness.
        sq = mono * mono
        rms = float(np.sqrt(sq.mean() + 1e-12))
        peak = float(np.abs(mono).max())
        crest = peak / max(rms, 1e-9)

        volume_db = max(20.0 * np.log10(max(rms, 1e-5)), VOLUME_DB_FLOOR)
        spikiness = float(np.clip(
            (np.log(max(crest, 1.4)) - CREST_LOG_MIN)
            / (CREST_LOG_MAX - CREST_LOG_MIN),
            0.0, 1.0,
        ))
        smoothness = 1.0 - spikiness

        # One FFT, magnitude spectrum.
        mag = np.abs(np.fft.rfft(mono * self.win)).astype(np.float32)
        mag_sum = float(mag.sum()) + 1e-12

        # Spectral centroid — manual, faster than librosa for one frame.
        centroid_hz = float((self.freqs * mag).sum() / mag_sum)
        centroid_n = float(np.clip(
            (np.log(max(centroid_hz, 1.0)) - CENTROID_LOG_MIN)
            / (CENTROID_LOG_MAX - CENTROID_LOG_MIN),
            0.0, 1.0,
        ))

        # Three-band energies + perceptual rolling-peak normalisation.
        mag2 = mag * mag
        bands_raw = np.array(
            [float(mag2[m].sum()) for m in self.band_masks],
            dtype=np.float32,
        )
        bands_n = self.band_norm.normalize(bands_raw)

        # Chroma (harmony vector on the wire) + pYIN note label.
        chroma = librosa.feature.chroma_stft(
            S=mag2[:, None], sr=rate,
        )[:, 0].astype(np.float32)
        midi_note, pitch_class, note_conf = self._estimate_pitch(mono, rms)
        if note_conf >= 0.20:
            chroma[pitch_class] = float(
                max(chroma[pitch_class], note_conf)
            )

        # Spectral flux → onset envelope (state for tempo).
        if self.prev_mag is None or self.prev_mag.shape != mag.shape:
            onset_raw = 0.0
        else:
            diff = mag - self.prev_mag
            np.maximum(diff, 0.0, out=diff)            # in-place HWR
            onset_raw = float(np.sqrt((diff * diff).sum()))
        self.prev_mag = mag
        self.onset_history.append(onset_raw)

        # Tempo estimate (rolling buffer, every ~2 s once the deque is full).
        self.chunks_since_tempo += 1
        if (self.chunks_since_tempo >= self.tempo_update_chunks
                and len(self.onset_history) == self.onset_history.maxlen):
            env = np.asarray(self.onset_history, dtype=np.float32)
            if env.max() > 1e-6:
                try:
                    bpm = _tempo_fn(
                        onset_envelope=env, sr=rate, hop_length=self.chunk,
                    )
                    self.tempo_bpm = float(np.atleast_1d(bpm)[0])
                except Exception:
                    pass
            self.chunks_since_tempo = 0

        return {
            "volume_db":   volume_db,
            "bass":        float(bands_n[0]),
            "mid":         float(bands_n[1]),
            "treble":      float(bands_n[2]),
            "bpm":         self.tempo_bpm if self.tempo_bpm is not None else -1.0,
            "smoothness":  smoothness,
            "centroid_hz": centroid_hz,
            "centroid_n":  centroid_n,
            "note":        midi_note,
            "pitch_class": pitch_class,
            "note_confidence": note_conf,
            "chroma":      chroma,
        }
