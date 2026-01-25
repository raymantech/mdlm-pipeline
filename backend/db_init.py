import sqlite3
from pathlib import Path

DB_PATH = Path("charts.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")  # 更稳，适合定时写入
    conn.execute("PRAGMA foreign_keys=ON;")

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS platform (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS chart (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        category TEXT,
        update_freq TEXT,
        source_url TEXT,
        UNIQUE(platform_id, name),
        FOREIGN KEY(platform_id) REFERENCES platform(id)
    );

    CREATE TABLE IF NOT EXISTS chart_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chart_id INTEGER NOT NULL,
        captured_at TEXT NOT NULL,   -- ISO时间字符串
        top_n INTEGER NOT NULL,
        raw_payload TEXT,            -- 可选：存JSON/HTML摘要
        FOREIGN KEY(chart_id) REFERENCES chart(id)
    );

    CREATE TABLE IF NOT EXISTS chart_entry (
        snapshot_id INTEGER NOT NULL,
        rank INTEGER NOT NULL,
        track_platform_id TEXT NOT NULL,
        track_name TEXT NOT NULL,
        artist_name_raw TEXT,
        score REAL,
        extra_metrics TEXT,          -- JSON字符串
        PRIMARY KEY(snapshot_id, rank),
        FOREIGN KEY(snapshot_id) REFERENCES chart_snapshot(id)
    );

    CREATE TABLE IF NOT EXISTS event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chart_id INTEGER NOT NULL,
        track_platform_id TEXT NOT NULL,
        event_type TEXT NOT NULL,    -- breakout/dropout/comeback/decline/hot
        severity INTEGER NOT NULL,   -- 1-5
        detected_at TEXT NOT NULL,
        evidence TEXT,               -- JSON字符串
        narrative TEXT,              -- 一句话摘要
        FOREIGN KEY(chart_id) REFERENCES chart(id)
    );
    """)

    # 初始化三平台（只插一次，重复运行不会重复）
    conn.execute("INSERT OR IGNORE INTO platform(name) VALUES (?)", ("QQ音乐",))
    conn.execute("INSERT OR IGNORE INTO platform(name) VALUES (?)", ("网易云音乐",))
    conn.execute("INSERT OR IGNORE INTO platform(name) VALUES (?)", ("酷狗音乐",))

    conn.commit()
    conn.close()
    print(f"✅ DB ready: {DB_PATH.resolve()}")

if __name__ == "__main__":
    init_db()