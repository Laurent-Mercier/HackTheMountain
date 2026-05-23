# syntax=docker/dockerfile:1.6
# ----------------------------------------------------------------------------
#  ht-receiver — small C++ image that runs the OSC dashboard receiver.
#
#  Two-stage build: a Debian builder compiles the binary, then we copy
#  just the executable into a minimal `debian:trixie-slim` runtime
#  layer. Final image is ~80 MB, with no toolchain or apt cache.
#
#  Build:
#      docker build -t ht-receiver audio/receiver/
#  Run:
#      docker run --rm --network host ht-receiver           # binds :9000
#      docker run --rm --network host ht-receiver 9001      # custom port
# ----------------------------------------------------------------------------

FROM debian:trixie-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        g++ make \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . /src
RUN make clean && make

# ---------------------------------------------------------------------------- #
FROM debian:trixie-slim AS runtime

# Nothing else needed — the receiver only depends on libstdc++ which
# ships in the slim image. No sound, no graphics, no Python.

COPY --from=builder /src/osc_receiver /usr/local/bin/osc_receiver

ENTRYPOINT ["/usr/local/bin/osc_receiver"]
CMD ["9000"]
