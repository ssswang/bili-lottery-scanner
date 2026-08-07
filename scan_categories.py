# -*- coding: utf-8 -*-
"""入口：分区扫描。"""

import sys
import traceback

from playwright.sync_api import sync_playwright

from scan import (
    PURPLE_SCAN_SWITCH,
    RED_SCAN_SWITCH,
    create_context,
    scan_categories,
    send_crash_notification,
)


def main():
    if not RED_SCAN_SWITCH and not PURPLE_SCAN_SWITCH:
        sys.exit("需要至少监控一种红包")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context, page = create_context(browser)
            page.mouse.wheel(0, 400)

            while True:
                scan_categories(page)

    except Exception:
        error_msg = traceback.format_exc()
        send_crash_notification(error_msg)
        raise


if __name__ == "__main__":
    main()
