# bili-lottery-scanner

### 📌 Overview
**bili-lottery-scanner** uses Playwright to launch a browser, monitor specified B Zhan live-stream categories and custom room lists, intercept network requests, and detect active popularity red packets. When a valid event is found, it extracts details (prizes, entry requirements, draw time) and triggers a system beep sound alert.

> ⚠️ **Disclaimer**: This tool is strictly for educational, research, and personal use. 

### ✨ Features
- 🎯 **Network Interception**: Directly intercepts `getLotteryInfoWeb` API responses for accurate detection when red packets icon is found in html.
- 🔊 **Sound Alert**: Triggers a system beep (`winsound`) when a red packet or a Geetest verification is detected.
- 🛡️ **Geetest Handling**: Auto-detects Geetest captchas, alerts the user, and waits for manual completion before resuming.
- 📋 **Flexible Scanning Targets**: Supports scanning specific target room IDs (`CUSTOM_ROOM_IDS`) and Bilibili live categories (`CATEGORY_URLS`).
- 📊 **Detailed Info Output**: Parses prize names, prize quantities, total value (battery), draw end times, and participation conditions (e.g., Follow, Fan Badge, Guard).
