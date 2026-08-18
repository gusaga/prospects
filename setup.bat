@echo off
rem First-time setup for Prospecting CRM (Windows).
rem Double-click this after downloading/cloning the repo.
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo  Prospecting CRM — first-time setup
echo  ==================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo  Python was not found on PATH.
  echo.
  echo  1^) Install Python 3.11 or newer from https://www.python.org/downloads/
  echo  2^) On the installer, check "Add python.exe to PATH"
  echo  3^) Close this window, open a new one, and run setup.bat again.
  echo.
  pause
  exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
  echo  Need Python 3.11+. You have:
  python --version
  echo  Install a newer version from https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo  Creating virtual environment .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo  Could not create .venv
    pause
    exit /b 1
  )
) else (
  echo  Found existing .venv
)

echo  Installing the app into .venv ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
  echo  Install failed.
  pause
  exit /b 1
)

echo.
echo  Setup complete.
echo.
echo  Next:
echo    - Double-click  run-live.bat  to pick or create a list in Documents
echo    - Then open Settings and fill your Ideal Customer Profile
echo.
echo  Guide: docs\how-to-run.html
echo.

choice /C YN /M "Start the CRM now"
if errorlevel 2 goto done
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m crm serve --open --port 8765 --pick-home
)

:done
pause
