@echo off
REM ============================================================================
REM HOUSEHOLD INVENTORY ^& SHOPPING HELPER DASHBOARD
REM ============================================================================
REM Description: This batch file runs the Streamlit dashboard for managing
REM household grocery inventory and shopping lists.
REM
REM Usage: Simply double-click this bat file.
REM ============================================================================

echo [INFO] Starting Household Inventory ^& Shopping Helper...

REM Set the path to the script directory (portable — works from any checkout/worktree)
set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] .venv missing. Make sure it exists at %SCRIPT_DIR%.venv
    exit /b 1
)

echo [INFO] Changing to script directory: "%SCRIPT_DIR%"
cd /d "%SCRIPT_DIR%"
if errorlevel 1 (
    echo [ERROR] Failed to change directory.
    exit /b 1
)

echo [INFO] Running app.py with Streamlit...
echo [INFO] The dashboard should open in your default browser.
"%VENV_PY%" -m streamlit run app/app.py --browser.gatherUsageStats false --server.headless false --server.address 0.0.0.0

if errorlevel 1 (
    echo [ERROR] Dashboard failed with error code %errorlevel%
    echo [INFO] Press any key to see error details...
    pause >nul
    goto :eof
)

echo [INFO] Dashboard closed.
