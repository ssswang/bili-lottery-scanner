@echo off
chcp 65001 >nul
set /p ROOM_ID=请输入要监听的直播间房间号: 
python room_watcher.py %ROOM_ID% --account acct1
pause
