"""WASAPI loopback capture + per-channel RMS analysis.

Captures whatever the chosen output device is playing (loopback) in small
frames and exposes per-channel RMS energy. Designed to run on its own thread;
the newest frame's levels are kept in a thread-safe latest-value buffer so the
UI never blocks on a growing queue (we only care about the newest frame).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np
import soundcard as sc


# ---- channel layout -------------------------------------------------------
# Standard WASAPI 7.1 channel order (KSAUDIO / Windows):
#   0 Front Left   1 Front Right   2 Center   3 LFE
#   4 Rear Left    5 Rear Right    6 Side Left 7 Side Right
# We label by index so the monitor and overlay agree on naming.
LAYOUT_71 = ["FL", "FR", "C", "LFE", "RL", "RR", "SL", "SR"]
LAYOUT_51 = ["FL", "FR", "C", "LFE", "RL", "RR"]
LAYOUT_STEREO = ["L", "R"]


def labels_for(channels: int) -> list[str]:
    if channels >= 8:
        return LAYOUT_71 + [f"ch{i}" for i in range(8, channels)]
    if channels == 6:
        return LAYOUT_51
    if channels == 2:
        return LAYOUT_STEREO
    return [f"ch{i}" for i in range(channels)]


@dataclass
class CaptureConfig:
    samplerate: int = 48000
    # ~12 ms frames at 48 kHz for low latency.
    blocksize: int = 576
    device_name: str | None = None  # None -> default speaker's loopback
    channels: int | None = None     # None -> device native channel count


# ---- frequency bands ------------------------------------------------------
# Broadband RMS hides a quiet sound under a loud one in the SAME channel: an
# engine or wind bed dominates the channel total, so a distant footstep barely
# moves it. Splitting into a few perceptual bands lets each sound be judged
# against the energy in ITS band only, which is what makes quiet-but-distinct
# cues (footsteps, proximity voices) readable.
BANDS = (
    ("low",   20.0,   120.0),    # rumble, explosions, vehicle body
    ("thump", 120.0,  500.0),    # footstep impacts, movement
    ("voice", 500.0,  2000.0),   # speech fundamentals, gear
    ("edge",  2000.0, 6000.0),   # speech consonants, gunshot crack, foliage
    ("air",   6000.0, 16000.0),  # hiss, casings, fine detail
)
BAND_NAMES = [b[0] for b in BANDS]


class BandSplitter:
    """Per-channel RMS within each band of BANDS, via one rFFT per block.

    Band RMS is scaled to be directly comparable with the plain broadband RMS
    (Parseval, corrected for the analysis window's power), so the same dB
    thresholds mean the same thing either way.

    The FFT runs over the last `window` samples, not just the block that just
    arrived. A 12 ms block spans only ~5 bins across 120-500 Hz, so its band
    level carries ±1.7 dB of pure estimator noise — more than a distant
    footstep contributes, which would make that footstep undetectable in
    principle. A longer window puts ~30 bins in the same band and successive
    windows overlap, so the measurement is stable enough for a small real rise
    to stand out. 2048 samples is 43 ms at 48 kHz: shorter than a footstep, so
    nothing is smeared away.
    """

    def __init__(self, samplerate: int = 48000, window: int = 2048):
        self.samplerate = samplerate
        self.window = window
        self._n = 0
        self._win: np.ndarray | None = None
        self._idx: list[np.ndarray] = []
        self._scale = 1.0
        self._hist: np.ndarray | None = None

    def _prepare(self, n: int) -> None:
        self._n = n
        self._win = np.hanning(n).astype(np.float32)
        # one-sided power sum -> mean square, undoing the window's power loss
        self._scale = 2.0 / (n * float(np.sum(self._win.astype(np.float64) ** 2)))
        freqs = np.fft.rfftfreq(n, 1.0 / self.samplerate)
        self._idx = []
        for _name, lo, hi in BANDS:
            sel = np.nonzero((freqs >= lo) & (freqs < hi))[0]
            if sel.size == 0:      # band narrower than one bin -> nearest bin
                sel = np.array([int(np.argmin(np.abs(freqs - 0.5 * (lo + hi))))])
            self._idx.append(sel)

    def _rolling(self, block: np.ndarray) -> np.ndarray:
        """The most recent `window` samples, ending with `block`."""
        ch = block.shape[1]
        if self._hist is None or self._hist.shape != (self.window, ch):
            # Prime with the first block rather than zeros. A zero-filled start
            # reports digital silence for the first few frames, and anything
            # downstream that learns a background level from it then has to
            # climb ~90 dB before it is usable.
            reps = -(-self.window // block.shape[0])
            self._hist = np.tile(np.asarray(block, dtype=np.float32),
                                 (reps, 1))[-self.window:].copy()
        take = min(block.shape[0], self.window)
        if take < self.window:
            self._hist[:-take] = self._hist[take:]
        self._hist[self.window - take:] = block[block.shape[0] - take:]
        return self._hist

    def analyse(self, block: np.ndarray) -> np.ndarray:
        """block (frames, channels) -> band RMS (channels, len(BANDS))."""
        if block.ndim != 2 or block.shape[0] < 1:
            return np.zeros((block.shape[1] if block.ndim == 2 else 0,
                             len(BANDS)), dtype=np.float32)
        frame = self._rolling(np.asarray(block, dtype=np.float32))
        n = frame.shape[0]
        if n != self._n:
            self._prepare(n)
        spec = np.fft.rfft(frame * self._win[:, None], axis=0)
        power = spec.real ** 2 + spec.imag ** 2        # (bins, channels)
        out = np.empty((frame.shape[1], len(BANDS)), dtype=np.float32)
        for b, sel in enumerate(self._idx):
            out[:, b] = np.sqrt(power[sel].sum(axis=0) * self._scale)
        return out


@dataclass
class Levels:
    """Latest per-channel RMS snapshot. Thread-safe via the holder's lock."""
    rms: np.ndarray = field(default_factory=lambda: np.zeros(0))
    channels: int = 0
    labels: list[str] = field(default_factory=list)
    ts: float = 0.0
    # per-channel per-band RMS, shape (channels, len(BANDS)); empty if the
    # backend didn't compute it (the analyser falls back to broadband then).
    bands: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))

    def copy(self) -> "Levels":
        return Levels(self.rms.copy(), self.channels, self.labels, self.ts,
                      self.bands.copy())


