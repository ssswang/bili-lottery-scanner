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


CONFIG = load_config()

# 当前 WS 监视器需要的运行配置。
RISK_BACKOFF_SECONDS = get_int(CONFIG, "RISK_BACKOFF_SECONDS", 60, minimum=60)

# Discord 配置
DISCORD_ENABLED = get_int(CONFIG, "DISCORD_ENABLED", 0) == 1
DISCORD_WEBHOOK = CONFIG.get("DISCORD_WEBHOOK", "")
