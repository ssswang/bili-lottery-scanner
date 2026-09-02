# -*- coding: utf-8 -*-
"""B Zhan 直接 API 的二维码登录、会话 Cookie 与 WBI 鉴权。"""

import hashlib
import hmac
import json
import os
import time
from collections import deque
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlparse

import requests


TICKET_API_URL = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
NAV_API_URL = "https://api.bilibili.com/x/web-interface/nav"
COOKIE_INFO_URL = "https://passport.bilibili.com/x/passport-login/web/cookie/info"
HOME_PAGE_URL = "https://www.bilibili.com/"
FINGERPRINT_API_URL = "https://api.bilibili.com/x/frontend/finger/spi"
QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
SESSION_PATH = Path(__file__).with_name("lotteryapi_session.json")
QR_IMAGE_PATH = Path(__file__).with_name("qr_login.png")
USER_AGENT = "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
WBI_MIXIN_KEY_TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
API_RATE_LIMIT = 450
API_RATE_WINDOW_SECONDS = 10 * 60
API_MIN_INTERVAL_SECONDS = API_RATE_WINDOW_SECONDS / API_RATE_LIMIT


class ApiRateLimiter:
    """按滑动时间窗口限制一个直接 API 会话的请求数量。"""

    def __init__(self, max_requests=API_RATE_LIMIT, window_seconds=API_RATE_WINDOW_SECONDS, minimum_interval_seconds=API_MIN_INTERVAL_SECONDS):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.minimum_interval_seconds = minimum_interval_seconds
        self.request_times = deque()
        self.last_request_time = None

    def wait_for_slot(self):
        """必要时等待最早请求过期，再登记当前请求。"""
        while True:
            now = time.monotonic()
            if self.last_request_time is not None:
                interval_wait = self.minimum_interval_seconds - (now - self.last_request_time)
                if interval_wait > 0:
                    time.sleep(interval_wait)
                    continue
            while self.request_times and now - self.request_times[0] >= self.window_seconds:
                self.request_times.popleft()
            if len(self.request_times) < self.max_requests:
                self.request_times.append(now)
                self.last_request_time = now
                return
            wait_seconds = self.window_seconds - (now - self.request_times[0])
            print(f"⏳ B Zhan API 请求接近 10 分钟限制，等待 {max(1, round(wait_seconds))} 秒后继续。")
            time.sleep(max(0.1, wait_seconds))


def request_bilibili(session, method, url, **kwargs):
    """通过当前会话发送受限流保护的 B Zhan API 请求。"""
    limiter = getattr(session, "_bili_api_rate_limiter", None)
    if limiter is None:
        limiter = ApiRateLimiter()
        session._bili_api_rate_limiter = limiter
    limiter.wait_for_slot()
    return getattr(session, method)(url, **kwargs)


def load_session():
    """读取扫码登录后保存的 Cookie；文件不存在时返回空配置。"""
    if not SESSION_PATH.is_file():
        return {}
    try:
        return json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"读取直接 API 会话文件失败：{error}") from error


