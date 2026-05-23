// osc_receiver.cpp — minimal, fast OSC-over-UDP receiver for the audio
// feature stream.  Single file, no dependencies beyond POSIX sockets.
//
// Build:   make osc_receiver
//   or:    g++ -std=c++17 -O2 -Wall osc_receiver.cpp -o osc_receiver
//
// Run:     ./osc_receiver           (binds 127.0.0.1:9000)
//   or:    ./osc_receiver 9001
//
// Pair with the Python side:
//     uv run python main.py --osc <song.mp3>
//     uv run python main.py --osc --mic
//
// Schema (must stay in sync with OscSender in main.py):
//
//   address : /audio/frame
//   types   : ,i f f f f f f f f f i f×12        (23 args, ~136 bytes/packet)
//   args:
//     [0]   seq           int    monotonic counter (loss detection)
//     [1]   t             float  seconds since stream start
//     [2]   volume_db     float  dBFS, clamped to [-80, 0]
//     [3]   bass          float  0..1   20..250  Hz
//     [4]   mid           float  0..1   250..4k  Hz
//     [5]   treble        float  0..1   4k..16k  Hz
//     [6]   bpm           float  beats/min (-1 if unknown)
//     [7]   smoothness    float  0..1   1 = sine-like, 0 = transient
//     [8]   centroid_hz   float  raw Hz
//     [9]   centroid_n    float  0..1   log-mapped (50..10k Hz)
//     [10]  note          int    dominant pitch class 0..11 (C=0)
//     [11..22] chroma[12] float  pitch-class energies, each 0..1

#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string_view>

namespace {

// --------------------------------------------------------------------------- //
//  Constants                                                                  //
// --------------------------------------------------------------------------- //

constexpr int    DEFAULT_PORT      = 9000;
constexpr size_t MAX_PACKET        = 1024;            // schema fits in ~150 B
constexpr int    SO_RCVBUF_TARGET  = 1 << 18;         // 256 KiB recv buffer
constexpr int    REFRESH_PERIOD_MS = 100;             // 10 Hz dashboard
constexpr int    DASHBOARD_LINES   = 9;

constexpr const char* NOTES[12] = {
    "C ", "C#", "D ", "D#", "E ", "F ", "F#",
    "G ", "G#", "A ", "A#", "B ",
};

constexpr const char* BAND_NAMES[3] = { "bass", "mid", "treble" };

// --------------------------------------------------------------------------- //
//  Frame: decoded /audio/frame message                                        //
// --------------------------------------------------------------------------- //

struct Frame {
    int32_t seq;
    float   t;
    float   volume_db;
    float   bass, mid, treble;
    float   bpm;
    float   smoothness;
    float   centroid_hz;
    float   centroid_n;
    int32_t note;
    float   chroma[12];
};

// --------------------------------------------------------------------------- //
//  Minimal OSC parser — big-endian fixed-width primitives + padded strings.   //
// --------------------------------------------------------------------------- //

inline uint32_t be32(const uint8_t* p) {
    return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16)
         | (uint32_t(p[2]) <<  8) |  uint32_t(p[3]);
}

inline int32_t read_i32(const uint8_t*& p, const uint8_t* end) {
    if (p + 4 > end) throw std::runtime_error("EOF in int");
    int32_t v = static_cast<int32_t>(be32(p));
    p += 4;
    return v;
}

inline float read_f32(const uint8_t*& p, const uint8_t* end) {
    if (p + 4 > end) throw std::runtime_error("EOF in float");
    uint32_t raw = be32(p);
    p += 4;
    float v;
    std::memcpy(&v, &raw, sizeof v);
    return v;
}

// OSC strings: null-terminated, zero-padded to a 4-byte boundary.
inline std::string_view read_str(const uint8_t*& p, const uint8_t* end) {
    const uint8_t* start = p;
    while (p < end && *p) ++p;
    if (p >= end) throw std::runtime_error("EOF in string");
    std::string_view sv(reinterpret_cast<const char*>(start),
                        static_cast<size_t>(p - start));
    ++p;                                          // null
    size_t consumed = static_cast<size_t>(p - start);
    size_t pad = (4 - (consumed % 4)) % 4;
    if (p + pad > end) throw std::runtime_error("EOF in padding");
    p += pad;
    return sv;
}

bool parse_audio_frame(const uint8_t* buf, size_t n, Frame& out) {
    const uint8_t* p   = buf;
    const uint8_t* end = buf + n;

    if (read_str(p, end) != "/audio/frame") return false;

    std::string_view types = read_str(p, end);
    if (types.size() != 24 || types[0] != ',')      return false;

    out.seq         = read_i32(p, end);
    out.t           = read_f32(p, end);
    out.volume_db   = read_f32(p, end);
    out.bass        = read_f32(p, end);
    out.mid         = read_f32(p, end);
    out.treble      = read_f32(p, end);
    out.bpm         = read_f32(p, end);
    out.smoothness  = read_f32(p, end);
    out.centroid_hz = read_f32(p, end);
    out.centroid_n  = read_f32(p, end);
    out.note        = read_i32(p, end);
    for (int i = 0; i < 12; ++i) out.chroma[i] = read_f32(p, end);

    return true;
}

