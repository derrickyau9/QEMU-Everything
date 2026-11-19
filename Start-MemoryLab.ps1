param([switch]$OpenXfce)
$ErrorActionPreference = 'Stop'
$memoryPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $memoryPython)) {
    $memoryPython = Join-Path (Split-Path -Parent $PSScriptRoot) 'Hasselblad-X2D-II-QEMU-DevKit\.venv\Scripts\pythonw.exe'
}
$memoryScript = Join-Path $PSScriptRoot 'scripts\run_xfce_lab.py'
$memoryDevice = Join-Path $PSScriptRoot '.local\memory-lab\camera-1.2.7.16'
$memoryArguments = @('-X', 'utf8', ('"' + $memoryScript + '"'), '--device', ('"' + $memoryDevice + '"'))
if ($OpenXfce) { $memoryArguments += '--open-xfce' }
Start-Process -FilePath $memoryPython -ArgumentList $memoryArguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
