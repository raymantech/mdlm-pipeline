#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Export merged events from SQLite to a frontend-friendly JSON.
Robust to small schema differences across MDLM versions.

Usage:
  python backend/export_dashboard_data.py --days 7 --out frontend/data/merged_events_latest.json

Env:
  CHARTS_DB_PATH: override sqlite path (default: backend/charts.db next to this file)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def get_db_path() -> Path:
    env = os.getenv("CHARTS_DB_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # backend/export_dashboard_data.py -> backend/charts.db
    return (Path(__file__).resolve().parent / "charts.db").resolve()


def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return []
    return [r[1] for r in rows]  # name


@dataclass
class MergedEventRow:
    id: int
    day: str
    count: int
    summary: Optional[str]
    payload_raw: str


def choose_col(cols: List[str], candidates: List[str]) -> Optional[str]:
    s = set(cols)
    for c in candidates:
        if c in s:
            return c
    return None


def default_out_path() -> Path:
    # repo_root/frontend/data/merged_events_latest.json
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / "frontend" / "data" / "merged_events_latest.json").resolve()


def load_rows(conn: sqlite3.Connection, days: int) -> Tuple[List[MergedEventRow], Dict[str, str]]:
    """
    Returns (rows, chosen_columns_map)
    """
    cols = table_columns(conn, "merged_event")
    if not cols:
        raise RuntimeError("SQLite table merged_event not found (did merge_events.py run successfully?)")

    day_col = choose_col(cols, ["day", "date", "captured_day", "snapshot_day"])
    payload_col = choose_col(cols, ["payload_json", "payload", "payload_text", "payload_str"])
    count_col = choose_col(cols, ["count", "event_count", "merged_count"])
    summary_col = choose_col(cols, ["summary", "summary_text", "desc", "note"])

    if not day_col:
        raise RuntimeError(f"merged_event missing day-like column. existing={cols}")
    if not payload_col:
        raise RuntimeError(f"merged_event missing payload column. existing={cols}")

    # Build SELECT with safe fallbacks
    select_parts = [
        "id",
        f"{day_col} AS day",
        (f"COALESCE({count_col}, 0) AS count" if count_col else "0 AS count"),
        (f"{summary_col} AS summary" if summary_col else "NULL AS summary"),
        f"{payload_col} AS payload_raw",
    ]
    sql = f"""
    SELECT {", ".join(select_parts)}
    FROM merged_event
    WHERE day >= date('now', ?)
    ORDER BY day DESC, id DESC
    """
    param = f"-{int(days)} day"
    out: List[MergedEventRow] = []
    for r in conn.execute(sql, (param,)).fetchall():
        out.append(MergedEventRow(
            id=int(r[0]),
            day=str(r[1])[:10],
            count=int(r[2] or 0),
            summary=(None if r[3] is None else str(r[3])),
            payload_raw="" if r[4] is None else str(r[4]),
        ))

    chosen = {"day_col": day_col, "payload_col": payload_col}
    if count_col:
        chosen["count_col"] = count_col
    if summary_col:
        chosen["summary_col"] = summary_col
    return out, chosen


def parse_payload(payload_raw: str) -> List[Dict[str, Any]]:
    """
    payload_raw might be:
      - a JSON list of events
      - a JSON object with keys like {"events":[...]}
      - empty/invalid JSON -> return []
    """
    if not payload_raw:
        return []
    try:
        obj = json.loads(payload_raw)
    except Exception:
        return []

    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        # Common shapes
        for k in ("events", "items", "data"):
            v = obj.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        # If it's a single event dict
        return [obj]
    return []


def normalize_events(rows: List[MergedEventRow]) -> Tuple[List[Dict[str, Any]], List[str], int]:
    events: List[Dict[str, Any]] = []
    days_set = set()
    for row in rows:
        day = row.day
        days_set.add(day)
        items = parse_payload(row.payload_raw)
        # Attach day if missing
        for it in items:
            if not it.get("day") and not it.get("date"):
                it["day"] = day
        events.extend(items)

    # Sort days desc
    days_list = sorted(days_set, reverse=True)

    # Total count: prefer len(events) because different schemas/count rules
    total = len(events)
    return events, days_list, total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="Export recent N days (default: 7)")
    ap.add_argument("--out", type=str, default="", help="Output JSON path")
    args = ap.parse_args()

    db_path = get_db_path()
    if not db_path.exists():
        raise SystemExit(f"❌ SQLite not found: {db_path}")

    outp = Path(args.out).expanduser().resolve() if args.out else default_out_path()
    # Ensure output dir exists (GitHub Actions checkout may not include ignored dirs like frontend/data)
    outp.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows, chosen_cols = load_rows(conn, days=int(args.days))
        events, days_list, total = normalize_events(rows)

        payload = {
            "generated_at": now_iso(),
            "days": days_list,
            "count": total,
            "events": events,  # frontend can render from this
            # Keep merged blocks too (useful for debugging / future UI)
            "merged_events": [
                {
                    "id": r.id,
                    "day": r.day,
                    "count": r.count,
                    "summary": r.summary,
                    "payload": parse_payload(r.payload_raw),
                }
                for r in rows
            ],
            "meta": {
                "db_path": str(db_path),
                "schema": chosen_cols,
                "window_days": int(args.days),
            },
        }

        outp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"✅ Export done: {outp}  (days={args.days}, events={total}, merged_blocks={len(rows)})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
