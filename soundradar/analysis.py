"""Turn per-channel audio levels into directional loudness for the overlay.

The important idea: a sound is judged by **how far it stands above the quiet
background of its own frequency band**, not by its absolute level.

Absolute level alone fails badly in a game. Your own footsteps are loud (say
-20 dBFS) and saturate the scale, while another player's footsteps 30 m away
(-48 dBFS) land near zero on a fixed -55..-8 dB scale — and worse, they are
measured inside a channel total that is already dominated by engine, wind and
gunfire energy, so they barely move it at all. Both of those sounds matter
equally to the player; only their loudness differs.

So the pipeline is:

  1. BandSplitter (audio.py) gives per-channel RMS in a few perceptual bands.
  2. BandNoiseFloor tracks the quiet baseline of every (channel, band) pair —
     rising slowly, falling quickly, so it settles on the background and is not
     pulled up by the events we want to see.
  3. detect = how many dB the band sits ABOVE its own floor. This is what makes
     a distant footstep or a proximity voice register: relative, not absolute.
  4. level = absolute loudness, shaped by `punch`. This decides how BIG the
     block draws, so a grenade still dwarfs a footstep.
  5. value = detect * (quiet_floor + (1 - quiet_floor) * level), then the
     loudest band wins. A detected-but-quiet cue never falls below
     `quiet_floor` of full size, so it stays visible.

DirectionEnvelopes then smooths with fast attack / slow decay for readability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .audio import BAND_NAMES, Levels

# What to emphasise, as a weight per band of audio.BANDS:
#   low(20-120) thump(120-500) voice(500-2k) edge(2k-6k) air(6k-16k)
LISTEN_PROFILES: dict[str, tuple[str, tuple[float, ...]]] = {
    "steps": ("Footsteps & voices", (0.25, 1.0, 1.0, 1.0, 0.55)),
    "all":   ("Everything",         (1.0, 1.0, 1.0, 1.0, 1.0)),
    "boom":  ("Explosions & vehicles", (1.0, 0.9, 0.45, 0.3, 0.2)),
}
DEFAULT_PROFILE = "steps"


def profile_weights(key: str) -> tuple[float, ...]:
    return LISTEN_PROFILES.get(key, LISTEN_PROFILES[DEFAULT_PROFILE])[1]


@dataclass
class AnalysisConfig:
    # absolute scale -> how big a block grows (loudness, not detection)
    floor_db: float = -60.0   # at/below this an event draws its smallest
    ceil_db: float = -6.0     # at/above this an event draws full size
    punch: float = 1.0        # exponent on loudness; >1 = loud reacts bigger
    quiet_floor: float = 0.35  # size floor for a detected but quiet event
    silence_db: float = -75.0  # below this a band is silence, never an event
    # detection, in units of each band's own normal fluctuation (see
    # BandStatistics) rather than absolute dB — this is what lets a footstep
    # that is QUIETER than the ambient bed in its band still register.
    onset_sigma: float = 2.0  # excursion before anything registers
    knee_sigma: float = 7.0   # excursion that counts as a full event
    # How much of the ABSOLUTE level still lights a direction. The radar has to
    # show where sound IS, not only where it changes: with detection purely
    # differential, any sustained sound (an engine, ongoing gunfire, a vehicle
    # to your left) is folded into the background within a second and vanishes.
    # `adapt` scales this down to favour events, but never to zero.
    abs_weight: float = 1.0
    # dB above floor_db at which we are fully confident a direction has
    # something in it. Detection ("is it there") saturates over this small
    # range, while loudness ("how big") keeps using the full floor..ceil scale.
    # Using one ramp for both made every block dim, because a moderate sound
    # scored low on confidence AND low on size, and the two multiplied.
    gate_db: float = 12.0
    min_rise_db: float = 1.0  # and it must be at least this many dB up on the
                              # background — the sigma test alone eventually
                              # fires on the wobble of a very steady band, and
                              # this dB test alone misses anything masked, so
                              # a detection has to satisfy both
    adapt: float = 0.4        # 0..1, how fast the floor absorbs constant sound
    contrast: float = 0.7     # subtract this much of the all-channel average
    # smoothing
    attack_ms: float = 25.0
    decay_ms: float = 450.0
    # per-band emphasis (see LISTEN_PROFILES)
    band_weights: tuple[float, ...] = field(
        default_factory=lambda: profile_weights(DEFAULT_PROFILE))


def _coef(dt_s: float, tau_ms: float) -> float:
    if tau_ms <= 0:
        return 1.0
    return 1.0 - math.exp(-dt_s / (tau_ms / 1000.0))


def _adapt_to_rise_ms(adapt: float) -> float:
    """`adapt` 0..1 -> how fast the noise floor climbs.

    Fast (high adapt) means a constant sound is absorbed into the floor within
    a second and stops lighting the radar, leaving only changes/events. Slow
    means sustained sounds keep showing.
    """
    a = max(0.0, min(1.0, adapt))
    return 8000.0 - a * 7200.0        # 8 s .. 0.8 s


class BandStatistics:
    """Per-(channel, band) background level AND how much it normally wobbles.

    Two trackers per band:

      * `mu`  — the TYPICAL background level in dB, a slow symmetric average.
        It must be the typical level, not the quiet extreme: a strongly
        asymmetric slow-rise/fast-fall follower converges toward the *minimum*
        of a fluctuating band, which leaves `delta` permanently positive and
        makes `dev` learn that offset as "normal", destroying all
        discrimination. The averaging time comes from `adapt`, which is what
        gives that setting its meaning — a sustained sound is folded into the
        background over that time and stops lighting the radar.
      * `dev` — the band's typical frame-to-frame deviation from `mu`, in dB.

    The excursion is then reported in units of `dev`. That is the key to
    hearing what you cannot pick out by absolute level: a steady engine bed
    varies by a fraction of a dB, so an enemy footstep that raises its band by
    only ~1.5 dB — while still being QUIETER than the bed — is a large
    statistical excursion and registers strongly. A band that is naturally
    chaotic has a big `dev`, so its random peaks do not false-trigger.

    `dev` deliberately learns much more slowly from frames that are already
    excursions, otherwise every event would inflate the variability estimate
    and hide the next one.
    """

    def __init__(self, fall_ratio: float = 0.5, dev_ms: float = 1200.0,
                 dev_min: float = 0.35, dev_max: float = 8.0,
                 warmup_s: float = 0.7):
        # mu falls somewhat faster than it rises (so it recovers promptly after
        # a loud passage) but nothing like fast enough to chase the minimum.
        self.fall_ratio = fall_ratio
        self.dev_ms = dev_ms
        self.dev_min = dev_min
        self.dev_max = dev_max
        # `rise_ms` is a multi-second time constant, so a cold start would need
        # ~15 s before mu/dev described the audio at all — the radar would sit
        # dead or flash noise for that whole time, and after any capture
        # reconnect too. Converge fast for the first `warmup_s`, and report no
        # detections until then rather than detections built on junk stats.
        self.warmup_s = warmup_s
        self._age = 0.0
        self._mu: np.ndarray | None = None
        self._dev: np.ndarray | None = None

    def reset(self) -> None:
        self._mu = None
        self._dev = None
        self._age = 0.0

    def update(self, db: np.ndarray, dt_s: float,
               rise_ms: float) -> tuple[np.ndarray, np.ndarray]:
        """-> (excursion in units of dev, background level in dB)."""
        if self._mu is None or self._mu.shape != db.shape:
            self._mu = db.astype(np.float32).copy()
            self._dev = np.full(db.shape, self.dev_min, dtype=np.float32)
            self._age = 0.0
            return np.zeros(db.shape, dtype=np.float32), self._mu

        self._age += dt_s
        warming = self._age < self.warmup_s
        mu_tau = 150.0 if warming else rise_ms
        dev_tau = 250.0 if warming else self.dev_ms

        delta = db - self._mu
        up = _coef(dt_s, mu_tau)
        dn = _coef(dt_s, mu_tau * self.fall_ratio)
        self._mu = self._mu + delta * np.where(delta > 0.0, up, dn)

        # Learn the typical deviation from a broad view of recent frames. Events
        # are brief enough not to distort a slow average much, and gating them
        # out aggressively would freeze `dev` too low and cause the detector to
        # fire constantly on ordinary noise.
        absd = np.abs(delta)
        c = _coef(dt_s, dev_tau)
        self._dev += (absd - self._dev) * np.where(
            absd <= (self._dev * 5.0 + 1.0), c, c * 0.25)
        np.clip(self._dev, self.dev_min, self.dev_max, out=self._dev)

        if warming:
            return np.zeros(db.shape, dtype=np.float32), self._mu
        return delta / self._dev, self._mu


class DirectionAnalyzer:
    """Levels -> per-channel 0..1 directional intensity."""

    def __init__(self, cfg: AnalysisConfig):
        self.cfg = cfg
        self._stats = BandStatistics()
        self._wide = AdaptiveBaseline(amount=cfg.adapt)

    def update(self, levels: Levels, dt_s: float) -> dict[str, float]:
        if levels.channels == 0:
            return {}
        if levels.bands.size == 0 or levels.bands.shape[0] != levels.channels:
            return self._broadband(levels, dt_s)   # backend gave no band data

        cfg = self.cfg
        bands = np.asarray(levels.bands, dtype=np.float32)
        n_b = bands.shape[1]
        w = np.asarray(cfg.band_weights, dtype=np.float32)
        if w.size != n_b:                          # be tolerant of a mismatch
            w = np.resize(w, n_b)

        db = 20.0 * np.log10(np.maximum(bands, 1e-7))
        z, mu = self._stats.update(db, dt_s, _adapt_to_rise_ms(cfg.adapt))

        rng = max(cfg.ceil_db - cfg.floor_db, 1.0)
        level = np.clip((db - cfg.floor_db) / rng, 0.0, 1.0)

        # (a) differential: stands out from its OWN band's background. Finds
        # quiet cues that absolute level cannot, e.g. a distant footstep under
        # a louder broadband bed. Both gates must pass (see min_rise_db).
        span = max(cfg.knee_sigma - cfg.onset_sigma, 0.5)
        diff = np.clip((z - cfg.onset_sigma) / span, 0.0, 1.0)
        diff *= np.clip((db - mu) / max(cfg.min_rise_db, 0.05), 0.0, 1.0)

        # (b) absolute: simply loud enough to matter, however long it lasts.
        absolute = np.clip((db - cfg.floor_db) / max(cfg.gate_db, 1.0), 0.0, 1.0)

        # A sound present equally in every channel carries no direction, so
        # subtract the cross-channel average per band. LFE is non-directional
        # and stays out of that average.
        dirs = [i for i, l in enumerate(levels.labels[:levels.channels])
                if l != "LFE"]
        if cfg.contrast > 0.0 and len(dirs) > 1:
            for part in (diff, absolute):
                base = part[dirs].mean(axis=0) * cfg.contrast
                part[dirs] = np.clip(part[dirs] - base, 0.0, 1.0)

        # whichever route finds it — loud, or quiet but distinct. `adapt` biases
        # away from the absolute route (favouring events) after the directional
        # subtraction, so it trades the two off without dimming everything.
        detect = np.maximum(diff, absolute * cfg.abs_weight)
        detect[db < cfg.silence_db] = 0.0     # never chase dither/near-silence

        level = level ** cfg.punch
        vis = cfg.quiet_floor + (1.0 - cfg.quiet_floor) * level

        val = (detect * vis) * w                   # (channels, bands)
        best = val.max(axis=1)
        return {lbl: float(best[i])
                for i, lbl in enumerate(levels.labels[:levels.channels])}

    # -- fallback: no band data available --------------------------------
    def _broadband(self, levels: Levels, dt_s: float) -> dict[str, float]:
        self._wide.amount = self.cfg.adapt
        raw = channels_to_directions(levels, self.cfg)
        return self._wide.apply(raw, dt_s)


def channels_to_directions(levels: Levels,
                           cfg: AnalysisConfig) -> dict[str, float]:
    """Broadband 0..1 loudness per channel label — the pre-band behaviour,
    kept as a fallback for a capture backend that publishes RMS only."""
    out: dict[str, float] = {}
    if levels.channels == 0 or levels.rms.size == 0:
        return out
    rng = max(cfg.ceil_db - cfg.floor_db, 1.0)
    for label, rms in zip(levels.labels, levels.rms):
        db = 20.0 * math.log10(max(float(rms), 1e-6))
        out[label] = max(0.0, min(1.0, (db - cfg.floor_db) / rng))

    if cfg.contrast > 0.0:
        dirs = [l for l in out if l != "LFE"]
        if dirs:
            base = (sum(out[l] for l in dirs) / len(dirs)) * cfg.contrast
            for l in dirs:
                out[l] = max(0.0, out[l] - base)
    return out


class AdaptiveBaseline:
    """Subtract a slow per-direction running average so a constantly-loud
    channel settles down and only changes stand out. amount 0 = off."""

    def __init__(self, amount: float = 0.6, tau_ms: float = 1500.0):
        self.amount = amount
        self.tau_ms = tau_ms
        self._avg: dict[str, float] = {}

    def apply(self, levels: dict[str, float], dt_s: float) -> dict[str, float]:
        if self.amount <= 0.0:
            return levels
        coef = _coef(dt_s, self.tau_ms)
        out = {}
        for k, v in levels.items():
            a = self._avg.get(k, 0.0)
            a += (v - a) * coef
            self._avg[k] = a
            out[k] = max(0.0, v - self.amount * a)
        return out


class DirectionEnvelopes:
    """Fast-attack / slow-decay smoothing over an arbitrary label set."""

    def __init__(self, cfg: AnalysisConfig):
        self.cfg = cfg
        self._val: dict[str, float] = {}

    def update(self, target: dict[str, float], dt_s: float) -> dict[str, float]:
        a = _coef(dt_s, self.cfg.attack_ms)
        d = _coef(dt_s, self.cfg.decay_ms)
        for k in set(self._val) | set(target):
            cur = self._val.get(k, 0.0)
            tgt = target.get(k, 0.0)
            coef = a if tgt > cur else d
            self._val[k] = cur + (tgt - cur) * coef
        return dict(self._val)


__all__ = ["AnalysisConfig", "DirectionAnalyzer", "DirectionEnvelopes",
           "AdaptiveBaseline", "channels_to_directions", "BandStatistics",
           "LISTEN_PROFILES", "DEFAULT_PROFILE", "profile_weights",
           "BAND_NAMES"]
