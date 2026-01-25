#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
LOG_DIR = BACKEND / "logs"

DB_PATH = BACKEND / "charts.db"
DB_INIT = BACKEND / "db_init.py"

INGEST_SCRIPTS = [
    BACKEND / "ingest_qq.py",
    BACKEND / "ingest_kugou.py",
]

ANALYZE_SCRIPT = BACKEND / "analyze_events.py"
MERGE_SCRIPT = BACKEND / "merge_events.py"
EXPORT_SCRIPT = BACKEND / "export_dashboard_data.py"

def log(msg):
    print(msg, flush=True)

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def run(cmd, name):
    log(f"[STEP] {name}")
    subprocess.run(cmd, check=True)
    log(f"[OK]   {name}")

def ensure_db():
    if DB_PATH.exists():
        log(f"[INFO] sqlite ok: {DB_PATH}")
        return

    log(f"[BOOT] charts.db not found, init db")
    run([sys.executable, str(DB_INIT)], "init sqlite db")

    if not DB_PATH.exists():
        raise SystemExit(f"❌ db_init finished but db still missing: {DB_PATH}")

def main():
    log("▶ MDLM daily pipeline")
    log(f"[INFO] ROOT={ROOT}")
    log(f"[INFO] python={sys.executable}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ensure_db()

    for s in INGEST_SCRIPTS:
        run([sys.executable, str(s)], f"ingest {s.name}")

    run([sys.executable, str(ANALYZE_SCRIPT)], "analyze events")
    run([sys.executable, str(MERGE_SCRIPT)], "merge events")
    run([sys.executable, str(EXPORT_SCRIPT), "--latest"], "export json")

    log("[OK] all done")

if __name__ == "__main__":
    main()