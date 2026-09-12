@echo off
chcp 65001 >nul
python list_scanner.py --account acct1 --hot-rank-limit 100 --refresh-seconds 180 --min-average 10 --max-get-danmu-info-per-minute 10 --get-danmu-info-jitter 1.5 --max-active-rooms 2000
pause
