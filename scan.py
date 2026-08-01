# -*- coding: utf-8 -*-
# ==============================================================================
# 项目名称: B站直播红包&天选抽奖检测脚本
# 联合作者: Gemini & 我
# ==============================================================================

import time
import os
import json
import re
import winsound
import requests
import traceback  
import urllib.request
from datetime import datetime
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

def load_external_config():
    """Load KEY=VALUE entries from config.txt first, then .env."""
    config_dir = os.path.dirname(os.path.abspath(__file__))
    filename = "config.txt"
    config_path = os.path.join(config_dir, filename)
    if not os.path.isfile(config_path):
        return {}
    
    values = {}
    with open(config_path, "r", encoding="utf-8") as config_file:
        for raw_line in config_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    print(f"Loaded config file: {config_path}")
    return values


def get_int_config(config, key, default):
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        print(f"Invalid {key}; using default {default}.")
        return default


def get_list_config(config, key, default):
    raw_value = config.get(key, default)
    if not raw_value:
        print("No category urls")
        return []
    try:
        urls = json.loads(raw_value)
        if isinstance(urls, list) and all(isinstance(url, str) for url in urls):
            return urls
    except json.JSONDecodeError:
        pass
    print(f"{key} must be a JSON array; using an empty list.")
    return []


CONFIG = load_external_config()
CUSTOM_ROOM_IDS = get_list_config(CONFIG, "CUSTOM_ROOM_IDS", "[1852636821]")
CATEGORY_URLS = get_list_config(CONFIG, "CATEGORY_URLS", '["https://live.bilibili.com/p/eden/area-tags?areaId=0&parentAreaId=1", "https://live.bilibili.com/p/eden/area-tags?&areaId=190&parentAreaId=5"]')
ROOM_COUNT = get_int_config(CONFIG, "ROOM_COUNT", 40)
IM_SWITCH = get_int_config(CONFIG, "IM_SWITCH", 0)
DISCORD_WEBHOOK = CONFIG.get("DISCORD_WEBHOOK", "")
RED_ALERT_THRESHOLD = get_int_config(CONFIG, "RED_ALERT_THRESHOLD", 40)
PURPLE_ALERT_THRESHOLD = get_int_config(CONFIG, "PURPLE_ALERT_THRESHOLD", 10)

def send_notification(username, room_id, gift_text, requirement_str, total_price, end_time_str):
    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK is not configured; skipping notification.")
        return
    """
    异步发送通知
    """
    payload = {
        "embeds": [
            {
                "title": f"🔥 红包/抽奖预警！主播: {username} ({room_id})",
                "url": f"https://live.bilibili.com/{room_id}",
                "color": 16729221,  # B站标志性粉色 (#FF6699)
                "fields": [
                    {
                        "name": "👤 主播信息",
                        "value": f"**昵称**: {username}\n**房间**: [{room_id}](https://live.bilibili.com/{room_id})",
                        "inline": False
                    },
                    {
                        "name": "房号",
                        "value": str(room_id), 
                        "inline": False
                    },
                    {
                        "name": "🎁 包含礼物/奖品",
                        "value": gift_text, 
                        "inline": False
                    },
                    {
                        "name": "🔑 参与门槛",
                        "value": f"**{requirement_str}**",
                        "inline": True
                    },
                    {
                        "name": "💰 总价值",
                        "value": f"**{total_price}** 电池",
                        "inline": True
                    },
                    {
                        "name": "🕒 开奖时间/倒计时",
                        "value": f"{end_time_str}",
                        "inline": False
                    }
                ],
                "footer": {
                    "text": "B站直播红包/天选抽奖监控"
                }
            }
        ]
    }

    try:
        response = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if response.status_code in [200, 204]:
            print("🚀 Discord 通知异步发送成功！")
        else:
            print(f"❌ Webhook 发送失败，状态码: {response.status_code}")
    except requests.RequestException as e:
        print(f"❌ 发送通知时出现异常: {e}")


