# -*- coding: utf-8 -*-
"""从本地数据库发送尚未送达的红包和天选通知。"""

import argparse
import json
import time
from datetime import datetime

from backend.database import LocalDatabase
from backend.discord_notifier import DiscordNotifier
from backend.qq_notifier import QQNotifier


REQUIREMENTS = {0: "无要求", 1: "需要关注", 2: "需要粉丝勋章", 3: "需要上舰"}


def format_end_time(timestamp):
    try:
        return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "未知"


def red_packet_details(event):
    try:
        data = json.loads(event.get("raw_data_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        data = {}
    awards = data.get("awards") if isinstance(data, dict) else []
    gifts = "\n".join(
        f"🎁 {item.get('gift_name', '未知礼物')} × {item.get('num', 0)}"
        for item in awards or []
        if isinstance(item, dict)
    ) or "奖品信息未提供"
    return {
        "gift_text": gifts,
        "requirement_str": REQUIREMENTS.get(event.get("requirement"), "未知"),
        "sender_name": event.get("sender_name") or "",
    }


def notification_details(event):
    if event["event_type"] == "red_packet":
        return red_packet_details(event)
    return {
        "gift_text": f"🎁 {event.get('award_name') or '未知奖品'} × {event.get('award_count') or 1}",
        "requirement_str": event.get("requirement") or "无要求",
        "sender_name": "",
    }


def send_event(event, discord_notifier, qq_notifier):
    details = notification_details(event)
    payload = {
        "host_name": event.get("host_name") or "未知主播",
        "room_id": event["room_id"],
        "gift_text": details["gift_text"],
        "requirement_str": details["requirement_str"],
        "total_price": event.get("total_price") or 0,
        "end_time_str": format_end_time(event.get("end_time")),
        "sender_name": details["sender_name"],
    }
    discord_delivered = discord_notifier.send_lottery_notification(**payload)
    qq_delivered = qq_notifier.send_lottery_notification(**payload)
    return bool(discord_delivered or qq_delivered)


def parse_args():
    parser = argparse.ArgumentParser(description="从 SQLite 数据库发送未送达的红包/天选通知")
    parser.add_argument("--database", default="data/red_packet_monitor.db")
    parser.add_argument("--include-expired", action="store_true", help="同时发送已开奖的历史记录")
    parser.add_argument(
        "--interval",
        type=float,
        default=30,
        help="持续运行模式下的轮询间隔秒数，必须小于 60（默认 30）",
    )
    parser.add_argument("--once", action="store_true", help="只扫描一轮后退出，不进入持续轮询")
    return parser.parse_args()


def scan_once(database, discord_notifier, qq_notifier, include_expired):
    """发送所有待通知事件；返回 (待处理数, 成功数, 失败数)。"""
    events = database.pending_notifications(include_expired=include_expired)
    if not events:
        return 0, 0, 0
    delivered = failed = 0
    for event in events:
        if send_event(event, discord_notifier, qq_notifier):
            database.mark_notification_sent(event["event_type"], event["room_id"], event["lot_id"])
            delivered += 1
        else:
            failed += 1
    return len(events), delivered, failed


def run_forever(args, database, discord_notifier, qq_notifier):
    """持续轮询数据库，按 --interval 间隔发送新通知；Ctrl+C 退出。"""
    print(
        f"🔄 进入持续轮询模式，每 {args.interval:g} 秒扫描一次数据库；按 Ctrl+C 停止。"
    )
    while True:
        try:
            pending, delivered, failed = scan_once(
                database, discord_notifier, qq_notifier, args.include_expired
            )
        except Exception as error:
            print(f"❌ 本轮扫描出现异常，稍后重试：{error}")
            pending = delivered = failed = 0
        if pending:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] 通知扫描完成：成功 {delivered}，失败 {failed}，待处理 {pending}。")
        time.sleep(args.interval)


def main():
    args = parse_args()
    if not args.once and not (0 < args.interval < 60):
        raise SystemExit("❌ --interval 必须在 (0, 60) 区间内，例如 30 表示每 30 秒扫描一次。")
    discord_notifier, qq_notifier = DiscordNotifier(), QQNotifier()
    if not discord_notifier.enabled and not qq_notifier.enabled:
        print("⚠️ 未启用 Discord 或 QQ 通知；请先在 backend/config.txt 配置通知渠道。")
        return

    database = LocalDatabase(args.database)
    try:
        if args.once:
            pending, delivered, failed = scan_once(
                database, discord_notifier, qq_notifier, args.include_expired
            )
            print(
                "没有待发送的通知。"
                if not pending
                else f"通知扫描完成：成功 {delivered}，失败 {failed}，待处理 {pending}。"
            )
            return
        run_forever(args, database, discord_notifier, qq_notifier)
    except KeyboardInterrupt:
        print("\n已停止持续通知。")
    finally:
        database.close()


if __name__ == "__main__":
    main()
