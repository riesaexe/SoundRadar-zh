"""SoundRadar — live audio -> glowing border overlay.

Recommended mode (clean, no audio changes): per-application capture via the
Windows Process Loopback API. SoundRadar reads the game's audio stream BEFORE
Windows mixes it to mono, so you keep "Mono audio" on and hear everything in
your good ear, while the radar still sees real left/right. Nothing in your
audio path is touched.

Run a game in BORDERLESS WINDOWED mode. The overlay is click-through, so there
is no window to close: stop with Ctrl+C in this console (or --seconds N).

Usage:
    python run.py --process stalker2          # capture the game by exe name
    python run.py --pid 12345                 # capture a specific process id
    python run.py --all-apps                  # capture everything (except self)
    python run.py --device "Headphones"       # legacy: device loopback (mono-collapsed if Win mono is on)
"""

from __future__ import annotations

import argparse
import math
import os
import signal
import sys
import time

from PySide6 import QtCore, QtGui, QtWidgets

from soundradar.audio import CaptureConfig, LoopbackCapture, list_loopback_devices
from soundradar.analysis import (AnalysisConfig, DirectionAnalyzer,
                                 DirectionEnvelopes, profile_weights)
from soundradar.overlay import OverlayWindow, OverlayStyle, CHANNEL_ANGLES
from soundradar.router import MonoRouter, RouterConfig
from soundradar.proc_loopback import ProcessLoopbackCapture, find_process_pids
from soundradar import settings as settings_mod
from soundradar.control_panel import SettingsWindow
from soundradar.recorder import SessionRecorder


def _sens_to_detect(sens):
    """0-100 -> (onset_sigma, knee_sigma).

    Sensitivity is how far a sound has to stand out from its band's normal
    fluctuation before the radar shows it. High = distant/quiet cues register;
    low = only obvious standouts do.
    """
    sv = max(0.0, min(100.0, sens)) / 100.0
    return 4.5 - sv * 3.3, 14.0 - sv * 8.0


def _punch_to_exponent(punch):
    """0-100 -> exponent on absolute loudness. 1.0 = every event the same size
    once detected; higher = loud sounds grow much bigger than ordinary ones."""
    return 1.0 + max(0.0, min(100.0, punch)) / 100.0 * 1.4


def _size_to_tick_gamma(size):
    sz = max(0.0, min(100.0, size)) / 100.0
    return 0.7 + sz * 2.0, 1.0 - sz * 0.5


