# B Zhan Live Lottery Scanner

A monitoring and alert script for B Zhan live stream red packets and anchor lotteries without login, built with Python and Playwright. The script periodically scans custom live rooms, hot ranking streams, and specified live categories. It intercepts network API responses to analyze reward value and entry requirements, sending real-time Discord notifications for high-value rewards and alerting on script crashes.

---

## ✨ Features

* **Multi-Source Room Scanning**:
  * **Custom Room List**: Prioritizes scanning user-defined room IDs (`CUSTOM_ROOM_IDS`).
  * **Hot Ranking List**: Automatically fetches and scans top live streams via the B Zhan Hot Rank API.
  * **Category Pages**: Scrolls through specified category pages (`CATEGORY_URLS`) to discover active live streams.
* **Zero-Miss Network Response Interception**:
  * Powered by Playwright Chromium automation.
  * Registers network listeners **before** navigating to room pages (`page.goto`), ensuring capture of `getLotteryInfoWeb` API data.
  * Validates red envelope / anchor lottery UI icons before processing data to maintain ultra-fast scanning speeds.
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
      <td><code>CUSTOM_ROOM_IDS</code></td>
      <td>List of specific room IDs to scan first (JSON Array format)</td>
      <td><code>[]</code></td>
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

### 3. Generate the `config.txt` if you need


## 🛡️ Exception & Risk Control Handling

* **GeeTest Captchas**: When `div.geetest_panel` appears in Liver's room, the script sounds a beep alarm and loops until you manually pass the verification.
* **Rate Limits**: If B Zhan triggers risk control code `-352` which is unlikely , the scanner will log the warning and halt the current category scan pass.
* **Fatal Crashes**: Any top-level unhandled exception triggers `send_crash_notification` to forward the error log directly to Discord.