def send_crash_notification(error_msg):
    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK is not configured; skipping crash notification.")
        return
    """
    程序崩溃 alert
    """
    payload = {
        "embeds": [
            {
                "title": "🚨 脚本崩溃警报！",
                "color": 16711680,  # 红色警告 (#FF0000)
                "description": f"监控脚本已停止运行，请及时检查服务器或本地环境。\n\n**错误堆栈信息:**\n```python\n{error_msg[-1800:]}\n```",
                "footer": {
                    "text": f"崩溃时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                }
            }
        ]
    }
    try:
        response = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if response.status_code in [200, 204]:
            print("🚀 Discord 崩溃通知已成功送达！")
        else:
            print(f"❌ 崩溃通知发送失败，状态码: {response.status_code}")
    except requests.RequestException as e:
        print(f"❌ 尝试发送崩溃通知时再次发生异常: {e}")


def wait_until_geetest_finished(page):
    selector = "div.geetest_panel"

    try:
        page.locator(selector).first.wait_for(state="visible", timeout=4000)
        print("🚨 检测到验证码，请输入...")
        
        alarmed = False
        while page.locator(selector).count():
            if not alarmed:
                alarm()
                alarmed = True
            page.wait_for_timeout(1000)

        print("✅ 验证完成，继续运行。")

    except Exception as e:
        if type(e).__name__ != 'TimeoutError':
            print(e)
        pass


def create_context(browser):
    print("正在拉起浏览器..")
    context = browser.new_context()
    page = context.new_page()

    target_trigger_url = CATEGORY_URLS[0]
    page.goto(target_trigger_url)
    return context, page


def clean_room_url(url):
    return url.split("?")[0]


def get_room_id(room_url):
    path = urlparse(room_url).path
    return path.strip("/").split("/")[0]


def alarm():
    winsound.Beep(1200, 800)


def get_hot_rank_rooms():
    api_url = "https://api.live.bilibili.com/xlive/web-interface/v1/index/getHotRankList?web_location=444.7"
    rooms = []
    print("正在获取热门排行榜接口数据...")
    
    try:
        req = urllib.request.Request(
            api_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if res_data.get("code") == 0:
                room_list = res_data.get("data", {}).get("list", [])
                for item in room_list:
                    room_id = item.get("roomid")
                    if room_id:
                        rooms.append(f"https://live.bilibili.com/{room_id}")
                print(f"成功获取热门榜房间数量: {len(rooms)}")
            else:
                print(f"❌ 获取热门榜接口失败, Code: {res_data.get('code')}")
    except Exception as e:
        print(f"❌ 请求热门榜接口异常: {e}")
    return rooms


def get_rooms(page, url):
    print("正在打开分区页面获取列表:", url)
    rooms = []
    try:
        page.goto(url)
        page.locator("#room-card-list").wait_for(timeout=7000)

        for _ in range((ROOM_COUNT - 20) // 20):
            page.mouse.wheel(0, 10)
            page.wait_for_timeout(1500)

        links = page.locator("#room-card-list a[href*='live.bilibili.com/']")

        count = links.count()
        for i in range(min(ROOM_COUNT, count)):
            href = links.nth(i).get_attribute("href")
            if href:
                if href.startswith("//"): 
                    href = "https:" + href
                rooms.append(href)
    except:
        pass      
    print("当前抓取到房间数量:", len(rooms))
    return rooms


def get_room_username(page):
    """获取房间的主播用户名"""
    try:
        owner_element = page.locator(".room-owner-username")
        owner_element.wait_for(state="attached", timeout=2000)
        return (owner_element.text_content()).strip()
    except:
        return "未知主播"


def calculate_anchor_lottery(page, anchor_data, room_id):
    """
    解析 json 里的 anchor (天选抽奖) 字段
    """
    if not anchor_data:
        return

    require_text = anchor_data.get("require_text", "")
    # 如果含有“舰长”或“提督”字样，直接忽略跳过
    if "舰长" in require_text or "提督" in require_text:
        print(f"⏩ 房间 {room_id} 天选包含高门槛({require_text})，跳过")
        return

    award_name = anchor_data.get("award_name", "未知奖品")
    award_num = anchor_data.get("award_num", 1)
    award_price_text = anchor_data.get("award_price_text", "")
    award_per_capita = anchor_data.get("award_per_capita", 1)
    award_goaway_time = anchor_data.get("goaway_time", -1)
    # 从 "价值52电池" 中出数字
    price_match = re.search(r"价值(\d+)电池", award_price_text)
    total_price = int(price_match.group(1)) if price_match else 0

    username = get_room_username(page)


    print(f"🎉 [=== 发现天选抽奖！主播: {username} | 房间: {room_id} ===]")
    gift_line = f"🎁 奖品: {award_name} x {award_num}"
    print(f" {gift_line}")
    print(f" 🔒 参与门槛: {require_text}")
    print(f" 💰 计算价值: {total_price} 电池 (单份数量:{award_per_capita})")
    print(f" 💰 关闭时间: {award_goaway_time // 60} 分钟")
    print("-" * 40)
    # 价值大于 PURPLE_ALERT_THRESHOLD 则报警并发送通知
    if total_price > PURPLE_ALERT_THRESHOLD:
        alarm()
        if IM_SWITCH:
            send_notification(
                username=username,
                room_id=room_id,
                gift_text=gift_line,
                requirement_str=require_text,
                total_price=total_price,
                end_time_str=award_goaway_time
            )


def calculate_red_packets(page, red_packets, room_id):
    """
    解析接口返回的红包 JSON 数据
    """
    username = get_room_username(page)
    print(f"🔥 [=== 发现红包！主播: {username} | 房间: {room_id} ===]")
    gifts_text = ""
    requirement_str = ""
    end_time_str = ""
    
    # 1. 新建 max_total 记录多个红包中的最大价值
    max_total = 0

    for packet in red_packets:
        join_requirement = packet.get("join_requirement")
        req_mapping = {0: "无要求", 1: "需要关注", 2: "需要粉丝勋章", 3: "上舰"}
        requirement_str = req_mapping.get(join_requirement, f"未知门槛({join_requirement})")
        
        end_time_ts = packet.get("end_time")
        if end_time_ts:
            end_time_str = datetime.fromtimestamp(end_time_ts).strftime('%Y-%m-%d %H:%M:%S')
        else:
            end_time_str = "未知"
        
        current_packet_price = (packet.get("total_price", 0)) // 100
        
        # 2. 更新最大价值 max_total
        if current_packet_price > max_total:
            max_total = current_packet_price

        awards = packet.get("awards") or []
        for award in awards:
            gift_line = f"🎁 礼物: {award.get('gift_name')} x {award.get('num')}"
            print(f" {gift_line}")
            gifts_text += f"\n{gift_line}" if gifts_text else gift_line
            
        print(f" 🔒 参与门槛类型: {requirement_str} | 单包价值: {current_packet_price} 电池")
        print(f" 🕒 开奖时间: {end_time_str} 之后")
        print("-" * 40)

    # 3. 改为判断 max_total > RED_ALERT_THRESHOLD，并在通知中传入最大红包价值
    if '小花花' not in gifts_text and max_total > RED_ALERT_THRESHOLD:
        alarm()
        if IM_SWITCH:
            send_notification(username, room_id, gifts_text, requirement_str, max_total, end_time_str)


def scan_room_by_intercept(page, room):
    """
    核心检测函数：红包与天选图标的 Selector 监听
    """
    room_id = get_room_id(room)
    target_url_keyword = "xlive/lottery-interface/v1/lottery/getLotteryInfoWeb"
    # 支持人气红包以及新增的天选抽奖两个 Selector
    packet_icon_selector = ".popularity-red-envelope-entry.gift-left-part, .anchor-lottery-entry.gift-left-part"

    try:
        # 监听红包/抽奖接口
        with page.expect_response(lambda response: target_url_keyword in response.url, timeout=6000) as response_info:
            page.goto(room)
            wait_until_geetest_finished(page)
            page.mouse.wheel(0, 100)

            # 查找红包或天选抽奖图标
            packet_btn = page.locator(packet_icon_selector)
            try:
                packet_btn.first.wait_for(state="attached", timeout=2000)
            except:
                # 2秒内未发现任何抽奖图标则跳过
                return True
        
        # 解析拦截到的响应
        response = response_info.value
        if response.status == 200:
            result = response.json()
            if result.get("code") == 0:
                data = result.get("data", {})
                
                # 1. 只有当 popularity_red_pocket 存在时才处理红包
                red_packets = data.get("popularity_red_pocket")
                if red_packets:
                    calculate_red_packets(page, red_packets, room_id)
                
                # 2. 当 anchor 存在 时，才处理天选抽奖
                anchor_data = data.get("anchor")
                if anchor_data:
                    calculate_anchor_lottery(page, anchor_data, room_id)
            else:
                print(f"❌ 房间 {room_id} 接口被拒 (Code: {result.get('code')}), 提示: {result.get('message')}")
                if result.get("code") in [-352]: 
                    return False
                    
    except Exception as e:
        if type(e).__name__ != 'TimeoutError':
            print(f"扫描房间 {room_id} 出错: {e}")
        pass 
        
    return True


def build_room_urls(room_ids):
    return [f"https://live.bilibili.com/{room_id}" for room_id in room_ids]


def main():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False) 
            context, page = create_context(browser)
            
            while True:
                print("\n--- 开始新一轮全自动监听检测 ---")
                
                # 1. 扫描自选列表
                custom_rooms = build_room_urls(CUSTOM_ROOM_IDS)
                for room in custom_rooms:
                    scan_room_by_intercept(page, room)
                # 2. 扫描热门排行榜列表
                hot_rooms = get_hot_rank_rooms()
                for room in hot_rooms:
                    success = scan_room_by_intercept(page, room)
                    if success != True:
                        return False
                # 3. 扫描分区列表
                for url in CATEGORY_URLS:
                    rooms = get_rooms(page, url)
                    
                    for room in rooms:
                        success = scan_room_by_intercept(page, room)
                        if not success:
                            print("🚨 触发频繁限制 退出")
                            break

                print("一轮扫描结束...")
                
    except Exception as e:
        error_msg = traceback.format_exc()
        print(f"💥 程序发生致命崩溃，正在发送 Discord 通知...\n{error_msg}")
        send_crash_notification(error_msg)
        raise e


if __name__ == "__main__":
    main()
