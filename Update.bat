@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title ComfyMax MusicLab - Updater

set "ROOT=%~dp0"
for %%R in ("%ROOT%.") do set "MUSICLAB_ROOT=%%~fR"
set "STATUS_FILE=%TEMP%\ComfyMax-MusicLab-update-%RANDOM%-%RANDOM%.tmp"

echo.
echo ============================================================
echo                  ComfyMax MusicLab Updater
echo ============================================================
echo.

echo [1/5] Checking Git installation...
where git.exe >nul 2>&1
if errorlevel 1 where git.cmd >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git was not found.
    goto :UPDATE_FAILED
)
call git --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git was found but could not be started.
    goto :UPDATE_FAILED
)
for /f "delims=" %%R in ('call git rev-parse --show-toplevel 2^>nul') do set "REPOSITORY_ROOT=%%~fR"
if not defined REPOSITORY_ROOT (
    echo [ERROR] This update method is only available for a Git installation of ComfyMax MusicLab.
    goto :UPDATE_FAILED
)
if /I not "!REPOSITORY_ROOT!"=="!MUSICLAB_ROOT!" (
    echo [ERROR] The MusicLab directory is not the root of this Git repository.
    goto :UPDATE_FAILED
)
echo [OK] Git

echo.
echo [2/5] Checking branch and local changes...
for /f "delims=" %%B in ('call git symbolic-ref --quiet --short HEAD 2^>nul') do set "CURRENT_BRANCH=%%B"
if not defined CURRENT_BRANCH (
    echo [ERROR] MusicLab is not currently on a branch. Detached HEAD updates are not supported.
    goto :UPDATE_FAILED
)
echo [INFO] Current branch: !CURRENT_BRANCH!
for /f "delims=" %%U in ('call git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}" 2^>nul') do set "UPSTREAM=%%U"
if not defined UPSTREAM (
    echo [ERROR] The current branch has no upstream branch configured.
    goto :UPDATE_FAILED
)
call git status --porcelain --untracked-files=no >"!STATUS_FILE!" 2>nul
if errorlevel 1 (
    del /q "!STATUS_FILE!" >nul 2>&1
    echo [ERROR] Git could not inspect local project changes.
    goto :UPDATE_FAILED
)
for %%S in ("!STATUS_FILE!") do set "STATUS_SIZE=%%~zS"
del /q "!STATUS_FILE!" >nul 2>&1
if not "!STATUS_SIZE!"=="0" (
    echo [ERROR] Local project changes were detected.
    echo Update was cancelled to prevent overwriting your work.
    goto :UPDATE_FAILED
)
echo [OK] Working tree clean

echo.
echo [3/5] Checking for updates...
call git fetch
if errorlevel 1 (
    echo [ERROR] Git fetch failed. Check your internet connection and GitHub access.
    goto :UPDATE_FAILED
)
set "AHEAD="
set "BEHIND="
for /f "tokens=1,2" %%A in ('call git rev-list --left-right --count "HEAD...@{upstream}" 2^>nul') do (
    set "AHEAD=%%A"
    set "BEHIND=%%B"
)
if not defined AHEAD (
    echo [ERROR] Git could not compare the current branch with !UPSTREAM!.
    goto :UPDATE_FAILED
)
if not "!AHEAD!"=="0" if not "!BEHIND!"=="0" (
    echo [ERROR] The current branch has diverged from !UPSTREAM!.
    echo Update was cancelled because a fast-forward update is not possible.
    goto :UPDATE_FAILED
)

echo.
echo [4/5] Updating MusicLab...
if "!BEHIND!"=="0" (
    echo [OK] ComfyMax MusicLab is already up to date.
    if not "!AHEAD!"=="0" echo [WARNING] The current branch contains local commits not present on !UPSTREAM!.
) else (
    echo [INFO] Updating !CURRENT_BRANCH! from !UPSTREAM!...
    call git pull --ff-only
    if errorlevel 1 (
        echo [ERROR] The fast-forward update failed. No reset, cleanup, or forced checkout was performed.
        goto :UPDATE_FAILED
    )
    echo [OK] MusicLab updated
)

echo.
echo [5/5] Checking dependencies and runtimes...
if not exist "%ROOT%Install.bat" (
    echo [ERROR] Install.bat is missing after the update.
    goto :UPDATE_FAILED
)
call "%ROOT%Install.bat"
if errorlevel 1 (
    echo [ERROR] Install.bat failed. The MusicLab code update was not rolled back.
    goto :UPDATE_FAILED
)

echo.
echo [OK] ComfyMax MusicLab update completed.
exit /b 0

:UPDATE_FAILED
if exist "!STATUS_FILE!" del /q "!STATUS_FILE!" >nul 2>&1
echo.
echo [ERROR] MusicLab was not updated.
if not defined COMFYMAX_NO_PAUSE pause
exit /b 1
