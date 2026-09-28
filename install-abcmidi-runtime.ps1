param(
    [string]$RuntimeDir = (Join-Path $PSScriptRoot 'engines\abcmidi'),
    [string]$ReleaseUrl = 'https://ifdo.ca/~seymour/runabc/abcmidi_win32_mingw64.zip',
    [string]$ExpectedArchiveSha256 = '1D68FA174F43172D3841C20FBA8649C022ACF6B70CD9B9323B0876F6AD6DA93A',
    [string]$ExpectedExecutableSha256 = '8EACC7CCE5EEE35581C6FD0314E501922FCEC030D17F8A79801110BF01051F69'
)
$ErrorActionPreference = 'Stop'
$Executable = Join-Path $RuntimeDir 'abc2abc.exe'
function Test-Abc2Abc {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    return (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash -eq $ExpectedExecutableSha256
}
if (Test-Abc2Abc) {
    Write-Host '[OK] abc2abc 2.22 runtime'
    exit 0
}
$TemporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ('ComfyMax-MusicLab-abc2abc-' + [guid]::NewGuid().ToString('N'))
$Archive = Join-Path $TemporaryRoot 'abctools.zip'
try {
    New-Item -ItemType Directory -Path $TemporaryRoot | Out-Null
    Invoke-WebRequest -UseBasicParsing -Uri $ReleaseUrl -OutFile $Archive
    if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash -ne $ExpectedArchiveSha256) {
        throw 'abcMIDI archive checksum mismatch.'
    }
    Expand-Archive -LiteralPath $Archive -DestinationPath $TemporaryRoot
    $Source = Join-Path $TemporaryRoot 'abcmidi_win32_mingw64\abc2abc.exe'
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { throw 'abc2abc is missing from the archive.' }
    if ((Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash -ne $ExpectedExecutableSha256) {
        throw 'abc2abc executable checksum mismatch.'
    }
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Executable -Force
    $Manifest = @{
        tool = 'abc2abc'; version = '2.22 April 30 2024'; build = 'win32-mingw64'
        source = $ReleaseUrl; archive_sha256 = $ExpectedArchiveSha256
        executable_sha256 = $ExpectedExecutableSha256
        license = 'GPL-2.0-or-later'
    } | ConvertTo-Json
    Set-Content -LiteralPath (Join-Path $RuntimeDir 'installation.json') -Value $Manifest -Encoding UTF8
    if (-not (Test-Abc2Abc)) { throw 'Installed abc2abc failed validation.' }
    Write-Host '[OK] abc2abc 2.22 runtime installed'
} finally {
    if (Test-Path -LiteralPath $TemporaryRoot) { Remove-Item -LiteralPath $TemporaryRoot -Recurse -Force }
}
