# Audio feature dashboard — value reference

Every line of the real-time dashboard (Python sender and C++ receiver alike)
shows a labelled value. This document explains the **range**, **unit**, and
**meaning of the min/max** for each field, so the visualizer side knows
exactly what to expect off the wire.

Example dashboard line group:

```
─── audio (sender) ───────────────────────────────
  Time:        86.33 s     (frame #1860    dropped 0 / 1860)
  Volume:     -23.1 dB
  Frequency:  treble       (bass=0.35 mid=0.84 treble=0.89)
  Centroid:    4255 Hz     ( 84 % perceptual)
  Smoothness:  0.66        (0 = spiky, 1 = smooth)
  Note:        D           (strength 1.00)
  Speed:       129.2 BPM
──────────────────────────────────────────────────
```

---

## Time

- **Range:** `0.0 → ∞` seconds (float).
- File mode: `0` to the file's duration. Mic mode: `0` until you Ctrl-C.
- Counts playback time from the start of the stream, **not** wall-clock.

## Frame # and Dropped

| Field      | Range                  | Meaning                                                                        |
| ---------- | ---------------------- | ------------------------------------------------------------------------------ |
| `frame #`  | `0 → 2 147 483 647`    | monotonic sequence; at ~21 Hz that's ~3 years of streaming before overflow.    |
| `dropped`  | `0 → frame #`          | packets the C++ side never saw, inferred from gaps in `seq`.                   |
| `X / Y`    | `X` = dropped, `Y` = X + received | `0 / 1860` = perfect run; `3 / 9` = 3 lost out of 9 expected. |

On localhost expect `dropped = 0` always. Non-zero only happens if the C++
side hitches for >100 ms with the kernel recv buffer (~256 KiB) already full.

## Volume — dBFS

- **Unit:** dB relative to digital full scale (`0 dBFS` = a sample at ±1.0).
- **Range:** clamped in code to `[−80, 0]`.
- **Theoretical:** `−∞` (silence) to `0` (full-scale signal). Above 0 = clipped source.

| Value      | Meaning                                                     |
| ---------- | ----------------------------------------------------------- |
| `−80 dB`   | digital silence floor (our clamp)                           |
| `−60 dB`   | room tone, breathing                                        |
| `−40 dB`   | very quiet music                                            |
| `−23 dB`   | comfortable mid-level mastered music (example above)        |
| `−14 dB`   | Spotify-targeted master                                     |
| `−10 dB`   | loud                                                        |
| `0 dB`     | clipping ceiling                                            |

dBFS describes the **digital signal level**, not acoustic loudness in your
room. Turning your speakers up doesn't change dBFS; it only changes SPL.

## Frequency — label + 3 band values

The label is `argmax(bass, mid, treble)`. The three numeric values are the
useful payload.

| Field    | Range          | Band (Hz)    | What lives there                                  |
| -------- | -------------- | ------------ | ------------------------------------------------- |
| `bass`   | `0.00 – 1.00`  | `20 – 250`   | kick, sub, bass-guitar / synth fundamentals       |
| `mid`    | `0.00 – 1.00`  | `250 – 4000` | vocals, snare body, most melodic content          |
| `treble` | `0.00 – 1.00`  | `4000 – 16000` | cymbals, sibilance, "air"                       |

What `0` and `1` mean:

- `0.00` = that band is currently silent **relative to its own recent history**.
- `1.00` = that band is at its loudest of the past few seconds.

Each band has its own `RollingPeak` normalizer (slow exponential decay,
`0.995` per frame). Consequences:

- The three values are independent — they can all be high, all low, or any
  mix. They do **not** sum to 1.
- They're **relative**, not absolute. A quiet ambient track still hits
  `1.00` because the normalizer auto-adapts. Comparing values *within* a
  song is meaningful; comparing *across* songs is not.
- The label flips between `bass / mid / treble` as the strongest band
  changes. In the example, `mid=0.84` vs `treble=0.89` makes treble win
  by a hair.