def _colours(hex_str):
    near = QtGui.QColor(hex_str)
    if not near.isValid():
        near = QtGui.QColor("#FF00DC")
    far = QtGui.QColor((near.red() + 255) // 2, (near.green() + 255) // 2,
                       (near.blue() + 255) // 2)
    return near, far


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    # per-application capture (recommended; pre-mono, no audio changes)
    ap.add_argument("--process", default=None,
                    help="capture this app by exe name (e.g. stalker2)")
    ap.add_argument("--pid", type=int, default=None,
                    help="capture this exact process id")
    ap.add_argument("--all-apps", action="store_true",
                    help="capture all audio except SoundRadar itself")
    ap.add_argument("--channels", type=int, default=2,
                    help="channels to capture (2 stereo, 8 for 7.1 surround)")
    ap.add_argument("--device", default=None, help="loopback device name")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="auto-close after N seconds (0 = run until Ctrl+C)")
    # analysis
    ap.add_argument("--floor-db", type=float, default=-55.0)
    ap.add_argument("--ceil-db", type=float, default=-8.0)
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--attack-ms", type=float, default=25.0)
    ap.add_argument("--decay-ms", type=float, default=450.0)
    ap.add_argument("--sensitivity", type=float, default=50.0,
                    help="0-100. Higher = reacts to more/quieter sounds; "
                         "lower = only the loudest, most directional sounds.")
    ap.add_argument("--contrast", type=float, default=None,
                    help="advanced: override ambient suppression (0..1)")
    ap.add_argument("--adapt", type=float, default=60.0,
                    help="0-100. Favor sound CHANGES/events over constant audio "
                         "(stops a steady front bed from always dominating).")
    # overlay
    ap.add_argument("--segments", type=int, default=9,
                    help="number of compass-bearing blocks around the border")
    ap.add_argument("--depth", type=int, default=29,
                    help="fixed inward thickness of each block (px)")
    ap.add_argument("--size", type=float, default=70.0,
                    help="0-100. How big/dramatically blocks grow with loudness.")
    # audio routing (SoundRadar plays the full mono mix to your headphones)
    ap.add_argument("--route-audio", action="store_true",
                    help="play a full mono mix of all channels to --output")
    ap.add_argument("--output", default="Headphones",
                    help="physical output device for the mono mix")
    ap.add_argument("--out-gain", type=float, default=0.5)
    args = ap.parse_args()

    if args.list:
        print("Loopback devices:")
        for m in list_loopback_devices():
            print(f"  channels={m.channels:2}  {m.name}")
        return 0

    # all tunables come from the saved settings (edited live in the control
    # panel); CLI keeps the mode flags (--route-audio, --device, --process...).
    cfg = settings_mod.load()
    onset_sigma, knee_sigma = _sens_to_detect(cfg.sensitivity)
    # Loudness drives block SIZE only. "Brightness" is a separate overlay
    # multiplier (st.brightness) so the two stay independent.
    acfg = AnalysisConfig(attack_ms=args.attack_ms, decay_ms=cfg.decay_ms,
                          onset_sigma=onset_sigma, knee_sigma=knee_sigma,
                          punch=_punch_to_exponent(cfg.punch),
                          adapt=max(0.0, min(100.0, cfg.adapt)) / 100.0,
                          band_weights=profile_weights(cfg.listen))
    cli_capture = (args.all_apps or args.process or args.pid
                   or args.route_audio or args.device)
    if cli_capture:
        # explicit command-line capture (power users / debugging)
        if args.all_apps or args.process or args.pid:
            if args.all_apps:
                pid, include, desc = os.getpid(), False, "all apps"
            elif args.pid:
                pid, include, desc = args.pid, True, f"pid {args.pid}"
            else:
                pids = find_process_pids(args.process)
                if not pids:
                    print(f"no running process matching '{args.process}'")
                    return 1
                pid, include, desc = pids[0], True, args.process
            cap = ProcessLoopbackCapture(pid, channels=args.channels,
                                         include=include,
                                         play_mono=args.route_audio,
                                         output_name=args.output,
                                         out_gain=cfg.out_gain)
        elif args.route_audio:
            cap = MonoRouter(RouterConfig(source_name=args.device,
                                          output_name=args.output,
                                          out_gain=cfg.out_gain))
        else:
            cap = LoopbackCapture(CaptureConfig(device_name=args.device))
    elif cfg.mode == "surround" and cfg.capture_device:
        # surround: device-loopback a 7.1 device + play full mono mix
        cap = MonoRouter(RouterConfig(source_name=cfg.capture_device,
                                      output_name=cfg.output_device,
                                      out_gain=cfg.out_gain))
        print(f"surround: '{cfg.capture_device}' -> mono mix to "
              f"'{cfg.output_device}'")
    else:
        # stereo: capture all system audio (pre-mono), no audio changes
        cap = ProcessLoopbackCapture(os.getpid(), include=False, channels=2)
        print("stereo: all system audio (no audio changes)")
    cap.start()

    tick_fraction, gamma = _size_to_tick_gamma(cfg.size)
    near, far = _colours(cfg.color)
    app = QtWidgets.QApplication([])
    _screens = app.screens()
    _mon = max(0, min(int(cfg.monitor), len(_screens) - 1))
    overlay = OverlayWindow(OverlayStyle(segments=cfg.segments,
                                         depth=cfg.thickness,
                                         opacity=cfg.opacity,
                                         tick_fraction=tick_fraction,
                                         gamma=gamma,
                                         brightness=cfg.gain,
                                         near_color=near, far_color=far),
                            screen=_screens[_mon])
    overlay.show()

    # system-tray icon: a magenta dot near the clock. Right-click -> Pause/Quit
    # so there's no console window to hunt down.
    app.setQuitOnLastWindowClosed(False)
    _base = getattr(sys, "_MEIPASS",
                    os.path.dirname(os.path.abspath(__file__)))
    _ico = os.path.join(_base, "soundradar.ico")
    if os.path.exists(_ico):
        _app_icon = QtGui.QIcon(_ico)
    else:
        _pix = QtGui.QPixmap(32, 32)
        _pix.fill(QtCore.Qt.GlobalColor.transparent)
        _ip = QtGui.QPainter(_pix)
        _ip.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        _ip.setPen(QtCore.Qt.PenStyle.NoPen)
        _ip.setBrush(QtGui.QColor(255, 0, 220))
        _ip.drawEllipse(3, 3, 26, 26)
        _ip.end()
        _app_icon = QtGui.QIcon(_pix)
    app.setWindowIcon(_app_icon)
    tray = QtWidgets.QSystemTrayIcon(_app_icon)
    tray.setToolTip("SoundRadar")
    menu = QtWidgets.QMenu()
    act_pause = menu.addAction("Pause overlay")

    def _toggle_pause():
        if overlay.isVisible():
            overlay.hide()
            act_pause.setText("Resume overlay")
        else:
            overlay.show()
            act_pause.setText("Pause overlay")
    act_pause.triggered.connect(_toggle_pause)
    menu.addAction("Settings…").triggered.connect(lambda: open_settings())
    menu.addSeparator()
    menu.addAction("Quit SoundRadar").triggered.connect(app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: _toggle_pause()
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    tray.showMessage("SoundRadar", "Running. Right-click the tray dot to quit.",
                     QtWidgets.QSystemTrayIcon.MessageIcon.Information, 3000)

    env = DirectionEnvelopes(acfg)
    analyzer = DirectionAnalyzer(acfg)

    def apply_settings():
        """Push the (possibly just-changed) settings into the live radar."""
        acfg.onset_sigma, acfg.knee_sigma = _sens_to_detect(cfg.sensitivity)
        acfg.decay_ms = cfg.decay_ms
        acfg.punch = _punch_to_exponent(cfg.punch)
        acfg.adapt = max(0.0, min(100.0, cfg.adapt)) / 100.0
        acfg.band_weights = profile_weights(cfg.listen)
        st = overlay.style_
        st.tick_fraction, st.gamma = _size_to_tick_gamma(cfg.size)
        st.depth = cfg.thickness
        st.opacity = cfg.opacity
        st.brightness = cfg.gain
        st.near_color, st.far_color = _colours(cfg.color)
        if st.segments != cfg.segments:
            st.segments = cfg.segments
            overlay._rebuild_geometry()
        if hasattr(cap, "out_gain"):
            cap.out_gain = cfg.out_gain
        elif hasattr(cap, "cfg"):
            cap.cfg.out_gain = cfg.out_gain
        scrs = app.screens()
        idx = max(0, min(int(cfg.monitor), len(scrs) - 1))
        if idx != _state["mon"]:
            _state["mon"] = idx
            overlay.set_screen(scrs[idx])
        overlay.update()
        settings_mod.save(cfg)

    _state = {"mon": _mon}

    _win = {"w": None}

    # -- tuning capture ---------------------------------------------------
    # Records the raw audio the capture backend is seeing, so the detector can
    # be tuned offline against a real game session instead of assumptions.
    _rec = {"r": None}

    def _samplerate_of(c):
        return (getattr(c, "samplerate", None)
                or getattr(getattr(c, "cfg", None), "samplerate", None) or 48000)

    def start_record(name):
        if _rec["r"] is not None:
            return None
        lv = cap.get_levels()
        if not lv.channels:
            return None               # no audio arriving yet; nothing to record
        r = SessionRecorder(
            _samplerate_of(cap), lv.channels, lv.labels, name=name,
            meta={"mode": cfg.mode, "capture_device": cfg.capture_device,
                  "settings": {f: getattr(cfg, f)
                               for f in settings_mod.PRESET_FIELDS}})
        r.start()
        cap.recorder = r              # only now: the writer must be ready
        _rec["r"] = r
        return r

    def stop_record():
        r = _rec["r"]
        if r is None:
            return None
        cap.recorder = None           # detach before closing the file
        _rec["r"] = None
        return r.stop()

    def mark_record(label):
        r = _rec["r"]
        return None if r is None else r.mark(label)

    def record_status():
        r = _rec["r"]
        if r is None:
            return None
        return {"elapsed": r.elapsed, "marks": len(r.marks),
                "dropped": r.dropped_frames, "error": r.error}

    def open_settings():
        if _win["w"] is None:
            _win["w"] = SettingsWindow(cfg, apply_settings, on_test=start_test,
                                       get_levels=cap.get_levels,
                                       recording=(start_record, stop_record,
                                                  mark_record, record_status))
        w = _win["w"]
        w.show(); w.raise_(); w.activateWindow()

    last = {"t": time.perf_counter(), "ch": None}
    test = {"active": False, "t": 0.0}

    def start_test():
        test["t"] = 0.0
        test["active"] = True

    def test_tick():
        if not test["active"]:
            return
        test["t"] += 0.016
        if test["t"] > 6.0:           # one full lap then back to live audio
            test["active"] = False
            overlay.set_channel_intensities({})
            return
        ang = (test["t"] / 6.0) * 360.0   # sweep a sound around the compass
        vals = {}
        for lbl, a in CHANNEL_ANGLES.items():
            if lbl in ("L", "R"):
                continue
            d = abs(a - ang)
            d = min(d, 360.0 - d)
            vals[lbl] = max(0.0, 1.0 - d / 45.0)
        overlay.set_channel_intensities(vals)

    def tick():
        if test["active"]:
            return                    # test sweep drives the overlay
        now = time.perf_counter()
        dt = now - last["t"]
        last["t"] = now
        lv = cap.get_levels()
        if lv.channels and lv.channels != last["ch"]:
            last["ch"] = lv.channels
            print(f"capturing {lv.channels} channels: {lv.labels}")
        raw = analyzer.update(lv, dt)
        smoothed = env.update(raw, dt)
        overlay.set_channel_intensities(smoothed, smoothed.get("LFE", 0.0))

    timer = QtCore.QTimer()
    timer.timeout.connect(tick)
    timer.start(16)  # ~60 fps

    test_timer = QtCore.QTimer()
    test_timer.timeout.connect(test_tick)
    test_timer.start(16)

    # keep the overlay above the game (some games grab top-most)
    topmost = QtCore.QTimer()
    topmost.timeout.connect(overlay.keep_on_top)
    topmost.start(250)

    if args.seconds > 0:
        QtCore.QTimer.singleShot(int(args.seconds * 1000), app.quit)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    kick = QtCore.QTimer()
    kick.timeout.connect(lambda: None)
    kick.start(200)

    print("SoundRadar running. Ctrl+C to stop.")
    try:
        rc = app.exec()
    finally:
        stop_record()      # flush and close a capture left running on quit
        cap.stop()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
