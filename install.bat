@echo off
setlocal

title Python & Playwright Installer

echo ============================================
echo Detecting Python...
echo ============================================

set PYTHON=

python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=python
)

if not defined PYTHON (
    py --version >nul 2>&1
    if not errorlevel 1 (
        set PYTHON=py
    )
)

if not defined PYTHON (
    echo.
    echo ERROR: Python was not found.
    echo Install Python from https://www.python.org/downloads/
    echo Make sure to enable "Add Python to PATH".
    pause
    exit /b 1
)

echo Using %PYTHON%
%PYTHON% --version

echo.
echo ============================================
echo Upgrading pip...
echo ============================================

%PYTHON% -m pip install --upgrade pip

echo.
echo ============================================
echo Creating requirements.txt...
echo ============================================

(
echo playwright
) > requirements.txt

echo.
echo ============================================
echo Installing Python packages...
echo ============================================

%PYTHON% -m pip install -r requirements.txt

echo.
echo ============================================
echo Installing Playwright browsers...
echo ============================================

%PYTHON% -m playwright install

echo.
echo ============================================
echo Installation Complete!
echo ============================================

echo Installed package:
echo   - playwright
echo.


pause