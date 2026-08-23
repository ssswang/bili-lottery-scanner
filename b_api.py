# -*- coding: utf-8 -*-
"""不启动浏览器，直接请求 B 站 getLotteryInfoWeb 接口的实验脚本。"""

import argparse
import hashlib
import hmac
import time
from collections import deque
from urllib.parse import quote, urlencode, urlparse

import requests

from settings import load_session


LOTTERY_API_URL = "https://api.live.bilibili.com/xlive/lottery-interface/v1/lottery/getLotteryInfoWeb"
POPULAR_ANCHOR_RANK_API_URL = "https://api.live.bilibili.com/xlive/general-interface/v1/rank/getPopularAnchorRank"
TICKET_API_URL = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
NAV_API_URL = "https://api.bilibili.com/x/web-interface/nav"
COOKIE_INFO_URL = "https://passport.bilibili.com/x/passport-login/web/cookie/info"
HOME_PAGE_URL = "https://www.bilibili.com/"
FINGERPRINT_API_URL = "https://api.bilibili.com/x/frontend/finger/spi"
WBI_MIXIN_KEY_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# 服务端限制为 10 分钟 500 次；保留余量给重试及其他 B 站页面操作。
API_RATE_LIMIT = 450
API_RATE_WINDOW_SECONDS = 10 * 60
API_MIN_INTERVAL_SECONDS = API_RATE_WINDOW_SECONDS / API_RATE_LIMIT


class ApiRateLimiter:
    """按滑动时间窗口限制单个扫描会话的 B 站 API 请求数量。"""

    def __init__(
        self,
        max_requests=API_RATE_LIMIT,
        window_seconds=API_RATE_WINDOW_SECONDS,
        minimum_interval_seconds=API_MIN_INTERVAL_SECONDS,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.minimum_interval_seconds = minimum_interval_seconds
        self.request_times = deque()
        self.last_request_time = None

    def wait_for_slot(self):
        """必要时等待最早的请求过期，再登记当前请求。"""
        while True:
            now = time.monotonic()
            if self.last_request_time is not None:
                interval_wait = self.minimum_interval_seconds - (
                    now - self.last_request_time
                )
                if interval_wait > 0:
                    time.sleep(interval_wait)
                    continue

            while (
                self.request_times
                and now - self.request_times[0] >= self.window_seconds
            ):
                self.request_times.popleft()

            if len(self.request_times) < self.max_requests:
                self.request_times.append(now)
                self.last_request_time = now
                return

            wait_seconds = self.window_seconds - (now - self.request_times[0])
            print(
                "⏳ B 站 API 请求接近 10 分钟限制，"
                f"等待 {max(1, round(wait_seconds))} 秒后继续。"
            )
            time.sleep(max(0.1, wait_seconds))


def request_bilibili(session, method, url, **kwargs):
    """通过当前会话发送一个受共享限流器保护的 B 站 API 请求。"""
    limiter = getattr(session, "_bili_api_rate_limiter", None)
    if limiter is None:
        limiter = ApiRateLimiter()
        session._bili_api_rate_limiter = limiter
    limiter.wait_for_slot()
    return getattr(session, method)(url, **kwargs)


def parse_cookie_header(cookie_header):
    """将独立会话文件中的完整 Cookie 请求头转为 requests 的 Cookie。"""
    cookies = {}
    for item in cookie_header.split(";"):
        key, separator, value = item.strip().partition("=")
        if separator and key:
            cookies[key] = value
    return cookies


def get_cookie_value(session, name):
    """按名称读取 Cookie，兼容同名 Cookie 存在于多个域名的情况。"""
    for cookie in session.cookies:
        if cookie.name == name:
            return cookie.value
    return ""


def get_csrf(session):
    """从 Cookie 中读取 bili_jct；未登录 Cookie 时允许为空。"""
    return get_cookie_value(session, "bili_jct")


def create_session(cookie_header):
    """创建带有用户 Cookie 和常用 Web 请求头的会话。"""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://live.bilibili.com",
        }
    )
    session.cookies.update(parse_cookie_header(cookie_header))
    return session


