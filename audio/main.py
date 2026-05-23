"""Real-time audio playback + feature extraction.

Plays a song (WAV / MP3 / FLAC / OGG / ...) through the default audio output
while extracting and printing features synchronously with playback.

The output stream's blocking ``write()`` call is what paces the loop to
real time, so feature lines scroll by in sync with what you're hearing.

Usage:
    uv run python main.py path/to/song.mp3
"""
from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path
from typing import Iterator, Optional, Tuple

import librosa
import numpy as np
import pyaudio
import soundfile as sf


def silence_alsa_noise() -> None:
    """Mute libasound's stderr probing on Linux.

    PortAudio enumerates every PCM in your ALSA config at startup; the
    'unable to open slave' / 'Unknown PCM' lines are libasound's own logging,
    not failures from this script. We install a no-op error handler.
    """
    try:
        asound = ctypes.cdll.LoadLibrary("libasound.so.2")
    except OSError:
        return  # not Linux or libasound missing — nothing to silence

    ERROR_HANDLER = ctypes.CFUNCTYPE(
        None, ctypes.c_char_p, ctypes.c_int,
        ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
    )
    handler = ERROR_HANDLER(lambda *_: None)
    asound.snd_lib_error_set_handler(handler)
    silence_alsa_noise._handler = handler  # keep ref alive


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

CHUNK = 2048      # samples per block. 2048 @ 44.1 kHz = ~46 ms latency
PRINT_HZ = 10     # feature-print rate (Hz)

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


# --------------------------------------------------------------------------- #
# File loading: soundfile streams from disk; librosa is a fallback that       #
# decodes the whole file into memory (handles codecs libsndfile can't).       #
# --------------------------------------------------------------------------- #

def load_streamer(path: Path) -> Tuple[int, int, Iterator[np.ndarray]]:
    """Open ``path`` and return ``(rate, channels, block_iterator)``.

    Each yielded block is a ``float32`` array shaped ``(CHUNK, channels)``
    (the final block may be shorter; the caller pads it).
    """
    try:
        song = sf.SoundFile(str(path))
    except RuntimeError as e:
        # libsndfile couldn't decode it (e.g. exotic MP3). Fall back to
        # librosa.load, which uses audioread/ffmpeg under the hood.
        print(
            f"[info] soundfile can't open this file ({e}); "
            f"falling back to a full librosa decode.",
            file=sys.stderr,
        )
        y, rate = librosa.load(str(path), sr=None, mono=False)
        if y.ndim == 1:
            y = y[np.newaxis, :]
        y = y.T.astype(np.float32)          # (samples, channels)
        channels = y.shape[1]

        def blocks_from_memory() -> Iterator[np.ndarray]:
            for i in range(0, len(y), CHUNK):
                yield y[i:i + CHUNK]

        return rate, channels, blocks_from_memory()

    rate, channels = song.samplerate, song.channels

    def blocks_from_disk() -> Iterator[np.ndarray]:
        try:
            yield from song.blocks(
                blocksize=CHUNK, dtype="float32", always_2d=True
            )
        finally:
            song.close()

    return rate, channels, blocks_from_disk()


# --------------------------------------------------------------------------- #
# Feature extraction                                                          #
# --------------------------------------------------------------------------- #

