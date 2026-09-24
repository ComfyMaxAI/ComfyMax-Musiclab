@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.ps1 first to install Audio Chunker.
  pause
  exit /b 1
)
set "PYTHONPATH=%~dp0src"
".venv\Scripts\python.exe" -m comfymax_audio_chunker.music.sheetsage_setup %*
set "setup_result=%ERRORLEVEL%"
pause
exit /b %setup_result%
