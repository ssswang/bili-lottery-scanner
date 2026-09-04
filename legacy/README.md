# Bilibili Live Lottery Scanner (Legacy)

`legacy/scan.py` is an independent Playwright-based scanner for Bilibili live-room red packets and anchor lotteries. It opens one visible Chromium browser, collects rooms from configured sources, then reads the room lottery API response while the page is open.

This is the legacy browser scanner. The repository-root `lotteryapi_scanner.py` is a separate direct-API program and has its own README and configuration.

## What it does

- Scans rooms listed in `CUSTOM_ROOM_IDS`, the Hot Rank list, and `CATEGORY_URLS`.
- Prints qualifying red-packet details, including the entry condition and draw time; it also processes anchor-lottery data.
- Keeps every visited, non-blacklisted live room open for at least 3 seconds.
- Repeats a full scan after a 60-second pause.
- Intercepts `getLotteryInfoWeb` responses first; the visual lottery-icon check is only a fallback when the response is late.
- Blocks streams and nonessential assets (`media`, fonts, `.m4s`, `.flv`, `.gif`, `.svg`, `.webp`, and analytics requests) to reduce page-load work. GeeTest and Bilibili passport resources remain available.
- Can play a Windows beep and send Discord alerts when enabled.

## Requirements

- Windows
- Python 3.8 or newer
- Playwright Chromium

Install dependencies from this folder:

```bat
pip install -r requirements.txt
playwright install chromium
```

`install.bat` performs the same setup on Windows.

## Configuration

Edit `config.txt` in this folder. Start from `config.txt.sample` if needed.

| Key | Meaning |
| --- | --- |
| `DISCORD_WEBHOOK` | Discord Webhook URL used when notifications are enabled. Keep it private. |
| `IM_SWITCH` | Discord notification switch: `1` on, `0` off. |
| `BEEP_SWITCH` | Windows beep switch: `1` on, `0` off. |
| `ROOM_COUNT` | Maximum number of rooms extracted from each configured category page. |
| `CATEGORY_URLS` | JSON array of Bilibili category-page URLs. |
| `CUSTOM_ROOM_IDS` | JSON array of room IDs to scan before rank and category rooms. |
| `BLACKLIST_ROOM_IDS` | JSON array of room IDs to skip. |
| `RED_ALERT_AVG_THRESHOLD` | Alert threshold for average red-packet battery value per winner. |
| `PURPLE_ALERT_THRESHOLD` | Alert threshold for total anchor-lottery battery value. |

Example:

```ini
IM_SWITCH=0
ROOM_COUNT=80
CUSTOM_ROOM_IDS=[]
BLACKLIST_ROOM_IDS=[]
RED_ALERT_AVG_THRESHOLD=4
PURPLE_ALERT_THRESHOLD=9000
BEEP_SWITCH=1
```

## Start

From the `legacy` folder, run:

```bat
python scan.py
```

Or double-click `scan.bat`.

## Login and verification

The browser starts visibly. Before a QR login has been handled in the current browser session, each newly opened page checks for a login panel and GeeTest verification.

If a Bilibili QR-login window appears, the scanner waits up to 7 seconds for it to render, plays an alert, and then pauses on that page until you scan and confirm the QR code in the Bilibili mobile app. Only after that window has actually appeared and been resolved does the scanner regard the session as logged in; later pages skip both the login-window and GeeTest checks.

If a GeeTest panel appears before login has been confirmed, the scanner alerts you and waits for manual completion for up to five minutes. It does not solve captchas automatically.

## Scan order

Each cycle runs in this order:

1. Rooms in `CUSTOM_ROOM_IDS`.
2. Eligible rooms from the Hot Rank API.
3. Rooms discovered from every URL in `CATEGORY_URLS`.
4. Wait 60 seconds, then begin the next cycle.

Rooms in `BLACKLIST_ROOM_IDS` are skipped without opening a live-room page.
