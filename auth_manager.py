# -*- coding: utf-8 -*-
"""B Zhan 直接 API 的二维码登录、会话 Cookie 与 WBI 鉴权。"""

import hashlib
import hmac
import json
import base64
import os
import random
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlparse

import requests

from proxy.proxy_pool import request_proxies


TICKET_API_URL = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
NAV_API_URL = "https://api.bilibili.com/x/web-interface/nav"
NAVIGATE_API_URL = "https://api.live.bilibili.com/room/v2/Index/getNavigate"
EX_CLIMB_WUZHI_URL = "https://api.bilibili.com/x/internal/gaia-gateway/ExClimbWuzhi"
COOKIE_INFO_URL = "https://passport.bilibili.com/x/passport-login/web/cookie/info"
HOME_PAGE_URL = "https://www.bilibili.com/"
FINGERPRINT_API_URL = "https://api.bilibili.com/x/frontend/finger/spi"
QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
SESSION_PATH = Path(__file__).with_name("lotteryapi_session.json")
ANON_IDENTITIES_PATH = Path(__file__).with_name("anonymous_identities.json")
QR_IMAGE_PATH = Path(__file__).with_name("qr_login.png")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
WEBGL_VENDOR = "Google Inc. (AMD)"
WEBGL_RENDERER = "ANGLE (AMD, AMD Radeon 780M Graphics (0x00001900) Direct3D11 vs_5_0 ps_5_0, D3D11)"
DEVICE_OS_NAME = "Win32"
DEVICE_RESOLUTION = "1920x1080"
DEVICE_TIMEZONE = "America/New_York"

# 每个身份（账号/匿名连接）一份唯一且持久化的硬件指纹档案，避免多个
# 身份共享同一"设备"特征而被风控关联。
DEVICE_PROFILES_PATH = Path(__file__).with_name("device_profiles.json")

# 现实存在的 WebGL vendor~renderer 组合，与对应分辨率/时区随机搭配。
WEBGL_POOL = (
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon 780M Graphics (0x00001900) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
)
RESOLUTION_POOL = ("1920x1080", "2560x1440", "1366x768", "1600x900", "3840x2160")
TIMEZONE_POOL = (
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Phoenix",
)


def load_device_profiles():
    """读取已持久化的设备指纹档案。"""
    if not DEVICE_PROFILES_PATH.is_file():
        return {}
    try:
        return json.loads(DEVICE_PROFILES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"⚠️ 读取设备指纹档案失败，将全部重建：{error}")
        return {}


def get_device_profile(key):
    """取指定身份的设备指纹档案；首次访问时随机生成并持久化。"""
    profiles = load_device_profiles()
    if key in profiles:
        return profiles[key]
    vendor, renderer = random.choice(WEBGL_POOL)
    profile = {
        "webgl_vendor": vendor,
        "webgl_renderer": renderer,
        "resolution": random.choice(RESOLUTION_POOL),
        "timezone": random.choice(TIMEZONE_POOL),
        "device_memory": random.choice((4, 8, 16, 32)),
        "hardware_concurrency": random.choice((4, 8, 12, 16, 24)),
        # canvas 指纹片段（bfe9/13ab），每个设备唯一。
        "canvas": base64.b64encode(os.urandom(48)).decode("ascii"),
        "_uuid": generate_uuid_cookie(),
        "b_lsid": generate_b_lsid(),
    }
    profiles[key] = profile
    try:
        DEVICE_PROFILES_PATH.write_text(
            json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as error:
        print(f"⚠️ 保存设备指纹档案失败：{error}")
    return profile
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


def session_file(name):
    """按账号名返回会话文件路径。"""
    return Path(__file__).with_name(f"lotteryapi_session_{name}.json")


def save_session(cookie_header, refresh_token, name="default"):
    """保存指定账号的 Cookie 与刷新令牌。"""
    path = session_file(name) if name != "default" else SESSION_PATH
    path.write_text(json.dumps({"cookie_header": cookie_header, "refresh_token": refresh_token}, ensure_ascii=False, indent=2), encoding="utf-8")


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
    for path in Path(__file__).parent.glob("lotteryapi_session_*.json"):
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


def replace_bilibili_cookie(session, name, value):
    """移除同名 Cookie 后写入唯一的 .bilibili.com Cookie。"""
    for cookie in list(session.cookies):
        if cookie.name == name:
            session.cookies.clear(cookie.domain, cookie.path, cookie.name)
    session.cookies.set(name, value, domain=".bilibili.com", path="/")


def get_csrf(session):
    """从 Cookie 中读取 bili_jct。"""
    return get_cookie_value(session, "bili_jct")


def create_session(cookie_header, proxy_url=None):
    """创建带有用户 Cookie 和常用 Web 请求头的会话；可选绑定一个代理。"""
    session = requests.Session()
    # 缺少 sec-ch-ua / sec-fetch 系列头会直接被 getLotteryInfoWeb 以 -352 拒绝。
    session.headers.update({
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
    })
    session.cookies.update(parse_cookie_header(cookie_header))
    if proxy_url:
        # 忽略系统环境变量里的代理设置，避免与配置的代理叠加。
        session.trust_env = False
        session.proxies.update(request_proxies(proxy_url))
    return session


def build_cookie_header(session):
    """把会话中的全部 Cookie（含补齐的设备 Cookie）还原为请求头字符串。"""
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in session.cookies)


