// UdpListener.hpp — RAII wrapper around a bound UDP socket.
//
// Binds 0.0.0.0:<port> (all interfaces — needed for Docker port publish),
// and exposes a single blocking `recv()` call. Closes the socket in
// the destructor; non-copyable, non-movable.
#pragma once

#include <cstddef>
#include <cstdint>
#include <sys/types.h>

namespace audio_brain {

class UdpListener {
public:
    /// Construct + bind. Throws `std::runtime_error` on failure
    /// (socket / setsockopt / bind). Loopback only — by design.
    explicit UdpListener(int port);

    ~UdpListener();

    UdpListener(const UdpListener&) = delete;
    UdpListener& operator=(const UdpListener&) = delete;

    /// Block until a datagram arrives or `recv()` errors. Returns the
    /// number of bytes read (>= 0); -1 on socket error (with `errno`
    /// set so the caller can `perror`).
    ssize_t recv(uint8_t* buf, std::size_t bufsz);

    /// Port we're bound to (handy for logging in `Receiver::run`).
    int port() const noexcept { return port_; }

private:
    int sock_{-1};
    int port_{0};
};

} // namespace audio_brain
