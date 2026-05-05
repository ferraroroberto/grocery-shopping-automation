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

REM Set the path to the virtual environment
set "VENV_DIR=E:\automation\grocery-shopping-automation\.venv"

REM Set the path to the grocery scripts
set "SCRIPT_DIR=E:\automation\grocery-shopping-automation"

echo [INFO] Activating virtual environment...
call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment. Make sure it exists at %VENV_DIR%
    echo [INFO] Attempting to continue without virtual environment activation...
)

echo [INFO] Changing to script directory: "%SCRIPT_DIR%"
cd /d "%SCRIPT_DIR%"
if errorlevel 1 (
    echo [ERROR] Failed to change directory.
    exit /b 1
)

echo [INFO] Running app.py with Streamlit...
echo [INFO] The dashboard should open in your default browser.
python -m streamlit run app/app.py --browser.gatherUsageStats false --server.headless false --server.address 0.0.0.0

if errorlevel 1 (
    echo [ERROR] Dashboard failed with error code %errorlevel%
    echo [INFO] Press any key to see error details...
    pause >nul
    goto :eof
)

echo [INFO] Dashboard closed.
