# B Zhan Lottery API Scanner

A browser-free monitoring tool for B Zhan live-stream lotteries. It uses the official B Zhan Web QR login to create an isolated session, requests `getLotteryInfoWeb` directly, scans enabled room-ranking sources, and reports matching red packets and anchor lotteries to the console or Discord.

## Features

- Official Web QR login without reading an existing browser Cookie store.
- Independently enables or disables the Hot Rank source and configured category-ranking sources.
- Reads four configured `parent_area_id=1` category lists, the radio list, and the virtual-streamer list. The four category lists and radio list use their first 50 returned rooms.
- Merges room sources by room ID while retaining all reported rank positions.
- Scans sequentially at a configurable interval without concurrent room requests.
- Applies a shared request limiter: at most 450 B Zhan API requests per rolling 10-minute window, paced at roughly 1.33 seconds or slower.
- Parses red-packet average value, maximum value, entry requirements, draw time, and anchor lotteries.
- Shows the host name and ranking at the end of lottery output; Discord lottery alerts include the same information.
- Optional Discord Webhook notifications and Windows beep alerts.
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
HOT_RANK_LIMIT=100
SCAN_HOT_RANK=1
SCAN_POPULAR_RANKS=1
ROOM_INTERVAL_SECONDS=3
RISK_BACKOFF_SECONDS=60
RED_ALERT_AVG_THRESHOLD=3
PURPLE_ALERT_THRESHOLD=9
BEEP_ENABLED=1

DISCORD_ENABLED=0
DISCORD_WEBHOOK=""
```

To enable Discord notifications, set:

```ini
DISCORD_ENABLED=1
DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
```

`config.txt`, the login session, and the QR image are excluded from Git.

## Login

Run:

```bat
python qr_login.py
```

The script generates and opens `qr_login.png`. Use the **Scan** feature in the B Zhan mobile app to scan the image and confirm the login on your phone.

On success, the login session is saved to `lotteryapi_session.json`. It contains Cookies and a refresh token—do not share or commit this file.

## Start scanning

```bat
python lotteryapi_scanner.py
```

The scanner continuously performs the following steps:

1. Refresh the ticket and WBI signing keys for the cycle.
2. Refresh each enabled ranking source, then merge duplicate room IDs.
3. Request each room sequentially using `ROOM_INTERVAL_SECONDS`.
4. Refresh the enabled sources and immediately start the next cycle.

There is no normal cycle delay. The scanner waits for one room interval only when every enabled source returns no rooms.

Temporarily override scan parameters:

```bat
python lotteryapi_scanner.py --limit 40 --room-interval 8
```

`--limit` applies to the Hot Rank source. Category-ranking sources follow their configured source rules.

You can also temporarily override the Discord Webhook; this automatically enables notifications:

```bat
python lotteryapi_scanner.py --discord-webhook "https://discord.com/api/webhooks/..."
```

## Hot Rank Top 3

`scan_top3.py` is separate from the direct API scanner. It starts a headless Chromium browser at `:00:05` of every hour, reads the first three entries from the B Zhan Hot Rank page, converts each page UID to a live-room ID through the room mapping API, and prints/sends the live-room ID and anchor name.

```bat
python scan_top3.py
```

Use `scan_top3.bat` on Windows if you prefer a double-click launcher.

## Single-room test

```bat
python b_api.py 1700657229
```

This requests one specified room and prints the API response. Use it to verify that the login session works before starting continuous scanning.

## Configuration

| Key | Description | Default |
| --- | --- | --- |
| `SCAN_HOT_RANK` | Scan the Hot Rank room list: `1` enabled, `0` disabled | `1` |
| `SCAN_POPULAR_RANKS` | Scan the configured category-rank room lists: `1` enabled, `0` disabled | `1` |
| `HOT_RANK_LIMIT` | Maximum eligible Hot Rank rooms per cycle | `100` |
| `ROOM_INTERVAL_SECONDS` | Delay between room requests; minimum allowed value is `3` seconds | `3` |
| `RISK_BACKOFF_SECONDS` | Cooldown after `-352` or an authentication failure | `60` |
| `RED_ALERT_AVG_THRESHOLD` | Red-packet average battery-value alert threshold | `3` |
| `PURPLE_ALERT_THRESHOLD` | Anchor-lottery total battery-value alert threshold | `9` |
| `BEEP_ENABLED` | Windows sound alert switch: `1` enabled, `0` disabled | `1` |
| `DISCORD_ENABLED` | Discord notification switch: `1` enabled, `0` disabled | `0` |
| `DISCORD_WEBHOOK` | Discord Webhook URL | Empty |

## Risk control and troubleshooting

- If the API returns `-352`, the scanner stops the current cycle and enters the configured cooldown (60 seconds by default). Do not lower the request interval or repeatedly restart the script to continue requesting.
- The scanner attempts to fill missing `buvid3`, `buvid4`, and `b_nut` in an otherwise valid logged-in session using B Zhan's public device endpoints. It cannot replace the required `SESSDATA` and `bili_jct` login Cookies.
- If Cookies need refresh or the login expires, run `python qr_login.py` again.
- This tool does not automate captchas, `v_voucher`, or other manual verification.
- The login QR code must be scanned with the B Zhan mobile app. Do not open the QR URL directly in a phone browser.

## Project structure

```text
qr_login.py             QR login and isolated session storage
settings.py             Login session read/write helpers
config.py               config.txt parsing and default values
b_api.py                Device-Cookie bootstrap, WBI signing, ticket refresh, and API requests
lotteryapi_scanner.py   Configured rank-source polling and lottery parsing
discord_notifier.py     Discord notifications
scan_top3.py            Hourly page Hot Rank Top 3 scanner with UID-to-room mapping (Playwright)
scan_top3.bat           Windows launcher for scan_top3.py
config.txt.sample       Sample configuration
```

The previous full Playwright-based scanner remains in `legacy/` and is independent from the direct API scanner. The Top 3 scanner is the only current root-level tool that uses Playwright.
