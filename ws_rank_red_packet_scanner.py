# -*- coding: utf-8 -*-
"""扫描分区人气榜，并用 WebSocket 实时监听入选房间的人气红包事件。"""

import argparse
import json
import threading
import time
from collections import deque
from datetime import datetime

import requests

from auth_manager import USER_AGENT, build_cookie_header, get_cookie_value, get_wbi_keys
from config import RISK_BACKOFF_SECONDS
from discord_notifier import DiscordNotifier
from room_lists import RoomListBuilder
from ws_red_packet_watcher import (
    OP_AUTH,
    OP_HEARTBEAT,
    RED_PACKET_COMMANDS,
    RiskControlError,
    build_packet,
    get_account_session,
    get_danmu_info,
    parse_packets,
    red_packet_average,
    red_packet_summary,
    websocket,
)


class ConnectionStats:
    """线程安全地汇总本次运行的 WS 建连与风控情况。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.attempts = 0
        self.connected = 0
        self.active = 0
        self.risk_352 = 0
        self.failures = 0

    def attempt(self, room_id):
        with self._lock:
            self.attempts += 1
            attempt_number = self.attempts
            # 每 10 次显示一次进度；首个连接也显示，避免大量重复日志。
            if attempt_number == 1 or attempt_number % 10 == 0:
                print(
                    f"🔢 WS 连接计数：尝试 {attempt_number} | 成功 {self.connected} | "
                    f"活跃 {self.active} | -352 {self.risk_352}"
                )
            return attempt_number

    def connection_opened(self):
        with self._lock:
            self.connected += 1
            self.active += 1

    def connection_closed(self):
        with self._lock:
            self.active = max(0, self.active - 1)

    def active_count(self):
        """返回当前已经成功建立的 WebSocket 连接数。"""
        with self._lock:
            return self.active

    def record_352(self, attempt_number, room_id):
        with self._lock:
            self.risk_352 += 1
            print(
                f"⚠️ -352 连接计数：第 {attempt_number} 次请求 | 房间 {room_id} | "
                f"成功 {self.connected} | 活跃 {self.active} | 累计 -352 {self.risk_352}"
            )

    def record_failure(self):
        with self._lock:
            self.failures += 1


class DanmuInfoRateLimiter:
    """限制 getDanmuInfo 调用，避免短时间内集中请求 WS token。"""

    def __init__(self, max_requests=20, window_seconds=60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._connection_times = deque()
        self._lock = threading.Lock()

    def wait_for_slot(self, stop_event):
        while not stop_event.is_set():
            with self._lock:
                now = time.monotonic()
                while (
                    self._connection_times
                    and now - self._connection_times[0] >= self.window_seconds
                ):
                    self._connection_times.popleft()
                if len(self._connection_times) < self.max_requests:
                    self._connection_times.append(now)
                    return True
                wait_seconds = self.window_seconds - (now - self._connection_times[0])
            if stop_event.wait(max(0.1, wait_seconds)):
                return False
        return False


class RoomWatcher(threading.Thread):
    """一个入选房间一个线程；所有 token 请求由外部锁串行化。"""

    def __init__(
        self, room, session, token_lock, wbi_keys, reconnect_delay, min_average,
        notifier, notified_lot_ids, notified_lot_ids_lock, connection_stats, danmu_info_rate_limiter,
    ):
        super().__init__(name=f"ws-room-{room['room_id']}", daemon=True)
        self.room = room
        self.session = session
        self.token_lock = token_lock
        self.wbi_keys = wbi_keys
        self.reconnect_delay = reconnect_delay
        self.min_average = min_average
        self.notifier = notifier
        self.notified_lot_ids = notified_lot_ids
        self.notified_lot_ids_lock = notified_lot_ids_lock
        self.connection_stats = connection_stats
        self.danmu_info_rate_limiter = danmu_info_rate_limiter
        self.room_closed = threading.Event()
        self.stop_event = threading.Event()
        self._socket = None

    @property
    def room_id(self):
        return self.room["room_id"]

    def stop(self):
        self.stop_event.set()
        if self._socket is not None:
            self._socket.close()

    def send_discord_notification(self, command):
        """同一红包只通知一次，避免 WebSocket 重连时重复推送。"""
        data = command.get("data") or {}
        lot_id = str(data.get("lot_id") or data.get("red_packet_id") or "")
        if lot_id:
            with self.notified_lot_ids_lock:
                if lot_id in self.notified_lot_ids:
                    return
                self.notified_lot_ids.add(lot_id)

        awards = data.get("awards") or []
        gift_text = "\n".join(
            f"🎁 {item.get('gift_name', '未知礼物')} × {item.get('num', 0)}"
            for item in awards
            if isinstance(item, dict)
        ) or "奖品信息未提供"
        try:
            total_price = int(data.get("total_price", 0)) // 100
        except (TypeError, ValueError):
            total_price = 0
        requirement = {0: "无要求", 1: "需要关注", 2: "需要粉丝勋章", 3: "需要上舰"}.get(
            data.get("join_requirement"), "未知"
        )
        try:
            draw_time = datetime.fromtimestamp(int(data.get("end_time"))).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except (TypeError, ValueError, OSError):
            draw_time = "未知"
        self.notifier.send_lottery_notification(
            self.room["host_name"],
            self.room_id,
            gift_text,
            requirement,
            total_price,
            draw_time,
            sender_name=data.get("sender_name") or data.get("uname") or "",
            rank_info="、".join(self.room["rank_infos"]),
        )

    def _connect(self):
        # getDanmuInfo 是受风控保护的接口，多个房间启动时不能并发突发请求。
        with self.token_lock:
            token, hosts = get_danmu_info(self.session, self.room_id, self.wbi_keys)
        host = hosts[0]
        url = f"wss://{host['host']}:{host['wss_port']}/sub"
        socket = websocket.create_connection(
            url,
            cookie=build_cookie_header(self.session),
            origin="https://live.bilibili.com",
            header=[f"User-Agent: {USER_AGENT}"],
            timeout=40,
        )
        uid = int(get_cookie_value(self.session, "DedeUserID") or 0)
        buvid3 = get_cookie_value(self.session, "buvid3")
        if not uid or not buvid3:
            socket.close()
            raise RuntimeError("登录会话缺少 DedeUserID 或 buvid3，请重新扫码登录。")
        auth = {
            "uid": uid,
            "roomid": int(self.room_id),
            "protover": 2,
            "platform": "web",
            "type": 2,
            "key": token,
            "buvid": buvid3,
        }
        socket.send_binary(build_packet(json.dumps(auth, separators=(",", ":")), OP_AUTH))
        return socket

    def run(self):
        label = f"{self.room['host_name']} | {self.room_id} | {'、'.join(self.room['rank_infos'])}"
        while not self.stop_event.is_set():
            # 限流许可紧邻 getDanmuInfo 请求；每次新连与重连都计入该接口配额。
            if not self.danmu_info_rate_limiter.wait_for_slot(self.stop_event):
                return
            counted_open = False
            attempt_number = self.connection_stats.attempt(self.room_id)
            try:
                self._socket = self._connect()
                self.connection_stats.connection_opened()
                counted_open = True
                next_heartbeat = time.monotonic() + 30
                while not self.stop_event.is_set():
                    self._socket.settimeout(max(1, next_heartbeat - time.monotonic()))
                    try:
                        message = self._socket.recv()
                    except websocket.WebSocketTimeoutException:
                        self._socket.send_binary(build_packet(b"", OP_HEARTBEAT))
                        next_heartbeat = time.monotonic() + 30
                        continue
                    if not message:
                        raise websocket.WebSocketConnectionClosedException("服务器关闭连接")
                    raw = message.encode("utf-8") if isinstance(message, str) else message
                    for command in parse_packets(raw):
                        name = command.get("cmd", "").split(":", 1)[0]
                        if name == "PREPARING":
                            self.room_closed.set()
                            self.stop_event.set()
                            break
                        if name in RED_PACKET_COMMANDS:
                            average = red_packet_average(command)
                            if average is None or average < self.min_average:
                                continue
                            now = time.strftime("%Y-%m-%d %H:%M:%S")
                            print(
                                f"[{now}] 🧧 {RED_PACKET_COMMANDS[name]} | {label} | "
                                f"包均: {average:.2f} 电池 | {red_packet_summary(command)}"
                            )
                            self.send_discord_notification(command)
            except RiskControlError as error:
                self.connection_stats.record_352(attempt_number, self.room_id)
                print(f"⚠️ {label}：{error}")
                self.stop_event.wait(max(60, self.reconnect_delay))
            except (requests.RequestException, RuntimeError, OSError, websocket.WebSocketException) as error:
                self.connection_stats.record_failure()
                if not self.stop_event.is_set():
                    print(f"⚠️ {label}：{error}；{self.reconnect_delay} 秒后重连。")
                    self.stop_event.wait(self.reconnect_delay)
            finally:
                if counted_open:
                    self.connection_stats.connection_closed()
                if self._socket is not None:
                    self._socket.close()
                    self._socket = None


def build_rank_rooms(session, hot_rank_limit):
    """合并人气榜与分区榜，并按房间 ID 去重后返回完整列表。"""
    wbi_keys = get_wbi_keys(session)
    rooms = RoomListBuilder(session, wbi_keys, hot_rank_limit=hot_rank_limit).build(
        include_hot_rank=True, include_popular_ranks=True
    )
    return rooms, wbi_keys


def main():
    parser = argparse.ArgumentParser(
        description="扫描分区人气榜，并实时监听入选房间的人气红包事件"
    )
    parser.add_argument("--account", default="acct1", help="使用的已登录账号名，默认 acct1")
    parser.add_argument(
        "--refresh-seconds", type=int, default=180,
        help="重新检查人气榜和分区榜并更新房间列表的间隔秒数，默认 180（3 分钟）",
    )
    parser.add_argument(
        "--hot-rank-limit", type=int, default=100,
        help="人气榜最多纳入的房间数，默认 100",
    )
    parser.add_argument("--reconnect-delay", type=int, default=10, help="单房间断线重连等待秒数")
    parser.add_argument(
        "--min-average", type=float, default=10,
        help="仅输出包均价值不低于该值的红包，默认 10 电池",
    )
    parser.add_argument(
        "--discord-webhook", default=None,
        help="临时指定 Discord Webhook；指定后自动启用通知",
    )
    parser.add_argument(
        "--connection-start-interval", type=float, default=0.2,
        help="新房间 WebSocket 启动之间的等待秒数，默认 0.2",
    )
    parser.add_argument(
        "--max-get-danmu-info-per-minute", type=int, default=20,
        help="每 60 秒最多调用 getDanmuInfo 的次数，默认 20",
    )
    args = parser.parse_args()
    if args.hot_rank_limit < 1:
        parser.error("--hot-rank-limit 必须至少为 1")
    if args.refresh_seconds < 60:
        parser.error("--refresh-seconds 必须至少为 60")
    if (
        args.reconnect_delay < 1
        or args.connection_start_interval < 0
        or args.min_average < 0
        or args.max_get_danmu_info_per_minute < 1
    ):
        parser.error("重连和启动间隔不能小于要求的最小值")

    session = get_account_session(args.account)
    notifier = DiscordNotifier(args.discord_webhook)
    token_lock = threading.Lock()
    watchers = {}
    notified_lot_ids = set()
    notified_lot_ids_lock = threading.Lock()
    connection_stats = ConnectionStats()
    danmu_info_rate_limiter = DanmuInfoRateLimiter(
        args.max_get_danmu_info_per_minute
    )
    print(
        f"分区榜 WS 红包扫描已启动：账号 {args.account}，"
        "按 Ctrl+C 停止。"
    )
    try:
        while True:
            try:
                all_rooms, wbi_keys = build_rank_rooms(session, args.hot_rank_limit)
            except (requests.RequestException, RuntimeError) as error:
                print(f"⚠️ 刷新分区榜失败：{error}；{RISK_BACKOFF_SECONDS} 秒后重试。")
                time.sleep(RISK_BACKOFF_SECONDS)
                continue

            # 收到下播事件的房间在本次刷新中移出管理列表。
            for room_id, watcher in list(watchers.items()):
                if watcher.room_closed.is_set():
                    watchers.pop(room_id).stop()

            # 榜单中新出现的房间加入监视；房间离开榜单不会被关闭，只有
            # PREPARING 下播事件才会使其断开并在下一次刷新时移出管理列表。
            rooms = all_rooms
            for room in rooms:
                room_id = room["room_id"]
                if room_id in watchers:
                    continue
                watcher = RoomWatcher(
                    room, session, token_lock, wbi_keys, args.reconnect_delay,
                    args.min_average, notifier, notified_lot_ids, notified_lot_ids_lock,
                    connection_stats, danmu_info_rate_limiter,
                )
                watchers[room_id] = watcher
                watcher.start()
                time.sleep(args.connection_start_interval)
            print(
                f"✅ 实际连接 {connection_stats.active_count()} 个，"
                f"管理房间 {len(watchers)} 个；"
                f"{args.refresh_seconds // 60} 分钟后更新榜单。"
            )
            time.sleep(args.refresh_seconds)
    except KeyboardInterrupt:
        print("\n正在停止所有房间监听…")
    finally:
        for watcher in watchers.values():
            watcher.stop()
        for watcher in watchers.values():
            watcher.join(timeout=3)


if __name__ == "__main__":
    main()