## Centroid — Hz + perceptual %

Two values describing the same thing on different scales.

### Centroid (Hz) — raw spectral centroid

- **Range:** `0 → 22 050` Hz (Nyquist at 44.1 kHz).
- **Practical:** `50 → 10 000` Hz for music.

| Hz                | "Feel"                                            |
| ----------------- | ------------------------------------------------- |
| `< 500 Hz`        | bass-only (kick alone, sub-drop)                  |
| `1 000 – 2 500`   | speech, midrange-dominant music                   |
| `3 000 – 5 000`   | bright pop / rock mix (`4255 Hz` example)         |
| `5 000 – 8 000`   | cymbal-heavy, very bright                         |
| `> 8 000`         | nearly white noise or extreme highs               |

### Centroid (% perceptual) — log-mapped

- **Range:** strict `0 % → 100 %` (clipped).
- **Mapping:** logarithmic, `50 Hz → 0 %`, `10 000 Hz → 100 %`.

| %     | ≈ Hz     | "Feel"      |
| ----- | -------- | ----------- |
| `0`   | `50`     | deep sub    |
| `25`  | `200`    | bass        |
| `50`  | `700`    | low-mid     |
| `75`  | `2 500`  | presence    |
| `84`  | `4 250`  | bright      |
| `100` | `10 000` | "air"       |

The percentage exists because Hz is non-linear to the ear: going from
`200 → 400 Hz` sounds like the same step as `4 000 → 8 000 Hz`, but linearly
they're vastly different. The log mapping makes "50 %" feel like
"halfway between dark and bright".

## Smoothness

- **Range:** strict `0.00 → 1.00`.
- **Derived from:** crest factor (`peak / RMS`), log-mapped and inverted.

| Value          | Meaning              | Typical sound                            |
| -------------- | -------------------- | ---------------------------------------- |
| `0.00`         | very spiky           | snare hit alone, hand clap, click        |
| `0.10 – 0.30`  | percussive           | drum-heavy section                       |
| `0.30 – 0.60`  | mixed                | typical music with transients            |
| `0.60 – 0.80`  | sustained            | vocal hold, pad, lead synth (`0.66` ex.) |
| `0.80 – 0.95`  | very smooth          | drone, sustained chord                   |
| `1.00`         | pure tone            | sine wave / single sustained note        |

Math:

- `crest = peak / rms`
- A sine wave has `crest = √2 ≈ 1.41` → `smoothness ≈ 1.0`
- A drum-hit transient has `crest ≈ 8 – 12` → `smoothness ≈ 0.0`
- Anything in between is log-interpolated.

## Note + strength

### Note (label)

- **Range:** `<pitch class><octave>`, e.g. `C4`, `A#3`, `G2` — or `--`.
- **Wire format:** sent as a **MIDI note number** in `args[10]` (int).
  Convert with: `pitch_class = note % 12` (0=C..11=B), `octave = note / 12 - 1`.
  Examples: A4 = 69, C4 (middle C) = 60, C2 = 36.
- `--` is shown when the dominant chroma value is below `0.20` (no clear
  pitch — drums or noise dominate).
- **How the octave is picked:** `argmax` of the chroma vector gives the
  pitch class; we then look at the FFT bin of that pitch class's
  fundamental in each octave (1..7) and pick the octave with the most
  energy. ~7 lookups per chunk — negligible CPU.

### Strength

- **Range:** `0.00 → 1.00`.
- **What it means:** librosa normalizes each chroma frame so the max is
  `1.0`. This is literally the height of the loudest pitch peak in the
  spectrum.

| Value         | Meaning                                                        |
| ------------- | -------------------------------------------------------------- |
| `< 0.20`      | no clear pitch → label becomes `--`                            |
| `0.20 – 0.40` | weakly pitched (busy mix, percussion blend)                    |
| `0.40 – 0.70` | melodic but mixed harmony (chord, several notes)               |
| `0.70 – 0.95` | clear note with harmonics                                      |
| `1.00`        | a single pitch class strongly dominates (example: `D 1.00`)    |

