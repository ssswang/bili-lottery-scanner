# -*- coding: utf-8 -*-
"""持续连接指定直播间并打印收到的全部 WebSocket 业务事件。"""

import argparse
import asyncio
import base64
import json
import sqlite3
import time
from pathlib import Path

import aiohttp

from backend.auth.api_auth import USER_AGENT, build_cookie_header, get_cookie_value, get_wbi_keys
from backend.auth.ws_auth import get_danmu_info
from backend.room_watcher import (
    OP_AUTH,
    OP_HEARTBEAT,
    build_packet,
    build_wss_url,
    get_account_session,
    parse_auth_reply,
    parse_packets,
    red_packet_summary,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "red_packet_monitor.db"
AUTH_TIMEOUT_SECONDS = 15
HEARTBEAT_INTERVAL_SECONDS = 30
AUTH_REQUEST_INTERVAL_SECONDS = 10
RECOGNIZED_EVENT_KINDS = frozenset(
    {
        "POPULARITY_RED_POCKET_NEW",
        "POPULARITY_RED_POCKET_V2_NEW",
        "POPULARITY_RED_POCKET_START",
        "POPULARITY_RED_POCKET_V2_START",
        "POPULARITY_RED_POCKET_WINNER_LIST",
        "ANCHOR_LOT_START",
        "SEND_GIFT_V2",
        "UNIVERSAL_EVENT_GIFT",
        "UNIVERSAL_EVENT_GIFT_V2",
        "DMS_SCORE",
        "INTERACT_WORD_V2",
    }
)
IGNORED_EVENT_KINDS = frozenset(
    {
        "WATCHED_CHANGE",
        "DANMU_MSG",
        "STOP_LIVE_ROOM_LIST",
        "ONLINE_RANK_V3",
        "ENTRY_EFFECT",
        "INTERACT_WORD_V2",
        "USER_TOAST_MSG_V2",
        "PK_BATTLE_PRE_NEW",
        "PK_BATTLE_PUNISH_END",
        "PK_INFO",
        "PLAYURL_RELOAD",
        "PLAYURL_RELOAD_MASTER",
        "POPULAR_RANK_CHANGED",
        "ONLINE_RANK_COUNT",
        "RANK_CHANGED_V2", 
        "COMBO_SEND", 
        "LIKE_INFO_V3_CLICK", 
        "LIKE_INFO_V3_UPDATE",
        "NOTICE_MSG",
        "USER_TOAST_MSG",
        "GUARD_BUY",
        "REVENUE_DISPLAY_EFFECT"
    }
)


def load_cached_auth(database_path, account_name, room_id):
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        row = connection.execute(
            "SELECT token, hosts_json FROM ws_auth_cache WHERE account_name = ? AND room_id = ?",
            (account_name, str(room_id)),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return None
    try:
        hosts = json.loads(row[1])
    except (TypeError, json.JSONDecodeError):
        return None
    return (row[0], hosts) if isinstance(row[0], str) and row[0] and isinstance(hosts, list) and hosts else None


async def receive_binary(websocket, timeout):
    message = await asyncio.wait_for(websocket.receive(), timeout)
    if message.type == aiohttp.WSMsgType.BINARY:
        return message.data
    if message.type == aiohttp.WSMsgType.TEXT:
        return message.data.encode("utf-8")
    if message.type == aiohttp.WSMsgType.ERROR:
        raise websocket.exception() or aiohttp.ClientConnectionError("WebSocket 错误")
    if message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED}:
        raise aiohttp.ClientConnectionError(f"服务端关闭连接（code={message.data or websocket.close_code or '未知'}）")
    return b""


def event_data_shape(command):
    """用字段名而不是完整内容标识未分类事件，避免测试输出过长。"""
    data = command.get("data")
    if isinstance(data, dict):
        keys = list(data)
        shown = "、".join(str(key) for key in keys[:12]) or "空对象"
        return f"数据字段：{shown}{'…' if len(keys) > 12 else ''}"
    return f"数据类型：{type(data).__name__}"


def read_varint(payload, offset):
    value, shift = 0, 0
    while offset < len(payload) and shift < 70:
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("无效的 Protobuf varint")


def decode_protobuf_fields(payload, depth=0):
    """通用 Protobuf wire-format 解码，仅用于测试时查看未知 pb 内容。"""
    fields, offset = {}, 0
    while offset < len(payload):
        key, offset = read_varint(payload, offset)
        field_number, wire_type = key >> 3, key & 0x07
        if not field_number:
            raise ValueError("无效的 Protobuf 字段号")
        if wire_type == 0:
            value, offset = read_varint(payload, offset)
        elif wire_type == 1:
            if offset + 8 > len(payload):
                raise ValueError("截断的 64 位字段")
            value, offset = {"hex": payload[offset : offset + 8].hex()}, offset + 8
        elif wire_type == 2:
            length, offset = read_varint(payload, offset)
            if offset + length > len(payload):
                raise ValueError("截断的长度字段")
            raw, offset = payload[offset : offset + length], offset + length
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            if text and text.isprintable():
                value = text
            elif depth < 2 and raw:
                try:
                    value = decode_protobuf_fields(raw, depth + 1)
                except ValueError:
                    value = {"hex": raw.hex()}
            else:
                value = {"hex": raw.hex()}
        elif wire_type == 5:
            if offset + 4 > len(payload):
                raise ValueError("截断的 32 位字段")
            value, offset = {"hex": payload[offset : offset + 4].hex()}, offset + 4
        else:
            raise ValueError(f"不支持的 wire type：{wire_type}")
        fields.setdefault(str(field_number), []).append(value)
    return fields


def decode_pb(pb):
    if not isinstance(pb, str) or not pb:
        return "未提供"
    try:
        decoded = decode_protobuf_fields(base64.b64decode(pb, validate=True))
    except (ValueError, TypeError):
        return "解码失败"
    return json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))


