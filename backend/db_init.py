# backend/db_init.py
import os
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

DB_PATH = Path(os.getenv("MDLM_DB", str(ROOT / "charts.db")))

# 尝试导入时区模块，如果失败则使用简单实现
try:
    from timezone_utils import beijing_timestamp
    def now_iso():
        return beijing_timestamp()
except ImportError:
    def now_iso():
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def create_tables(conn: sqlite3.Connection):
    cur = conn.cursor()

    # 平台表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS platform (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)

    # 榜单表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chart (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        code TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(platform_id, name),
        FOREIGN KEY(platform_id) REFERENCES platform(id)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chart_platform ON chart(platform_id);")

    # 每次抓取快照（⚠️不要 platform_id NOT NULL）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chart_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chart_id INTEGER NOT NULL,
        captured_at TEXT NOT NULL,
        top_n INTEGER,
        raw_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY(chart_id) REFERENCES chart(id)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_chart ON chart_snapshot(chart_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_day ON chart_snapshot(substr(captured_at,1,10));")

    # 榜单明细（ingest_qq/ingest_kugou 需要的最小 entry 契约）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chart_entry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER NOT NULL,
        track_platform_id TEXT NOT NULL,
        track_name TEXT NOT NULL,
        artist_name_raw TEXT,
        rank INTEGER NOT NULL,
        heat REAL,
        extra_metrics TEXT,
        raw_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY(snapshot_id) REFERENCES chart_snapshot(id)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_entry_snapshot ON chart_entry(snapshot_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_entry_trackid ON chart_entry(track_platform_id);")

    # 简单的迁移逻辑：检查是否缺少列
    try:
        cur.execute("SELECT artist_name_raw FROM chart_entry LIMIT 1")
    except sqlite3.OperationalError:
        print("  [db_init] Migrating: Adding artist_name_raw to chart_entry")
        cur.execute("ALTER TABLE chart_entry ADD COLUMN artist_name_raw TEXT")

    try:
        cur.execute("SELECT extra_metrics FROM chart_entry LIMIT 1")
    except sqlite3.OperationalError:
        print("  [db_init] Migrating: Adding extra_metrics to chart_entry")
        cur.execute("ALTER TABLE chart_entry ADD COLUMN extra_metrics TEXT")

<<<<<<< Updated upstream
=======
    # 迁移：添加 score 列（用于酷狗音乐）
    # 使用 PRAGMA table_info 检查列是否存在，实现幂等迁移
    columns = [row[1] for row in cur.execute("PRAGMA table_info(chart_entry)").fetchall()]
    if "score" not in columns:
        print("  [db_init] Migrating: Adding score to chart_entry")
        cur.execute("ALTER TABLE chart_entry ADD COLUMN score REAL")

>>>>>>> Stashed changes
    conn.commit()

def seed_basics(conn: sqlite3.Connection):
    cur = conn.cursor()

    # 平台（名字要和 ingest 脚本里 PLATFORM_NAME 一致；常见就是这些）
    for p in ["QQ音乐", "酷狗音乐", "网易云音乐", "抖音(汽水)"]:
        cur.execute("INSERT OR IGNORE INTO platform(name) VALUES (?)", (p,))

    # chart：至少把 ingest 会用到的 chart 预置出来
    # 这里用 “热歌/飙升/原创” 做最小集合（如果你的 ingest_qq 用的是别的 name，就把那几个 name 改成它的）
    platform_map = {r[0]: r[1] for r in cur.execute("SELECT name,id FROM platform").fetchall()}

    def add_chart(platform_name: str, chart_name: str):
        pid = platform_map[platform_name]
        cur.execute(
            "INSERT OR IGNORE INTO chart(platform_id, name) VALUES (?,?)",
            (pid, chart_name),
        )

    # QQ
    for n in ["热歌榜", "飙升榜", "新歌榜", "原创榜"]:
        add_chart("QQ音乐", n)

    # 酷狗
    for n in ["热歌榜", "飙升榜", "新歌榜", "原创榜"]:
        add_chart("酷狗音乐", n)

    # 网易云（如果你有 ingest_netease）
    for n in ["热歌榜", "飙升榜", "新歌榜", "原创榜"]:
        add_chart("网易云音乐", n)

    conn.commit()

def main():
    print(f"[db_init] init sqlite at: {DB_PATH}")
    conn = connect()
    try:
        create_tables(conn)
        seed_basics(conn)
        conn.commit()
    finally:
        conn.close()

    sz = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    print(f"[db_init] done, db size = {sz} bytes")

if __name__ == "__main__":
    main()