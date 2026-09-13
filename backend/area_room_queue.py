# -*- coding: utf-8 -*-
"""通过直播父分区 getRoomList 接口构建全量待监听房间队列。"""

import time

import requests

from backend.auth.api_auth import USER_AGENT, request_bilibili


AREA_ROOM_LIST_URL = "https://api.live.bilibili.com/room/v3/area/getRoomList"
PARENT_AREA_IDS = (1, 5, 9)
PAGE_SIZE = 99


def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}")


class AreaRoomQueueBuilder:
    """分页读取指定父分区的全部开播房间，并按房间号去重。"""

    def __init__(self, session, parent_area_ids=PARENT_AREA_IDS, page_size=PAGE_SIZE):
        self.session = session
        self.parent_area_ids = tuple(parent_area_ids)
        self.page_size = page_size

    def build(self):
        rooms_by_id = {}
        totals = {}
        for parent_area_id in self.parent_area_ids:
            rooms = self.get_parent_area_rooms(parent_area_id)
            totals[parent_area_id] = len(rooms)
            for room in rooms:
                existing = rooms_by_id.get(room["room_id"])
                if existing is None:
                    rooms_by_id[room["room_id"]] = room
                    continue
                self._merge_room(existing, room)
        rooms = list(rooms_by_id.values())
        summary = "，".join(f"{area_id}区 {count}" for area_id, count in totals.items())
        log(f"📋 父分区房间：{len(rooms)}（{summary}，去重后）")
        return rooms

    def get_parent_area_rooms(self, parent_area_id):
        rooms = []
        page = 1
        while True:
            try:
                response = request_bilibili(
                    self.session,
                    "get",
                    AREA_ROOM_LIST_URL,
                    params={
                        "platform": "web",
                        "page": page,
                        "page_size": self.page_size,
                        "parent_area_id": parent_area_id,
                        "cate_id": 0,
                        "area_id": 0,
                        "sort_type": "online",
                        "tag_version": 1,
                    },
                    headers={"Referer": "https://live.bilibili.com/", "User-Agent": USER_AGENT},
                    timeout=15,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as error:
                raise RuntimeError(f"获取父分区 {parent_area_id} 第 {page} 页失败：{error}") from error

            if payload.get("code") != 0:
                raise RuntimeError(
                    f"获取父分区 {parent_area_id} 第 {page} 页失败："
                    f"{payload.get('code')} {payload.get('message', payload.get('msg', '未知错误'))}"
                )
            data = payload.get("data") or {}
            items = data.get("list") or []
            if not isinstance(items, list):
                raise RuntimeError(f"父分区 {parent_area_id} 第 {page} 页未返回房间列表")
            for item in items:
                if isinstance(item, dict):
                    room = self.normalize_room(item, parent_area_id)
                    if room is not None:
                        rooms.append(room)
            # 该接口的 has_more 在不同版本中可能缺失。缺失时以满页继续、
            # 不满 page_size 停止，避免把第一页误判为最后一页。
            has_more = data.get("has_more")
            has_more_is_false = has_more is False or has_more in (0, "0", "false", "False")
            if not items or has_more_is_false or (has_more is None and len(items) < self.page_size):
                break
            page += 1
        return rooms

    @staticmethod
    def normalize_room(item, requested_parent_area_id):
        room_id = item.get("roomid") or item.get("room_id")
        if room_id is None or not str(room_id):
            return None
        anchor = item.get("anchor_info") or {}
        base_info = anchor.get("base_info") or {}
        host_name = (
            item.get("uname")
            or item.get("anchor_name")
            or base_info.get("uname")
            or base_info.get("name")
            or "未知主播"
        )
        anchor_id = item.get("uid") or item.get("anchor_uid") or base_info.get("uid")
        parent_name = item.get("area_v2_parent_name") or item.get("parent_area_name")
        area_name = item.get("area_v2_name") or item.get("area_name")
        if parent_name and area_name and parent_name != area_name:
            area_names = [f"{parent_name} / {area_name}"]
        else:
            area_names = [area_name or parent_name or f"父分区 {requested_parent_area_id}"]
        return {
            "room_id": str(room_id),
            "host_name": str(host_name),
            "anchor_id": str(anchor_id) if anchor_id is not None and str(anchor_id) else None,
            "area_names": area_names,
            "rank_infos": [],
        }

    @staticmethod
    def _merge_room(existing, incoming):
        if existing["host_name"] == "未知主播" and incoming["host_name"] != "未知主播":
            existing["host_name"] = incoming["host_name"]
        if not existing.get("anchor_id") and incoming.get("anchor_id"):
            existing["anchor_id"] = incoming["anchor_id"]
        for area_name in incoming.get("area_names", []):
            if area_name and area_name not in existing["area_names"]:
                existing["area_names"].append(area_name)
