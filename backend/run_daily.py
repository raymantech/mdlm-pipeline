# backend/run_daily.py
import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent
PY = sys.executable

def ts():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def run(cmd, title):
    print(f"{ts()} [STEP] {title}")
    subprocess.run(cmd, check=True)
    print(f"{ts()} [OK]   {title}")

def main():
    os.environ.setdefault("MDLM_DB", str(ROOT / "charts.db"))

    # 1) init db
    run([PY, str(ROOT / "db_init.py")], "init sqlite db")

    # 2) ingest
    run([PY, str(ROOT / "ingest_qq.py")], "ingest ingest_qq.py")
    run([PY, str(ROOT / "ingest_kugou.py")], "ingest ingest_kugou.py")

    # 3) analyze & merge
    if (ROOT / "analyze_events.py").exists():
        run([PY, str(ROOT / "analyze_events.py")], "post analyze_events.py")
    if (ROOT / "merge_events.py").exists():
        run([PY, str(ROOT / "merge_events.py")], "post merge_events.py")

    # 4) export dashboard json (keep latest 7 days)
    if (ROOT / "export_dashboard_data.py").exists():
        out = os.getenv("EXPORT_OUT", str((ROOT.parent / "frontend" / "data" / "merged_events_latest.json").resolve()))
        days = os.getenv("EXPORT_DAYS", "7")
        run([PY, str(ROOT / "export_dashboard_data.py"), "--days", str(days), "--out", out],
            f"post export_dashboard_data.py (--days {days})")

if __name__ == "__main__":
    main()