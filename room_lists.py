# -*- coding: utf-8 -*-
"""收集各直播榜单房间，并生成去重后的综合房间列表。"""

import requests

from auth_manager import USER_AGENT, request_bilibili, sign_wbi


HOT_RANK_API_URL = "https://api.live.bilibili.com/xlive/web-interface/v1/index/getHotRankList"
POPULAR_ANCHOR_RANK_API_URL = "https://api.live.bilibili.com/xlive/general-interface/v1/rank/getPopularAnchorRank"
TOP_FIFTY_PARENT_AREA_IDS = {1, 5}
TOP_FIFTY_MAX_ROOMS = 100
POPULAR_ANCHOR_RANKS = (
    {"area_id": 207, "parent_area_id": 1, "rank_type": 3},
    {"area_id": 530, "parent_area_id": 1, "rank_type": 3},
    {"area_id": 145, "parent_area_id": 1, "rank_type": 3},
    {"area_id": 21, "parent_area_id": 1, "rank_type": 3},
    {"area_id": 1013, "parent_area_id": 1, "rank_type": 3}, #团播
    {"area_id": 0, "parent_area_id": 5, "rank_type": 2},  # 电台
    {"area_id": 0, "parent_area_id": 9, "rank_type": 2},  # 虚拟
    # {"area_id": 0, "parent_area_id": 6, "rank_type": 2},  # 单机
    # {"area_id": 0, "parent_area_id": 2, "rank_type": 2}  # 网游
)


