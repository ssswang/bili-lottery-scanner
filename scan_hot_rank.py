# -*- coding: utf-8 -*-
"""入口：热门榜扫描。"""

import sys
import traceback
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

from scan import (
    PURPLE_SCAN_SWITCH,
    RED_SCAN_SWITCH,
    create_context,
    get_hot_rank_rooms,
    scan_hot_rank,
    send_crash_notification,
    wait_until,
)


def main():
    if not RED_SCAN_SWITCH and not PURPLE_SCAN_SWITCH:
        sys.exit("需要至少监控一种红包")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context, page = create_context(browser)
            page.mouse.wheel(0, 400)
            locked_hot_rooms = None

            while True:
                now = datetime.now()
                locked_scan_start = now.replace(
                    minute=0, second=30, microsecond=0
                )
                locked_scan_end = now.replace(
                    minute=20, second=0, microsecond=0
                )
                live_scan_start = now.replace(
                    minute=40, second=0, microsecond=0
                )
                snapshot_time = now.replace(
                    minute=59, second=45, microsecond=0
                )

                if now >= snapshot_time:
                    print(
                        f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] "
                        "获取并锁定下一小时的热门榜"
                    )
                    locked_hot_rooms = get_hot_rank_rooms()
                    next_locked_scan_start = (
                        now + timedelta(hours=1)
                    ).replace(minute=0, second=30, microsecond=0)
                    wait_until(next_locked_scan_start)
                elif now < locked_scan_start:
                    if locked_hot_rooms is None:
                        print("脚本中途启动，获取一份备用锁定热门榜")
                        locked_hot_rooms = get_hot_rank_rooms()
                    wait_until(locked_scan_start)
                elif now < locked_scan_end:
                    if locked_hot_rooms is None:
                        print("未找到锁定热门榜，获取一份备用列表")
                        locked_hot_rooms = get_hot_rank_rooms()
                    scan_hot_rank(page, locked_scan_end, locked_hot_rooms)
                elif now < live_scan_start:
                    wait_until(live_scan_start)
                else:
                    scan_hot_rank(page, snapshot_time)

    except Exception:
        error_msg = traceback.format_exc()
        send_crash_notification(error_msg)
        raise


if __name__ == "__main__":
    main()
