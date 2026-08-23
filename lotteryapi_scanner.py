# -*- coding: utf-8 -*-
"""不启动浏览器，轮询人气榜并解析 getLotteryInfoWeb 抽奖数据。"""

import argparse
import re
import time
from datetime import datetime

import requests
import winsound

from b_api import (
    USER_AGENT,
    load_authorized_session,
    request_bilibili,
    refresh_authorization,
    request_lottery_info,
    request_popular_anchor_rank,
)
from config import (
    HOT_RANK_LIMIT,
    BEEP_ENABLED,
    PURPLE_ALERT_THRESHOLD,
    RED_ALERT_AVG_THRESHOLD,
    RISK_BACKOFF_SECONDS,
    ROOM_INTERVAL_SECONDS,
    SCAN_HOT_RANK,
    SCAN_POPULAR_RANKS,
)
from discord_notifier import DiscordNotifier


HOT_RANK_API_URL = "https://api.live.bilibili.com/xlive/web-interface/v1/index/getHotRankList"
TOP_FIFTY_PARENT_AREA_IDS = {1, 5}
TOP_FIFTY_MAX_ROOMS = 60
POPULAR_ANCHOR_RANKS = (
    {"area_id": 207, "parent_area_id": 1, "rank_type": 3},
    {"area_id": 530, "parent_area_id": 1, "rank_type": 3},
    {"area_id": 145, "parent_area_id": 1, "rank_type": 3},
    {"area_id": 21, "parent_area_id": 1, "rank_type": 3},
    {"area_id": 0, "parent_area_id": 5, "rank_type": 2},  # 电台
    {"area_id": 0, "parent_area_id": 9, "rank_type": 2},  # 虚拟
)


def alert_beep():
    """达到告警阈值时播放 Windows 提示音。"""
    if BEEP_ENABLED:
        winsound.Beep(1200, 800)


def format_rank_info(rank_name, rank):
    """将接口的数字排名转换为便于终端和通知展示的文字。"""
    return f"{rank_name}第 {rank} 名" if rank is not None else f"{rank_name}排名未提供"


class LotteryProcessor:
    """解析红包和天选数据，并在达到阈值时输出和发送 Discord 通知。"""

    def __init__(self, notifier, red_threshold=3, purple_threshold=9):
        self.notifier = notifier
        self.red_threshold = red_threshold
        self.purple_threshold = purple_threshold

    def process(self, room_id, host_name, rank_info, payload):
        """处理一个成功的 getLotteryInfoWeb 接口响应。"""
        data = payload.get("data", {})
        red_packets = data.get("popularity_red_pocket") or []
        anchor_data = data.get("anchor")
        if red_packets:
            self.calculate_red_packets(room_id, host_name, rank_info, red_packets)
        if anchor_data:
            self.calculate_anchor_lottery(room_id, host_name, rank_info, anchor_data)

    def calculate_anchor_lottery(self, room_id, host_name, rank_info, anchor_data):
        """解析天选抽奖，并在满足阈值时告警。"""
        require_text = anchor_data.get("require_text", "")
        if "舰长" in require_text or "提督" in require_text:
            print(f"⏩ 房间 {room_id} 天选包含高门槛({require_text})，跳过")
            return

        award_name = anchor_data.get("award_name", "未知奖品")
        award_num = anchor_data.get("award_num", 1)
        price_match = re.search(r"价值(\d+)电池", anchor_data.get("award_price_text", ""))
        total_price = int(price_match.group(1)) if price_match else 0
        remaining_seconds = int(anchor_data.get("time", -1))
        draw_time = datetime.fromtimestamp(
            datetime.now().timestamp() + max(0, remaining_seconds)
        ).strftime("%Y-%m-%d %H:%M:%S")
        gift_text = f"🟪 奖品: {award_name} 最大中奖人数: {award_num}"

        print(f"🎉 发现天选抽奖！主播: {host_name} | 房间: {room_id}")
        print(
            f" {gift_text}\n 🔒 参与门槛: {require_text}\n 💰 价值: {total_price} 电池"
            f"\n 🏆 榜单排名: {rank_info}"
        )
        if total_price > self.purple_threshold and remaining_seconds > 20:
            alert_beep()
            self.notifier.send_lottery_notification(
                host_name,
                room_id,
                gift_text,
                require_text,
                total_price,
                draw_time,
                rank_info=rank_info,
            )

    def calculate_red_packets(self, room_id, host_name, rank_info, red_packets):
        """解析红包包均价值；达到阈值时发送通知。"""
        requirement_map = {0: "无要求", 1: "需要关注", 2: "需要粉丝勋章", 3: "上舰"}
        gift_lines = []
        max_total = 0
        max_average = 0.0
        requirement = "无要求"
        draw_time = "未知"
        sender_name = "未知"
        for packet in red_packets:
            total_price = int(packet.get("total_price", 0)) // 100
            if total_price > max_total:
                max_total = total_price
                sender_name = packet.get("sender_name") or "未知"

            item_count = 0
            for award in packet.get("awards") or []:
                count = int(award.get("num", 0))
                item_count += count
                gift_lines.append(
                    f"🎁 礼物: {award.get('gift_name', '未知')} 最大中奖人数: {count}"
                )

            average = total_price / item_count if item_count else 0.0
            if average >= max_average:
                max_average = average
                requirement = requirement_map.get(
                    packet.get("join_requirement"), "未知门槛"
                )
                end_time = packet.get("end_time")
                draw_time = (
                    datetime.fromtimestamp(end_time).strftime("%Y-%m-%d %H:%M:%S")
                    if end_time
                    else "未知"
                )

        print(
            f"🎉 发现红包！主播: {host_name} | 房间: {room_id} | "
            f"最大包价值: {max_total} 电池 | 发送者: {sender_name} | "
            f"包均: {max_average:.2f} 电池/人"
        )
        if max_average > self.red_threshold:
            gift_text = "\n".join(gift_lines)
            print(
                f" {gift_text}\n 🔒 参与门槛: {requirement}\n 🕒 开奖时间: {draw_time}"
                f"\n 🏆 榜单排名: {rank_info}"
            )
            alert_beep()
            self.notifier.send_lottery_notification(
                host_name,
                room_id,
                gift_text,
                requirement,
                max_total,
                draw_time,
                sender_name=sender_name,
                rank_info=rank_info,
            )
        else:
            print(f" 🏆 榜单排名: {rank_info}")