def pb_first(fields, field_number, default=None):
    values = fields.get(str(field_number)) or []
    return values[0] if values else default


def decode_interact_word_v2(pb):
    """按已知字段把互动事件转换为中文信息；字段缺失时返回通用解码。"""
    if not isinstance(pb, str) or not pb:
        return "未提供 pb"
    try:
        fields = decode_protobuf_fields(base64.b64decode(pb, validate=True))
    except (ValueError, TypeError):
        return "解码失败"
    uid, uname = pb_first(fields, 1, 0), pb_first(fields, 2, "未知用户")
    msg_type = pb_first(fields, 5, 0)
    room_id, timestamp = pb_first(fields, 6, 0), pb_first(fields, 7, 0)
    action = {1: "进入直播间", 2: "关注直播间", 3: "分享直播间"}.get(msg_type, f"互动类型 {msg_type}")
    result = f"用户：{uname}（UID {uid}） | 动作：{action} | 房间：{room_id}"
    medal = pb_first(fields, 9, {})
    if isinstance(medal, dict):
        level, name = pb_first(medal, 2, 0), pb_first(medal, 3, "")
        if level or name:
            result += f" | 粉丝牌：{name or '未知'} Lv.{level}"
    if timestamp:
        result += f" | 时间戳：{timestamp}"
    return result


