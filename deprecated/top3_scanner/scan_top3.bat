@echo off
chcp 65001 >nul
cd /d "%~dp0"
python scan_top3.py
pause
