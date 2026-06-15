@echo off
title G.I.L. — First Time Setup
echo ====================================================
echo   PROJECT G.I.L. — FIRST TIME SETUP
echo ====================================================
echo.
echo Registering G.I.L. for automatic Windows startup...
python "%~dp0_register_startup.py"
if errorlevel 1 (
    echo   ERROR: Registration failed. Make sure Python is installed.
    pause
    exit /b 1
)
echo.
echo Starting G.I.L. in the background now...
start "" pythonw "%~dp0gil.pyw"
echo.
echo ====================================================
echo   DONE.
echo   G.I.L. will now start automatically with Windows.
echo   Say "Hello G.I.L." to activate it anytime.
echo ====================================================
echo.
pause
