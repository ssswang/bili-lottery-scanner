# -*- coding: utf-8 -*-
"""监视脚本：打开多个直播间，人工通过验证码，持续记录
getLotteryInfoWeb / gaia-vgate 的请求与响应、Cookie 变化，还原风控清除链路。"""

import json
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

ROOMS = ["4522620", "23058", "5050450", "21622887", "796809", "1726455263"]
REPORT_PATH = "capture_report.json"
TARGET_HOSTS = ("api.bilibili.com", "api.live.bilibili.com")
WATCHED = ("getLotteryInfoWeb", "gaia-vgate", "ExClimbWuzhi", "ExGetAxe", "GenWebTicket")
DWELL_SECONDS = 20


def main():
    events = []
    cookie_snapshots = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            viewport={"width": 1600, "height": 900},
        )
        page = context.new_page()

        def watched(url):
            return any(k in url for k in WATCHED)

        def on_response(response):
            url = response.url
            if not any(urlparse(url).netloc.endswith(h) for h in TARGET_HOSTS):
                return
            entry = {
                "kind": "response",
                "url": url,
                "status": response.status,
            }
            if watched(url):
                try:
                    entry["body"] = response.text()[:1500]
                except Exception:
                    entry["body"] = None
                entry["headers"] = {
                    k: v for k, v in response.headers.items()
                    if k.lower() in ("set-cookie", "x-bili-gaia-vtoken", "content-type")
                }
            events.append(entry)

        def on_request(request):
            url = request.url
            if not any(urlparse(url).netloc.endswith(h) for h in TARGET_HOSTS):
                return
            if watched(url):
                events.append(
                    {
                        "kind": "request",
                        "url": url,
                        "method": request.method,
                        "headers": dict(request.headers),
                        "post_data": request.post_data,
                    }
                )

        page.on("response", on_response)
        page.on("request", on_request)

        for index, room in enumerate(ROOMS, 1):
            url = f"https://live.bilibili.com/{room}"
            print(f"[{index}/{len(ROOMS)}] 打开 {url} ……", flush=True)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as error:
                print(f"  打开失败：{error}", flush=True)
                continue

            # 出现 GeeTest 面板则等用户完成。
            try:
                page.wait_for_selector("div.geetest_panel", timeout=3_000, state="visible")
                print("  ⚠️ 检测到验证码，请在浏览器中完成……", flush=True)
                page.wait_for_selector("div.geetest_panel", timeout=300_000, state="detached")
                print("  ✅ 验证码完成。", flush=True)
            except PlaywrightTimeoutError:
                pass

            page.wait_for_timeout(DWELL_SECONDS * 1000)
            cookie_snapshots[url] = sorted(c["name"] for c in context.cookies())
            print(f"  cookies: {cookie_snapshots[url]}", flush=True)

        browser.close()

    report = {"events": events, "cookie_snapshots": cookie_snapshots}
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"共 {len(events)} 条事件，报告已保存：{REPORT_PATH}")


if __name__ == "__main__":
    main()
