@echo off
chcp 65001 >nul
REM Standalone FastAPI/PWA launcher for grocery (:8502).

setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] .venv missing. Install dependencies first.
    exit /b 1
)

cd /d "%SCRIPT_DIR%" || exit /b 1

set "CERT_DIR=%SCRIPT_DIR%webapp\certificates"
set "CERT=%CERT_DIR%\cert.pem"
set "KEY=%CERT_DIR%\key.pem"

if not exist "%CERT%" if exist "%SCRIPT_DIR%certificates\cert.pem" (
    set "CERT=%SCRIPT_DIR%certificates\cert.pem"
    set "KEY=%SCRIPT_DIR%certificates\key.pem"
)

REM Auto-renew Tailscale cert if expiring within 30 days.
"%VENV_PY%" "%SCRIPT_DIR%scripts\gen_tailscale_cert.py" --check

if exist "%CERT%" (
    echo [INFO] Starting HTTPS FastAPI webapp on :8502.
    "%VENV_PY%" -m uvicorn app.api:app --host 0.0.0.0 --port 8502 --ssl-keyfile "%KEY%" --ssl-certfile "%CERT%"
) else (
    echo [INFO] No HTTPS cert found; starting HTTP FastAPI webapp on :8502.
    echo        Run ^& .\.venv\Scripts\python.exe src\gen_ssl_cert.py to enable HTTPS.
    "%VENV_PY%" -m uvicorn app.api:app --host 0.0.0.0 --port 8502
)

exit /b %ERRORLEVEL%
