# B Zhan WebSocket Red-Packet Monitor

A browser-free monitor for B Zhan live-stream red packets. It uses one official Web QR-login session, discovers rooms from category rankings, and receives qualifying red-packet events through live-room WebSockets.

## Features

- One required QR-login account: `acct1`.
- Merges Hot Rank and category-ranking sources by room ID, then monitors every discovered room.
- Refreshes the ranking list every three minutes; only rooms that go offline are disconnected.
- Only evaluates `POPULARITY_RED_POCKET_START`, which includes the total value and award count needed to calculate the packet average.
- Outputs and optionally sends Discord notifications only for packets meeting the configured average threshold.
- Stores qualifying packets, rooms, anchors, senders, and award details in the local SQLite database `red_packet_monitor.db`.
- Spaces `getDanmuInfo` token requests by at least four seconds with random jitter, capped at 15 per minute by default.

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
python ws_rank_red_packet_scanner.py --account acct1
```

The scanner continuously performs the following steps:

1. Uses `acct1` to refresh Hot Rank and category-ranking sources, deduplicating rooms before adding newly discovered rooms to monitoring.
2. Gets a WebSocket token for each selected room and listens only for red-packet start events.
3. Prints a packet only when its average value is at least 10 batteries.
4. Refreshes the ranking list every three minutes and updates the monitored rooms.

## Live WebSocket red-packet watcher

`ws_red_packet_watcher.py` keeps one WebSocket connection open for a selected live room and prints qualifying real-time red-packet start events. It reuses a named QR-login session and reconnects safely after a transient disconnect.

```bat
python ws_red_packet_watcher.py 1700657229 --account acct1
```

Run `ws_red_packet_watcher.bat` for an interactive Windows launcher. Install the newly added dependencies once with `pip install -r requirements.txt`.

### WebSocket category-rank red-packet scanner

`ws_rank_red_packet_scanner.py` combines Hot Rank and configured category-ranking sources with the WebSocket watcher, deduplicating rooms by ID. It only evaluates `POPULARITY_RED_POCKET_START`, because that event includes both the prize total and count needed for a package-average calculation. It refreshes the rankings every three minutes and adds newly discovered rooms to monitoring. A room is disconnected only after its offline event arrives. It prints only red packets with an average value of at least 10 batteries and does not poll `getLotteryInfoWeb`.

```bat
python ws_rank_red_packet_scanner.py --account acct1 --hot-rank-limit 100 --min-average 10
```

Use `ws_rank_red_packet_scanner.bat` for the default Windows launcher.

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
ws_rank_red_packet_scanner.py  Category-rank WebSocket red-packet monitor
ws_rank_red_packet_scanner.bat Windows launcher for the WS monitor
config.txt.sample       Sample configuration
```
