# -*- coding: utf-8 -*-
"""每小时读取一次 B Zhan 页面人气榜 Top 3。"""

import traceback
from datetime import datetime, timedelta
import time

from playwright.sync_api import sync_playwright

from discord_notifier import DiscordNotifier


APP_HOT_RANK_URL = "https://live.bilibili.com/p/html/live-app-hotrank/index.html#/v2"


def get_top_rank_rooms(page):
    """读取页面人气榜前三名的主播用户 ID 和名称。"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 获取页面人气榜前三名")
    page.goto(APP_HOT_RANK_URL)
    items = page.locator("div.top-list > div.top-item")

    try:
        items.first.wait_for(state="attached", timeout=7000)
    except Exception as error:
        print(f"获取页面人气榜失败：{error}")
        return []

    rank_rooms = []
    for index in range(min(3, items.count())):
        item = items.nth(index)
        user_id = item.get_attribute("data-id")
        try:
            anchor_name = item.locator("div.anchor-name").inner_text().strip()
        except Exception:
            anchor_name = "未知主播"
        # 页面 data-id 对应主播用户 ID，可用于跳转其个人主页。
        if user_id:
            rank_rooms.append({"user_id": user_id, "anchor_name": anchor_name})
    return rank_rooms


def print_top_rank_rooms(rank_rooms):
    """按排名输出页面人气榜前三名。"""
    for rank, room in enumerate(rank_rooms, start=1):
        print(f"Top {rank}：主播主页 ID {room['user_id']} | 主播 {room['anchor_name']}")


def get_next_scan_time():
    """计算下一次每小时零分五秒的扫描时间。"""
    now = datetime.now()
    target_time = now.replace(minute=0, second=5, microsecond=0)
    if target_time < now:
        target_time += timedelta(hours=1)
    return target_time


def wait_until(target_time):
    """等待到下一个整点五秒的 Top 3 扫描时间。"""
    seconds = max(0, (target_time - datetime.now()).total_seconds())
    if seconds:
        print(f"等待至 {target_time.strftime('%Y-%m-%d %H:%M:%S')}")
        time.sleep(seconds)


def main():
    notifier = DiscordNotifier()
    try:
        with sync_playwright() as p:
            while True:
                wait_until(get_next_scan_time())
                browser = p.chromium.launch(headless=True)
                try:
                    context = browser.new_context()
                    try:
                        page = context.new_page()
                        rank_rooms = get_top_rank_rooms(page)
                        if rank_rooms:
                            print_top_rank_rooms(rank_rooms)
                            notifier.send_top_rank_notification(rank_rooms)
                    finally:
                        context.close()
                finally:
                    browser.close()

    except Exception:
        error_msg = traceback.format_exc()
        notifier.send_interaction_notification(f"💥 Top 3 扫描器已停止：\n{error_msg}")
        raise


if __name__ == "__main__":
    main()
