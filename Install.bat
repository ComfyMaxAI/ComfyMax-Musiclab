@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title ComfyMax MusicLab - Installer

set "ROOT=%~dp0"
set "VENV_PYTHON=%ROOT%.venv\Scripts\python.exe"
set "FFMPEG_DIR=%ROOT%engines\ffmpeg"
set "FFMPEG_BIN=%FFMPEG_DIR%\bin"
set "FFMPEG_EXE=%FFMPEG_BIN%\ffmpeg.exe"
set "FFPROBE_EXE=%FFMPEG_BIN%\ffprobe.exe"
set "AUDIOCPP_RUNTIME_URL=https://github.com/ComfyMaxAI/ComfyMax-Musiclab/releases/download/runtime-audiocpp-0.8.1/audiocpp-runtime-windows-cuda.zip"
set "AUDIOCPP_RUNTIME_SHA256=5B2F0CDC4036B20D15C440D22B4B292FCBC09AD27C8D3E8366211CFDA6319B88"

echo.
echo ============================================================
echo                  ComfyMax MusicLab Installer
echo ============================================================
echo Models are not installed automatically.
echo.

echo [1/9] Checking Python 3.11...
if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,11) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Existing .venv does not use Python 3.11.
        echo Remove or rename .venv yourself, install Python 3.11 64-bit, and rerun Install.bat.
        goto :INSTALL_FAILED
    )
    echo [OK] Python 3.11 in existing virtual environment
    goto :PYTHON_READY
)

py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,11) and sys.maxsize ^> 2**32 else 1)" >nul 2>&1
if not errorlevel 1 (
    echo [OK] System Python 3.11 64-bit detected
    goto :PYTHON_READY
)

where uv.exe >nul 2>&1
if not errorlevel 1 (
    echo [WARNING] System Python 3.11 was not found.
    echo The existing setup uses uv to install project-local CPython 3.11.15.
    goto :PYTHON_READY
)

echo [ERROR] Python 3.11 64-bit is required and was not found.
echo Install Python 3.11 from python.org, then rerun Install.bat.
goto :INSTALL_FAILED

:PYTHON_READY
echo.
echo [2/9] Checking FFmpeg...
if exist "%FFMPEG_EXE%" if exist "%FFPROBE_EXE%" (
    set "PATH=%FFMPEG_BIN%;%PATH%"
    goto :VERIFY_FFMPEG
)

where ffmpeg.exe >nul 2>&1
if errorlevel 1 goto :INSTALL_FFMPEG
where ffprobe.exe >nul 2>&1
if errorlevel 1 goto :INSTALL_FFMPEG
goto :VERIFY_FFMPEG

:INSTALL_FFMPEG
echo [WARNING] FFmpeg with ffprobe was not found. Installing the existing private MusicLab FFmpeg runtime...
where curl.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] curl.exe is required to install FFmpeg.
    goto :INSTALL_FAILED
)
where tar.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] tar.exe is required to extract FFmpeg.
    goto :INSTALL_FAILED
)
set "FFMPEG_ZIP=%TEMP%\comfymax-ffmpeg.zip"
set "FFMPEG_TMP=%TEMP%\comfymax-ffmpeg-extract"
if exist "%FFMPEG_ZIP%" del /q "%FFMPEG_ZIP%"
if exist "%FFMPEG_TMP%" rmdir /s /q "%FFMPEG_TMP%"
mkdir "%FFMPEG_TMP%" >nul 2>&1
curl.exe -L --fail --progress-bar "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -o "%FFMPEG_ZIP%"
if errorlevel 1 goto :FFMPEG_FAILED
tar.exe -xf "%FFMPEG_ZIP%" -C "%FFMPEG_TMP%"
if errorlevel 1 goto :FFMPEG_FAILED
if not exist "%FFMPEG_DIR%" mkdir "%FFMPEG_DIR%" >nul 2>&1
for /d %%D in ("%FFMPEG_TMP%\ffmpeg-*") do if exist "%%D\bin\ffmpeg.exe" xcopy "%%D\*" "%FFMPEG_DIR%\" /E /I /Q /Y >nul
if not exist "%FFMPEG_EXE%" goto :FFMPEG_FAILED
if not exist "%FFPROBE_EXE%" goto :FFMPEG_FAILED
del /q "%FFMPEG_ZIP%" >nul 2>&1
rmdir /s /q "%FFMPEG_TMP%" >nul 2>&1
set "PATH=%FFMPEG_BIN%;%PATH%"

:VERIFY_FFMPEG
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] ffmpeg could not be started.
    goto :INSTALL_FAILED
)
ffprobe -version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] ffprobe could not be started.
    goto :INSTALL_FAILED
)
echo [OK] FFmpeg and ffprobe

