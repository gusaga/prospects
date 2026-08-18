@echo off
rem Launch the CRM and pick which Documents list to open.
rem Lists live under %USERPROFILE%\Documents\ProspectingCRM*
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo No .venv yet. Running first-time setup first...
  echo.
  call "%~dp0setup.bat"
  if errorlevel 1 exit /b 1
)

rem Always show the picker — do not inherit a leftover CRM_HOME.
set "CRM_HOME="

echo Opening the list picker at http://127.0.0.1:8765/homes
".venv\Scripts\python.exe" -m crm serve --open --port 8765 --pick-home
pause
