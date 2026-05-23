// UdpListener.cpp — RAII UDP socket bound to 0.0.0.0:<port> (all interfaces).
#include "UdpListener.hpp"

#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <unistd.h>

namespace audio_brain {
namespace {

/// 256 KiB recv buffer — gives the kernel enough headroom to absorb
/// a sub-second hitch in our render loop without dropping datagrams.
constexpr int SO_RCVBUF_TARGET = 1 << 18;

[[noreturn]] void throw_errno(const char* what) {
    throw std::runtime_error(std::string(what) + ": " + std::strerror(errno));
}

} // anonymous namespace

UdpListener::UdpListener(int port) : port_(port) {
    sock_ = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (sock_ < 0) throw_errno("socket");

    int rcvbuf = SO_RCVBUF_TARGET;
    ::setsockopt(sock_, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof rcvbuf);

    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port        = htons(static_cast<uint16_t>(port));
    if (::bind(sock_, reinterpret_cast<sockaddr*>(&addr), sizeof addr) < 0) {
        int saved = errno;
        ::close(sock_);
        sock_ = -1;
        errno = saved;
        throw_errno("bind");
    }
}

UdpListener::~UdpListener() {
    if (sock_ >= 0) ::close(sock_);
}

ssize_t UdpListener::recv(uint8_t* buf, std::size_t bufsz) {
    return ::recv(sock_, buf, bufsz, 0);
}

} // namespace audio_brain
