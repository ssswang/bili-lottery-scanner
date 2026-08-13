# -*- coding: utf-8 -*-
"""直接 API 实验脚本专用的本地会话存储。"""

import json
from pathlib import Path


SESSION_PATH = Path(__file__).with_name("lotteryapi_session.json")
LEGACY_SESSION_PATH = Path(__file__).with_name("direct_lottery_session.json")


def load_session():
    """读取扫码登录后保存的 Cookie；文件不存在时返回空配置。"""
    session_path = SESSION_PATH
    if not session_path.is_file() and LEGACY_SESSION_PATH.is_file():
        session_path = LEGACY_SESSION_PATH
    if not session_path.is_file():
        return {}
    try:
        payload = json.loads(session_path.read_text(encoding="utf-8"))
        if session_path == LEGACY_SESSION_PATH:
            save_session(payload.get("cookie_header", ""), payload.get("refresh_token", ""))
            print("已迁移旧的直连登录会话到 lotteryapi_session.json。")
        return payload
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"读取直接 API 会话文件失败：{error}") from error


def save_session(cookie_header, refresh_token):
    """保存仅供直接 API 脚本使用的 Cookie 与刷新令牌。"""
    payload = {"cookie_header": cookie_header, "refresh_token": refresh_token}
    SESSION_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def cookie_header_from_jar(cookie_jar):
    """将 requests CookieJar 转换为后续请求可直接使用的 Cookie 请求头。"""
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in cookie_jar)
