# -*- coding: utf-8 -*-
"""直连抽奖扫描器的 Discord 通知模块。"""

from datetime import datetime

import requests

from config import DISCORD_ENABLED, DISCORD_WEBHOOK


class DiscordNotifier:
    """仅在提供 Webhook 时发送通知，同时始终保留终端输出。"""

    def __init__(self, webhook_url=None, enabled=None):
        self.webhook_url = DISCORD_WEBHOOK if webhook_url is None else webhook_url
        if enabled is None:
            # 显式传入命令行 Webhook 时，视为临时启用通知。
            self.enabled = bool(webhook_url) if webhook_url is not None else DISCORD_ENABLED
        else:
            self.enabled = enabled

    def send_lottery_notification(
        self, username, room_id, gift_text, requirement_str, total_price, end_time_str
    ):
        """发送红包或天选抽奖通知。"""
        payload = {
            "embeds": [
                {
                    "title": f"🔥 红包/抽奖预警！主播: {username} ({room_id})",
                    "url": f"https://live.bilibili.com/{room_id}",
                    "color": 16729221,
                    "fields": [
                        {
                            "name": "👤 直播间",
                            "value": f"[{room_id}](https://live.bilibili.com/{room_id})",
                            "inline": False,
                        },
                        {
                            "name": "🎁 包含礼物/奖品",
                            "value": gift_text[:1024] or "未知",
                            "inline": False,
                        },
                        {
                            "name": "🔑 参与门槛",
                            "value": requirement_str or "无要求",
                            "inline": True,
                        },
                        {
                            "name": "💰 最大包价值",
                            "value": f"**{total_price}** 电池",
                            "inline": True,
                        },
                        {
                            "name": "🕒 开奖时间",
                            "value": end_time_str or "未知",
                            "inline": False,
                        },
                    ],
                    "footer": {"text": "B站直播红包/天选抽奖直连监控"},
                }
            ]
        }
        return self.post_discord(payload)

    def send_interaction_notification(self, message):
        """发送风控或人工处理提醒。"""
        print(message)
        return self.post_discord(
            {
                "embeds": [
                    {
                        "title": "⚠️ 需要人工处理",
                        "color": 16711680,
                        "description": message,
                        "footer": {
                            "text": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        },
                    }
                ]
            }
        )

    def send_top_rank_notification(self, rank_rooms):
        """发送页面人气榜前三名通知。"""
        fields = []
        for rank, room in enumerate(rank_rooms, start=1):
            user_id = room["user_id"]
            anchor_name = room["anchor_name"]
            fields.append(
                {
                    "name": f"Top {rank}",
                    "value": (
                        f"主页：[{anchor_name}](https://space.bilibili.com/{user_id})\n"
                        f"主播：{anchor_name}"
                    ),
                    "inline": False,
                }
            )
        return self.post_discord(
            {
                "embeds": [
                    {
                        "title": "📈 B Zhan 人气榜 Top 3",
                        "color": 3447003,
                        "fields": fields,
                        "footer": {
                            "text": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        },
                    }
                ]
            }
        )

    def post_discord(self, payload):
        """Webhook 未配置时仅跳过远程通知，不影响终端输出。"""
        if not self.enabled:
            return False
        if not self.webhook_url:
            print("⚠️ Discord 已启用但未配置 DISCORD_WEBHOOK，跳过通知。")
            return False
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            if response.status_code in (200, 204):
                print("🚀 Discord 通知已成功送达！")
                return True
            print(f"❌ Discord 通知发送失败，状态码: {response.status_code}")
        except requests.RequestException as error:
            print(f"❌ 发送 Discord 通知时发生异常: {error}")
        return False
