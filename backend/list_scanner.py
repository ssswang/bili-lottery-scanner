# -*- coding: utf-8 -*-
"""异步扫描直播榜单，并通过 WebSocket 监听红包和可选天选事件。"""

import argparse
import asyncio
import json
import random
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import requests

try:
    import aiohttp
except ImportError as error:
    raise SystemExit("缺少 aiohttp。请执行：pip install -r requirements.txt") from error

from backend.auth.api_auth import USER_AGENT, build_cookie_header, get_cookie_value, get_wbi_keys
from backend.config import ANCHOR_LOTTERY_MIN_AVERAGE, PROCESS_ANCHOR_LOTTERY, RED_PACKET_MIN_AVERAGE, RED_PACKET_SOUND_ENABLED, RISK_BACKOFF_SECONDS, ROOM_BLACKLIST
from backend.database import LocalDatabase
from backend.area_room_queue import AreaRoomQueueBuilder
from backend.room_watcher import ANCHOR_LOTTERY_COMMANDS, OP_AUTH, OP_HEARTBEAT, RED_PACKET_COMMANDS, ROOM_STOP_COMMANDS, anchor_lottery_details, anchor_lottery_summary, build_packet, build_wss_url, get_account_session, parse_auth_reply, parse_packets, red_packet_average, red_packet_summary
from backend.auth.ws_auth import RiskControlError, get_danmu_info


MAX_HIGH_ENERGY_USERS = 500
MAX_CUMULATIVE_WATCHERS = 10_000
DEFAULT_MAX_ACTIVE_ROOMS = 1500
MAX_ROOM_CONNECTION_STARTS_PER_MINUTE = 100
MAX_CONSECUTIVE_352_BEFORE_DANMU_INFO_STOP = 2
AUTH_REPLY_TIMEOUT_SECONDS = 15
HEARTBEAT_INTERVAL_SECONDS = 30
STATUS_UPDATE_SECONDS = 180
BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}")


def play_red_packet_alert():
    """非阻塞播放 Windows 系统提示音；非 Windows 平台或播放失败时静默跳过。"""
    if not RED_PACKET_SOUND_ENABLED:
        return
    try:
        import winsound

        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except (ImportError, RuntimeError):
        pass


class TokenRejectedError(RuntimeError):
    pass


class AuthConfirmationTimeout(RuntimeError):
    pass


class ConnectionStats:
    def __init__(self):
        self.attempts = self.connected = self.active = self.risk_352 = 0
        self.consecutive_risk_352 = self.danmu_info_requests = 0
        self.danmu_info_blocked = False
        self.danmu_info_blocked_until = None
        self.auth_confirmed = self.cached_auth_confirmed = 0
        self.cached_auth_failed = self.auth_timeouts = 0

    def attempt(self):
        self.attempts += 1
        return self.attempts

    def record_danmu_info_request(self):
        self.danmu_info_requests += 1

    def record_danmu_info_success(self):
        self.consecutive_risk_352 = 0

    def record_352(self, room_id, attempt_number):
        self.risk_352 += 1
        self.consecutive_risk_352 += 1
        log(f"⚠️ -352 | 房间 {room_id} | 请求 {attempt_number} | 累计 {self.risk_352}／连续 {self.consecutive_risk_352}")
        if self.consecutive_risk_352 >= MAX_CONSECUTIVE_352_BEFORE_DANMU_INFO_STOP:
            self.danmu_info_blocked = True
            now = datetime.now(BEIJING_TIMEZONE)
            tomorrow = now.date() + timedelta(days=1)
            self.danmu_info_blocked_until = datetime.combine(
                tomorrow, datetime.min.time(), tzinfo=BEIJING_TIMEZONE
            )
            log(
                f"⛔ 连续 -352 已达 {MAX_CONSECUTIVE_352_BEFORE_DANMU_INFO_STOP} 次："
                f"暂停新的 getDanmuInfo 至北京时间 {self.danmu_info_blocked_until:%Y-%m-%d %H:%M:%S}；"
                "已缓存房间继续监听。"
            )
            return True
        return False

    def seconds_until_danmu_info_resume(self):
        """返回风控暂停剩余时间；到北京时间零点后自动解除暂停。"""
        if not self.danmu_info_blocked:
            return 0
        now = datetime.now(BEIJING_TIMEZONE)
        remaining = (self.danmu_info_blocked_until - now).total_seconds() if self.danmu_info_blocked_until else 0
        if remaining > 0:
            return remaining
        self.danmu_info_blocked = False
        self.danmu_info_blocked_until = None
        self.consecutive_risk_352 = 0
        log("▶️ 已到北京时间 00:00：恢复 getDanmuInfo 请求。")
        return 0

    def record_auth_confirmed(self, used_cached_auth):
        self.auth_confirmed += 1
        self.connected += 1
        self.active += 1
        if used_cached_auth:
            self.cached_auth_confirmed += 1
        if self.connected == 1 or self.connected % 100 == 0:
            log(f"🔌 房间连接进度：当前已连接房间 {self.active} | 本次启动连接成功次数 {self.connected} | 使用缓存 token 连接成功 {self.cached_auth_confirmed}")

    def record_connection_closed(self):
        self.active = max(0, self.active - 1)

    def record_auth_failure(self, used_cached_auth, timed_out=False):
        if used_cached_auth:
            self.cached_auth_failed += 1
        if timed_out:
            self.auth_timeouts += 1


