# Changelog

## 2026-08-03
**Compare**: [098a59b...d2062be](https://github.com/ssswang/bili-lottery-scanner/compare/098a59b2d844140088204934fae6e1f4cad618ab...d2062be524a9667f0e671594240727bea1e28801)
### ✨ New
- Added support for **blacklisting specific rooms** via `BLACKLIST_ROOM_IDS`.
- Added detection of **VIP/Guard-only streams**, allowing them to be skipped automatically.
- Added **viewer-count filtering** for Hot Rank rooms to prioritize streams with a higher chance of valuable lotteries.

### 🚀 Improvements
- Improved GeeTest detection reliability and timeout handling.
- Changed browser initialization to start from the live homepage instead of a category page.
- Added GeeTest verification checks during category scanning.
- Improved lottery parsing:
  - Participation requirements and draw times are now associated with the **highest-value lottery** instead of the last processed entry.
  - Notification content is more accurate and consistent.
- Enhanced logging and exception reporting for easier debugging and long-term stability.

### ⚙️ Configuration Changes
Added a new configuration option:

- `BLACKLIST_ROOM_IDS`

### 🐛 Fixes
- Improved GeeTest detection reliability and timeout handling.
- Fixed Code `-352` notification logic to ensure risk-control events are reported correctly.

### ⚠️ Behavioral Changes
- Increased the delay between scan cycles to **300 seconds (5 minutes)** to reduce request frequency and lower the likelihood of triggering anti-abuse mechanisms.
## 2026-08-02
**Compare**: [2398ada...88afdd6](https://github.com/ssswang/bili-lottery-scanner/compare/2398adafba4ba4784201686e3fba10d7c6766a8c...88afdd6877db7a011b8cf0a31caddeea556bd1c4)

### ✨ New
- Added `BEEP_SWITCH` configuration to enable or disable sound alerts.
- Added Discord interaction notifications for:
  - GeeTest captcha required
  - Captcha completed
  - API access denied / rate limiting
  - Runtime failures
- Introduced a shared Discord posting function for all notification types.

### 🚀 Improved
- Redesigned network interception to register response listeners **before** page navigation.
- Optimized red packet evaluation logic:
  - Replaced total reward threshold with **average battery value per prize**.
  - Added configurable `RED_ALERT_AVG_THRESHOLD`.
- Improved anchor lottery filtering:
  - Ignore lotteries with very little remaining time.
  - Skip logging and notifications for extremely low-value prizes.
- Improved scanning stability by cleaning up Playwright event listeners after each room scan.
- Added an additional page scroll after entering rooms to improve UI detection.
- Unified Discord notification formatting and logging.
- Refined README with updated architecture, detection flow, filtering rules and configuration descriptions.

### 🐛 Fixed
- Fixed notification timeout wording.
- Improved error reporting consistency throughout the application.

---

## 2026-08-01
**Compare**: [79fceb0...2398ada](https://github.com/ssswang/bili-lottery-scanner/compare/79fceb076dcbc2fcee3a3ae61bfb8a867563b52e...2398adafba4ba4784201686e3fba10d7c6766a8c)

### ✨ New
- Added support for **Anchor Lottery** detection in addition to popularity red packets.
- Added separate alert thresholds for:
  - `PURPLE_ALERT_THRESHOLD`
  - `RED_ALERT_THRESHOLD`
- Discord notifications now support both red packets and anchor lotteries.

### 🚀 Improved
- Added reusable helper to retrieve streamer usernames.
- Added automatic conversion of lottery countdown into a readable closing timestamp.
- Improved console output formatting.
- Added filtering for high-threshold lotteries.
- Red packet notifications now use the **largest packet value** instead of the last packet.
- Always display detected anchor lotteries in console even when below notification threshold.
- README updated with new configuration options and threshold explanations.

### ⚙️ Configuration Changes
- Split the original alert threshold into:
  - `RED_ALERT_THRESHOLD`
  - `PURPLE_ALERT_THRESHOLD`
- Default `PURPLE_ALERT_THRESHOLD` set to **10**.

### 🐛 Fixed
- Fixed duplicate Discord notifications caused by incorrect indentation.
- Fixed lottery closing time display.

---

## 2026-07-31
**Compare**: [cd903eb...79fceb0](https://github.com/ssswang/bili-lottery-scanner/compare/cd903eb249881f9f7558d3e62c604c3b10fd8f95...79fceb076dcbc2fcee3a3ae61bfb8a867563b52e)

### ✨ New
- Added **Hot Ranking** scanning by querying the B Hot Rank API, expanding coverage beyond custom rooms and category pages.
- Added **crash notifications** to Discord, including stack traces for unexpected runtime failures.
- Introduced support for an external `config.txt` file, allowing runtime configuration without modifying source code.
- Added configurable alert threshold (`ALERT_THRESHOLD`) for lottery notifications.
- Added sample configuration file (`config.txt.sample`).
- Added `requests` dependency for webhook communication.

### 🚀 Improved
- Improved **Discord Webhook notifications** for high-value lottery events with rich embed messages.
- Reduced category room extraction limit from **60** to **40** by default for faster scan cycles.
- Shortened the interval between scan rounds from **60 seconds** to continuous scanning with minimal delay.
- Improved room list extraction with better exception handling and more reliable page loading..
- Improved overall application stability by wrapping the main loop with top-level exception handling.
- Simplified installation process by removing unnecessary setup steps.

### ⚙️ Configuration Changes
Added support for external configuration via `config.txt`:

- `DISCORD_WEBHOOK`
- `IM_SWITCH`
- `ROOM_COUNT`
- `CATEGORY_URLS`
- `CUSTOM_ROOM_IDS`
- `ALERT_THRESHOLD`

### 🐛 Fixed
- Improved error handling when category pages fail to load.
- Prevented malformed configuration values from causing runtime failures.
- Removed the unnecessary hover action when detecting lottery icons, simplifying room scanning logic.
- Improved robustness when reading room owner information and room lists.

---

## 2026-07-26
**Compare**: [bf97528...cd903eb](https://github.com/ssswang/bili-lottery-scanner/compare/bf975289cf778c10cc5c4acabb8fba6b68ab2acf...cd903eb249881f9f7558d3e62c604c3b10fd8f95)

### 📖 Documentation
- Added a comprehensive project overview.
- Documented core features and scanning workflow.
- Added Geetest captcha explanation.
- Added webhook integration notes.
- Expanded README with configuration descriptions.

### 🚀 Improved
- Simplified custom room configuration.
- Reduced GeeTest detection wait time for faster scanning.

## 2026-07-15

### ✨ Early version with core functionality:
- Playwright-based monitoring for B live stream red packets.
- Intercepts `getLotteryInfoWeb` API responses to detect rewards.
- Room IDs and category URLs are hardcoded inside `scan.py`.
- Geetest captcha detection triggers a beep and waits for manual solving.
