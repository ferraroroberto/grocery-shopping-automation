@echo off
chcp 65001 >nul
REM Start grocery FastAPI/PWA plus a named Cloudflare tunnel.

setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [ERROR] .venv missing. Install dependencies first.
    exit /b 1
)

where cloudflared >nul 2>&1
if errorlevel 1 (
    echo [ERROR] cloudflared not installed.
    echo   winget install Cloudflare.cloudflared
    pause
    exit /b 1
)

if not exist "%SCRIPT_DIR%webapp\cloudflared.yml" (
    echo [ERROR] webapp\cloudflared.yml missing.
    echo   Copy webapp\cloudflared.sample.yml to webapp\cloudflared.yml
    echo   and fill in your tunnel UUID and hostname.
    pause
    exit /b 1
)

cd /d "%SCRIPT_DIR%" || exit /b 1
"%VENV_PY%" scripts\run_named_tunnel.py
exit /b %ERRORLEVEL%
