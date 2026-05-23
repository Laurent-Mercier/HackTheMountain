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

# --------------------------------------------------------------------------- #
# Tunables                                                                    #
# --------------------------------------------------------------------------- #

CHUNK = 2048      # samples per block. 2048 @ 44.1 kHz = ~46 ms latency
PRINT_HZ = 10     # dashboard refresh rate

TEMPO_WINDOW_S = 4.0   # how much onset history to use for tempo estimation
TEMPO_UPDATE_S = 2.0   # how often to recompute tempo

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
# Stateful feature extractor                                                  #
# --------------------------------------------------------------------------- #

# Try to find the right tempo function across librosa versions.
try:
    _tempo_fn = librosa.feature.tempo            # librosa >= 0.10
except AttributeError:                           # pragma: no cover
    _tempo_fn = librosa.beat.tempo               # older librosa


class FeatureExtractor:
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
    )


# --------------------------------------------------------------------------- #
# Main pipeline                                                               #
# --------------------------------------------------------------------------- #

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


def play_and_analyze(path: Path, device_index: Optional[int] = None) -> None:
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
    print(format_header())

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
            if t - last_print_t >= 1.0 / PRINT_HZ:
                print(format_dashboard(t, feats))
                last_print_t = t

            frames_played += CHUNK
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()


def listen_and_analyze(
    input_device: Optional[int] = None,
    rate: int = 44100,
) -> None:
    """Mic mode: capture from microphone → feature dashboard (no playback).

    No audio is written back to the speakers, so this is safe to run with
    laptop speakers on (no feedback loop). ``stream.read`` blocks until a
    full CHUNK has been captured, which paces the loop to real time exactly
    the same way ``stream.write`` does in file mode.
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
        stream = p.open(
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
    print("(press Ctrl-C to stop)")
    print(format_header())

    try:
        last_print_t = -1e9
        frames_read = 0
        while True:
            # exception_on_overflow=False → drop samples instead of raising
            # if our processing falls behind the audio thread.
            raw = stream.read(CHUNK, exception_on_overflow=False)
            mono = np.frombuffer(raw, dtype=np.float32).copy()
            feats = extractor(mono)

            t = frames_read / rate
            if t - last_print_t >= 1.0 / PRINT_HZ:
                print(format_dashboard(t, feats))
                last_print_t = t
            frames_read += CHUNK
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        p.terminate()


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

    if args.mic:
        if args.song is not None:
            print("[info] --mic given; ignoring song path", file=sys.stderr)
        try:
            listen_and_analyze(
                input_device=args.input_device, rate=args.rate,
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
        play_and_analyze(args.song, device_index=args.device)
    except KeyboardInterrupt:
        print("\ninterrupted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
