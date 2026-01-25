#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "backend" / "charts.db"

def main():
    print(f"[db_init] init sqlite at: {DB_PATH}")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # === chart_snapshot ===
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chart_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT,
        chart_name TEXT,
        song_id TEXT,
        song_name TEXT,
        artist TEXT,
        rank INTEGER,
        captured_at TEXT
    )
    """)

    # === event ===
    cur.execute("""
    CREATE TABLE IF NOT EXISTS event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT,
        chart_name TEXT,
        song_id TEXT,
        song_name TEXT,
        artist TEXT,
        event_type TEXT,
        delta INTEGER,
        detected_at TEXT
    )
    """)

    # === merged_event ===
    cur.execute("""
    CREATE TABLE IF NOT EXISTS merged_event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT,
        platform TEXT,
        song_id TEXT,
        song_name TEXT,
        artist TEXT,
        events TEXT
    )
    """)

    conn.commit()
    conn.close()

    print(f"[db_init] done, db size = {DB_PATH.stat().st_size} bytes")

if __name__ == "__main__":
    main()