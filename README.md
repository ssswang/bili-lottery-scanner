# B Zhan WebSocket Red-Packet Monitor

A browser-free monitor for B Zhan live-stream red packets. It uses one official Web QR-login session, discovers rooms from category rankings, and receives qualifying red-packet events through live-room WebSockets.

## Features

- One required QR-login account: `acct1`.
- Merges Hot Rank and category-ranking sources by room ID, then monitors every discovered room.
- Refreshes the ranking list every three minutes; only rooms that go offline are disconnected.
- Keeps at most 2000 live WebSocket connections; a newly discovered room replaces the longest-connected room when full.
- Disconnects rooms when their WebSocket reports more than 500 high-energy users or 10,000 cumulative viewers.
- Only evaluates `POPULARITY_RED_POCKET_START`, which includes the total value and award count needed to calculate the packet average.
- Outputs and optionally sends Discord notifications only for packets meeting the configured average threshold.
- Stores qualifying packets, rooms, anchors, and senders in the local SQLite database `red_packet_monitor.db`; packets default to `is_battery_lottery = 1`.
- Persistently caches per-account, per-room WebSocket tokens and host lists; cache-hit reconnects do not call `getDanmuInfo`, while rejected or repeatedly unconfirmed tokens are refreshed.
- Spaces uncached `getDanmuInfo` token requests by at least 7.5 seconds with random jitter, capped at 8 per minute by default.

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
copy config.txt.sample config.txt
```

Edit `config.txt`. A minimal configuration is:

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

`config.txt`, the login session, and the QR image are excluded from Git. The saved login session contains sensitive Cookies; do not share or commit it.

## Login

The monitor requires only `acct1`:

```bat
python qr_login.py --name acct1
```

Alternatively, double-click `qr_login_acct1.bat`.

Login generates and opens `qr_login.png`. Use the **Scan** feature in the B Zhan mobile app to scan the image and confirm the login on your phone. The session is saved as `lotteryapi_session_acct1.json`.

## Start WS rank monitoring

```bat
python list_scanner.py --account acct1
```

The scanner continuously performs the following steps:

1. Uses `acct1` to refresh Hot Rank and category-ranking sources, deduplicating rooms before adding newly discovered rooms to monitoring.
2. Gets a WebSocket token for each selected room and listens only for red-packet start events.
3. Prints a packet only when its average value is at least 10 batteries.
4. Refreshes the ranking list every three minutes and updates the monitored rooms.

## Live WebSocket red-packet watcher

`room_watcher.py` keeps one WebSocket connection open for a selected live room and prints qualifying real-time red-packet start events. It reuses a named QR-login session and reconnects safely after a transient disconnect.

```bat
python room_watcher.py 1700657229 --account acct1
```

Run `room_watcher.bat` for an interactive Windows launcher. Install the newly added dependencies once with `pip install -r requirements.txt`.

### WebSocket category-rank red-packet scanner

`list_scanner.py` combines Hot Rank and configured category-ranking sources with the WebSocket watcher, deduplicating rooms by ID. It starts rooms with cached WebSocket credentials first, so rooms waiting for `getDanmuInfo` do not delay them. It evaluates `POPULARITY_RED_POCKET_START` for qualifying red packets and can optionally observe `ANCHOR_LOT_START` events when `PROCESS_ANCHOR_LOTTERY=1` is set in `config.txt` (off by default). It refreshes the rankings every three minutes and adds newly discovered rooms to monitoring. The red-packet and anchor-lottery average thresholds are controlled by `RED_PACKET_MIN_AVERAGE` and `ANCHOR_LOTTERY_MIN_AVERAGE`, both defaulting to 10 batteries. It does not poll `getLotteryInfoWeb`.

```bat
python list_scanner.py --account acct1 --hot-rank-limit 100 --min-average 10
```

Use `list_scanner.bat` for the default Windows launcher.

Set `DISCORD_ENABLED=1` and `DISCORD_WEBHOOK` in `config.txt` to send qualifying red-packet events to Discord. Alternatively, provide a one-run webhook with `--discord-webhook "https://discord.com/api/webhooks/..."`.


## Risk control and troubleshooting

- A `-352` response pauses the affected token request before retrying. Do not repeatedly restart the monitor while this is happening.
- If the login expires or `-352` persists, log in again with `python qr_login.py --name acct1` and complete any required verification in the B Zhan app or website.
- This tool does not automate captchas, `v_voucher`, or other manual verification.
- The login QR code must be scanned with the B Zhan mobile app. Do not open the QR URL directly in a phone browser.

## Project structure

```text
qr_login.py             Named-account QR login launcher
qr_login_acct1.bat      Windows launcher for acct1 QR login
config.py               config.txt parsing and default values
auth_manager.py         QR session storage, WBI signing, ticket refresh, and rate limiting
room_lists.py           Rank-source room collection, normalization, and combined-list building
discord_notifier.py     Discord notifications
list_scanner.py                 Category-rank WebSocket red-packet monitor
list_scanner.bat                Windows launcher for the WS monitor
room_watcher.py                 Single-room WebSocket event watcher
room_watcher.bat                Windows launcher for the single-room watcher
test_ws_token_reuse.py          WebSocket token reuse test
config.txt.sample       Sample configuration
```