def save_session(cookie_header, refresh_token):
    """保存直接 API 使用的 Cookie 与刷新令牌。"""
    SESSION_PATH.write_text(json.dumps({"cookie_header": cookie_header, "refresh_token": refresh_token}, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_cookie_header(cookie_header):
    """将完整 Cookie 请求头转换为 requests Cookie。"""
    cookies = {}
    for item in cookie_header.split(";"):
        key, separator, value = item.strip().partition("=")
        if separator and key:
            cookies[key] = value
    return cookies


def get_cookie_value(session, name):
    """按名称读取 Cookie，兼容多个域名中存在同名 Cookie。"""
    return next((cookie.value for cookie in session.cookies if cookie.name == name), "")


def get_csrf(session):
    """从 Cookie 中读取 bili_jct。"""
    return get_cookie_value(session, "bili_jct")


def create_session(cookie_header):
    """创建带有用户 Cookie 和常用 Web 请求头的会话。"""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8", "Origin": "https://live.bilibili.com"})
    session.cookies.update(parse_cookie_header(cookie_header))
    return session


def save_qr_image(login_url):
    """生成并打开供 B Zhan App 扫码的本地二维码图片。"""
    try:
        import qrcode
    except ImportError as error:
        raise SystemExit("缺少二维码依赖。请先执行：pip install -r requirements.txt") from error
    qrcode.make(login_url).save(QR_IMAGE_PATH)
    print(f"二维码图片已生成：{QR_IMAGE_PATH}")
    try:
        os.startfile(QR_IMAGE_PATH)
    except OSError as error:
        print(f"无法自动打开二维码图片，请手动打开该文件：{error}")


def build_login_cookie_header(session, login_data):
    """合并响应 Cookie 与跨域登录 URL 中的 Web 登录 Cookie。"""
    cookies = {cookie.name: cookie.value for cookie in session.cookies}
    names = {"DedeUserID", "DedeUserID__ckMd5", "SESSDATA", "bili_jct", "sid"}
    for key, value in parse_qsl(urlparse(login_data.get("url", "")).query, keep_blank_values=True):
        if key in names:
            cookies[key] = value
    return "; ".join(f"{key}={value}" for key, value in cookies.items())


def bootstrap_device_cookies(session):
    """初始化 QR 登录会话的 Web 设备标识。"""
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


def qr_login_main():
    """通过官方 Web 二维码登录并保存直接 API 会话。"""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Referer": HOME_PAGE_URL})
    try:
        bootstrap_device_cookies(session)
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
    print("请使用 B Zhan App 的“扫一扫”扫描已打开的二维码图片并确认登录，二维码有效期约三分钟。")
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
            cookie_header = build_login_cookie_header(session, data)
            if "SESSDATA=" not in cookie_header:
                raise SystemExit("登录成功但未收到 SESSDATA，Cookie 未保存；请重新运行脚本。")
            save_session(cookie_header, data.get("refresh_token", ""))
            print(f"✅ 登录成功；会话已保存到：{SESSION_PATH}")
            return
        if status == 86038:
            raise SystemExit("二维码已失效，请重新运行脚本。")
        if status != last_status:
            print({86101: "等待扫码", 86090: "已扫码，等待手机确认"}.get(status, f"登录状态：{status} {data.get('message', '')}"))
            last_status = status
        time.sleep(2)
    raise SystemExit("二维码已超时，请重新运行脚本。")


def ensure_device_cookies(session):
    """按公开设备接口补齐缺失的 buvid3、buvid4 与 b_nut。"""
    required = ("buvid3", "buvid4", "b_nut")
    missing = [name for name in required if not get_cookie_value(session, name)]
    if not missing:
        return []
    if "buvid3" in missing or "b_nut" in missing:
        response = request_bilibili(session, "get", HOME_PAGE_URL, headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Referer": HOME_PAGE_URL}, timeout=10)
        response.raise_for_status()
    missing = [name for name in required if not get_cookie_value(session, name)]
    if "buvid3" in missing or "buvid4" in missing:
        response = request_bilibili(session, "get", FINGERPRINT_API_URL, headers={"Referer": HOME_PAGE_URL}, timeout=10)
        response.raise_for_status()
        data = response.json().get("data", {})
        for cookie_name, value_key in (("buvid3", "b_3"), ("buvid4", "b_4")):
            if not get_cookie_value(session, cookie_name) and data.get(value_key):
                session.cookies.set(cookie_name, data[value_key], domain=".bilibili.com", path="/")
    return [name for name in required if not get_cookie_value(session, name)]


def get_bili_ticket(session):
    """申请新的 bili_ticket，并写回当前会话。"""
    timestamp = int(time.time())
    hexsign = hmac.new(b"XgwSnGZ1p", f"ts{timestamp}".encode("utf-8"), hashlib.sha256).hexdigest()
    response = request_bilibili(session, "post", TICKET_API_URL, params={"key_id": "ec02", "hexsign": hexsign, "context[ts]": timestamp, "csrf": get_csrf(session)}, headers={"Referer": HOME_PAGE_URL}, timeout=10)
    response.raise_for_status()
    payload = response.json()
    ticket = payload.get("data", {}).get("ticket") if payload.get("code") == 0 else None
    if not ticket:
        raise RuntimeError(f"获取 bili_ticket 失败：{payload.get('code')} {payload.get('message')}")
    session.cookies.set("bili_ticket", ticket, domain=".bilibili.com", path="/")


def get_wbi_keys(session):
    """从导航接口获取当前 WBI 密钥。"""
    response = request_bilibili(session, "get", NAV_API_URL, headers={"Referer": HOME_PAGE_URL}, timeout=10)
    response.raise_for_status()
    wbi_img = response.json().get("data", {}).get("wbi_img", {})
    img_url, sub_url = wbi_img.get("img_url", ""), wbi_img.get("sub_url", "")
    if not img_url or not sub_url:
        raise RuntimeError("导航接口未返回 WBI 密钥")
    return (urlparse(img_url).path.rsplit("/", 1)[-1].split(".", 1)[0], urlparse(sub_url).path.rsplit("/", 1)[-1].split(".", 1)[0])


def sign_wbi(params, img_key, sub_key):
    """为查询参数添加 WBI 所需的 wts 与 w_rid。"""
    mixin_key = "".join((img_key + sub_key)[index] for index in WBI_MIXIN_KEY_TAB)[:32]
    signed = {key: "".join(char for char in str(value) if char not in "!'()*") for key, value in params.items()}
    signed["wts"] = str(int(time.time()))
    query = urlencode(sorted(signed.items()), quote_via=quote, safe="")
    signed["w_rid"] = hashlib.md5(f"{query}{mixin_key}".encode("utf-8")).hexdigest()
    return signed


def check_cookie_refresh(session):
    """仅检查 Cookie 是否需要官方刷新；不自动执行验证码流程。"""
    response = request_bilibili(session, "get", COOKIE_INFO_URL, params={"csrf": get_csrf(session)}, headers={"Referer": HOME_PAGE_URL}, timeout=10)
    response.raise_for_status()
    payload = response.json()
    return payload.get("code") == 0 and payload.get("data", {}).get("refresh", False)


def load_authorized_session():
    """读取登录会话，并验证直接请求所需的 Cookie。"""
    cookie_header = load_session().get("cookie_header", "")
    if not cookie_header:
        raise RuntimeError("请先运行 qr_login.py 完成二维码登录。")
    session = create_session(cookie_header)
    missing_login = [name for name in ("SESSDATA", "bili_jct") if not get_cookie_value(session, name)]
    if missing_login:
        raise RuntimeError(f"直接 API 会话缺少登录 Cookie：{', '.join(missing_login)}。请重新运行 qr_login.py。")
    try:
        missing_device = ensure_device_cookies(session)
    except requests.RequestException as error:
        raise RuntimeError(f"自动补齐设备 Cookie 失败：{error}") from error
    if missing_device:
        raise RuntimeError(f"直接 API 会话缺少设备 Cookie，自动补齐未完成：{', '.join(missing_device)}。请重新运行 qr_login.py。")
    return session


def refresh_authorization(session):
    """在每轮扫描前刷新 ticket 与 WBI 密钥。"""
    if check_cookie_refresh(session):
        raise RuntimeError("当前 Cookie 需要刷新，请重新运行 qr_login.py。")
    get_bili_ticket(session)
    return get_wbi_keys(session)
