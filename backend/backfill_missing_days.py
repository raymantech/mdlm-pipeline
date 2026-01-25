#!/usr/bin/env python3
import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional, Set

from mdlm_config import db_path, load_env

load_env(override=True)
DB = str(db_path())


def q1(conn: sqlite3.Connection, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def distinct_days(conn: sqlite3.Connection, table: str, col: str) -> List[str]:
    rows = conn.execute(
        f"SELECT DISTINCT substr({col},1,10) d FROM {table} WHERE {col} IS NOT NULL AND {col} != '' ORDER BY d ASC"
    ).fetchall()
    return [r[0] for r in rows if r and r[0]]


def run_py(script_path: Path, args: List[str]) -> None:
    cmd = [sys.executable, str(script_path)] + args
    subprocess.run(cmd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill missing merged_event days based on available snapshots.")
    ap.add_argument("--from", dest="from_day", default=None, help="Start day (YYYY-MM-DD), inclusive")
    ap.add_argument("--to", dest="to_day", default=None, help="End day (YYYY-MM-DD), inclusive")
    ap.add_argument("--dry-run", action="store_true", help="Only print what would run")
    args = ap.parse_args()

    dbp = Path(DB).resolve()
    if not dbp.exists():
        raise SystemExit(f"❌ SQLite not found: {dbp}")

    backend_dir = Path(__file__).resolve().parent
    analyze = backend_dir / "analyze_events.py"
    merge = backend_dir / "merge_events.py"
    export = backend_dir / "export_dashboard_data.py"

    # Export strategy (so the frontend has enough history):
    # - EXPORT_MODE=all or EXPORT_ALL=1 -> --all
    # - EXPORT_DAYS=N -> --days N
    # - default -> --days 180
    export_mode = (os.getenv("EXPORT_MODE") or "").strip().lower()
    export_days = (os.getenv("EXPORT_DAYS") or "").strip()
    if os.getenv("EXPORT_ALL") == "1" or export_mode == "all":
        export_args = ["--all"]
    elif export_days.isdigit():
        export_args = ["--days", export_days]
    else:
        export_args = ["--days", "180"]

    with sqlite3.connect(str(dbp)) as conn:
        snapshot_days = distinct_days(conn, "chart_snapshot", "captured_at")
        if not snapshot_days or len(snapshot_days) < 2:
            print("[backfill] Not enough snapshot days to backfill (need at least 2).")
            return

        # merged_event table may not exist yet
        try:
            merged_days = set(distinct_days(conn, "merged_event", "merge_date"))
        except Exception:
            merged_days = set()

        # default range: from (last merged + 1 day) to latest snapshot day
        last_merged: Optional[str] = None
        if merged_days:
            last_merged = q1(conn, "SELECT MAX(merge_date) FROM merged_event")

        start_day = args.from_day
        end_day = args.to_day

        if not end_day:
            end_day = snapshot_days[-1]
        if not start_day:
            # if never merged, start from second snapshot day
            start_day = (last_merged or snapshot_days[1])

        # build candidate days in range, but only those that actually exist in snapshots
        candidates: List[str] = [d for d in snapshot_days if start_day <= d <= end_day]

        missing: List[str] = [d for d in candidates if d not in merged_days]

        if not missing:
            print(f"[backfill] No missing days in range {start_day} ~ {end_day}.")
            # still export latest to keep frontend fresh
            if not args.dry_run:
                run_py(export, export_args)
            return

        print(f"[backfill] Missing days: {len(missing)}")
        for d in missing:
            print(f"  - {d}")

        if args.dry_run:
            print("[backfill] dry-run: done")
            return

        for d in missing:
            print(f"\n[backfill] Analyze {d}")
            run_py(analyze, ["--day", d])
            print(f"[backfill] Merge {d}")
            run_py(merge, ["--day", d])

        print("\n[backfill] Export for frontend")
        run_py(export, export_args)
        print("[backfill] OK")


if __name__ == "__main__":
    main()
