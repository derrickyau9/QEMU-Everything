param([switch]$OpenXfce)
$ErrorActionPreference = 'Stop'
$xfcePython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $xfcePython)) {
    $xfcePython = Join-Path (Split-Path -Parent $PSScriptRoot) 'Hasselblad-X2D-II-QEMU-DevKit\.venv\Scripts\pythonw.exe'
}
if (-not (Test-Path -LiteralPath $xfcePython)) { throw 'Run Launch.cmd once to prepare the Python runtime.' }
$xfceScript = Join-Path $PSScriptRoot 'scripts\run_xfce_lab.py'
$xfceArguments = @('-X', 'utf8', ('"' + $xfceScript + '"'))
if ($OpenXfce) { $xfceArguments += '--open-xfce' }
Start-Process -FilePath $xfcePython -ArgumentList $xfceArguments -WorkingDirectory $PSScriptRoot -WindowStyle Normal
