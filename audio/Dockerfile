# syntax=docker/dockerfile:1.6
# ----------------------------------------------------------------------------
#  audio_brain — real-time audio feature extractor + OSC broadcaster.
#
#  Build:
#      docker build -t ht-audio audio/
#
#  Run examples (Linux host, OSC reaches localhost:9000 like before):
#      docker run --rm --network host \
#          -v "$PWD/audio":/music ht-audio music /music/Darude\ -\ Sandstorm.mp3
#      docker run --rm --network host --device /dev/snd ht-audio mic
#      docker run --rm --network host --device /dev/snd ht-audio midi
#      docker run --rm --network host ht-audio list-devices
#
#  See audio/README.md for the full Docker recipe (compose included).
# ----------------------------------------------------------------------------

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Make `audio_brain` importable from any CWD so the entrypoint can
    # `cd /music` before calling the CLI without breaking `python -m`.
    PYTHONPATH=/app

# System libraries needed by pyaudio (PortAudio), soundfile (libsndfile),
# librosa (ffmpeg fallback decoder), and ALSA (mic / loopback access).
#
# pyaudio ships only an sdist on PyPI, so it has to be compiled from
# source — `build-essential` (gcc + libc6-dev + make) is what supplies
# the C standard headers (`stdlib.h`, …) the build needs. Python's own
# headers (`Python.h`) are already shipped under
# `/usr/local/include/python3.12/` by the python:3.12-slim image, so we
# DO NOT pull in `python3-dev` (which on Debian Trixie would drag in
# Python 3.13 headers that don't match this image's interpreter).
#
# `DEBIAN_FRONTEND=noninteractive` silences the debconf warnings about
# the missing TTY in `docker build` / `docker compose build` output.
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential \
        portaudio19-dev \
        libportaudio2 \
        libsndfile1 \
        libasound2 \
        libasound2-plugins \
        libpulse0 \
        alsa-utils \
        ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ALSA "default" PCM → pulse plugin → PipeWire/PulseAudio host socket.
# The libasound2-plugins package above provides
# /usr/lib/x86_64-linux-gnu/alsa-lib/libasound_module_pcm_pulse.so;
# this minimal asound.conf turns it on so PortAudio's "default" output
# in the container actually reaches the host's audio server.
RUN printf '%s\n' \
        'pcm.!default {' \
        '    type pulse' \
        '}' \
        'ctl.!default {' \
        '    type pulse' \
        '}' \
        > /etc/asound.conf

WORKDIR /app

# Install Python deps. Hardcoded versions match the original
# pyproject.toml the project shipped with; bump here if you bump the
# producer's deps.
RUN python -m pip install \
        "librosa>=0.10.2" \
        "numpy>=2.4.6" \
        "pyaudio>=0.2.14" \
        "python-osc>=1.8.3" \
        "soundfile>=0.12.1"

# Copy source and entrypoint.
COPY audio_brain/ ./audio_brain/
COPY docker-entrypoint.sh /usr/local/bin/audio
RUN chmod +x /usr/local/bin/audio

# Default user volume for music files.
VOLUME ["/music"]

ENTRYPOINT ["/usr/local/bin/audio"]
CMD ["list-devices"]
