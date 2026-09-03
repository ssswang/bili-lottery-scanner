# -*- coding: utf-8 -*-
"""用 FreeProxy 的多个代理源抓取指定国家的免费代理，验证能连通
live.bilibili.com 后保存到 proxies.txt。默认国家为美国（US）。"""

import argparse
import os
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# pyfreeproxy 的 proxynova 源依赖 quickjs（需 C++ 编译工具）；本脚本不用
# proxynova，导入失败时注入一个最小桩，保证包可以正常加载。
try:
    import quickjs  # noqa: F401
except ImportError:
    stub = types.ModuleType("quickjs")

    class _UnavailableContext:
        def __getattr__(self, name):
            raise NotImplementedError("quickjs 不可用；proxynova 代理源无法使用。")

    stub.Context = _UnavailableContext
    sys.modules["quickjs"] = stub

from freeproxy.modules import proxies as proxy_modules

from proxy.proxy_pool import PROXY_FILE


TEST_URL = "https://live.bilibili.com/"
TARGET_GOOD_PROXIES = 10  # 攒够这么多可用代理就提前停止验证
MAX_ACCEPTABLE_SECONDS = 12.0  # 响应超过该时间的代理视为质量不佳
TEST_HEADERS = {
    "User-Agent": "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Referer": "https://live.bilibili.com/",
}
# 精选的快速代理源；Databay/Proxynova 等慢源已排除。
SOURCE_NAMES = (
    "TheSpeedXProxiedSession",
    "ProxyScrapeProxiedSession",
    "ProxiflyProxiedSession",
    "GeonodeProxiedSession",
    "OpenProxyListProxiedSession",
    "FreeproxylistProxiedSession",
    "IPRoyalProxiedSession",
    "ProxySpaceProxiedSession",
    "KuaidailiProxiedSession",
    "IP3366ProxiedSession",
    "IP89ProxiedSession",
)
SOURCE_TIMEOUT_SECONDS = 60  # 单个代理源超时即跳过


def collect_candidates(country_code):
    """并行抓取各精选代理源的指定国家候选；超时的源直接跳过。

    country_code 为 ANY 时不过滤国家，靠连通性测试筛选可用代理。
    """
    filter_rule = None if country_code == "ANY" else {"country_code": [country_code]}

    def harvest(source_class, found):
        session = source_class(max_pages=1, filter_rule=filter_rule)
        session.refreshproxies()
        found.extend(
            f"{info.ip}:{info.port}"
            for info in session.candidate_proxies
            if info.ip and info.port
        )

    workers = {}
    for name in SOURCE_NAMES:
        source_class = getattr(proxy_modules, name, None)
        if source_class is None:
            print(f"⚠️ 跳过不存在的代理源：{name}")
            continue
        found = []
        worker = threading.Thread(target=harvest, args=(source_class, found), daemon=True)
        worker.start()
        workers[name] = (worker, found)

    deadline = time.monotonic() + SOURCE_TIMEOUT_SECONDS
    candidates = set()
    for name, (worker, found) in workers.items():
        worker.join(max(0, deadline - time.monotonic()))
        if worker.is_alive():
            print(f"⚠️ 代理源 {name} 超过 {SOURCE_TIMEOUT_SECONDS} 秒，跳过。")
            continue
        found = set(found)
        print(f"[{name}] {len(found)} 个候选")
        candidates |= found
    return sorted(candidates)


def test_proxy(proxy_url, timeout=6):
    """代理能成功访问 live.bilibili.com 时返回响应耗时，否则返回 None。"""
    proxies = {"http": f"http://{proxy_url}", "https": f"http://{proxy_url}"}
    try:
        response = requests.get(
            TEST_URL,
            proxies=proxies,
            headers=TEST_HEADERS,
            timeout=timeout,
        )
        if response.status_code == 200 and "bilibili" in response.text.lower():
            return response.elapsed.total_seconds()
    except requests.RequestException:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(
        description="抓取指定国家的免费代理，用 live.bilibili.com 验证后保存到 proxies.txt"
    )
    parser.add_argument("--threads", type=int, default=50, help="并发验证线程数")
    parser.add_argument("--pages", type=int, default=1, help="每个源抓取的页数")
    parser.add_argument("--country", default="US", help="两位国家代码，默认 US（美国）；ANY 表示不限国家")
    args = parser.parse_args()

    country_code = args.country.upper()
    print(f"正在从 {len(SOURCE_NAMES)} 个代理源抓取 {country_code} 候选代理……")
    candidates = collect_candidates(country_code)
    print(f"共 {len(candidates)} 个候选代理，开始验证连通性……")
    if not candidates:
        print("⚠️ 未抓取到候选代理，proxies.txt 保持不变。")
        return

    working = []
    stop_validation = False
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futures = {pool.submit(test_proxy, proxy_url): proxy_url for proxy_url in candidates}
        for future in as_completed(futures):
            proxy_url = futures[future]
            elapsed = future.result()
            if elapsed is not None:
                print(f"✅ {proxy_url}（{elapsed:.2f} 秒）", flush=True)
                working.append((elapsed, proxy_url))
                # 已经验证出足够多的可用代理，取消剩余验证任务。
                if len(working) >= TARGET_GOOD_PROXIES * 2:
                    stop_validation = True
                    break
        if stop_validation:
            for future in futures:
                future.cancel()

    # 只保留质量最好的：响应最快的前 TARGET_GOOD_PROXIES 个，且不超过阈值。
    working.sort()
    working = [
        (elapsed, proxy_url)
        for elapsed, proxy_url in working
        if elapsed <= MAX_ACCEPTABLE_SECONDS
    ][:TARGET_GOOD_PROXIES]

    working.sort()
    PROXY_FILE.write_text(
        "\n".join(proxy_url for _, proxy_url in working) + ("\n" if working else ""),
        encoding="utf-8",
    )
    print(f"✅ {country_code} 可用代理 {len(working)} 个，已保存到：{PROXY_FILE}")
    # 超时跳过的源仍有后台线程在跑，这里直接结束进程避免卡住。
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
