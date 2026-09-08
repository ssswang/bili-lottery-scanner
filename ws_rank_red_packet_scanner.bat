@echo off
chcp 65001 >nul
python ws_rank_red_packet_scanner.py --account acct1 --max-connections 600 --hot-rank-limit 100 --refresh-seconds 180 --min-average 10 --max-get-danmu-info-per-minute 20
pause
