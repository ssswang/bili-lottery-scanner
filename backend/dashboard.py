# -*- coding: utf-8 -*-
"""本地红包队列仪表盘：读取 SQLite 数据库，并提供近实时 JSON 接口。"""

import argparse
import json
import sqlite3
import webbrowser
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "red_packet_monitor.db"
WEB_ROOT = PROJECT_ROOT / "web"


def requirement_text(value):
    return {0: "无要求", 1: "需要关注", 2: "需要粉丝勋章", 3: "需要上舰"}.get(value, "未知")


def format_awards(raw_data):
    try:
        awards = json.loads(raw_data).get("awards") or []
    except (TypeError, json.JSONDecodeError):
        return "奖品信息未提供"
    items = [
        f"{item.get('gift_name', '未知礼物')} × {item.get('num', 0)}"
        for item in awards
        if isinstance(item, dict)
    ]
    return "、".join(items) or "奖品信息未提供"


def read_red_packets(database_path, limit):
    now = int(datetime.now().timestamp())
    cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    if not database_path.is_file():
        return {"now": now, "active": [], "expired": [], "error": "尚未找到数据库；请先启动扫描器。"}
    try:
        connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT p.room_id, p.lot_id, p.sender_name, p.total_price, p.award_count,
                       p.average_value, p.join_requirement, p.start_time, p.end_time,
                       p.recorded_at, p.raw_data_json, COALESCE(r.host_name, '') AS host_name
                FROM red_packets AS p
                LEFT JOIN rooms AS r ON r.room_id = p.room_id
                WHERE p.recorded_at >= ?
                ORDER BY p.end_time DESC, p.recorded_at DESC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return {"now": now, "active": [], "expired": [], "error": f"读取数据库失败：{error}"}

    active, expired = [], []
    for row in rows:
        packet = dict(row)
        packet.pop("raw_data_json", None)
        packet["awards"] = format_awards(row["raw_data_json"])
        packet["requirement"] = requirement_text(packet.pop("join_requirement", None))
        # 数据库中的 total_price 已在写入时从分换算为电池，页面不能重复除以 100。
        packet["total_price"] = max(0, int(packet["total_price"] or 0))
        packet["end_time"] = int(packet["end_time"] or 0)
        (active if packet["end_time"] > now else expired).append(packet)
    active.sort(key=lambda item: item["end_time"])
    expired.sort(key=lambda item: item["end_time"], reverse=True)
    return {"now": now, "active": active, "expired": expired, "error": None}


class DashboardHandler(SimpleHTTPRequestHandler):
    database_path = DEFAULT_DATABASE
    packet_limit = 300

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def do_GET(self):
        if urlparse(self.path).path != "/api/red-packets":
            return super().do_GET()
        payload = json.dumps(read_red_packets(self.database_path, self.packet_limit), ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def parse_args():
    parser = argparse.ArgumentParser(description="本地红包近实时队列仪表盘")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite 数据库路径")
    parser.add_argument("--port", type=int, default=8765, help="本地网页端口，默认 8765")
    parser.add_argument("--limit", type=int, default=300, help="最多读取的红包数量，默认 300")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.limit < 1:
        parser.error("端口或数量上限无效")
    return args


def main():
    args = parse_args()
    DashboardHandler.database_path = Path(args.database).resolve()
    DashboardHandler.packet_limit = args.limit
    address = f"http://127.0.0.1:{args.port}"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    print(f"红包仪表盘已启动：{address}（每 2 秒自动刷新，按 Ctrl+C 停止）")
    if not args.no_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n红包仪表盘已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
