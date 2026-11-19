param([switch]$MatchCameraHeadroom)
$ErrorActionPreference = 'Stop'
$memoryPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $memoryPython)) {
    $memoryPython = Join-Path (Split-Path -Parent $PSScriptRoot) 'Hasselblad-X2D-II-QEMU-DevKit\.venv\Scripts\python.exe'
}
$memoryDevice = Join-Path $PSScriptRoot '.local\memory-lab\camera-1.2.7.16'
$memoryArgs = @('-X', 'utf8', (Join-Path $PSScriptRoot 'scripts\check_xfce_lab.py'),
    '--device', $memoryDevice, '--memory-profile', (Join-Path $memoryDevice 'memory-profile.json'), '--seconds', '360')
if ($MatchCameraHeadroom) {
    $memoryArgs += @('--headroom-helper', (Join-Path $PSScriptRoot '.local\memory-lab\qe-memory-headroom'))
}
& $memoryPython @memoryArgs
exit $LASTEXITCODE