def pb_int(fields, field_number, default=0):
    value = pb_first(fields, field_number, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coin_label(coin_type):
    return {"gold": "金瓜子", "silver": "银瓜子"}.get(str(coin_type).lower(), str(coin_type) or "瓜子")


def decode_send_gift_v2(pb):
    """按 SendGiftBroadcast 的已知字段，把 protobuf 礼物广播转换成摘要。"""
    if not isinstance(pb, str) or not pb:
        return "未提供 pb"
    try:
        fields = decode_protobuf_fields(base64.b64decode(pb, validate=True))
    except (ValueError, TypeError):
        return "pb 解码失败"

    uid = pb_int(fields, 1)
    uname = pb_first(fields, 2, "未知用户")
    guard_level = pb_int(fields, 5)
    sender = f"送礼人：{uname or '未知用户'}"
    if uid:
        sender += f"（UID {uid}）"
    if guard_level:
        sender += f" | 舰长等级：{guard_level}"

    gifts = [item for item in fields.get("10", []) if isinstance(item, dict)]
    if not gifts:
        return f"{sender} | 未读到礼物列表"

    gift_texts = []
    for gift in gifts:
        gift_id = pb_int(gift, 1)
        name = pb_first(gift, 2, "") or (f"礼物 #{gift_id}" if gift_id else "未知礼物")
        number = pb_int(gift, 3, 1) or 1
        price = pb_int(gift, 5)
        total = pb_int(gift, 7)
        currency = coin_label(pb_first(gift, 8, ""))
        detail = f"{name} ×{number}"
        if total:
            detail += f"（合计 {total} {currency}）"
        elif price:
            detail += f"（单价 {price} {currency}）"
        gift_texts.append(detail)

    blind_gift = pb_first(fields, 9, {})
    blind_name = pb_first(blind_gift, 3, "") if isinstance(blind_gift, dict) else ""
    blind_note = f" | 盲盒原礼物：{blind_name}" if blind_name else ""
    return f"{sender} | 礼物：{'；'.join(gift_texts)}{blind_note}"


def format_universal_event_gift(data):
    """将两种多人连线礼物榜事件收敛为成员与累计礼物价值。"""
    if not isinstance(data, dict):
        return "数据格式无效"
    info = data.get("info") if isinstance(data.get("info"), dict) else data
    members = info.get("members")
    if not isinstance(members, list):
        return "未提供连线成员"

    score_by_uid = {}
    multi_conn_info = info.get("multi_conn_info")
    if isinstance(multi_conn_info, dict):
        for score in multi_conn_info.get("scores") or []:
            if isinstance(score, dict) and score.get("uid") is not None:
                score_by_uid[str(score["uid"])] = score.get("price_text") or score.get("price")

    entries = []
    for member in members:
        if not isinstance(member, dict):
            continue
        name = member.get("display_name") or member.get("uname") or "未知成员"
        room_id = member.get("room_id")
        extra_data = member.get("biz_extra_data")
        multi_conn = extra_data.get("multi_conn") if isinstance(extra_data, dict) else None
        price = (multi_conn or {}).get("price_text") or (multi_conn or {}).get("price")
        if price in (None, ""):
            price = score_by_uid.get(str(member.get("uid")))
        entry = str(name)
        if room_id:
            entry += f"（房间 {room_id}）"
        if price not in (None, ""):
            entry += f"：累计 {price}"
        entries.append(entry)

    business = info.get("business_label") or "多人连线"
    shown = "；".join(entries[:8]) or "无有效成员"
    suffix = f"；另 {len(entries) - 8} 人" if len(entries) > 8 else ""
    return f"连线礼物榜：{business} | 成员 {len(entries)} 人 | {shown}{suffix}"


def special_event_details(event_kind, command):
    """输出测试时需要的已解码事件摘要。"""
    data = command.get("data")
    if event_kind == "INTERACT_WORD_V2":
        pb = data.get("pb") if isinstance(data, dict) else None
        return decode_interact_word_v2(pb)
    if event_kind == "SEND_GIFT_V2":
        pb = data.get("pb") if isinstance(data, dict) else None
        return decode_send_gift_v2(pb)
    if event_kind in {"UNIVERSAL_EVENT_GIFT", "UNIVERSAL_EVENT_GIFT_V2"}:
        return format_universal_event_gift(data)
    if event_kind == "DMS_SCORE":
        return f"DMS_SCORE：{json.dumps(data, ensure_ascii=False, separators=(',', ':'))}"
    return ""


def print_event(room_id, command, show_json, seen_unknown_event_kinds):
    name = str(command.get("cmd") or "未命名事件")
    event_kind = name.split(":", 1)[0]
    timestamp = time.strftime("%H:%M:%S")
    line = f"[{timestamp}] {room_id} | {name}"
    if event_kind in {"POPULARITY_RED_POCKET_NEW", "POPULARITY_RED_POCKET_V2_NEW", "POPULARITY_RED_POCKET_START", "POPULARITY_RED_POCKET_V2_START"}:
        line += f" | {red_packet_summary(command)}"
    elif event_kind in {"SEND_GIFT_V2", "UNIVERSAL_EVENT_GIFT", "UNIVERSAL_EVENT_GIFT_V2", "DMS_SCORE", "INTERACT_WORD_V2"}:
        line += f" | {special_event_details(event_kind, command)}"
    elif event_kind not in RECOGNIZED_EVENT_KINDS and event_kind not in seen_unknown_event_kinds:
        seen_unknown_event_kinds.add(event_kind)
        line += f" | 🔎 未知事件（首次） | {event_data_shape(command)}"
    if show_json:
        line += f" | {json.dumps(command, ensure_ascii=False, separators=(',', ':'))}"
    print(line, flush=True)


class TokenResolver:
    """所有测试房间共享新 token 获取节流；缓存 token 不受此限制。"""

    def __init__(self, session, database_path, account_name):
        self.session = session
        self.database_path = database_path
        self.account_name = account_name
        self.lock, self.wbi_keys, self.next_request_at = asyncio.Lock(), None, 0.0

    async def resolve(self, room_id):
        cached = load_cached_auth(self.database_path, self.account_name, room_id)
        if cached:
            print(f"房间 {room_id}：使用已保存的 token/host。", flush=True)
            return cached
        async with self.lock:
            cached = load_cached_auth(self.database_path, self.account_name, room_id)
            if cached:
                print(f"房间 {room_id}：使用已保存的 token/host。", flush=True)
                return cached
            delay = self.next_request_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            if self.wbi_keys is None:
                self.wbi_keys = await asyncio.to_thread(get_wbi_keys, self.session)
            print(f"房间 {room_id}：未找到缓存 token，正在获取。", flush=True)
            token, hosts = await asyncio.to_thread(
                get_danmu_info, self.session, room_id, self.wbi_keys
            )
            self.next_request_at = time.monotonic() + AUTH_REQUEST_INTERVAL_SECONDS
            return token, hosts


async def watch_room(room_id, args, session, uid, buvid3, ws_session, token_resolver, seen_unknown_event_kinds):
    """独立监听一个房间；其连接失败不会中断其他测试房间。"""
    try:
        token, hosts = await token_resolver.resolve(room_id)
        auth = {"uid": uid, "roomid": int(room_id), "protover": 2, "platform": "web", "type": 2, "key": token, "buvid": buvid3}
        headers = {"Cookie": build_cookie_header(session), "Origin": "https://live.bilibili.com", "User-Agent": USER_AGENT}
        event_count = 0
        async with ws_session.ws_connect(build_wss_url(hosts[0]), headers=headers, autoping=True, heartbeat=None, timeout=40) as websocket:
            await websocket.send_bytes(build_packet(json.dumps(auth, separators=(",", ":")), OP_AUTH))
            reply = parse_auth_reply(await receive_binary(websocket, AUTH_TIMEOUT_SECONDS))
            if not reply or reply.get("code") != 0:
                reason = (reply or {}).get("message") or (reply or {}).get("msg") or "未收到有效鉴权确认"
                raise RuntimeError(f"WebSocket 鉴权失败：{reason}")
            print(f"已连接房间 {room_id}，持续输出事件；按 Ctrl+C 停止。", flush=True)
            next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL_SECONDS
            while True:
                try:
                    raw = await receive_binary(websocket, 3)
                except asyncio.TimeoutError:
                    raw = b""
                for command in parse_packets(raw):
                    event_kind = str(command.get("cmd") or "").split(":", 1)[0]
                    if event_kind in IGNORED_EVENT_KINDS:
                        continue
                    event_count += 1
                    print_event(room_id, command, args.json, seen_unknown_event_kinds)
                if time.monotonic() >= next_heartbeat:
                    await websocket.send_bytes(build_packet(b"", OP_HEARTBEAT))
                    next_heartbeat += HEARTBEAT_INTERVAL_SECONDS
    except asyncio.CancelledError:
        raise
    except (RuntimeError, TypeError, aiohttp.ClientError, OSError, ValueError) as error:
        print(f"房间 {room_id}：监听失败：{error}", flush=True)


async def main_async(args):
    database_path = Path(args.database).resolve()
    session = get_account_session(args.account)
    uid = int(get_cookie_value(session, "DedeUserID") or 0)
    buvid3 = get_cookie_value(session, "buvid3")
    if not uid or not buvid3:
        raise RuntimeError("登录会话缺少 DedeUserID 或 buvid3。")
    seen_unknown_event_kinds = set()
    token_resolver = TokenResolver(session, database_path, args.account)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0, ttl_dns_cache=300)) as ws_session:
        tasks = [
            asyncio.create_task(
                watch_room(room_id, args, session, uid, buvid3, ws_session, token_resolver, seen_unknown_event_kinds),
                name=f"test-room-{room_id}",
            )
            for room_id in args.room_ids
        ]
        await asyncio.gather(*tasks)


def parse_args():
    parser = argparse.ArgumentParser(description="持续监听多个直播间并打印全部 WebSocket 业务事件")
    parser.add_argument("room_ids", nargs="+", help="要监听的一个或多个直播间号")
    parser.add_argument("--account", default="acct1")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--json", action="store_true", help="同时输出每个事件的完整 JSON")
    args = parser.parse_args()
    args.room_ids = list(dict.fromkeys(str(room_id) for room_id in args.room_ids))
    if any(not room_id.isdigit() for room_id in args.room_ids):
        parser.error("房间号无效")
    return args


def main():
    try:
        asyncio.run(main_async(parse_args()))
    except KeyboardInterrupt:
        print("\n已停止监听。")
    except (RuntimeError, sqlite3.Error, aiohttp.ClientError, OSError, ValueError) as error:
        raise SystemExit(error) from error


if __name__ == "__main__":
    main()
