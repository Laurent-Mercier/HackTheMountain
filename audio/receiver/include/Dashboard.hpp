// Dashboard.hpp — ANSI in-place dashboard for the receiver.
//
// First call prints an 8-line block; subsequent calls move the cursor
// up `LINES` rows and overwrite each line, so the values appear to
// update in place rather than scrolling off the top.
#pragma once

#include "Frame.hpp"

#include <cstdint>

namespace audio_brain {

class Dashboard {
public:
    /// Number of lines drawn per render. Must match the number of
    /// `printf` calls inside `Dashboard.cpp` — the cursor-up math
    /// hinges on it.
    static constexpr int LINES = 8;

    /// Re-paint the dashboard for one freshly-decoded frame.
    void render(const Frame& f, uint64_t received, uint64_t dropped);

private:
    bool first_{true};
};

} // namespace audio_brain
