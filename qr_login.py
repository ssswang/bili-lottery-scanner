# -*- coding: utf-8 -*-
"""通过 B 站官方 Web 二维码登录，为直接 API 实验脚本获取 Cookie。"""

import os
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import requests

from settings import (
    SESSION_PATH,
    save_session,
)


QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
HOME_URL = "https://www.bilibili.com/"
FINGERPRINT_API_URL = "https://api.bilibili.com/x/frontend/finger/spi"
QR_IMAGE_PATH = Path(__file__).with_name("qr_login.png")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


def save_qr_image(login_url):
    """生成并打开供 B 站 App 扫码的本地二维码图片。"""
    try:
        import qrcode
    except ImportError as error:
        raise SystemExit(
            "缺少二维码依赖。请先执行：pip install -r requirements.txt"
        ) from error
    qrcode.make(login_url).save(QR_IMAGE_PATH)
    print(f"二维码图片已生成：{QR_IMAGE_PATH}")
    try:
        os.startfile(QR_IMAGE_PATH)
    except OSError as error:
        print(f"无法自动打开二维码图片，请手动打开该文件：{error}")


def build_login_cookie_header(session, login_data):
    """合并响应 Cookie 与跨域登录 URL 中携带的 Web 登录 Cookie。"""
    cookies = {cookie.name: cookie.value for cookie in session.cookies}
    # 成功响应中的 data.url 是官方跨域登录地址，部分环境不会把其 Cookie 写入 CookieJar。
    cookie_names = {"DedeUserID", "DedeUserID__ckMd5", "SESSDATA", "bili_jct", "sid"}
    login_url = login_data.get("url", "")
    for key, value in parse_qsl(urlparse(login_url).query, keep_blank_values=True):
        if key in cookie_names:
            cookies[key] = value
    return "; ".join(f"{key}={value}" for key, value in cookies.items())


def bootstrap_device_cookies(session):
    """初始化一次 Web 设备标识，避免直接 API 请求缺少 buvid/b_nut。"""
    response = session.get(HOME_URL, timeout=10)
    response.raise_for_status()

    response = session.get(
        FINGERPRINT_API_URL,
        headers={"Referer": HOME_URL},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", {}) if payload.get("code") == 0 else {}
    buvid3 = data.get("b_3")
    buvid4 = data.get("b_4")
    if not buvid3 or not buvid4:
        raise RuntimeError("设备指纹接口未返回 buvid3 / buvid4")

    session.cookies.set("buvid3", buvid3, domain=".bilibili.com", path="/")
    session.cookies.set("buvid4", buvid4, domain=".bilibili.com", path="/")
    if "b_nut" not in {cookie.name for cookie in session.cookies}:
        # 首次不带 Cookie 访问主页时，b_nut 为服务端生成时刻的秒级时间戳。
        session.cookies.set("b_nut", str(int(time.time())), domain=".bilibili.com", path="/")


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"})
    try:
        bootstrap_device_cookies(session)
        response = session.get(QR_GENERATE_URL, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, RuntimeError) as error:
        raise SystemExit(f"获取登录二维码失败：{error}") from error

    data = payload.get("data", {}) if payload.get("code") == 0 else {}
    login_url = data.get("url")
    qrcode_key = data.get("qrcode_key")
    if not login_url or not qrcode_key:
        raise SystemExit(f"获取登录二维码失败：{payload.get('message', payload)}")

    save_qr_image(login_url)
    print("请使用 B 站 App 的“扫一扫”扫描已打开的二维码图片并确认登录，二维码有效期约三分钟。")

    deadline = time.monotonic() + 185
    last_status = None
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
            cookie_header = build_login_cookie_header(session, data)
            if "SESSDATA=" not in cookie_header:
                raise SystemExit("登录成功但未收到 SESSDATA，Cookie 未保存；请重新运行脚本。")
            save_session(cookie_header, data.get("refresh_token", ""))
            print(f"✅ 登录成功；会话已保存到：{SESSION_PATH}")
            return
        if status == 86038:
            raise SystemExit("二维码已失效，请重新运行脚本。")
        if status != last_status:
            messages = {86101: "等待扫码", 86090: "已扫码，等待手机确认"}
            print(messages.get(status, f"登录状态：{status} {data.get('message', '')}"))
            last_status = status
        time.sleep(2)

    raise SystemExit("二维码已超时，请重新运行脚本。")


if __name__ == "__main__":
    main()
