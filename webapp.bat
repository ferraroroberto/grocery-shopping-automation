@echo off
chcp 65001 >nul
REM Standalone FastAPI/PWA launcher for grocery (default :8502; host/port follow
REM config\webapp_config.json, the same source the tray uses).

setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] .venv missing. Install dependencies first.
    exit /b 1
)

cd /d "%SCRIPT_DIR%" || exit /b 1

REM Auto-renew Tailscale cert if expiring within 30 days.
"%VENV_PY%" "%SCRIPT_DIR%scripts\gen_tailscale_cert.py" --check

REM Cert lookup, bind address and server come from src.uvicorn_launch, the
REM same command line the tray builds.
"%VENV_PY%" -m src.uvicorn_launch

exit /b %ERRORLEVEL%
