# -*- coding: utf-8 -*-
"""Lottery API 扫描器的运行配置。"""

from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("config.txt")


def load_config():
    """读取 config.txt 中的 KEY=VALUE 配置；文件不存在时使用默认值。"""
    if not CONFIG_PATH.is_file():
        return {}

    values = {}
    for raw_line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_int(config, key, default, minimum=None):
    """读取整数配置；无效时回退默认值，可限制最小值。"""
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        print(f"⚠️ 配置 {key} 无效，使用默认值 {default}。")
        return default
    return max(minimum, value) if minimum is not None else value


def get_float(config, key, default, minimum=None):
    """读取小数配置；无效时回退默认值，可限制最小值。"""
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        print(f"⚠️ 配置 {key} 无效，使用默认值 {default}。")
        return default
    return max(minimum, value) if minimum is not None else value


CONFIG = load_config()

# 扫描配置
HOT_RANK_LIMIT = get_int(CONFIG, "HOT_RANK_LIMIT", 80, minimum=1)
ROOM_INTERVAL_SECONDS = get_float(CONFIG, "ROOM_INTERVAL_SECONDS", 5, minimum=2)
RISK_BACKOFF_SECONDS = get_int(CONFIG, "RISK_BACKOFF_SECONDS", 900, minimum=60)
RED_ALERT_AVG_THRESHOLD = get_float(CONFIG, "RED_ALERT_AVG_THRESHOLD", 3, minimum=0)
PURPLE_ALERT_THRESHOLD = get_int(CONFIG, "PURPLE_ALERT_THRESHOLD", 9, minimum=0)
BEEP_ENABLED = get_int(CONFIG, "BEEP_ENABLED", 1) == 1

# Discord 配置
DISCORD_ENABLED = get_int(CONFIG, "DISCORD_ENABLED", 0) == 1
DISCORD_WEBHOOK = CONFIG.get("DISCORD_WEBHOOK", "")