// --------------------------------------------------------------------------- //
//  Socket setup                                                               //
// --------------------------------------------------------------------------- //

int open_udp_listener(int port) {
    int sock = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) { std::perror("socket"); return -1; }

    // Larger recv buffer = more headroom for bursts before kernel drops.
    int rcvbuf = SO_RCVBUF_TARGET;
    ::setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof rcvbuf);

    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port        = htons(static_cast<uint16_t>(port));
    if (::bind(sock, reinterpret_cast<sockaddr*>(&addr), sizeof addr) < 0) {
        std::perror("bind");
        ::close(sock);
        return -1;
    }
    return sock;
}

// --------------------------------------------------------------------------- //
//  Dashboard: in-place multi-line render with ANSI cursor moves.              //
// --------------------------------------------------------------------------- //

class Dashboard {
public:
    void render(const Frame& f, uint64_t received, uint64_t dropped) {
        const int dom = (f.mid   > f.bass   ? 1 : 0);
        const int dom_band = (f.treble > (dom == 0 ? f.bass : f.mid)) ? 2 : dom;

        const char* note     = NOTES[f.note];
        const float note_str = f.chroma[f.note];

        char bpm_buf[16];
        if (f.bpm > 0) std::snprintf(bpm_buf, sizeof bpm_buf, "%.1f", f.bpm);
        else           std::strcpy(bpm_buf, "---");

        char note_buf[4];
        if (note_str > 0.20f) std::snprintf(note_buf, sizeof note_buf, "%s", note);
        else                  std::strcpy(note_buf, "--");

        if (!first_) std::printf("\x1b[%dA", DASHBOARD_LINES);
        else         first_ = false;

        std::printf("\r\x1b[2K─── osc-receiver ──────────────────────────────────\n");
        std::printf("\r\x1b[2K  Time:        %7.2f s     "
                    "(frame #%-6lu  dropped %lu / %lu)\n",
                    f.t,
                    static_cast<unsigned long>(received),
                    static_cast<unsigned long>(dropped),
                    static_cast<unsigned long>(received + dropped));
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
        std::printf("\r\x1b[2K  Note:        %-2s           (strength %.2f)\n",
                    note_buf, note_str);
        std::printf("\r\x1b[2K  Speed:       %5s BPM\n", bpm_buf);
        std::printf("\r\x1b[2K──────────────────────────────────────────────────\n");
        std::fflush(stdout);
    }

private:
    bool first_ = true;
};

} // anonymous namespace

// --------------------------------------------------------------------------- //
//  main: bind → recv loop → parse → throttled dashboard.                      //
// --------------------------------------------------------------------------- //

int main(int argc, char** argv) {
    int port = DEFAULT_PORT;
    if (argc > 1) port = std::atoi(argv[1]);

    int sock = open_udp_listener(port);
    if (sock < 0) return 1;

    std::printf("[osc-receiver] listening on udp 127.0.0.1:%d for /audio/frame ...\n",
                port);
    std::fflush(stdout);

    uint8_t  buf[MAX_PACKET];
    Frame    frame;
    int32_t  last_seq      = -1;
    uint64_t total_drops   = 0;
    uint64_t total_received = 0;

    Dashboard dash;
    using clock_t = std::chrono::steady_clock;
    auto last_refresh = clock_t::now() - std::chrono::seconds(1);

    while (true) {
        ssize_t n = ::recv(sock, buf, sizeof buf, 0);
        if (n <= 0) {
            if (n < 0) std::perror("recv");
            continue;
        }

        try {
            if (!parse_audio_frame(buf, static_cast<size_t>(n), frame)) {
                continue;
            }
        } catch (const std::exception& e) {
            std::fprintf(stderr, "[osc-receiver] parse error: %s\n", e.what());
            continue;
        }

        // Sequence-gap tracking. Negative jump = sender restarted; resync.
        if (last_seq >= 0 && frame.seq != last_seq + 1) {
            int32_t gap = frame.seq - last_seq - 1;
            if (gap > 0) total_drops += static_cast<uint64_t>(gap);
        }
        last_seq = frame.seq;
        ++total_received;

        // Throttle the dashboard refresh; loss/seq still update every packet.
        auto now = clock_t::now();
        auto since = std::chrono::duration_cast<std::chrono::milliseconds>(
            now - last_refresh).count();
        if (since >= REFRESH_PERIOD_MS) {
            dash.render(frame, total_received, total_drops);
            last_refresh = now;
        }
    }

    ::close(sock);
    return 0;
}
