# -*- coding: utf-8 -*-
"""红包 WS 监视器的本地 SQLite 存储。"""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path


class LocalDatabase:
    """保存红包资料及可复用的 WebSocket 鉴权缓存，不保存登录 Cookie。"""

    def __init__(self, database_path=None):
        path = Path(database_path) if database_path else Path(__file__).with_name(
            "red_packet_monitor.db"
        )
        self.path = path
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        self._ensure_column("rooms", "anchor_id", "TEXT")
        self._ensure_column("anchors", "anchor_id", "TEXT")
        self._ensure_column("red_packets", "is_battery_lottery", "INTEGER NOT NULL DEFAULT 1")

    def _create_tables(self):
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    room_id TEXT PRIMARY KEY,
                    host_name TEXT NOT NULL,
                    anchor_id TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS anchors (
                    room_id TEXT PRIMARY KEY,
                    anchor_id TEXT,
                    anchor_name TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    FOREIGN KEY (room_id) REFERENCES rooms(room_id)
                );

                CREATE TABLE IF NOT EXISTS senders (
                    sender_uid TEXT PRIMARY KEY,
                    sender_name TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS red_packets (
                    room_id TEXT NOT NULL,
                    lot_id TEXT NOT NULL,
                    sender_uid TEXT,
                    sender_name TEXT NOT NULL DEFAULT '',
                    total_price INTEGER NOT NULL DEFAULT 0,
                    award_count INTEGER NOT NULL DEFAULT 0,
                    average_value REAL NOT NULL DEFAULT 0,
                    join_requirement INTEGER,
                    start_time INTEGER,
                    end_time INTEGER,
                    is_battery_lottery INTEGER NOT NULL DEFAULT 1,
                    recorded_at TEXT NOT NULL,
                    raw_data_json TEXT NOT NULL,
                    PRIMARY KEY (room_id, lot_id),
                    FOREIGN KEY (room_id) REFERENCES rooms(room_id),
                    FOREIGN KEY (sender_uid) REFERENCES senders(sender_uid)
                );

                CREATE TABLE IF NOT EXISTS ws_auth_cache (
                    account_name TEXT NOT NULL,
                    room_id TEXT NOT NULL,
                    token TEXT NOT NULL,
                    hosts_json TEXT NOT NULL,
                    expires_at INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_name, room_id)
                );
                """
            )

    def _ensure_column(self, table_name, column_name, definition):
        """为已有数据库补充新列，不删除任何历史数据。"""
        columns = {
            row[1]
            for row in self._connection.execute(f"PRAGMA table_info({table_name})")
        }
        if column_name not in columns:
            with self._connection:
                self._connection.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
                )

    @staticmethod
    def _is_battery_lottery(data):
        """仅奖品全部为 gift_id=0 的电池红包才标记为 1。"""
        awards = data.get("awards") or []
        if not isinstance(awards, list) or not awards:
            return 0
        for award in awards:
            if not isinstance(award, dict):
                return 0
            try:
                if int(award.get("gift_id")) != 0:
                    return 0
            except (TypeError, ValueError):
                return 0
        return 1

    def load_ws_auth_cache(self, account_name, room_id):
        """读取持久保存的房间 WS 鉴权信息；仅丢弃损坏记录。"""
        account_name = str(account_name)
        room_id = str(room_id)
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT token, hosts_json
                FROM ws_auth_cache
                WHERE account_name = ? AND room_id = ?
                """,
                (account_name, room_id),
            ).fetchone()
            if not row:
                return None
            token, hosts_json = row
            try:
                hosts = json.loads(hosts_json)
            except (TypeError, ValueError):
                hosts = None
            if not isinstance(token, str) or not token or not isinstance(hosts, list) or not hosts:
                self._connection.execute(
                    "DELETE FROM ws_auth_cache WHERE account_name = ? AND room_id = ?",
                    (account_name, room_id),
                )
                return None
            return token, hosts

    def save_ws_auth_cache(self, account_name, room_id, token, hosts):
        """持久保存 WS token；服务器拒绝时由监视器主动清除。"""
        account_name = str(account_name)
        room_id = str(room_id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        hosts_json = json.dumps(hosts, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO ws_auth_cache (
                    account_name, room_id, token, hosts_json, expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_name, room_id) DO UPDATE SET
                    token=excluded.token,
                    hosts_json=excluded.hosts_json,
                    expires_at=excluded.expires_at,
                    updated_at=excluded.updated_at
                """,
                (account_name, room_id, token, hosts_json, 0, now),
            )

    def delete_ws_auth_cache(self, account_name, room_id):
        """在服务器拒绝鉴权时删除对应缓存。"""
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM ws_auth_cache WHERE account_name = ? AND room_id = ?",
                (str(account_name), str(room_id)),
            )

    def save_red_packet(self, room, command, average_value):
        """将一个 POPULARITY_RED_POCKET_START 事件做幂等写入。"""
        data = command.get("data") or {}
        room_id = str(room["room_id"])
        lot_id = str(data.get("lot_id") or data.get("red_packet_id") or "")
        if not lot_id:
            return False

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        host_name = room.get("host_name") or "未知主播"
        anchor_id = room.get("anchor_id")
        anchor_id = str(anchor_id) if anchor_id is not None else None
        sender_uid = data.get("sender_uid") or data.get("uid")
        sender_uid = str(sender_uid) if sender_uid is not None else None
        sender_name = data.get("sender_name") or data.get("uname") or "未知"
        awards = data.get("awards") or []
        is_battery_lottery = self._is_battery_lottery(data)
        award_count = 0
        for award in awards:
            if not isinstance(award, dict):
                continue
            try:
                award_count += int(award.get("num", 0))
            except (TypeError, ValueError):
                continue
        try:
            total_price = int(data.get("total_price", 0)) // 100
        except (TypeError, ValueError):
            total_price = 0

        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO rooms (room_id, host_name, anchor_id, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(room_id) DO UPDATE SET
                    host_name=excluded.host_name,
                    anchor_id=excluded.anchor_id,
                    last_seen_at=excluded.last_seen_at
                """,
                (room_id, host_name, anchor_id, now, now),
            )
            self._connection.execute(
                """
                INSERT INTO anchors (room_id, anchor_id, anchor_name, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(room_id) DO UPDATE SET
                    anchor_id=excluded.anchor_id,
                    anchor_name=excluded.anchor_name,
                    last_seen_at=excluded.last_seen_at
                """,
                (room_id, anchor_id, host_name, now),
            )
            if sender_uid:
                self._connection.execute(
                    """
                    INSERT INTO senders (sender_uid, sender_name, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(sender_uid) DO UPDATE SET
                        sender_name=excluded.sender_name,
                        last_seen_at=excluded.last_seen_at
                    """,
                    (sender_uid, sender_name, now, now),
                )
            self._connection.execute(
                """
                INSERT INTO red_packets (
                    room_id, lot_id, sender_uid, sender_name, total_price, award_count,
                    average_value, join_requirement, start_time, end_time, recorded_at,
                    raw_data_json, is_battery_lottery
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(room_id, lot_id) DO UPDATE SET
                    sender_uid=excluded.sender_uid,
                    sender_name=excluded.sender_name,
                    total_price=excluded.total_price,
                    award_count=excluded.award_count,
                    average_value=excluded.average_value,
                    join_requirement=excluded.join_requirement,
                    start_time=excluded.start_time,
                    end_time=excluded.end_time,
                    recorded_at=excluded.recorded_at,
                    raw_data_json=excluded.raw_data_json
                """,
                (
                    room_id, lot_id, sender_uid, sender_name, total_price, award_count,
                    average_value, data.get("join_requirement"), data.get("start_time"),
                    data.get("end_time"), now,
                    json.dumps(data, ensure_ascii=False, separators=(",", ":")), is_battery_lottery,
                ),
            )
        return True

    def close(self):
        with self._lock:
            self._connection.close()
