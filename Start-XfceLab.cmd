@echo off
setlocal
set "QE_XFCE_PY=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%QE_XFCE_PY%" set "QE_XFCE_PY=%~dp0..\Hasselblad-X2D-II-QEMU-DevKit\.venv\Scripts\pythonw.exe"
if not exist "%QE_XFCE_PY%" (
  echo Run Launch.cmd once to prepare the Python runtime.
  pause
  exit /b 1
)
start "" "%QE_XFCE_PY%" -X utf8 "%~dp0scripts\run_xfce_lab.py" --open-xfce
