"""Record a tuning session: the raw captured audio, plus timestamped marks.

Why raw audio rather than a log of detector values: the detector's own numbers
are only meaningful for the exact band edges, window length and thresholds that
produced them. Change any of those and an old log is worthless. A WAV of what
the capture actually heard can be replayed through any future version of the
analysis offline, as many times as needed, without playing the game again.

Writes two files side by side in %APPDATA%/SoundRadar/captures/:
    <name>.wav   — 16-bit PCM, the captured channels at their native layout.
                   (16-bit floors at about -96 dBFS, well below the ~-75 dBFS
                   the detector cares about, and is half the size of float32.)
    <name>.json  — samplerate, channel labels, the settings in force, and the
                   marks: {t: seconds-into-the-recording, label}.

Disk writes happen on their own thread. The capture thread must never block on
I/O — a stall there is a gap in the audio the radar sees (and, in surround mode,
in what the user hears).
"""

from __future__ import annotations

import collections
import json
import os
import threading
import time
import wave

import numpy as np

from . import settings as settings_mod

# Scenarios worth marking during a session. Order is the suggested protocol.
MARK_LABELS = [
    "quiet — standing still",
    "my own footsteps",
    "other player footsteps",
    "proximity chat",
    "gunfire",
    "vehicle / engine",
    "explosion",
    "other",
]


def _safe_name(s: str) -> str:
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in s]
    return "".join(keep).strip("_") or "session"


class SessionRecorder:
    """Buffered WAV writer fed from a capture thread."""

    def __init__(self, samplerate: int, channels: int, labels: list[str],
                 name: str = "session", meta: dict | None = None,
                 max_queue_s: float = 4.0):
        self.samplerate = int(samplerate)
        self.channels = int(channels)
        self.labels = list(labels)
        self.meta = dict(meta or {})
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = f"{stamp}-{_safe_name(name)}"
        self.dir = settings_mod.CAPTURE_DIR
        self.path = os.path.join(self.dir, base + ".wav")
        self.meta_path = os.path.join(self.dir, base + ".json")

        self._q: collections.deque[bytes] = collections.deque()
        self._q_frames = 0
        self._q_lock = threading.Lock()
        self._max_frames = int(max_queue_s * self.samplerate)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wav: wave.Wave_write | None = None
        self._t0 = 0.0
        self.frames_accepted = 0   # frames queued == position in the WAV
        self.frames_written = 0
        self.dropped_frames = 0
        self.marks: list[dict] = []
        self.error: str | None = None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        self._wav = wave.open(self.path, "wb")
        self._wav.setnchannels(self.channels)
        self._wav.setsampwidth(2)
        self._wav.setframerate(self.samplerate)
        self._t0 = time.perf_counter()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rec",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> str:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        if self._wav is not None:
            try:
                self._wav.close()
            finally:
                self._wav = None
        self._write_meta()
        return self.path

    @property
    def elapsed(self) -> float:
        """Position in the RECORDING, in seconds — derived from the audio
        accepted, not the wall clock. Marks have to point at the right place in
        the WAV, and a capture stall or a dropped block would make wall-clock
        time drift away from the file's own timeline."""
        return self.frames_accepted / float(max(self.samplerate, 1))

    @property
    def wall_elapsed(self) -> float:
        return 0.0 if self._t0 == 0.0 else time.perf_counter() - self._t0

    # -- called from the capture thread ----------------------------------
    def write(self, block: np.ndarray) -> None:
        """Queue a (frames, channels) float block. Never blocks on disk."""
        if self._wav is None or block.ndim != 2:
            return
        if block.shape[1] != self.channels:
            return                      # layout changed mid-session; skip
        pcm = np.clip(block, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16, copy=False)
        data = pcm.tobytes()
        with self._q_lock:
            if self._q_frames >= self._max_frames:
                self.dropped_frames += block.shape[0]
                return                  # writer fell behind: drop, don't stall
            self._q.append(data)
            self._q_frames += block.shape[0]
            self.frames_accepted += block.shape[0]

    def mark(self, label: str) -> float:
        t = self.elapsed
        self.marks.append({"t": round(t, 3), "label": label})
        return t

    # -- writer thread ---------------------------------------------------
    def _run(self) -> None:
        try:
            while True:
                chunk = None
                with self._q_lock:
                    if self._q:
                        chunk = self._q.popleft()
                        self._q_frames -= len(chunk) // (2 * self.channels)
                if chunk is None:
                    if self._stop.is_set():
                        return
                    self._stop.wait(0.01)
                    continue
                if self._wav is not None:
                    self._wav.writeframes(chunk)
                    self.frames_written += len(chunk) // (2 * self.channels)
        except Exception as e:            # noqa: BLE001 — never kill capture
            self.error = f"{type(e).__name__}: {e}"

    def _write_meta(self) -> None:
        info = {
            "wav": os.path.basename(self.path),
            "samplerate": self.samplerate,
            "channels": self.channels,
            "channel_labels": self.labels,
            "seconds": round(self.frames_written / max(self.samplerate, 1), 3),
            "dropped_frames": self.dropped_frames,
            "marks": self.marks,
        }
        info.update(self.meta)
        try:
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(info, f, indent=2)
        except OSError:
            pass