def ensure_device_cookies(session):
    """按公开设备接口补齐缺失的 buvid3、buvid4 与 b_nut。"""
    required_device_cookies = ("buvid3", "buvid4", "b_nut")
    missing_cookies = [
        name for name in required_device_cookies if not get_cookie_value(session, name)
    ]
    if not missing_cookies:
        return []

    # 首页响应会由 requests 自动写入其 Set-Cookie 中的 buvid3 与 b_nut。
    if "buvid3" in missing_cookies or "b_nut" in missing_cookies:
        response = request_bilibili(
            session,
            "get",
            HOME_PAGE_URL,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": HOME_PAGE_URL,
            },
            timeout=10,
        )
        response.raise_for_status()

    missing_cookies = [
        name for name in required_device_cookies if not get_cookie_value(session, name)
    ]
    if "buvid3" in missing_cookies or "buvid4" in missing_cookies:
        response = request_bilibili(
            session,
            "get",
            FINGERPRINT_API_URL,
            headers={"Referer": HOME_PAGE_URL},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        fingerprint_data = payload.get("data", {}) if payload.get("code") == 0 else {}
        for cookie_name, value_key in (("buvid3", "b_3"), ("buvid4", "b_4")):
            if not get_cookie_value(session, cookie_name):
                value = fingerprint_data.get(value_key)
                if value:
                    session.cookies.set(
                        cookie_name, value, domain=".bilibili.com", path="/"
                    )

    return [
        name for name in required_device_cookies if not get_cookie_value(session, name)
    ]


def get_bili_ticket(session):
    """申请新的 bili_ticket，并写回当前 Session 以降低接口风控概率。"""
    timestamp = int(time.time())
    hexsign = hmac.new(
        b"XgwSnGZ1p", f"ts{timestamp}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    response = request_bilibili(
        session,
        "post",
        TICKET_API_URL,
        params={
            "key_id": "ec02",
            "hexsign": hexsign,
            "context[ts]": timestamp,
            "csrf": get_csrf(session),
        },
        headers={"Referer": "https://www.bilibili.com/"},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    ticket = payload.get("data", {}).get("ticket") if payload.get("code") == 0 else None
    if not ticket:
        raise RuntimeError(f"获取 bili_ticket 失败：{payload.get('code')} {payload.get('message')}")
    session.cookies.set("bili_ticket", ticket, domain=".bilibili.com", path="/")


def get_wbi_keys(session):
    """从导航接口获取当天的 WBI 实时密钥。"""
    response = request_bilibili(
        session,
        "get",
        NAV_API_URL,
        headers={"Referer": "https://www.bilibili.com/"},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    wbi_img = payload.get("data", {}).get("wbi_img", {})
    img_url = wbi_img.get("img_url", "")
    sub_url = wbi_img.get("sub_url", "")
    if not img_url or not sub_url:
        raise RuntimeError("导航接口未返回 WBI 密钥")
    img_key = urlparse(img_url).path.rsplit("/", 1)[-1].split(".", 1)[0]
    sub_key = urlparse(sub_url).path.rsplit("/", 1)[-1].split(".", 1)[0]
    return img_key, sub_key


def sign_wbi(params, img_key, sub_key):
    """为查询参数添加 WBI 所需的 wts 与 w_rid。"""
    raw_key = img_key + sub_key
    mixin_key = "".join(raw_key[index] for index in WBI_MIXIN_KEY_TAB)[:32]
    signing_params = {
        key: "".join(char for char in str(value) if char not in "!'()*")
        for key, value in params.items()
    }
    signing_params["wts"] = str(int(time.time()))
    # 使用 quote 而不是默认 quote_plus，确保空格编码为 %20。
    query = urlencode(sorted(signing_params.items()), quote_via=quote, safe="")
    signing_params["w_rid"] = hashlib.md5(
        f"{query}{mixin_key}".encode("utf-8")
    ).hexdigest()
    return signing_params


def check_cookie_refresh(session):
    """仅检查 Cookie 是否需要官方刷新；不自动执行刷新或验证码流程。"""
    response = request_bilibili(
        session,
        "get",
        COOKIE_INFO_URL,
        params={"csrf": get_csrf(session)},
        headers={"Referer": "https://www.bilibili.com/"},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("code") == 0 and payload.get("data", {}).get("refresh", False)


def load_authorized_session():
    """读取独立登录会话，并验证直接请求所需的基础 Cookie。"""
    settings = load_session()
    cookie_header = settings.get("cookie_header", "")
    if not cookie_header:
        raise RuntimeError("请先运行 qr_login.py 完成二维码登录。")

    session = create_session(cookie_header)
    missing_login_cookies = [
        name for name in ("SESSDATA", "bili_jct") if not get_cookie_value(session, name)
    ]
    if missing_login_cookies:
        raise RuntimeError(
            "直接 API 会话缺少登录 Cookie："
            f"{', '.join(missing_login_cookies)}。请重新运行 qr_login.py。"
        )

    try:
        missing_device_cookies = ensure_device_cookies(session)
    except requests.RequestException as error:
        raise RuntimeError(f"自动补齐设备 Cookie 失败：{error}") from error
    if missing_device_cookies:
        raise RuntimeError(
            "直接 API 会话缺少设备 Cookie，自动补齐未完成："
            f"{', '.join(missing_device_cookies)}。请重新运行 qr_login.py。"
        )
    return session


def refresh_authorization(session):
    """在每一轮扫描前刷新票据和 WBI 密钥。"""
    if check_cookie_refresh(session):
        raise RuntimeError("当前 Cookie 需要刷新，请重新运行 qr_login.py。")
    get_bili_ticket(session)
    return get_wbi_keys(session)


def request_lottery_info(session, room_id, wbi_keys=None):
    """为本次请求生成新签名，并获取指定直播间的抽奖信息。"""
    img_key, sub_key = wbi_keys or get_wbi_keys(session)
    params = sign_wbi(
        {"roomid": room_id, "need_guard": "true", "web_location": "444.8"},
        img_key,
        sub_key,
    )
    response = request_bilibili(
        session,
        "get",
        LOTTERY_API_URL,
        params=params,
        headers={"Referer": f"https://live.bilibili.com/{room_id}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def request_popular_anchor_rank(
    session, area_id, parent_area_id, rank_type, wbi_keys=None
):
    """请求一个直播分区人气榜，并为本次请求生成新的 WBI 签名。"""
    img_key, sub_key = wbi_keys or get_wbi_keys(session)
    params = sign_wbi(
        {
            "area_id": area_id,
            "clientType": "2",
            "location_code": "",
            "parent_area_id": parent_area_id,
            "rank_id": "0",
            "rank_type": rank_type,
            "uid": "0",
            "web_location": "445.28",
        },
        img_key,
        sub_key,
    )
    response = request_bilibili(
        session,
        "get",
        POPULAR_ANCHOR_RANK_API_URL,
        params=params,
        headers={"Referer": "https://live.bilibili.com/"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(description="直接请求 getLotteryInfoWeb，不启动浏览器")
    parser.add_argument("room_id", help="直播间房间号")
    args = parser.parse_args()

    try:
        session = load_authorized_session()
        wbi_keys = refresh_authorization(session)
        payload = request_lottery_info(session, args.room_id, wbi_keys)
    except requests.RequestException as error:
        raise SystemExit(f"网络请求失败：{error}") from error
    except RuntimeError as error:
        raise SystemExit(error) from error

    code = payload.get("code")
    if code == -352:
        voucher = payload.get("data", {}).get("v_voucher")
        print("⚠️ 请求触发 -352 风控；请停止高频请求，并在网页完成必要验证后更新 Cookie。")
        if voucher:
            print("接口返回了 v_voucher（本脚本不会自动处理验证码）。")
    elif code != 0:
        print(f"⚠️ 接口返回非成功状态：{code} {payload.get('message')}")
    else:
        print("✅ 成功获取抽奖信息")
    print(payload)


if __name__ == "__main__":
    main()