def build_anonymous_session(proxy_url=None, identity_key=None):
    """构建带完整设备指纹的匿名会话（无登录 Cookie）。

    identity_key 非空时优先复用磁盘上已持久化的设备身份——匿名身份应像
    真实设备一样长期使用，频繁新建反而会触发风控；命中 -352 后由调用方
    丢弃该身份再重建。配合 getLotteryInfoWeb 的 need_guard=false 使用。
    """
    if identity_key:
        stored_header = load_anonymous_identities().get(identity_key, "")
        if stored_header:
            session = create_session(stored_header, proxy_url=proxy_url)
            try:
                if not get_cookie_value(session, "bili_ticket"):
                    get_bili_ticket(session)
            except requests.RequestException as error:
                print(f"⚠️ 刷新 bili_ticket 失败：{error}")
            return session

    session = create_session("", proxy_url=proxy_url)
    # 每个身份一份唯一的设备指纹档案（WebGL/分辨率/时区/canvas/_uuid）。
    profile = get_device_profile(identity_key) if identity_key else None
    try:
        # LIVE_BUVID 由直播间接口下发，是匿名身份的起点。
        request_bilibili(session, "get", NAVIGATE_API_URL, timeout=10)
        get_bili_ticket(session)
        # _uuid/b_lsid 由浏览器 JS 生成，先补齐再激活（激活报文的 df35 引用 _uuid）。
        set_client_identity_cookies(session, profile)
        ensure_device_cookies(session, profile)
    except (requests.RequestException, RuntimeError) as error:
        raise RuntimeError(f"构建匿名会话失败：{error}") from error
    if identity_key:
        save_anonymous_identity(identity_key, build_cookie_header(session))
    return session


def load_anonymous_identities():
    """读取已持久化的匿名设备身份；文件不存在或损坏时返回空表。"""
    if not ANON_IDENTITIES_PATH.is_file():
        return {}
    try:
        return json.loads(ANON_IDENTITIES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"⚠️ 读取匿名身份文件失败，将全部重建：{error}")
        return {}


