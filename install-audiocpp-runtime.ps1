param(
    [Parameter(Mandatory = $true)][string]$RuntimeDir,
    [Parameter(Mandatory = $true)][string]$ReleaseUrl,
    [Parameter(Mandatory = $true)][string]$ExpectedSha256,
    [string]$SourceArchive = '',
    [string]$WorkingDirectory = ''
)

$ErrorActionPreference = 'Stop'
$RequiredRuntimeFiles = @(
    'audiocpp_server.exe',
    'cublas64_13.dll',
    'cublasLt64_13.dll',
    'cufft64_12.dll',
    'ggml-base.dll',
    'ggml-cpu-haswell.dll',
    'ggml-cuda.dll',
    'ggml.dll',
    'MSVCP140_CODECVT_IDS.dll',
    'MSVCP140.dll',
    'VCRUNTIME140_1.dll',
    'VCRUNTIME140.dll'
)

function Get-MissingRuntimeFiles {
    param([string]$Directory)
    @($RequiredRuntimeFiles | Where-Object {
        -not (Test-Path -LiteralPath (Join-Path $Directory $_) -PathType Leaf)
    })
}

$runtimePath = [IO.Path]::GetFullPath($RuntimeDir)
$serverConfig = Join-Path $runtimePath 'server.json'
if (-not (Test-Path -LiteralPath $serverConfig -PathType Leaf)) {
    Write-Host '[ERROR] Missing repository audio.cpp configuration: server.json'
    exit 1
}

$missing = @(Get-MissingRuntimeFiles -Directory $runtimePath)
if ($missing.Count -eq 0) {
    Write-Host '[OK] audio.cpp runtime'
    exit 0
}

foreach ($name in $missing) {
    Write-Host "[MISSING] audio.cpp runtime file: $name"
}
Write-Host '[INFO] Downloading audio.cpp Windows CUDA runtime...'

$ownsWorkingDirectory = [string]::IsNullOrWhiteSpace($WorkingDirectory)
if ($ownsWorkingDirectory) {
    $workPath = Join-Path ([IO.Path]::GetTempPath()) ('ComfyMax-audiocpp-' + [guid]::NewGuid().ToString('N'))
} else {
    $workPath = [IO.Path]::GetFullPath($WorkingDirectory)
}
$archivePath = Join-Path $workPath 'audiocpp-runtime-windows-cuda.zip'
$extractPath = Join-Path $workPath 'extracted'

try {
    if (Test-Path -LiteralPath $workPath) {
        Remove-Item -LiteralPath $workPath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $workPath | Out-Null

    if ([string]::IsNullOrWhiteSpace($SourceArchive)) {
        Invoke-WebRequest -UseBasicParsing -Uri $ReleaseUrl -OutFile $archivePath
    } else {
        Copy-Item -LiteralPath $SourceArchive -Destination $archivePath
    }
    if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf) -or
        (Get-Item -LiteralPath $archivePath).Length -eq 0) {
        throw 'The audio.cpp runtime download is missing or empty.'
    }

    $sha256 = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($archivePath)
    try {
        $actualHash = ([BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '')
    } finally {
        $stream.Dispose()
        $sha256.Dispose()
    }
    if ($actualHash -ne $ExpectedSha256.ToUpperInvariant()) {
        throw "audio.cpp runtime SHA-256 mismatch. Expected $ExpectedSha256 but received $actualHash."
    }

    New-Item -ItemType Directory -Path $extractPath | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractPath -Force
    $packageMissing = @($RequiredRuntimeFiles | Where-Object {
        -not (Test-Path -LiteralPath (Join-Path $extractPath $_) -PathType Leaf)
    })
    if ($packageMissing.Count -gt 0) {
        foreach ($name in $packageMissing) {
            Write-Host "[MISSING] Extracted audio.cpp runtime file: $name"
        }
        throw 'audio.cpp runtime installation failed'
    }

    New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
    foreach ($name in $RequiredRuntimeFiles) {
        Copy-Item -LiteralPath (Join-Path $extractPath $name) -Destination (Join-Path $runtimePath $name) -Force
    }

    $missingAfterInstall = @(Get-MissingRuntimeFiles -Directory $runtimePath)
    if ($missingAfterInstall.Count -gt 0) {
        foreach ($name in $missingAfterInstall) {
            Write-Host "[MISSING] audio.cpp runtime file after extraction: $name"
        }
        throw 'audio.cpp runtime installation failed'
    }
    if (-not (Test-Path -LiteralPath $serverConfig -PathType Leaf)) {
        throw 'Repository audio.cpp configuration server.json was removed unexpectedly.'
    }

    Write-Host '[OK] audio.cpp runtime'
    exit 0
} catch {
    Write-Host ('[ERROR] ' + $_.Exception.Message)
    exit 1
} finally {
    if (Test-Path -LiteralPath $workPath) {
        Remove-Item -LiteralPath $workPath -Recurse -Force -ErrorAction SilentlyContinue
    }
}
