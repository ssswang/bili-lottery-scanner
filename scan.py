# -*- coding: utf-8 -*-
# ==============================================================================
# 项目名称: B站直播红包&天选抽奖检测脚本，最好有VPN搭配使用
# 联合作者: Gemini & 我
# ==============================================================================
import json
import os
import re
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests
import winsound
from playwright.sync_api import sync_playwright


def load_external_config():
    """Load KEY=VALUE entries from config.txt"""
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
BLACKLIST_ROOM_IDS = get_list_config(CONFIG, "BLACKLIST_ROOM_IDS", "[]")
CATEGORY_URLS = get_list_config(CONFIG, "CATEGORY_URLS", '["https://live.bilibili.com/p/eden/area-tags?areaId=0&parentAreaId=1", "https://live.bilibili.com/p/eden/area-tags?&areaId=190&parentAreaId=5"]')
ROOM_COUNT = get_int_config(CONFIG, "ROOM_COUNT", 40)
IM_SWITCH = get_int_config(CONFIG, "IM_SWITCH", 0)
DISCORD_WEBHOOK = CONFIG.get("DISCORD_WEBHOOK", "")
RED_ALERT_AVG_THRESHOLD = get_int_config(CONFIG, "RED_ALERT_AVG_THRESHOLD", 3)
PURPLE_ALERT_THRESHOLD = get_int_config(CONFIG, "PURPLE_ALERT_THRESHOLD", 9)
BEEP_SWITCH = get_int_config(CONFIG, "BEEP_SWITCH", 1)
PURPLE_SCAN_SWITCH= get_int_config(CONFIG, "PURPLE_SCAN_SWITCH", 1)
RED_SCAN_SWITCH= get_int_config(CONFIG, "RED_SCAN_SWITCH", 1)
def send_lottery_notification(
    username, room_id, gift_text, requirement_str, total_price, end_time_str
):
    """装配discord通知"""
    payload = {
        "embeds": [
            {
                "title": f"🔥 红包/抽奖预警！主播: {username} ({room_id})",
                "url": f"https://live.bilibili.com/{room_id}",
                "color": 16729221,  # B站标志性粉色 (#FF6699)
                "fields": [
                    {
                        "name": "👤 主播信息",
                        "value": (
                            f"**昵称**: {username}\n**房间**:"
                            f" [{room_id}](https://live.bilibili.com/{room_id})"
                        ),
                        "inline": False,
                    },
                    {"name": "房号", "value": str(room_id), "inline": False},
                    {
                        "name": "🎁 包含礼物/奖品",
                        "value": gift_text,
                        "inline": False,
                    },
                    {
                        "name": "🔑 参与门槛",
                        "value": f"**{requirement_str}**",
                        "inline": True,
                    },
                    {
                        "name": "💰 最大包价值",
                        "value": f"**{total_price}** 电池",
                        "inline": True,
                    },
                    {
                        "name": "🕒 开奖时间/倒计时",
                        "value": f"{end_time_str}",
                        "inline": False,
                    },
                ],
                "footer": {"text": "B站直播红包/天选抽奖监控"},
            }
        ]
    }
    return post_discord(payload)


