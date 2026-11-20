@echo off
rem Tanqih: the first run installs what it needs (uv, Python, the libraries), then it opens the interface.
rem Double-click this file. Keep this window open while you work.
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set "UV=uv"
where uv >nul 2>&1
if not errorlevel 1 goto run
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if exist "%UV%" goto run
echo First run: installing uv (it brings Python and the libraries)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if exist "%UV%" goto run
echo Could not install uv. Check the internet connection and try again.
pause
exit /b 1
:run
echo Starting Tanqih (the first run downloads Python and the libraries; this takes a few minutes)...
"%UV%" run python -m tanqih
if errorlevel 1 pause
