# -*- coding: utf-8 -*-
"""直播 WebSocket 鉴权 token 的获取逻辑。"""

from .api_auth import get_wbi_keys, request_bilibili, sign_wbi


DANMU_INFO_URL = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"


class RiskControlError(RuntimeError):
    """getDanmuInfo 明确返回 -352 风控时抛出。"""


def get_danmu_info(session, room_id, wbi_keys=None):
    """获取房间专用 token 与可用 WebSocket 服务器列表。"""
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
        raise RiskControlError("getDanmuInfo 返回 -352 风控校验失败。")
    data = payload.get("data", {}) if payload.get("code") == 0 else {}
    token, hosts = data.get("token"), data.get("host_list") or []
    if not token or not hosts:
        raise RuntimeError(f"获取直播 WebSocket 鉴权信息失败：{payload.get('message', payload)}")
    return token, hosts
