# The eye is listening

## Inspiration

Music has always been a visual experience in our minds,
a bass drop *feels* like an explosion, a soft piano *looks* like
rippling water. We wanted to make that synesthesia real.
The question was simple: **what if Darude - Sandstorm didn't just
hit your ears, but painted a fractal?**

Every genre carries a different emotional geometry. A techno kick
lives in a different mathematical universe than a jazz chord.
We wanted to prove that visually.

---

## What we built

A real-time pipeline that listens to music and generates
**unique fractal images driven by the physics of the sound itself.**

The system extracts a rich set of audio features on every frame:

| Feature | What it captures |
|---|---|
| Volume (dBFS) | Energy level, from silence (−80 dB) to full scale (0 dB) |
| Bass / Mid / Treble | Spectral energy per band, each normalised 0 → 1 |
| Spectral centroid | Perceived brightness of the mix (Hz + log % scale) |
| Smoothness | Crest factor, how percussive vs. sustained the sound is |
| BPM | Tempo, recomputed every ~2 s from onset history |
| Chroma (12 bins) | Harmonic content per pitch class (C, C#, … B) |

Each of these values becomes a **parameter of the fractal renderer**,
zoom depth, iteration count, color palette, symmetry axis, animation speed.
A heavy bass hit warps the geometry. A bright cymbal opens the color space.
The BPM drives the rhythm of the visual evolution.

The result: **no two genres look the same.**

---

## How we built it

The architecture is a clean producer → wire → consumer split:

Music / mic / loopback
↓
Python audio brain
(FFT, chroma, BPM, smoothness)
↓
OSC over UDP (~86 Hz)
↓
C++ fractal renderer
↓
Live fractal image

**Producer (Python):** A `FeatureExtractor` runs one FFT per chunk
(512 samples @ 44.1 kHz) and derives all features. A `RollingPeak`
normaliser keeps band values in `[0, 1]` relative to recent history,
so quiet ambient music still drives the visuals, it auto-adapts.
Features are packed into an OSC `/audio/frame` message and fired
over UDP at ~86 Hz.

**Wire format:** ~140 bytes per packet. A `SequenceTracker` on the
receiver counts gaps, so dropped packets are visible in the dashboard.
On localhost, dropped = 0 always.

**Consumer (C++):** `UdpListener` → `OscParser` → fractal parameter
mapping → rendered frame. The C++ side is fully header-only modular,
drop the headers into any renderer.

**Infrastructure:** The entire project ships as two Docker images
orchestrated by a single `./play.sh` command. One `Ctrl-C` stops
everything. Works on Linux (PulseAudio Unix socket) and macOS
(PulseAudio TCP via BlackHole).

The smoothness metric deserves a special mention — it is derived
from the **crest factor**:

$$
\text{smoothness} = 1 - \log_{\,c_{\max}}\!\left(\frac{\text{peak}}{\text{RMS}}\right)
$$

A pure sine wave (crest $= \sqrt{2}$) gives smoothness $\approx 1.0$.
A sharp snare hit (crest $\approx 10$) gives smoothness $\approx 0.0$.
This single value tells the renderer whether to show fluid organic
forms or sharp geometric spikes.

---

## Challenges

- **Latency vs. accuracy tradeoff:** smaller chunks = lower latency
but noisier FFT. We settled on 512 samples (~12 ms) as the sweet
spot between reactivity and stable feature values.
- **BPM stability:** librosa's tempo estimator needs ~4 s of onset
history before it locks on, and half/double-tempo errors are common
in the first few seconds. We display until it stabilises.
- **Cross-platform audio routing:** Docker can't see Core Audio or
WASAPI directly. We bridge through PulseAudio TCP on macOS/Windows,
which required careful ALSA → pulse configuration inside the container.
- **Chroma vs. note:** early versions tried to send a single MIDI
note name. Real music is chords,  argmax of chroma is meaningless
most of the time. We switched to sending all 12 chroma bins and let
the renderer decide what to do with harmony.

---

## What we learned

- Real-time audio feature extraction is as much about **normalisation**
as it is about the math. Raw FFT values are useless without a
running-peak normaliser that adapts to the dynamic range of each song.
- OSC over UDP is a surprisingly elegant wire protocol for this use
case , stateless, low overhead, and trivially parseable in C++.
- The most musically meaningful features for visuals are not always
the obvious ones. **Smoothness** (crest factor) and **spectral
centroid** consistently produce more interesting mappings than
raw volume alone.

---

## Category

This project sits at the intersection of **audio art and visual art.**
The sound is the artist,  the fractal is its signature.
Every song paints something that has never existed before
and will never exist again in exactly that form.

## What's next for The eye is listening

Adding features such as scene creation for shows and spectacles would be really nice to submerge the users into a full visual and audio experience !
