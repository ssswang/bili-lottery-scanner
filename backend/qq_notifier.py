# -*- coding: utf-8 -*-
"""直连抽奖扫描器的 QQ 群通知模块（NapCat / OneBot 11 HTTP）。"""

from datetime import datetime

import requests

from backend.config import (
    NAPCAT_GROUP_IDS,
    NAPCAT_HTTP_URL,
    NAPCAT_TOKEN,
    QQ_ENABLED,
)

# QQ 单条群消息的安全长度上限，超出部分截断。
MAX_MESSAGE_LENGTH = 4000


class QQNotifier:
    """通过 NapCat 的 OneBot 11 HTTP 接口向 QQ 群发送通知，内容与 Discord 通知保持一致。"""

    def __init__(self, http_url=None, group_ids=None, token=None, enabled=None):
        url = NAPCAT_HTTP_URL if http_url is None else http_url
        self.http_url = (url or "").strip().rstrip("/")
        self.group_ids = NAPCAT_GROUP_IDS if group_ids is None else group_ids
        self.token = (NAPCAT_TOKEN if token is None else token).strip()
        if enabled is None:
            self.enabled = QQ_ENABLED
        else:
            self.enabled = enabled

    def send_lottery_notification(
        self,
        host_name,
        room_id,
        gift_text,
        requirement_str,
        total_price,
        end_time_str,
        sender_name="",
        area_info="",
    ):
        """发送红包或天选抽奖通知（与 Discord 通知同内容）。"""
        lines = [
            f"🔥 红包/抽奖预警！主播: {host_name} ({room_id})",
            f"🏠 直播间：https://live.bilibili.com/{room_id}",
            f"🎁 包含礼物/奖品：{gift_text or '未知'}",
        ]
        if sender_name:
            lines.append(f"📤 红包发送者：{sender_name}")
        lines.append(f"🔑 参与门槛：{requirement_str or '无要求'}")
        lines.append(f"💰 最大包价值：{total_price} 电池")
        lines.append(f"🕒 开奖时间：{end_time_str or '未知'}")
        if area_info:
            lines.append(f"📂 直播分区：{area_info}")
        return self.post_group("\n".join(lines))

    def send_interaction_notification(self, message):
        """发送风控或人工处理提醒。"""
        print(message)
        return self.post_group(
            f"⚠️ 需要人工处理\n{message}\n"
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def send_top_rank_notification(self, rank_rooms):
        """发送页面人气榜前三名通知。"""
        lines = ["📈 B Zhan 人气榜 Top 3"]
        for rank, room in enumerate(rank_rooms, start=1):
            room_id = room["room_id"]
            lines.append(
                f"Top {rank} 直播间：https://live.bilibili.com/{room_id} "
                f"主播：{room['anchor_name']}"
            )
        lines.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return self.post_group("\n".join(lines))

    def post_group(self, message):
        """未启用或未配置时跳过远程通知，不影响终端输出。"""
        if not self.enabled:
            return False
        if not self.http_url or not self.group_ids:
            print("⚠️ QQ 通知已启用但未配置 NAPCAT_HTTP_URL / NAPCAT_GROUP_ID，跳过通知。")
            return False

        message = message[:MAX_MESSAGE_LENGTH]
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        delivered = False
        for group_id in self.group_ids:
            try:
                response = requests.post(
                    f"{self.http_url}/send_group_msg",
                    json={"group_id": group_id, "message": message},
                    headers=headers,
                    timeout=10,
                )
                if self._is_success(response):
                    print(f"🚀 QQ 群 {group_id} 通知已成功送达！")
                    delivered = True
                else:
                    print(
                        f"❌ QQ 群 {group_id} 通知发送失败，"
                        f"状态码: {response.status_code}，响应: {response.text[:200]}"
                    )
            except requests.RequestException as error:
                print(f"❌ 发送 QQ 群 {group_id} 通知时发生异常: {error}")
        return delivered

    @staticmethod
    def _is_success(response):
        """OneBot 11 以 retcode=0 表示成功；兼容仅返回 HTTP 200 的实现。"""
        if response.status_code != 200:
            return False
        try:
            return int(response.json().get("retcode")) == 0
        except (ValueError, AttributeError):
            return True
