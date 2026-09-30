"""第一阶段控制台监视器：确认回环采集正常。

在游戏或其他环绕声来源播放声音时运行，观察每个声道的 RMS 音量条。程序会
显示实际收到的声道数，以确认输入是真正的 7.1（8 声道）还是已合并的立体声。

用法：
    python monitor.py              # 采集默认扬声器回环
    python monitor.py --list       # 列出回环设备后退出
    python monitor.py --device "Speakers (Realtek...)"
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from soundradar.audio import (
    CaptureConfig,
    LoopbackCapture,
    list_loopback_devices,
    rms_to_dbfs,
)

BAR_WIDTH = 28


def bar(value01: float) -> str:
    n = int(round(max(0.0, min(1.0, value01)) * BAR_WIDTH))
    return "#" * n + "-" * (BAR_WIDTH - n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true",
                    help="列出回环采集设备后退出")
    ap.add_argument("--device", default=None, help="回环采集设备名称")
    ap.add_argument("--floor-db", type=float, default=-60.0,
                    help="音量条为空时对应的 dBFS 值（默认 -60）")
    args = ap.parse_args()

    if args.list:
        print("可用的回环采集设备：")
        for m in list_loopback_devices():
            print(f"  声道数={m.channels:2}  {m.name}")
        return 0

    cfg = CaptureConfig(device_name=args.device)
    cap = LoopbackCapture(cfg)
    cap.start()
    print("正在采集……请播放环绕声音频。按 Ctrl+C 停止。\n")

    floor = args.floor_db
    try:
        while True:
            lv = cap.get_levels()
            if lv.channels == 0:
                time.sleep(0.1)
                continue
            db = rms_to_dbfs(lv.rms)
            # map [floor..0] dBFS -> [0..1]
            norm = np.clip((db - floor) / (0.0 - floor), 0.0, 1.0)

            lines = [f"声道数：{lv.channels}   （按 Ctrl+C 停止）"]
            for label, d, nv in zip(lv.labels, db, norm):
                lines.append(f"  {label:>3}  {bar(nv)}  {d:6.1f} dBFS")
            out = "\n".join(lines)
            # redraw in place
            sys.stdout.write("\033[H\033[J" + out + "\n")
            sys.stdout.flush()
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        cap.stop()
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
