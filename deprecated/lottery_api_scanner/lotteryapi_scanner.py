# -*- coding: utf-8 -*-
"""不启动浏览器，轮询人气榜并解析 getLotteryInfoWeb 抽奖数据。"""

import argparse
import random
import re
import time
import threading
from datetime import datetime

import requests
import winsound

from auth.api_auth import (
    create_session,
    ensure_device_cookies,
    generate_and_set_buvid_fp,
    get_device_profile,
    get_cookie_value,
    get_wbi_keys,
    set_client_identity_cookies,
)
from b_api import request_lottery_info
from config import (
    HOT_RANK_LIMIT,
    BEEP_ENABLED,
    PROCESS_ANCHOR_LOTTERY,
    PURPLE_ALERT_THRESHOLD,
    RED_ALERT_AVG_THRESHOLD,
    RISK_BACKOFF_SECONDS,
    ROOM_INTERVAL_SECONDS,
    SCAN_HOT_RANK,
    SCAN_POPULAR_RANKS,
)
from auth.user_auth import load_saved_sessions
from discord_notifier import DiscordNotifier
from risk_control import RiskControlHandler
from room_lists import RoomListBuilder


def alert_beep():
    """达到告警阈值时播放 Windows 提示音。"""
    if BEEP_ENABLED:
        winsound.Beep(1200, 800)


DEVICE_ID_COOKIE_NAMES = ("buvid3", "buvid4", "buvid_fp", "_uuid", "b_lsid")


def verify_distinct_device_identities(worker_sessions):
    """确保并发账号不会复用关键设备 Cookie。"""
    for cookie_name in DEVICE_ID_COOKIE_NAMES:
        owner_by_value = {}
        for account_name, session in worker_sessions:
            value = get_cookie_value(session, cookie_name)
            if not value:
                raise SystemExit(
                    f"账号 {account_name} 缺少设备标识 {cookie_name}，请重新登录后再启动。"
                )
            if value in owner_by_value:
                raise SystemExit(
                    f"账号 {account_name} 与 {owner_by_value[value]} 共用了设备标识 "
                    f"{cookie_name}，请重新登录其中一个账号后再启动。"
                )
            owner_by_value[value] = account_name


class AnchorLotteryEvent:
    """天选抽奖事件：负责独立解析、输出与告警。"""

    def __init__(self, notifier, threshold):
        self.notifier = notifier
        self.threshold = threshold

    def process(self, room_id, host_name, rank_info, anchor_data):
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
        if total_price > self.threshold and remaining_seconds > 20:
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


class RedPacketEvent:
    """红包事件：负责独立解析、输出与告警。"""

    def __init__(self, notifier, threshold):
        self.notifier = notifier
        self.threshold = threshold

    def process(self, room_id, host_name, rank_info, red_packets):
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
            f"包均: {max_average:.2f} 电池/人 | 抽取条件: {requirement}"
        )
        if max_average > self.threshold:
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


class LotteryProcessor:
    """将一次接口响应分发给红包与天选两个独立事件。"""

    def __init__(
        self, notifier, red_threshold, purple_threshold, process_anchor_lottery
    ):
        self.red_packet_event = RedPacketEvent(notifier, red_threshold)
        self.anchor_lottery_event = (
            AnchorLotteryEvent(notifier, purple_threshold)
            if process_anchor_lottery
            else None
        )

    def process(self, room_id, host_name, rank_info, payload):
        """只请求一次房间接口，再分别处理其中的两类抽奖事件。"""
        data = payload.get("data", {})
        red_packets = data.get("popularity_red_pocket") or []
        anchor_data = data.get("anchor")
        if red_packets:
            self.red_packet_event.process(room_id, host_name, rank_info, red_packets)
        if self.anchor_lottery_event and anchor_data:
            self.anchor_lottery_event.process(room_id, host_name, rank_info, anchor_data)


def scan_once(account_name, session, rooms, wbi_keys, room_interval, processor, risk_control):
    """顺序扫描一个房间分片；命中 -352 时立即结束该账号的本轮分片。"""
    # 榜单查询刚结束，先隔开一个间隔再开始房间扫描，避免突发触发频率检测。
    time.sleep(room_interval)
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
                risk_control.record_352(account_name, room_id)
                return
            if code != 0:
                print(f"⚠️ 房间 {room_id} 接口返回：{code} {payload.get('message')}")
            else:
                processor.process(room_id, host_name, rank_info, payload)

        if index < len(rooms):
            # 保持 3.x 秒级随机间隔，避免机械等间隔请求被频率检测识别。
            time.sleep(random.uniform(room_interval, room_interval + 0.9))


def split_rooms(rooms, shard_count):
    """把房间列表按顺序轮流分成 shard_count 份，尽量均匀。"""
    shards = [[] for _ in range(shard_count)]
    for index, room in enumerate(rooms):
        shards[index % shard_count].append(room)
    return shards


