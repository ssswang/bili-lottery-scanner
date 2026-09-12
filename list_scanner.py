# -*- coding: utf-8 -*-
"""扫描分区人气榜，并用 WebSocket 实时监听入选房间的人气红包事件。"""

import argparse
import json
import random
import threading
import time
from collections import deque
from datetime import datetime

import requests

from auth_manager import USER_AGENT, build_cookie_header, get_cookie_value, get_wbi_keys
from config import (
    ANCHOR_LOTTERY_MIN_AVERAGE,
    PROCESS_ANCHOR_LOTTERY,
    RED_PACKET_MIN_AVERAGE,
    RISK_BACKOFF_SECONDS,
    ROOM_BLACKLIST,
)
from discord_notifier import DiscordNotifier
from local_database import LocalDatabase
from room_lists import RoomListBuilder
from room_watcher import (
    OP_AUTH,
    OP_HEARTBEAT,
    ANCHOR_LOTTERY_COMMANDS,
    RED_PACKET_COMMANDS,
    RiskControlError,
    anchor_lottery_details,
    anchor_lottery_summary,
    build_packet,
    build_wss_url,
    get_account_session,
    get_danmu_info,
    parse_auth_reply,
    parse_packets,
    red_packet_average,
    red_packet_summary,
    websocket,
)


MAX_HIGH_ENERGY_USERS = 500
MAX_CUMULATIVE_WATCHERS = 10_000
DEFAULT_MAX_ACTIVE_ROOMS = 2000
MAX_RISK_352_BEFORE_DANMU_INFO_STOP = 100


