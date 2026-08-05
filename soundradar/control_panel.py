"""Settings control panel — modern dark, tabbed, compact, with presets.

Opened from the tray icon. Each change updates the Settings object, calls the
apply callback (updates the running radar live) and saves to disk.
"""

from __future__ import annotations

import os

from PySide6 import QtCore, QtGui, QtWidgets

from . import settings as settings_mod
from .settings import PRESET_FIELDS, Settings
from .analysis import LISTEN_PROFILES
from .audio import list_loopback_devices, list_output_devices, rms_to_dbfs

ACCENT = "#4ECDC4"        # refined muted teal
ACCENT_SOFT = "rgba(78, 205, 196, 0.12)"

STYLE = f"""
* {{ font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 13px; }}
QWidget {{ background: #101116; color: #e9ebf1; }}
QTabWidget::pane {{ border: 1px solid #20232c; border-radius: 12px; top: -1px;
                    background: #14161c; }}
QTabBar::tab {{ background: transparent; color: #757b87; padding: 9px 20px;
                border: none; margin-right: 2px; letter-spacing: 0.3px; }}
QTabBar::tab:selected {{ color: #ffffff; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: #c2c6d0; }}
QGroupBox {{ border: 1px solid #20232c; border-radius: 12px; margin-top: 16px;
             padding: 18px 16px 8px 16px; background: #181a21; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px;
                    color: #8b93a0; font-weight: 600; text-transform: uppercase;
                    letter-spacing: 0.6px; font-size: 11px; }}
QLabel#hint {{ color: #757b87; }}
QSlider::groove:horizontal {{ height: 4px; background: #262a34; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #eef1f6; width: 14px; height: 14px;
                              margin: -6px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}
QPushButton {{ background: #1d2029; border: 1px solid #2a2e39; border-radius: 8px;
               padding: 8px 12px; color: #e9ebf1; }}
QPushButton:hover {{ background: #242833; border-color: #363b48; }}
QPushButton#accent {{ background: transparent; border: 1px solid {ACCENT};
                      color: {ACCENT}; font-weight: 600; padding: 10px;
                      letter-spacing: 0.4px; }}
QPushButton#accent:hover {{ background: {ACCENT_SOFT}; }}
QComboBox {{ background: #1d2029; border: 1px solid #2a2e39; border-radius: 8px;
             padding: 6px 10px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #1d2029; border: 1px solid #2a2e39;
                               selection-background-color: {ACCENT};
                               selection-color: #101116; outline: none; }}
QFrame#card {{ background: #181a21; border: 1px solid #20232c; border-radius: 12px; }}
QLabel#infotitle {{ color: #ffffff; font-weight: 600; }}
QToolTip {{ background: #1d2029; color: #e9ebf1; border: 1px solid #2a2e39;
            padding: 4px 6px; }}
QInputDialog, QMessageBox {{ background: #14161c; }}
QProgressBar {{ background: #1d2029; border: 1px solid #2a2e39; border-radius: 5px;
                height: 12px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QLineEdit {{ background: #1d2029; border: 1px solid #2a2e39; border-radius: 8px;
             padding: 6px 10px; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: #2a2e39; border-radius: 5px; }}
QScrollBar::handle:vertical {{ min-height: 28px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; }}
QScrollBar::handle:hover {{ background: #363b48; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""


class SettingsWindow(QtWidgets.QWidget):
    def __init__(self, settings: Settings, on_change, on_test=None,
                 get_levels=None, recording=None):
        super().__init__(None)
        self.s = settings
        self.on_change = on_change
        self.on_test = on_test
        self._get_levels = get_levels      # () -> Levels, for the Check tab
        # (start, stop, mark, status) callbacks; None hides the Tune tab
        self._rec = recording
        self._diag_n = 0                   # channel count the bars are built for
        self._diag_bars = {}               # index -> (QProgressBar, value label)
        self._rows = []          # (field, slider, disp_fn, value_label)
        self._presets = settings_mod.load_presets()
        self.setWindowTitle("SoundRadar")
        # A normal resizable window: min/max/close, and free to be maximised or
        # dragged to any size. (A fixed width used to disable maximise, and an
        # unbounded height meant the panel opened taller than the desktop work
        # area on scaled laptop displays and clipped its own footer.)
        self.setWindowFlags(QtCore.Qt.WindowType.Window
                            | QtCore.Qt.WindowType.WindowMinimizeButtonHint
                            | QtCore.Qt.WindowType.WindowMaximizeButtonHint
                            | QtCore.Qt.WindowType.WindowCloseButtonHint)
        self.setStyleSheet(STYLE)
        # Height can go as small as the user likes (tabs scroll). Width gets a
        # content-derived floor at the end of __init__ so no row is ever cut off.
        self.setMinimumHeight(240)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        title = QtWidgets.QLabel("SoundRadar")
        tf = title.font(); tf.setPointSize(15); tf.setBold(True); title.setFont(tf)
        root.addWidget(title)

        root.addLayout(self._preset_bar())

        if on_test is not None:
            tb = QtWidgets.QPushButton("◎   Test radar")
            tb.setObjectName("accent")
            tb.setToolTip("Sweep a sound around the ring for ~6s — see and tune "
                          "the radar without a game.")
            tb.clicked.connect(lambda: self.on_test())
            root.addWidget(tb)

        # Every tab scrolls: the window can then be shrunk to any height (or
        # opened on a short screen) without ever clipping its content.
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._scrollable(self._radar_tab()), "Radar")
        tabs.addTab(self._scrollable(self._setup_tab()), "Setup")
        tabs.addTab(self._scrollable(self._diag_tab()), "Check")
        if self._rec is not None:
            tabs.addTab(self._scrollable(self._tune_tab()), "Tune")
        root.addWidget(tabs, 1)

        foot = QtWidgets.QLabel("Changes apply live and save automatically.")
        foot.setObjectName("hint")
        root.addWidget(foot)

        # poll the live capture for the Check tab (cheap; only paints when shown)
        self._diag_timer = QtCore.QTimer(self)
        self._diag_timer.timeout.connect(self._update_diag)
        self._diag_timer.start(120)

        # A long device name ("Voicemeeter VAIO3 Input (VB-Audio…)") must not
        # dictate how wide the panel has to be. An explicit minimum width is the
        # one thing that reliably overrides QComboBox's content-derived
        # minimumSizeHint (the size-adjust policy alone is ignored once a
        # stylesheet is in play), and Expanding lets them use whatever width the
        # window does have. The full name stays available in the dropdown and as
        # a tooltip, so nothing is actually lost when the panel is narrow.
        for cb in self.findChildren(QtWidgets.QComboBox):
            cb.view().setMinimumWidth(cb.minimumSizeHint().width())
            cb.setMinimumWidth(110)
            cb.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                             cb.sizePolicy().verticalPolicy())
            if not cb.toolTip():   # don't clobber an explanatory tooltip
                cb.setToolTip(cb.currentText())
                cb.currentTextChanged.connect(
                    lambda t, c=cb: c.setToolTip(t) if t else None)

        self.setMinimumWidth(400)
        self.resize(460, self.sizeHint().height())
        self._fit_to_screen()

    # -- window sizing ---------------------------------------------------
    def _fit_to_screen(self):
        """Clamp to the desktop work area and nudge fully on-screen.

        Guards two cases: the panel's natural height exceeding a short (or
        display-scaled) screen, and being reopened after the resolution changed
        or on a different monitor.
        """
        scr = self.screen() or QtWidgets.QApplication.primaryScreen()
        if scr is None:
            return
        avail = scr.availableGeometry()
        w = min(self.width(), avail.width() - 24)
        h = min(self.height(), avail.height() - 24)
        if (w, h) != (self.width(), self.height()):
            self.resize(max(self.minimumWidth(), w),
                        max(self.minimumHeight(), h))
        g = self.frameGeometry()
        if not avail.contains(g):
            g.moveLeft(max(avail.left(), min(g.left(), avail.right() - g.width())))
            g.moveTop(max(avail.top(), min(g.top(), avail.bottom() - g.height())))
            self.move(g.topLeft())

    def showEvent(self, e: QtGui.QShowEvent) -> None:
        super().showEvent(e)
        if not self.isMaximized():
            self._fit_to_screen()

    # -- presets ---------------------------------------------------------
    def _preset_bar(self):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Preset"))
        self._preset_combo = QtWidgets.QComboBox()
        self._reload_preset_combo()
        self._preset_combo.activated.connect(self._on_preset_pick)
        row.addWidget(self._preset_combo, 1)
        save = QtWidgets.QPushButton("Save…")
        save.clicked.connect(self._save_preset)
        row.addWidget(save)
        dele = QtWidgets.QPushButton("Delete")
        dele.clicked.connect(self._delete_preset)
        row.addWidget(dele)
        return row

    def _reload_preset_combo(self):
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("— choose preset —")
        for name in sorted(self._presets):
            self._preset_combo.addItem(name)
        self._preset_combo.setCurrentIndex(0)
        self._preset_combo.blockSignals(False)

    def _on_preset_pick(self, idx):
        if idx <= 0:
            return
        name = self._preset_combo.itemText(idx)
        data = self._presets.get(name, {})
        for k, v in data.items():
            setattr(self.s, k, v)
        self.on_change()
        self._refresh()

    def _save_preset(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Save preset",
                                                  "Preset name:")
        name = name.strip()
        if not ok or not name:
            return
        self._presets[name] = {f: getattr(self.s, f) for f in PRESET_FIELDS}
        settings_mod.save_presets(self._presets)
        self._reload_preset_combo()
        self._preset_combo.setCurrentText(name)

    def _delete_preset(self):
        name = self._preset_combo.currentText()
        if name in self._presets:
            del self._presets[name]
            settings_mod.save_presets(self._presets)
            self._reload_preset_combo()

    # -- tabs ------------------------------------------------------------
    def _radar_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(12, 8, 12, 12); v.setSpacing(12)

        beh = self._card("Behaviour"); g = beh.layout()
        self._row(g, 0, "Sensitivity", "sensitivity", 0, 100,
                  tip="How far above its own background a sound must stand to "
                      "show. Higher picks up distant footsteps and quiet "
                      "voices; lower shows only obvious sounds.")
        self._row(g, 1, "Adapt", "adapt", 0, 100,
                  tip="How quickly constant audio (engine, wind, music) is "
                      "treated as background and stops lighting the radar.")
        self._row(g, 2, "Punch", "punch", 0, 100,
                  tip="How much bigger loud sounds react than ordinary ones. "
                      "0 = every detected sound draws the same size; higher = "
                      "gunfire and explosions dwarf footsteps.")
        self._row(g, 3, "Fade", "decay_ms", 100, 900,
                  tip="How slowly a block fades once the sound stops.")
        g.addWidget(QtWidgets.QLabel("Listen for"), 4, 0)
        self._listen = QtWidgets.QComboBox()
        for key, (label, _w) in LISTEN_PROFILES.items():
            self._listen.addItem(label, key)
        idx = self._listen.findData(self.s.listen)
        self._listen.setCurrentIndex(idx if idx >= 0 else 0)
        self._listen.setToolTip(
            "Which frequencies matter. 'Footsteps & voices' de-emphasises "
            "rumble so a quiet footstep or proximity chat isn't buried under "
            "engine and explosion noise.")
        self._listen.currentIndexChanged.connect(
            lambda i: self._set("listen", self._listen.itemData(i)))
        g.addWidget(self._listen, 4, 1, 1, 2)
        v.addWidget(beh)

        app = self._card("Appearance"); g = app.layout()
        self._row(g, 0, "Size", "size", 0, 100)
        self._row(g, 1, "Brightness", "gain", 50, 400, mul=100)
        self._row(g, 2, "Blocks", "segments", 6, 30, integer=True)
        self._row(g, 3, "Thickness", "thickness", 8, 70, integer=True)
        self._row(g, 4, "Opacity", "opacity", 25, 100, mul=100)
        g.addWidget(QtWidgets.QLabel("Colour"), 5, 0)
        self._sw = QtWidgets.QPushButton(); self._sw.setFixedSize(54, 22)
        self._sw.clicked.connect(self._pick_colour); self._paint_swatch()
        g.addWidget(self._sw, 5, 1, QtCore.Qt.AlignmentFlag.AlignLeft)
        v.addWidget(app)
        v.addStretch(1)
        return w

    def _setup_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(12, 8, 12, 12); v.setSpacing(12)

        cap = self._card("Capture"); g = cap.layout()
        g.addWidget(QtWidgets.QLabel("Mode"), 0, 0)
        self._mode = QtWidgets.QComboBox()
        self._mode.addItem("Stereo — no setup, left/right", "stereo")
        self._mode.addItem("Surround — 7.1 device, front/back", "surround")
        self._mode.setCurrentIndex(1 if self.s.mode == "surround" else 0)
        self._mode.currentIndexChanged.connect(self._on_mode)
        g.addWidget(self._mode, 0, 1, 1, 2)
        g.addWidget(QtWidgets.QLabel("Device"), 1, 0)
        self._dev = QtWidgets.QComboBox()
        names = [m.name for m in list_loopback_devices()]
        self._dev.addItems(names or ["(no loopback devices found)"])
        if self.s.capture_device in names:
            self._dev.setCurrentText(self.s.capture_device)
        self._dev.currentTextChanged.connect(
            lambda t: self._set("capture_device", t))
        self._dev.setEnabled(self.s.mode == "surround")
        g.addWidget(self._dev, 1, 1, 1, 2)
        note = QtWidgets.QLabel("Restart SoundRadar (tray ▸ Quit, reopen) to "
                                "apply capture changes.")
        note.setObjectName("hint"); note.setWordWrap(True)
        g.addWidget(note, 2, 0, 1, 3)
        v.addWidget(cap)

        disp = self._card("Display & audio"); g = disp.layout()
        g.addWidget(QtWidgets.QLabel("Monitor"), 0, 0)
        screens = QtWidgets.QApplication.instance().screens()
        combo = QtWidgets.QComboBox()
        for i, sc in enumerate(screens):
            geo = sc.geometry()
            star = " •" if sc == QtWidgets.QApplication.primaryScreen() else ""
            combo.addItem(f"{i + 1}: {geo.width()}×{geo.height()}{star}", i)
        combo.setCurrentIndex(max(0, min(self.s.monitor, len(screens) - 1)))
        combo.currentIndexChanged.connect(
            lambda idx: self._set("monitor", combo.itemData(idx)))
        g.addWidget(combo, 0, 1, 1, 2)

        g.addWidget(QtWidgets.QLabel("Output"), 1, 0)
        self._out = QtWidgets.QComboBox()
        try:
            out_names = [s.name for s in list_output_devices()]
        except Exception:       # noqa: BLE001 — never block the panel on audio
            out_names = []
        self._out.addItems(out_names or ["Headphones"])
        # match the saved value even if only a substring is stored (e.g. "Headphones")
        cur = next((nm for nm in out_names if self.s.output_device
                    and self.s.output_device.lower() in nm.lower()), None)
        if cur:
            self._out.setCurrentText(cur)
        elif self.s.output_device:
            self._out.insertItem(0, self.s.output_device)
            self._out.setCurrentIndex(0)
        self._out.setToolTip("Surround mode plays the mono mix here — pick your "
                             "headset. (Stereo mode ignores this.)")
        self._out.currentTextChanged.connect(
            lambda t: self._set("output_device", t))
        g.addWidget(self._out, 1, 1, 1, 2)

        self._row(g, 2, "Volume", "out_gain", 0, 200, mul=100,
                  tip="Loudness of the mono mix sent to your headphones. 100 = "
                      "about as loud as the game itself. Above 100 is safe — a "
                      "limiter holds the peaks instead of distorting them.")
        outnote = QtWidgets.QLabel("Restart SoundRadar to apply an output change.")
        outnote.setObjectName("hint"); outnote.setWordWrap(True)
        g.addWidget(outnote, 3, 0, 1, 3)
        v.addWidget(disp)

        card = QtWidgets.QFrame(); card.setObjectName("card")
        cl = QtWidgets.QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12); cl.setSpacing(8)
        t = QtWidgets.QLabel("Keep an app off the radar")
        t.setObjectName("infotitle"); cl.addWidget(t)
        body = QtWidgets.QLabel(
            "The radar shows whatever is sent to your capture device. To stop an "
            "app (e.g. voice chat) showing up, set its output to your headphones "
            "in Windows Volume mixer — you'll still hear it, it just won't appear.")
        body.setObjectName("hint"); body.setWordWrap(True); cl.addWidget(body)
        mix = QtWidgets.QPushButton("Open Windows Volume mixer")
        mix.clicked.connect(self._open_mixer); cl.addWidget(mix)
        v.addWidget(card)
        v.addStretch(1)
        return w

    def _diag_tab(self):
        """Live capture check — per-channel levels + a surround/mono verdict.
        This is the diag.py test built into the app."""
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(12, 8, 12, 12); v.setSpacing(12)

        card = QtWidgets.QGroupBox("Live capture")
        cv = QtWidgets.QVBoxLayout(card)
        cv.setContentsMargins(16, 18, 16, 12); cv.setSpacing(10)

        self._diag_verdict = QtWidgets.QLabel("Waiting for audio…")
        vf = self._diag_verdict.font(); vf.setBold(True); vf.setPointSize(13)
        self._diag_verdict.setFont(vf)
        cv.addWidget(self._diag_verdict)

        # shown only when the verdict needs explaining (e.g. stereo-in-7.1)
        self._diag_detail = QtWidgets.QLabel("")
        self._diag_detail.setObjectName("hint")
        self._diag_detail.setWordWrap(True)
        self._diag_detail.setVisible(False)
        cv.addWidget(self._diag_detail)

        sub = QtWidgets.QLabel(
            "Play a sound with a clear direction. For real surround the bars "
            "should differ. If every bar moves together, it's being collapsed "
            "to mono.")
        sub.setObjectName("hint"); sub.setWordWrap(True)
        cv.addWidget(sub)

        self._diag_host = QtWidgets.QWidget()
        self._diag_host_v = QtWidgets.QVBoxLayout(self._diag_host)
        self._diag_host_v.setContentsMargins(0, 4, 0, 0)
        self._diag_host_v.setSpacing(6)
        cv.addWidget(self._diag_host)
        v.addWidget(card)

        tip = QtWidgets.QLabel(
            "All bars equal? Turn Windows “Mono audio” OFF and set the game to "
            "7.1. Bars at the floor (no movement) = nothing is reaching the "
            "capture device.")
        tip.setObjectName("hint"); tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch(1)
        return w

    def _rebuild_diag_rows(self, n, labels):
        while self._diag_host_v.count():
            item = self._diag_host_v.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._diag_bars = {}
        for i in range(n):
            row = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
            lab = QtWidgets.QLabel(labels[i] if i < len(labels) else f"ch{i}")
            lab.setFixedWidth(32)
            bar = QtWidgets.QProgressBar()
            bar.setMinimum(0); bar.setMaximum(100); bar.setTextVisible(False)
            bar.setFixedHeight(12)
            val = QtWidgets.QLabel("—"); val.setObjectName("hint")
            val.setFixedWidth(46)
            val.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight
                             | QtCore.Qt.AlignmentFlag.AlignVCenter)
            h.addWidget(lab); h.addWidget(bar, 1); h.addWidget(val)
            self._diag_host_v.addWidget(row)
            self._diag_bars[i] = (bar, val)

    def _update_diag(self):
        if self._get_levels is None or not self.isVisible():
            return
        lv = self._get_levels()
        n = int(getattr(lv, "channels", 0))
        if n <= 0 or lv.rms.size == 0:
            self._diag_verdict.setText("● No audio yet")
            self._diag_verdict.setStyleSheet("color:#757b87;")
            return
        if n != self._diag_n:
            self._rebuild_diag_rows(n, lv.labels)
            self._diag_n = n
        db = rms_to_dbfs(lv.rms)
        allvals, any_loud = [], False
        for i in range(n):
            d = float(db[i]) if i < db.size else -120.0
            allvals.append(d)
            bar, val = self._diag_bars[i]
            bar.setValue(int(max(0.0, min(100.0, (d + 60.0) / 60.0 * 100.0))))
            val.setText("—" if d <= -119.0 else f"{d:.0f} dB")
            any_loud = any_loud or d > -55.0
        # Three distinct failures, and the spread test alone cannot tell them
        # apart. A stereo app playing into a 7.1 device leaves the surround
        # channels at DIGITAL ZERO, which produces a huge spread and used to be
        # reported as "direction detected" — exactly backwards, and it hides the
        # one problem the user actually has to go and fix.
        spread = (max(allvals) - min(allvals)) if allvals else 0.0
        live = [i for i in range(n) if allvals[i] > -80.0]
        if not any_loud:
            self._diag_verdict.setText("● Silence — nothing on this device")
            self._diag_verdict.setStyleSheet("color:#757b87;")
        elif n >= 6 and len(live) <= 2:
            names = ", ".join(lv.labels[i] for i in live) or "none"
            self._diag_verdict.setText(
                f"● Stereo only ({names}) — no front/back")
            self._diag_verdict.setStyleSheet("color:#e0a030;")
            self._diag_detail.setText(
                f"Audio is reaching this {n}-channel device, but only {names} "
                "carry any signal — the surround channels are silent. The radar "
                "can only show left/right like this. The app you're listening to "
                "is playing in stereo: set the GAME's audio output to 7.1 "
                "surround (and its Windows output device to this one).")
            self._diag_detail.setVisible(True)
            return
        elif spread > 6.0:
            self._diag_verdict.setText("● Direction detected — radar will work")
            self._diag_verdict.setStyleSheet(f"color:{ACCENT};")
        else:
            self._diag_verdict.setText("● Mono / uniform — collapsed, no direction")
            self._diag_verdict.setStyleSheet("color:#e0a030;")
        self._diag_detail.setVisible(False)

    # -- tune tab --------------------------------------------------------
    def _tune_tab(self):
        """Record a real session so the detector can be tuned against actual
        game audio rather than assumptions about how a game is mixed."""
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(12, 8, 12, 12); v.setSpacing(12)

        card = self._card("Record a sample"); g = card.layout()
        intro = QtWidgets.QLabel(
            "Optional. Records a few minutes of the audio SoundRadar is "
            "capturing, so the radar's defaults can be checked against how "
            "this game actually sounds instead of guesswork.\n\n"
            "There is nothing to set up: start it, play normally, stop it. "
            "You don't need to stage anything or press anything while playing.")
        intro.setObjectName("hint"); intro.setWordWrap(True)
        g.addWidget(intro, 0, 0, 1, 3)

        g.addWidget(QtWidgets.QLabel("Name"), 1, 0)
        self._rec_name = QtWidgets.QLineEdit()
        self._rec_name.setPlaceholderText("e.g. arma-reforger")
        g.addWidget(self._rec_name, 1, 1, 1, 2)

        self._rec_btn = QtWidgets.QPushButton("●   Start recording")
        self._rec_btn.setObjectName("accent")
        self._rec_btn.clicked.connect(self._toggle_record)
        g.addWidget(self._rec_btn, 2, 0, 1, 3)

        self._rec_state = QtWidgets.QLabel("Not recording.")
        self._rec_state.setObjectName("hint"); self._rec_state.setWordWrap(True)
        g.addWidget(self._rec_state, 3, 0, 1, 3)

        self._mark_btn = QtWidgets.QPushButton("Mark this moment (optional)")
        self._mark_btn.setToolTip("Only if you happen to be at the panel — a "
                                 "marked moment is easier to find in the "
                                 "recording. Never required.")
        self._mark_btn.clicked.connect(self._do_mark)
        self._mark_btn.setEnabled(False)
        g.addWidget(self._mark_btn, 4, 0, 1, 3)
        v.addWidget(card)

        howto = QtWidgets.QFrame(); howto.setObjectName("card")
        hl = QtWidgets.QVBoxLayout(howto)
        hl.setContentsMargins(14, 12, 14, 12); hl.setSpacing(8)
        openf = QtWidgets.QPushButton("Open recordings folder")
        openf.clicked.connect(self._open_captures); hl.addWidget(openf)
        note = QtWidgets.QLabel(
            "Saved locally to %APPDATA%\\SoundRadar\\captures — about 45 MB "
            "per minute. Note this records the game audio, including any voice "
            "chat that is playing.")
        note.setObjectName("hint"); note.setWordWrap(True); hl.addWidget(note)
        v.addWidget(howto)
        v.addStretch(1)

        self._rec_timer = QtCore.QTimer(self)
        self._rec_timer.timeout.connect(self._update_record_state)
        self._rec_timer.start(500)
        return w

    def _toggle_record(self):
        start, stop, _mark, status = self._rec
        if status() is not None:
            path = stop()
            self._rec_btn.setText("●   Start recording")
            self._mark_btn.setEnabled(False)
            self._rec_state.setText(f"Saved: {path}" if path
                                    else "Nothing was recorded.")
            return
        name = self._rec_name.text().strip() or "session"
        if start(name) is None:
            self._rec_state.setText(
                "No audio arriving yet — start the game (or check the Check "
                "tab) and try again.")
            return
        self._rec_btn.setText("■   Stop recording")
        self._mark_btn.setEnabled(True)

    def _do_mark(self):
        _start, _stop, mark, _status = self._rec
        t = mark("interesting")
        if t is not None:
            self._rec_state.setText(f"Marked {t:.1f}s into the recording")

    def _update_record_state(self):
        if self._rec is None or not self.isVisible():
            return
        st = self._rec[3]()
        if st is None:
            return
        msg = f"Recording — {st['elapsed']:.0f}s, {st['marks']} mark(s)"
        if st["dropped"]:
            msg += f"  (dropped {st['dropped']} frames)"
        if st["error"]:
            msg += f"  ERROR: {st['error']}"
        self._rec_state.setText(msg)

    def _open_captures(self):
        os.makedirs(settings_mod.CAPTURE_DIR, exist_ok=True)
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(settings_mod.CAPTURE_DIR))

    # -- helpers ---------------------------------------------------------
    def _scrollable(self, inner):
        """Wrap a tab page so it scrolls vertically instead of being clipped."""
        sa = QtWidgets.QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        # AsNeeded, not AlwaysOff: the window's minimum width is derived from the
        # content below, so this bar should never appear — but if a big system
        # font or scaling factor makes a row wider than expected, content stays
        # reachable by scrolling instead of being cut off past the right edge.
        sa.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sa.setWidget(inner)
        return sa

    def _card(self, title):
        box = QtWidgets.QGroupBox(title)
        grid = QtWidgets.QGridLayout(box)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(2, 34)
        grid.setHorizontalSpacing(12); grid.setVerticalSpacing(10)
        return box

    def _row(self, grid, r, label, field, lo, hi, mul=1, integer=False,
             tip=None):
        def disp(val):
            return int(round(val * mul))

        def parse(x):
            v = x / mul
            return int(v) if integer else v

        name = QtWidgets.QLabel(label)
        sld = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        if tip:
            name.setToolTip(tip)
            sld.setToolTip(tip)
        sld.setMinimum(int(lo)); sld.setMaximum(int(hi))
        sld.setValue(disp(getattr(self.s, field)))
        val = QtWidgets.QLabel(str(sld.value())); val.setObjectName("hint")
        val.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight
                         | QtCore.Qt.AlignmentFlag.AlignVCenter)

        def changed(x):
            val.setText(str(x))
            self._set(field, parse(x))
        sld.valueChanged.connect(changed)
        self._rows.append((field, sld, disp, val))
        grid.addWidget(name, r, 0); grid.addWidget(sld, r, 1)
        grid.addWidget(val, r, 2)

    def _refresh(self):
        """Push current settings back into the widgets (after loading a preset)."""
        for field, sld, disp, val in self._rows:
            sld.blockSignals(True)
            sld.setValue(disp(getattr(self.s, field)))
            sld.blockSignals(False)
            val.setText(str(sld.value()))
        idx = self._listen.findData(self.s.listen)
        if idx >= 0:
            self._listen.blockSignals(True)
            self._listen.setCurrentIndex(idx)
            self._listen.blockSignals(False)
        self._paint_swatch()

    def _on_mode(self, idx):
        mode = self._mode.itemData(idx)
        self._dev.setEnabled(mode == "surround")
        self._set("mode", mode)

    def _set(self, field, value):
        setattr(self.s, field, value)
        self.on_change()

    def _paint_swatch(self):
        self._sw.setStyleSheet(
            f"background:{self.s.color}; border:1px solid #555; border-radius:5px;")

    def _pick_colour(self):
        col = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self.s.color), self, "Radar colour")
        if col.isValid():
            self.s.color = col.name().upper()
            self._paint_swatch()
            self.on_change()

    def _open_mixer(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl("ms-settings:apps-volume"))
