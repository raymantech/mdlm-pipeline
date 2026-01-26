import os
import sys
import json
import time
import random
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

# 确保时区处理模块在路径中
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from mdlm_config import db_path, load_env
from timezone_utils import beijing_now_iso, beijing_today_iso

# =========================================================
# ENV
# =========================================================
load_env(override=True)

SQLITE_DB_PATH = str(db_path())
SYNC_TOP_N = int(os.getenv("SYNC_TOP_N", "100"))

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
QQ_COOKIE = os.getenv("QQ_COOKIE", "").strip()

FETCH_TIMEOUT = float(os.getenv("FETCH_TIMEOUT", "20"))
FETCH_RETRY = int(os.getenv("FETCH_RETRY", "3"))
FETCH_SLEEP = float(os.getenv("FETCH_SLEEP", "1.2"))

PLATFORM_NAME = "QQ音乐"

QQ_CHARTS: List[Tuple[str, int]] = [
    ("热歌榜", 26),
    ("新歌榜", 27),
    ("飙升榜", 4),
]


def now_iso() -> str:
    """使用北京时间"""
    return beijing_now_iso()


def db_connect() -> sqlite3.Connection:
    db_path = Path(SQLITE_DB_PATH).resolve()
    if not db_path.exists():
        raise SystemExit(f"❌ 找不到 SQLite 数据库：{db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return [r[1] for r in rows]


def get_platform_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM platform WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute("INSERT INTO platform(name) VALUES (?)", (name,))
    conn.commit()
    return int(cur.lastrowid)


def get_chart_id(conn: sqlite3.Connection, platform_id: int, chart_name: str) -> int:
    row = conn.execute(
        "SELECT id FROM chart WHERE platform_id = ? AND name = ?",
        (platform_id, chart_name),
    ).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO chart(platform_id, name) VALUES (?, ?)",
        (platform_id, chart_name),
    )
    conn.commit()
    return int(cur.lastrowid)


def insert_snapshot(
    conn: sqlite3.Connection,
    chart_id: int,
    captured_at: str,
    top_n: int,
    raw_obj: Optional[Dict[str, Any]] = None
) -> int:
    """
    chart_snapshot schema evolves. Insert only existing columns.
    Your current schema has NOT NULL top_n, so we must fill it if present.
    """
    cols = table_columns(conn, "chart_snapshot")
    payload: Dict[str, Any] = {}

    if "chart_id" in cols:
        payload["chart_id"] = chart_id
    if "captured_at" in cols:
        payload["captured_at"] = captured_at

    # ✅ important: top_n NOT NULL in your DB
    if "top_n" in cols:
        payload["top_n"] = int(top_n)

    # optional raw
    raw_text = json.dumps(raw_obj, ensure_ascii=False) if isinstance(raw_obj, dict) else None
    if raw_text:
        if "raw" in cols:
            payload["raw"] = raw_text
        elif "raw_json" in cols:
            payload["raw_json"] = raw_text
        elif "meta_json" in cols:
            payload["meta_json"] = raw_text

    if not payload:
        raise SystemExit("❌ chart_snapshot 没有可写入的列（至少应有 chart_id/captured_at/top_n 之一）")

    keys = ",".join(payload.keys())
    qs = ",".join(["?"] * len(payload))
    cur = conn.execute(f"INSERT INTO chart_snapshot({keys}) VALUES ({qs})", tuple(payload.values()))
    conn.commit()
    return int(cur.lastrowid)


def insert_entries(conn: sqlite3.Connection, snapshot_id: int, songs: List[Dict[str, Any]]) -> None:
    cols = table_columns(conn, "chart_entry")

    required = {"snapshot_id", "rank", "track_platform_id", "track_name"}
    missing_required = [c for c in required if c not in cols]
    if missing_required:
        raise SystemExit(f"❌ chart_entry 缺少必要字段：{missing_required}")

    has_artist = "artist_name_raw" in cols
    has_score = "score" in cols
    has_extra = "extra_metrics" in cols

    for s in songs:
        payload: Dict[str, Any] = {
            "snapshot_id": int(snapshot_id),
            "rank": int(s["rank"]),
            "track_platform_id": str(s["track_platform_id"]),
            "track_name": str(s["track_name"]).strip(),
        }

        if has_artist:
            payload["artist_name_raw"] = (s.get("artist_name_raw") or "").strip()

        if has_score and isinstance(s.get("score"), (int, float)):
            payload["score"] = float(s["score"])

        if has_extra and isinstance(s.get("extra_metrics"), dict):
            payload["extra_metrics"] = json.dumps(s["extra_metrics"], ensure_ascii=False)

        keys = ",".join(payload.keys())
        qs = ",".join(["?"] * len(payload))
        conn.execute(f"INSERT INTO chart_entry({keys}) VALUES ({qs})", tuple(payload.values()))

    conn.commit()


