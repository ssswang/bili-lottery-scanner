@echo off
chcp 65001 >nul
python ws_rank_red_packet_scanner.py --account acct1 --hot-rank-limit 100 --refresh-seconds 180 --min-average 10 --connection-start-interval 3 --connection-start-jitter 1 --max-get-danmu-info-per-minute 15 --get-danmu-info-jitter 1.5
pause