def get_hot_rank_rooms(session, limit):
    """获取符合筛选规则的人气榜房间，并保留接口返回的主播名。"""
    response = request_bilibili(
        session,
        "get",
        HOT_RANK_API_URL,
        params={"web_location": "444.7"},
        headers={"Referer": "https://live.bilibili.com/", "User-Agent": USER_AGENT},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(
            f"获取人气榜失败：{payload.get('code')} {payload.get('message')}"
        )

    rooms = []
    for item in payload.get("data", {}).get("list", []):
        room_id = item.get("roomid")
        try:
            user_num = int(item.get("user_num", 0))
        except (TypeError, ValueError):
            continue
        if room_id and user_num < 300:
            rooms.append(
                {
                    "room_id": str(room_id),
                    "host_name": item.get("uname") or "未知主播",
                    "rank_infos": [format_rank_info("人气榜", item.get("rank"))],
                }
            )
        if len(rooms) >= limit:
            break
    print(f"成功获取人气榜房间数量：{len(rooms)}")
    return rooms


def get_popular_anchor_rank_rooms(session, wbi_keys):
    """获取指定直播分区人气榜房间，并保留接口返回的主播名。"""
    rooms = []
    successful_rank_count = 0
    for rank_params in POPULAR_ANCHOR_RANKS:
        try:
            payload = request_popular_anchor_rank(session, wbi_keys=wbi_keys, **rank_params)
        except requests.RequestException as error:
            print(f"⚠️ 获取分区人气榜失败：{rank_params}：{error}")
            continue
        if payload.get("code") != 0:
            print(
                "⚠️ 获取分区人气榜失败："
                f"{rank_params}：{payload.get('code')} {payload.get('message')}"
            )
            continue

        successful_rank_count += 1
        for index, item in enumerate(payload.get("data", {}).get("list_new") or [], 1):
            # 指定分区只按接口返回顺序取前 50 条，不依赖 rank 字段是否存在。
            if (
                rank_params["parent_area_id"] in TOP_FIFTY_PARENT_AREA_IDS
                and index > TOP_FIFTY_MAX_ROOMS
            ):
                break

            rank = item.get("rank")
            room_id = item.get("room_id")
            if not room_id:
                continue
            host_name = (
                item.get("uinfo", {}).get("base", {}).get("name") or "未知主播"
            )
            rooms.append(
                {
                    "room_id": str(room_id),
                    "host_name": host_name,
                    "rank_infos": [format_rank_info("分区榜", rank)],
                }
            )
    print(
        "成功获取分区人气榜房间数量："
        f"{len(rooms)}（{successful_rank_count}/{len(POPULAR_ANCHOR_RANKS)} 个分区）"
    )
    return rooms


def merge_unique_rooms(*room_groups):
    """按房间号合并多个榜单，并保留该房间的全部榜单排名。"""
    rooms = []
    rooms_by_id = {}
    for room_group in room_groups:
        for room in room_group:
            room_id = room["room_id"]
            if room_id not in rooms_by_id:
                rooms_by_id[room_id] = room
                rooms.append(room)
                continue

            existing_room = rooms_by_id[room_id]
            if (
                existing_room["host_name"] == "未知主播"
                and room["host_name"] != "未知主播"
            ):
                existing_room["host_name"] = room["host_name"]
            for rank_info in room["rank_infos"]:
                if rank_info not in existing_room["rank_infos"]:
                    existing_room["rank_infos"].append(rank_info)
    return rooms


def scan_once(session, rooms, wbi_keys, room_interval, processor, notifier):
    """顺序扫描一轮房间；触发风控时立即停止本轮。"""
    for index, room in enumerate(rooms, start=1):
        room_id = room["room_id"]
        host_name = room["host_name"]
        rank_info = "、".join(room["rank_infos"])
        try:
            payload = request_lottery_info(session, room_id, wbi_keys)
        except (requests.RequestException, RuntimeError) as error:
            print(f"⚠️ 房间 {room_id} 请求失败：{error}")
        else:
            code = payload.get("code")
            if code == -352:
                notifier.send_interaction_notification(
                    f"⚠️ 触发 -352 风控，本轮停止并冷却 {RISK_BACKOFF_SECONDS} 秒。"
                )
                return True
            if code != 0:
                print(f"⚠️ 房间 {room_id} 接口返回：{code} {payload.get('message')}")
            else:
                processor.process(room_id, host_name, rank_info, payload)

        if index < len(rooms):
            time.sleep(room_interval)
    return False


def main():
    parser = argparse.ArgumentParser(description="直接轮询 B 站榜单抽奖接口，不启动浏览器")
    parser.add_argument(
        "--limit", type=int, default=HOT_RANK_LIMIT, help="人气榜最多获取的房间数"
    )
    parser.add_argument(
        "--room-interval",
        type=float,
        default=ROOM_INTERVAL_SECONDS,
        help="每个房间请求之间的秒数，最低为 3",
    )
    parser.add_argument(
        "--discord-webhook", default=None, help="临时覆盖 config.txt 中的 Discord Webhook"
    )
    parser.add_argument(
        "--red-threshold", type=float, default=RED_ALERT_AVG_THRESHOLD, help="红包包均告警阈值"
    )
    parser.add_argument(
        "--purple-threshold", type=int, default=PURPLE_ALERT_THRESHOLD, help="天选价值告警阈值"
    )
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit 必须大于 0")
    if not SCAN_HOT_RANK and not SCAN_POPULAR_RANKS:
        parser.error("SCAN_HOT_RANK 和 SCAN_POPULAR_RANKS 不能同时关闭")

    room_interval = max(3, args.room_interval)
    enabled_sources = []
    if SCAN_HOT_RANK:
        enabled_sources.append("人气榜")
    if SCAN_POPULAR_RANKS:
        enabled_sources.append("分区榜")
    print(f"已启用扫描来源：{'、'.join(enabled_sources)}")
    notifier = DiscordNotifier(args.discord_webhook)
    processor = LotteryProcessor(
        notifier, red_threshold=args.red_threshold, purple_threshold=args.purple_threshold
    )

    try:
        session = load_authorized_session()
    except RuntimeError as error:
        raise SystemExit(error) from error

    while True:
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始新一轮直接扫描")
        try:
            # 每轮重新申请 ticket 和 WBI 密钥；每个房间再使用当前时间生成 wts/w_rid。
            wbi_keys = refresh_authorization(session)
            hot_rank_rooms = (
                get_hot_rank_rooms(session, args.limit) if SCAN_HOT_RANK else []
            )
            popular_rank_rooms = (
                get_popular_anchor_rank_rooms(session, wbi_keys)
                if SCAN_POPULAR_RANKS
                else []
            )
            rooms = merge_unique_rooms(hot_rank_rooms, popular_rank_rooms)
            print(f"本轮去重后待扫描房间数量：{len(rooms)}")
            hit_risk_control = scan_once(
                session, rooms, wbi_keys, room_interval, processor, notifier
            )
            if not rooms:
                # 人气榜暂时为空时沿用房间间隔，避免无间隔重复请求列表接口。
                time.sleep(room_interval)
        except (requests.RequestException, RuntimeError) as error:
            print(f"⚠️ 本轮扫描失败：{error}")
            hit_risk_control = True

        if hit_risk_control:
            print(f"等待 {RISK_BACKOFF_SECONDS} 秒后开始下一轮。")
            time.sleep(RISK_BACKOFF_SECONDS)


if __name__ == "__main__":
    main()
