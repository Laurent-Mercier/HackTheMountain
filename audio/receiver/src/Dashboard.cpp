// Dashboard.cpp — ANSI in-place receiver dashboard.
//
// The format strings below are byte-identical to the legacy
// `osc_receiver.cpp` so existing screen recordings / regression tests
// against the receiver output keep matching.
#include "Dashboard.hpp"

#include <cstdio>
#include <cstring>

namespace audio_brain {
namespace {

constexpr const char* NOTES[12] = {
    "C", "C#", "D", "D#", "E", "F", "F#",
    "G", "G#", "A", "A#", "B",
};

constexpr const char* BAND_NAMES[3] = { "bass", "mid", "treble" };

} // anonymous namespace

void Dashboard::render(const Frame& f, uint64_t received, uint64_t dropped) {
    // Dominant band: argmax(bass, mid, treble), implemented inline to
    // avoid pulling in <algorithm> for one comparison.
    const int dom      = (f.mid > f.bass ? 1 : 0);
    const int dom_band = (f.treble > (dom == 0 ? f.bass : f.mid)) ? 2 : dom;

    const int   pc       = ((f.note % 12) + 12) % 12;
    int         octave   = f.note / 12 - 1;
    if (octave < -1) octave = -1;
    if (octave >  9) octave =  9;
    const float note_str = f.chroma[pc];

    char bpm_buf[16];
    if (f.bpm > 0) std::snprintf(bpm_buf, sizeof bpm_buf, "%.1f", f.bpm);
    else           std::strcpy(bpm_buf, "---");

    char note_buf[8];
    if (note_str > 0.20f)
        std::snprintf(note_buf, sizeof note_buf, "%s%d", NOTES[pc], octave);
    else
        std::strcpy(note_buf, "--");

    if (!first_) std::printf("\x1b[%dA", LINES);
    else         first_ = false;

    std::printf("\r\x1b[2K─── osc-receiver ──────────────────────────────────\n");
    if (f.total_time > f.t + 0.05f) {
        std::printf("\r\x1b[2K  Time:        %7.2f / %7.2f s  "
                    "(frame #%-6lu  dropped %lu / %lu)\n",
                    f.t, f.total_time,
                    static_cast<unsigned long>(received),
                    static_cast<unsigned long>(dropped),
                    static_cast<unsigned long>(received + dropped));
    } else {
        std::printf("\r\x1b[2K  Time:        %7.2f s     "
                    "(frame #%-6lu  dropped %lu / %lu)\n",
                    f.t,
                    static_cast<unsigned long>(received),
                    static_cast<unsigned long>(dropped),
                    static_cast<unsigned long>(received + dropped));
    }
    std::printf("\r\x1b[2K  Volume:      %+6.1f dB\n", f.volume_db);
    std::printf("\r\x1b[2K  Frequency:   %-6s      "
                "(bass=%.2f mid=%.2f treble=%.2f)\n",
                BAND_NAMES[dom_band], f.bass, f.mid, f.treble);
    std::printf("\r\x1b[2K  Centroid:    %6.0f Hz    "
                "(%3.0f %% perceptual)\n",
                f.centroid_hz, f.centroid_n * 100.f);
    std::printf("\r\x1b[2K  Smoothness:  %.2f         "
                "(0 = spiky, 1 = smooth)\n",
                f.smoothness);
    std::printf("\r\x1b[2K  Note:        %-4s         (strength %.2f)\n",
                note_buf, note_str);
    std::printf("\r\x1b[2K  Speed:       %5s BPM\n", bpm_buf);
    std::printf("\r\x1b[2K──────────────────────────────────────────────────\n");
    std::fflush(stdout);
}

} // namespace audio_brain