def scan_rooms_parallel(
    list_session, worker_sessions, rooms, wbi_keys, room_interval, processor, risk_control
):
    """把房间列表分成多份，通过独立账号会话并发扫描。

    worker_sessions 为 (身份键, 会话) 列表。`risk_control` 统一记录 -352；
    本函数仅返回连接失败的身份键。
    """
    workers = max(1, min(len(worker_sessions), len(rooms))) if rooms else 0
    if workers <= 1:
        if worker_sessions and rooms:
            print("⚠️ 可用账号不足或房间过少，本轮退回单连接扫描。")
        # 单 worker 时使用可用的账号会话。
        identity_key, session = (
            worker_sessions[0] if worker_sessions else ("direct", list_session)
        )
        try:
            scan_once(
                identity_key,
                session,
                rooms,
                wbi_keys,
                room_interval,
                processor,
                risk_control,
            )
        except (requests.RequestException, RuntimeError) as error:
            print(f"⚠️ 扫描线程失败：{error}")
            return {identity_key}
        return set()

    shards = split_rooms(rooms, workers)
    print(f"🔀 本轮使用 {workers} 个账号连接并发扫描：{[len(shard) for shard in shards]} 个房间/连接")
    dead_keys = set()
    lock = threading.Lock()

    def worker(identity_key, session, shard):
        try:
            scan_once(
                identity_key,
                session,
                shard,
                wbi_keys,
                room_interval,
                processor,
                risk_control,
            )
        except (requests.RequestException, RuntimeError) as error:
            print(f"⚠️ 扫描线程失败：{error}")
            with lock:
                dead_keys.add(identity_key)
            return

    threads = [
        threading.Thread(
            target=worker,
            args=(identity_key, session, shard),
            name=f"scanner-{index}",
        )
        for index, ((identity_key, session), shard) in enumerate(zip(worker_sessions, shards))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return dead_keys


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

    room_interval = max(3.0, args.room_interval)
    enabled_sources = []
    if SCAN_HOT_RANK:
        enabled_sources.append("人气榜")
    if SCAN_POPULAR_RANKS:
        enabled_sources.append("分区榜")
    print(f"已启用扫描来源：{'、'.join(enabled_sources)}")
    notifier = DiscordNotifier(args.discord_webhook)
    processor = LotteryProcessor(
        notifier,
        red_threshold=args.red_threshold,
        purple_threshold=args.purple_threshold,
        process_anchor_lottery=PROCESS_ANCHOR_LOTTERY,
    )
    risk_control = RiskControlHandler(RISK_BACKOFF_SECONDS)
    print(f"天选事件处理：{'开启' if PROCESS_ANCHOR_LOTTERY else '关闭'}")
    required_account_names = ("acct1", "acct2")
    scan_account_names = (*required_account_names, "acct3")
    accounts = [
        (name, header)
        for name, header in load_saved_sessions()
        if name in scan_account_names
    ]
    available_account_names = {name for name, _ in accounts}
    missing_accounts = [
        name for name in required_account_names if name not in available_account_names
    ]
    if missing_accounts:
        raise SystemExit(
            f"未找到 {('、'.join(missing_accounts))} 的登录会话，请先完成对应账号的二维码登录。"
        )

    worker_map = {}  # 账号名 -> 直连会话
    print(f"👤 使用 {('、'.join(name for name, _ in accounts))} 的直连会话并发扫描。")

    def ensure_account_worker(name, header):
        """为每个账号构建独立的直连会话与设备指纹档案。"""
        try:
            session = create_session(header)
            # 扫描器始终直连，不继承 HTTP_PROXY/HTTPS_PROXY 等环境设置。
            session.trust_env = False
            session.proxies.clear()
            profile = get_device_profile(f"account-{name}")
            # 即使旧登录 Cookie 中存在这些字段，也用账号专属档案覆盖。
            set_client_identity_cookies(session, profile, force=True)
            missing_device_cookies = ensure_device_cookies(session, profile)
            if missing_device_cookies:
                raise RuntimeError(
                    f"缺少设备 Cookie：{', '.join(missing_device_cookies)}"
                )
            # 依据账号自己的 buvid3 与设备档案重新计算，避免复用旧 buvid_fp。
            generate_and_set_buvid_fp(session, profile)
        except (requests.RequestException, RuntimeError) as error:
            print(f"⚠️ 账号 {name} 直连会话构建失败：{error}")
            worker_map.pop(name, None)
            return
        worker_map[name] = session
        print(f"✅ 账号 {name} 直连会话就绪")

    while True:
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始新一轮直接扫描")
        # acct1 负责榜单接口；已登录的 acct1、acct2、acct3 最多三个账号并发扫描。
        for name, header in accounts:
            if worker_map.get(name) is None:
                ensure_account_worker(name, header)
        if any(worker_map.get(name) is None for name, _ in accounts):
            print("⚠️ 账号会话尚未全部就绪，冷却后重试。")
            time.sleep(RISK_BACKOFF_SECONDS)
            continue
        verify_distinct_device_identities(
            [(name, worker_map[name]) for name, _ in accounts]
        )
        list_session = worker_map.get("acct1")
        if list_session is None:
            time.sleep(RISK_BACKOFF_SECONDS)
            continue

        try:
            # 每轮刷新 WBI 密钥；每个房间再使用当前时间生成 wts/w_rid。
            wbi_keys = get_wbi_keys(list_session)
            room_list_builder = RoomListBuilder(list_session, wbi_keys, args.limit)
            rooms = room_list_builder.build(SCAN_HOT_RANK, SCAN_POPULAR_RANKS)
            print(f"本轮去重后待扫描房间数量：{len(rooms)}")
            worker_sessions = [
                (key, session) for key, session in worker_map.items() if session
            ]
            dead_keys = scan_rooms_parallel(
                list_session,
                worker_sessions,
                rooms,
                wbi_keys,
                room_interval,
                processor,
                risk_control,
            )
            # 连接失败的账号会话下轮重新建立；不会触碰代理配置或代理文件。
            for key in dead_keys:
                worker_map.pop(key, None)
                print(f"⚠️ 账号 {key} 的连接失败，将在下一轮重建会话。")
            if not rooms:
                # 人气榜暂时为空时沿用房间间隔，避免无间隔重复请求列表接口。
                time.sleep(room_interval)
        except (requests.RequestException, RuntimeError) as error:
            print(f"⚠️ 本轮扫描失败：{error}")
            if not risk_control.cooldown_if_needed():
                time.sleep(RISK_BACKOFF_SECONDS)
            continue

        risk_control.cooldown_if_needed()


if __name__ == "__main__":
    main()
