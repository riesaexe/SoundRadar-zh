"""SoundRadar 音频采集诊断。

显示 SoundRadar 从所选采集设备收到的音频，用于确认：
  1. 是否收到声音（显示“静音”代表没有音频）。
  2. 是否为真实环绕声（声道差异很小表示音频可能已合并为单声道）。

运行：python diag.py
运行时播放方向明确的游戏或视频声音。程序每秒约输出两行，并在约 90 秒后
自动停止；也可以随时按 Ctrl+C 结束。
"""

from __future__ import annotations

import sys
import time

import numpy as np
import soundcard as sc

from soundradar import settings as settings_mod
from soundradar.audio import labels_for, rms_to_dbfs

SR = 48000
BLOCK = 2400      # 50 ms frames
PRINT_EVERY = 0.5  # seconds between printed lines
RUN_SECONDS = 90.0


def main() -> None:
    s = settings_mod.load()
    mode = "环绕声" if s.mode == "surround" else "立体声"
    print(f"采集模式：{mode}")
    print(f"采集设备：{s.capture_device or '默认扬声器回环'}")
    print()

    print("=== 可用的回环采集设备 ===")
    for m in sc.all_microphones(include_loopback=True):
        loop = " [回环]" if getattr(m, "isloopback", False) else ""
        print(f"  声道数={m.channels:<2} {m.name}{loop}")
    print()

    name = s.capture_device or None
    try:
        if name is None:
            spk = sc.default_speaker()
            mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
        else:
            mic = sc.get_microphone(id=name, include_loopback=True)
    except Exception as e:  # noqa: BLE001
        print(f"！！无法打开设备“{name}”：{e}")
        sys.exit(1)

    channels = mic.channels
    labels = labels_for(channels)
    print(f"正在采集“{mic.name}”（{channels} 个声道）")
    print("播放方向明显的声音，每行会列出音量最大的声道。\n")

    peak = np.zeros(channels, dtype=np.float64)
    last_print = 0.0
    start = time.perf_counter()

    with mic.recorder(samplerate=SR, channels=channels, blocksize=BLOCK) as rec:
        try:
            while time.perf_counter() - start < RUN_SECONDS:
                data = rec.record(numframes=BLOCK)
                if data.size == 0:
                    continue
                rms = np.sqrt(np.mean(np.square(data, dtype=np.float64), axis=0))
                peak = np.maximum(peak * 0.5, rms)

                now = time.perf_counter()
                if now - last_print < PRINT_EVERY:
                    continue
                last_print = now

                db = rms_to_dbfs(peak.astype(np.float32))
                active = db > -55.0
                if not active.any():
                    print("  ……静音（此设备没有收到音频）……")
                    continue

                spread = float(db[active].max() - db[active].min())
                # list only channels that are making noise, loudest first
                idx = np.argsort(db)[::-1]
                parts = []
                for i in idx:
                    if db[i] <= -55.0:
                        continue
                    lab = labels[i] if i < len(labels) else f"ch{i}"
                    parts.append(f"{lab}={db[i]:.0f}")
                tag = "环绕声" if spread > 6.0 else "单声道/声道相同"
                print(f"  [{tag:>12}] spread={spread:4.1f}dB  " + "  ".join(parts))
        except KeyboardInterrupt:
            pass
    print("\n已停止。")


if __name__ == "__main__":
    main()
