import os
import json
import sqlite3
from datetime import datetime
from typing import Dict, Any, List, Tuple
from pathlib import Path

import httpx
from mdlm_config import db_path, load_env

# -------------------------
# env (optional)
# -------------------------
load_env(override=True)

SQLITE_DB_PATH = str(db_path())
TOP_N = int(os.getenv("SYNC_TOP_N", "100"))  # 默认 100

# -------------------------
# NetEase toplist mapping
# -------------------------
# 常见榜单对应的歌单ID（业内常用映射）
# 热歌榜 3778678，新歌榜 3779629，飙升榜 19723756
# 来源：多处整理文章/项目中使用的榜单ID映射。 [oai_citation:3‡腾讯云](https://cloud.tencent.com/developer/article/1543945?utm_source=chatgpt.com)
NETEASE_TOPLISTS: List[Tuple[str, int]] = [
    ("热歌榜", 3778678),
    ("新歌榜", 3779629),
    ("飙升榜", 19723756),
]

PLATFORM_NAME = "网易云音乐"

# -------------------------
# HTTP
# -------------------------
http = httpx.Client(
    timeout=30.0,
    headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Referer": "https://music.163.com/",
    }
)

def fetch_netease_playlist_detail(playlist_id: int) -> Dict[str, Any]:
    """
    说明：使用常见的 playlist detail 接口抓取榜单歌单详情。
    该接口可用性可能随平台策略变化；若返回异常，再做备选方案。
     [oai_citation:4‡腾讯云](https://cloud.tencent.com/developer/article/1543945?utm_source=chatgpt.com)
    """
    url = "https://music.163.com/api/playlist/detail"
    r = http.get(url, params={"id": str(playlist_id)})
    r.raise_for_status()
    return r.json()

# -------------------------
# DB helpers
# -------------------------
def ensure_platform(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO platform(name) VALUES (?)", (name,))
    conn.commit()
    row = conn.execute("SELECT id FROM platform WHERE name=?", (name,)).fetchone()
    return int(row[0])

def ensure_chart(conn: sqlite3.Connection, platform_id: int, chart_name: str, source_url: str) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO chart(platform_id, name, category, update_freq, source_url) VALUES (?, ?, ?, ?, ?)",
        (platform_id, chart_name, "toplist", "daily", source_url),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM chart WHERE platform_id=? AND name=?",
        (platform_id, chart_name),
    ).fetchone()
    return int(row[0])

def insert_snapshot(conn: sqlite3.Connection, chart_id: int, top_n: int, raw_payload: Dict[str, Any]) -> int:
    captured_at = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO chart_snapshot(chart_id, captured_at, top_n, raw_payload) VALUES (?, ?, ?, ?)",
        (chart_id, captured_at, top_n, json.dumps(raw_payload, ensure_ascii=False)),
    )
    conn.commit()
    return int(cur.lastrowid)

def insert_entries(
    conn: sqlite3.Connection,
    snapshot_id: int,
    entries: List[Tuple[int, str, str, str]],
) -> None:
    """
    entries: (rank, track_platform_id, track_name, artist_name_raw)
    """
    conn.executemany(
        "INSERT INTO chart_entry(snapshot_id, rank, track_platform_id, track_name, artist_name_raw, score, extra_metrics) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(snapshot_id, rank, tid, name, artist, None, None) for (rank, tid, name, artist) in entries],
    )
    conn.commit()

def parse_tracks(payload: Dict[str, Any], top_n: int) -> List[Tuple[int, str, str, str]]:
    """
    兼容 playlist/detail 常见返回：payload['result']['tracks']
    """
    result = payload.get("result") or payload.get("playlist") or {}
    tracks = result.get("tracks") or []
    out: List[Tuple[int, str, str, str]] = []
    for idx, t in enumerate(tracks[:top_n], start=1):
        tid = str(t.get("id", ""))
        name = t.get("name") or ""
        # artists 字段可能在 ar / artists
        ars = t.get("ar") or t.get("artists") or []
        artist = "/".join([a.get("name", "") for a in ars if isinstance(a, dict)]) or ""
        out.append((idx, tid, name, artist))
    return out

def main():
    db_path = Path(SQLITE_DB_PATH).resolve()
    if not db_path.exists():
        raise SystemExit(f"❌ 找不到 SQLite: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        platform_id = ensure_platform(conn, PLATFORM_NAME)

        for chart_name, playlist_id in NETEASE_TOPLISTS:
            source_url = f"https://music.163.com/#/discover/toplist?id={playlist_id}"
            chart_id = ensure_chart(conn, platform_id, chart_name, source_url)

            payload = fetch_netease_playlist_detail(playlist_id)
            entries = parse_tracks(payload, TOP_N)

            if not entries:
                print(f"⚠️ {PLATFORM_NAME}-{chart_name} 没抓到 tracks，跳过")
                continue

            snapshot_id = insert_snapshot(conn, chart_id, TOP_N, payload)
            insert_entries(conn, snapshot_id, entries)

            print(f"✅ 入库完成：{PLATFORM_NAME} - {chart_name} Top{len(entries)} (snapshot_id={snapshot_id})")

    finally:
        conn.close()
        http.close()

if __name__ == "__main__":
    main()