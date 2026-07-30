@echo off
rem Double-click launcher for the Prospecting CRM.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m crm serve --open --port 8765
pause