class AsyncDanmuInfoRateLimiter:
    def __init__(self, max_requests=6, window_seconds=60, jitter_seconds=1.5):
        self.max_requests, self.window_seconds, self.jitter_seconds = max_requests, window_seconds, jitter_seconds
        self.minimum_interval = window_seconds / max_requests
        self.request_times, self.next_allowed_time = deque(), 0.0
        self.lock = asyncio.Lock()

    async def wait_for_slot(self, stop_event):
        while not stop_event.is_set():
            async with self.lock:
                now = time.monotonic()
                while self.request_times and now - self.request_times[0] >= self.window_seconds:
                    self.request_times.popleft()
                interval_wait = self.next_allowed_time - now
                if len(self.request_times) < self.max_requests and interval_wait <= 0:
                    self.request_times.append(now)
                    self.next_allowed_time = now + self.minimum_interval + random.uniform(0, self.jitter_seconds)
                    return True
                window_wait = self.window_seconds - (now - self.request_times[0]) if len(self.request_times) >= self.max_requests else 0
                wait_seconds = max(interval_wait, window_wait, 0.1)
            try:
                await asyncio.wait_for(stop_event.wait(), wait_seconds)
            except asyncio.TimeoutError:
                pass
        return False


def exceeds_room_activity_limit(name, command):
    data = command.get("data") or {}
    value, limit = (data.get("count"), MAX_HIGH_ENERGY_USERS) if name == "ONLINE_RANK_COUNT" else (data.get("num"), MAX_CUMULATIVE_WATCHERS) if name == "WATCHED_CHANGE" else (None, None)
    try:
        return value is not None and int(value) > limit
    except (TypeError, ValueError):
        return False


def online_rank_count(command):
    """读取 ONLINE_RANK_COUNT 中的高能用户人数；字段缺失或异常时返回 None。"""
    try:
        return int((command.get("data") or {}).get("count"))
    except (TypeError, ValueError):
        return None


def build_area_rooms(session):
    wbi_keys = get_wbi_keys(session)
    rooms = AreaRoomQueueBuilder(session).build()
    return [room for room in rooms if room["room_id"] not in ROOM_BLACKLIST], wbi_keys


