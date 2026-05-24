// Frame.hpp — decoded `/audio/frame` OSC message.
//
// Mirrors the schema documented in `audio_brain/osc.py` (Python sender)
// one-to-one. Any change here MUST also be made on the Python side or
// the receiver will silently mis-parse.
#pragma once

#include <cstdint>

namespace audio_brain {

/// A single audio-feature snapshot, one per OSC packet.
///
/// All ranges and units are inherited from the Python `OscSender`:
///
///   * `t`           — elapsed seconds since stream start.
///   * `total_time`  — file duration (music); mic/midi mirror `t`.
///   * `volume_db`   — dBFS clamped to [-80, 0].
///   * `bass / mid / treble` — band energies in [0, 1] (rolling-peak
///                              normalised by the sender).
///   * `bpm`         — beats per minute, or -1.0 if not yet known.
///   * `smoothness`  — 0 = transient, 1 = sine-like.
///   * `centroid_hz` — raw spectral centroid (Hz).
///   * `centroid_n`  — log-mapped centroid in [0, 1].
///   * `note`        — reserved (sender always 0; use chroma for harmony).
///   * `chroma[12]`  — pitch-class energies (C..B), each in [0, 1].
struct Frame {
    int32_t seq;
    float   t;
    float   total_time;
    float   volume_db;
    float   bass, mid, treble;
    float   bpm;
    float   smoothness;
    float   centroid_hz;
    float   centroid_n;
    int32_t note;
    float   chroma[12];
};

} // namespace audio_brain
