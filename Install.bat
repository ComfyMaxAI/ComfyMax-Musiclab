@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title ComfyMax MusicLab - Installer

echo.
echo ============================================================
echo                  ComfyMax MusicLab
echo                       Installer
echo ============================================================
echo.
echo This installer will:
echo.
echo   - Install a local FFmpeg runtime when needed
echo   - Set up Python 3.11
echo   - Create the MusicLab virtual environment
echo   - Install MusicLab and its dependencies
echo   - Install CUDA-enabled PyTorch
echo   - Run dependency checks
echo   - Run the MusicLab test suite
echo.
echo SheetSage2 is optional and can be installed afterwards.
echo.
pause

set "FFMPEG_DIR=%~dp0engines\ffmpeg"
set "FFMPEG_BIN=%FFMPEG_DIR%\bin"
set "FFMPEG_EXE=%FFMPEG_BIN%\ffmpeg.exe"
set "FFPROBE_EXE=%FFMPEG_BIN%\ffprobe.exe"

echo.
echo [1/4] Checking FFmpeg...
echo.

if exist "%FFMPEG_EXE%" if exist "%FFPROBE_EXE%" (
    echo Local FFmpeg found.
    goto :FFMPEG_READY
)

echo Local FFmpeg was not found.
echo.
echo Checking for an existing system FFmpeg installation...

where ffmpeg >nul 2>&1
if errorlevel 1 goto :INSTALL_FFMPEG

where ffprobe >nul 2>&1
if errorlevel 1 goto :INSTALL_FFMPEG

echo System FFmpeg found.
echo.
echo MusicLab can use the existing system installation.
goto :FFMPEG_READY


:INSTALL_FFMPEG
echo.
echo FFmpeg is not installed.
echo MusicLab will now install a private local copy.
echo.
echo Destination:
echo   %FFMPEG_DIR%
echo.

where curl.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] curl.exe is not available on this Windows installation.
    goto :INSTALL_FAILED
)

where tar.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] tar.exe is not available on this Windows installation.
    goto :INSTALL_FAILED
)

set "FFMPEG_ZIP=%TEMP%\comfymax-ffmpeg.zip"
set "FFMPEG_TMP=%TEMP%\comfymax-ffmpeg-extract"

if exist "%FFMPEG_ZIP%" del /q "%FFMPEG_ZIP%"
if exist "%FFMPEG_TMP%" rmdir /s /q "%FFMPEG_TMP%"

mkdir "%FFMPEG_TMP%" >nul 2>&1

echo Downloading FFmpeg...
echo.

curl.exe -L --fail --progress-bar ^
  "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" ^
  -o "%FFMPEG_ZIP%"

if errorlevel 1 (
    echo.
    echo [ERROR] FFmpeg download failed.
    goto :FFMPEG_FAILED
)

echo.
echo Extracting FFmpeg...
echo.

tar.exe -xf "%FFMPEG_ZIP%" -C "%FFMPEG_TMP%"

if errorlevel 1 (
    echo.
    echo [ERROR] FFmpeg extraction failed.
    goto :FFMPEG_FAILED
)

if exist "%FFMPEG_DIR%" rmdir /s /q "%FFMPEG_DIR%"
mkdir "%FFMPEG_DIR%" >nul 2>&1

for /d %%D in ("%FFMPEG_TMP%\ffmpeg-*") do (
    if exist "%%D\bin\ffmpeg.exe" (
        xcopy "%%D\*" "%FFMPEG_DIR%\" /E /I /Q /Y >nul
        goto :FFMPEG_COPIED
    )
)

echo.
echo [ERROR] Could not locate ffmpeg.exe in the downloaded archive.
goto :FFMPEG_FAILED


:FFMPEG_COPIED

if not exist "%FFMPEG_EXE%" (
    echo.
    echo [ERROR] Local ffmpeg.exe was not installed correctly.
    goto :FFMPEG_FAILED
)

if not exist "%FFPROBE_EXE%" (
    echo.
    echo [ERROR] Local ffprobe.exe was not installed correctly.
    goto :FFMPEG_FAILED
)

echo.
echo Local FFmpeg installed successfully.

del /q "%FFMPEG_ZIP%" >nul 2>&1
rmdir /s /q "%FFMPEG_TMP%" >nul 2>&1


:FFMPEG_READY

REM Put project-local FFmpeg first on PATH for setup.ps1 and child processes.
if exist "%FFMPEG_BIN%\ffmpeg.exe" (
    set "PATH=%FFMPEG_BIN%;%PATH%"
)

echo.
echo Verifying FFmpeg...
echo.

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

echo FFmpeg OK.

echo.
echo [2/4] Installing ComfyMax MusicLab...
echo.
echo This may take several minutes.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"

if errorlevel 1 goto :INSTALL_FAILED

echo.
echo [3/4] Verifying MusicLab...
echo.

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo [ERROR] MusicLab virtual environment was not created.
    goto :INSTALL_FAILED
)

echo MusicLab environment OK.

echo.
echo [4/4] Installation completed successfully.
echo.
echo ============================================================
echo              ComfyMax MusicLab is ready!
echo ============================================================
echo.
echo Start MusicLab with:
echo.
echo     Launch Editor.cmd
echo.
echo Optional SheetSage2 support can be installed with:
echo.
echo     Install_SheetSage.bat
echo.

choice /C YN /N /M "Install optional SheetSage2 now? [Y/N]: "

if errorlevel 2 goto :DONE
if errorlevel 1 goto :INSTALL_SHEETSAGE


:INSTALL_SHEETSAGE
echo.
echo Starting SheetSage2 installer...
echo.

call "%~dp0Install_SheetSage.bat"

goto :DONE


:FFMPEG_FAILED
if exist "%FFMPEG_ZIP%" del /q "%FFMPEG_ZIP%" >nul 2>&1
if exist "%FFMPEG_TMP%" rmdir /s /q "%FFMPEG_TMP%" >nul 2>&1
goto :INSTALL_FAILED


:INSTALL_FAILED
echo.
echo ============================================================
echo                 INSTALLATION FAILED
echo ============================================================
echo.
echo MusicLab could not be installed completely.
echo.
echo Review the error messages above.
echo.
echo Nothing has been deleted from your projects.
echo You can run Install.bat again after correcting the problem.
echo.
pause
exit /b 1


:DONE
echo.
echo ============================================================
echo                         DONE
echo ============================================================
echo.
echo You can now start ComfyMax MusicLab with:
echo.
echo     Launch Editor.cmd
echo.
pause
exit /b 0