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

    # 1) init db (必须第一步)
    run([PY, str(ROOT / "db_init.py")], "init sqlite db")

    # 2) ingest
    run([PY, str(ROOT / "ingest_qq.py")], "ingest ingest_qq.py")
    run([PY, str(ROOT / "ingest_kugou.py")], "ingest ingest_kugou.py")

    # 3) 事件分析/合并/导出（如果你仓库里有）
    maybe = [
        ROOT / "analyze_events.py",
        ROOT / "merge_events.py",
        ROOT / "export_dashboard_data.py",
    ]
    for s in maybe:
        if s.exists():
            run([PY, str(s), "--latest"] if s.name == "export_dashboard_data.py" else [PY, str(s)],
                f"post {s.name}")

if __name__ == "__main__":
    main()