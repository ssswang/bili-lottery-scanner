# -*- coding: utf-8 -*-
# ==============================================================================
# 项目名称: B站直播红包检测脚本
# 联合作者: Gemini & 我
# ==============================================================================

import time
import os
import json
import winsound
import requests
import traceback  
import urllib.request
from datetime import datetime
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

# 自定义监听的房间号

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
CUSTOM_ROOM_IDS = get_list_config(CONFIG, "CUSTOM_ROOM_IDS", "[]")
CATEGORY_URLS = get_list_config(CONFIG, "CATEGORY_URLS", '["https://live.bilibili.com/p/eden/area-tags?areaId=0&parentAreaId=1", "https://live.bilibili.com/p/eden/area-tags?&areaId=190&parentAreaId=5"]')
ROOM_COUNT = get_int_config(CONFIG, "ROOM_COUNT", 40)
IM_SWITCH = get_int_config(CONFIG, "IM_SWITCH", 0)
DISCORD_WEBHOOK = CONFIG.get("DISCORD_WEBHOOK", "")
ALERT_THRESHOLD = get_int_config(CONFIG, "ALERT_THRESHOLD", 40)
def send_notification(username, room_id, gift_text, requirement_str, total_price, end_time_str):
    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK is not configured; skipping notification.")
        return
    """
    发送通知
    """
    payload = {
        "embeds": [
            {
                "title": f"🔥 红包预警！主播: {username} ({room_id})",
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
                        "value": room_id, 
                        "inline": False
                    },
                    {
                        "name": "🎁 包含礼物",
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
                        "name": "🕒 开奖时间",
                        "value": f"{end_time_str} 之后",
                        "inline": False
                    }
                ],
                "footer": {
                    "text": "B站直播红包监控"
                }
            }
        ]
    }
    

    try:
        response = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if response.status_code in [200, 204]:
            print("🚀 Discord 通知发送成功！")
        else:
            print(f"❌ Webhook 发送失败，状态码: {response.status_code}")
    except requests.RequestException as e:
        print(f"❌ 发送通知时出现异常: {e}")


