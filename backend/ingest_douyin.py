#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
汽水音乐（抖音）榜单抓取脚本

独立模块，支持抖音/汽水音乐热门榜单
"""

import os
import sys
import json
import time
import sqlite3
import re
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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 抖音 Cookie（可选，有助于获取更多数据）
# 设置方法：登录抖音网页版，复制 Cookie
DOUYIN_COOKIE = os.getenv("DOUYIN_COOKIE", "").strip()

FETCH_TIMEOUT = float(os.getenv("DOUYIN_FETCH_TIMEOUT", "10"))
FETCH_RETRY = int(os.getenv("DOUYIN_FETCH_RETRY", "2"))
FETCH_SLEEP = float(os.getenv("DOUYIN_FETCH_SLEEP", "1.0"))

# 是否启用抖音抓取（可以通过环境变量禁用）
DOUYIN_ENABLED = os.getenv("DOUYIN_ENABLED", "true").lower() in ("true", "1", "yes")

PLATFORM_NAME = "抖音(汽水)"

# 抖音/汽水音乐榜单配置
# 使用抖音开放的榜单接口
DOUYIN_CHARTS: List[Tuple[str, str, str]] = [
    # (榜单名, 榜单类型, 榜单ID/URL标识)
    ("热歌榜", "hot", "hot"),
    ("飙升榜", "soar", "soar"),
    ("新歌榜", "new", "new"),
    ("原创榜", "original", "original"),
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
    """插入快照记录"""
    cols = table_columns(conn, "chart_snapshot")
    payload: Dict[str, Any] = {}

    if "chart_id" in cols:
        payload["chart_id"] = chart_id
    if "captured_at" in cols:
        payload["captured_at"] = captured_at
    if "top_n" in cols:
        payload["top_n"] = int(top_n)

    raw_text = json.dumps(raw_obj, ensure_ascii=False) if isinstance(raw_obj, dict) else None
    if raw_text:
        for col in ["raw", "raw_json", "meta_json"]:
            if col in cols:
                payload[col] = raw_text
                break

    keys = ",".join(payload.keys())
    qs = ",".join(["?"] * len(payload))
    cur = conn.execute(f"INSERT INTO chart_snapshot({keys}) VALUES ({qs})", tuple(payload.values()))
    conn.commit()
    return int(cur.lastrowid)


def insert_entries(conn: sqlite3.Connection, snapshot_id: int, songs: List[Dict[str, Any]]) -> None:
    """插入榜单条目"""
    cols = table_columns(conn, "chart_entry")

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
    """删除今天已有的快照"""
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


def fetch_douyin_chart(chart_type: str, limit: int = 100) -> Dict[str, Any]:
    """
    获取抖音/汽水音乐榜单数据
    
    使用抖音开放平台的热点音乐数据
    支持通过 DOUYIN_COOKIE 环境变量传入认证信息
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.douyin.com/",
        "Origin": "https://www.douyin.com",
    }
    
    # 如果有 Cookie，添加到请求头
    if DOUYIN_COOKIE:
        headers["Cookie"] = DOUYIN_COOKIE
    
    # 抖音热点榜单 API
    # 榜单 ID 映射
    chart_ids = {
        "hot": "6853972723954146568",      # 抖音热歌榜
        "soar": "6853972723954146568",     # 飙升榜（暂用同一个）
        "new": "6853972723954146568",      # 新歌榜（暂用同一个）
        "original": "6853972723954146568", # 原创榜（暂用同一个）
    }
    
    chart_id = chart_ids.get(chart_type, chart_ids["hot"])
    
    apis = [
        # 抖音热歌榜 API
        {
            "url": "https://www.douyin.com/aweme/v1/chart/music/list/",
            "params": {"chart_id": chart_id, "count": str(limit), "cursor": "0"},
        },
    ]
    
    last_err = None
    
    for api_config in apis:
        url = api_config["url"]
        params = api_config.get("params", {})
        
        for attempt in range(FETCH_RETRY):
            try:
                with httpx.Client(timeout=FETCH_TIMEOUT, headers=headers, follow_redirects=True) as client:
                    r = client.get(url, params=params)
                    
                    if r.status_code == 200:
                        try:
                            data = r.json()
                            # 检查是否有有效数据
                            music_list = data.get("music_list") or data.get("data", {}).get("music_list")
                            if music_list and len(music_list) > 0:
                                return {"source": url, "data": data, "chart_type": chart_type}
                        except json.JSONDecodeError:
                            pass
                    
                    last_err = f"HTTP {r.status_code}"
                    
            except httpx.TimeoutException:
                last_err = "Timeout"
            except Exception as e:
                last_err = f"{type(e).__name__}"
            
            time.sleep(FETCH_SLEEP)
    
    raise RuntimeError(f"抖音 API 不可用 ({chart_type}): {last_err}")


def fetch_douyin_chart_fallback(chart_type: str, limit: int) -> Dict[str, Any]:
    """
    后备方案：直接返回空数据
    抖音 API 限制严格，暂时跳过
    """
    raise RuntimeError(f"抖音榜单 API 暂不可用 (type={chart_type})")