def request_popular_anchor_rank(session, area_id, parent_area_id, rank_type, wbi_keys):
    """请求分区人气榜；保留在 WS 扫描所需的榜单模块内。"""
    img_key, sub_key = wbi_keys
    params = sign_wbi(
        {
            "area_id": area_id,
            "clientType": "2",
            "location_code": "",
            "parent_area_id": parent_area_id,
            "rank_id": "0",
            "rank_type": rank_type,
            "uid": "0",
            "web_location": "445.28",
        },
        img_key,
        sub_key,
    )
    response = request_bilibili(
        session,
        "get",
        POPULAR_ANCHOR_RANK_API_URL,
        params=params,
        headers={"Referer": "https://live.bilibili.com/"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


class RoomListBuilder:
    """将已启用榜单标准化、去重，并输出一份综合房间列表。"""

    def __init__(self, session, wbi_keys, hot_rank_limit):
        self.session = session
        self.wbi_keys = wbi_keys
        self.hot_rank_limit = hot_rank_limit

    def build(self, include_hot_rank, include_popular_ranks):
        """获取已启用来源，并按房间号合并为待扫描的综合列表。"""
        hot_rank_rooms = self.get_hot_rank_rooms() if include_hot_rank else []
        popular_rank_rooms = (
            self.get_popular_anchor_rank_rooms() if include_popular_ranks else []
        )
        return self.merge_unique_rooms(hot_rank_rooms, popular_rank_rooms)

    def get_hot_rank_rooms(self):
        """获取符合筛选规则的人气榜房间，并保留接口返回的主播名。"""
        response = request_bilibili(
            self.session,
            "get",
            HOT_RANK_API_URL,
            params={"web_location": "444.7"},
            headers={"Referer": "https://live.bilibili.com/", "User-Agent": USER_AGENT},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(
                f"获取人气榜失败：{payload.get('code')} {payload.get('message')}"
            )

        rooms = []
        for item in payload.get("data", {}).get("list", []):
            room_id = item.get("roomid")
            try:
                user_num = int(item.get("user_num", 0))
            except (TypeError, ValueError):
                continue
            if room_id and user_num < 300:
                rooms.append(
                    {
                        "room_id": str(room_id),
                        "host_name": item.get("uname") or "未知主播",
                        "anchor_id": self.get_anchor_id(item),
                        "area_names": self.get_area_names(item),
                        "rank_infos": [self.format_rank_info("人气榜", item.get("rank"))],
                    }
                )
            if len(rooms) >= self.hot_rank_limit:
                break
        print(f"成功获取人气榜房间数量：{len(rooms)}")
        return rooms

    def get_popular_anchor_rank_rooms(self):
        """获取指定直播分区人气榜房间，并保留接口返回的主播名。"""
        rooms = []
        successful_rank_count = 0
        for rank_params in POPULAR_ANCHOR_RANKS:
            try:
                payload = request_popular_anchor_rank(
                    self.session, wbi_keys=self.wbi_keys, **rank_params
                )
            except requests.RequestException as error:
                print(f"⚠️ 获取分区人气榜失败：{rank_params}：{error}")
                continue
            if payload.get("code") != 0:
                print(
                    "⚠️ 获取分区人气榜失败："
                    f"{rank_params}：{payload.get('code')} {payload.get('message')}"
                )
                continue

            successful_rank_count += 1
            for index, item in enumerate(payload.get("data", {}).get("list_new") or [], 1):
                # 指定分区按接口返回顺序取前 60 条，不依赖 rank 字段是否存在。
                if (
                    rank_params["parent_area_id"] in TOP_FIFTY_PARENT_AREA_IDS
                    and index > TOP_FIFTY_MAX_ROOMS
                ):
                    break

                room_id = item.get("room_id")
                if not room_id:
                    continue
                host_name = (
                    item.get("uinfo", {}).get("base", {}).get("name") or "未知主播"
                )
                rooms.append(
                    {
                        "room_id": str(room_id),
                        "host_name": host_name,
                        "anchor_id": self.get_anchor_id(item),
                        "area_names": self.get_area_names(item),
                        "rank_infos": [
                            self.format_rank_info("分区榜", item.get("rank"))
                        ],
                    }
                )
        print(
            "📋 分区榜直播间："
            f"{len(rooms)}（已获取 {successful_rank_count}/{len(POPULAR_ANCHOR_RANKS)} 个分区）"
        )
        return rooms

    @staticmethod
    def format_rank_info(rank_name, rank):
        """将接口的数字排名转换为便于终端和通知展示的文字。"""
        return (
            f"{rank_name}第 {rank} 名"
            if rank is not None
            else f"{rank_name}排名未提供"
        )

    @staticmethod
    def get_area_names(item):
        """从不同榜单接口的房间数据中提取可展示的直播分区。"""
        parent_name = item.get("area_v2_parent_name") or item.get("parent_area_name")
        area_name = (
            item.get("area_v2_name")
            or item.get("area_name")
            or item.get("area_name_v2")
        )
        if parent_name and area_name and parent_name != area_name:
            return [f"{parent_name} / {area_name}"]
        return [area_name or parent_name] if area_name or parent_name else []

    @staticmethod
    def get_anchor_id(item):
        """兼容不同榜单接口的字段，提取主播 UID。"""
        base_info = (item.get("uinfo") or {}).get("base") or {}
        for value in (
            item.get("uid"),
            item.get("anchor_uid"),
            item.get("user_id"),
            base_info.get("uid"),
            base_info.get("mid"),
            (item.get("uinfo") or {}).get("uid"),
        ):
            if value is not None and str(value):
                return str(value)
        return None

    @staticmethod
    def merge_unique_rooms(*room_groups):
        """按房间号合并多个榜单，并保留该房间的全部榜单排名。"""
        rooms = []
        rooms_by_id = {}
        for room_group in room_groups:
            for room in room_group:
                room_id = room["room_id"]
                if room_id not in rooms_by_id:
                    rooms_by_id[room_id] = room
                    rooms.append(room)
                    continue

                existing_room = rooms_by_id[room_id]
                if (
                    existing_room["host_name"] == "未知主播"
                    and room["host_name"] != "未知主播"
                ):
                    existing_room["host_name"] = room["host_name"]
                if not existing_room.get("anchor_id") and room.get("anchor_id"):
                    existing_room["anchor_id"] = room["anchor_id"]
                for rank_info in room["rank_infos"]:
                    if rank_info not in existing_room["rank_infos"]:
                        existing_room["rank_infos"].append(rank_info)
                for area_name in room.get("area_names", []):
                    if area_name and area_name not in existing_room.setdefault("area_names", []):
                        existing_room["area_names"].append(area_name)
        return rooms
