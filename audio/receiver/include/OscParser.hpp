// OscParser.hpp — minimal `/audio/frame` parser.
//
// Only what we need to decode the producer's hot-loop schema:
// big-endian fixed-width primitives + null-terminated, 4-byte-aligned
// strings. No bundles, no nested addresses, no float64.
#pragma once

#include "Frame.hpp"

#include <cstddef>
#include <cstdint>

namespace audio_brain::osc {

/// Parse a single UDP datagram as a `/audio/frame` message.
///
/// Returns `false` if the address tag, the type tag, or the argument
/// count don't match the schema. On success, `out` is fully populated
/// (every field overwritten) and `true` is returned.
///
/// Throws `std::runtime_error` on truncated input (the caller's catch
/// block in `main` swallows it and prints a diagnostic to stderr).
bool parse_audio_frame(const uint8_t* buf, std::size_t n, Frame& out);

} // namespace audio_brain::osc
