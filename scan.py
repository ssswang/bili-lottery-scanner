# -*- coding: utf-8 -*-
# ==============================================================================
# Gemini & Chatgpt & 我
# 基于 Playwright 框架，实现红包检测与提醒。
# ==============================================================================

import time
import os
import json
import winsound
from datetime import datetime
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

# 自定义监听的房间号
CUSTOM_ROOM_IDS = [
    "2233",
]

# 监听的分区链接
CATEGORY_URLS = [
    "https://live.bilibili.com/p/eden/area-tags?areaId=0&parentAreaId=1",     # 娱乐区
    "https://live.bilibili.com/p/eden/area-tags?areaId=744&parentAreaId=9",   # V歌势
    "https://live.bilibili.com/p/eden/area-tags?&areaId=819&parentAreaId=14", # 厅
]

# 每个分区最多抓取的房间数
ROOM_COUNT = 60

def wait_until_geetest_finished(page):
    """
    检测并等待验证码完成
    如果在5秒内出现验证码，会发出蜂鸣声警报，并循环等待用户手动完成验证
    """
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

def get_rooms(page, url):
    """
    打开分区页面，模拟向下滚动鼠标以加载更多房间，并提取指定数量的直播间链接
    """
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
    """
    解析接口返回的红包JSON数据，提取并打印主播名、门槛、礼物详情以及准确的开奖时间
    """
    if not data:
        return
    red_packets = data.get("popularity_red_pocket")
    
    if red_packets:
        # 提取主播名
        try:
            owner_element = page.locator(".room-owner-username")
            owner_element.wait_for(state="attached", timeout=2000)
            username = owner_element.text_content().strip()
        except:
            username = "未知主播"

        print(f"🔥 [=== 发现红包！主播: {username} | 房间: {room_id} ===]")
        alarm()
        
        for packet in red_packets:
            # 门槛转换
            join_requirement = packet.get("join_requirement")
            req_mapping = {0: "无要求", 1: "需要关注", 2: "需要粉丝牌", 3: "上舰"}
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
                print(f" 🎁 礼物: {award.get('gift_name')} x {award.get('num')}")
                
            print(f" 🔒 参与门槛类型: {requirement_str} | 总价值: {total_price} 电池")
            print(f" 🕒 开奖时间: {end_time_str} 之后")
        print("-" * 40)

def scan_room_by_intercept(page, room):
    """
    核心检测函数：进入直播间，定位红包图标，
    触发JS加载，拦截并解析 getLotteryInfoWeb 接口数据。
    """
    room_id = get_room_id(room)
    target_url_keyword = "xlive/lottery-interface/v1/lottery/getLotteryInfoWeb"
    packet_icon_selector = ".popularity-red-envelope-entry.gift-left-part" # 天选：".anchor-lottery-entry .gift-left-part"
    print(room_id, 'スキャンしま～す...')

    try:
        # 监听红包接口
        with page.expect_response(lambda response: target_url_keyword in response.url, timeout=6000) as response_info:
            page.goto(room)
            wait_until_geetest_finished(page)

            # 查找红包图标并悬停触发
            packet_btn = page.locator(packet_icon_selector)
            try:
                packet_btn.wait_for(state="visible", timeout=2000)
                packet_btn.hover()
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
            print(e)
        pass 
        
    return True

def build_room_urls(room_ids):
    """
    根据传入的纯数字房间号列表，拼接构建成完整的 bilibili 直播间 URL 列表
    """
    return [f"https://live.bilibili.com/{room_id}" for room_id in room_ids]

def main():
    """
    程序主入口，初始化 Playwright 浏览器，
    无限循环依次扫描“自选列表”和“分区列表”，并在每轮结束后休眠指定时间
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False) 
        context, page = create_context(browser)
        
        while True:
            print("\n--- 开始新一轮全自动监听检测 ---")
            
            # 1. 扫描自选列表
            custom_rooms = build_room_urls(CUSTOM_ROOM_IDS)
            for room in custom_rooms:
                success = scan_room_by_intercept(page, room)

            # 2. 扫描分区列表
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