echo.
echo [3/9] Validating MusicLab-managed audio.cpp runtime...
set "AUDIOCPP=%ROOT%engines\audiocpp"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%install-audiocpp-runtime.ps1" -RuntimeDir "%AUDIOCPP%" -ReleaseUrl "%AUDIOCPP_RUNTIME_URL%" -ExpectedSha256 "%AUDIOCPP_RUNTIME_SHA256%"
if errorlevel 1 goto :INSTALL_FAILED

echo.
echo [4/9] Installing Python environment and MusicLab dependencies...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup.ps1"
if errorlevel 1 goto :INSTALL_FAILED
if not exist "%VENV_PYTHON%" (
    echo [ERROR] MusicLab virtual environment was not created.
    goto :INSTALL_FAILED
)
echo [OK] Virtual environment

echo.
echo [5/9] Creating model directories without downloading models...
if not exist "%ROOT%models\yue2" mkdir "%ROOT%models\yue2"
if not exist "%ROOT%models\sheetsage2" mkdir "%ROOT%models\sheetsage2"
if not exist "%ROOT%.cache\whisper" mkdir "%ROOT%.cache\whisper"
echo [OK] Model directories

echo.
echo [6/9] Installing or validating SheetSage runtime...
set "SHEETSAGE_RUNTIME=%ROOT%engines\sheetsage\runtime"
"%VENV_PYTHON%" -c "from pathlib import Path; from comfymax_audio_chunker.music.sheetsage_setup import validate_runtime; validate_runtime(Path(r'%SHEETSAGE_RUNTIME%'))" >nul 2>&1
if not errorlevel 1 (
    echo [OK] SheetSage runtime
    goto :SHEETSAGE_READY
)
echo [INFO] Installing SheetSage runtime...
"%VENV_PYTHON%" -m comfymax_audio_chunker.music.sheetsage_setup --runtime-only
if errorlevel 1 goto :INSTALL_FAILED
"%VENV_PYTHON%" -c "from pathlib import Path; from comfymax_audio_chunker.music.sheetsage_setup import validate_runtime; validate_runtime(Path(r'%SHEETSAGE_RUNTIME%'))"
if errorlevel 1 (
    echo [ERROR] SheetSage runtime installation failed
    goto :INSTALL_FAILED
)
echo [OK] SheetSage runtime
:SHEETSAGE_READY

echo.
echo [7/9] Installing or validating abc2abc runtime...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%install-abcmidi-runtime.ps1"
if errorlevel 1 goto :INSTALL_FAILED

echo.
echo [8/9] Running installation self-check...
"%VENV_PYTHON%" -c "import comfymax_audio_chunker, PySide6, faster_whisper, mido, sounddevice, soundfile, librosa, demucs; print('[OK] MusicLab dependencies'); print('[OK] Whisper runtime')"
if errorlevel 1 goto :INSTALL_FAILED
"%VENV_PYTHON%" -c "from comfymax_audio_chunker.music import sheetsage_setup; print('[OK] SheetSage integration')"
if errorlevel 1 goto :INSTALL_FAILED
"%VENV_PYTHON%" -c "from comfymax_audio_chunker.music.beat_backend import configuration, validate_managed_runtime; validate_managed_runtime(); c=configuration(); assert c['backend']=='beat-transformer'; assert c['command'][0].lower().startswith(r'%ROOT%.venv'.lower()); print('[OK] MusicLab-managed Beat-Transformer runtime')"
if errorlevel 1 goto :INSTALL_FAILED
"%VENV_PYTHON%" -m pip check
if errorlevel 1 goto :INSTALL_FAILED
echo [OK] Python

echo.
echo [9/9] Checking NVIDIA GPU...
where nvidia-smi.exe >nul 2>&1
if errorlevel 1 (
    echo [WARNING] nvidia-smi was not found. GPU-accelerated functions may not be available.
) else (
    for /f "usebackq delims=" %%G in (`nvidia-smi --query-gpu^=name --format^=csv^,noheader 2^>nul`) do echo [OK] NVIDIA GPU detected: %%G
)

echo.
echo Models are not installed automatically.
echo Use MusicLab Settings to install/select models.
echo.
echo ============================================================
echo MusicLab installation complete.
echo Start MusicLab with: Launch Editor.cmd
echo ============================================================
echo.
pause
exit /b 0

:FFMPEG_FAILED
if exist "%FFMPEG_ZIP%" del /q "%FFMPEG_ZIP%" >nul 2>&1
if exist "%FFMPEG_TMP%" rmdir /s /q "%FFMPEG_TMP%" >nul 2>&1
echo [ERROR] The private FFmpeg runtime could not be installed.
goto :INSTALL_FAILED

:INSTALL_FAILED
echo.
echo ============================================================
echo [ERROR] MusicLab installation failed.
echo Correct the error above and safely rerun Install.bat.
echo Existing models, settings, projects and outputs were not removed.
echo ============================================================
echo.
pause
exit /b 1
