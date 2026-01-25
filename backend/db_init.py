#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "backend" / "charts.db"

PLATFORMS = [
    "QQ音乐",
    "网易云音乐",
    "酷狗音乐",
    "抖音",
    "汽水音乐",
]

def main():
    print(f"[db_init] init sqlite at: {DB_PATH}")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    cur = conn.cursor()

    # --- platform ---
    cur.execute("""
    CREATE TABLE IF NOT EXISTS platform (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """)

    # --- chart ---
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chart (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(platform_id, name),
        FOREIGN KEY(platform_id) REFERENCES platform(id)
    )
    """)

    # --- song ---
    # song_id: 各平台歌曲唯一标识（QQ 可能是 songmid，网易云是 id，酷狗是 hash）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS song (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform_id INTEGER NOT NULL,
        song_id TEXT NOT NULL,
        song_name TEXT,
        artist TEXT,
        meta_json TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(platform_id, song_id),
        FOREIGN KEY(platform_id) REFERENCES platform(id)
    )
    """)

    # --- chart_snapshot ---
    # 快照记录：某天某榜某歌的排名等
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chart_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform_id INTEGER NOT NULL,
        chart_id INTEGER NOT NULL,
        song_id TEXT NOT NULL,
        rank INTEGER,
        heat REAL,
        captured_at TEXT NOT NULL,
        raw_json TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(platform_id) REFERENCES platform(id),
        FOREIGN KEY(chart_id) REFERENCES chart(id)
    )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_day ON chart_snapshot(substr(captured_at,1,10));")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_platform_chart ON chart_snapshot(platform_id, chart_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_song ON chart_snapshot(platform_id, song_id);")

    # --- event ---
    cur.execute("""
    CREATE TABLE IF NOT EXISTS event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,
        platform TEXT,
        chart_name TEXT,
        song_id TEXT,
        song_name TEXT,
        artist TEXT,
        event_type TEXT,
        delta INTEGER,
        detected_at TEXT,
        meta_json TEXT
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_event_day ON event(day);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_event_song ON event(platform, song_id);")

    # --- merged_event ---
    # 你之前脚本里用的是 day 字段而不是 merged_at（避免再踩坑）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS merged_event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,
        platform TEXT,
        song_id TEXT,
        song_name TEXT,
        artist TEXT,
        events TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_merged_day ON merged_event(day);")

    # --- seed platforms (保证 ingest_qq.py get_platform_id 能查到) ---
    for name in PLATFORMS:
        cur.execute("INSERT OR IGNORE INTO platform(name) VALUES(?)", (name,))

    conn.commit()
    conn.close()

    print(f"[db_init] done, db size = {DB_PATH.stat().st_size} bytes")

if __name__ == "__main__":
    main()