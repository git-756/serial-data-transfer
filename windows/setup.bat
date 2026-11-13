@echo off
rem ------------------------------------------------------------------
rem  Jetson file transfer - setup / update (Windows)
rem  Double-click this file. It will:
rem    1. install uv (if missing)
rem    2. install or update serial-xfer from GitHub (no login needed)
rem    3. create shortcuts on the Desktop and in "Send to"
rem    4. start the transfer window
rem
rem  Proxy (company network) is picked automatically, first match wins:
rem    a. argument:            setup.bat http://proxy:port
rem    b. HTTPS_PROXY that is already set
rem    c. proxy.txt next to this file (one line: http://proxy:port)
rem    d. Windows proxy settings (the same ones the browser uses)
rem ------------------------------------------------------------------
setlocal
set "HERE=%~dp0"
set "PKG=https://github.com/git-756/serial-data-transfer/archive/refs/heads/main.zip"

call :find_proxy "%~1"
if not defined PX goto :no_proxy
set "HTTP_PROXY=%PX%"
set "HTTPS_PROXY=%PX%"
echo Proxy: %PX%
goto :proxy_done
:no_proxy
echo Proxy: none - direct connection
:proxy_done

where uv >nul 2>nul
if not errorlevel 1 goto :have_uv
echo [1/3] Installing uv ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "if ($env:HTTPS_PROXY) { [Net.WebRequest]::DefaultWebProxy = New-Object Net.WebProxy $env:HTTPS_PROXY }; irm https://astral.sh/uv/install.ps1 | iex"
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
echo Setup failed. Check the network / proxy.
echo If you are behind a proxy, put its address in proxy.txt next to this file
echo (one line, e.g. http://proxy.example.com:8080) and run this file again.
pause
exit /b 1

rem ---- sets PX to the proxy URL, or leaves it empty -------------------
:find_proxy
set "PX="
if not "%~1"=="" set "PX=%~1"
if defined PX goto :eof
if defined HTTPS_PROXY set "PX=%HTTPS_PROXY%"
if defined PX goto :eof
if exist "%HERE%proxy.txt" for /f "usebackq eol=# tokens=*" %%p in ("%HERE%proxy.txt") do if not defined PX set "PX=%%p"
if defined PX goto :eof
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$u = [Uri]'https://github.com'; $p = [Net.WebRequest]::GetSystemWebProxy().GetProxy($u); if ($p.Host -ne $u.Host) { $p.AbsoluteUri.TrimEnd('/') }"`) do set "PX=%%p"
goto :eof
