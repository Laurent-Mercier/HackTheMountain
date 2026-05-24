// OscParser.cpp — minimal `/audio/frame` parser.
#include "OscParser.hpp"

#include <cstring>
#include <stdexcept>
#include <string_view>

namespace audio_brain::osc {
namespace {

/// Big-endian 32-bit unsigned read.
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

/// OSC string: null-terminated, zero-padded to a 4-byte boundary.
inline std::string_view read_str(const uint8_t*& p, const uint8_t* end) {
    const uint8_t* start = p;
    while (p < end && *p) ++p;
    if (p >= end) throw std::runtime_error("EOF in string");
    std::string_view sv(reinterpret_cast<const char*>(start),
                        static_cast<std::size_t>(p - start));
    ++p;                                           // null
    std::size_t consumed = static_cast<std::size_t>(p - start);
    std::size_t pad = (4 - (consumed % 4)) % 4;
    if (p + pad > end) throw std::runtime_error("EOF in padding");
    p += pad;
    return sv;
}

} // anonymous namespace

bool parse_audio_frame(const uint8_t* buf, std::size_t n, Frame& out) {
    const uint8_t* p   = buf;
    const uint8_t* end = buf + n;

    if (read_str(p, end) != "/audio/frame") return false;

    std::string_view types = read_str(p, end);
    if (types.size() != 25 || types[0] != ',') return false;

    out.seq         = read_i32(p, end);
    out.t           = read_f32(p, end);
    out.total_time  = read_f32(p, end);
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

} // namespace audio_brain::osc
