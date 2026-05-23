// osc_receiver.cpp — minimal OSC-over-UDP receiver for the audio feature stream.
//
// Build:   make osc_receiver
//   or:    g++ -std=c++17 -O2 -Wall osc_receiver.cpp -o osc_receiver
//
// Run:     ./osc_receiver           (binds 127.0.0.1:9000 by default)
//   or:    ./osc_receiver 9001      (different port)
//
// Pair with the Python side:
//     uv run python main.py --osc <song.mp3>
//     uv run python main.py --osc --mic
//
// Expected schema (must match OscSender in main.py):
//     address: /audio/frame
//     types:   ,i + 42×f
//     args:
//       0       seq                     int, monotonic
//       1       t_seconds               float
//       2..5    rms peak crest zcr
//       6..9    centroid rolloff bandwidth flatness
//       10      onset_norm (0..1)
//       11      tempo_bpm (-1.0 if unknown)
//       12..18  bands_norm[7]
//       19..30  chroma[12]
//       31..42  mfcc[12]

#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr int    DEFAULT_PORT = 9000;
constexpr size_t MAX_PACKET   = 4096;

const char* NOTES[12] = {
    "C ", "C#", "D ", "D#", "E ", "F ", "F#", "G ", "G#", "A ", "A#", "B "
};

uint32_t read_be32(const uint8_t* p) {
    return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16)
         | (uint32_t(p[2]) << 8)  |  uint32_t(p[3]);
}

float read_float(const uint8_t*& p, const uint8_t* end) {
    if (p + 4 > end) throw std::runtime_error("EOF in float");
    uint32_t raw = read_be32(p);
    p += 4;
    float f;
    std::memcpy(&f, &raw, sizeof f);
    return f;
}

int32_t read_int(const uint8_t*& p, const uint8_t* end) {
    if (p + 4 > end) throw std::runtime_error("EOF in int");
    int32_t v = static_cast<int32_t>(read_be32(p));
    p += 4;
    return v;
}

// OSC strings are null-terminated and zero-padded to a 4-byte boundary.
std::string read_string(const uint8_t*& p, const uint8_t* end) {
    const uint8_t* start = p;
    while (p < end && *p) ++p;
    if (p >= end) throw std::runtime_error("EOF in string");
    std::string s(reinterpret_cast<const char*>(start), p - start);
    ++p;                                             // null terminator
    size_t consumed = static_cast<size_t>(p - start);
    size_t pad = (4 - (consumed % 4)) % 4;
    if (p + pad > end) throw std::runtime_error("EOF in string padding");
    p += pad;
    return s;
}

struct Frame {
    int32_t            seq;
    float              t;
    std::vector<float> f;   // 42 floats following the seq
};

bool parse_audio_frame(const uint8_t* buf, size_t n, Frame& out) {
    const uint8_t* p   = buf;
    const uint8_t* end = buf + n;

    std::string address = read_string(p, end);
    if (address != "/audio/frame") return false;

    std::string types = read_string(p, end);
    if (types.empty() || types[0] != ',') return false;

    out.f.clear();
    bool got_seq = false;
    for (size_t i = 1; i < types.size(); ++i) {
        char t = types[i];
        if (t == 'i') {
            int32_t v = read_int(p, end);
            if (!got_seq) { out.seq = v; got_seq = true; }
        } else if (t == 'f') {
            out.f.push_back(read_float(p, end));
        } else {
            return false;                            // unsupported type
        }
    }

    if (!got_seq || out.f.size() < 11) return false; // need at least t + scalars
    out.t = out.f[0];
    return true;
}

int dominant_note(const float* chroma12) {
    int best = 0;
    for (int i = 1; i < 12; ++i)
        if (chroma12[i] > chroma12[best]) best = i;
    return best;
}

} // namespace

int main(int argc, char** argv) {
    int port = DEFAULT_PORT;
    if (argc > 1) port = std::atoi(argv[1]);

    int sock = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) { std::perror("socket"); return 1; }

    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port        = htons(static_cast<uint16_t>(port));

    if (::bind(sock, reinterpret_cast<sockaddr*>(&addr), sizeof addr) < 0) {
        std::perror("bind");
        ::close(sock);
        return 1;
    }

    std::printf("[c++] listening on udp 127.0.0.1:%d for /audio/frame ...\n",
                port);
    std::fflush(stdout);

    uint8_t buf[MAX_PACKET];
    Frame frame;
    int32_t last_seq = -1;
    int     dropped  = 0;

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
            std::fprintf(stderr, "[c++] parse error: %s\n", e.what());
            continue;
        }

        // Sequence-gap detection (lets you spot drops or out-of-order packets).
        if (last_seq >= 0 && frame.seq != last_seq + 1) {
            int gap = frame.seq - last_seq - 1;
            if (gap > 0) dropped += gap;
            std::fprintf(stderr,
                         "[c++] seq jump: %d → %d (gap=%d, total dropped=%d)\n",
                         last_seq, frame.seq, gap, dropped);
        }
        last_seq = frame.seq;

        // Map indices (see header comment for layout).
        const float rms      = frame.f[1];
        const float peak     = frame.f[2];
        const float crest    = frame.f[3];
        const float centroid = frame.f[5];
        const float flatness = frame.f[8];
        const float onset    = frame.f[9];
        const float tempo    = frame.f[10];
        const float* chroma  = frame.f.data() + 18;

        const int   note_idx     = dominant_note(chroma);
        const float note_strength = chroma[note_idx];

        std::printf(
            "[c++] seq=%5d t=%7.2fs │ rms=%.3f pk=%.2f crest=%4.1f │ "
            "cen=%5.0fHz flat=%.3f │ note=%s(%.2f) │ "
            "onset=%.2f │ %s%5.1f BPM\n",
            frame.seq, frame.t,
            rms, peak, crest,
            centroid, flatness,
            NOTES[note_idx], note_strength,
            onset,
            tempo < 0 ? "  --- " : "      ",
            tempo < 0 ? 0.0f : tempo
        );
        std::fflush(stdout);
    }

    ::close(sock);
    return 0;
}
