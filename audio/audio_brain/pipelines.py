"""Streaming audio pipelines: file playback, mic capture, virtual loopback.

The three pipelines share roughly the same body: open one or two
PortAudio streams, run a per-chunk loop driven by a blocking ``read`` /
``write`` (which paces it to real time), feed each chunk through a
:class:`FeatureExtractor`, push features over OSC, throttle a dashboard
refresh. The shared scaffolding lives on :class:`BasePipeline`; each
concrete pipeline only implements what's actually different — opening
the right kind of stream and yielding mono blocks from it.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path
from typing import ClassVar, Iterator, Optional

import numpy as np
import pyaudio

from .config import CHUNK, PRINT_HZ
from .dashboard import Dashboard
from .devices import (
    find_loopback_input,
    find_matching_output,
    find_pulse_output,
    is_monitor_source,
    is_raw_alsa_hardware,
    open_best_duplex_stream,
    open_best_stream,
    print_pulse_routing,
    pulse_env_routing,
    resolve_loopback_capture_device,
    resolve_pulse_capture_device,
    resolve_pulse_playback_device,
    routes_through_pulse,
)
from .extractor import FeatureExtractor
from .loaders import audio_duration_s, load_streamer
from .osc import OscSender


class PipelineAbort(RuntimeError):
    """Raised by :meth:`BasePipeline.setup` when the pipeline can't start.

    Examples: no input device available, can't open a stream at the
    requested sample rate, no virtual-loopback device found. The
    :meth:`BasePipeline.run` orchestrator catches this, prints the
    message to ``stderr``, and returns cleanly.
    """


# --------------------------------------------------------------------------- #
#  Base class — owns the PortAudio handle, the extractor, the dashboard, and  #
#  the per-chunk processing loop.                                             #
# --------------------------------------------------------------------------- #


class BasePipeline:
    """Common scaffolding for every streaming pipeline.

    Subclasses only need to provide:

    * a :data:`title` class attribute (shown on the dashboard top bar),
    * :meth:`setup` to open PortAudio streams and store any per-stream
      state (channel counts, devices, file blocks, …),
    * :meth:`iter_blocks` to yield mono ``float32`` arrays of length
      :data:`audio_brain.config.CHUNK` — typically driven by a blocking
      ``stream.read`` or ``stream.write`` so the iterator paces itself
      to real time.

    Optional hooks: :meth:`announce` to print extra context lines after
    warm-up, :meth:`teardown` for post-loop cleanup (mic-mode replay
    lives there), :meth:`_on_interrupt` for custom Ctrl-C reporting.

    When :class:`OscSender` is attached, the in-process :class:`Dashboard` is
    **not** redrawn — the C++ receiver owns the terminal UI (see ``play.sh``).
    """

    #: Title shown on the dashboard top bar. Override in subclasses.
    title: ClassVar[str] = "audio (sender)"

    def __init__(
        self,
        *,
        rate: int,
        chunk: int = CHUNK,
        osc: Optional[OscSender] = None,
        dashboard: Optional[Dashboard] = None,
    ) -> None:
        self.rate = rate
        self.chunk = chunk
        self.osc = osc
        self.dashboard = dashboard or Dashboard(title=self.title)
        self.extractor = FeatureExtractor(rate=rate, chunk=chunk)

        self._pa: Optional[pyaudio.PyAudio] = None
        self._streams: list[pyaudio.Stream] = []
        self._frames = 0
        self._sent_seq = 0
        self._last_print_t = -1e9
        #: Fixed stream length (music/file). ``None`` → live modes use elapsed time.
        self._total_time_s: Optional[float] = None

    # ------------------------------------------------------------------ #
    # Subclass-customisation hooks                                       #
    # ------------------------------------------------------------------ #

    def setup(self) -> None:
        """Open streams. Override in subclasses.

        Use :meth:`_open_stream` (which auto-tracks the stream for
        teardown) instead of calling ``self._pa.open`` directly.
        """

    def iter_blocks(self) -> Iterator[np.ndarray]:
        """Yield mono ``CHUNK``-sized ``float32`` buffers.

        Subclasses must implement this. Each yield triggers one feature
        extraction + OSC send + (throttled) dashboard refresh.
        """
        raise NotImplementedError

    def teardown(self) -> None:
        """Run after the main loop exits (cleanly or via Ctrl-C).

        Default is a no-op; subclasses can override to e.g. play back a
        captured recording.
        """

    def announce(self) -> None:
        """Print any per-pipeline context line(s) after warm-up.

        Default behaviour: log the OSC destination if we have one.
        Override and call ``super().announce()`` to keep the OSC line.
        """
        if self.osc is not None:
            print(
                f"sending OSC to {self.osc.host}:{self.osc.port} "
                f"as /audio/frame"
            )

    # ------------------------------------------------------------------ #
    # Run loop                                                           #
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """Full lifecycle: PyAudio init → setup → warmup → loop → teardown.

        Catches :class:`KeyboardInterrupt` so callers don't see a
        traceback on Ctrl-C. Catches :class:`PipelineAbort` from
        :meth:`setup` and prints a short error message before returning.
        Network and audio resources are always released, even on error.
        """
        self._pa = pyaudio.PyAudio()
        try:
            try:
                self.setup()
            except PipelineAbort as e:
                print(f"error: {e}", file=sys.stderr)
                return

            self._warmup()
            self.announce()

            try:
                for mono in self.iter_blocks():
                    self._process(mono)
            except KeyboardInterrupt:
                self._on_interrupt()
        finally:
            try:
                self.teardown()
            finally:
                self._close_all_streams()
                if self._pa is not None:
                    self._pa.terminate()
                    self._pa = None

    # ------------------------------------------------------------------ #
    # Helpers usable from subclasses                                     #
    # ------------------------------------------------------------------ #

    @property
    def pa(self) -> pyaudio.PyAudio:
        """Active PortAudio handle. Only valid between setup and teardown."""
        assert self._pa is not None, "PyAudio not initialised — call .run()"
        return self._pa

    def _open_stream(self, **kwargs) -> pyaudio.Stream:
        """Open and remember a PortAudio stream for automatic teardown."""
        stream = self.pa.open(**kwargs)
        self._streams.append(stream)
        return stream

    @property
    def frames_processed(self) -> int:
        """Total samples processed since the run started."""
        return self._frames

    @property
    def stream_time_s(self) -> float:
        """Stream-relative time in seconds (``frames / rate``)."""
        return self._frames / self.rate

    @property
    def total_time_s(self) -> float:
        """Total stream length for OSC (file duration, or elapsed time live)."""
        if self._total_time_s is not None and self._total_time_s > 0:
            return self._total_time_s
        return self.stream_time_s

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    def _warmup(self) -> None:
        print("warming up feature extractors...", end="", flush=True)
        self.extractor.warmup()
        print(" done.")

    def _process(self, mono: np.ndarray) -> None:
        """Extract features, send OSC, and optionally refresh the dashboard."""
        feats = self.extractor(mono)
        t = self.stream_time_s
        total_t = self.total_time_s
        if self.osc is not None:
            self.osc.send(t, feats, total_time=total_t)
            self._sent_seq = self.osc.seq
        else:
            self._sent_seq += 1

        # With OSC, the C++ receiver owns the dashboard (avoids duplicate/jumbled
        # output when play.sh streams container logs).
        if self.osc is None and t - self._last_print_t >= 1.0 / PRINT_HZ:
            self.dashboard.render(t, feats, self._sent_seq, total_time=total_t)
            self._last_print_t = t

        self._frames += self.chunk

    def _on_interrupt(self) -> None:
        """Default Ctrl-C handler: report stream-time and continue tear-down."""
        print(f"\nstopped after {self.stream_time_s:.1f}s.")

    def _close_all_streams(self) -> None:
        for stream in self._streams:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        self._streams.clear()


# --------------------------------------------------------------------------- #
#  File mode — read a file from disk, push to speakers, analyse on the way.   #
# --------------------------------------------------------------------------- #


class FilePlaybackPipeline(BasePipeline):
    """Stream a song from disk to the speakers + emit features.

    Output stream's blocking ``write`` paces the loop, so OSC frames stay
    in lockstep with what the user hears.

    With ``PULSE_SERVER`` set, :meth:`setup` resolves the output via
    :func:`~audio_brain.devices.resolve_pulse_playback_device` so
    ``PULSE_SINK`` (e.g. Bluetooth) from ``play.sh --use-route`` is honoured.

    Parameters
    ----------
    path
        File to play. Anything libsndfile or librosa can decode (WAV /
        MP3 / FLAC / OGG / …) is supported.
    device_index
        PortAudio output device index. ``None`` auto-selects Pulse/default.
    osc / dashboard
        See :class:`BasePipeline`.
    """

    title: ClassVar[str] = "audio (sender)"

    def __init__(
        self,
        path: Path,
        *,
        device_index: Optional[int] = None,
        osc: Optional[OscSender] = None,
        dashboard: Optional[Dashboard] = None,
    ) -> None:
        # Open the file first so we can size the extractor to the real rate.
        rate, channels, blocks = load_streamer(path)
        super().__init__(rate=rate, osc=osc, dashboard=dashboard)
        self.path = path
        self.channels = channels
        self.device_index = device_index
        self._blocks = blocks
        self._out_stream: Optional[pyaudio.Stream] = None
        self._total_time_s = audio_duration_s(path)

    def setup(self) -> None:
        print_pulse_routing()
        out_dev = self.device_index
        if out_dev is None and routes_through_pulse():
            out_dev = resolve_pulse_playback_device(self.pa)
            if out_dev is not None:
                self.device_index = out_dev
        if self.device_index is not None:
            info = self.pa.get_device_info_by_index(self.device_index)
            print(f"using output device [{self.device_index}] {info['name']}")
        if self.device_index is None:
            try:
                self.device_index = int(
                    self.pa.get_default_output_device_info()["index"]
                )
            except OSError as exc:
                raise PipelineAbort(f"no output device: {exc}") from exc
        try:
            self._out_stream, _, _out_ch = open_best_stream(
                self.pa,
                direction="output",
                device_index=self.device_index,
                rate=self.rate,
                frames_per_buffer=self.chunk,
            )
            self._streams.append(self._out_stream)
        except OSError as exc:
            raise PipelineAbort(
                f"failed to open playback stream: {exc}"
            ) from exc

    def announce(self) -> None:
        latency_ms = 1000 * self.chunk / self.rate
        print(
            f"playing: {self.path.name} | {self.rate} Hz | "
            f"{self.channels}ch | duration={self._total_time_s:.1f}s | "
            f"chunk={self.chunk} samples (~{latency_ms:.0f} ms)"
        )
        super().announce()

    def iter_blocks(self) -> Iterator[np.ndarray]:
        assert self._out_stream is not None, "FilePlaybackPipeline not set up"
        for block in self._blocks:
            if len(block) < self.chunk:
                pad = np.zeros(
                    (self.chunk - len(block), self.channels), dtype=np.float32,
                )
                block = np.vstack([block, pad])
            self._out_stream.write(np.ascontiguousarray(block).tobytes())
            mono = (
                block.mean(axis=1) if self.channels > 1 else block[:, 0]
            ).astype(np.float32)
            yield mono


# --------------------------------------------------------------------------- #
#  Mic mode — capture from the microphone, optionally play back on stop.      #
# --------------------------------------------------------------------------- #


class MicCapturePipeline(BasePipeline):
    """Capture audio from the microphone + emit features.

    By default nothing is written to the speakers *during* capture (avoids
    feedback on laptop speakers). Pass ``monitor=True`` for real-time
    pass-through — use headphones, or split routing (laptop mic + BT out).

    With ``monitor=True``, post-capture replay is **off by default** (you
    already heard everything live). Pass ``playback=True`` or ``--replay``
    on the CLI if you want both.

    Docker / Pulse behaviour:

    * ``PULSE_SOURCE`` / ``PULSE_SINK`` from ``play.sh`` select host routes.
    * Opens PortAudio ``default``, not raw ``hw:*`` (see ``devices.py``).
    * Split routes use a **duplex** stream on ``default`` for live monitor.
    * Runs a short capture probe after open; warns if the mic is silent.

    Parameters
    ----------
    input_device
        PortAudio input index. ``None`` → :func:`resolve_pulse_capture_device`.
    output_device
        PortAudio output for monitor/replay. ``None`` →
        :func:`resolve_pulse_playback_device`.
    rate
        Sample rate to request; actual rate may differ (see ``open_best_stream``).
    monitor
        If ``True``, pass captured audio to the output in real time.
    playback
        If ``True``, buffer chunks and replay the full recording on stop.
    """

    title: ClassVar[str] = "mic (sender)"

    def __init__(
        self,
        *,
        input_device: Optional[int] = None,
        output_device: Optional[int] = None,
        rate: int = 44100,
        monitor: bool = False,
        playback: bool = True,
        osc: Optional[OscSender] = None,
        dashboard: Optional[Dashboard] = None,
    ) -> None:
        super().__init__(rate=rate, osc=osc, dashboard=dashboard)
        self.input_device = input_device
        self.output_device = output_device
        self.monitor = monitor
        self.playback = playback
        self._in_stream: Optional[pyaudio.Stream] = None
        self._out_stream: Optional[pyaudio.Stream] = None
        self._recording: list[np.ndarray] = []
        self._in_channels: int = 1
        self._out_channels: int = 1
        self._monitor_queue: Optional[queue.Queue[Optional[bytes]]] = None
        self._monitor_stop: Optional[threading.Event] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_write_errors: int = 0
        self._duplex: bool = False

    def _probe_input_peak(self, *, chunks: int = 6) -> float:
        """Read a few blocks and report peak level (sanity-check the mic)."""
        assert self._in_stream is not None
        peak = 0.0
        for _ in range(chunks):
            raw = self._in_stream.read(self.chunk, exception_on_overflow=False)
            buf = np.frombuffer(raw, dtype=np.float32)
            if self._in_channels > 1:
                buf = buf.reshape(-1, self._in_channels).mean(axis=1)
            peak = max(peak, float(np.max(np.abs(buf))))
        if peak < 1e-4:
            src = os.environ.get("PULSE_SOURCE", "(unset)")
            print(
                f"warning: capture is silent (peak={peak:.2e}) — "
                f"check PULSE_SOURCE={src}\n"
                f"  PipeWire HiFi: prefer *Mic1* not *Mic2* "
                f"(./play.sh pulse-routes)\n"
                f"  Host test: parec -d {src} /dev/null  # speak; Ctrl-C",
                file=sys.stderr,
            )
        else:
            print(f"capture probe: peak={peak:.4f} (mic signal detected)")
        return peak

    def setup(self) -> None:
        print_pulse_routing()

        try:
            if self.input_device is not None:
                info = self.pa.get_device_info_by_index(self.input_device)
            else:
                mic_idx = resolve_pulse_capture_device(self.pa)
                if mic_idx is not None:
                    self.input_device = mic_idx
                    info = self.pa.get_device_info_by_index(mic_idx)
                else:
                    info = self.pa.get_default_input_device_info()
                    self.input_device = int(info["index"])
        except OSError as exc:
            raise PipelineAbort(f"no input device available ({exc})") from exc

        name = str(info["name"])
        if self.monitor and is_monitor_source(name):
            raise PipelineAbort(
                f"input [{self.input_device}] {name!r} is a speaker monitor, "
                f"not a microphone — pass --input-device N (see list-devices)."
            )
        if routes_through_pulse() and is_raw_alsa_hardware(name):
            raise PipelineAbort(
                f"refusing raw ALSA device [{self.input_device}] {name!r} "
                f"while PULSE_SERVER is set — audio would bypass Bluetooth.\n"
                f"  Run ./play.sh list-devices and use a 'puls' / default "
                f"index, or fix the Pulse socket mount (see README)."
            )

        print(f"using input device [{self.input_device}] {name}")

        out_idx = self.output_device
        if self.monitor and out_idx is None:
            if routes_through_pulse():
                out_idx = resolve_pulse_playback_device(self.pa)
            else:
                out_idx = find_matching_output(self.pa, self.input_device)

        use_duplex = (
            self.monitor
            and pulse_env_routing()
            and out_idx is not None
            and out_idx == self.input_device
        )

        try:
            if use_duplex:
                self._duplex = True
                self.output_device = out_idx
                out_info = self.pa.get_device_info_by_index(out_idx)
                print(
                    f"monitoring (duplex) through [{out_idx}] "
                    f"{out_info['name']}"
                )
                stream, self.rate, self._in_channels = open_best_duplex_stream(
                    self.pa,
                    device_index=self.input_device,
                    rate=self.rate,
                    frames_per_buffer=self.chunk,
                )
                self._out_channels = self._in_channels
                self._streams.append(stream)
                self._in_stream = stream
                self._out_stream = stream
            else:
                self._in_stream, self.rate, self._in_channels = open_best_stream(
                    self.pa,
                    direction="input",
                    device_index=self.input_device,
                    rate=self.rate,
                    frames_per_buffer=self.chunk,
                )
                self._streams.append(self._in_stream)
        except OSError as exc:
            raise PipelineAbort(
                f"failed to open input stream (tried multiple rates/channels): "
                f"{exc}"
            ) from exc

        print(
            f"capture: {self.rate} Hz, {self._in_channels}ch "
            f"(chunk={self.chunk})"
        )
        self._probe_input_peak()

        if not self.monitor:
            return

        if self._duplex:
            print(f"monitor: duplex {self._out_channels}ch")
            return

        try:
            if self.output_device is None:
                if out_idx is not None:
                    self.output_device = out_idx
                    out_info = self.pa.get_device_info_by_index(out_idx)
                else:
                    out_info = self.pa.get_default_output_device_info()
                    self.output_device = int(out_info["index"])
            else:
                out_info = self.pa.get_device_info_by_index(self.output_device)
            print(
                f"monitoring through [{self.output_device}] {out_info['name']}"
            )
            self._out_stream, out_rate, self._out_channels = open_best_stream(
                self.pa,
                direction="output",
                device_index=self.output_device,
                rate=self.rate,
                frames_per_buffer=self.chunk,
            )
            self._streams.append(self._out_stream)
            if out_rate != self.rate:
                print(
                    f"warning: output opened at {out_rate} Hz but capture is "
                    f"{self.rate} Hz — monitor may sound wrong",
                    file=sys.stderr,
                )
        except OSError as exc:
            print(f"warning: live monitor disabled ({exc})", file=sys.stderr)
            self._out_stream = None
            return

        print(f"monitor: {self._out_channels}ch output")

        self._monitor_queue = queue.Queue(maxsize=2)
        self._monitor_stop = threading.Event()
        self._monitor_thread = threading.Thread(
            target=self._monitor_worker,
            name="mic-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _write_monitor(self, mono: np.ndarray) -> None:
        """Play one mono chunk on the monitor output stream."""
        if self._out_stream is None:
            return
        try:
            if self._out_channels == 2:
                stereo = np.column_stack([mono, mono])
                self._out_stream.write(
                    np.ascontiguousarray(stereo).tobytes()
                )
            else:
                self._out_stream.write(
                    np.ascontiguousarray(mono).tobytes()
                )
        except OSError as exc:
            self._monitor_write_errors += 1
            if self._monitor_write_errors == 1:
                print(
                    f"warning: monitor write failed: {exc}",
                    file=sys.stderr,
                )

    def _monitor_worker(self) -> None:
        """Drain the monitor queue into the output stream."""
        assert self._monitor_queue is not None
        while not self._monitor_stop.is_set():
            try:
                raw = self._monitor_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if raw is None:
                break
            if self._out_stream is not None:
                samples = np.frombuffer(raw, dtype=np.float32)
                if self._in_channels > 1:
                    samples = samples.reshape(-1, self._in_channels).mean(axis=1)
                self._write_monitor(samples)

    def _stop_monitor(self) -> None:
        if self._monitor_thread is None:
            return
        assert self._monitor_stop is not None
        self._monitor_stop.set()
        if self._monitor_queue is not None:
            try:
                self._monitor_queue.put_nowait(None)
            except queue.Full:
                pass
        self._monitor_thread.join(timeout=1.0)
        self._monitor_thread = None

    def announce(self) -> None:
        latency_ms = 1000 * self.chunk / self.rate
        print(
            f"listening: mic | {self.rate} Hz | mono | "
            f"chunk={self.chunk} samples (~{latency_ms:.0f} ms)"
        )
        super().announce()
        if self.monitor:
            print(
                "(live monitor on — audio goes to Pulse/your headphones; "
                "the receiver dashboard does not play sound)"
            )
        stop_hint = "press Ctrl-C to stop"
        if self.playback:
            stop_hint += " and play back" if not self.monitor else " (and replay after)"
        print(f"({stop_hint})")

    def iter_blocks(self) -> Iterator[np.ndarray]:
        assert self._in_stream is not None, "MicCapturePipeline not set up"
        while True:
            # exception_on_overflow=False → drop samples instead of raising
            # if our processing falls behind the audio thread.
            raw = self._in_stream.read(self.chunk, exception_on_overflow=False)
            buf = np.frombuffer(raw, dtype=np.float32)
            if self._in_channels > 1:
                mono = buf.reshape(-1, self._in_channels).mean(axis=1).copy()
            else:
                mono = buf.copy()
            if self._duplex and self.monitor:
                self._write_monitor(mono)
            elif self._monitor_queue is not None:
                chunk = np.ascontiguousarray(mono).tobytes()
                try:
                    self._monitor_queue.put_nowait(chunk)
                except queue.Full:
                    try:
                        self._monitor_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._monitor_queue.put_nowait(chunk)
                    except queue.Full:
                        pass
            if self.playback:
                self._recording.append(mono)
            yield mono

    def _on_interrupt(self) -> None:
        captured_s = self.stream_time_s
        print(f"\nstopped capture after {captured_s:.1f}s.")

    def teardown(self) -> None:
        self._stop_monitor()
        if not (self.playback and self._recording and self._pa is not None):
            return
        self._playback_recording()

    # ------------------------------------------------------------------ #
    # Replay helper                                                      #
    # ------------------------------------------------------------------ #

    def _playback_recording(self) -> None:
        """Concatenate captured mono chunks and play them back once."""
        audio = np.concatenate(self._recording)
        duration = len(audio) / self.rate
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0

        if peak < 1e-4:
            print("(captured audio is essentially silent — skipping playback)")
            return

        out_dev = self.output_device
        if out_dev is None:
            try:
                out_dev = int(self.pa.get_default_output_device_info()["index"])
            except OSError:
                out_dev = None
        if out_dev is not None:
            try:
                info = self.pa.get_device_info_by_index(out_dev)
                print(
                    f"playing back {duration:.1f}s on [{out_dev}] "
                    f"{info['name']} (peak={peak:.3f}, Ctrl-C to skip)"
                )
            except OSError:
                out_dev = None
        if out_dev is None:
            print(
                f"playing back {duration:.1f}s on default output "
                f"(peak={peak:.3f}, Ctrl-C to skip)"
            )
            try:
                out_dev = int(self.pa.get_default_output_device_info()["index"])
            except OSError as exc:
                print(f"error: no output device: {exc}", file=sys.stderr)
                return

        try:
            out_stream, _, out_ch = open_best_stream(
                self.pa,
                direction="output",
                device_index=out_dev,
                rate=self.rate,
                frames_per_buffer=self.chunk,
            )
        except OSError as exc:
            print(f"error: failed to open output stream: {exc}", file=sys.stderr)
            return

        try:
            for i in range(0, len(audio), self.chunk):
                block = audio[i:i + self.chunk]
                if len(block) < self.chunk:
                    pad = np.zeros(self.chunk - len(block), dtype=np.float32)
                    block = np.concatenate([block, pad])
                if out_ch == 2:
                    block = np.column_stack([block, block]).ravel()
                out_stream.write(np.ascontiguousarray(block).tobytes())
        except KeyboardInterrupt:
            print("\nplayback skipped.")


# --------------------------------------------------------------------------- #
#  Virtual-loopback mode — analyse a DAW (BlackHole / pulse loopback / …).    #
# --------------------------------------------------------------------------- #


class LoopbackPipeline(BasePipeline):
    """Capture from a virtual loopback (e.g. BlackHole on macOS) + analyse.

    macOS recipe::

        brew install --cask blackhole-2ch
        # In *Audio MIDI Setup* create a Multi-Output Device containing
        # BlackHole + your speakers; pick it as system output (or use it
        # as the output device inside GarageBand).

    Pass ``--midi`` on the CLI to dispatch into this pipeline. With Docker,
    use ``play.sh midi --pick-route`` / ``--use-route`` so ``PULSE_SOURCE``
    and ``PULSE_SINK`` select loopback capture and monitor output (same as
    mic/music). BlackHole is auto-detected when no Pulse route is set.

    Parameters
    ----------
    input_device
        PortAudio input index. ``None`` → :func:`resolve_loopback_capture_device`.
    output_device
        Monitor output index. ``None`` → :func:`resolve_pulse_playback_device`.
    rate
        Sample rate to request from the loopback input.
    monitor
        If ``False``, captured audio is *not* routed to an output device.
    """

    title: ClassVar[str] = "loopback (sender)"

    def __init__(
        self,
        *,
        input_device: Optional[int] = None,
        output_device: Optional[int] = None,
        rate: int = 44100,
        monitor: bool = True,
        osc: Optional[OscSender] = None,
        dashboard: Optional[Dashboard] = None,
    ) -> None:
        super().__init__(rate=rate, osc=osc, dashboard=dashboard)
        self.input_device = input_device
        self.output_device = output_device
        self.monitor = monitor
        self._in_stream: Optional[pyaudio.Stream] = None
        self._out_stream: Optional[pyaudio.Stream] = None
        self._in_channels: int = 1
        self._out_channels: int = 1
        self._device_name: str = ""

    def setup(self) -> None:
        print_pulse_routing()

        try:
            if self.input_device is None:
                self.input_device = resolve_loopback_capture_device(self.pa)
                if self.input_device is None:
                    raise PipelineAbort(
                        "no loopback/BlackHole-like input device found.\n"
                        "  Docker: ./play.sh midi --pick-route (choose a "
                        "Monitor of … or loopback source)\n"
                        "  macOS: brew install --cask blackhole-2ch\n"
                        "  Or pass --input-device N (see --list-devices)."
                    )
            info = self.pa.get_device_info_by_index(self.input_device)
            self._device_name = str(info["name"])
            if routes_through_pulse() and is_raw_alsa_hardware(self._device_name):
                raise PipelineAbort(
                    f"refusing raw ALSA device [{self.input_device}] "
                    f"{self._device_name!r} while PULSE_SERVER is set."
                )
            self._in_channels = max(1, min(int(info["maxInputChannels"]), 2))
            print(
                f"using loopback input [{self.input_device}] "
                f"{self._device_name} ({self._in_channels}ch)"
            )
        except OSError as exc:
            raise PipelineAbort(str(exc)) from exc

        try:
            self._in_stream, self.rate, in_ch = open_best_stream(
                self.pa,
                direction="input",
                device_index=self.input_device,
                rate=self.rate,
                frames_per_buffer=self.chunk,
            )
            self._in_channels = in_ch
            self._streams.append(self._in_stream)
        except OSError as exc:
            raise PipelineAbort(
                f"failed to open loopback input: {exc}"
            ) from exc
        print(f"capture: {self.rate} Hz, {self._in_channels}ch")

        if not self.monitor:
            return

        try:
            out_idx = self.output_device
            if out_idx is None and routes_through_pulse():
                out_idx = resolve_pulse_playback_device(self.pa)
            if out_idx is None:
                out_idx = int(self.pa.get_default_output_device_info()["index"])
            self.output_device = out_idx
            out_info = self.pa.get_device_info_by_index(out_idx)
            print(
                f"monitoring through [{out_idx}] {out_info['name']}"
            )
            self._out_stream, _, self._out_channels = open_best_stream(
                self.pa,
                direction="output",
                device_index=out_idx,
                rate=self.rate,
                frames_per_buffer=self.chunk,
            )
            self._streams.append(self._out_stream)
        except OSError as exc:
            print(f"warning: monitoring disabled ({exc})", file=sys.stderr)
            self._out_stream = None

    def announce(self) -> None:
        latency_ms = 1000 * self.chunk / self.rate
        print(
            f"loopback: {self._device_name} | {self.rate} Hz | "
            f"{self._in_channels}ch | chunk={self.chunk} samples "
            f"(~{latency_ms:.0f} ms)"
        )
        super().announce()
        print("(press Ctrl-C to stop)")

    def _write_monitor(self, raw: bytes) -> None:
        if self._out_stream is None:
            return
        try:
            buf = np.frombuffer(raw, dtype=np.float32)
            if self._out_channels == 2 and self._in_channels == 1:
                mono = buf.reshape(-1, self._in_channels).mean(axis=1)
                stereo = np.column_stack([mono, mono])
                self._out_stream.write(
                    np.ascontiguousarray(stereo).tobytes()
                )
            else:
                self._out_stream.write(raw)
        except OSError as exc:
            print(f"warning: monitor write failed: {exc}", file=sys.stderr)

    def iter_blocks(self) -> Iterator[np.ndarray]:
        assert self._in_stream is not None, "LoopbackPipeline not set up"
        while True:
            raw = self._in_stream.read(self.chunk, exception_on_overflow=False)
            if self._out_stream is not None:
                self._write_monitor(raw)
            buf = np.frombuffer(raw, dtype=np.float32)
            if self._in_channels > 1:
                mono = buf.reshape(-1, self._in_channels).mean(axis=1).astype(
                    np.float32
                )
            else:
                mono = buf.copy()
            yield mono
