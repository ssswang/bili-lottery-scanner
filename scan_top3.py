# -*- coding: utf-8 -*-
"""每小时读取一次 B Zhan 页面人气榜 Top 3。"""

import traceback
from datetime import datetime, timedelta
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
import requests

from discord_notifier import DiscordNotifier


APP_HOT_RANK_URL = "https://live.bilibili.com/p/html/live-app-hotrank/index.html#/v2"
ROOM_ID_BY_UID_API_URL = "https://api.live.bilibili.com/room/v2/Room/room_id_by_uid"


def get_room_id_by_uid(user_id):
    """将页面榜单中的主播 UID 转换为直播间 ID。"""
    try:
        response = requests.get(
            ROOM_ID_BY_UID_API_URL,
            params={"uid": user_id},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://live.bilibili.com/"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        print(f"获取直播间 ID 失败：{error}")
        return None

    room_id = payload.get("data", {}).get("room_id")
    if payload.get("code") != 0 or not room_id:
        print(f"未找到直播间 ID：{payload.get('message', payload.get('msg', '未知错误'))}")
        return None
    return str(room_id)


def get_top_rank_rooms(page):
    """读取页面人气榜前三名，并将主播 UID 转换为直播间 ID。"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 获取页面人气榜前三名")
    try:
        # 该 SPA 的 load 事件可能被长期连接阻塞；DOM 可用后即可读取榜单。
        page.goto(APP_HOT_RANK_URL, wait_until="domcontentloaded", timeout=15_000)
    except PlaywrightTimeoutError:
        print("获取页面人气榜超时，跳过本次扫描。")
        return []
    except PlaywrightError as error:
        print(f"打开页面人气榜失败：{error}")
        return []

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
        if user_id:
            room_id = get_room_id_by_uid(user_id)
            if room_id:
                rank_rooms.append({"room_id": room_id, "anchor_name": anchor_name})
    return rank_rooms


def print_top_rank_rooms(rank_rooms):
    """按排名输出页面人气榜前三名。"""
    for rank, room in enumerate(rank_rooms, start=1):
        print(f"Top {rank}：直播间 ID {room['room_id']} | 主播 {room['anchor_name']}")


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
