param([switch]$CpuOnly)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PIP_CACHE_DIR = Join-Path $PSScriptRoot '.cache\pip'
function Run-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe" }
}
# Optional project-local FFmpeg
$LocalFFmpegBin = Join-Path $PSScriptRoot 'ffmpeg\bin'

if (
    (Test-Path (Join-Path $LocalFFmpegBin "ffmpeg.exe")) -and
    (Test-Path (Join-Path $LocalFFmpegBin "ffprobe.exe"))
) {
    $env:PATH = "$LocalFFmpegBin;$env:PATH"
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw 'Install FFmpeg with ffprobe on PATH, then reopen PowerShell.'
}
$PythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $PythonExe)) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot '.python'
        $env:UV_CACHE_DIR = Join-Path $PSScriptRoot '.cache\uv'
        Run-Checked 'uv' @('python','install','3.11','--no-bin','--no-registry')
        Run-Checked 'uv' @('venv','--python','3.11','--seed','.venv')
    } else {
        if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Install Python 3.11 (64-bit) from python.org, then rerun setup.' }
        Run-Checked 'py' @('-3.11','-m','venv','.venv')
    }
}
Run-Checked $PythonExe @('-c','import sys; assert sys.version_info[:2] == (3,11), "Python 3.11 required"')
Run-Checked $PythonExe @('-m','pip','install','--upgrade','pip','wheel','setuptools<81')
$Index = 'https://download.pytorch.org/whl/cu128'
if ($CpuOnly) { $Index = 'https://download.pytorch.org/whl/cpu' }
Run-Checked $PythonExe @('-m','pip','install','torch==2.8.0','torchaudio==2.8.0','--index-url',$Index)
Run-Checked $PythonExe @('-m','pip','install','-e','.[editor]')
Run-Checked $PythonExe @('-m','pip','check')
Run-Checked $PythonExe @('-m','unittest','discover','-s','tests','-v')
& $PythonExe -m pip freeze | ForEach-Object { if ($_ -match '^-e ') { '-e .' } else { $_ } } | Set-Content -Encoding UTF8 'installed-versions.txt'
Write-Host 'Ready. Run Launch Editor.cmd. Optional SheetSage2 support: Install_SheetSage.bat.'
