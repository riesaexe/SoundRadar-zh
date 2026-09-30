@echo off
chcp 65001 >nul
title SoundRadar（关闭此窗口以停止）
cd /d "%~dp0"
echo 正在启动 SoundRadar……关闭此窗口即可停止。
echo （7.1 模式需要原生或虚拟 8 声道播放设备；推荐 VB-CABLE，详见 SETUP.md）
REM No flags: capture mode, device, output and every tunable come from the
REM settings saved by the control panel, so this and the Settings window can
REM never disagree. (The old flags here were silently ignored.)
python run.py
if errorlevel 1 (
  echo.
  echo SoundRadar 已出错退出，请查看上方信息。
  echo 如果无法打开音频设备，请检查 SETUP.md 中的设置步骤。
  pause
)
