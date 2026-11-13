@echo off
rem ------------------------------------------------------------------
rem  Jetson file transfer - setup / update (Windows)
rem  Double-click this file. It will:
rem    1. install uv (if missing)
rem    2. install or update serial-xfer from GitHub (no login needed)
rem    3. create shortcuts on the Desktop and in "Send to"
rem    4. start the transfer window
rem  Behind a proxy: set HTTPS_PROXY=http://proxy:port before running.
rem ------------------------------------------------------------------
setlocal
set "PKG=https://github.com/git-756/serial-data-transfer/archive/refs/heads/main.zip"

where uv >nul 2>nul
if not errorlevel 1 goto :have_uv
echo [1/3] Installing uv ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 goto :fail
:have_uv

echo [2/3] Installing / updating serial-xfer ...
uv tool install --force "%PKG%"
if errorlevel 1 goto :fail

for /f "delims=" %%i in ('uv tool dir --bin') do set "BIN=%%i"
if not exist "%BIN%\serial-xfer-gui.exe" goto :fail

echo [3/3] Creating shortcuts ...
"%BIN%\serial-xfer-gui.exe" --create-shortcuts

echo Done. Starting the transfer window.
start "" "%BIN%\serial-xfer-gui.exe"
exit /b 0

:fail
echo.
echo Setup failed. Check the network / proxy (HTTPS_PROXY) and try again.
pause
exit /b 1