def send_crash_notification(error_msg):
    """程序崩溃 alert"""
    print(f"💥 程序发生崩溃，正在发送 Discord 通知...\n{error_msg}")
    payload = {
        "embeds": [
            {
                "title": "🚨 脚本警报！",
                "color": 16711680,  # 红色警告 (#FF0000)
                "description": (
                    "监控脚本已停止运行，请及时检查服务器或本地环境。"
                ),
                "footer": {
                    "text": (
                        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                },
            }
        ]
    }
    return post_discord(payload)


def send_interaction_notification(msg):
    """提示需要交互的信息"""
    print(msg)
    payload = {
        "embeds": [
            {
                "title": "🚨 脚本警报！",
                "color": 16711680,  # 警告 (#FF0000)
                "description": f"{msg}\n",
                "footer": {
                    "text": (
                        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                },
            }
        ]
    }
    return post_discord(payload)


def post_discord(payload):
    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK is not configured; skipping notification.")
        return
    try:
        response = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if response.status_code in [200, 204]:
            print("🚀 Discord 通知已成功送达！")
        else:
            print(f"❌ 通知发送失败，状态码: {response.status_code}")
    except requests.RequestException as e:
        print(f"❌ 尝试发送通知时再次发生异常: {e}")

    return


def wait_until_geetest_finished(page):
    selector = "div.geetest_panel"
    max_wait_seconds = 300  # 5 分钟超时限制

    try:
        page.locator(selector).first.wait_for(state="attached", timeout=7000)
        send_interaction_notification("🚨 检测到验证码，请输入...")

        alarmed = False
        start_time = time.time()

        while page.locator(selector).count():
            elapsed_time = time.time() - start_time
            if elapsed_time > max_wait_seconds:
                send_interaction_notification(
                    "❌ 验证码等待超时，程序退出。"
                )
                raise TimeoutError("验证码等待超时，超过 5 分钟未完成输入。")

            if not alarmed:
                alarm()
                alarmed = True

            page.wait_for_timeout(1000)

        send_interaction_notification("✅ 验证完成，继续运行。")

    except Exception as e:
        if isinstance(e, TimeoutError) and "超过 5 分钟" in str(e):
            raise e
        elif type(e).__name__ != "TimeoutError":
            print(f"验证码检测流程出现异常: {e}")

 
def detect_login(page):
    """弹出登录框即判定为触发了 352 风控"""
    selector = "div.login-scan-wp"
    max_wait_seconds = 300  # 5 分钟超时限制
    
 
    if not page.locator(selector).count():
        return False
    else:
        send_interaction_notification("🚨 触发 352 风控（弹出强制登录框），请登录")
 
        alarmed = False
        start_time = time.time()
        try:
            while page.locator(selector).count():
                elapsed_time = time.time() - start_time
                if elapsed_time > max_wait_seconds:
                    send_interaction_notification(
                        "❌ 验证码等待超时，程序退出。"
                    )
                    raise TimeoutError("验证码等待超时，超过 5 分钟未完成输入。")
 
                if not alarmed:
                    alarm()
                    alarmed = True
 
                page.wait_for_timeout(1000)
            
        except Exception as e:
            if isinstance(e, TimeoutError) and "超过 5 分钟" in str(e):
                raise e
            elif type(e).__name__ != "TimeoutError":
                print(f"验证码检测流程出现异常: {e}")  
    return False

    
def detect_login_and_quite(page):
    """弹出登录框即判定为触发了 352 风控"""
    selector = "div.login-scan-wp"
    if page.locator(selector).count():
        alarm()
        send_interaction_notification("🚨 触发 352 风控（弹出强制登录框）")
        sys.exit("退出")
    return False

def detect_vip_stream(page):
    """判定大航海直播"""
    selector = "div.live-charge-panel"
    if page.locator(selector).count():
        return True
        
    return False  
    
def create_context(browser):
    print("正在拉起浏览器..")
    context = browser.new_context()
    page = context.new_page()

    target_trigger_url = "https://live.bilibili.com"
    page.goto(target_trigger_url)

    return context, page


def clean_room_url(url):
    return url.split("?")[0]


def get_room_id(room_url):
    path = urlparse(room_url).path
    return path.strip("/").split("/")[0]


def alarm():
    if BEEP_SWITCH:
        winsound.Beep(1200, 800)


def get_hot_rank_rooms():
    """获取热门排行榜接口数据"""
    api_url = "https://api.live.bilibili.com/xlive/web-interface/v1/index/getHotRankList?web_location=444.7"
    rooms = []

    try:
        req = urllib.request.Request(
            api_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if res_data.get("code") == 0:
                room_list = res_data.get("data", {}).get("list", [])
                for item in room_list:
                    user_num = item.get("user_num", 0)
                    room_id = item.get("roomid")
                    if user_num < 300 and room_id:
                        rooms.append(f"https://live.bilibili.com/{room_id}")
                print(f"成功获取热门榜房间数量: {len(rooms)}")
            else:
                print(f"❌ 获取热门榜接口失败, Code: {res_data.get('code')}")
    except Exception as e:
        send_interaction_notification(f"❌ 请求热门榜接口异常: {e}")
    return rooms


def get_rooms(page, url):
    print("正在打开分区页面获取列表:", url)
    rooms = []
    try:
        page.goto(url)
        wait_until_geetest_finished(page)
        page.locator("#room-card-list").wait_for(timeout=7000)

        for _ in range((ROOM_COUNT - 20) // 20):
            page.mouse.wheel(0, 100)
            page.wait_for_timeout(1500)

        links = page.locator("#room-card-list a[href*='live.bilibili.com/']")

        count = links.count()
        for i in range(min(ROOM_COUNT, count)):
            href = links.nth(i).get_attribute("href")
            if href:
                if href.startswith("//"):
                    href = "https:" + href
                rooms.append(href)
    except Exception:
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
    """解析 json 里的 anchor (天选抽奖) 字段"""
    if not anchor_data:
        return

    require_text = anchor_data.get("require_text", "")
    if "舰长" in require_text or "提督" in require_text:
        print(f"⏩ 房间 {room_id} 天选包含高门槛({require_text})，跳过")
        return

    award_name = anchor_data.get("award_name", "未知奖品")
    award_num = anchor_data.get("award_num", 1)
    award_price_text = anchor_data.get("award_price_text", "")
    award_per_capita = anchor_data.get("award_per_capita", 1)
    award_goaway_time = anchor_data.get("time", -1)

    price_match = re.search(r"价值(\d+)电池", award_price_text)
    total_price = int(price_match.group(1)) if price_match else 0
    gift_line = f"🟪 奖品: {award_name} 最大中奖人数: {award_num}"
    username = get_room_username(page)

    now = datetime.now()
    seconds_to_add = int(award_goaway_time)
    new_datetime = now + timedelta(seconds=seconds_to_add)
    formatted_new_datetime = new_datetime.strftime("%Y-%m-%d %H:%M:%S")

    if total_price > PURPLE_ALERT_THRESHOLD and award_goaway_time > 20:
        print(f"🎉 发现天选抽奖！主播: {username} | 房间: {room_id} ")
        print(f" {gift_line}")
        print(f" 🔒 参与门槛: {require_text}")
        print(f" 💰 计算价值: {total_price} 电池 (单份数量:{award_per_capita})")
        print(f" 🕒 关闭时间: {formatted_new_datetime}")
        print("-" * 40)

    if total_price > PURPLE_ALERT_THRESHOLD and award_goaway_time > 20:
        alarm()
        if IM_SWITCH:
            send_lottery_notification(
                username,
                room_id,
                gift_line,
                require_text,
                total_price,
                formatted_new_datetime,
            )


def calculate_red_packets(page, red_packets, room_id):
    """解析接口返回的红包 JSON 数据"""
    username = get_room_username(page)

    gifts_text = ""
    requirement_str = ""
    end_time_str = ""

    max_total = 0
    max_avg = 0.0

    req_mapping = {
        0: "无要求",
        1: "需要关注",
        2: "需要粉丝勋章",
        3: "上舰",
    }

    for packet in red_packets:
        join_requirement = packet.get("join_requirement")
        current_requirement_str = req_mapping.get(
            join_requirement, f"未知门槛({join_requirement})"
        )

        end_time_ts = packet.get("end_time")
        if end_time_ts:
            current_end_time_str = datetime.fromtimestamp(end_time_ts).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        else:
            current_end_time_str = "未知"

        current_packet_price = (packet.get("total_price", 0)) // 100

        if current_packet_price > max_total:
            max_total = current_packet_price

        packet_item_count = 0
        awards = packet.get("awards") or []
        for award in awards:
            num = award.get("num", 0)
            gift_line = (
                f" 🎁 礼物: {award.get('gift_name')} 最大中奖人数: {num}"
            )
            gifts_text += f"\n{gift_line}" if gifts_text else gift_line
            packet_item_count += num

        if packet_item_count > 0:
            current_packet_avg = current_packet_price / packet_item_count
        else:
            current_packet_avg = 0.0

        # 门槛/开奖时间跟随触发报警的那个指标（包均价）联动，
        # 而不是简单取循环里最后一个红包的数据
        if current_packet_avg >= max_avg:
            max_avg = current_packet_avg
            requirement_str = current_requirement_str
            end_time_str = current_end_time_str

    print(
        f"🎉 主播: {username} | 房间: {room_id} | 最大包价值: {max_total} 电池 |"
        f" 包均: {max_avg:.2f} 电池/人"
    )

    if max_avg > RED_ALERT_AVG_THRESHOLD:
        alarm()
        print(f"{gifts_text}")
        print(f" 🔒 参与门槛: {requirement_str}")
        print(f" 🕒 开奖时间: {end_time_str} 之后")
        print("-" * 40)
        if IM_SWITCH:
            send_lottery_notification(
                username,
                room_id,
                gifts_text,
                requirement_str,
                max_total,
                end_time_str,
            )


def scan_room_by_intercept(page, room):
    room_id = get_room_id(room)
    if room_id in BLACKLIST_ROOM_IDS:
        return True
    target_url_keyword = "xlive/lottery-interface/v1/lottery/getLotteryInfoWeb"
    selectors = []
    if RED_SCAN_SWITCH:
        selectors.append(".popularity-red-envelope-entry.gift-left-part")
    if PURPLE_SCAN_SWITCH:
        selectors.append(".anchor-lottery-entry.gift-left-part")

    packet_icon_selector = ", ".join(selectors)

    captured_json = [None]
    is_success = True

    def handle_response(response):
        if target_url_keyword in response.url and response.status == 200:
            try:
                captured_json[0] = response.json()
            except Exception:
                pass

    page.on("response", handle_response)

    try:
        page.goto(room)
        # 1. 验证码
        wait_until_geetest_finished(page)
        # 2. 大航海
        if detect_vip_stream(page):
            return True
        # 3. 弹出登录框
        detect_login(page)

        heat = page.locator(".heat-index-scroll-item")
        if heat.count():
            heat.first.click()
        page.mouse.wheel(0, 9)
        # 4. UI 图标判断：先等 1.5 秒看看页面有没有红包/天选图标
        packet_btn = page.locator(packet_icon_selector)
        try:
            packet_btn.first.wait_for(state="attached", timeout=1500)
        except Exception:
            # 没图标说明是空房间，直接返回 True 快速扫下一个
            return True

        # 5. 确认有图标后，轮询最多 2 秒等待 JSON 结果返回
        for _ in range(4):
            if captured_json[0] is not None:
                break
            page.wait_for_timeout(500)

        # 6. 解析 JSON
        result = captured_json[0]
        if result:
            code = result.get("code")  
            if code == 0:
                data = result.get("data", {})

                red_packets = data.get("popularity_red_pocket")
                if RED_SCAN_SWITCH and red_packets:
                    calculate_red_packets(page, red_packets, room_id)

                anchor_data = data.get("anchor")
                if PURPLE_SCAN_SWITCH and anchor_data:
                    calculate_anchor_lottery(page, anchor_data, room_id)
            else:
                print(f"⚠️ 房间 {room_id} 接口被拒, Code: {code}")
                if code in [-352]:
                    send_interaction_notification(
                        f"🚨 房间 {room_id} 触发 -352 频繁限制！"
                    )
                    is_success = False

    except Exception as e:
        if isinstance(e, TimeoutError) and "超过 5 分钟" in str(e):
            raise e
        elif type(e).__name__ != "TimeoutError":
            print(f"扫描房间 {room_id} 出错: {e}")

    finally:
        page.remove_listener("response", handle_response)

    return is_success


def build_room_urls(room_ids):
    return [f"https://live.bilibili.com/{room_id}" for room_id in room_ids]


def main():
    if not RED_SCAN_SWITCH and not PURPLE_SCAN_SWITCH:
        sys.exit("需要至少监控一种红包")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context, page = create_context(browser)
            page.mouse.wheel(0, 400)
            while True:
                print("\n--- 开始新一轮全自动监听检测 ---")

                # 2. 扫描热门排行榜列表
                hot_rooms = get_hot_rank_rooms()
                for room in hot_rooms:
                    scan_room_by_intercept(page, room)

                # 3. 扫描分区列表
                for url in CATEGORY_URLS:
                    rooms = get_rooms(page, url)
                    for room in rooms:
                        scan_room_by_intercept(page, room)
                        
                idle = 300
                print("一轮扫描结束...休息", idle)
                time.sleep(idle) 

    except Exception as e:
        error_msg = traceback.format_exc()
        send_crash_notification(error_msg)
        raise e


if __name__ == "__main__":
    main()