# -*- coding: utf-8 -*-
"""通过 B 站直播 WebSocket 实时输出指定直播间的人气红包事件。"""

import argparse
import json
import struct
import time
import zlib
from datetime import datetime

import requests

from auth_manager import (
    USER_AGENT,
    build_cookie_header,
    create_session,
    ensure_device_cookies,
    generate_and_set_buvid_fp,
    get_cookie_value,
    get_device_profile,
    get_wbi_keys,
    load_saved_sessions,
    request_bilibili,
    set_client_identity_cookies,
    sign_wbi,
)

try:
    import websocket
except ImportError as error:
    raise SystemExit("缺少 websocket-client。请执行：pip install -r requirements.txt") from error

try:
    import brotli
except ImportError:
    brotli = None


DANMU_INFO_URL = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"
HEADER_LENGTH = 16
OP_HEARTBEAT = 2
OP_MESSAGE = 5
OP_AUTH = 7
OP_AUTH_REPLY = 8

RED_PACKET_COMMANDS = {
    "POPULARITY_RED_POCKET_START": "红包开始",
}


class RiskControlError(RuntimeError):
    """接口明确返回 -352 时使用，避免短间隔重复请求。"""


def build_packet(body, operation, protover=1):
    """构造 B 站直播协议包；所有整数均为大端序。"""
    if isinstance(body, str):
        body = body.encode("utf-8")
    return struct.pack("!IHHII", HEADER_LENGTH + len(body), HEADER_LENGTH, protover, operation, 1) + body


def build_wss_url(host):
    """使用 getDanmuInfo 返回的加密 wss_port 构造连接地址。"""
    hostname = host.get("host") if isinstance(host, dict) else None
    port = host.get("wss_port") if isinstance(host, dict) else None
    if not hostname or not port:
        raise RuntimeError("getDanmuInfo 未返回可用的加密 wss_port")
    return f"wss://{hostname}:{port}/sub"


def parse_packets(buffer):
    """解析一个帧内串联的协议包，并递归处理 zlib/Brotli 压缩包。"""
    offset = 0
    while offset + HEADER_LENGTH <= len(buffer):
        packet_length, header_length, protover, operation, _ = struct.unpack_from(
            "!IHHII", buffer, offset
        )
        if packet_length < header_length or offset + packet_length > len(buffer):
            return
        body = buffer[offset + header_length : offset + packet_length]
        offset += packet_length

        if operation != OP_MESSAGE:
            continue
        if protover == 2:
            try:
                yield from parse_packets(zlib.decompress(body))
            except zlib.error as error:
                print(f"⚠️ 无法解压 zlib 消息：{error}")
        elif protover == 3:
            if brotli is None:
                print("⚠️ 收到 Brotli 消息；请执行 pip install brotli 后重试。")
                continue
            try:
                yield from parse_packets(brotli.decompress(body))
            except brotli.error as error:
                print(f"⚠️ 无法解压 Brotli 消息：{error}")
        else:
            try:
                yield json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue


def parse_auth_reply(buffer):
    """从单个 WS 帧中读取服务端 OP_AUTH_REPLY；非鉴权帧返回 None。"""
    offset = 0
    while offset + HEADER_LENGTH <= len(buffer):
        packet_length, header_length, _, operation, _ = struct.unpack_from(
            "!IHHII", buffer, offset
        )
        if packet_length < header_length or offset + packet_length > len(buffer):
            return None
        body = buffer[offset + header_length : offset + packet_length]
        offset += packet_length
        if operation != OP_AUTH_REPLY:
            continue
        try:
            reply = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"code": None, "message": "无法解析服务端鉴权回复"}
        return reply if isinstance(reply, dict) else {"code": None, "message": "鉴权回复格式无效"}
    return None


