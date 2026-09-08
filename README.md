# B Zhan WebSocket Red-Packet Monitor

A browser-free monitor for B Zhan live-stream red packets. It uses one official Web QR-login session, discovers rooms from category rankings, and receives qualifying red-packet events through live-room WebSockets.

## Features

- One required QR-login account: `acct1`.
- Merges Hot Rank and category-ranking sources by room ID, then keeps up to 600 live-room WebSocket connections.
- Refreshes the ranking list every three minutes; rooms leaving the list are marked removable and are retained until offline or their slot is needed.
- Only evaluates `POPULARITY_RED_POCKET_START`, which includes the total value and award count needed to calculate the packet average.
- Outputs and optionally sends Discord notifications only for packets meeting the configured average threshold.
- Limits `getDanmuInfo` token requests to 20 per minute by default.
- Includes an independent, hourly Playwright scanner for the B Zhan page Hot Rank Top 3, reported with live-room IDs.

## Requirements

- Windows
- Python 3.8 or later
- A B Zhan mobile app that can scan QR codes

Install dependencies:

```bat
pip install -r requirements.txt
```

The Top 3 scanner also needs the Playwright Chromium browser:

```bat
playwright install chromium
```

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

1. Uses `acct1` to refresh Hot Rank and category-ranking sources, deduplicating rooms before selecting up to 600 rooms.
2. Gets a WebSocket token for each selected room and listens only for red-packet start events.
3. Prints a packet only when its average value is at least 10 batteries.
4. Refreshes the ranking list every three minutes and updates the monitored rooms.

## Hot Rank Top 3

`scan_top3.py` starts a headless Chromium browser at `:00:05` of every hour, reads the first three entries from the B Zhan Hot Rank page, converts each page UID to a live-room ID through the room mapping API, and prints/sends the live-room ID and anchor name.

```bat
python scan_top3.py
```

Use `scan_top3.bat` on Windows if you prefer a double-click launcher.

## Live WebSocket red-packet watcher

`ws_red_packet_watcher.py` keeps one WebSocket connection open for a selected live room and prints qualifying real-time red-packet start events. It reuses a named QR-login session and reconnects safely after a transient disconnect.

```bat
python ws_red_packet_watcher.py 1700657229 --account acct1
```

Run `ws_red_packet_watcher.bat` for an interactive Windows launcher. Install the newly added dependencies once with `pip install -r requirements.txt`.

### WebSocket category-rank red-packet scanner

`ws_rank_red_packet_scanner.py` combines Hot Rank and configured category-ranking sources with the WebSocket watcher, deduplicating rooms by ID. It only evaluates `POPULARITY_RED_POCKET_START`, because that event includes both the prize total and count needed for a package-average calculation. By default it monitors up to 600 ranked rooms and refreshes the rankings every three minutes. A room that leaves both ranking sources is marked removable, but is disconnected only after it goes offline or a new room needs its connection slot. It prints only red packets with an average value of at least 10 batteries and does not poll `getLotteryInfoWeb`.

```bat
python ws_rank_red_packet_scanner.py --account acct1 --max-connections 600 --hot-rank-limit 100 --min-average 10
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
scan_top3.py            Hourly page Hot Rank Top 3 scanner with UID-to-room mapping (Playwright)
scan_top3.bat           Windows launcher for scan_top3.py
ws_rank_red_packet_scanner.py  Category-rank WebSocket red-packet monitor
ws_rank_red_packet_scanner.bat Windows launcher for the WS monitor
config.txt.sample       Sample configuration
```

The previous full Playwright-based scanner remains in `legacy/`. The Top 3 scanner is the only current root-level tool that uses Playwright.
