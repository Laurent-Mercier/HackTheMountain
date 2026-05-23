"""Real-time audio playback + feature extraction (music-brain edition).

Plays a song (WAV / MP3 / FLAC / OGG / ...) through the default audio output
while extracting a rich bundle of per-chunk features and a rolling tempo
estimate, then prints a compact dashboard line in sync with playback.

The output stream's blocking ``write()`` paces the loop to real time, so
feature lines scroll past in lockstep with what you're hearing.

Usage:
    uv run python main.py path/to/song.mp3
"""
from __future__ import annotations

import argparse
import ctypes
import sys
from collections import deque
from pathlib import Path
from typing import Iterator, Optional, Tuple

import librosa
import numpy as np
import pyaudio
import soundfile as sf
from pythonosc.udp_client import SimpleUDPClient

# --------------------------------------------------------------------------- #
# Tunables                                                                    #
# --------------------------------------------------------------------------- #

CHUNK = 2048      # samples per block. 2048 @ 44.1 kHz = ~46 ms latency
PRINT_HZ = 10     # dashboard refresh rate

TEMPO_WINDOW_S = 4.0   # how much onset history to use for tempo estimation
TEMPO_UPDATE_S = 2.0   # how often to recompute tempo

<<<<<<< HEAD
# Standard 7-band EQ splits (Hz).
BANDS = [
    ("sub",    20,    60),
    ("bass",   60,    250),
    ("lo-mid", 250,   500),
    ("mid",    500,   2000),
    ("hi-mid", 2000,  4000),
    ("treble", 4000,  6000),
    ("air",    6000,  16000),
]

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
BLOCKS = " ▁▂▃▄▅▆▇█"   # 9 levels for unicode bar rendering
=======
# Three perceptually meaningful frequency bands (Hz).
BANDS = [
    ("bass",   20,    250),    # sub + bass
    ("mid",    250,   4000),   # vocals + body + presence
    ("treble", 4000,  16000),  # treble + air
]
BAND_LABELS = [b[0] for b in BANDS]

# Perceptual mapping ranges (used to produce 0..1 "intuitive" values).
CENTROID_LOG_MIN = float(np.log(50.0))          # 50 Hz  → 0.0
CENTROID_LOG_MAX = float(np.log(10000.0))       # 10 kHz → 1.0
CREST_LOG_MIN    = float(np.log(1.4))           # sine (≈ √2) → smoothness 1
CREST_LOG_MAX    = float(np.log(12.0))          # very spiky  → smoothness 0
VOLUME_DB_FLOOR  = -80.0                        # rms ≤ 1e-4 reads as -80 dB

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
>>>>>>> origin/main


# --------------------------------------------------------------------------- #
# Linux ALSA stderr noise muter + device listing                              #
# --------------------------------------------------------------------------- #

def silence_alsa_noise() -> None:
    """Mute libasound's stderr probing on Linux."""
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
    silence_alsa_noise._handler = handler


def list_devices() -> None:
    p = pyaudio.PyAudio()
    try:
        print(f"{'idx':>3}  {'in':>2} {'out':>3}  {'rate':>6}  name")
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            print(
                f"{i:>3}  "
                f"{info['maxInputChannels']:>2} "
                f"{info['maxOutputChannels']:>3}  "
                f"{int(info['defaultSampleRate']):>6}  "
                f"{info['name']}"
            )
        try:
            default = p.get_default_output_device_info()
            print(f"\ndefault output: [{default['index']}] {default['name']}")
        except OSError:
            print("\nno default output device found")
    finally:
        p.terminate()


# --------------------------------------------------------------------------- #
# File loading (soundfile streaming + librosa in-memory fallback)             #
# --------------------------------------------------------------------------- #

def load_streamer(path: Path) -> Tuple[int, int, Iterator[np.ndarray]]:
    """Open ``path`` and return ``(rate, channels, block_iterator)``."""
    try:
        song = sf.SoundFile(str(path))
    except RuntimeError as e:
        print(
            f"[info] soundfile can't open this file ({e}); "
            f"falling back to a full librosa decode.",
            file=sys.stderr,
        )
        y, rate = librosa.load(str(path), sr=None, mono=False)
        if y.ndim == 1:
            y = y[np.newaxis, :]
        y = y.T.astype(np.float32)
        channels = y.shape[1]

        def blocks_from_memory() -> Iterator[np.ndarray]:
            for i in range(0, len(y), CHUNK):
                yield y[i:i + CHUNK]

        return rate, channels, blocks_from_memory()

    rate, channels = song.samplerate, song.channels

    def blocks_from_disk() -> Iterator[np.ndarray]:
        try:
            yield from song.blocks(
                blocksize=CHUNK, dtype="float32", always_2d=True,
            )
        finally:
            song.close()

    return rate, channels, blocks_from_disk()


