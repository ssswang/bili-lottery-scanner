# B Zhan WebSocket Red-Packet Monitor

A browser-free monitor for B Zhan live-stream red packets. It uses one official Web QR-login session, discovers rooms from category rankings, and receives qualifying red-packet events through live-room WebSockets.

## Features

- One required QR-login account: `acct1`.
- Pages through every live room in parent areas 1, 5, and 9, deduplicates by room ID, and adds them to the monitoring queue.
- Refreshes the parent-area room queue every three minutes; only rooms that go offline are disconnected.
- Uses one `asyncio` event loop with `aiohttp` WebSockets, avoiding one Python thread per room; database writes and Discord notifications are handled by independent queues.
- Keeps at most 2000 live WebSocket connections.
- Disconnects rooms when their WebSocket reports more than 500 high-energy users or 10,000 cumulative viewers.
- Only evaluates `POPULARITY_RED_POCKET_START`, which includes the total value and award count needed to calculate the packet average.
- Outputs and optionally sends Discord notifications only for packets meeting the configured average threshold.
- Stores qualifying packets, rooms, anchors, and senders in the local SQLite database `data/red_packet_monitor.db`; packets default to `is_battery_lottery = 1`.
- Persistently caches per-account, per-room WebSocket tokens and host lists; cache-hit reconnects do not call `getDanmuInfo`, while rejected or repeatedly unconfirmed tokens are refreshed.
- Spaces uncached `getDanmuInfo` token requests by at least 10 seconds with random jitter, capped at 6 per minute by default.

## Requirements

- Windows
- Python 3.8 or later
- A B Zhan mobile app that can scan QR codes

Install dependencies:

```bat
pip install -r requirements.txt
```

Or run `install.bat` to install the dependencies interactively.

## Initial configuration

Copy the sample configuration:

```bat
copy config.txt.sample backend\config.txt
```

Edit `backend/config.txt`. A minimal configuration is:

```ini
RISK_BACKOFF_SECONDS=60
DISCORD_ENABLED=0
DISCORD_WEBHOOK=""
PROCESS_ANCHOR_LOTTERY=0
RED_PACKET_MIN_AVERAGE=10
ANCHOR_LOTTERY_MIN_AVERAGE=10
```

To enable Discord notifications, set:

```ini
DISCORD_ENABLED=1
DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
```

`backend/config.txt`, the login session, and the QR image are excluded from Git. The saved login session contains sensitive Cookies; do not share or commit it.

## Login

The monitor requires only `acct1`:

```bat
python -m backend.qr_login --name acct1
```

Alternatively, double-click `qr_login_acct1.bat`.

Login generates and opens `data/qr_login.png`. Use the **Scan** feature in the B Zhan mobile app to scan the image and confirm the login on your phone. The session is saved as `data/lotteryapi_session_acct1.json`.

## Start WS rank monitoring

```bat
python -m backend.list_scanner --account acct1
```

The scanner continuously performs the following steps:

1. Uses `acct1` to refresh every page of the parent-area room lists for areas 1, 5, and 9, then queues newly discovered rooms after deduplication.
2. Uses cached WebSocket credentials first; uncached rooms obtain a token under the global rate limit and then listen for red-packet start events.
3. Prints a packet only when its average value is at least 10 batteries.
4. Refreshes the ranking list every three minutes and updates the monitored rooms.

## Local red-packet dashboard

While the scanner is running, start the read-only local dashboard in a second terminal:

```bat
python backend\dashboard.py
```

It opens `http://127.0.0.1:8765`, shows packets recorded in the last 24 hours, and refreshes the active and expired queues every two seconds. It reads only `data/red_packet_monitor.db` and does not affect scanning.

## Live WebSocket red-packet watcher

`backend/room_watcher.py` keeps one WebSocket connection open for a selected live room and prints qualifying real-time red-packet start events. It reuses a named QR-login session and reconnects safely after a transient disconnect.

```bat
python -m backend.room_watcher 1700657229 --account acct1
```

Install the required dependencies once with `pip install -r requirements.txt`.

### WebSocket category-rank red-packet scanner

`backend/list_scanner.py` pages through all live rooms in parent areas 1, 5, and 9, deduplicates them by room ID, and adds new rooms to the monitoring queue. It starts rooms with cached WebSocket credentials first, so rooms waiting for `getDanmuInfo` do not delay them. It evaluates `POPULARITY_RED_POCKET_START` for qualifying red packets and can optionally observe `ANCHOR_LOT_START` events when `PROCESS_ANCHOR_LOTTERY=1` is set in `backend/config.txt` (off by default). It refreshes the queue every three minutes. The red-packet and anchor-lottery average thresholds are controlled by `RED_PACKET_MIN_AVERAGE` and `ANCHOR_LOTTERY_MIN_AVERAGE`, both defaulting to 10 batteries. It does not poll `getLotteryInfoWeb`.

```bat
python -m backend.list_scanner --account acct1 --min-average 10
```

Use `list_scanner.bat` for the default Windows launcher.

Set `DISCORD_ENABLED=1` and `DISCORD_WEBHOOK` in `backend/config.txt` to send qualifying red-packet events to Discord. Alternatively, provide a one-run webhook with `--discord-webhook "https://discord.com/api/webhooks/..."`.


## Risk control and troubleshooting

- A `-352` response pauses the affected token request before retrying. Do not repeatedly restart the monitor while this is happening.
- If the login expires or `-352` persists, log in again with `python -m backend.qr_login --name acct1` and complete any required verification in the B Zhan app or website.
- This tool does not automate captchas, `v_voucher`, or other manual verification.
- The login QR code must be scanned with the B Zhan mobile app. Do not open the QR URL directly in a phone browser.

## Project structure

```text
backend/qr_login.py     Named-account QR login launcher
qr_login_acct1.bat      Windows launcher for acct1 QR login
backend/config.py       config.txt parsing and default values
backend/auth/api_auth.py API/WBI signing, device identity, ticket refresh, and rate limiting
backend/auth/user_auth.py Saved user sessions and QR-login entry points
backend/auth/ws_auth.py  getDanmuInfo token retrieval and -352 handling
data/                   Reserved local data directory (database and session artifacts)
backend/database.py     SQLite persistence layer
backend/discord_notifier.py  Discord notification service
backend/dashboard.py    Local dashboard HTTP service
web/index.html          Dashboard page
backend/area_room_queue.py Parent-area room queue source
backend/list_scanner.py         Category-rank WebSocket red-packet monitor
list_scanner_acct1.bat          Windows launcher for the WS monitor
dashboard.bat                   Windows launcher for the local dashboard
backend/room_watcher.py         Single-room WebSocket event watcher
test_ws_token_reuse.py          WebSocket token reuse test
config.txt.sample       Sample configuration; copy to backend/config.txt
```
