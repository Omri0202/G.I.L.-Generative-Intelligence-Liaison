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
echo Installing the browser for shop comparison and orders...
python -m playwright install chromium
if errorlevel 1 (
    echo   WARNING: Couldn't install it. Run "pip install -r requirements.txt", then
    echo   "python -m playwright install chromium". G.I.L. works without it, but can't
    echo   compare shops that need JavaScript or get orders ready.
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
