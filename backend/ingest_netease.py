#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云音乐榜单抓取脚本

独立模块，不与其他平台代码混合
支持的榜单：热歌榜、新歌榜、飙升榜、原创榜
"""

import os
import sys
import json
import time
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

FETCH_TIMEOUT = float(os.getenv("FETCH_TIMEOUT", "20"))
FETCH_RETRY = int(os.getenv("FETCH_RETRY", "3"))
FETCH_SLEEP = float(os.getenv("FETCH_SLEEP", "1.5"))

PLATFORM_NAME = "网易云音乐"

# 网易云榜单 ID 映射
# 参考: https://music.163.com/#/discover/toplist
NETEASE_CHARTS: List[Tuple[str, int]] = [
    ("热歌榜", 3778678),      # 云音乐热歌榜
    ("新歌榜", 3779629),      # 云音乐新歌榜
    ("飙升榜", 19723756),     # 云音乐飙升榜
    ("原创榜", 2884035),      # 网易原创歌曲榜
]


def now_iso() -> str:
    """使用北京时间"""
    return beijing_now_iso()


def db_connect() -> sqlite3.Connection:
    db_file = Path(SQLITE_DB_PATH).resolve()
    if not db_file.exists():
        raise SystemExit(f"❌ 找不到 SQLite 数据库：{db_file}")
    conn = sqlite3.connect(str(db_file))
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
    """插入快照记录，兼容不同的表结构"""
    cols = table_columns(conn, "chart_snapshot")
    payload: Dict[str, Any] = {}

    if "chart_id" in cols:
        payload["chart_id"] = chart_id
    if "captured_at" in cols:
        payload["captured_at"] = captured_at
    if "top_n" in cols:
        payload["top_n"] = int(top_n)

    # raw json 列名兼容
    raw_text = json.dumps(raw_obj, ensure_ascii=False) if isinstance(raw_obj, dict) else None
    if raw_text:
        for col in ["raw", "raw_json", "meta_json"]:
            if col in cols:
                payload[col] = raw_text
                break

    if not payload:
        raise SystemExit("❌ chart_snapshot 没有可写入的列")

    keys = ",".join(payload.keys())
    qs = ",".join(["?"] * len(payload))
    cur = conn.execute(f"INSERT INTO chart_snapshot({keys}) VALUES ({qs})", tuple(payload.values()))
    conn.commit()
    return int(cur.lastrowid)


def insert_entries(conn: sqlite3.Connection, snapshot_id: int, songs: List[Dict[str, Any]]) -> None:
    """插入榜单条目"""
    cols = table_columns(conn, "chart_entry")

    required = {"snapshot_id", "rank", "track_platform_id", "track_name"}
    missing_required = [c for c in required if c not in cols]
    if missing_required:
        raise SystemExit(f"❌ chart_entry 缺少必要字段：{missing_required}")

    has_artist = "artist_name_raw" in cols
    has_heat = "heat" in cols
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

        if has_heat and s.get("heat") is not None:
            payload["heat"] = float(s["heat"])

        if has_extra and isinstance(s.get("extra_metrics"), dict):
            payload["extra_metrics"] = json.dumps(s["extra_metrics"], ensure_ascii=False)

        keys = ",".join(payload.keys())
        qs = ",".join(["?"] * len(payload))
        conn.execute(f"INSERT INTO chart_entry({keys}) VALUES ({qs})", tuple(payload.values()))

    conn.commit()


def delete_today_chart_snapshot(conn: sqlite3.Connection, chart_id: int, day: str) -> None:
    """删除今天已有的快照（避免重复）"""
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


def fetch_netease_playlist(playlist_id: int, limit: int = 100) -> Dict[str, Any]:
    """
    获取网易云音乐榜单数据
    
    使用网易云音乐 Web API
    """
    # 方法1: 使用 playlist/detail 接口
    url = "https://music.163.com/api/playlist/detail"
    params = {
        "id": str(playlist_id),
        "n": str(limit),
    }
    
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://music.163.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    last_err = None
    for attempt in range(FETCH_RETRY):
        try:
            with httpx.Client(timeout=FETCH_TIMEOUT, headers=headers, follow_redirects=True) as client:
                r = client.get(url, params=params)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                    time.sleep(FETCH_SLEEP * (attempt + 1))
                    continue
                
                data = r.json()
                if data.get("code") != 200:
                    last_err = f"API error: {data.get('code')} - {data.get('msg', '')}"
                    time.sleep(FETCH_SLEEP * (attempt + 1))
                    continue
                
                return data
        except Exception as e:
            last_err = repr(e)
            time.sleep(FETCH_SLEEP * (attempt + 1))

    raise RuntimeError(f"网易云 fetch failed (playlist_id={playlist_id}): {last_err}")


def parse_netease_songs(payload: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    """解析网易云返回的歌曲数据"""
    result = payload.get("result") or payload.get("playlist") or {}
    tracks = result.get("tracks") or []
    
    if not tracks:
        raise RuntimeError("网易云 parse failed: no tracks found")
    
    out: List[Dict[str, Any]] = []
    
    for idx, track in enumerate(tracks[:limit]):
        if not isinstance(track, dict):
            continue
        
        rank = idx + 1
        
        # 歌曲信息
        song_id = track.get("id")
        song_name = (track.get("name") or "").strip()
        
        if not song_name:
            continue
        
        # 艺人信息
        artists = track.get("artists") or track.get("ar") or []
        artist_names = []
        for ar in artists:
            if isinstance(ar, dict):
                name = (ar.get("name") or "").strip()
                if name:
                    artist_names.append(name)
        artist = " / ".join(artist_names)
        
        # 专辑信息（可选）
        album = track.get("album") or track.get("al") or {}
        album_name = (album.get("name") or "").strip() if isinstance(album, dict) else ""
        
        # 热度/播放量
        popularity = track.get("popularity") or track.get("pop") or 0
        
        track_platform_id = f"netease:{song_id}" if song_id else f"netease:{rank}"
        
        out.append({
            "rank": rank,
            "track_platform_id": track_platform_id,
            "track_name": song_name,
            "artist_name_raw": artist,
            "heat": popularity,
            "extra_metrics": {
                "song_id": song_id,
                "album": album_name,
                "popularity": popularity,
            },
        })
    
    if not out:
        raise RuntimeError("网易云 parse failed: no valid songs")
    
    return out


def ingest_netease(top_n: int = 100) -> None:
    """
    抓取网易云音乐榜单数据并入库
    """
    conn = db_connect()
    try:
        platform_id = get_platform_id(conn, PLATFORM_NAME)
        today = beijing_today_iso()
        
        print(f"[netease] Starting ingest for {PLATFORM_NAME}, date: {today}")

        success_count = 0
        for chart_name, playlist_id in NETEASE_CHARTS:
            try:
                chart_id = get_chart_id(conn, platform_id, chart_name)
                
                # 删除今天已有的数据（避免重复）
                delete_today_chart_snapshot(conn, chart_id, today)
                
                # 获取数据
                payload = fetch_netease_playlist(playlist_id, limit=top_n)
                songs = parse_netease_songs(payload, limit=top_n)
                
                # 入库
                snapshot_id = insert_snapshot(conn, chart_id, now_iso(), top_n=top_n, raw_obj=payload)
                insert_entries(conn, snapshot_id, songs)
                
                print(f"✅ 入库完成：{PLATFORM_NAME} - {chart_name} Top{len(songs)} (snapshot_id={snapshot_id})")
                success_count += 1
                
                time.sleep(FETCH_SLEEP)
                
            except Exception as e:
                print(f"⚠️ {PLATFORM_NAME} - {chart_name} 抓取失败: {e}")
                continue
        
        if success_count == 0:
            print(f"❌ {PLATFORM_NAME} 所有榜单抓取失败")
        else:
            print(f"[netease] Completed: {success_count}/{len(NETEASE_CHARTS)} charts")

    finally:
        conn.close()


if __name__ == "__main__":
    ingest_netease(top_n=int(os.getenv("SYNC_TOP_N", "100")))
