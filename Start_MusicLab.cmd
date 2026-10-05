@echo off
cd /d "%~dp0"
if exist "engines\ffmpeg\bin\ffmpeg.exe" if exist "engines\ffmpeg\bin\ffprobe.exe" set "PATH=%~dp0engines\ffmpeg\bin;%PATH%"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run Install.bat first.
  pause
  exit /b 1
)
start "ComfyMax MusicLab" ".venv\Scripts\pythonw.exe" -m comfymax_audio_chunker.editor.marker_app
