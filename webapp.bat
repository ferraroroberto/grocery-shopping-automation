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

set "CERT_DIR=%SCRIPT_DIR%webapp\certificates"
set "CERT=%CERT_DIR%\cert.pem"
set "KEY=%CERT_DIR%\key.pem"

if not exist "%CERT%" if exist "%SCRIPT_DIR%certificates\cert.pem" (
    set "CERT=%SCRIPT_DIR%certificates\cert.pem"
    set "KEY=%SCRIPT_DIR%certificates\key.pem"
)

REM Bind address from config\webapp_config.json ("<host> <port>"); if the read
REM fails (error shown above) fall back to the defaults.
set "BIND_HOST=0.0.0.0"
set "BIND_PORT=8502"
for /f "usebackq tokens=1,2" %%a in (`"%VENV_PY%" -m src.webapp_config`) do (
    set "BIND_HOST=%%a"
    set "BIND_PORT=%%b"
)

REM Auto-renew Tailscale cert if expiring within 30 days.
"%VENV_PY%" "%SCRIPT_DIR%scripts\gen_tailscale_cert.py" --check

if exist "%CERT%" (
    echo [INFO] Starting HTTPS FastAPI webapp on %BIND_HOST%:%BIND_PORT%.
    "%VENV_PY%" -m uvicorn app.api:app --host %BIND_HOST% --port %BIND_PORT% --ssl-keyfile "%KEY%" --ssl-certfile "%CERT%"
) else (
    echo [INFO] No HTTPS cert found; starting HTTP FastAPI webapp on %BIND_HOST%:%BIND_PORT%.
    echo        Run ^& .\.venv\Scripts\python.exe src\gen_ssl_cert.py to enable HTTPS.
    "%VENV_PY%" -m uvicorn app.api:app --host %BIND_HOST% --port %BIND_PORT%
)

exit /b %ERRORLEVEL%