def list_loopback_devices() -> list:
    return [m for m in sc.all_microphones(include_loopback=True)
            if getattr(m, "isloopback", False)]


def list_output_devices() -> list:
    """Physical/virtual playback devices — for choosing where the mono mix
    plays in surround mode (e.g. your headset)."""
    return list(sc.all_speakers())


def _resolve_mic(cfg: CaptureConfig):
    if cfg.device_name is None:
        spk = sc.default_speaker()
        return sc.get_microphone(id=str(spk.name), include_loopback=True), spk
    mic = sc.get_microphone(id=cfg.device_name, include_loopback=True)
    return mic, mic


class LoopbackCapture:
    """Background loopback capture publishing the newest per-channel RMS."""

    def __init__(self, cfg: CaptureConfig | None = None):
        self.cfg = cfg or CaptureConfig()
        self._lock = threading.Lock()
        self._levels = Levels()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.actual_channels: int | None = None
        # optional recorder.SessionRecorder for tuning captures; set/cleared by
        # the UI while running. Read once per block so swapping it is safe.
        self.recorder = None

    def get_levels(self) -> Levels:
        with self._lock:
            return self._levels.copy()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="loopback",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        mic, _ = _resolve_mic(self.cfg)
        channels = self.cfg.channels or mic.channels
        splitter = BandSplitter(self.cfg.samplerate)
        with mic.recorder(samplerate=self.cfg.samplerate,
                          channels=channels,
                          blocksize=self.cfg.blocksize) as rec:
            while not self._stop.is_set():
                data = rec.record(numframes=self.cfg.blocksize)  # (frames, ch)
                if data.size == 0:
                    continue
                self.actual_channels = data.shape[1]
                rec = self.recorder
                if rec is not None:
                    rec.write(data)
                rms = np.sqrt(np.mean(np.square(data, dtype=np.float64),
                                      axis=0))
                with self._lock:
                    self._levels = Levels(
                        rms=rms.astype(np.float32),
                        channels=data.shape[1],
                        labels=labels_for(data.shape[1]),
                        ts=time.perf_counter(),
                        bands=splitter.analyse(data),
                    )


def rms_to_dbfs(rms: np.ndarray) -> np.ndarray:
    """Convert linear RMS to dBFS (-inf..0). Floor at -120 dB for display."""
    return 20.0 * np.log10(np.maximum(rms, 1e-6))
