# -*- coding: utf-8 -*-
"""用户账号会话的读取、保存与二维码登录。"""

import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SESSION_PATH = DATA_DIR / "lotteryapi_session.json"
QR_IMAGE_PATH = DATA_DIR / "qr_login.png"
HOME_PAGE_URL = "https://www.bilibili.com/"
FINGERPRINT_API_URL = "https://api.bilibili.com/x/frontend/finger/spi"
QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


def load_session():
    """读取默认扫码登录会话；文件不存在时返回空配置。"""
    if not SESSION_PATH.is_file():
        return {}
    try:
        return json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"读取登录会话文件失败：{error}") from error


def session_file(name):
    """按账号名返回会话文件路径。"""
    return DATA_DIR / f"lotteryapi_session_{name}.json"


def save_session(cookie_header, refresh_token, name="default"):
    """保存指定账号的 Cookie 与刷新令牌。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = session_file(name) if name != "default" else SESSION_PATH
    path.write_text(
        json.dumps(
            {"cookie_header": cookie_header, "refresh_token": refresh_token},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_saved_sessions():
    """读取所有已保存的登录账号，返回 [(账号名, cookie_header), ...]。"""
    sessions = []
    seen_names = set()
    if SESSION_PATH.is_file():
        try:
            data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
            header = data.get("cookie_header", "")
            if header:
                sessions.append(("default", header))
                seen_names.add("default")
        except (OSError, json.JSONDecodeError) as error:
            print(f"⚠️ 读取默认会话文件失败：{error}")
    for path in DATA_DIR.glob("lotteryapi_session_*.json"):
        name = path.stem[len("lotteryapi_session_"):]
        if name in seen_names:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            header = data.get("cookie_header", "")
        except (OSError, json.JSONDecodeError) as error:
            print(f"⚠️ 读取会话文件 {path.name} 失败：{error}")
            continue
        if header:
            sessions.append((name, header))
    return sessions


def _parse_cookie_header(cookie_header):
    cookies = {}
    for item in cookie_header.split(";"):
        key, separator, value = item.strip().partition("=")
        if separator and key:
            cookies[key] = value
    return cookies


def _get_cookie_value(session, name):
    return next((cookie.value for cookie in session.cookies if cookie.name == name), "")


def _create_session(cookie_header):
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://live.bilibili.com",
            "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
        }
    )
    session.cookies.update(_parse_cookie_header(cookie_header))
    return session


def save_qr_image(login_url):
    """生成并打开供 B Zhan App 扫码的本地二维码图片。"""
    try:
        import qrcode
    except ImportError as error:
        raise SystemExit("缺少二维码依赖。请先执行：pip install -r requirements.txt") from error
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    qrcode.make(login_url).save(QR_IMAGE_PATH)
    print(f"二维码图片已生成：{QR_IMAGE_PATH}")
    try:
        os.startfile(QR_IMAGE_PATH)
    except OSError as error:
        print(f"无法自动打开二维码图片，请手动打开该文件：{error}")


def _build_login_cookie_header(session, login_data):
    cookies = {cookie.name: cookie.value for cookie in session.cookies}
    names = {"DedeUserID", "DedeUserID__ckMd5", "SESSDATA", "bili_jct", "sid"}
    for key, value in parse_qsl(urlparse(login_data.get("url", "")).query, keep_blank_values=True):
        if key in names:
            cookies[key] = value
    return "; ".join(f"{key}={value}" for key, value in cookies.items())


def _bootstrap_device_cookies(session):
    response = session.get(HOME_PAGE_URL, timeout=10)
    response.raise_for_status()
    response = session.get(FINGERPRINT_API_URL, headers={"Referer": HOME_PAGE_URL}, timeout=10)
    response.raise_for_status()
    data = response.json().get("data", {})
    buvid3, buvid4 = data.get("b_3"), data.get("b_4")
    if not buvid3 or not buvid4:
        raise RuntimeError("设备指纹接口未返回 buvid3 / buvid4")
    session.cookies.set("buvid3", buvid3, domain=".bilibili.com", path="/")
    session.cookies.set("buvid4", buvid4, domain=".bilibili.com", path="/")
    if "b_nut" not in {cookie.name for cookie in session.cookies}:
        session.cookies.set("b_nut", str(int(time.time())), domain=".bilibili.com", path="/")


def qr_login_main(name="default"):
    """通过官方 Web 二维码登录并保存指定账号的会话。"""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Referer": HOME_PAGE_URL})
    try:
        _bootstrap_device_cookies(session)
        response = session.get(QR_GENERATE_URL, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, RuntimeError) as error:
        raise SystemExit(f"获取登录二维码失败：{error}") from error
    data = payload.get("data", {}) if payload.get("code") == 0 else {}
    login_url, qrcode_key = data.get("url"), data.get("qrcode_key")
    if not login_url or not qrcode_key:
        raise SystemExit(f"获取登录二维码失败：{payload.get('message', payload)}")
    save_qr_image(login_url)
    print(f"请使用 B Zhan App 的“扫一扫”扫描已打开的二维码图片并确认登录（账号 {name}），二维码有效期约三分钟。")
    deadline, last_status = time.monotonic() + 185, None
    while time.monotonic() < deadline:
        try:
            response = session.get(QR_POLL_URL, params={"qrcode_key": qrcode_key}, timeout=10)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as error:
            raise SystemExit(f"轮询登录状态失败：{error}") from error
        data = payload.get("data", {})
        status = data.get("code")
        if status == 0:
            cookie_header = _build_login_cookie_header(session, data)
            if "SESSDATA=" not in cookie_header:
                raise SystemExit("登录成功但未收到 SESSDATA，Cookie 未保存；请重新运行脚本。")
            save_session(cookie_header, data.get("refresh_token", ""), name=name)
            print(f"✅ 账号 {name} 登录成功；会话已保存。")
            return
        if status == 86038:
            raise SystemExit("二维码已失效，请重新运行脚本。")
        if status != last_status:
            message = {86101: "等待扫码", 86090: "已扫码，等待手机确认"}.get(
                status, f"登录状态：{status} {data.get('message', '')}"
            )
            print(message)
            last_status = status
        time.sleep(2)
    raise SystemExit("二维码已超时，请重新运行脚本。")


def load_authorized_session():
    """读取已登录账号的会话，并校验必要的登录 Cookie。"""
    cookie_header = load_session().get("cookie_header", "")
    if not cookie_header:
        raise RuntimeError("请先运行 qr_login.py 完成二维码登录。")
    session = _create_session(cookie_header)
    missing_login = [name for name in ("SESSDATA", "bili_jct") if not _get_cookie_value(session, name)]
    if missing_login:
        raise RuntimeError(f"登录会话缺少 Cookie：{', '.join(missing_login)}。请重新运行 qr_login.py。")
    return session


__all__ = (
    "load_authorized_session",
    "load_saved_sessions",
    "load_session",
    "qr_login_main",
    "save_session",
    "session_file",
)
