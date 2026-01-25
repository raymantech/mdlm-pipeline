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

    # --- chart_entry: 兼容 ingest_qq / ingest_kugou ---
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chart_entry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER NOT NULL,
        platform_id INTEGER,
        chart_id INTEGER,
        track_platform_id TEXT NOT NULL,
        track_name TEXT NOT NULL,
        rank INTEGER NOT NULL,
        heat REAL,
        raw_json TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(snapshot_id) REFERENCES chart_snapshot(id),
        FOREIGN KEY(platform_id) REFERENCES platform(id),
        FOREIGN KEY(chart_id) REFERENCES chart(id)
    )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_entry_snapshot ON chart_entry(snapshot_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_entry_track ON chart_entry(platform_id, track_platform_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_entry_day ON chart_entry(substr(created_at,1,10));")


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

    # --- chart_snapshot (兼容 ingest_qq：insert_snapshot 不一定带 platform_id) ---
    # snapshot：一次抓取的元信息（某榜在某时刻抓了一次）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chart_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform_id INTEGER,                 -- 允许为空：兼容 ingest_qq 未传 platform_id 的情况
        chart_id INTEGER NOT NULL,
        captured_at TEXT NOT NULL,           -- ISO datetime
        top_n INTEGER,
        raw_json TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(platform_id) REFERENCES platform(id),
        FOREIGN KEY(chart_id) REFERENCES chart(id)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_chart_time ON chart_snapshot(chart_id, captured_at);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_day ON chart_snapshot(substr(captured_at,1,10));")

    # --- chart_snapshot_item ---
    # items：一次抓取里每首歌的排名/热度明细（给后续事件分析用）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chart_snapshot_item (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER NOT NULL,
        platform_id INTEGER,                 -- 可为空（可从 chart / platform 推导）
        chart_id INTEGER,
        song_id TEXT NOT NULL,
        rank INTEGER,
        heat REAL,
        raw_json TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(snapshot_id) REFERENCES chart_snapshot(id),
        FOREIGN KEY(platform_id) REFERENCES platform(id),
        FOREIGN KEY(chart_id) REFERENCES chart(id)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_item_snapshot ON chart_snapshot_item(snapshot_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_item_song ON chart_snapshot_item(platform_id, song_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_item_day ON chart_snapshot_item(substr(created_at,1,10));")
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