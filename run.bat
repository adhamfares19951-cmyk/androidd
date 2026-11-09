@echo off
cd /d "%~dp0"
title Android Doctor Pro
echo Starting Android Doctor Pro...
python main.py --lang ar
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo An error occurred.
    pause
)
