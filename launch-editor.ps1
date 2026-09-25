param([string]$Project)
$ErrorActionPreference = 'Stop'
$PythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$LocalFFmpegBin = Join-Path $PSScriptRoot "engines\ffmpeg\bin"

if (
    (Test-Path (Join-Path $LocalFFmpegBin "ffmpeg.exe")) -and
    (Test-Path (Join-Path $LocalFFmpegBin "ffprobe.exe"))
) {
    $env:PATH = "$LocalFFmpegBin;$env:PATH"
}
if (-not (Test-Path $PythonExe)) { throw 'Run setup.ps1 first.' }
$EditorArgs = @('-m','comfymax_audio_chunker.editor.marker_app')
if ($Project) { $EditorArgs += $Project }
& $PythonExe @EditorArgs
if ($LASTEXITCODE -ne 0) { throw "Editor exited with error $LASTEXITCODE" }
