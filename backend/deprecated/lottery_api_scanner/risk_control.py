# -*- coding: utf-8 -*-
"""统一处理扫描期间的 B 站 -352 风控响应。"""

import threading
import time


class RiskControlHandler:
    """记录命中 -352 的账号，并在本轮结束后统一冷却。"""

    def __init__(self, cooldown_seconds):
        self.cooldown_seconds = cooldown_seconds
        self._hit_accounts = set()
        self._lock = threading.Lock()

    def record_352(self, account_name, room_id):
        """记录一个账号的 -352 命中；调用方应停止该账号的当前分片。"""
        with self._lock:
            self._hit_accounts.add(account_name)
        print(f"⚠️ 账号 {account_name} 在房间 {room_id} 触发 -352，本轮停止。")

    def cooldown_if_needed(self):
        """输出本轮命中的账号并冷却；未命中时返回 False。"""
        with self._lock:
            hit_accounts = sorted(self._hit_accounts)
            self._hit_accounts.clear()
        if not hit_accounts:
            return False

        print(
            f"⚠️ 命中 -352 风控的账号：{('、'.join(hit_accounts))}；"
            f"休息 {self.cooldown_seconds} 秒后继续扫描。"
        )
        time.sleep(self.cooldown_seconds)
        return True
