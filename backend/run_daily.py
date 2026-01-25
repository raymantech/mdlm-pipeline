#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MDLM daily pipeline entry.

Goals:
- Work in both local and GitHub Actions
- If backend/charts.db missing, auto init via backend/db_init.py
- Run ingest scripts -> analyze -> merge -> export
- Exit non-zero when any step fails (CI friendly)
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime


ROOT = Path(__file__).resolve().parents[1]              # repo root
BACKEND = ROOT / "backend"
LOG_DIR = BACKEND / "logs"

DB_PATH = BACKEND / "charts.db"
DB_INIT = BACKEND / "db_init.py"

INGEST_SCRIPTS = [
    BACKEND / "ingest_qq.py",
    BACKEND / "ingest_kugou.py",
    # 如果你有网易云脚本（例如 ingest_163.py / ingest_netease.py），在这里加一行：
    # BACKEND / "ingest_163.py",
]

ANALYZE_SCRIPT = BACKEND / "analyze_events.py"
MERGE_SCRIPT = BACKEND / "merge_events.py"
EXPORT_SCRIPT = BACKEND / "export_dashboard_data.py"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _print(msg: str) -> None:
    print(msg, flush=True)


def _ensure_logs_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _run(cmd: list[str], step_name: str, log_file: Path | None = None) -> None:
    """
    Run a subprocess. If log_file provided, append stdout/stderr to it.
    Raise on failure.
    """
    _print(f"[STEP] {step_name}")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    if log_file is None:
        subprocess.run(cmd, check=True, cwd=str(ROOT), env=env)
        _print(f"[OK]   {step_name}")
        return

    _ensure_logs_dir()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n\n==================== {_now()} {step_name} ====================\n")
        f.write("CMD: " + " ".join(cmd) + "\n")
        f.flush()
        p = subprocess.run(cmd, cwd=str(ROOT), env=env, stdout=f, stderr=subprocess.STDOUT)
        if p.returncode != 0:
            f.write(f"\n[FAIL] {step_name} (exit {p.returncode})\n")
            f.flush()
            raise subprocess.CalledProcessError(p.returncode, cmd)
        f.write(f"\n[OK] {step_name}\n")
        f.flush()

    _print(f"[OK]   {step_name} (log: {log_file})")


def ensure_db() -> None:
    """
    Make sure backend/charts.db exists. If not, run db_init.py.
    """
    if DB_PATH.exists():
        return

    _print(f"[BOOT] charts.db not found: {DB_PATH}")
    if not DB_INIT.exists():
        raise SystemExit(f"❌ db_init.py not found: {DB_INIT}")

    # Run db_init.py with current python (venv in CI/local)
    _run([sys.executable, str(DB_INIT)], "init sqlite db", LOG_DIR / "db_init.log")

    if not DB_PATH.exists():
        raise SystemExit(f"❌ db_init.py ran but still no db: {DB_PATH}")

    _print(f"[BOOT] db created: {DB_PATH}")


def check_scripts_exist() -> None:
    missing = []
    for p in INGEST_SCRIPTS + [ANALYZE_SCRIPT, MERGE_SCRIPT, EXPORT_SCRIPT]:
        if not p.exists():
            missing.append(str(p))
    if missing:
        raise SystemExit("❌ Missing pipeline scripts:\n" + "\n".join(missing))


def main() -> int:
    _print("▶ MDLM daily pipeline (run_daily.py)")
    _print(f"[INFO] ROOT={ROOT}")
    _print(f"[INFO] python={sys.executable}")

    _ensure_logs_dir()
    check_scripts_exist()
    ensure_db()

    # 1) ingest
    for script in INGEST_SCRIPTS:
        _run([sys.executable, str(script)], f"ingest: {script.name}", LOG_DIR / "ingest.log")

    # 2) analyze
    _run([sys.executable, str(ANALYZE_SCRIPT)], "analyze events", LOG_DIR / "analyze.log")

    # 3) merge
    _run([sys.executable, str(MERGE_SCRIPT)], "merge events", LOG_DIR / "merge.log")

    # 4) export latest json (你之前用 --latest 或 --all，这里默认 latest)
    # 如果你希望 CI 每天都导出“近7天”，并且写同一个 merged_events_latest.json，
    # 建议 export_dashboard_data.py 支持 --days 7（你 2.0 里应该已经有）。
    # 这里先用 --latest，保证不报错；你要 --days 7 我也可以给你配套改 export。
    _run([sys.executable, str(EXPORT_SCRIPT), "--latest"], "export latest json", LOG_DIR / "export.log")

    _print("[OK] all done")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as e:
        _print(f"[FAIL] subprocess error: {e}")
        raise
    except Exception as e:
        _print(f"[FAIL] unexpected error: {e}")
        raise