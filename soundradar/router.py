"""Mono router — capture 7.1, feed the radar, AND play a full mono mix.

Solves the hearing problem without VoiceMeeter's lossy downmix: SoundRadar sums
ALL captured channels into one complete mono signal and plays it to the
headphones itself, so nothing is dropped.

Capture and playback run on SEPARATE threads with a small ring buffer between
them. This is essential: a single record->play loop cannot keep real time and
the output starves (audio plays back slow / glitchy). The capture thread reads
continuously (so the loopback never overflows) and the playback thread drains
the buffer at the output device's own pace.

Signal flow:
    game -> VAIO3 (7.1) --loopback--> [capture thread]
                                         |-- per-channel RMS -> radar overlay
                                         '-- sum -> mono -> ring buffer
                                                              |
                                          [playback thread] --'-> headphones
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass

import numpy as np
import soundcard as sc

from .audio import BandSplitter, Levels, labels_for


# Mono downmix weights by channel label. Everything is included so no sound is
# ever missed; center/surround at -3 dB (0.707), LFE kept substantial so
# low-end action stays audible.
DOWNMIX_WEIGHTS = {
    "FL": 1.0, "FR": 1.0, "L": 1.0, "R": 1.0,
    "C": 0.707,
    "LFE": 0.7,
    "RL": 0.707, "RR": 0.707,
    "SL": 0.707, "SR": 0.707,
}


def _weights(labels: list[str]) -> np.ndarray:
    return np.array([DOWNMIX_WEIGHTS.get(lbl, 1.0) for lbl in labels],
                    dtype=np.float32)


def downmix_to_mono(frame: np.ndarray, labels: list[str],
                    normalize: bool = True) -> np.ndarray:
    """Sum all channels to one mono signal, at a sane level.

    The raw weights sum to ~6.2 for 7.1, so a plain sum sends anything present
    across many channels (ambience, engines, explosions — most loud content)
    far past full scale. Dividing by the root-sum-square of the weights keeps
    the LOUDNESS of the result about equal to the source for the normal case of
    partly-correlated channels, instead of leaving the caller to pick a volume
    that either distorts or buries the quiet detail.

    Fully-correlated content can still exceed full scale after this, which is
    what PeakLimiter is for — that residual must be limited, never clipped.
    """
    w = _weights(labels)
    mono = frame.astype(np.float32) @ w
    if normalize:
        mono /= float(np.sqrt(np.sum(w * w)))
    return mono


class PeakLimiter:
    """Keeps the mono mix under `ceiling` without hard clipping.

    np.clip flattens every sample past full scale, which is heavy distortion on
    exactly the loud wide sounds a surround mix produces most. This instead
    turns the gain down just enough, just before it is needed:

      * a peak follower with instant attack and exponential release tracks the
        signal envelope;
      * the signal itself is delayed by `lookahead_ms`, so the gain reduction
        computed from a peak is already applied when that peak arrives at the
        output — no overshoot, and no clicks from a late correction;
      * gain recovers over `release_ms`, so a single transient does not duck
        the following quiet detail for long.

    Quiet material never reaches the ceiling, so it passes through untouched.
    """

    def __init__(self, samplerate: int = 48000, ceiling: float = 0.97,
                 lookahead_ms: float = 2.0, release_ms: float = 120.0):
        self.ceiling = float(ceiling)
        self.n_look = max(1, int(samplerate * lookahead_ms / 1000.0))
        self._decay = float(np.exp(-1.0 / max(samplerate * release_ms / 1000.0,
                                              1.0)))
        self._delay = np.zeros(self.n_look, dtype=np.float32)
        self._env = 0.0
        self.reductions = 0        # blocks where the limiter actually engaged

    def process(self, x: np.ndarray) -> np.ndarray:
        n = x.shape[0]
        if n == 0:
            return x
        # envelope: env[i] = max_j |x[j]| * decay^(i-j), including prior state.
        # Computed in closed form so no per-sample Python loop is needed.
        r = self._decay
        idx = np.arange(n + 1, dtype=np.float64)
        scale = r ** (-idx)
        seeded = np.concatenate(([self._env], np.abs(x).astype(np.float64)))
        env = np.maximum.accumulate(seeded * scale) / scale
        self._env = float(env[-1])
        env = env[1:]

        gain = np.minimum(1.0, self.ceiling / np.maximum(env, 1e-9))
        if np.any(gain < 1.0):
            self.reductions += 1

        # delay the audio so each peak meets its own gain reduction
        buf = np.concatenate((self._delay, x))
        out = buf[:n].astype(np.float32, copy=True)
        self._delay = buf[n:].copy()
        out *= gain.astype(np.float32)
        return out


class QuietLift:
    """Upward compression: raise QUIET audio, leave peaks alone.

    Volume cannot make the mix louder than full scale — past the point where
    peaks reach the limiter's ceiling, more gain produces a bit-identical
    output, so the control appears dead. The only way to genuinely increase
    perceived loudness is to reduce the distance between the quiet parts and the
    loud ones, which is also exactly what helps a hard-of-hearing listener pick
    out a distant footstep or someone talking.

    Gain is applied only below `threshold_db` and tapers to nothing at the
    threshold, so transients keep their impact and the radar's dynamics are
    unaffected (this is on the listening path only). Slow release keeps it from
    pumping.
    """

    # Threshold/floor set the window over which the lift ramps in. The span has
    # to be tight enough that genuinely quiet game audio (around -35 dBFS in the
    # mix) receives most of the available boost — a wide span gave a distant
    # footstep only a third of it, which is not worth a control.
    def __init__(self, samplerate: int = 48000, amount_db: float = 0.0,
                 threshold_db: float = -20.0, floor_db: float = -42.0,
                 attack_ms: float = 20.0, release_ms: float = 300.0):
        self.amount_db = amount_db
        self.threshold_db = threshold_db
        self.floor_db = floor_db
        self._atk = float(np.exp(-1.0 / max(samplerate * attack_ms / 1000.0, 1.0)))
        self._rel = float(np.exp(-1.0 / max(samplerate * release_ms / 1000.0, 1.0)))
        self._env = 1e-6
        self._gain_db = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        if self.amount_db <= 0.01 or x.size == 0:
            return x
        # one envelope + one gain per block: cheap, and 10 ms blocks are short
        # enough that per-sample smoothing buys nothing audible here.
        level = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
        c = self._atk if level > self._env else self._rel
        self._env = level + (self._env - level) * c
        env_db = 20.0 * np.log10(max(self._env, 1e-9))
        span = max(self.threshold_db - self.floor_db, 1.0)
        below = min(max((self.threshold_db - env_db) / span, 0.0), 1.0)
        target = self.amount_db * below
        # ease toward the target so a sudden quiet passage doesn't jump
        self._gain_db += (target - self._gain_db) * 0.25
        return (x * (10.0 ** (self._gain_db / 20.0))).astype(np.float32)


class MonoMix:
    """Downmix -> volume -> lift quiet detail -> limiter. One place, so both
    capture backends give the listener the same processed audio."""

    def __init__(self, samplerate: int = 48000, out_gain: float = 1.0,
                 lift_db: float = 0.0):
        self.out_gain = out_gain
        self.lift = QuietLift(samplerate, lift_db)
        self.limiter = PeakLimiter(samplerate)
        self.peak_in = 0.0

    def process(self, block: np.ndarray, labels: list[str]) -> np.ndarray:
        mono = downmix_to_mono(block, labels) * self.out_gain
        self.peak_in = float(np.max(np.abs(mono))) if mono.size else 0.0
        return self.limiter.process(self.lift.process(mono))


@dataclass
class RouterConfig:
    samplerate: int = 48000
    blocksize: int = 480            # 10 ms
    source_name: str | None = None  # None -> default speaker loopback (VAIO3)
    output_name: str = "Headphones"
    # 1.0 now means "about as loud as the source". The old 0.5 was chosen to
    # tame an un-normalised 6.2x sum and is not comparable.
    out_gain: float = 1.0
    lift_db: float = 0.0            # upward compression, dB (see QuietLift)
    target_buffer_ms: float = 60.0  # latency cushion playback steers toward
    max_buffer_ms: float = 400.0    # hard safety cap (drift handled smoothly)


class MonoRouter:
    def __init__(self, cfg: RouterConfig | None = None):
        self.cfg = cfg or RouterConfig()
        self._lock = threading.Lock()
        self._levels = Levels()
        self._buf = collections.deque()  # mono float32 chunks
        self._buf_samples = 0
        self._buf_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.peak_out = 0.0
        self.underruns = 0
        self.drops = 0
        self.recorder = None    # optional recorder.SessionRecorder (tuning)
        self.error: str | None = None
        self.capture_retries = 0
        self.output_retries = 0

    # -- radar interface (drop-in for LoopbackCapture) -------------------
    def get_levels(self) -> Levels:
        with self._lock:
            return self._levels.copy()

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._capture, name="cap", daemon=True),
            threading.Thread(target=self._playback, name="play", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=1.5)
        self._threads = []

    # -- threads ---------------------------------------------------------
    # Both threads retry instead of dying. A thread that raises here takes out
    # either the radar or ALL the audio the listener hears, and in a windowed
    # build the traceback goes nowhere — the app just silently stops working.
    # Anything recoverable (device in use, device removed, format change,
    # VoiceMeeter restarting) must reconnect, and the last error is kept so the
    # Check tab can say what is wrong.
    def _capture(self) -> None:
        while not self._stop.is_set():
            try:
                self._capture_session()
            except Exception as e:                     # noqa: BLE001
                self.error = f"capture: {type(e).__name__}: {e}"
                self.capture_retries += 1
                self._stop.wait(0.5)

    def _playback(self) -> None:
        while not self._stop.is_set():
            try:
                self._playback_session()
            except Exception as e:                     # noqa: BLE001
                self.error = f"output: {type(e).__name__}: {e}"
                self.output_retries += 1
                self._stop.wait(0.5)

    def _capture_session(self) -> None:
        if self.cfg.source_name is None:
            src = sc.get_microphone(id=str(sc.default_speaker().name),
                                    include_loopback=True)
        else:
            src = sc.get_microphone(id=self.cfg.source_name,
                                    include_loopback=True)
        n = self.cfg.blocksize
        max_samples = int(self.cfg.max_buffer_ms / 1000 * self.cfg.samplerate)
        splitter = BandSplitter(self.cfg.samplerate)
        mix = MonoMix(self.cfg.samplerate, self.cfg.out_gain, self.cfg.lift_db)
        with src.recorder(samplerate=self.cfg.samplerate, channels=None,
                          blocksize=n) as rec:
            while not self._stop.is_set():
                data = rec.record(numframes=n)
                if data.size == 0:
                    continue
                ch = data.shape[1]
                labels = labels_for(ch)
                # NB: must not be named `rec` — that is the audio recorder this
                # loop reads from, and shadowing it killed the capture thread
                # after a single block.
                session = self.recorder
                if session is not None:
                    session.write(data)
                rms = np.sqrt(np.mean(np.square(data, dtype=np.float64),
                                      axis=0)).astype(np.float32)
                with self._lock:
                    self._levels = Levels(rms, ch, labels, time.perf_counter(),
                                          splitter.analyse(data))
                mix.out_gain = self.cfg.out_gain      # live volume changes
                mix.lift.amount_db = self.cfg.lift_db
                mono = mix.process(data, labels)
                with self._buf_lock:
                    self._buf.append(mono)
                    self._buf_samples += mono.shape[0]
                    # drift guard: drop oldest if we're running too far ahead
                    while self._buf_samples > max_samples and self._buf:
                        old = self._buf.popleft()
                        self._buf_samples -= old.shape[0]
                        self.drops += 1

    def _pull(self, n: int) -> np.ndarray:
        """Pull exactly n mono samples; pad with zeros on underrun."""
        out = np.zeros(n, dtype=np.float32)
        got = 0
        with self._buf_lock:
            while got < n and self._buf:
                chunk = self._buf[0]
                take = min(n - got, chunk.shape[0])
                out[got:got + take] = chunk[:take]
                if take == chunk.shape[0]:
                    self._buf.popleft()
                else:
                    self._buf[0] = chunk[take:]
                self._buf_samples -= take
                got += take
        if got < n:
            self.underruns += 1
        return out

    def _playback_session(self) -> None:
        out = sc.get_speaker(self.cfg.output_name)
        n = self.cfg.blocksize
        sr = self.cfg.samplerate
        target = int(self.cfg.target_buffer_ms / 1000 * sr)
        # prime: wait until the cushion is filled so we never start starved
        while not self._stop.is_set():
            with self._buf_lock:
                ready = self._buf_samples
            if ready >= target:
                break
            time.sleep(0.002)
        out_idx = np.arange(n, dtype=np.float32)
        with out.player(samplerate=sr, channels=2, blocksize=n) as player:
            while not self._stop.is_set():
                with self._buf_lock:
                    fill = self._buf_samples
                # Steer the buffer toward `target` by nudging how many input
                # samples feed each output block, capped at +/-1% (inaudible
                # pitch shift). This absorbs capture/playback clock drift with
                # no clicks instead of dropping/padding whole chunks.
                adj = max(-0.01, min(0.01, (fill - target) / (target * 4.0 + 1.0)))
                pull_n = max(8, int(round(n * (1.0 + adj))))
                block = self._pull(pull_n)
                if pull_n != n:
                    src = np.arange(pull_n, dtype=np.float32)
                    block = np.interp(np.linspace(0.0, pull_n - 1.0, n),
                                      src, block).astype(np.float32)
                self.peak_out = float(np.max(np.abs(block))) if block.size else 0.0
                player.play(np.stack([block, block], axis=1))
