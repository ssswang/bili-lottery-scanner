import time
import os
import json
import winsound
from datetime import datetime
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright


CUSTOM_ROOM_IDS = [
    "2233",
]
CATEGORY_URLS = [
    "https://live.bilibili.com/p/eden/area-tags?areaId=0&parentAreaId=1",  # 娱乐区
    "https://live.bilibili.com/p/eden/area-tags?areaId=744&parentAreaId=9",  # V歌势
    "https://live.bilibili.com/p/eden/area-tags?&areaId=819&parentAreaId=14", # 厅
]

ROOM_COUNT = 60

def wait_until_geetest_finished(page):
    selector = "div.geetest_panel"

    try:
        page.locator(selector).first.wait_for(state="visible", timeout=5000)

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

def get_rooms(page, url):
    print("正在打开分区页面获取列表:", url)
    page.goto(url)
    page.locator("#room-card-list").wait_for(timeout=5000)

    for _ in range((ROOM_COUNT - 20) // 20):
        page.mouse.wheel(0, 1000)
        page.wait_for_timeout(1500)

    links = page.locator("#room-card-list a[href*='live.bilibili.com/']")
    rooms = []
    for i in range(min(ROOM_COUNT, links.count())):
        href = links.nth(i).get_attribute("href")
        if href:
            if href.startswith("//"): href = "https:" + href
            rooms.append(href)
    print("当前抓取到房间数量:", len(rooms))
    return rooms

def calculate_red_packets(page, data, room_id):
    if not data:
        return
    red_packets = data.get("popularity_red_pocket")
    if red_packets:
        try:
            owner_element = page.locator(".room-owner-username")
            owner_element.wait_for(state="attached", timeout=2000)
            username = owner_element.text_content().strip()
        except:
            username = "未知主播"

        print(f"🔥 [=== 发现红包！主播: {username} | 房间: {room_id} ===]")
        alarm()
        for packet in red_packets:
            join_requirement = packet.get("join_requirement")
            req_mapping = {0: "无要求", 1: "需要关注", 2: "需要粉丝勋章", 3: "上舰"}
            requirement_str = req_mapping.get(join_requirement, f"未知门槛({join_requirement})")
            # --- 🕒 新增：解析并转换开奖时间 ---
            end_time_ts = packet.get("end_time")
            if end_time_ts:
                end_time_str = datetime.fromtimestamp(end_time_ts).strftime('%Y-%m-%d %H:%M:%S')
            else:
                end_time_str = "未知"
            
            # 兼容处理某些没有 total_price 字段或 awards 为 null 的特殊情况
            total_price = (packet.get("total_price", 0)) // 100
            awards = packet.get("awards") or []
            
            for award in awards:
                print(f" 🎁 礼物: {award.get('gift_name')} x {award.get('num')}")
                
            # 打印包含开奖时间的信息
            print(f" 🔒 参与门槛类型: {requirement_str} | 总价值: {total_price} 电池")
            print(f" 🕒 开奖时间: {end_time_str} 之后")
        print("-" * 40)
def check_red_packet(page):
    try:
        page.locator(".popularity-red-envelope-entry.gift-left-part").wait_for(
            state="visible",
            timeout=3000
        )
        return True
    except:
        return False
def check_purple_lottery(page):
    try:
        page.locator(".anchor-lottery-entry .gift-left-part").wait_for(
            state="visible",
            timeout=3000
        )
        return True
    except:
        return False

def scan_room_by_intercept(page, room):
    room_id = get_room_id(room)
    target_url_keyword = "xlive/lottery-interface/v1/lottery/getLotteryInfoWeb"
    packet_icon_selector = ".popularity-red-envelope-entry.gift-left-part"
    print(room_id, 'スキャンしま～す')

    try:
        # 启动拦截器，超时延长到 6 秒
        with page.expect_response(lambda response: target_url_keyword in response.url, timeout=6000) as response_info:
            page.goto(room)
            wait_until_geetest_finished(page)
            scr = page.locator(".fullscreen-container-paddingbox")
            scr.hover()
            # 检查页面上有没有出现红包图标
            
            packet_btn = page.locator(packet_icon_selector)
            try:
                packet_btn.wait_for(state="visible", timeout=2000)
                packet_btn.hover()
            except:
                # 2秒内没有红包图标，直接返回True，进入下一个房间
                return True
        
        # 成功截获请求
        response = response_info.value
        if response.status == 200:
            result = response.json()
            if result.get("code") == 0:
                data = result.get("data")
                # print(data)
                calculate_red_packets(page, data, room_id)
            else:
                print(f"❌ 房间 {room_id} getLotteryInfoWeb请求被拒 (Code: {result.get('code')}), 提示: {result.get('message')}")
                if result.get("code") in [-352]: 
                    return False
                    
    except Exception as e:
        if type(e).__name__ != 'TimeoutError':
            print(e)
        pass 
    return True

def build_room_urls(room_ids):
    return [f"https://live.bilibili.com/{room_id}" for room_id in room_ids]

def main():
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False) 
        context, page = create_context(browser)
        
        while True:
            print("\n--- 开始新一轮全自动监听检测 ---")
            
            # 1. 遍历自选列表
            custom_rooms = build_room_urls(CUSTOM_ROOM_IDS)
            for room in custom_rooms:
                success = scan_room_by_intercept(page, room)


            # 2. 遍历分区列表
            for url in CATEGORY_URLS:
                rooms = get_rooms(page, url)
                
                for room in rooms:
                    success = scan_room_by_intercept(page, room)
                    if success != True:
                        return False


            print("一轮扫描结束，休息 60 秒...")
            time.sleep(60)

if __name__ == "__main__":
    main()
