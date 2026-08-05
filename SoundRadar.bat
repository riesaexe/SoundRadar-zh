@echo off
title SoundRadar  (close this window to stop)
cd /d "%~dp0"
echo Starting SoundRadar... close this window to stop.
echo (Surround mode needs VoiceMeeter running and set up - see SETUP.md)
REM No flags: capture mode, device, output and every tunable come from the
REM settings saved by the control panel, so this and the Settings window can
REM never disagree. (The old flags here were silently ignored.)
python run.py
if errorlevel 1 (
  echo.
  echo SoundRadar exited with an error - see the messages above.
  echo If it could not open the audio device, check the SETUP.md checklist.
  pause
)
