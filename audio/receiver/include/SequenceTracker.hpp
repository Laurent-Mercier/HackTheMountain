// SequenceTracker.hpp — counts received / dropped OSC packets.
//
// Resyncs silently when the sender restarts (negative seq jump) so the
// drop counter doesn't explode on a Ctrl-C / re-launch cycle.
#pragma once

#include <cstdint>

namespace audio_brain {

class SequenceTracker {
public:
    /// Account for one freshly-parsed packet's `seq` field.
    void observe(int32_t seq) noexcept {
        if (last_seq_ >= 0 && seq != last_seq_ + 1) {
            int32_t gap = seq - last_seq_ - 1;
            if (gap > 0) total_drops_ += static_cast<uint64_t>(gap);
        }
        last_seq_ = seq;
        ++total_received_;
    }

    uint64_t received() const noexcept { return total_received_; }
    uint64_t dropped()  const noexcept { return total_drops_; }

private:
    int32_t  last_seq_      {-1};
    uint64_t total_drops_   {0};
    uint64_t total_received_{0};
};

} // namespace audio_brain
