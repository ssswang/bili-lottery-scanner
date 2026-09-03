@echo off
cd /d "%~dp0\.."
python -m proxy.fetch_proxies
pause
