@echo off
cd /d "%~dp0\..\..\.."
python backend\deprecated\lottery_api_scanner\lotteryapi_scanner.py
pause