def extract_features(mono: np.ndarray, rate: int) -> dict:
    """Compute time- and frequency-domain features for one mono chunk."""
    n_fft = len(mono)

    rms = float(np.sqrt(np.mean(mono ** 2) + 1e-12))
    peak = float(np.max(np.abs(mono)))
    zcr = float(np.mean(np.abs(np.diff(np.signbit(mono)))))

    # One windowed FFT, reused for every spectral feature.
    win = np.hanning(n_fft).astype(np.float32)
    mag = np.abs(np.fft.rfft(mono * win)).astype(np.float32)
    S_mag = mag[:, np.newaxis]              # librosa wants shape (freq, frame)
    S_pow = S_mag ** 2

    centroid  = float(librosa.feature.spectral_centroid (S=S_mag, sr=rate)[0, 0])
    rolloff   = float(librosa.feature.spectral_rolloff  (S=S_mag, sr=rate)[0, 0])
    bandwidth = float(librosa.feature.spectral_bandwidth(S=S_mag, sr=rate)[0, 0])
    flatness  = float(librosa.feature.spectral_flatness (S=S_mag)        [0, 0])

    mel = librosa.feature.melspectrogram(S=S_pow, sr=rate, n_mels=64)
    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(mel), n_mfcc=13)[:, 0]

    chroma = librosa.feature.chroma_stft(S=S_pow, sr=rate)[:, 0]

    return {
        "rms": rms, "peak": peak, "zcr": zcr,
        "centroid": centroid, "rolloff": rolloff,
        "bandwidth": bandwidth, "flatness": flatness,
        "mfcc": mfcc, "chroma": chroma,
    }


def format_line(t: float, f: dict) -> str:
    note = NOTES[int(np.argmax(f["chroma"]))]
    return (
        f"t={t:6.2f}s  "
        f"rms={f['rms']:.3f}  "
        f"centroid={f['centroid']:6.0f}Hz  "
        f"rolloff={f['rolloff']:6.0f}Hz  "
        f"flatness={f['flatness']:.3f}  "
        f"note={note:>2}  "
        f"mfcc[0..3]=[{f['mfcc'][0]:+.1f}, {f['mfcc'][1]:+.1f}, "
        f"{f['mfcc'][2]:+.1f}, {f['mfcc'][3]:+.1f}]"
    )


# --------------------------------------------------------------------------- #
# Main pipeline                                                               #
# --------------------------------------------------------------------------- #

def play_and_analyze(path: Path, device_index: Optional[int] = None) -> None:
    rate, channels, blocks = load_streamer(path)

    # Open the device first — this is where the Pulse/ALSA handshake happens.
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

    # Pre-warm librosa: the first call to mfcc/melspectrogram/chroma triggers
    # numba JIT compilation (1-3 s). Doing it on a silent buffer here means
    # the first real audio chunk plays back without a hitch.
    print("warming up feature extractors...", end="", flush=True)
    extract_features(np.zeros(CHUNK, dtype=np.float32), rate)
    print(" done.")

    print(
        f"playing: {path.name} | {rate} Hz | {channels}ch | "
        f"chunk={CHUNK} samples (~{1000 * CHUNK / rate:.0f} ms)"
    )

    try:
        last_print_t = -1e9
        frames_played = 0
        for block in blocks:
            # Pad final short block so feature extraction sees a fixed size.
            if len(block) < CHUNK:
                pad = np.zeros((CHUNK - len(block), channels), dtype=np.float32)
                block = np.vstack([block, pad])

            # 1. Playback — blocking write paces the loop to real time.
            stream.write(np.ascontiguousarray(block).tobytes())

            # 2. Downmix to mono for analysis only (playback stays stereo).
            mono = block.mean(axis=1) if channels > 1 else block[:, 0]

            # 3. Throttled feature extraction + print.
            t = frames_played / rate
            if t - last_print_t >= 1.0 / PRINT_HZ:
                feats = extract_features(mono.astype(np.float32), rate)
                print(format_line(t, feats))
                last_print_t = t

            frames_played += CHUNK
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Play an audio file while printing real-time features."
    )
    parser.add_argument(
        "song", type=Path, nargs="?",
        help="audio file (wav/mp3/flac/ogg/...)",
    )
    parser.add_argument(
        "--list-devices", action="store_true",
        help="list audio output devices and exit",
    )
    parser.add_argument(
        "--device", type=int, default=None,
        help="output device index (see --list-devices)",
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

    if args.song is None:
        parser.error("song path is required (or pass --list-devices)")
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
