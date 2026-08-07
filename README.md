# B Zhan Live Lottery Scanner

A monitoring and alert tool for B Zhan live stream red packets and anchor lotteries, built with Python and Playwright. It now has two independent scripts: one for Hot Rank rooms and one for configured category pages. Both intercept network API responses to analyze reward value and entry requirements, sending real-time Discord notifications for high-value rewards and alerting on crashes.

---

## ✨ Features

* **Independent Scan Scripts**:
  * **`scan_hot_rank.py`**: Scans only Hot Rank rooms.
  * **`scan_categories.py`**: Continuously scans only rooms discovered from `CATEGORY_URLS`.
* **Network Response Interception**:
  * Powered by Playwright Chromium automation.
  * Registers network listeners **before** navigating to room pages (`page.goto`), ensuring capture of `getLotteryInfoWeb` API data.
  * Checks the `getLotteryInfoWeb` response first. Normal (`code == 0`) responses are parsed immediately; non-zero or missing responses trigger GeeTest/login checks, then reload the room and request the lottery data again.
* **Low-Bandwidth Room Loading**:
  * Blocks Playwright `media` requests and `.m4s` stream segments, so live audio/video is not downloaded while lottery API requests remain available.
* **Scheduled Scan Windows**:
  * Hot Rank and Category scans run in separate processes and can run at the same time.
  * The Hot Rank script locks a list at `:59:45`, scans that saved list from `:00:30` through `:19:59`, rests until `:40:00`, then refreshes the list for every scan through `:59:44`.
  * The Category script has no schedule and continuously repeats category scans.
  * Start and finish timestamps are printed for each Hot Rank and Category scan.
* **Advanced Lottery & Red Packet Filters**:
  * **Anchor Lotteries**: Filters out high-threshold requirements and low-time remaining draws.
  * **Red Packets**: Calculates average battery value per award item to ensure only high-yield packets trigger notifications.
* **GeeTest Captcha Detection & Audio Alarm**:
  * Triggers a system beep (`winsound`) when a GeeTest captcha panel appears on screen.
  * Pauses execution until manually solved, with a **5-minute timeout limit** to prevent infinite hanging.
* **Discord Webhook Alerts**:
  * **Lottery & Red Packet Alerts**: Sends formatted embed notifications for lotteries that meet specified thresholds.
  * **Crash Alert**: Automatically catches unhandled runtime exceptions and posts the stack trace to Discord.
  * **Interaction Alert**: Sends warnings for captcha prompts or API rate limit responses.
* **External Configuration Support**:
  * Automatically reads key-value configurations from `config.txt` at launch.

---

## 🕒 Separate Script Schedules

### `scan_hot_rank.py`

| Time in each hour | Action |
| --- | --- |
| `:59:45` | Fetch and save one Hot Rank list for the following hour. |
| `:59:45–next hour :00:29` | Wait; no room scanning is started during this interval. |
| `:00:30–:19:59` | Repeatedly scan the saved Hot Rank list. |
| `:20:00–:39:59` | Wait; no room scanning is started during this interval. |
| `:40:00–:59:44` | Fetch and scan the current Hot Rank list. |

### `scan_categories.py`

Starts immediately and repeatedly scans the configured category pages. It does not wait for a scheduled time window.

Each scan checks its current window boundary before starting the next room. If the Hot Rank script starts during the saved-list period without a snapshot, it creates a fallback snapshot for that period.

---

## ⚙️ Configuration (`config.txt`)

The script parses `config.txt` located in the root directory:

<table>
  <thead>
    <tr>
      <th>Key</th>
      <th>Description</th>
      <th>Default / Example</th>
    </tr>
  </thead>
  <tbody>
     <tr>
      <td><code>IM_SWITCH</code></td>
      <td>Push Discord notification toggle (<code>1</code> = Enabled, <code>0</code> = Disabled)</td>
      <td><code>0</code></td>
    </tr>
    <tr>
      <td><code>BEEP_SWITCH</code></td>
      <td>Windows Beep sound toggle (<code>1</code> = Enabled, <code>0</code> = Disabled)</td>
      <td><code>1</code></td>
    </tr>
    <tr>
      <td><code>DISCORD_WEBHOOK</code></td>
      <td>Discord Webhook URL for alert notifications</td>
      <td><code>"https://discord.com/api/webhooks/..."</code></td>
    </tr>
    <tr>
      <td><code>ROOM_COUNT</code></td>
      <td>Maximum number of rooms to extract per category page</td>
      <td><code>40</code></td>
    </tr>
    <tr>
      <td><code>PURPLE_ALERT_THRESHOLD</code></td>
      <td>🟪 Alert will be sent when total prize battery value exceeds this number</td>
      <td><code>9</code></td>
    </tr>
    <tr>
      <td><code>RED_ALERT_AVG_THRESHOLD</code></td>
      <td>🧧 Alert will be sent when <b>average battery value per prize</b> exceeds this number</td>
      <td><code>3</code></td>
    </tr>
    <tr>
      <td><code>CATEGORY_URLS</code></td>
      <td>List of category page URLs (JSON Array format)</td>
      <td><code>["https://live.bilibili.com/p/eden/area-tags?..."]</code></td>
    </tr>
    <tr>
      <td><code>RED_SCAN_SWITCH</code></td>
      <td>Switch for Scan 🧧</td>
      <td><code>1</code></td>
    </tr>
      <tr>
      <td><code>PURPLE_SCAN_SWITCH</code></td>
      <td>Switch for Scan 🟪</td>
      <td><code>1</code></td>
    </tr>
  </tbody>
</table>
---

## 🚀 Quick Start

### 1. Requirements
* Windows OS (required for native `winsound` audio alarms).
* Python 3.8 or higher.

### 2. One-Click Setup
Run `install.bat` on Windows to automatically install dependencies, download the Playwright Chromium browser binary.

### 3. Start a scanner

Run either script in a separate terminal window:

```bat
python scan_hot_rank.py
python scan_categories.py
```

Run both if you want to monitor Hot Rank rooms and category pages concurrently.

`scan.py` now contains shared scanner functions and is not the executable entry point.


## 🛡️ Exception & Risk Control Handling

* **GeeTest Captchas and Login**: When GeeTest panel or login panel appears in Liver's room, the script sounds a beep alarm and loops until you manually pass the verification.
* **Fatal Crashes**: Any top-level unhandled exception triggers `send_crash_notification` to forward the error log directly to Discord.
