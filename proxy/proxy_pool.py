# -*- coding: utf-8 -*-
"""代理列表的读取与轮询分配，供可选代理工具使用。"""

from pathlib import Path
from threading import Lock
from urllib.parse import urlparse


PROXY_FILE = Path(__file__).with_name("proxies.txt")
DEFAULT_PROXY_COUNT = 3


def load_proxies(proxy_file=PROXY_FILE):
    """读取已验证的代理列表；文件不存在时返回空列表。"""
    if not proxy_file.is_file():
        return []
    proxies = [
        line.strip()
        for line in proxy_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return proxies


def format_proxy_url(proxy):
    """把 ip:port 或 host:port 规范成带协议前缀的代理地址。"""
    if "://" in proxy:
        return proxy
    return f"http://{proxy}"


def request_proxies(proxy_url):
    """生成 requests 使用的 proxies 字典。"""
    return {"http": proxy_url, "https": proxy_url}


class ProxyRotator:
    """把代理按请求顺序轮询分配给多个并发工作线程。"""

    def __init__(self, proxies):
        self.proxies = [format_proxy_url(proxy) for proxy in proxies]
        self._lock = Lock()
        self._next_index = 0

    def take(self):
        """返回下一个代理；无代理时返回 None。"""
        if not self.proxies:
            return None
        with self._lock:
            proxy_url = self.proxies[self._next_index % len(self.proxies)]
            self._next_index += 1
        return proxy_url

    def __len__(self):
        return len(self.proxies)


def remove_proxy(proxy_url, proxy_file=PROXY_FILE):
    """从代理列表文件中剔除失效代理；有变化时返回 True。"""
    proxies = load_proxies(proxy_file)
    normalized = format_proxy_url(proxy_url)
    remaining = [p for p in proxies if format_proxy_url(p) != normalized]
    if len(remaining) == len(proxies):
        return False
    proxy_file.write_text(
        "\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8"
    )
    return True
