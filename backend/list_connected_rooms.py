# -*- coding: utf-8 -*-
"""读取扫描器状态快照，按直播间号查询当前连接状态。"""

import argparse
import json
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "red_packet_monitor.db"


def load_active_rooms(database_path):
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        row = connection.execute(
            "SELECT status_json, updated_at FROM scanner_status WHERE status_id = 1"
        ).fetchone()
    finally:
        connection.close()
    if not row:
        raise RuntimeError("尚未找到扫描器状态快照；请先启动新版扫描器并等待状态更新。")
    try:
        status = json.loads(row[0])
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"扫描器状态快照无法解析：{error}") from error
    rooms = status.get("active_rooms")
    if not isinstance(rooms, list):
        raise RuntimeError("当前快照不含已连接房间列表；请重启新版扫描器并等待状态更新。")
    return row[1], rooms


def parse_args():
    parser = argparse.ArgumentParser(description="按直播间号查询扫描器是否实际连接")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("room_ids", nargs="+", help="要查询的一个或多个直播间号")
    args = parser.parse_args()
    return args


def main():
    args = parse_args()
    try:
        updated_at, rooms = load_active_rooms(Path(args.database).resolve())
    except (RuntimeError, sqlite3.Error) as error:
        raise SystemExit(error) from error
    print(f"状态快照：{updated_at} | 实际已连接房间 {len(rooms)} 个")
    rooms_by_id = {str(room.get("room_id")): room for room in rooms if room.get("room_id") is not None}
    for room_id in dict.fromkeys(str(value) for value in args.room_ids):
        room = rooms_by_id.get(room_id)
        if room is None:
            print(f"❌ 房间 {room_id}：当前不在实际连接列表中")
            continue
        host_name = room.get("host_name") or "未知主播"
        area_info = room.get("area_info") or "未知分区"
        print(f"✅ 房间 {room_id}：当前已连接 | {host_name} | {area_info}")


if __name__ == "__main__":
    main()
