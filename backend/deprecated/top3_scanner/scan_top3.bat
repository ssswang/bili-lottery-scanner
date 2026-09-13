@echo off
chcp 65001 >nul
cd /d "%~dp0\..\..\.."
python backend\deprecated\top3_scanner\scan_top3.py
pause
