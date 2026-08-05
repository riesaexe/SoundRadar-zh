"""User settings: persisted to %APPDATA%/SoundRadar/config.json and edited live
from the control panel. These are all the knobs we tuned, made adjustable."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields

_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                    "SoundRadar")
DATA_DIR = _DIR
CONFIG_PATH = os.path.join(_DIR, "config.json")
PRESETS_PATH = os.path.join(_DIR, "presets.json")
CAPTURE_DIR = os.path.join(_DIR, "captures")

# fields a preset captures (the per-game look/behaviour, not the audio setup)
PRESET_FIELDS = ["sensitivity", "adapt", "punch", "listen", "decay_ms", "size",
                 "gain", "segments", "thickness", "opacity", "color"]


@dataclass
class Settings:
    # analysis / behaviour (0-100 scales where noted)
    sensitivity: float = 50.0   # how far above its own background a sound must
                                # stand to register (high = quieter cues show)
    adapt: float = 40.0         # favour events over constant audio
    punch: float = 45.0         # 0-100, how much bigger loud sounds react than
                                # ordinary ones (0 = all events the same size)
    listen: str = "all"         # which frequencies to emphasise; see
                                # analysis.LISTEN_PROFILES. Stays NEUTRAL as a
                                # global default — per-game tuning belongs in a
                                # preset, not baked in for every game.
    gain: float = 2.2           # overall brightness
    decay_ms: float = 450.0     # how slowly a block fades
    # overlay look
    size: float = 45.0          # how big/dramatically blocks grow (0-100)
    segments: int = 9           # number of blocks around the border
    thickness: int = 29         # bar thickness against the edge (px)
    opacity: float = 0.85       # 0.3 = very see-through, 1 = solid
    color: str = "#FF00DC"      # bright/near colour (hex)
    monitor: int = 0            # which screen to draw on (0 = primary)
    # capture
    mode: str = "stereo"        # "stereo" (no setup) or "surround" (7.1 device)
    capture_device: str = ""    # surround: name of the 7.1 virtual device
    output_device: str = "Headphones"  # where the mono mix plays (surround)
    # audio output
    out_gain: float = 1.0       # volume of the mono mix to the headphones.
                                # 1.0 = about as loud as the source. Values
                                # above 1.0 are safe: the limiter protects the
                                # output instead of clipping it.


def load() -> Settings:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        known = {f.name for f in fields(Settings)}
        return Settings(**{k: v for k, v in data.items() if k in known})
    except (OSError, ValueError, TypeError):
        return Settings()


def save(s: Settings) -> None:
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(s), f, indent=2)
    except OSError:
        pass


def load_presets() -> dict:
    try:
        with open(PRESETS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_presets(presets: dict) -> None:
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(PRESETS_PATH, "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=2)
    except OSError:
        pass