# --------------------------------------------------------------------------- #
# Rolling-peak normalizer (for visual scaling of bars / onset strength)       #
# --------------------------------------------------------------------------- #

class RollingPeak:
    """Per-element running peak with exponential decay.

    Lets quiet sections grow visible bars after a while, while loud
    sections still hit full scale. ``decay`` close to 1 = slow adaptation.
    """

    def __init__(self, n: int, decay: float = 0.995, floor: float = 1e-6):
        self.peaks = np.full(n, floor, dtype=np.float32)
        self.decay = decay
        self.floor = floor

    def normalize(self, x: np.ndarray) -> np.ndarray:
        self.peaks = np.maximum(self.peaks * self.decay, x.astype(np.float32))
        return np.clip(x / np.maximum(self.peaks, self.floor), 0.0, 1.0)


# --------------------------------------------------------------------------- #
# OSC sender — pushes feature snapshots to a visualizer over UDP localhost.   #
# --------------------------------------------------------------------------- #

class OscSender:
    """Send each feature snapshot as one ``/audio/frame`` OSC message.

<<<<<<< HEAD
    Wire schema (also documented in osc_receiver.cpp):

        address: /audio/frame
        types:   ,i + 42×f          (1 int + 42 floats)
        args:
          0  : seq                 (int, monotonic)
          1  : t_seconds           (float, since start of stream)
          2  : rms
          3  : peak
          4  : crest
          5  : zcr
          6  : centroid_hz
          7  : rolloff_hz
          8  : bandwidth_hz
          9  : flatness
          10 : onset_norm          (0..1)
          11 : tempo_bpm           (-1.0 if unknown)
          12-18 : bands_norm[7]    (0..1)
          19-30 : chroma[12]       (0..1)
          31-42 : mfcc[12]         (unbounded)
=======
    Schema (mirrored in osc_receiver.cpp — keep both in sync):

        address : /audio/frame
        types   : ,i  f f f f f f f f f  i  f×12          (23 args)
        args:
          0    seq           int    monotonic counter (loss detection)
          1    t             float  seconds since stream start
          2    volume_db     float  dBFS, clamped to [-80, 0]
          3    bass          float  0..1   low-band   (20..250  Hz)
          4    mid           float  0..1   mid-band   (250..4k  Hz)
          5    treble        float  0..1   high-band  (4k..16k  Hz)
          6    bpm           float  beats/min, -1.0 if unknown
          7    smoothness    float  0..1   1 = sine-like, 0 = transient
          8    centroid_hz   float  raw centroid in Hz
          9    centroid_n    float  0..1   log-mapped centroid (50..10k Hz)
          10   note          int    dominant pitch class 0..11 (C=0, B=11)
          11-22 chroma[12]   float  pitch-class energies, each 0..1

        Payload: ~136 bytes/packet. At chunk=2048 @ 44.1 kHz that's ~3 KB/s.
>>>>>>> origin/main
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9000):
        self.client = SimpleUDPClient(host, port)
        self.host = host
        self.port = port
        self.seq = 0

<<<<<<< HEAD
    def send(self, t: float, feats: dict) -> None:
        tempo = feats.get("tempo")
        values = [
            int(self.seq),
            float(t),
            float(feats["rms"]),
            float(feats["peak"]),
            float(feats["crest"]),
            float(feats["zcr"]),
            float(feats["centroid"]),
            float(feats["rolloff"]),
            float(feats["bandwidth"]),
            float(feats["flatness"]),
            float(feats["onset_norm"]),
            float(tempo) if tempo is not None else -1.0,
        ]
        values.extend(float(v) for v in feats["bands_norm"])
        values.extend(float(v) for v in feats["chroma"])
        values.extend(float(v) for v in feats["mfcc"])
        try:
            self.client.send_message("/audio/frame", values)
        except OSError:
            # Network hiccups should never kill the audio loop.
=======
    def send(self, t: float, f: dict) -> None:
        values = [
            int(self.seq),
            float(t),
            float(f["volume_db"]),
            float(f["bass"]), float(f["mid"]), float(f["treble"]),
            float(f["bpm"]),
            float(f["smoothness"]),
            float(f["centroid_hz"]),
            float(f["centroid_n"]),
            int(f["note"]),
        ]
        values.extend(float(v) for v in f["chroma"])
        try:
            self.client.send_message("/audio/frame", values)
        except OSError:
            # Network hiccups must never kill the audio loop.
>>>>>>> origin/main
            pass
        self.seq += 1


# --------------------------------------------------------------------------- #
# Stateful feature extractor                                                  #
# --------------------------------------------------------------------------- #

# Try to find the right tempo function across librosa versions.
try:
    _tempo_fn = librosa.feature.tempo            # librosa >= 0.10
except AttributeError:                           # pragma: no cover
    _tempo_fn = librosa.beat.tempo               # older librosa


class FeatureExtractor:
<<<<<<< HEAD
    """Streaming feature extractor.

    Call the instance with each mono chunk (``float32``, length ``CHUNK``)
    to get a dict of features. Keeps state for spectral flux (→ onset
    strength) and for periodic tempo estimation.
    """

    def __init__(self, rate: int, chunk: int = CHUNK,
                 tempo_window_s: float = TEMPO_WINDOW_S,
                 tempo_update_s: float = TEMPO_UPDATE_S):
        self.rate = rate
        self.chunk = chunk
        self.win = np.hanning(chunk).astype(np.float32)
        self.freqs = np.fft.rfftfreq(chunk, 1.0 / rate)
        self.band_masks = [
            (self.freqs >= lo) & (self.freqs < hi) for _, lo, hi in BANDS
        ]
        self.band_norm = RollingPeak(len(BANDS), decay=0.995)
        self.onset_norm = RollingPeak(1, decay=0.99)

        # Spectral flux state
        self.prev_mag: Optional[np.ndarray] = None

        # Onset envelope history for tempo estimation
        n_frames = int(np.ceil(tempo_window_s * rate / chunk))
        self.onset_history: deque = deque(maxlen=n_frames)
        self.tempo_update_chunks = int(np.ceil(tempo_update_s * rate / chunk))
        self.chunks_since_tempo = 0
        self.tempo_bpm: Optional[float] = None

    def __call__(self, mono: np.ndarray) -> dict:
        rate = self.rate

        # ---- time domain ---------------------------------------------------
        rms = float(np.sqrt(np.mean(mono ** 2) + 1e-12))
        peak = float(np.max(np.abs(mono)))
        crest = peak / (rms + 1e-12)
        zcr = float(np.mean(np.abs(np.diff(np.signbit(mono)))))

        # ---- one windowed FFT, reused everywhere --------------------------
        mag = np.abs(np.fft.rfft(mono * self.win)).astype(np.float32)
        S_mag = mag[:, None]
        S_pow = S_mag ** 2

        # ---- spectral shape ------------------------------------------------
        centroid  = float(librosa.feature.spectral_centroid (S=S_mag, sr=rate)[0, 0])
        rolloff   = float(librosa.feature.spectral_rolloff  (S=S_mag, sr=rate)[0, 0])
        bandwidth = float(librosa.feature.spectral_bandwidth(S=S_mag, sr=rate)[0, 0])
        flatness  = float(librosa.feature.spectral_flatness (S=S_mag)        [0, 0])

        # ---- band energies -------------------------------------------------
        bands = np.array(
            [float(np.sum(mag[m] ** 2)) for m in self.band_masks],
            dtype=np.float32,
        )
        bands_norm = self.band_norm.normalize(bands)

        # ---- chroma & MFCC -------------------------------------------------
        chroma = librosa.feature.chroma_stft(S=S_pow, sr=rate)[:, 0]
        mel = librosa.feature.melspectrogram(S=S_pow, sr=rate, n_mels=64)
        # MFCC 1..12 — drop coef 0 (overall log-energy ≈ rms).
        mfcc = librosa.feature.mfcc(S=librosa.power_to_db(mel), n_mfcc=13)[1:, 0]

        # ---- onset strength (half-wave-rectified spectral flux) -----------
        if self.prev_mag is None or self.prev_mag.shape != mag.shape:
            onset_raw = 0.0
        else:
            diff = np.maximum(mag - self.prev_mag, 0.0)
            onset_raw = float(np.sqrt(np.sum(diff ** 2)))
        self.prev_mag = mag

        onset_norm = float(
            self.onset_norm.normalize(np.array([onset_raw], dtype=np.float32))[0]
        )
        self.onset_history.append(onset_raw)

        # ---- tempo (rolling buffer, infrequent) ---------------------------
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
            "rms": rms, "peak": peak, "crest": crest, "zcr": zcr,
            "centroid": centroid, "rolloff": rolloff,
            "bandwidth": bandwidth, "flatness": flatness,
            "bands": bands,
            "bands_norm": bands_norm,
            "chroma": chroma,
            "mfcc": mfcc,
            "onset": onset_raw,
            "onset_norm": onset_norm,
            "tempo": self.tempo_bpm,
        }


# --------------------------------------------------------------------------- #
# Compact dashboard formatter                                                 #
# --------------------------------------------------------------------------- #

def _bars(values: np.ndarray) -> str:
    """Render an array of 0..1 floats as a unicode block-bar string."""
    n = len(BLOCKS) - 1
    return "".join(BLOCKS[min(n, max(0, int(round(v * n))))] for v in values)


def format_dashboard(t: float, f: dict) -> str:
    band_bars = _bars(f["bands_norm"])
    note_idx = int(np.argmax(f["chroma"]))
    note = NOTES[note_idx]
    note_strength = float(f["chroma"][note_idx])

    bpm = f"{f['tempo']:5.1f}" if f["tempo"] is not None else "  ---"
    onset_bar = BLOCKS[min(len(BLOCKS) - 1, int(f["onset_norm"] * (len(BLOCKS) - 1)))]

    return (
        f"t={t:6.2f}s │ "
        f"[{band_bars}] │ "
        f"rms={f['rms']:.3f} pk={f['peak']:.2f} crest={f['crest']:4.1f} "
        f"zcr={f['zcr']:.2f} │ "
        f"cen={f['centroid']:5.0f} rol={f['rolloff']:5.0f} "
        f"bw={f['bandwidth']:4.0f} flat={f['flatness']:.2f} │ "
        f"note={note:>2}({note_strength:.2f}) │ "
        f"mfcc1..3=[{f['mfcc'][0]:+5.1f},{f['mfcc'][1]:+5.1f},{f['mfcc'][2]:+5.1f}] │ "
        f"onset {onset_bar} │ "
        f"{bpm} BPM"
    )


def format_header() -> str:
    band_labels = " ".join(b[0][:3] for b in BANDS)
    return (
        f"    time   │ bands [{band_labels}] │ amplitude            │ "
        f"timbre                          │ note      │ "
        f"mfcc 1..3              │ onset │ tempo"
=======
    """Streaming feature extractor — lean version.

    Computes only the features that are actually sent over OSC. Drops the
    heavy librosa calls (mel + mfcc + rolloff + bandwidth + flatness) for
    a noticeable per-chunk CPU win. Per chunk we do:

      * 1 ``np.fft.rfft``  (the FFT we'd have to do anyway)
      * 1 ``librosa.chroma_stft`` (only librosa call left)
      * everything else in plain NumPy

    State: previous magnitude spectrum (for spectral flux), an onset-strength
    history for periodic tempo estimation, and rolling-peak normalisers
    for the three EQ bands.
    """

    __slots__ = (
        "rate", "chunk", "win", "freqs", "band_masks", "band_norm",
        "prev_mag", "onset_history",
        "tempo_update_chunks", "chunks_since_tempo", "tempo_bpm",
>>>>>>> origin/main
    )

    def __init__(self, rate: int, chunk: int = CHUNK,
                 tempo_window_s: float = TEMPO_WINDOW_S,
                 tempo_update_s: float = TEMPO_UPDATE_S):
        self.rate = rate
        self.chunk = chunk
        self.win = np.hanning(chunk).astype(np.float32)
        self.freqs = np.fft.rfftfreq(chunk, 1.0 / rate).astype(np.float32)
        self.band_masks = [
            (self.freqs >= lo) & (self.freqs < hi) for _, lo, hi in BANDS
        ]
        self.band_norm = RollingPeak(len(BANDS), decay=0.995)

        # Spectral-flux state (for onset envelope → tempo).
        self.prev_mag: Optional[np.ndarray] = None

        # Onset envelope history → tempo.
        n_frames = int(np.ceil(tempo_window_s * rate / chunk))
        self.onset_history: deque = deque(maxlen=n_frames)
        self.tempo_update_chunks = int(np.ceil(tempo_update_s * rate / chunk))
        self.chunks_since_tempo = 0
        self.tempo_bpm: Optional[float] = None

    def __call__(self, mono: np.ndarray) -> dict:
        rate = self.rate

        # ---- time domain (cheap) ------------------------------------------
        sq = mono * mono
        rms = float(np.sqrt(sq.mean() + 1e-12))
        peak = float(np.abs(mono).max())
        crest = peak / max(rms, 1e-9)

        volume_db = max(20.0 * np.log10(max(rms, 1e-5)), VOLUME_DB_FLOOR)
        # smoothness: 1.0 = sine-like (low crest), 0.0 = percussive transient.
        spikiness = float(np.clip(
            (np.log(max(crest, 1.4)) - CREST_LOG_MIN)
            / (CREST_LOG_MAX - CREST_LOG_MIN),
            0.0, 1.0,
        ))
        smoothness = 1.0 - spikiness

        # ---- one FFT, magnitude spectrum ----------------------------------
        mag = np.abs(np.fft.rfft(mono * self.win)).astype(np.float32)
        mag_sum = float(mag.sum()) + 1e-12

        # ---- spectral centroid (manual — faster than librosa for 1 frame) -
        centroid_hz = float((self.freqs * mag).sum() / mag_sum)
        centroid_n = float(np.clip(
            (np.log(max(centroid_hz, 1.0)) - CENTROID_LOG_MIN)
            / (CENTROID_LOG_MAX - CENTROID_LOG_MIN),
            0.0, 1.0,
        ))

        # ---- 3-band energies + perceptual normalisation -------------------
        mag2 = mag * mag
        bands_raw = np.array(
            [float(mag2[m].sum()) for m in self.band_masks],
            dtype=np.float32,
        )
        bands_n = self.band_norm.normalize(bands_raw)

        # ---- chroma (notes) -----------------------------------------------
        chroma = librosa.feature.chroma_stft(
            S=mag2[:, None], sr=rate,
        )[:, 0]
        note_idx = int(np.argmax(chroma))

        # ---- spectral flux → onset envelope (state for tempo) -------------
        if self.prev_mag is None or self.prev_mag.shape != mag.shape:
            onset_raw = 0.0
        else:
            diff = mag - self.prev_mag
            np.maximum(diff, 0.0, out=diff)            # in-place HWR
            onset_raw = float(np.sqrt((diff * diff).sum()))
        self.prev_mag = mag
        self.onset_history.append(onset_raw)

        # ---- tempo (rolling buffer, every ~2s) ----------------------------
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
            "note":        note_idx,
            "chroma":      chroma.astype(np.float32),
        }


# --------------------------------------------------------------------------- #
# Compact dashboard formatter                                                 #
# --------------------------------------------------------------------------- #

DASHBOARD_HEIGHT = 9   # number of lines drawn by render_dashboard()


class Dashboard:
    """ANSI-driven in-place dashboard renderer.

    First call prints the full dashboard. Subsequent calls move the cursor
    up ``DASHBOARD_HEIGHT`` lines and overwrite each line, so the values
    update in place instead of scrolling.
    """

    def __init__(self, title: str = "audio (sender)"):
        self.title = title
        self.first = True

    def render(self, t: float, f: dict, seq: int) -> None:
        bands = [f["bass"], f["mid"], f["treble"]]
        dom_band = BAND_LABELS[int(np.argmax(bands))]

        note_strength = float(f["chroma"][f["note"]])
        note = NOTES[f["note"]] if note_strength > 0.20 else "--"
        bpm_str = f"{f['bpm']:.1f}" if f["bpm"] > 0 else "---"

        lines = [
            f"─── {self.title} {'─' * (44 - len(self.title))}",
            f"  Time:        {t:7.2f} s     (frame #{seq})",
            f"  Volume:      {f['volume_db']:+6.1f} dB",
            f"  Frequency:   {dom_band:<6}      "
            f"(bass={bands[0]:.2f} mid={bands[1]:.2f} treble={bands[2]:.2f})",
            f"  Centroid:    {f['centroid_hz']:6.0f} Hz    "
            f"({f['centroid_n']*100:3.0f} % perceptual)",
            f"  Smoothness:  {f['smoothness']:.2f}         (0 = spiky, 1 = smooth)",
            f"  Note:        {note:<2}           (strength {note_strength:.2f})",
            f"  Speed:       {bpm_str:>5} BPM",
            "─" * 50,
        ]
        assert len(lines) == DASHBOARD_HEIGHT

        out = sys.stdout
        if not self.first:
            out.write(f"\x1b[{DASHBOARD_HEIGHT}A")    # cursor up N lines
        else:
            self.first = False
        for line in lines:
            out.write("\r\x1b[2K" + line + "\n")      # clear line + redraw
        out.flush()


# --------------------------------------------------------------------------- #
# Main pipeline                                                               #
# --------------------------------------------------------------------------- #

<<<<<<< HEAD
=======
def find_loopback_input(p: pyaudio.PyAudio) -> Optional[int]:
    """Return index of a virtual-loopback input device (BlackHole/Soundflower).

    Looks for common names of virtual audio drivers (macOS = BlackHole /
    Soundflower; Linux = pulse-loopback variants). Returns ``None`` if
    nothing matches — caller should fall back to ``--input-device``.
    """
    needles = ("blackhole", "soundflower", "loopback", "monitor of")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] <= 0:
            continue
        if any(n in info["name"].lower() for n in needles):
            return i
    return None


>>>>>>> origin/main
def warmup_extractor(extractor: "FeatureExtractor") -> None:
    """Run a few silent chunks through the extractor to trigger numba JIT.

    Uses low-level noise (not zeros) so chroma_stft doesn't warn about an
    empty frequency set. Stateful buffers are reset afterwards so the first
    real chunk starts clean.
    """
    print("warming up feature extractors...", end="", flush=True)
    warmup = (np.random.randn(extractor.chunk).astype(np.float32) * 1e-4)
    for _ in range(3):
        extractor(warmup)
    extractor.prev_mag = None
    extractor.onset_history.clear()
    extractor.tempo_bpm = None
    extractor.chunks_since_tempo = 0
    print(" done.")


def play_and_analyze(
    path: Path,
    device_index: Optional[int] = None,
    osc: Optional[OscSender] = None,
) -> None:
    """File mode: stream from disk → speakers + feature dashboard."""
    rate, channels, blocks = load_streamer(path)

    p = pyaudio.PyAudio()
    if device_index is not None:
        info = p.get_device_info_by_index(device_index)
        print(f"using output device [{device_index}] {info['name']}")
    stream = p.open(
        format=pyaudio.paFloat32,
        channels=channels,
        rate=rate,
        output=True,
        output_device_index=device_index,
        frames_per_buffer=CHUNK,
    )

    extractor = FeatureExtractor(rate=rate, chunk=CHUNK)
    warmup_extractor(extractor)

    print(
        f"playing: {path.name} | {rate} Hz | {channels}ch | "
        f"chunk={CHUNK} samples (~{1000 * CHUNK / rate:.0f} ms)"
    )
    if osc is not None:
        print(f"sending OSC to {osc.host}:{osc.port} as /audio/frame")
<<<<<<< HEAD
    print(format_header())
=======

    dashboard = Dashboard(title="audio (sender)")
    sent_seq = 0
>>>>>>> origin/main

    try:
        last_print_t = -1e9
        frames_played = 0
        for block in blocks:
            if len(block) < CHUNK:
                pad = np.zeros((CHUNK - len(block), channels), dtype=np.float32)
                block = np.vstack([block, pad])

            stream.write(np.ascontiguousarray(block).tobytes())

            mono = (block.mean(axis=1) if channels > 1 else block[:, 0]).astype(np.float32)
            feats = extractor(mono)

            t = frames_played / rate
            if osc is not None:
                osc.send(t, feats)
<<<<<<< HEAD

            if t - last_print_t >= 1.0 / PRINT_HZ:
                print(format_dashboard(t, feats))
=======
                sent_seq = osc.seq
            else:
                sent_seq += 1

            if t - last_print_t >= 1.0 / PRINT_HZ:
                dashboard.render(t, feats, sent_seq)
>>>>>>> origin/main
                last_print_t = t

            frames_played += CHUNK
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()


def listen_and_analyze(
    input_device: Optional[int] = None,
    output_device: Optional[int] = None,
    rate: int = 44100,
    playback: bool = True,
    osc: Optional[OscSender] = None,
) -> None:
    """Mic mode: capture from microphone → feature dashboard.

    No audio is written back to the speakers *during* capture, so this is
    safe to run with laptop speakers on (no feedback loop). ``stream.read``
    blocks until a full CHUNK has been captured, which paces the loop to
    real time exactly the same way ``stream.write`` does in file mode.

    On Ctrl-C, if ``playback=True``, the captured audio is played back
    through ``output_device`` before the function returns. A second Ctrl-C
    during playback skips it.
    """
    p = pyaudio.PyAudio()
    try:
        if input_device is not None:
            info = p.get_device_info_by_index(input_device)
        else:
            info = p.get_default_input_device_info()
            input_device = int(info["index"])
        print(f"using input device [{input_device}] {info['name']}")
    except OSError:
        print("error: no input device available", file=sys.stderr)
        p.terminate()
        return

    try:
        in_stream = p.open(
            format=pyaudio.paFloat32,
            channels=1,
            rate=rate,
            input=True,
            input_device_index=input_device,
            frames_per_buffer=CHUNK,
        )
    except OSError as e:
        print(f"error: failed to open input stream at {rate} Hz: {e}",
              file=sys.stderr)
        p.terminate()
        return

    extractor = FeatureExtractor(rate=rate, chunk=CHUNK)
    warmup_extractor(extractor)

    print(
        f"listening: mic | {rate} Hz | mono | "
        f"chunk={CHUNK} samples (~{1000 * CHUNK / rate:.0f} ms)"
    )
    if osc is not None:
        print(f"sending OSC to {osc.host}:{osc.port} as /audio/frame")
    print("(press Ctrl-C to stop"
          + (" and play back" if playback else "") + ")")
<<<<<<< HEAD
    print(format_header())
=======

    dashboard = Dashboard(title="mic (sender)")
    sent_seq = 0
>>>>>>> origin/main

    recording: list[np.ndarray] = []

    try:
        last_print_t = -1e9
        frames_read = 0
        while True:
            # exception_on_overflow=False → drop samples instead of raising
            # if our processing falls behind the audio thread.
            raw = in_stream.read(CHUNK, exception_on_overflow=False)
            mono = np.frombuffer(raw, dtype=np.float32).copy()
            if playback:
                recording.append(mono)

            feats = extractor(mono)

            t = frames_read / rate
            if osc is not None:
                osc.send(t, feats)
<<<<<<< HEAD

            if t - last_print_t >= 1.0 / PRINT_HZ:
                print(format_dashboard(t, feats))
=======
                sent_seq = osc.seq
            else:
                sent_seq += 1

            if t - last_print_t >= 1.0 / PRINT_HZ:
                dashboard.render(t, feats, sent_seq)
>>>>>>> origin/main
                last_print_t = t
            frames_read += CHUNK
    except KeyboardInterrupt:
        captured_s = frames_read / rate
        print(f"\nstopped capture after {captured_s:.1f}s.")
    finally:
        try:
            in_stream.stop_stream()
            in_stream.close()
        except Exception:
            pass

    if playback and recording:
        _playback_recording(p, recording, rate, output_device)

    p.terminate()


<<<<<<< HEAD
=======
def loopback_and_analyze(
    input_device: Optional[int] = None,
    output_device: Optional[int] = None,
    rate: int = 44100,
    monitor: bool = True,
    osc: Optional[OscSender] = None,
) -> None:
    """DAW-loopback mode: capture from a virtual audio device (e.g. BlackHole
    on macOS) → feature dashboard → optionally pass audio through to your
    real speakers in real time.

    macOS setup:
      1. ``brew install --cask blackhole-2ch``
      2. In *Audio MIDI Setup* create a Multi-Output Device containing
         BlackHole + your speakers; pick it as system output (or use it as
         the output device inside GarageBand).
      3. Run with ``--midi``. BlackHole is auto-detected; pass
         ``--input-device N`` to override.
    """
    p = pyaudio.PyAudio()

    try:
        if input_device is None:
            input_device = find_loopback_input(p)
            if input_device is None:
                print(
                    "error: no loopback/BlackHole-like input device found.\n"
                    "  macOS: brew install --cask blackhole-2ch\n"
                    "  Then route the DAW through BlackHole (Multi-Output\n"
                    "  Device) and rerun, or pass --input-device N "
                    "explicitly (see --list-devices).",
                    file=sys.stderr,
                )
                p.terminate()
                return
        info = p.get_device_info_by_index(input_device)
        in_channels = max(1, min(int(info["maxInputChannels"]), 2))
        print(f"using loopback input [{input_device}] {info['name']} "
              f"({in_channels}ch)")
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        p.terminate()
        return

    try:
        in_stream = p.open(
            format=pyaudio.paFloat32,
            channels=in_channels,
            rate=rate,
            input=True,
            input_device_index=input_device,
            frames_per_buffer=CHUNK,
        )
    except OSError as e:
        print(f"error: failed to open loopback input at {rate} Hz: {e}",
              file=sys.stderr)
        p.terminate()
        return

    out_stream = None
    if monitor:
        try:
            if output_device is None:
                out_info = p.get_default_output_device_info()
                output_device = int(out_info["index"])
            else:
                out_info = p.get_device_info_by_index(output_device)
            print(f"monitoring through [{output_device}] {out_info['name']}")
            out_stream = p.open(
                format=pyaudio.paFloat32,
                channels=in_channels,
                rate=rate,
                output=True,
                output_device_index=output_device,
                frames_per_buffer=CHUNK,
            )
        except OSError as e:
            print(f"warning: monitoring disabled ({e})", file=sys.stderr)
            out_stream = None

    extractor = FeatureExtractor(rate=rate, chunk=CHUNK)
    warmup_extractor(extractor)

    print(
        f"loopback: {info['name']} | {rate} Hz | {in_channels}ch | "
        f"chunk={CHUNK} samples (~{1000 * CHUNK / rate:.0f} ms)"
    )
    if osc is not None:
        print(f"sending OSC to {osc.host}:{osc.port} as /audio/frame")
    print("(press Ctrl-C to stop)")

    dashboard = Dashboard(title="loopback (sender)")
    sent_seq = 0

    try:
        last_print_t = -1e9
        frames_read = 0
        while True:
            raw = in_stream.read(CHUNK, exception_on_overflow=False)
            if out_stream is not None:
                # Pass-through: write the same bytes we read, so the user
                # still hears the DAW through their real speakers.
                out_stream.write(raw)

            buf = np.frombuffer(raw, dtype=np.float32)
            if in_channels > 1:
                mono = buf.reshape(-1, in_channels).mean(axis=1).astype(
                    np.float32)
            else:
                mono = buf.copy()

            feats = extractor(mono)

            t = frames_read / rate
            if osc is not None:
                osc.send(t, feats)
                sent_seq = osc.seq
            else:
                sent_seq += 1

            if t - last_print_t >= 1.0 / PRINT_HZ:
                dashboard.render(t, feats, sent_seq)
                last_print_t = t
            frames_read += CHUNK
    except KeyboardInterrupt:
        captured = frames_read / rate
        print(f"\nstopped after {captured:.1f}s.")
    finally:
        for s in (in_stream, out_stream):
            if s is None:
                continue
            try:
                s.stop_stream()
                s.close()
            except Exception:
                pass
        p.terminate()


>>>>>>> origin/main
def _playback_recording(
    p: pyaudio.PyAudio,
    recording: list[np.ndarray],
    rate: int,
    output_device: Optional[int],
) -> None:
    """Concatenate the captured mono chunks and play them back once."""
    audio = np.concatenate(recording)
    duration = len(audio) / rate
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0

    if peak < 1e-4:
        print("(captured audio is essentially silent — skipping playback)")
        return

    if output_device is not None:
        try:
            info = p.get_device_info_by_index(output_device)
            print(f"playing back {duration:.1f}s on [{output_device}] "
                  f"{info['name']} (peak={peak:.3f}, Ctrl-C to skip)")
        except OSError:
            output_device = None

    if output_device is None:
        print(f"playing back {duration:.1f}s on default output "
              f"(peak={peak:.3f}, Ctrl-C to skip)")

    try:
        out_stream = p.open(
            format=pyaudio.paFloat32,
            channels=1,
            rate=rate,
            output=True,
            output_device_index=output_device,
            frames_per_buffer=CHUNK,
        )
    except OSError as e:
        print(f"error: failed to open output stream: {e}", file=sys.stderr)
        return

    try:
        for i in range(0, len(audio), CHUNK):
            block = audio[i:i + CHUNK]
            if len(block) < CHUNK:
                pad = np.zeros(CHUNK - len(block), dtype=np.float32)
                block = np.concatenate([block, pad])
            out_stream.write(np.ascontiguousarray(block).tobytes())
    except KeyboardInterrupt:
        print("\nplayback skipped.")
    finally:
        try:
            out_stream.stop_stream()
            out_stream.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Play an audio file (or capture from mic) while printing "
                    "real-time features."
    )
    parser.add_argument(
        "song", type=Path, nargs="?",
        help="audio file (wav/mp3/flac/ogg/...); omit when using --mic",
    )
    parser.add_argument(
        "--mic", action="store_true",
        help="capture from the microphone instead of playing a file",
    )
    parser.add_argument(
<<<<<<< HEAD
=======
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
>>>>>>> origin/main
        "--input-device", type=int, default=None,
        help="input device index (with --mic; see --list-devices)",
    )
    parser.add_argument(
        "--device", type=int, default=None,
        help="output device index for file playback (see --list-devices)",
    )
    parser.add_argument(
        "--rate", type=int, default=44100,
        help="sample rate to request from the mic (default 44100)",
    )
    parser.add_argument(
        "--no-playback", action="store_true",
        help="don't replay the recording when --mic is stopped with Ctrl-C",
    )
    parser.add_argument(
<<<<<<< HEAD
        "--osc", action="store_true",
        help="broadcast feature snapshots as OSC /audio/frame over UDP",
=======
        "--no-osc", action="store_true",
        help="disable OSC broadcasting (default: send /audio/frame to "
             "127.0.0.1:9000)",
>>>>>>> origin/main
    )
    parser.add_argument(
        "--osc-host", type=str, default="127.0.0.1",
        help="OSC destination host (default 127.0.0.1)",
    )
    parser.add_argument(
        "--osc-port", type=int, default=9000,
        help="OSC destination port (default 9000)",
    )
    parser.add_argument(
        "--list-devices", action="store_true",
        help="list audio devices and exit",
    )
    parser.add_argument(
        "--alsa-noise", action="store_true",
        help="don't silence libasound stderr probing on Linux",
    )
    args = parser.parse_args()

    if not args.alsa_noise:
        silence_alsa_noise()

    if args.list_devices:
        list_devices()
        return 0

<<<<<<< HEAD
    osc = OscSender(args.osc_host, args.osc_port) if args.osc else None
=======
    osc = None if args.no_osc else OscSender(args.osc_host, args.osc_port)

    if args.midi:
        if args.mic or args.song is not None:
            print("[info] --midi takes precedence over --mic / song path",
                  file=sys.stderr)
        try:
            loopback_and_analyze(
                input_device=args.input_device,
                output_device=args.device,
                rate=args.rate,
                monitor=not args.no_monitor,
                osc=osc,
            )
        except KeyboardInterrupt:
            print("\ninterrupted.")
        return 0
>>>>>>> origin/main

    if args.mic:
        if args.song is not None:
            print("[info] --mic given; ignoring song path", file=sys.stderr)
        try:
            listen_and_analyze(
                input_device=args.input_device,
                output_device=args.device,
                rate=args.rate,
                playback=not args.no_playback,
                osc=osc,
            )
        except KeyboardInterrupt:
            print("\ninterrupted.")
        return 0

    if args.song is None:
        parser.error("a song path is required (or use --mic, or --list-devices)")
    if not args.song.is_file():
        print(f"error: file not found: {args.song}", file=sys.stderr)
        return 1

    try:
        play_and_analyze(args.song, device_index=args.device, osc=osc)
    except KeyboardInterrupt:
        print("\ninterrupted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
