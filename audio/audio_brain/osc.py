"""Tiny OSC sender that pushes per-chunk feature snapshots over UDP.

The schema below is the source of truth — :file:`osc_receiver.cpp` mirrors
it exactly. Any change here must be reflected on the C++ side or the
receiver will silently mis-parse.
"""
from __future__ import annotations

from typing import Any, Mapping

from pythonosc.udp_client import SimpleUDPClient

from .config import DEFAULT_OSC_HOST, DEFAULT_OSC_PORT


class OscSender:
    """Push each feature snapshot as one ``/audio/frame`` OSC message.

    Schema (mirrored in ``osc_receiver.cpp`` — keep both in sync):

        address : ``/audio/frame``
        types   : ``,i  f f f f f f f f f  i  f×12``  (23 args)

        ============  ======  ============================================
        index         type    meaning
        ============  ======  ============================================
        0   ``seq``           int    monotonic counter (loss detection)
        1   ``t``             float  seconds since stream start
        2   ``volume_db``     float  dBFS, clamped to ``[-80, 0]``
        3   ``bass``          float  ``0..1``  low-band   (20..250  Hz)
        4   ``mid``           float  ``0..1``  mid-band   (250..4k  Hz)
        5   ``treble``        float  ``0..1``  high-band  (4k..16k  Hz)
        6   ``bpm``           float  beats/min, ``-1.0`` if unknown
        7   ``smoothness``    float  ``0..1``  1 = sine-like, 0 = transient
        8   ``centroid_hz``   float  raw centroid in Hz
        9   ``centroid_n``    float  ``0..1``  log-mapped (50..10k Hz)
        10  ``note``          int    MIDI note number
                                     (C-1=0, A4=69, C8=108)
                                     - pitch class = ``note % 12``  (0=C)
                                     - octave      = ``note // 12 - 1``
        11..22 ``chroma[12]`` float  pitch-class energies, each ``0..1``
        ============  ======  ============================================

    Payload: ~136 bytes/packet. At ``CHUNK = 2048 @ 44.1 kHz`` that's
    ~3 KB/s — vanishingly small even on the slowest network link.
    """

    def __init__(
        self,
        host: str = DEFAULT_OSC_HOST,
        port: int = DEFAULT_OSC_PORT,
    ) -> None:
        self.host = host
        self.port = port
        self.client = SimpleUDPClient(host, port)
        self.seq: int = 0

    def send(self, t: float, features: Mapping[str, Any]) -> None:
        """Serialise one feature snapshot and fire a UDP packet.

        Parameters
        ----------
        t
            Stream-relative time in seconds (not wall-clock).
        features
            Mapping with the keys produced by
            :meth:`audio_brain.extractor.FeatureExtractor.__call__`.

        Network errors are swallowed by design: a transient ``OSError``
        on UDP must never kill the audio loop. Sequence counter still
        increments on every send so the receiver's gap detector remains
        accurate.
        """
        values: list[Any] = [
            int(self.seq),
            float(t),
            float(features["volume_db"]),
            float(features["bass"]),
            float(features["mid"]),
            float(features["treble"]),
            float(features["bpm"]),
            float(features["smoothness"]),
            float(features["centroid_hz"]),
            float(features["centroid_n"]),
            int(features["note"]),
        ]
        values.extend(float(v) for v in features["chroma"])
        try:
            self.client.send_message("/audio/frame", values)
        except OSError:
            pass
        self.seq += 1
