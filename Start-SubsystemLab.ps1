param([switch]$OpenXfce)
$ErrorActionPreference = 'Stop'
$subsystemPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $subsystemPython)) {
    $subsystemPython = Join-Path (Split-Path -Parent $PSScriptRoot) 'Hasselblad-X2D-II-QEMU-DevKit\.venv\Scripts\pythonw.exe'
}
$subsystemScript = Join-Path $PSScriptRoot 'scripts\run_xfce_lab.py'
$subsystemDevice = Join-Path $PSScriptRoot '.local\subsystem-arm64\device'
$subsystemArguments = @('-X', 'utf8', ('"' + $subsystemScript + '"'), '--device', ('"' + $subsystemDevice + '"'))
if ($OpenXfce) { $subsystemArguments += '--open-xfce' }
Start-Process -FilePath $subsystemPython -ArgumentList $subsystemArguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