def delete_today_chart_snapshot(conn: sqlite3.Connection, chart_id: int, day: str) -> None:
    cols = table_columns(conn, "chart_snapshot")
    if "captured_at" not in cols:
        return

    snap_rows = conn.execute(
        "SELECT id FROM chart_snapshot WHERE chart_id=? AND substr(captured_at,1,10)=?",
        (chart_id, day),
    ).fetchall()
    snap_ids = [int(r[0]) for r in snap_rows]
    if not snap_ids:
        return

    conn.executemany("DELETE FROM chart_entry WHERE snapshot_id = ?", [(sid,) for sid in snap_ids])
    conn.executemany("DELETE FROM chart_snapshot WHERE id = ?", [(sid,) for sid in snap_ids])
    conn.commit()


def fetch_qq_toplist(topid: int, top_n: int) -> Dict[str, Any]:
    url = "https://c.y.qq.com/v8/fcg-bin/fcg_v8_toplist_cp.fcg"
    params = {
        "topid": str(topid),
        "format": "json",
        "inCharset": "utf-8",
        "outCharset": "utf-8",
        "notice": "0",
        "platform": "h5",
        "needNewCode": "1",
        "tpl": "3",
        "page": "detail",
        "type": "top",
        "song_begin": "0",
        "song_num": str(top_n),
    }

    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://y.qq.com/",
        "Origin": "https://y.qq.com",
        "Accept": "application/json, text/plain, */*",
    }
    if QQ_COOKIE:
        headers["Cookie"] = QQ_COOKIE

    last_err = None
    for _ in range(FETCH_RETRY):
        try:
            with httpx.Client(timeout=FETCH_TIMEOUT, headers=headers) as client:
                r = client.get(url, params=params)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                    time.sleep(FETCH_SLEEP)
                    continue
                return r.json()
        except Exception as e:
            last_err = repr(e)
            time.sleep(FETCH_SLEEP)

    raise RuntimeError(f"QQ fetch failed: {last_err}")


def parse_qq_songs(payload: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    songlist = payload.get("songlist") or []
    out: List[Dict[str, Any]] = []
    rank = 0

    for item in songlist:
        data = item.get("data") if isinstance(item, dict) else None
        if not isinstance(data, dict):
            continue

        rank += 1
        if rank > limit:
            break

        songmid = (data.get("songmid") or data.get("mid") or "").strip()
        songid = data.get("songid")
        track_platform_id = f"qq:{songmid or songid or rank}"

        songname = (data.get("songname") or data.get("name") or "").strip()

        singers = data.get("singer") or []
        names: List[str] = []
        if isinstance(singers, list):
            for s in singers:
                if isinstance(s, dict):
                    n = (s.get("name") or "").strip()
                    if n:
                        names.append(n)
        artist = " / ".join(names)

        if not songname:
            continue

        out.append({
            "rank": rank,
            "track_platform_id": track_platform_id,
            "track_name": songname,
            "artist_name_raw": artist,
            "extra_metrics": {"qq_topid": payload.get("topid")},
        })

    if not out:
        raise RuntimeError("QQ parse failed: empty song rows")
    return out


def ingest_qq(top_n: int = 100) -> None:
    conn = db_connect()
    try:
        platform_id = get_platform_id(conn, PLATFORM_NAME)
        today = beijing_today_iso()  # 使用北京时间

        for chart_name, topid in QQ_CHARTS:
            chart_id = get_chart_id(conn, platform_id, chart_name)

            delete_today_chart_snapshot(conn, chart_id, today)

            payload = fetch_qq_toplist(topid, top_n=top_n)
            songs = parse_qq_songs(payload, limit=top_n)

            snapshot_id = insert_snapshot(conn, chart_id, now_iso(), top_n=top_n, raw_obj=payload)
            insert_entries(conn, snapshot_id, songs)

            print(f"✅ 入库完成：{PLATFORM_NAME} - {chart_name} Top{top_n} (snapshot_id={snapshot_id})")
            time.sleep(FETCH_SLEEP + random.uniform(0, 0.35))

    finally:
        conn.close()


if __name__ == "__main__":
    ingest_qq(top_n=int(os.getenv("SYNC_TOP_N", "100")))