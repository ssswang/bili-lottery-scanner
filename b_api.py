# -*- coding: utf-8 -*-
"""不启动浏览器，直接请求 B 站 getLotteryInfoWeb 接口的实验脚本。"""

import argparse
import hashlib
import hmac
import time
from urllib.parse import quote, urlencode, urlparse

import requests

from settings import load_session


LOTTERY_API_URL = "https://api.live.bilibili.com/xlive/lottery-interface/v1/lottery/getLotteryInfoWeb"
TICKET_API_URL = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
NAV_API_URL = "https://api.bilibili.com/x/web-interface/nav"
COOKIE_INFO_URL = "https://passport.bilibili.com/x/passport-login/web/cookie/info"
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


def get_bili_ticket(session):
    """申请新的 bili_ticket，并写回当前 Session 以降低接口风控概率。"""
    timestamp = int(time.time())
    hexsign = hmac.new(
        b"XgwSnGZ1p", f"ts{timestamp}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    response = session.post(
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
    response = session.get(
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
    response = session.get(
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
    required_cookies = ("SESSDATA", "bili_jct", "buvid3", "buvid4", "b_nut")
    missing_cookies = [
        name for name in required_cookies if not get_cookie_value(session, name)
    ]
    if missing_cookies:
        raise RuntimeError(
            "直接 API 会话缺少设备/登录 Cookie："
            f"{', '.join(missing_cookies)}。请重新运行 qr_login.py。"
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
    response = session.get(
        LOTTERY_API_URL,
        params=params,
        headers={"Referer": f"https://live.bilibili.com/{room_id}"},
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
