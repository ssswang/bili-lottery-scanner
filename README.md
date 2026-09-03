# B Zhan Lottery API Scanner

A browser-free monitoring tool for B Zhan live-stream lotteries. It uses official B Zhan Web QR login sessions, requests `getLotteryInfoWeb` directly, scans enabled room-ranking sources, and reports matching red packets and optional anchor lotteries to the console or Discord.

## Features

- Official Web QR login without reading an existing browser Cookie store.
- `acct1` and `acct2` are required direct sessions; an optional `acct3` session can join them, for a maximum of three concurrent room scanners. `acct1` also requests the ranking lists.
- Before scanning, the program verifies that active accounts do not share `buvid3`, `buvid4`, `buvid_fp`, `_uuid`, or `b_lsid` device identifiers.
- Independently enables or disables the Hot Rank source and configured category-ranking sources.
- Reads four configured `parent_area_id=1` category lists, the radio list, and the virtual-streamer list. The four category lists and radio list use their first 50 returned rooms.
- Merges room sources by room ID while retaining all reported rank positions.
- Splits rooms between two or three active accounts and scans all partitions concurrently. Each account waits a random 3.0–3.9 seconds between room API requests by default.
- Applies a per-session request limiter: at most 450 B Zhan API requests per rolling 10-minute window.
- Parses red-packet average value, maximum value, entry requirements, draw time, and optional anchor lotteries. Console output includes the red-packet draw condition.
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
PROCESS_ANCHOR_LOTTERY=0
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

The scanner requires both `acct1` and `acct2`. You may also add the optional third concurrent account, `acct3`:

```bat
python qr_login.py --name acct1
python qr_login.py --name acct2
python qr_login.py --name acct3
```

Alternatively, double-click `qr_login_acct1.bat`, `qr_login_acct2.bat`, and optionally `qr_login_acct3.bat`.

Each login generates and opens `qr_login.png`. Use the **Scan** feature in the B Zhan mobile app to scan the image and confirm the login on your phone. Sessions are saved as `lotteryapi_session_acct1.json`, `lotteryapi_session_acct2.json`, and optionally `lotteryapi_session_acct3.json`; they contain Cookies and refresh tokens—do not share or commit them.

## Start scanning

```bat
python lotteryapi_scanner.py
```

The scanner continuously performs the following steps:

1. Build isolated, direct sessions for `acct1`, `acct2`, and optional `acct3`, then verify their device identifiers are distinct.
2. Use `acct1` to refresh WBI signing keys and each enabled ranking source, then merge duplicate room IDs.
3. Split rooms between the active accounts (up to `acct3`); each account requests its partition with a random interval from `ROOM_INTERVAL_SECONDS` to `ROOM_INTERVAL_SECONDS + 0.9` seconds.
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
| `ROOM_INTERVAL_SECONDS` | Base delay between room requests; each request uses a random value from the base to base + 0.9 seconds, with a minimum base of `3` | `3` |
| `RISK_BACKOFF_SECONDS` | Cooldown after `-352` or an authentication failure | `60` |
| `RED_ALERT_AVG_THRESHOLD` | Red-packet average battery-value alert threshold | `3` |
| `PURPLE_ALERT_THRESHOLD` | Anchor-lottery total battery-value alert threshold | `9` |
| `PROCESS_ANCHOR_LOTTERY` | Process anchor-lottery events: `1` enabled, `0` disabled | `1` |
| `BEEP_ENABLED` | Windows sound alert switch: `1` enabled, `0` disabled | `1` |
| `DISCORD_ENABLED` | Discord notification switch: `1` enabled, `0` disabled | `0` |
| `DISCORD_WEBHOOK` | Discord Webhook URL | Empty |

## Risk control and troubleshooting

- A single `-352` skips that room and enters the configured cooldown. Two consecutive `-352` responses stop that account's partition for the current cycle. Do not lower the request interval or repeatedly restart the script to continue requesting.
- The scanner fills missing `buvid3`, `buvid4`, and `b_nut`, generates per-account `_uuid`, `b_lsid`, and `buvid_fp`, then refuses to scan if any active accounts share a key device identifier. It cannot replace the required `SESSDATA` and `bili_jct` login Cookies.
- If Cookies need refresh, a login expires, or the device-ID check fails, log in to the affected account again with `python qr_login.py --name acct1`, `acct2`, or `acct3`.
- This tool does not automate captchas, `v_voucher`, or other manual verification.
- The login QR code must be scanned with the B Zhan mobile app. Do not open the QR URL directly in a phone browser.

## Project structure

```text
qr_login.py             Named-account QR login launcher
qr_login_acct1.bat      Windows launcher for acct1 QR login
qr_login_acct2.bat      Windows launcher for acct2 QR login
qr_login_acct3.bat      Windows launcher for optional acct3 QR login
config.py               config.txt parsing and default values
auth_manager.py         QR session storage, device cookies, WBI signing, ticket refresh, and rate limiting
b_api.py                Lottery and category-ranking API requests
room_lists.py           Rank-source room collection, normalization, and combined-list building
lotteryapi_scanner.py   Lottery polling and red-packet/anchor-lottery event parsing
discord_notifier.py     Discord notifications
scan_top3.py            Hourly page Hot Rank Top 3 scanner with UID-to-room mapping (Playwright)
scan_top3.bat           Windows launcher for scan_top3.py
config.txt.sample       Sample configuration
```

The previous full Playwright-based scanner remains in `legacy/` and is independent from the direct API scanner. The Top 3 scanner is the only current root-level tool that uses Playwright.
