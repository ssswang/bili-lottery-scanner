@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo 未找到 Python。请先安装 Python 3.8 或更高版本，并勾选“Add Python to PATH”。
        pause
        exit /b 1
    )
    set "PYTHON_CMD=python"
)

echo 正在安装项目依赖…
call %PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 (
    echo 依赖安装失败，请检查网络连接和 Python 环境后重试。
    pause
    exit /b 1
)

set /p INSTALL_BROWSER=是否安装 Top 3 工具需要的 Chromium？[y/N]: 
if /I "%INSTALL_BROWSER%"=="Y" (
    echo 正在安装 Playwright Chromium…
    call %PYTHON_CMD% -m playwright install chromium
    if errorlevel 1 (
        echo Chromium 安装失败；红包 WS 监视器仍可使用。
    )
)

echo.
echo 安装完成。
echo 启动红包监视器：ws_rank_red_packet_scanner.bat
pause
