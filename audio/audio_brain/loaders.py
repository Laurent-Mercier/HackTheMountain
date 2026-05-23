"""File loaders that yield ``CHUNK``-sized blocks of float32 PCM."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator, Tuple

import librosa
import numpy as np
import soundfile as sf

from .config import CHUNK


def load_streamer(path: Path) -> Tuple[int, int, Iterator[np.ndarray]]:
    """Open ``path`` and return ``(sample_rate, channels, block_iterator)``.

    Two strategies are tried, in order:

    1. ``soundfile.SoundFile`` — streams blocks straight off disk, near
       zero memory cost; works for WAV / FLAC / OGG and recent libsndfile
       builds support MP3 too.
    2. Full :func:`librosa.load` decode into memory, then yield slices.
       This is the fallback when libsndfile cannot open the file (older
       builds, exotic codecs, etc.).

    Either way the returned iterator yields ``(N, channels)`` float32
    arrays where ``N <= CHUNK`` (the last block may be short).
    """
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
