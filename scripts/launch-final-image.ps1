<#
    Open the original X2D II UI from a locally prepared 1.4.15.7 system image.

    The image is imported into the DevKit firmware library on first run (that step
    can take a while); later runs reuse the cached entry.

    The workbench starts in a separate process. Close its window to stop the guest.

    Example:

        .\scripts\launch-final-image.ps1 -SystemImage '.\system.img'

    The Python interpreter is probed in this order: -Python, this checkout's own
    .venv, the sibling DevKit checkout's .venv, then python on PATH.
#>
param(
    [Parameter(Mandatory = $true)][string]$SystemImage,
    [string]$Entry,
    [string]$Python
)
$ErrorActionPreference = 'Stop'

# $PSScriptRoot is not populated while param() defaults bind, so resolve the
# derived paths here rather than in the parameter list.
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Entry) {
    $Entry = Join-Path $PSScriptRoot 'run_final_image.py'
}
if (-not $Python) {
    $Candidate = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Candidate)) {
        $Candidate = Join-Path $Root '..\Hasselblad-X2D-II-QEMU-DevKit\.venv\Scripts\python.exe'
    }
    if (Test-Path -LiteralPath $Candidate) {
        $Python = (Resolve-Path -LiteralPath $Candidate).Path
    } else {
        $OnPath = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
        if (-not $OnPath) { throw 'No Python found. Run Launch.ps1 once, or pass -Python.' }
        $Python = $OnPath.Source
    }
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python not found: $Python" }
if (-not (Test-Path -LiteralPath $Entry -PathType Leaf)) { throw "Entry script not found: $Entry" }
if (-not (Test-Path -LiteralPath $SystemImage -PathType Leaf)) { throw "System image not found: $SystemImage" }

$Python = (Resolve-Path -LiteralPath $Python).Path
$Entry = (Resolve-Path -LiteralPath $Entry).Path
# Start-Process joins ArgumentList into one command line on Windows.
$EntryArgument = '"' + $Entry + '"'
$ImageArgument = '"' + (Resolve-Path -LiteralPath $SystemImage).Path + '"'
$Process = Start-Process -FilePath $Python `
    -ArgumentList '-u', $EntryArgument, $ImageArgument `
    -WorkingDirectory (Split-Path -Parent $Entry) `
    -WindowStyle Hidden `
    -PassThru

Write-Host "DevKit launching (pid $($Process.Id)) with $Python"
Write-Host 'Guest console: console.log in the imported firmware library entry.'
Write-Host "Look for 'X2DII_UI_SESSION_READY' to confirm the UI came up."
