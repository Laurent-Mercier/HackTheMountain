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

- **Producer:** `main.py` — Python, real-time audio pipeline (file or mic).
  See the `FeatureExtractor` and `OscSender` classes for the exact formulas.
- **Wire format:** OSC over UDP localhost, address `/audio/frame`,
  ~136 bytes per packet at ~21 Hz. Full schema is in the `OscSender`
  docstring and mirrored in `osc_receiver.cpp`.
- **Consumer (PoC):** `osc_receiver.cpp` — single-file C++ receiver that
  prints the same dashboard. Drop its `Frame` struct and
  `parse_audio_frame` into your real visualizer to consume the stream.
