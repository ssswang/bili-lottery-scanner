# B Zhan Live Lottery Scanner

A monitoring and alert script for B Zhan live stream red packets without login, built with Python and Playwright. The script periodically scans custom live rooms, hot ranking streams, and specified live categories. It intercepts web API responses to analyze reward value and entry requirements, sending real-time Discord notifications for high-value rewards and alerting on script crashes.

---

## ✨ Features

* **Multi-Source Room Scanning**:
  * **Custom Room List**: Prioritizes scanning user-defined room IDs (`CUSTOM_ROOM_IDS`).
  * **Hot Ranking List**: Automatically fetches and scans top live streams via the B Zhan Hot Rank API.
  * **Category Pages**: Scrolls through specified category pages (`CATEGORY_URLS`) to discover active live streams.
* **Network Response Interception**:
  * Powered by Playwright Chromium automation.
  * Automatically detects red envelope icons, hovers over them to trigger JavaScript requests, and intercepts `getLotteryInfoWeb` API responses.
* **GeeTest Captcha Detection & Audio Alarm**:
  * Triggers a system beep (`winsound`) when a GeeTest captcha panel appears on screen, pausing execution until manually solved.
* **Discord Webhook Alerts**:
  * **Lottery Alert**: Sends formatted embed notifications for lotteries valued over 40 batteries (filtering out low-value filler rewards).
  * **Crash Alert**: Automatically catches unhandled runtime exceptions and posts the error stack trace to Discord.
* **External Configuration Support**:
  * Automatically reads key-value configuration from `config.txt` at launch.

---

## ⚙️ Configuration (`config.txt`)

The script parses `config.txt` located in the root directory:

| Key | Description | Default / Example |
| :--- | :--- | :--- |

| `IM_SWITCH` | Push notification toggle (`1` = Enabled, `0` = Disabled) | `1` |
| `DISCORD_WEBHOOK` | Discord Webhook URL for pushing notifications | `"https://discord.com/api/webhooks/..."` |
| `ROOM_COUNT` | Maximum number of rooms to extract per category page | `40` |
| `CATEGORY_URLS` | List of category page URLs (JSON Array format) | `["https://live.b.com/p/eden/area-tags?areaId=0&parentAreaId=1", ...]` |
| `CUSTOM_ROOM_IDS` | List of specific room IDs to scan first (JSON Array format) | `[]` |

---

## 🚀 Quick Start

### 1. Requirements
* Windows OS (required for native `winsound` audio alarms).
* Python 3.8 or higher.

### 2. One-Click Setup
Run `install.bat` on Windows to automatically install dependencies, download the Playwright Chromium browser binary, and generate a default `config.txt` file.

### 3. Manual Installation
1. Install Python package dependencies:
   ```bash
   pip install requests playwright
   ```
2. Install Playwright browser binaries:
   ```bash
   playwright install chromium
   ```
3. If you need discord notification, configure `config.txt` with `IM_SWITCH=1` AND your `DISCORD_WEBHOOK` URL.
4. Launch the script or double click scan.bat:
   ```bash
   python scan.py
   ```

---

## 🛡️ Exception & Risk Control Handling

* **GeeTest Captchas**: When `div.geetest_panel` appears, the script sounds a beep alarm and loops until you manually pass the verification.
* **Rate Limits**: If B Zhan triggers risk control code `-352`, the scanner will log the warning and halt the current category scan pass.
* **Fatal Crashes**: Any top-level unhandled exception triggers `send_crash_notification` to forward the error log directly to Discord.