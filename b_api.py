# -*- coding: utf-8 -*-
"""B Zhan 直播抽奖与分区榜接口请求。"""

import argparse

import requests

from auth_manager import (
    load_authorized_session,
    refresh_authorization,
    request_bilibili,
    sign_wbi,
)


LOTTERY_API_URL = "https://api.live.bilibili.com/xlive/lottery-interface/v1/lottery/getLotteryInfoWeb"
POPULAR_ANCHOR_RANK_API_URL = "https://api.live.bilibili.com/xlive/general-interface/v1/rank/getPopularAnchorRank"


def request_lottery_info(session, room_id, wbi_keys):
    """为本次请求生成新签名，并获取指定直播间的抽奖信息。"""
    img_key, sub_key = wbi_keys
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


def request_popular_anchor_rank(session, area_id, parent_area_id, rank_type, wbi_keys):
    """请求一个直播分区人气榜，并为本次请求生成新的 WBI 签名。"""
    img_key, sub_key = wbi_keys
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
        print("⚠️ 请求触发 -352 风控；请停止高频请求，并在网页完成必要验证后更新 Cookie。")
        if payload.get("data", {}).get("v_voucher"):
            print("接口返回了 v_voucher（本脚本不会自动处理验证码）。")
    elif code != 0:
        print(f"⚠️ 接口返回非成功状态：{code} {payload.get('message')}")
    else:
        print("✅ 成功获取抽奖信息")
    print(payload)


if __name__ == "__main__":
    main()
