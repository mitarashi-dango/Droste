@echo off
cd /d "%~dp0"
echo ===================================================
echo  Droste - First-time setup
echo ===================================================
echo.

set "BOOTSTRAP_PYTHON="
set "BOOTSTRAP_ARGS="
set "PYTHON_INSTALLER=python-3.12.10-amd64.exe"
set "PYTHON_INSTALL_DIR=%LocalAppData%\Programs\Python\Python312"

where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -c "import sys,struct; raise SystemExit(0 if sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "BOOTSTRAP_PYTHON=py"
        set "BOOTSTRAP_ARGS=-3.12"
    )
)

if not defined BOOTSTRAP_PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys,struct; raise SystemExit(0 if sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
        if not errorlevel 1 set "BOOTSTRAP_PYTHON=python"
    )
)

if not defined BOOTSTRAP_PYTHON if exist "%PYTHON_INSTALL_DIR%\python.exe" (
    "%PYTHON_INSTALL_DIR%\python.exe" -c "import sys,struct; raise SystemExit(0 if sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
    if not errorlevel 1 set "BOOTSTRAP_PYTHON=%PYTHON_INSTALL_DIR%\python.exe"
)

if not defined BOOTSTRAP_PYTHON if exist "%ProgramFiles%\Python312\python.exe" (
    "%ProgramFiles%\Python312\python.exe" -c "import sys,struct; raise SystemExit(0 if sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
    if not errorlevel 1 set "BOOTSTRAP_PYTHON=%ProgramFiles%\Python312\python.exe"
)

if not defined BOOTSTRAP_PYTHON goto python_missing
goto python_ready

:python_missing
if not exist "%PYTHON_INSTALLER%" goto python_installer_missing

echo Python 3.12 64-bit is not installed.
echo The bundled official Python installer can install it for this Windows user.
echo Existing Python versions will not be removed.
echo.
choice /C YN /N /M "Open the bundled Python installer? [Y/N]: "
if errorlevel 2 exit /b 1

powershell.exe -NoProfile -ExecutionPolicy Bypass -File verify_python_installer.ps1 -InstallerPath "%PYTHON_INSTALLER%"
if errorlevel 1 goto python_installer_invalid

start "" /wait "%PYTHON_INSTALLER%" InstallAllUsers=0 Include_launcher=1 InstallLauncherAllUsers=0 Include_test=0 AssociateFiles=0 Shortcuts=0 PrependPath=0 TargetDir="%PYTHON_INSTALL_DIR%" SimpleInstall=1 SimpleInstallDescription="Required by Droste"
if errorlevel 1 goto python_install_failed

if not exist "%PYTHON_INSTALL_DIR%\python.exe" goto python_install_failed
"%PYTHON_INSTALL_DIR%\python.exe" -c "import sys,struct; raise SystemExit(0 if sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
if errorlevel 1 goto python_install_failed
set "BOOTSTRAP_PYTHON=%PYTHON_INSTALL_DIR%\python.exe"

:python_ready

if not exist ".venv\Scripts\python.exe" (
    echo Creating the private Python environment...
    "%BOOTSTRAP_PYTHON%" %BOOTSTRAP_ARGS% -m venv .venv
    if errorlevel 1 goto setup_failed
)

".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)" >nul 2>nul
if errorlevel 1 goto stale_environment

echo Installing verified dependency versions...
if exist "wheelhouse" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File verify_wheelhouse.ps1
    if errorlevel 1 goto setup_failed
    ".venv\Scripts\python.exe" -m pip install --no-index --find-links wheelhouse -r requirements.lock.txt
) else (
    ".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
)
if errorlevel 1 goto setup_failed

".venv\Scripts\python.exe" -c "import flask,PIL,win32gui,qrcode,cheroot,cryptography" >nul 2>nul
if errorlevel 1 goto setup_failed

powershell.exe -NoProfile -ExecutionPolicy Bypass -File create_shortcut.ps1 -ProjectRoot "%~dp0"
if errorlevel 1 (
    echo Warning: The Droste desktop shortcut could not be created.
    echo You can still start Droste by double-clicking regain.bat.
)

echo.
echo Setup completed successfully.
echo Double-click the Droste desktop shortcut to start.
echo You can also double-click regain.bat in this folder.
pause
exit /b 0

:python_installer_missing
echo Python 3.12 64-bit was not found, and the bundled installer is missing.
echo Obtain a complete Droste distribution ZIP and try again.
pause
exit /b 1

:python_installer_invalid
echo The bundled Python installer failed its security check.
echo Do not run it. Obtain a fresh Droste distribution ZIP.
pause
exit /b 1

:python_install_failed
echo Python 3.12 installation was cancelled or did not complete.
echo Run setup.bat again when installation is complete.
pause
exit /b 1

:stale_environment
echo The .venv folder was created on another PC or with another Python version.
echo Delete only the .venv folder, then run setup.bat again.
pause
exit /b 1

:setup_failed
echo.
echo Setup failed. Check the message above and your network connection.
pause
exit /b 1