def get_account_session(account_name):
    """按主扫描器的账号设备档案构建 WebSocket 前置 HTTP 会话。"""
    accounts = dict(load_saved_sessions())
    cookie_header = accounts.get(account_name)
    if not cookie_header:
        available = "、".join(accounts) or "无"
        raise RuntimeError(
            f"未找到账号 {account_name} 的会话（可用：{available}）。"
            "请先运行 qr_login.py --name " + account_name
        )
    session = create_session(cookie_header)
    session.trust_env = False
    session.proxies.clear()
    # getDanmuInfo 也会校验 Web 设备身份。仅带 SESSDATA 的新 requests
    # 会话容易被 -352 拒绝，因此与 lotteryapi_scanner 保持相同的初始化流程。
    try:
        profile = get_device_profile(f"account-{account_name}")
        set_client_identity_cookies(session, profile, force=True)
        missing = ensure_device_cookies(session, profile)
        generate_and_set_buvid_fp(session, profile)
    except requests.RequestException as error:
        raise RuntimeError(f"初始化账号 {account_name} 的设备 Cookie 失败：{error}") from error
    if missing:
        raise RuntimeError(
            f"账号 {account_name} 缺少设备 Cookie：{', '.join(missing)}；请重新扫码登录。"
        )
    return session


def get_danmu_info(session, room_id, wbi_keys=None):
    """获取当前房间专用 token 与可用 WebSocket 服务器列表。"""
    # 2026 年该接口已要求 WBI 签名；只传 id/type 会返回 -352。
    img_key, sub_key = wbi_keys or get_wbi_keys(session)
    params = sign_wbi(
        {"id": room_id, "type": 0, "web_location": "444.8"}, img_key, sub_key
    )
    response = request_bilibili(
        session,
        "get",
        DANMU_INFO_URL,
        params=params,
        headers={"Referer": f"https://live.bilibili.com/{room_id}"},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") == -352:
        raise RiskControlError(
            "getDanmuInfo 返回 -352 风控校验失败；监听将在 60 秒后再尝试。"
        )
    data = payload.get("data", {}) if payload.get("code") == 0 else {}
    token, hosts = data.get("token"), data.get("host_list") or []
    if not token or not hosts:
        raise RuntimeError(f"获取直播 WebSocket 鉴权信息失败：{payload.get('message', payload)}")
    return token, hosts


def red_packet_summary(command):
    """把三种红包事件转换为稳定、可读的终端摘要，不输出原始 JSON。"""
    data = command.get("data") or {}
    name = command.get("cmd", "").split(":", 1)[0]
    lot_id = data.get("lot_id") or data.get("red_packet_id") or "未知"
    requirement = {0: "无要求", 1: "需要关注", 2: "需要粉丝勋章", 3: "需要上舰"}

    def format_time(value):
        try:
            return datetime.fromtimestamp(int(value)).strftime("%H:%M:%S")
        except (TypeError, ValueError, OSError):
            return "未知"

    if name == "POPULARITY_RED_POCKET_NEW":
        sender = data.get("uname") or data.get("sender_name") or "未知"
        gift = data.get("gift_name") or "红包"
        count = data.get("num", 1)
        return (
            f"红包 ID: {lot_id} | 发送者: {sender} | 礼物: {gift} × {count} | "
            f"开始时间: {format_time(data.get('start_time'))}"
        )

    if name == "POPULARITY_RED_POCKET_START":
        sender = data.get("sender_name") or data.get("uname") or "未知"
        awards = data.get("awards") or []
        award_text = "、".join(
            f"{item.get('gift_name', '未知礼物')} × {item.get('num', 0)}"
            for item in awards
            if isinstance(item, dict)
        ) or "奖品信息未提供"
        total_price = data.get("total_price")
        price_text = f" | 总价值: {int(total_price) // 100} 电池" if str(total_price).isdigit() else ""
        return (
            f"红包 ID: {lot_id} | 发送者: {sender} | 奖品: {award_text}{price_text} | "
            f"参与条件: {requirement.get(data.get('join_requirement'), '未知')} | "
            f"开奖时间: {format_time(data.get('end_time'))}"
        )

    if name == "POPULARITY_RED_POCKET_WINNER_LIST":
        winners = data.get("winner_info") or []
        names = [str(item[1]) for item in winners if isinstance(item, list) and len(item) > 1]
        shown_names = "、".join(names[:10]) or "未提供"
        suffix = "…" if len(names) > 10 else ""
        return (
            f"红包 ID: {lot_id} | 中奖人数: {data.get('total_num', len(winners))} | "
            f"中奖用户: {shown_names}{suffix}"
        )

    return f"红包 ID: {lot_id}"


def red_packet_average(command):
    """返回红包包均电池价值；信息不足或不是开始事件时返回 None。"""
    if command.get("cmd", "").split(":", 1)[0] != "POPULARITY_RED_POCKET_START":
        return None
    data = command.get("data") or {}
    try:
        total_price = int(data.get("total_price", 0)) / 100
    except (TypeError, ValueError):
        return None
    award_count = 0
    for item in data.get("awards") or []:
        if not isinstance(item, dict):
            continue
        try:
            award_count += int(item.get("num", 0))
        except (TypeError, ValueError):
            continue
    return total_price / award_count if award_count else None


def watch(room_id, account_name, reconnect_delay):
    session = get_account_session(account_name)
    uid = int(get_cookie_value(session, "DedeUserID") or 0)
    buvid3 = get_cookie_value(session, "buvid3")
    if not uid or not buvid3:
        raise RuntimeError("登录会话缺少 DedeUserID 或 buvid3，请重新运行对应账号的二维码登录。")

    while True:
        ws = None
        try:
            token, hosts = get_danmu_info(session, room_id)
            host = hosts[0]
            url = build_wss_url(host)
            ws = websocket.create_connection(
                url,
                cookie=build_cookie_header(session),
                origin="https://live.bilibili.com",
                header=[f"User-Agent: {USER_AGENT}"],
                timeout=40,
            )
            auth = {
                "uid": uid,
                "roomid": int(room_id),
                "protover": 2,  # 选 zlib，标准库即可解压；仍兼容服务器返回的 Brotli。
                "platform": "web",
                "type": 2,
                "key": token,
                "buvid": buvid3,
            }
            ws.send_binary(build_packet(json.dumps(auth, separators=(",", ":")), OP_AUTH))
            print(f"✅ 已连接房间 {room_id}，正在监听红包事件（账号：{account_name}）。按 Ctrl+C 停止。")
            next_heartbeat = time.monotonic() + 30

            while True:
                timeout = max(1, next_heartbeat - time.monotonic())
                ws.settimeout(timeout)
                try:
                    message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    ws.send_binary(build_packet(b"", OP_HEARTBEAT))
                    next_heartbeat = time.monotonic() + 30
                    continue
                if not message:
                    raise websocket.WebSocketConnectionClosedException("服务器关闭了连接")
                raw = message.encode("utf-8") if isinstance(message, str) else message
                for command in parse_packets(raw):
                    name = command.get("cmd", "").split(":", 1)[0]
                    if name in RED_PACKET_COMMANDS:
                        now = time.strftime("%Y-%m-%d %H:%M:%S")
                        print(f"[{now}] 🧧 {RED_PACKET_COMMANDS[name]} | {red_packet_summary(command)}")
        except KeyboardInterrupt:
            print("\n已停止监听。")
            return
        except RiskControlError as error:
            cooldown = max(60, reconnect_delay)
            print(f"⚠️ {error}")
            time.sleep(cooldown)
        except (requests.RequestException, RuntimeError, OSError, websocket.WebSocketException) as error:
            print(f"⚠️ 连接或监听失败：{error}；{reconnect_delay} 秒后重连。")
            time.sleep(reconnect_delay)
        finally:
            if ws is not None:
                ws.close()


def main():
    parser = argparse.ArgumentParser(description="通过直播 WebSocket 实时查看指定房间的人气红包事件")
    parser.add_argument("room_id", help="要监听的直播间房间号")
    parser.add_argument("--account", default="acct1", help="使用的已登录账号名，默认 acct1")
    parser.add_argument("--reconnect-delay", type=int, default=10, help="断线重连等待秒数，默认 10")
    args = parser.parse_args()
    if args.reconnect_delay < 1:
        parser.error("--reconnect-delay 必须至少为 1")
    try:
        watch(args.room_id, args.account, args.reconnect_delay)
    except RuntimeError as error:
        raise SystemExit(error) from error


if __name__ == "__main__":
    main()