def save_anonymous_identity(identity_key, cookie_header):
    """持久化一个匿名设备身份的 Cookie 头。"""
    identities = load_anonymous_identities()
    identities[identity_key] = cookie_header
    try:
        ANON_IDENTITIES_PATH.write_text(
            json.dumps(identities, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as error:
        print(f"⚠️ 保存匿名身份失败：{error}")


def drop_anonymous_identity(identity_key):
    """丢弃命中风控的匿名身份，下轮构建全新设备。"""
    identities = load_anonymous_identities()
    if identity_key in identities:
        del identities[identity_key]
        try:
            ANON_IDENTITIES_PATH.write_text(
                json.dumps(identities, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as error:
            print(f"⚠️ 清理匿名身份失败：{error}")


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


def qr_login_main(name="default"):
    """通过官方 Web 二维码登录并保存指定账号的直接 API 会话。"""
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
            cookie_header = build_login_cookie_header(session, data)
            if "SESSDATA=" not in cookie_header:
                raise SystemExit("登录成功但未收到 SESSDATA，Cookie 未保存；请重新运行脚本。")
            save_session(cookie_header, data.get("refresh_token", ""), name=name)
            print(f"✅ 账号 {name} 登录成功；会话已保存。")
            return
        if status == 86038:
            raise SystemExit("二维码已失效，请重新运行脚本。")
        if status != last_status:
            print({86101: "等待扫码", 86090: "已扫码，等待手机确认"}.get(status, f"登录状态：{status} {data.get('message', '')}"))
            last_status = status
        time.sleep(2)
    raise SystemExit("二维码已超时，请重新运行脚本。")


def generate_and_set_buvid_fp(session, profile=None):
    """按 buvid3 + UA + WebGL 环境计算 buvid_fp 并写入会话 Cookie。"""
    profile = profile or {}
    buvid3 = get_cookie_value(session, "buvid3")
    raw_string = (
        f"{buvid3}{USER_AGENT}"
        f"{profile.get('webgl_vendor', WEBGL_VENDOR)}"
        f"{profile.get('webgl_renderer', WEBGL_RENDERER)}"
        f"{DEVICE_OS_NAME}"
    )
    buvid_fp = hashlib.md5(raw_string.encode("utf-8")).hexdigest()
    replace_bilibili_cookie(session, "buvid_fp", buvid_fp)
    return buvid_fp


def fetch_live_buvid(session):
    """访问直播间 getNavigate 接口，由 Set-Cookie 写入 LIVE_BUVID。"""
    try:
        response = request_bilibili(session, "get", NAVIGATE_API_URL, timeout=10)
        response.raise_for_status()
    except requests.RequestException as error:
        print(f"⚠️ 获取 LIVE_BUVID 失败：{error}")
        return False
    return bool(get_cookie_value(session, "LIVE_BUVID"))


def generate_uuid_cookie():
    """生成 _uuid Cookie（ExClimbWuzhi 的 df35 字段），格式：UUID大写+数字+infoc。"""
    base = str(uuid.uuid4()).upper()
    digits = f"{random.randint(0, 99999):05d}"
    return f"{base}{digits}infoc"


def generate_b_lsid():
    """生成 b_lsid Cookie（直播间会话标识），格式：8位hex_11位hex。"""
    return (
        "".join(random.choice("0123456789ABCDEF") for _ in range(8))
        + "_"
        + "".join(random.choice("0123456789ABCDEF") for _ in range(11))
    )


def set_client_identity_cookies(session, profile=None, force=False):
    """补齐浏览器端 JS 生成的身份 Cookie：_uuid 与 b_lsid（来自设备档案）。"""
    profile = profile or {}
    if force or not get_cookie_value(session, "_uuid"):
        replace_bilibili_cookie(
            session, "_uuid", profile.get("_uuid") or generate_uuid_cookie()
        )
    if force or not get_cookie_value(session, "b_lsid"):
        replace_bilibili_cookie(
            session, "b_lsid", profile.get("b_lsid") or generate_b_lsid()
        )


def activate_device_cookies(session, profile=None):
    """参考 ExClimbWuzhi 逻辑上报设备指纹，激活风控 Cookie。"""
    profile = profile or {}
    buvid3 = get_cookie_value(session, "buvid3")
    if not buvid3:
        return False
    vendor = profile.get("webgl_vendor", WEBGL_VENDOR)
    renderer = profile.get("webgl_renderer", WEBGL_RENDERER)
    resolution = profile.get("resolution", DEVICE_RESOLUTION)
    screen_w, screen_h = (int(v) for v in resolution.split("x"))
    inner_payload = {
        "3064": 1,
        "5062": str(int(time.time() * 1000)),
        "03bf": "https%3A%2F%2Flive.bilibili.com%2F",
        "39c8": "444.8.fp.risk",
        "34f1": "",
        "d402": "",
        "654a": "",
        "6e7c": resolution,
        "3c43": {
            "2673": 0,
            "5766": profile.get("device_memory", 16),
            "6527": 0,
            "7003": 1,
            "807e": 1,
            "b8ce": USER_AGENT,
            "641c": 1,
            "07a4": "zh-CN",
            "1c57": profile.get("hardware_concurrency", 16),
            "0bd0": profile.get("hardware_concurrency", 16),
            "748e": [screen_h, screen_w],
            "d61f": [screen_h, screen_w],
            "fc9d": 240,
            "6aa9": profile.get("timezone", DEVICE_TIMEZONE),
            "75b8": 1,
            "3b21": 1,
            "8a1c": 0,
            "d52f": "not available",
            "adca": DEVICE_OS_NAME,
            "6bc5": f"{vendor}~{renderer}",
            "52cd": [0, 0, 0],
            "13ab": profile.get("canvas", "sjzzAAAAAElFTkSuQmCC"),
            "bfe9": profile.get("canvas", "//isNzMQAAAAZJREFUAwB3M/7wUSWWrQAAAABJRU5ErkJggg=="),
            "a3c1": "",
        },
        "54ef": "{}",
        "8b94": "",
        "df35": get_cookie_value(session, "_uuid") or None,
        "07a4": "zh-CN",
        "5f45": None,
        "db46": 0,
    }
    body_data = json.dumps({"payload": json.dumps(inner_payload)})
    try:
        response = request_bilibili(
            session,
            "post",
            EX_CLIMB_WUZHI_URL,
            data=body_data,
            headers={"Content-Type": "application/json;charset=UTF-8"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        print(f"⚠️ 激活设备 Cookie 失败：{error}")
        return False
    if payload.get("code") != 0:
        print(f"⚠️ 激活设备 Cookie 返回：{payload.get('code')} {payload.get('message')}")
        return False
    return True


def ensure_device_cookies(session, profile=None):
    """按公开设备接口补齐缺失的 buvid3、buvid4、b_nut 与 buvid_fp，并激活。"""
    required = ("buvid3", "buvid4", "b_nut")
    created = [name for name in required if not get_cookie_value(session, name)]
    if "buvid3" in created or "b_nut" in created:
        response = request_bilibili(session, "get", HOME_PAGE_URL, headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Referer": HOME_PAGE_URL}, timeout=10)
        response.raise_for_status()
    if "buvid3" in created or "buvid4" in created:
        response = request_bilibili(session, "get", FINGERPRINT_API_URL, headers={"Referer": HOME_PAGE_URL}, timeout=10)
        response.raise_for_status()
        data = response.json().get("data", {})
        for cookie_name, value_key in (("buvid3", "b_3"), ("buvid4", "b_4")):
            if not get_cookie_value(session, cookie_name) and data.get(value_key):
                session.cookies.set(cookie_name, data[value_key], domain=".bilibili.com", path="/")

    # 补齐 buvid_fp（依赖 buvid3），缺指纹说明该会话未激活过，需上报激活。
    need_activate = not get_cookie_value(session, "buvid_fp")
    if need_activate and get_cookie_value(session, "buvid3"):
        generate_and_set_buvid_fp(session, profile)

    # LIVE_BUVID 由直播间接口下发，失败不影响扫描。
    if not get_cookie_value(session, "LIVE_BUVID"):
        fetch_live_buvid(session)

    if need_activate:
        activate_device_cookies(session, profile)

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
    ticket_data = payload.get("data", {})
    expires = str(int(ticket_data.get("created_at", timestamp)) + int(ticket_data.get("ttl", 86400)))
    session.cookies.set("bili_ticket_expires", expires, domain=".bilibili.com", path="/")


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


def load_authorized_session(proxy_url=None):
    """读取登录会话，并验证直接请求所需的 Cookie。"""
    cookie_header = load_session().get("cookie_header", "")
    if not cookie_header:
        raise RuntimeError("请先运行 qr_login.py 完成二维码登录。")
    session = create_session(cookie_header, proxy_url=proxy_url)
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
