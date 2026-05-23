// Receiver.cpp — recv → parse → observe → render loop.
#include "Receiver.hpp"

#include "Frame.hpp"
#include "OscParser.hpp"

#include <chrono>
#include <cstddef>
#include <cstdio>
#include <cstdint>
#include <exception>

namespace audio_brain {
namespace {

/// Schema fits in ~150 B; round up generously to absorb any future
/// growth without re-allocating.
constexpr std::size_t MAX_PACKET = 1024;

} // anonymous namespace

Receiver::Receiver(int port, int refresh_period_ms)
    : listener_(port), refresh_period_ms_(refresh_period_ms) {}

void Receiver::run() {
    std::printf(
        "[osc-receiver] listening on udp 0.0.0.0:%d for /audio/frame ...\n",
        listener_.port());
    std::fflush(stdout);

    uint8_t buf[MAX_PACKET];
    Frame   frame;

    using clock_t = std::chrono::steady_clock;
    auto last_refresh = clock_t::now() - std::chrono::seconds(1);

    while (true) {
        ssize_t n = listener_.recv(buf, sizeof buf);
        if (n <= 0) {
            if (n < 0) std::perror("recv");
            continue;
        }

        try {
            if (!osc::parse_audio_frame(buf, static_cast<std::size_t>(n),
                                        frame)) {
                continue;
            }
        } catch (const std::exception& e) {
            std::fprintf(stderr, "[osc-receiver] parse error: %s\n", e.what());
            continue;
        }

        tracker_.observe(frame.seq);

        // Throttle the dashboard refresh; loss / seq counters above
        // still update on every packet.
        auto now = clock_t::now();
        auto since = std::chrono::duration_cast<std::chrono::milliseconds>(
            now - last_refresh).count();
        if (since >= refresh_period_ms_) {
            dashboard_.render(frame, tracker_.received(), tracker_.dropped());
            last_refresh = now;
        }
    }
}

} // namespace audio_brain
