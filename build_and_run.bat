@echo off
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION

set "VENV_PATH=venv"
set "PYTHON_CMD=python"

%PYTHON_CMD% --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11+ is required but was not found in PATH.
    echo Install Python and run this script again.
    pause
    exit /b 1
)

if not exist "%VENV_PATH%\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    %PYTHON_CMD% -m venv "%VENV_PATH%"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [INFO] Activating virtual environment...
call "%VENV_PATH%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

echo [INFO] Upgrading pip and installing dependencies...
python -m pip install --upgrade pip
if errorlevel 1 goto :pip_fail
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :pip_fail

echo [INFO] Building Windows executable with PyInstaller...
pyinstaller --noconfirm --onefile --windowed bot_app\main.py --name datamatrix_bot --collect-binaries pylibdmtx
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo [SUCCESS] Build complete. You can run dist\datamatrix_bot.exe to start the bot.
pause
exit /b 0

:pip_fail
echo [ERROR] Failed to install Python dependencies.
pause
exit /b 1
