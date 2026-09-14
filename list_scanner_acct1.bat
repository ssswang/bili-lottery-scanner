@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m backend.list_scanner --account acct1 --refresh-seconds 180 --min-average 10 --max-get-danmu-info-per-minute 6 --get-danmu-info-jitter 1.5 --max-active-rooms 4000
pause