def parse_douyin_songs(payload: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    """解析抖音/汽水音乐返回的歌曲数据"""
    data = payload.get("data", {})
    
    # 尝试多种数据结构
    tracks = (
        data.get("music_list") or 
        data.get("tracks") or 
        data.get("data", {}).get("music_list") or
        data.get("data", {}).get("list") or
        data.get("list") or
        []
    )
    
    if not tracks:
        # 尝试从嵌套结构中提取
        if isinstance(data, dict):
            for key in data:
                if isinstance(data[key], list) and len(data[key]) > 0:
                    if isinstance(data[key][0], dict) and ("title" in data[key][0] or "name" in data[key][0]):
                        tracks = data[key]
                        break
    
    if not tracks:
        raise RuntimeError("抖音 parse failed: no tracks found")
    
    out: List[Dict[str, Any]] = []
    
    for idx, track in enumerate(tracks[:limit]):
        if not isinstance(track, dict):
            continue
        
        rank = idx + 1
        
        # 歌曲信息 - 兼容多种字段名
        music_id = (
            track.get("id") or 
            track.get("music_id") or 
            track.get("mid") or
            track.get("item_id")
        )
        
        song_name = (
            track.get("title") or 
            track.get("name") or 
            track.get("music_name") or
            track.get("song_name") or
            ""
        ).strip()
        
        if not song_name:
            continue
        
        # 艺人信息
        artist = (
            track.get("author") or 
            track.get("artist") or 
            track.get("singer") or
            track.get("author_name") or
            ""
        ).strip()
        
        # 如果艺人是列表
        if isinstance(artist, list):
            artist_names = [a.get("name", str(a)) if isinstance(a, dict) else str(a) for a in artist]
            artist = " / ".join(filter(None, artist_names))
        
        # 热度
        heat = (
            track.get("hot_value") or 
            track.get("heat") or 
            track.get("play_count") or
            track.get("use_count") or
            0
        )
        
        track_platform_id = f"douyin:{music_id}" if music_id else f"douyin:{rank}"
        
        out.append({
            "rank": rank,
            "track_platform_id": track_platform_id,
            "track_name": song_name,
            "artist_name_raw": artist,
            "heat": heat if isinstance(heat, (int, float)) else 0,
            "extra_metrics": {
                "music_id": music_id,
                "heat": heat,
                "source": payload.get("source", ""),
            },
        })
    
    if not out:
        raise RuntimeError("抖音 parse failed: no valid songs")
    
    return out


def generate_mock_douyin_data(chart_type: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    生成模拟数据（当 API 不可用时的后备方案）
    
    注意：这只是为了保持数据结构完整性，实际数据需要从真实 API 获取
    """
    print(f"  [WARN] Using mock data for {chart_type} chart (API unavailable)")
    
    # 返回空列表，表示该榜单暂时无数据
    return []


def ingest_douyin(top_n: int = 100) -> None:
    """
    抓取抖音/汽水音乐榜单数据并入库
    
    注意：抖音 API 有访问限制，可能需要配置 DOUYIN_COOKIE 环境变量
    可以通过设置 DOUYIN_ENABLED=false 禁用此抓取
    """
    if not DOUYIN_ENABLED:
        print(f"[douyin] {PLATFORM_NAME} 抓取已禁用 (DOUYIN_ENABLED=false)")
        return
    
    conn = db_connect()
    try:
        platform_id = get_platform_id(conn, PLATFORM_NAME)
        today = beijing_today_iso()
        
        print(f"[douyin] Starting ingest for {PLATFORM_NAME}, date: {today}")

        success_count = 0
        for chart_name, chart_type, chart_id_str in DOUYIN_CHARTS:
            try:
                chart_id = get_chart_id(conn, platform_id, chart_name)
                
                # 删除今天已有的数据
                delete_today_chart_snapshot(conn, chart_id, today)
                
                # 获取数据
                try:
                    payload = fetch_douyin_chart(chart_type, limit=top_n)
                    songs = parse_douyin_songs(payload, limit=top_n)
                except Exception as e:
                    print(f"  [WARN] {chart_name} API failed: {e}")
                    songs = generate_mock_douyin_data(chart_type, limit=top_n)
                
                if not songs:
                    print(f"  [SKIP] {PLATFORM_NAME} - {chart_name}: 无数据")
                    continue
                
                # 入库
                snapshot_id = insert_snapshot(
                    conn, chart_id, now_iso(), 
                    top_n=len(songs), 
                    raw_obj={"chart_type": chart_type, "count": len(songs)}
                )
                insert_entries(conn, snapshot_id, songs)
                
                print(f"✅ 入库完成：{PLATFORM_NAME} - {chart_name} Top{len(songs)} (snapshot_id={snapshot_id})")
                success_count += 1
                
                time.sleep(FETCH_SLEEP)
                
            except Exception as e:
                print(f"⚠️ {PLATFORM_NAME} - {chart_name} 抓取失败: {e}")
                continue
        
        if success_count == 0:
            print(f"[douyin] {PLATFORM_NAME} 所有榜单暂时无法获取（API 限制）")
        else:
            print(f"[douyin] Completed: {success_count}/{len(DOUYIN_CHARTS)} charts")

    finally:
        conn.close()


if __name__ == "__main__":
    ingest_douyin(top_n=int(os.getenv("SYNC_TOP_N", "100")))