class ConnectionStats:
    """线程安全地汇总本次运行的 WS 建连与风控情况。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.attempts = 0
        self.connected = 0
        self.active = 0
        self.risk_352 = 0
        self.failures = 0
        self.danmu_info_requests = 0
        self.danmu_info_blocked = False

    def attempt(self, room_id):
        with self._lock:
            self.attempts += 1
            return self.attempts

    def connection_opened(self):
        with self._lock:
            self.connected += 1
            self.active += 1
            # 缓存房间会在短时间内并行发起大量连接；只汇报实际成功的里程碑，
            # 避免输出尚未连接成功的“尝试”计数。
            if self.connected == 1 or self.connected % 100 == 0:
                print(
                    f"🔌 WS 已连接 {self.connected} | 当前活跃 {self.active} | "
                    f"getDanmuInfo {self.danmu_info_requests} | "
                    f"-352 {self.risk_352}"
                )

    def connection_closed(self):
        with self._lock:
            self.active = max(0, self.active - 1)

    def active_count(self):
        """返回当前已经成功建立的 WebSocket 连接数。"""
        with self._lock:
            return self.active

    def record_danmu_info_request(self):
        """仅在真正发出 getDanmuInfo HTTP 请求时计数，缓存命中不计。"""
        with self._lock:
            self.danmu_info_requests += 1

    def danmu_info_request_count(self):
        with self._lock:
            return self.danmu_info_requests

    def is_danmu_info_blocked(self):
        """达到 -352 阈值后，禁止新的 getDanmuInfo 请求但不影响缓存重连。"""
        with self._lock:
            return self.danmu_info_blocked

    def record_352(self, attempt_number, room_id):
        with self._lock:
            self.risk_352 += 1
            print(
                f"⚠️ -352 连接计数：第 {attempt_number} 次请求 | 房间 {room_id} | "
                f"成功 {self.connected} | 活跃 {self.active} | "
                f"getDanmuInfo {self.danmu_info_requests} | 累计 -352 {self.risk_352}"
            )
            if (
                self.risk_352 >= MAX_RISK_352_BEFORE_DANMU_INFO_STOP
                and not self.danmu_info_blocked
            ):
                self.danmu_info_blocked = True
                print(
                    f"⛔ 累计 -352 已达 {MAX_RISK_352_BEFORE_DANMU_INFO_STOP} 次："
                    "停止新的 getDanmuInfo 请求；已缓存的房间仍可重连。"
                )

    def record_failure(self):
        with self._lock:
            self.failures += 1


class ConnectionCapacity:
    """限制实际 WebSocket 连接数，连接建立和重连都必须先取得一个名额。"""

    def __init__(self, maximum):
        self.maximum = maximum
        self._slots = threading.BoundedSemaphore(maximum)

    def acquire(self, stop_event):
        while not stop_event.is_set():
            if self._slots.acquire(timeout=0.5):
                return True
        return False

    def release(self):
        self._slots.release()


class TokenRejectedError(RuntimeError):
    """服务端明确拒绝或持续无法确认缓存 token 的鉴权。"""


class DanmuInfoRateLimiter:
    """均匀、带抖动地限制 getDanmuInfo 调用，避免短时间内集中请求。"""

    def __init__(self, max_requests=10, window_seconds=60, jitter_seconds=1.5):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.jitter_seconds = jitter_seconds
        self.minimum_interval = window_seconds / max_requests
        self._connection_times = deque()
        self._next_allowed_time = 0.0
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
                interval_wait = self._next_allowed_time - now
                if len(self._connection_times) < self.max_requests and interval_wait <= 0:
                    self._connection_times.append(now)
                    self._next_allowed_time = now + self.minimum_interval + random.uniform(
                        0, self.jitter_seconds
                    )
                    return True
                window_wait = (
                    self.window_seconds - (now - self._connection_times[0])
                    if len(self._connection_times) >= self.max_requests
                    else 0
                )
                wait_seconds = max(interval_wait, window_wait, 0.1)
            if stop_event.wait(max(0.1, wait_seconds)):
                return False
        return False


def exceeds_room_activity_limit(command_name, command):
    """判断 WS 推送的高能用户或累计看过人数是否超过连接保留阈值。"""
    data = command.get("data") or {}
    if command_name == "ONLINE_RANK_COUNT":
        value, limit = data.get("count"), MAX_HIGH_ENERGY_USERS
    elif command_name == "WATCHED_CHANGE":
        value, limit = data.get("num"), MAX_CUMULATIVE_WATCHERS
    else:
        return False
    try:
        return int(value) > limit
    except (TypeError, ValueError):
        return False


class RoomWatcher(threading.Thread):
    """一个入选房间一个线程；优先复用数据库内长期保存的 token。"""

    def __init__(
        self, room, account_name, session, token_lock, wbi_keys, reconnect_delay, min_average,
        min_anchor_average,
        notifier, database, notified_lot_ids, notified_lot_ids_lock, connection_stats,
        danmu_info_rate_limiter, connection_capacity,
    ):
        super().__init__(name=f"ws-room-{room['room_id']}", daemon=True)
        self.room = room
        self.account_name = account_name
        self.session = session
        self.token_lock = token_lock
        self.wbi_keys = wbi_keys
        self.reconnect_delay = reconnect_delay
        self.min_average = min_average
        self.min_anchor_average = min_anchor_average
        self.notifier = notifier
        self.database = database
        self.notified_lot_ids = notified_lot_ids
        self.notified_lot_ids_lock = notified_lot_ids_lock
        self.connection_stats = connection_stats
        self.danmu_info_rate_limiter = danmu_info_rate_limiter
        self.connection_capacity = connection_capacity
        self.room_closed = threading.Event()
        self.stop_event = threading.Event()
        self._socket = None
        self._host_index = 0
        self._connection_state_lock = threading.Lock()
        self._connected_since = None
        self._used_cached_auth = False
        self._auth_reply_received = False
        self._cached_auth_failures = 0

    @property
    def room_id(self):
        return self.room["room_id"]

    def stop(self):
        self.stop_event.set()
        if self._socket is not None:
            self._socket.close()

    def mark_connected(self):
        with self._connection_state_lock:
            self._connected_since = time.monotonic()

    def mark_disconnected(self):
        with self._connection_state_lock:
            self._connected_since = None

    def connected_since(self):
        """返回当前连接的开始时间；未连接时返回 None。"""
        with self._connection_state_lock:
            return self._connected_since

    def send_discord_notification(self, command):
        """同一红包只通知一次，避免 WebSocket 重连时重复推送。"""
        data = command.get("data") or {}
        lot_id = str(data.get("lot_id") or data.get("red_packet_id") or "")
        if lot_id:
            with self.notified_lot_ids_lock:
                notification_id = f"red:{self.room_id}:{lot_id}"
                if notification_id in self.notified_lot_ids:
                    return
                self.notified_lot_ids.add(notification_id)

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
            area_info="、".join(self.room.get("area_names") or []),
        )

    def send_anchor_lottery_notification(self, command):
        """天选开始事件只通知一次，并复用统一的 Discord 抽奖通知格式。"""
        details = anchor_lottery_details(command)
        if details is None:
            return
        lottery_id = details["lot_id"]
        if lottery_id:
            with self.notified_lot_ids_lock:
                notification_id = f"anchor:{self.room_id}:{lottery_id}"
                if notification_id in self.notified_lot_ids:
                    return
                self.notified_lot_ids.add(notification_id)
        self.notifier.send_lottery_notification(
            self.room["host_name"],
            self.room_id,
            details["gift_text"],
            details["requirement"],
            details["total_price"],
            details["draw_time"],
            area_info="、".join(self.room.get("area_names") or []),
        )

    def _get_auth_info(self):
        """优先复用数据库 token；未命中或失效后才占用接口限流额度。"""
        cached = self.database.load_ws_auth_cache(self.account_name, self.room_id)
        if cached:
            self._used_cached_auth = True
            return cached
        self._used_cached_auth = False
        if self.connection_stats.is_danmu_info_blocked():
            return None
        if not self.danmu_info_rate_limiter.wait_for_slot(self.stop_event):
            return None
        # 排队等待限流期间，其他执行路径可能已经写入缓存，因此再次确认。
        with self.token_lock:
            cached = self.database.load_ws_auth_cache(self.account_name, self.room_id)
            if cached:
                self._used_cached_auth = True
                return cached
            if self.connection_stats.is_danmu_info_blocked():
                return None
            self.connection_stats.record_danmu_info_request()
            token, hosts = get_danmu_info(self.session, self.room_id, self.wbi_keys)
            self.database.save_ws_auth_cache(self.account_name, self.room_id, token, hosts)
            self._used_cached_auth = False
            return token, hosts

    def invalidate_cached_auth(self):
        self.database.delete_ws_auth_cache(self.account_name, self.room_id)
        self._cached_auth_failures = 0

    def _connect(self, auth_info):
        token, hosts = auth_info
        host = hosts[self._host_index % len(hosts)]
        self._host_index += 1
        url = build_wss_url(host)
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
        label = f"{self.room['host_name']} | {self.room_id}"
        while not self.stop_event.is_set():
            counted_open = False
            slot_acquired = False
            attempt_number = self.connection_stats.attempt(self.room_id)
            try:
                auth_info = self._get_auth_info()
                if auth_info is None:
                    return
                if not self.connection_capacity.acquire(self.stop_event):
                    return
                slot_acquired = True
                self._auth_reply_received = False
                self._socket = self._connect(auth_info)
                self.connection_stats.connection_opened()
                counted_open = True
                self.mark_connected()
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
                    auth_reply = parse_auth_reply(raw)
                    if auth_reply is not None:
                        self._auth_reply_received = True
                        if auth_reply.get("code") != 0:
                            message = auth_reply.get("message") or auth_reply.get("msg") or "未提供原因"
                            raise TokenRejectedError(
                                f"WebSocket 鉴权被拒绝：code={auth_reply.get('code')}，{message}"
                            )
                        self._cached_auth_failures = 0
                        continue
                    for command in parse_packets(raw):
                        name = command.get("cmd", "").split(":", 1)[0]
                        if (
                            name in {"PREPARING", "CUT_OFF"}
                            or exceeds_room_activity_limit(name, command)
                        ):
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
                            try:
                                self.database.save_red_packet(self.room, command, average)
                            except Exception as error:
                                print(f"⚠️ 本地数据库写入失败：{error}")
                            self.send_discord_notification(command)
                        elif PROCESS_ANCHOR_LOTTERY and name in ANCHOR_LOTTERY_COMMANDS:
                            details = anchor_lottery_details(command)
                            if (
                                details is None
                                or details["average_value"] < self.min_anchor_average
                            ):
                                continue
                            now = time.strftime("%Y-%m-%d %H:%M:%S")
                            print(
                                f"[{now}] 🟪 {ANCHOR_LOTTERY_COMMANDS[name]} | {label} | "
                                f"{anchor_lottery_summary(command)}"
                            )
                            try:
                                self.database.save_anchor_event(self.room, command, details)
                            except Exception as error:
                                print(f"⚠️ 本地数据库写入失败：{error}")
                            self.send_anchor_lottery_notification(command)
            except TokenRejectedError as error:
                self.invalidate_cached_auth()
                if not self.stop_event.is_set():
                    print(f"⚠️ {label}：{error}；将重新获取鉴权信息。")
                    self.stop_event.wait(self.reconnect_delay)
            except RiskControlError as error:
                self.connection_stats.record_352(attempt_number, self.room_id)
                print(f"⚠️ {label}：{error}")
                self.stop_event.wait(max(60, self.reconnect_delay))
            except (requests.RequestException, RuntimeError, OSError, websocket.WebSocketException) as error:
                self.connection_stats.record_failure()
                if self._used_cached_auth and not self._auth_reply_received:
                    self._cached_auth_failures += 1
                    if self._cached_auth_failures >= 2:
                        self.invalidate_cached_auth()
                if not self.stop_event.is_set():
                    print(f"⚠️ {label}：{error}；{self.reconnect_delay} 秒后重连。")
                    self.stop_event.wait(self.reconnect_delay)
            finally:
                if counted_open:
                    self.connection_stats.connection_closed()
                    self.mark_disconnected()
                if self._socket is not None:
                    self._socket.close()
                    self._socket = None
                if slot_acquired:
                    self.connection_capacity.release()


def build_rank_rooms(session, hot_rank_limit):
    """合并人气榜与分区榜，并按房间 ID 去重后返回完整列表。"""
    wbi_keys = get_wbi_keys(session)
    rooms = RoomListBuilder(session, wbi_keys, hot_rank_limit=hot_rank_limit).build(
        include_hot_rank=True, include_popular_ranks=True
    )
    rooms = [room for room in rooms if room["room_id"] not in ROOM_BLACKLIST]
    return rooms, wbi_keys


def oldest_connected_watcher(watchers):
    """选出本轮最早成功建立 WS 连接、且仍在线的房间。"""
    candidates = []
    for watcher in watchers.values():
        connected_since = watcher.connected_since()
        if connected_since is not None:
            candidates.append((connected_since, watcher.room_id, watcher))
    return min(candidates, default=None, key=lambda item: item[:2])


def prioritize_rooms_by_cached_auth(rooms, database, account_name):
    """缓存鉴权房间优先建连，未命中缓存的房间留给 HTTP 限流队列。"""
    cached_rooms = []
    uncached_rooms = []
    for room in rooms:
        if database.load_ws_auth_cache(account_name, room["room_id"]):
            cached_rooms.append(room)
        else:
            uncached_rooms.append(room)
    return cached_rooms, uncached_rooms


def main():
    parser = argparse.ArgumentParser(
        description="扫描分区人气榜，并实时监听入选房间的红包与可选天选事件"
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
        "--min-average", type=float, default=RED_PACKET_MIN_AVERAGE,
        help="仅输出包均价值不低于该值的红包，默认读取 RED_PACKET_MIN_AVERAGE",
    )
    parser.add_argument(
        "--min-anchor-average", type=float, default=ANCHOR_LOTTERY_MIN_AVERAGE,
        help="仅输出包均价值不低于该值的天选，默认读取 ANCHOR_LOTTERY_MIN_AVERAGE",
    )
    parser.add_argument(
        "--discord-webhook", default=None,
        help="临时指定 Discord Webhook；指定后自动启用通知",
    )
    parser.add_argument(
        "--database", default="red_packet_monitor.db",
        help="本地 SQLite 数据库文件路径，默认 red_packet_monitor.db",
    )
    parser.add_argument(
        "--connection-start-interval", type=float, default=3.0,
        help="新房间 WebSocket 启动之间的基础等待秒数，默认 3",
    )
    parser.add_argument(
        "--connection-start-jitter", type=float, default=1.0,
        help="新房间启动额外随机等待的最大秒数，默认 1",
    )
    parser.add_argument(
        "--max-get-danmu-info-per-minute", type=int, default=10,
        help="每 60 秒最多调用 getDanmuInfo 的次数，默认 10",
    )
    parser.add_argument(
        "--get-danmu-info-jitter", type=float, default=1.5,
        help="两次 getDanmuInfo 之间的额外随机等待最大秒数，默认 1.5",
    )
    parser.add_argument(
        "--max-active-rooms", type=int, default=DEFAULT_MAX_ACTIVE_ROOMS,
        help="实际同时保持的 WebSocket 连接上限，默认 2000",
    )
    args = parser.parse_args()
    if args.hot_rank_limit < 1:
        parser.error("--hot-rank-limit 必须至少为 1")
    if args.connection_start_interval < 0 or args.connection_start_jitter < 0:
        parser.error("连接启动等待时间不能小于 0")
    if (
        args.max_get_danmu_info_per_minute < 1
        or args.get_danmu_info_jitter < 0
        or args.max_active_rooms < 1
    ):
        parser.error("getDanmuInfo 限流或活跃连接参数无效")
    if args.refresh_seconds < 60:
        parser.error("--refresh-seconds 必须至少为 60")
    if (
        args.reconnect_delay < 1
        or args.connection_start_interval < 0
        or args.min_average < 0
        or args.min_anchor_average < 0
        or args.max_get_danmu_info_per_minute < 1
    ):
        parser.error("重连和启动间隔不能小于要求的最小值")

    session = get_account_session(args.account)
    notifier = DiscordNotifier(args.discord_webhook)
    database = LocalDatabase(args.database)
    token_lock = threading.Lock()
    watchers = {}
    notified_lot_ids = set()
    notified_lot_ids_lock = threading.Lock()
    connection_stats = ConnectionStats()
    danmu_info_rate_limiter = DanmuInfoRateLimiter(
        args.max_get_danmu_info_per_minute,
        jitter_seconds=args.get_danmu_info_jitter,
    )
    connection_capacity = ConnectionCapacity(args.max_active_rooms)
    print(
        f"分区榜 WS 红包扫描已启动：账号 {args.account}，"
        f"天选事件：{'开启' if PROCESS_ANCHOR_LOTTERY else '关闭'}，"
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
            # 下播、切断，或触发人数阈值的房间会在下一次刷新时移出管理列表。
            cached_rooms, uncached_rooms = prioritize_rooms_by_cached_auth(
                all_rooms, database, args.account
            )
            # 先快速启动可直接连 WS 的缓存房间；只有未命中缓存的房间才受
            # 新房间启动间隔影响，并在各自线程中排队等待 getDanmuInfo 限流。
            rooms = [(room, True) for room in cached_rooms] + [
                (room, False) for room in uncached_rooms
            ]
            scheduled_cached = 0
            scheduled_uncached = 0
            for room, has_cached_auth in rooms:
                room_id = room["room_id"]
                if room_id in watchers:
                    continue
                # 使用真实已建立的连接数判断是否满额。名额满时只替换一间，
                # 新房间会等待旧连接释放槽位，因此任何时刻均不会超过上限。
                if connection_stats.active_count() >= args.max_active_rooms:
                    oldest = oldest_connected_watcher(watchers)
                    if oldest is None:
                        # 连接正在建立、尚无可替换对象时，不累积等待中的房间线程。
                        break
                    _, old_room_id, old_watcher = oldest
                    watchers.pop(old_room_id, None)
                    old_watcher.stop()
                    old_watcher.join(timeout=3)
                    if old_watcher.is_alive():
                        # 尚未真正释放连接时，留到下一轮刷新再加入，避免一口气关闭多间。
                        break
                    print(
                        f"🔄 活跃连接已达 {args.max_active_rooms}："
                        f"关闭连接最久的房间 {old_room_id}，加入房间 {room_id}。"
                    )
                watcher = RoomWatcher(
                    room, args.account, session, token_lock, wbi_keys, args.reconnect_delay,
                    args.min_average, args.min_anchor_average, notifier, database,
                    notified_lot_ids, notified_lot_ids_lock,
                    connection_stats, danmu_info_rate_limiter, connection_capacity,
                )
                watchers[room_id] = watcher
                watcher.start()
                if not has_cached_auth:
                    scheduled_uncached += 1
                    time.sleep(
                        args.connection_start_interval
                        + random.uniform(0, args.connection_start_jitter)
                    )
                else:
                    scheduled_cached += 1
            danmu_info_status = (
                "新 getDanmuInfo 请求已停止；"
                if connection_stats.is_danmu_info_blocked()
                else ""
            )
            print(
                f"📊 本轮新增：缓存鉴权 {scheduled_cached}，待取鉴权 {scheduled_uncached} | "
                f"实际连接 {connection_stats.active_count()}，管理房间 {len(watchers)} | "
                f"getDanmuInfo 累计 {connection_stats.danmu_info_request_count()} 次；"
                f"{danmu_info_status}"
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
        database.close()


if __name__ == "__main__":
    main()
