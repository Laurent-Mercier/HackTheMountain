// Receiver.hpp — top-level glue.
//
// Owns a UdpListener, a SequenceTracker, and a Dashboard, and runs
// the recv → parse → observe → (throttled) render loop until the
// process is interrupted.
#pragma once

#include "Dashboard.hpp"
#include "SequenceTracker.hpp"
#include "UdpListener.hpp"

namespace audio_brain {

class Receiver {
public:
    /// @param port              UDP port to bind on 127.0.0.1.
    /// @param refresh_period_ms Dashboard repaint interval in ms.
    ///                          Loss/seq counters are still updated
    ///                          on every packet, regardless.
    explicit Receiver(int port, int refresh_period_ms = 100);

    /// Block forever, processing packets. Returns only on a recv()
    /// error that the kernel marks as terminal (rare).
    void run();

private:
    UdpListener     listener_;
    SequenceTracker tracker_;
    Dashboard       dashboard_;
    int             refresh_period_ms_;
};

} // namespace audio_brain