**Important:** `strength = 1.00` does **not** mean *only* one note is
playing — only that one pitch class is the **strongest** of the 12. Other
pitches can still be present (e.g. the 5th often sits around `0.7` during
a held major chord).

## Speed — BPM

- **Range:** `−1.0` (unknown) or `30.0 → 320.0` (librosa's default bounds).
- **Practical:** most music sits in `60 – 220` BPM.
- **Updates:** recomputed every ~2 s once 4 s of onset history is buffered.
  Before then you see `---`.

| BPM       | Style                                  |
| --------- | -------------------------------------- |
| `60 – 80` | ballads                                |
| `80 – 100` | hip-hop                               |
| `100 – 130` | pop                                  |
| `120 – 130` | house / trance                       |
| `129`     | dance-tempo territory (example above)  |
| `130 – 145` | dubstep / drum & bass half-time      |
| `160 – 180` | drum & bass                          |
| `200+`    | hardcore / speedcore                   |

Failure modes to watch for:

- **Half / double errors:** if librosa locks onto every *other* beat or
  *two* per beat, BPM appears at ~65 or ~258 for a 130-BPM song.
  Usually stabilises after the first 4–6 s of music.
- **Sparse rhythms:** pure ambient / solo-vocal sections give a flat onset
  envelope → tempo just freezes on the last estimate.

---

## Cheat sheet

| Field                  | Min          | Max               | Min means              | Max means                  |
| ---------------------- | ------------ | ----------------- | ---------------------- | -------------------------- |
| Time                   | `0`          | `∞ s`             | start                  | end of file / forever      |
| frame #                | `0`          | `2 147 483 647`   | first packet           | overflow (~3 years)        |
| dropped                | `0`          | `frame #`         | no loss                | every packet lost          |
| Volume                 | `−80 dB`     | `0 dB`            | silence                | clipping ceiling           |
| bass / mid / treble    | `0.00`       | `1.00`            | band empty             | band at recent peak        |
| Centroid (Hz)          | `0`          | `22 050`          | DC                     | Nyquist                    |
| Centroid %             | `0 %`        | `100 %`           | ≤ 50 Hz (deep sub)     | ≥ 10 kHz (air)             |
| Smoothness             | `0.00`       | `1.00`            | percussive spike       | pure sine                  |
| Note strength          | `0.00`       | `1.00`            | no pitch               | one pitch fully dominant   |
| Speed                  | `−1` / `30 BPM` | `320 BPM`      | unknown / very slow    | very fast                  |

---

## Where these values come from

- **Producer:** the [`audio_brain`](audio_brain/) Python package — real-time
  audio pipelines for file / mic / loopback input. See
  [`FeatureExtractor`](audio_brain/extractor.py) and
  [`OscSender`](audio_brain/osc.py) for the exact formulas.
- **Wire format:** OSC over UDP localhost, address `/audio/frame`,
  ~136 bytes per packet at ~21 Hz. Full schema lives in the `OscSender`
  docstring (and is mirrored verbatim in `receiver/include/Frame.hpp`
  and `receiver/src/OscParser.cpp`).
- **Consumer:** the [`receiver/`](receiver/) C++ module — `UdpListener`,
  `OscParser`, `SequenceTracker`, `Dashboard`, all glued together by a
  single `Receiver::run()` loop. Drop the headers under `receiver/include/`
  into your real visualizer to consume the stream.

---

# Repository layout

This project is **Docker-only**: there is no bare-metal Python or
Make build path. Everything ships as two container images.

```
audio/
├── audio_brain/                 # Python package — the producer
│   ├── __init__.py              # public API re-exports
│   ├── config.py                # constants (CHUNK, BANDS, NOTES, …)
│   ├── osc.py                   # OscSender — OSC wire-format sender
│   ├── normalizer.py            # RollingPeak — running-peak normaliser
│   ├── extractor.py             # FeatureExtractor — one FFT, all features
│   ├── dashboard.py             # Dashboard — ANSI in-place renderer
│   ├── devices.py               # Pulse routing, device pick, stream open
│   ├── loaders.py               # load_streamer (soundfile + librosa)
│   ├── pipelines.py             # BasePipeline + File / Mic / Loopback
│   └── cli.py                   # argparse entry point
├── receiver/                    # C++ package — the consumer
│   ├── include/
│   │   ├── Frame.hpp            # decoded /audio/frame struct
│   │   ├── OscParser.hpp        # parse_audio_frame()
│   │   ├── UdpListener.hpp      # RAII UDP socket
│   │   ├── SequenceTracker.hpp  # loss / gap accounting
│   │   ├── Dashboard.hpp        # ANSI in-place render
│   │   └── Receiver.hpp         # orchestrator
│   ├── src/                     # one .cpp per header (+ main.cpp)
│   ├── Makefile                 # used inside the receiver image
│   └── Dockerfile               # multi-stage build (~80 MB runtime)
├── Dockerfile                   # producer (Python + PortAudio + pulse plugin)
├── docker-entrypoint.sh         # subcommand wrapper inside the producer
├── docker-compose.yml           # the two services + recipes per mode
├── docker-compose.linux.yml     # Pulse socket bind-mount (Linux only)
├── play.sh                      # one-command UX (recommended entry point)
├── scripts/
│   ├── interactive-pulse-pick.sh  # host device picker (pactl)
│   ├── pulse-split-routes.sh      # auto laptop mic → Bluetooth
│   └── host-pulse-tcp.sh          # macOS/Windows Pulse TCP server
├── .pulse-route.env             # saved PULSE_* routes (gitignored; optional)
└── Darude - Sandstorm.mp3       # demo song (bind-mounted into the producer)
```

The Python pipelines (file / mic / loopback) share a single
`BasePipeline.run()` loop — each subclass only implements how to open
the right PortAudio streams and yield mono `CHUNK`-sized buffers.
Adding a new input mode (e.g. WebRTC, RTSP) is a ~30-line subclass.

The C++ receiver follows the same module-per-responsibility split:
`UdpListener` is an RAII socket wrapper, `OscParser` is a free function
in the `audio_brain::osc` namespace, `SequenceTracker` is a header-only
counter, `Dashboard` owns the ANSI render state, and `Receiver` glues
them together with a single `run()` method that mirrors
`BasePipeline.run()` on the Python side.

# Dashboard (receiver terminal)

With the default Docker workflow (`./play.sh`), only the **C++ receiver**
dashboard is shown in your terminal (`─── osc-receiver ───`). The Python
sender still extracts features and sends OSC, but skips its own ANSI dashboard
to avoid jumbled output from two containers logging at once.

Run the sender alone with `--no-osc` if you want the Python dashboard instead.

# Quickstart — `play.sh`

Recommended entry point: one command, one terminal, one Ctrl-C stops both
containers. Flags can appear **before or after** the song path.

```bash
cd audio

# Music (file → host speakers / headphones via Pulse)
./play.sh                                          # default demo song
./play.sh music "your-song.mp3"
./play.sh music "your-song.mp3" --use-route        # saved playback device
./play.sh music "song.mp3" --pick-route --save-route

# Microphone (capture + OSC; optional live monitor)
./play.sh mic                                      # record only, replay on stop
./play.sh mic --monitor                            # live pass-through, no replay
./play.sh mic --monitor --replay                   # live + full replay after Ctrl-C
./play.sh mic --monitor --use-route                # saved mic + output route
./play.sh mic --monitor --pick-route --save-route  # interactive pick + save route

# Laptop mic + Bluetooth earbuds (auto split via pactl)
./play.sh mic --monitor --split-audio

# Virtual loopback / DAW (monitor on by default; pick loopback source + output)
./play.sh midi
./play.sh midi --use-route
./play.sh midi --pick-route --save-route

# Device discovery
./play.sh pick                                     # interactive Pulse menu
./play.sh pulse-routes                             # pactl list (raw)
./play.sh list-devices                             # PortAudio indices (in container)
./play.sh help
```

### `play.sh` audio flags

| Flag | Meaning |
|------|---------|
| `--pick-route`, `--pick-audio`, `-i` | Interactive picker (`pactl` on host). Music: playback only. Mic: mic + output. Midi: loopback/monitor source + output. |
| `--save-route` | With `--pick-route`, write `audio/.pulse-route.env` |
| `--use-route` | Load `audio/.pulse-route.env` (music: **sink** only; mic/midi: sink + source) |
| `--split-audio` | Auto-select built-in mic + first Bluetooth sink (mic/midi only) |

Saved route example:

```bash
./play.sh mic --monitor --pick-route --save-route
# later:
./play.sh mic --monitor --use-route
./play.sh midi --use-route
./play.sh music "Darude - Sandstorm.mp3" --use-route
```

### Where sound actually plays

| Mode | You hear audio from… | Terminal shows… |
|------|----------------------|-----------------|
| **music** | Python sender → Pulse → your sink | Receiver OSC dashboard |
| **mic** (no `--monitor`) | Replay on Ctrl-C only (if not `--no-playback`) | Receiver dashboard |
| **mic --monitor** | Python sender (live monitor) | Receiver dashboard |
| **midi** | Python sender (pass-through) | Receiver dashboard |

Inside Docker, playback uses Pulse (`PULSE_SINK` / `PULSE_SOURCE`), not raw
ALSA `hw:*` devices. On PipeWire HiFi laptops, pick **Mic1** not **Mic2** for
capture. Bluetooth devices appear as `bluez_output…` / `bluez_input…` in the
picker (labelled **Bluetooth headphones** / **Bluetooth headset mic**).

### What `play.sh` does

1. Builds `ht-receiver` and `ht-audio` images (cached after the first run).
2. Starts both on Docker bridge network `htnet`; sender → **`receiver:9000`**.
3. Routes audio through the host Pulse/PipeWire socket (Linux) or TCP (macOS/Win).
4. Streams **receiver** logs in the terminal; sender logs: `docker logs audio-mic-1`.
5. **Ctrl-C once** stops both containers.

# Cross-platform audio

One `./play.sh` command on every OS. **OSC works the same everywhere**
(bridge network + service DNS). **Playback** uses different host hooks:

| OS | Host audio hook | Before first run |
|----|-----------------|------------------|
| **Linux** | Unix socket `unix:/run/pulse/native` (PipeWire/Pulse) | Nothing — `play.sh` adds `docker-compose.linux.yml` automatically |
| **macOS** | PulseAudio **TCP** `tcp:host.docker.internal:4713` | `./scripts/host-pulse-tcp.sh` (needs `brew install pulseaudio`) |
| **Windows** | Same TCP URL | `./scripts/host-pulse-tcp.sh` (PulseAudio on the host) |

### Linux (default — no extra steps)

PipeWire or Pulse exposes `$XDG_RUNTIME_DIR/pulse/native`. The producer
container mounts that socket and plays through your normal desktop audio.

### macOS / Windows

`play.sh` uses the **`COMPOSE_FILE`** environment variable (not multiple
`-f` flags) so `compose rm --force` works on Docker Desktop — a bare
`compose rm -f` after `-f docker-compose.yml` is parsed as two compose files.

Docker cannot see Core Audio / WASAPI directly. Instead the producer
talks to **PulseAudio running on the host** over TCP:

```bash
# once per session (or add to your shell profile)
./scripts/host-pulse-tcp.sh

# then the usual workflow
./play.sh music "Darude - Sandstorm.mp3"
```

`host-pulse-tcp.sh` starts PulseAudio with `module-native-protocol-tcp` on
port **4713** (`auth-anonymous=1` — fine for local dev only). Containers
set `PULSE_SERVER=tcp:host.docker.internal:4713` automatically.

**Mic / loopback on Mac:** route input through a virtual device
(BlackHole, VB-Cable) **into host Pulse**, then use `./play.sh mic` or
`./play.sh midi`. The container sees whatever Pulse exposes.

### OSC (all platforms)

| Piece | Address |
|-------|---------|
| Sender → receiver (inside compose) | `receiver:9000` |
| Host → receiver (debugging) | `localhost:9000` (UDP port published) |

The C++ receiver binds **`0.0.0.0:9000`** inside its container so port
mapping and bridge networking both work. No `network_mode: host` required.

# Direct `docker compose` recipes

On **Linux**, set `COMPOSE_FILE` (or pass multiple `-f` flags yourself):

```bash
cd audio
export OSC_HOST=receiver PULSE_SERVER=unix:/run/pulse/native
export COMPOSE_FILE="$PWD/docker-compose.yml:$PWD/docker-compose.linux.yml"
docker compose build

docker compose run --rm receiver
docker compose run --rm music "Darude - Sandstorm.mp3"
```

On **macOS / Windows**, start `./scripts/host-pulse-tcp.sh` first, then
omit `docker-compose.linux.yml` (TCP pulse is the default in the base file).

Any flag the Python CLI accepts is forwarded verbatim:

```bash
docker compose run --rm music song.mp3 --no-osc
docker compose run --rm mic --rate 48000 --no-playback
```

Because `docker compose run` only foregrounds the named service, the
two-terminal version of `play.sh` looks like:

```bash
# terminal 1 — receiver dashboard
docker compose run --rm receiver

# terminal 2 — sender (and host audio playback)
docker compose run --rm music "Darude - Sandstorm.mp3"
```

# OSC destination overrides

Default inside compose is **`receiver:9000`**. Override via env vars:

```bash
OSC_PORT=9001 ./play.sh
OSC_HOST=receiver OSC_PORT=9001 docker compose run --rm music song.mp3
```

…or pass `--osc-host` / `--osc-port` directly to a sender:

```bash
docker compose run --rm mic --osc-host 192.168.1.42
```

# Audio routing — under the hood

`play.sh` sets **`PULSE_SINK`** (playback) and optionally **`PULSE_SOURCE`**
(capture) before starting containers. The Python producer opens PortAudio's
**`default`** device (not raw `hw:*` cards) so libpulse honours those variables.

Inside the producer image, ALSA's `default` PCM uses the
`pcm.!default { type pulse }` plugin (`/etc/asound.conf` in the image).
It connects wherever `PULSE_SERVER` points:

- **Linux:** `unix:/run/pulse/native` (bind-mounted from the host)
- **macOS / Windows:** `tcp:host.docker.internal:4713` (host PulseAudio)

PipeWire's PulseAudio compatibility layer (the default on Arch, Fedora
38+, Ubuntu 22.10+, etc.) exposes exactly this socket, so the recipe
works out of the box. On hosts running plain PulseAudio it's identical.

If the pulse socket isn't where we expect it:

```bash
ls "$XDG_RUNTIME_DIR/pulse"            # should list `native`
echo "$XDG_RUNTIME_DIR"                # usually /run/user/<uid>
```

If `XDG_RUNTIME_DIR` is unset (some non-systemd setups), the compose
file falls back to `/run/user/1000`. Export the variable or edit
`docker-compose.yml` to suit.

You'll see harmless warnings at container start — PortAudio probing
JACK / dmix backends that aren't wired up:

```
Cannot connect to server socket err = No such file or directory
jack server is not running or cannot be started
```

PortAudio still falls through to ALSA → pulse and the music plays.
They'd disappear if a `jackd` server were running inside the container,
but it's not worth the complexity here.
