@echo off
cd /d "%~dp0"
echo ===================================================
echo  Room Indicator Streamer
echo ===================================================
echo.
echo Starting the local PC management page and guest HTTPS server.
echo The PC management page will open automatically.
echo.

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set "PYTHON_ARGS="
if exist "%PYTHON_EXE%" goto run

where python >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_EXE=python"
    goto run
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_EXE=py"
        set "PYTHON_ARGS=-3"
        goto run
    )
)

echo Python was not found.
echo Install Python 3.12 or later by following README.md.
pause
exit /b 1

:run
"%PYTHON_EXE%" %PYTHON_ARGS% -c "import flask, PIL, win32gui, qrcode, cheroot, cryptography" >nul 2>nul
if errorlevel 1 (
    echo Required Python packages are missing.
    echo Run:
    echo   "%PYTHON_EXE%" %PYTHON_ARGS% -m pip install -r requirements.txt
    pause
    exit /b 1
)

"%PYTHON_EXE%" %PYTHON_ARGS% app.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
