# B Zhan Live Red-Packet Monitor

This project discovers live rooms in B Zhan parent areas 1, 5, and 9, then monitors them through live-room WebSockets. Qualifying red-packet events are printed, stored in SQLite, and can be sent to Discord.

## Highlights

- Refreshes the room queue every three minutes and deduplicates rooms by ID.
- Reuses saved WebSocket hosts and tokens before requesting new credentials.
- Uses `asyncio` and `aiohttp` to monitor many rooms concurrently.
- Stores packets, rooms, anchors, senders, and WebSocket credentials in `data/red_packet_monitor.db`.
- Includes a local dashboard for packets recorded during the last 24 hours.

## Requirements

- Windows
- Python 3.8 or later
- B Zhan mobile app for QR login

Install dependencies:

```bat
pip install -r requirements.txt
```


## Configuration

Create the local configuration file:

```bat
copy backend\config.txt.sample backend\config.txt
```

Key settings in `backend/config.txt`:

```ini
DISCORD_ENABLED=0
DISCORD_WEBHOOK=""
PROCESS_ANCHOR_LOTTERY=0
RED_PACKET_MIN_AVERAGE=10
ANCHOR_LOTTERY_MIN_AVERAGE=10
RED_PACKET_SOUND_ENABLED=0
ROOM_BLACKLIST=""
```

Set `RED_PACKET_SOUND_ENABLED=1` to play the Windows system alert sound when a qualifying red packet is first processed. It is disabled by default.

Set `DISCORD_ENABLED=1` and `DISCORD_WEBHOOK` to receive Discord notifications.

## Login

Log in with the required account, `acct1`:

```bat
python -m backend.qr_login --name acct1
```

Or double-click `qr_login_acct1.bat`. Scan `data/qr_login.png` with the B Zhan mobile app to save the session.

## Start monitoring

```bat
python -m backend.list_scanner --account acct1
```

Or double-click `list_scanner_acct1.bat`.

The scanner fetches pages from the parent-area room list, schedules cached-token rooms first, and listens for red-packet start events. By default, packets with an average value of at least 10 batteries are printed and notified.

Useful options:

| Option | Default | Description |
| --- | --- | --- |
| `--refresh-seconds` | `180` | Parent-area room queue refresh interval |
| `--min-average` | `10` | Minimum average packet value in batteries |
| `--max-get-danmu-info-per-minute` | `6` | Maximum new WebSocket credential requests per minute |
| `--get-danmu-info-jitter` | `1.5` | Maximum extra random delay between credential requests |
| `--max-active-rooms` | `1500` | Maximum concurrent room connections (upper limit: 1500) |
| `--discord-webhook` | empty | Temporary Discord webhook for this run |
| `--database` | `data/red_packet_monitor.db` | SQLite database path |

New room connections start at no more than 100 per minute. When all 1,500 slots are occupied, pending rooms wait for a slot; rooms with fewer than three high-energy users are disconnected when their `ONLINE_RANK_COUNT` event arrives.

## Dashboard

Start the dashboard in a second terminal:

```bat
python -m backend.dashboard
```

Or double-click `dashboard.bat`. Open `http://127.0.0.1:8765` to view active and expired red packets from the last 24 hours.


## Project layout

```text
backend/
  auth/                       API, user-session, and WebSocket authentication
  area_room_queue.py          Parent-area room discovery
  list_scanner.py             Multi-room WebSocket scanner
  room_watcher.py             Single-room watcher
  dashboard.py                Local dashboard service
  database.py                 SQLite persistence
  discord_notifier.py         Discord notifications
  config.txt.sample           Configuration template
  test/                       Manual diagnostic scripts
data/                         Local sessions, device data, and SQLite database
web/index.html                Dashboard page
list_scanner_acct1.bat        Scanner launcher
dashboard.bat                  Dashboard launcher
qr_login_acct1.bat            QR-login launcher
```