class AsyncListScanner:
    def __init__(self, args):
        self.args = args
        self.session = get_account_session(args.account)
        self.database = LocalDatabase(args.database)
        self.stop_event, self.http_lock = asyncio.Event(), asyncio.Lock()
        self.connection_slots = asyncio.Semaphore(args.max_active_rooms)
        self.rate_limiter = AsyncDanmuInfoRateLimiter(args.max_get_danmu_info_per_minute, jitter_seconds=args.get_danmu_info_jitter)
        self.connection_start_limiter = AsyncDanmuInfoRateLimiter(
            MAX_ROOM_CONNECTION_STARTS_PER_MINUTE, jitter_seconds=0
        )
        self.stats, self.wbi_keys = ConnectionStats(), None
        self.token_room_queue, self.queued_room_ids = asyncio.Queue(), set()
        self.pending_token_room_ids = set()
        self.room_workers, self.cached_room_tasks = [], set()
        self.closed_room_ids, self.notified_lot_ids = set(), set()
        self.low_online_room_ids = set()
        self.database_queue = asyncio.Queue()
        self.latest_cached_rooms = self.latest_uncached_rooms = 0

    def status_snapshot(self, running=True):
        return {
            "running": running,
            "cached_rooms": self.latest_cached_rooms,
            "uncached_rooms": self.latest_uncached_rooms,
            "token_queue_size": len(self.pending_token_room_ids),
            "active_connections": self.stats.active,
            "total_connections": self.stats.auth_confirmed,
            "cached_token_success": self.stats.cached_auth_confirmed,
            "cached_token_failure": self.stats.cached_auth_failed,
            "auth_timeouts": self.stats.auth_timeouts,
            "new_token_requests": self.stats.danmu_info_requests,
        }

    async def status_updater(self):
        while not self.stop_event.is_set():
            try:
                await asyncio.to_thread(self.database.save_scanner_status, self.status_snapshot())
            except Exception as error:
                log(f"⚠️ 扫描器状态写入失败：{error}")
            try:
                await asyncio.wait_for(self.stop_event.wait(), STATUS_UPDATE_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def database_worker(self):
        while True:
            job = await self.database_queue.get()
            try:
                if job is None:
                    return
                method, arguments = job
                await asyncio.to_thread(method, *arguments)
            except Exception as error:
                log(f"⚠️ 本地数据库写入失败：{error}")
            finally:
                self.database_queue.task_done()

    async def load_cache(self, room_id):
        return await asyncio.to_thread(self.database.load_ws_auth_cache, self.args.account, room_id)

    async def wait_for_danmu_info_resume(self):
        """暂停期间保留无缓存房间任务，零点恢复后继续按原速率获取 token。"""
        while not self.stop_event.is_set():
            wait_seconds = self.stats.seconds_until_danmu_info_resume()
            if wait_seconds <= 0:
                return True
            try:
                await asyncio.wait_for(self.stop_event.wait(), wait_seconds)
            except asyncio.TimeoutError:
                pass
        return False

    async def get_auth_info(self, room_id):
        while not self.stop_event.is_set():
            cached = await self.load_cache(room_id)
            if cached:
                self.pending_token_room_ids.discard(room_id)
                return cached, True
            self.pending_token_room_ids.add(room_id)
            if not await self.wait_for_danmu_info_resume():
                self.pending_token_room_ids.discard(room_id)
                return None, False
            if not await self.rate_limiter.wait_for_slot(self.stop_event):
                self.pending_token_room_ids.discard(room_id)
                return None, False
            async with self.http_lock:
                cached = await self.load_cache(room_id)
                if cached:
                    self.pending_token_room_ids.discard(room_id)
                    return cached, True
                if self.stats.danmu_info_blocked:
                    continue
                self.stats.record_danmu_info_request()
                token, hosts = await asyncio.to_thread(get_danmu_info, self.session, room_id, self.wbi_keys)
                self.stats.record_danmu_info_success()
                await asyncio.to_thread(self.database.save_ws_auth_cache, self.args.account, room_id, token, hosts)
                self.pending_token_room_ids.discard(room_id)
                return (token, hosts), False
        self.pending_token_room_ids.discard(room_id)
        return None, False

    async def invalidate_cache(self, room_id):
        await asyncio.to_thread(self.database.delete_ws_auth_cache, self.args.account, room_id)

    async def queue_worker(self, worker_number):
        """处理需要新 token 的房间；缓存命中房间由独立任务直接连接。"""
        while not self.stop_event.is_set():
            room = await self.token_room_queue.get()
            room_id = room["room_id"]
            try:
                if room_id not in self.closed_room_ids:
                    await self.watch_room(room)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log(f"⚠️ 队列工作协程 {worker_number} | 房间 {room_id} 异常：{error}")
            finally:
                self.queued_room_ids.discard(room_id)
                self.pending_token_room_ids.discard(room_id)
                self.token_room_queue.task_done()

    async def cached_room_worker(self, room):
        """缓存 token 命中后立即尝试建立连接，不等待新 token 队列。"""
        room_id = room["room_id"]
        try:
            if room_id not in self.closed_room_ids:
                await self.watch_room(room)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log(f"⚠️ 缓存 token 房间 {room_id} 异常：{error}")
        finally:
            self.queued_room_ids.discard(room_id)
            self.pending_token_room_ids.discard(room_id)

    async def enqueue_red_packet(self, room, command, average):
        data, room_id = command.get("data") or {}, room["room_id"]
        lot_id = str(data.get("lot_id") or data.get("red_packet_id") or "")
        key = f"red:{lot_id}"
        if lot_id and key in self.notified_lot_ids:
            return False
        if lot_id:
            self.notified_lot_ids.add(key)
        await self.database_queue.put((self.database.save_red_packet, (room, command, average)))
        return True

    async def enqueue_anchor_lottery(self, room, command, details):
        room_id, lot_id = room["room_id"], details["lot_id"]
        key = f"anchor:{room_id}:{lot_id}"
        if lot_id and key in self.notified_lot_ids:
            return
        if lot_id:
            self.notified_lot_ids.add(key)
        await self.database_queue.put((self.database.save_anchor_event, (room, command, details)))

    async def receive(self, websocket, timeout):
        try:
            message = await asyncio.wait_for(websocket.receive(), timeout)
        except asyncio.TimeoutError:
            return None
        if message.type == aiohttp.WSMsgType.BINARY:
            return message.data
        if message.type == aiohttp.WSMsgType.TEXT:
            return message.data.encode("utf-8")
        if message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED}:
            code = message.data or websocket.close_code or "未知"
            reason = message.extra or "未提供原因"
            raise aiohttp.ClientConnectionError(f"服务器关闭连接（code={code}，reason={reason}）")
        if message.type == aiohttp.WSMsgType.ERROR:
            raise websocket.exception() or aiohttp.ClientConnectionError("WebSocket 错误")
        return b""

    async def send_heartbeats(self, websocket):
        """独立发送 B 站协议心跳，不能由 aiohttp 的 WebSocket ping 替代。"""
        while not self.stop_event.is_set() and not websocket.closed:
            try:
                await asyncio.wait_for(self.stop_event.wait(), HEARTBEAT_INTERVAL_SECONDS)
                return
            except asyncio.TimeoutError:
                await websocket.send_bytes(build_packet(b"", OP_HEARTBEAT))

    async def acquire_connection_slot(self, room_id):
        """等待连接名额，并限制新连接启动频率。"""
        await self.connection_slots.acquire()
        if await self.connection_start_limiter.wait_for_slot(self.stop_event):
            return True
        self.connection_slots.release()
        return False

    async def watch_connection(self, room, auth_info, used_cached_auth):
        room_id = room["room_id"]
        token, hosts = auth_info
        uid, buvid3 = int(get_cookie_value(self.session, "DedeUserID") or 0), get_cookie_value(self.session, "buvid3")
        if not uid or not buvid3:
            raise RuntimeError("登录会话缺少 DedeUserID 或 buvid3，请重新扫码登录。")
        auth = {"uid": uid, "roomid": int(room_id), "protover": 2, "platform": "web", "type": 2, "key": token, "buvid": buvid3}
        headers = {"Cookie": build_cookie_header(self.session), "Origin": "https://live.bilibili.com", "User-Agent": USER_AGENT}
        if not await self.acquire_connection_slot(room_id):
            return
        try:
            async with self.ws_session.ws_connect(build_wss_url(random.choice(hosts)), headers=headers, autoping=True, heartbeat=None, timeout=40) as websocket:
                await websocket.send_bytes(build_packet(json.dumps(auth, separators=(",", ":")), OP_AUTH))
                raw = await self.receive(websocket, AUTH_REPLY_TIMEOUT_SECONDS)
                if raw is None:
                    raise AuthConfirmationTimeout(f"{AUTH_REPLY_TIMEOUT_SECONDS} 秒内未收到 WebSocket 鉴权确认")
                reply = parse_auth_reply(raw)
                if reply is None:
                    raise AuthConfirmationTimeout("未收到有效的 WebSocket 鉴权确认")
                if reply.get("code") != 0:
                    reason = reply.get("message") or reply.get("msg") or "未提供原因"
                    raise TokenRejectedError(f"WebSocket 鉴权被拒绝：code={reply.get('code')}，{reason}")
                self.stats.record_auth_confirmed(used_cached_auth)
                heartbeat_task = asyncio.create_task(
                    self.send_heartbeats(websocket), name=f"ws-heartbeat-{room_id}"
                )
                try:
                    while not self.stop_event.is_set():
                        raw = await self.receive(websocket, HEARTBEAT_INTERVAL_SECONDS)
                        if raw is None:
                            continue
                        for command in parse_packets(raw):
                            name = command.get("cmd", "").split(":", 1)[0]
                            if name == "ONLINE_RANK_COUNT" and online_rank_count(command) is not None and online_rank_count(command) < 3:
                                self.low_online_room_ids.add(room_id)
                                return
                            if name in ROOM_STOP_COMMANDS or exceeds_room_activity_limit(name, command):
                                self.closed_room_ids.add(room_id)
                                return
                            if name in RED_PACKET_COMMANDS:
                                average = red_packet_average(command)
                                if average is None or average < self.args.min_average:
                                    continue
                                if await self.enqueue_red_packet(room, command, average):
                                    now = time.strftime("%Y-%m-%d %H:%M:%S")
                                    print(f"[{now}] 🧧 {RED_PACKET_COMMANDS[name]} | {room['host_name']} | {room_id} | 包均: {average:.2f} 电池 | {red_packet_summary(command)}")
                                    play_red_packet_alert()
                            elif PROCESS_ANCHOR_LOTTERY and name in ANCHOR_LOTTERY_COMMANDS:
                                details = anchor_lottery_details(command)
                                if details is None or details["average_value"] < self.args.min_anchor_average:
                                    continue
                                now = time.strftime("%Y-%m-%d %H:%M:%S")
                                print(f"[{now}] 🟪 {ANCHOR_LOTTERY_COMMANDS[name]} | {room['host_name']} | {room_id} | {anchor_lottery_summary(command)}")
                                await self.enqueue_anchor_lottery(room, command, details)
                finally:
                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)
                    self.stats.record_connection_closed()
        finally:
            self.connection_slots.release()

    async def watch_room(self, room):
        room_id, cached_failures = room["room_id"], 0
        while (
            not self.stop_event.is_set()
            and room_id not in self.closed_room_ids
            and room_id not in self.low_online_room_ids
        ):
            attempt, used_cached_auth = self.stats.attempt(), False
            try:
                auth_info, used_cached_auth = await self.get_auth_info(room_id)
                if auth_info is None:
                    return
                await self.watch_connection(room, auth_info, used_cached_auth)
                cached_failures = 0
            except TokenRejectedError as error:
                self.stats.record_auth_failure(used_cached_auth)
                await self.invalidate_cache(room_id)
                log(f"⚠️ {room['host_name']} | {room_id}：{error}；重新获取鉴权信息。")
            except RiskControlError as error:
                blocked = self.stats.record_352(room_id, attempt)
                if not blocked:
                    log(f"⚠️ {room['host_name']} | {room_id}：{error}；停止为该房间获取 token。")
                    return
                await self.wait_for_danmu_info_resume()
            except (aiohttp.ClientError, OSError, RuntimeError) as error:
                self.stats.record_auth_failure(used_cached_auth, isinstance(error, AuthConfirmationTimeout))
                if used_cached_auth:
                    cached_failures += 1
                    if cached_failures >= 2:
                        await self.invalidate_cache(room_id)
                        cached_failures = 0
                if not self.stop_event.is_set():
                    log(f"⚠️ {room['host_name']} | {room_id}：{error}；{self.args.reconnect_delay} 秒后重连。")
            await asyncio.sleep(self.args.reconnect_delay)

    async def refresh_rooms(self):
        rooms, self.wbi_keys = await asyncio.to_thread(build_area_rooms, self.session)
        current_ids = {room["room_id"] for room in rooms}
        # 关闭过的房间只要重新出现在大分区列表，即恢复为可连接状态。
        self.closed_room_ids.difference_update(current_ids)
        # 低观看人数房间在仍处于本轮榜单时不重连；离开榜单后清除标记，之后重新出现时可再检查。
        self.low_online_room_ids.intersection_update(current_ids)
        cached, uncached = [], []
        for room in rooms:
            room_id = room["room_id"]
            if (
                room_id in self.queued_room_ids
                or room_id in self.closed_room_ids
                or room_id in self.low_online_room_ids
            ):
                continue
            (cached if await self.load_cache(room_id) else uncached).append(room)
        for room in cached:
            self.queued_room_ids.add(room["room_id"])
            task = asyncio.create_task(self.cached_room_worker(room), name=f"cached-room-{room['room_id']}")
            self.cached_room_tasks.add(task)
            task.add_done_callback(self.cached_room_tasks.discard)
        for room in uncached:
            self.queued_room_ids.add(room["room_id"])
            self.pending_token_room_ids.add(room["room_id"])
            self.token_room_queue.put_nowait(room)
        self.latest_cached_rooms, self.latest_uncached_rooms = len(cached), len(uncached)
        log(f"🔑 Token 获取：本轮已有缓存 token {len(cached)} | 本轮新发现无缓存房间 {len(uncached)} | 当前等待获取 token 的房间 {len(self.pending_token_room_ids)} | 本次启动全新获取 {self.stats.danmu_info_requests}")
        log(f"🔌 房间连接：当前已连接房间 {self.stats.active} | 本次启动连接成功次数 {self.stats.auth_confirmed} | 使用缓存 token 的连接结果：成功 {self.stats.cached_auth_confirmed}，失败 {self.stats.cached_auth_failed}，超时 {self.stats.auth_timeouts}")

    async def run(self):
        database_task = asyncio.create_task(self.database_worker())
        status_task = asyncio.create_task(self.status_updater())
        self.ws_session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0, ttl_dns_cache=300))
        self.room_workers = [
            asyncio.create_task(self.queue_worker(index + 1), name=f"room-queue-{index + 1}")
            for index in range(self.args.max_active_rooms)
        ]
        log(f"红包扫描器已启动：账号 {self.args.account}，天选事件：{'开启' if PROCESS_ANCHOR_LOTTERY else '关闭'}。")
        try:
            while not self.stop_event.is_set():
                try:
                    await self.refresh_rooms()
                except (requests.RequestException, RuntimeError, OSError) as error:
                    log(f"⚠️ 刷新父分区房间队列失败：{error}；{RISK_BACKOFF_SECONDS} 秒后重试。")
                    await asyncio.sleep(RISK_BACKOFF_SECONDS)
                    continue
                try:
                    await asyncio.wait_for(self.stop_event.wait(), self.args.refresh_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.stop_event.set()
            await asyncio.gather(status_task, return_exceptions=True)
            await asyncio.to_thread(self.database.save_scanner_status, self.status_snapshot(running=False))
            cached_tasks = tuple(self.cached_room_tasks)
            for task in cached_tasks:
                task.cancel()
            for task in self.room_workers:
                task.cancel()
            await asyncio.gather(*cached_tasks, return_exceptions=True)
            await asyncio.gather(*self.room_workers, return_exceptions=True)
            await self.database_queue.join()
            await self.database_queue.put(None)
            await asyncio.gather(database_task, return_exceptions=True)
            await self.ws_session.close()
            await asyncio.to_thread(self.database.close)


def parse_args():
    parser = argparse.ArgumentParser(description="异步扫描直播榜单并监听红包与可选天选事件")
    parser.add_argument("--account", default="acct1")
    parser.add_argument("--refresh-seconds", type=int, default=180)
    parser.add_argument("--reconnect-delay", type=int, default=10)
    parser.add_argument("--min-average", type=float, default=RED_PACKET_MIN_AVERAGE)
    parser.add_argument("--min-anchor-average", type=float, default=ANCHOR_LOTTERY_MIN_AVERAGE)
    parser.add_argument("--database", default="data/red_packet_monitor.db")
    parser.add_argument("--max-get-danmu-info-per-minute", type=int, default=6)
    parser.add_argument("--get-danmu-info-jitter", type=float, default=1.5)
    parser.add_argument("--max-active-rooms", type=int, default=DEFAULT_MAX_ACTIVE_ROOMS)
    args = parser.parse_args()
    if args.refresh_seconds < 60 or args.reconnect_delay < 1 or args.min_average < 0 or args.min_anchor_average < 0 or args.max_get_danmu_info_per_minute < 1 or args.get_danmu_info_jitter < 0 or not 1 <= args.max_active_rooms <= DEFAULT_MAX_ACTIVE_ROOMS:
        parser.error("参数值无效")
    return args


def main():
    try:
        asyncio.run(AsyncListScanner(parse_args()).run())
    except KeyboardInterrupt:
        log("正在停止所有房间监听…")


if __name__ == "__main__":
    main()
