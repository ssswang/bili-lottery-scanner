# -*- coding: utf-8 -*-
"""测试 NapCat (OneBot 11 HTTP) 连通性，并向 QQ 群发送一条测试通知。"""

import argparse

import requests

from backend.config import NAPCAT_GROUP_IDS, NAPCAT_HTTP_URL, NAPCAT_TOKEN, QQ_ENABLED
from backend.qq_notifier import QQNotifier

DEFAULT_TEST_MESSAGE = "✅ NapCat QQ 通知测试：收到本条消息说明链路正常。"
TIMEOUT_SECONDS = 10


def check_login_info(http_url, token):
    """调用 /get_login_info 验证 NapCat HTTP 服务可达且鉴权有效。"""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = requests.get(
            f"{http_url}/get_login_info", headers=headers, timeout=TIMEOUT_SECONDS
        )
    except requests.RequestException as error:
        print(f"❌ 无法连接 NapCat HTTP 服务 {http_url}：{error}")
        print("   请确认 NapCat 已启动，且网络配置中启用了 HTTP 服务器并监听该地址。")
        return False
    if response.status_code != 200:
        print(f"❌ /get_login_info 返回状态码 {response.status_code}：{response.text[:200]}")
        if response.status_code in (401, 403):
            print("   鉴权失败：请检查 NAPCAT_TOKEN 是否与 NapCat 中的 Access Token 一致。")
        return False
    try:
        body = response.json()
    except ValueError:
        print(f"❌ /get_login_info 返回了非 JSON 内容：{response.text[:200]}")
        return False
    if body.get("retcode") != 0:
        print(f"❌ /get_login_info 返回错误：{body}")
        return False
    login = body.get("data") or {}
    print(f"✅ NapCat 连接正常，机器人账号：{login.get('nickname')}（QQ {login.get('user_id')}）")
    return True


def run_test(args):
    http_url = (args.url or NAPCAT_HTTP_URL).strip().rstrip("/")
    token = (args.token if args.token is not None else NAPCAT_TOKEN).strip()
    group_ids = args.groups or NAPCAT_GROUP_IDS

    if not http_url:
        raise SystemExit("未提供 NapCat 地址：请用 --url 指定，或在 config.txt 配置 NAPCAT_HTTP_URL。")
    if not group_ids:
        raise SystemExit("未提供群号：请用 --group 指定，或在 config.txt 配置 NAPCAT_GROUP_ID。")
    if args.groups:
        try:
            group_ids = [int(group) for group in args.groups]
        except ValueError:
            raise SystemExit("群号无效：--group 只接受数字 QQ 群号。") from None

    print(f"目标 NapCat 服务：{http_url}")
    print(f"目标群号：{', '.join(str(group_id) for group_id in group_ids)}")
    print(f"Access Token：{'已配置' if token else '未配置'}")
    if not check_login_info(http_url, token):
        raise SystemExit(1)

    # 强制启用（enabled=True），便于在 config.txt 尚未开启 QQ_ENABLED 时测试。
    notifier = QQNotifier(
        http_url=http_url, group_ids=group_ids, token=token, enabled=True
    )
    print("\n--- 1. 发送纯文本测试消息 ---")
    ok_text = notifier.post_group(args.message)

    print("\n--- 2. 发送示例抽奖通知（与扫描器真实通知同格式） ---")
    ok_lottery = notifier.send_lottery_notification(
        host_name="测试主播",
        room_id="6",
        gift_text="测试礼物 ×1（合计 100 金瓜子）",
        requirement_str="关注主播",
        total_price=666,
        end_time_str="2099-01-01 00:00:00",
        sender_name="测试发送者",
        area_info="虚拟主播",
    )

    print("\n=== 测试结果 ===")
    print(f"纯文本消息：{'✅ 成功' if ok_text else '❌ 失败'}")
    print(f"抽奖通知：{'✅ 成功' if ok_lottery else '❌ 失败'}")
    if not QQ_ENABLED and (ok_text or ok_lottery):
        print("提示：config.txt 中 QQ_ENABLED=0，扫描器暂不会发 QQ 通知；确认无误后改为 1。")
    raise SystemExit(0 if (ok_text and ok_lottery) else 1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="测试 NapCat HTTP 连通性并向 QQ 群发送测试消息"
    )
    parser.add_argument("--url", default="", help="NapCat HTTP 地址，如 http://127.0.0.1:3000；默认读 config.txt")
    parser.add_argument(
        "--group",
        dest="groups",
        action="append",
        help="目标 QQ 群号，可多次传入；默认读 config.txt 的 NAPCAT_GROUP_ID",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="NapCat Access Token（无鉴权时留空）；默认读 config.txt 的 NAPCAT_TOKEN",
    )
    parser.add_argument("--message", default=DEFAULT_TEST_MESSAGE, help="自定义纯文本测试消息内容")
    return parser.parse_args()


def main():
    try:
        run_test(parse_args())
    except KeyboardInterrupt:
        print("\n已停止测试。")
    except requests.RequestException as error:
        raise SystemExit(f"❌ 请求异常：{error}") from error


if __name__ == "__main__":
    main()
