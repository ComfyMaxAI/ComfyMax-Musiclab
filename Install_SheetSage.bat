@echo off
setlocal
title ComfyMax MusicLab - SheetSage2 Setup
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.ps1 first to install ComfyMax MusicLab.
  pause
  exit /b 1
)
echo.
echo ==========================================
echo   ComfyMax MusicLab - SheetSage2 Setup
echo ==========================================
echo.
set "PYTHONPATH=%~dp0src"
".venv\Scripts\python.exe" -m comfymax_audio_chunker.music.sheetsage_setup %*
set "setup_result=%ERRORLEVEL%"
echo.
if "%setup_result%"=="0" (
  echo SheetSage2 setup completed successfully.
) else (
  echo SheetSage2 setup failed with exit code %setup_result%.
)
echo.
pause
exit /b %setup_result%