def send_crash_notification(error_msg):
    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK is not configured; skipping crash notification.")
        return
    """
    新增：当程序发生未捕获异常崩溃时，发送警报至 Discord
    """
    payload = {
        "embeds": [
            {
                "title": "🚨 脚本崩溃警报！",
                "color": 16711680,  # 红色警告 (#FF0000)
                "description": f"监控脚本已停止运行，请及时检查服务器或本地环境。\n\n**错误堆栈信息:**\n```python\n{error_msg[-1800:]}\n```", # 限制字数防止超出Discord单个field限制
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
    """
    检测并等待验证码完成
    如果在5秒内出现验证码，会发出蜂鸣声警报，并循环等待用户手动完成验证
    """
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
    """
    初始化浏览器上下文并打开初始分区页面
    """
    print("正在拉起浏览器..")
    context = browser.new_context()
    page = context.new_page()

    target_trigger_url = CATEGORY_URLS[0]
    page.goto(target_trigger_url)
    return context, page


def clean_room_url(url):
    """
    清理直播间URL，去除携带的查询参数
    """
    return url.split("?")[0]


def get_room_id(room_url):
    """
    从直播间的 URL 地址中提取出纯数字房间号
    """
    path = urlparse(room_url).path
    return path.strip("/").split("/")[0]


def alarm():
    """
    播放特定频率和时长的系统蜂鸣声，用于声音提醒
    """
    winsound.Beep(1200, 800)

def get_hot_rank_rooms():
    """
    请求热门排行榜接口，提取 JSON 中的 roomid 并构建直播间 URL 列表
    """
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
    """
    打开分区页面，模拟向下滚动鼠标以加载更多房间，并提取指定数量的直播间链接
    """
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


def calculate_red_packets(page, data, room_id):
    """
    解析接口返回的红包JSON数据，提取并打印主播名、门槛、礼物详情以及准确的开奖时间，并触发邮件发送
    """
    if not data:
        return
    red_packets = data.get("popularity_red_pocket")
    
    if red_packets:
        # 提取主播名
        try:
            owner_element = page.locator(".room-owner-username")
            owner_element.wait_for(state="attached", timeout=2000)
            username = (owner_element.text_content()).strip()
        except:
            username = "未知主播"

        print(f"🔥 [=== 发现红包！主播: {username} | 房间: {room_id} ===]")
        gifts_text = ""
        total_price = 0
        for packet in red_packets:
            # 门槛转换
            join_requirement = packet.get("join_requirement")
            req_mapping = {0: "无要求", 1: "需要关注", 2: "需要粉丝勋章", 3: "上舰"}
            requirement_str = req_mapping.get(join_requirement, f"未知门槛({join_requirement})")
            
            # 时间戳转换
            end_time_ts = packet.get("end_time")
            if end_time_ts:
                end_time_str = datetime.fromtimestamp(end_time_ts).strftime('%Y-%m-%d %H:%M:%S')
            else:
                end_time_str = "未知"
            
            # 兼容处理空数据
            total_price = (packet.get("total_price", 0)) // 100
            awards = packet.get("awards") or []
            
            
            for award in awards:
                gift_line = f"🎁 礼物: {award.get('gift_name')} x {award.get('num')}"
                print(f" {gift_line}")
                gifts_text += f"\n{gift_line}" if gifts_text else gift_line
                
            print(f" 🔒 参与门槛类型: {requirement_str} | 总价值: {total_price} 电池")
            print(f" 🕒 开奖时间: {end_time_str} 之后")
            print("-" * 40)
            

        if '小花花' not in gifts_text and total_price > ALERT_THRESHOLD:
            alarm()
            if IM_SWITCH:
                send_notification(username, room_id, gifts_text, requirement_str, total_price, end_time_str)


def scan_room_by_intercept(page, room):
    """
    核心检测函数：进入直播间，定位红包图标，
    触发B端JS加载，拦截并解析 getLotteryInfoWeb 接口数据。
    """
    room_id = get_room_id(room)
    target_url_keyword = "xlive/lottery-interface/v1/lottery/getLotteryInfoWeb"
    packet_icon_selector = ".popularity-red-envelope-entry.gift-left-part"

    try:
        # 监听红包接口
        with page.expect_response(lambda response: target_url_keyword in response.url, timeout=6000) as response_info:
            page.goto(room)
            wait_until_geetest_finished(page)
            page.mouse.wheel(0, 100)
            # owner_element = page.locator(".room-owner-username")
            # owner_element.wait_for(state="attached", timeout=1000)
            # username = (owner_element.text_content()).strip()
            # print(username)
            # 查找红包图标
            packet_btn = page.locator(packet_icon_selector)
            try:
                packet_btn.wait_for(state="attached", timeout=2000)
                
            except:
                # 2秒内未发现红包图标则直接跳过
                return True
        
        # 解析拦截到的响应
        response = response_info.value
        if response.status == 200:
            result = response.json()
            if result.get("code") == 0:
                data = result.get("data")
                calculate_red_packets(page, data, room_id)
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
    """
    根据传入的纯数字房间号列表，拼接构建成完整的直播间 URL 列表
    """
    return [f"https://live.bilibili.com/{room_id}" for room_id in room_ids]


def main():
    """
    程序主入口，初始化 Playwright 浏览器，
    无限循环依次扫描“自选列表”和“分区列表”，并在每轮结束后休眠指定时间
    """
    # 修改：将整个主逻辑包裹在 try...except 中以捕获未预期的严重崩溃
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
        # 获取完整的报错文本
        error_msg = traceback.format_exc()
        print(f"💥 程序发生致命崩溃，正在发送 Discord 通知...\n{error_msg}")
        # 触发崩溃通知
        send_crash_notification(error_msg)
        # 保持原本抛出异常的行为，方便在终端查看
        raise e


if __name__ == "__main__":
    main()
