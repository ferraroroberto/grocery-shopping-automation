@echo off
REM ============================================================================
REM HOUSEHOLD INVENTORY ^& SHOPPING HELPER DASHBOARD
REM ============================================================================
REM Description: Runs the Streamlit dashboard for managing household grocery
REM              inventory and shopping lists.
REM
REM Usage: Double-click this bat file from the repo root.
REM ============================================================================

setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"

echo [INFO] Starting Household Inventory ^& Shopping Helper...

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found at "%VENV_DIR%".
    echo [INFO] Create it first:  python -m venv .venv ^&^& .\.venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

echo [INFO] Activating virtual environment...
call "%VENV_DIR%\Scripts\activate.bat"

echo [INFO] Changing to repo directory: "%SCRIPT_DIR%"
cd /d "%SCRIPT_DIR%"
if errorlevel 1 (
    echo [ERROR] Failed to change directory.
    exit /b 1
)

echo [INFO] Running app/app.py with Streamlit...
echo [INFO] The dashboard should open in your default browser.
python -m streamlit run app/app.py --browser.gatherUsageStats false --server.headless false --server.address 0.0.0.0

if errorlevel 1 (
    echo [ERROR] Dashboard failed with error code %errorlevel%
    echo [INFO] Press any key to see error details...
    pause >nul
    goto :eof
)

echo [INFO] Dashboard closed.
endlocal
