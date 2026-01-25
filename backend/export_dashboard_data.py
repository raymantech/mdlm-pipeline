#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_dashboard_data.py
Robust exporter for MDLM pipeline.

- Reads merged_event (preferred) and/or event tables from backend/charts.db
- Produces frontend/data/merged_events_latest.json
- Supports --days N window (default 7)
- Tolerates schema drift: detects date-like columns, JSON columns, etc.
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "backend" / "charts.db"
DEFAULT_OUT = ROOT / "frontend" / "data" / "merged_events_latest.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_parent(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in rows]


def has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone()
    return row is not None


def pick_date_column(cols: List[str]) -> Optional[str]:
    """
    Heuristic: choose a date-like column in priority order.
    """
    priority = [
        "day",
        "date",
        "merge_date",
        "captured_at",
        "created_at",
        "updated_at",
        "ts",
        "timestamp",
    ]
    for c in priority:
        if c in cols:
            return c
    # fallback: any column containing 'date' or ending with '_at'
    for c in cols:
        lc = c.lower()
        if "date" in lc:
            return c
    for c in cols:
        lc = c.lower()
        if lc.endswith("_at"):
            return c
    return None


def to_day_expr(col: str) -> str:
    """
    SQLite expression to derive YYYY-MM-DD from a column that stores ISO datetime or date.
    For 'merge_date' which is already YYYY-MM-DD, substr works fine.
    """
    # use substr for robustness (works for '2026-01-25' and '2026-01-25T..')
    return f"substr({col},1,10)"


def safe_json_load(s: Any) -> Any:
    if s is None:
        return None
    if isinstance(s, (dict, list, int, float, bool)):
        return s
    if not isinstance(s, str):
        try:
            s = str(s)
        except Exception:
            return None
    s = s.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s  # keep raw string if not json


def fetch_days(conn: sqlite3.Connection, table: str, date_col: str, days: int) -> List[str]:
    expr = to_day_expr(date_col)
    rows = conn.execute(
        f"SELECT DISTINCT {expr} AS d FROM {table} "
        f"WHERE {expr} IS NOT NULL AND length({expr})=10 "
        f"ORDER BY d DESC LIMIT ?",
        (days,),
    ).fetchall()
    return [r["d"] for r in rows]


def export_from_merged_event(conn: sqlite3.Connection, days: int) -> Dict[str, Any]:
    cols = table_columns(conn, "merged_event")
    date_col = pick_date_column(cols)
    if not date_col:
        raise RuntimeError(f"merged_event missing day-like column. existing={cols}")

    # JSON-ish columns we know
    json_candidates = [
        "payload_json",
        "merged_json",
        "summary_json",
        "tags_json",
        "charts_json",
        "source_event_ids_json",
        "source_events_json",
        "extra_json",
        "raw_json",
    ]
    # choose any existing among candidates
    existing_json_cols = [c for c in json_candidates if c in cols]

    days_list = fetch_days(conn, "merged_event", date_col, days)
    if not days_list:
        return {
            "meta": {"generated_at": utc_now_iso(), "source": "merged_event", "days": days, "note": "no rows"},
            "days": [],
            "count": 0,
            "merged_events": [],
            "events": [],
        }

    # fetch rows within window
    # Use IN list for exact day match using derived day expr
    expr = to_day_expr(date_col)
    q_marks = ",".join("?" for _ in days_list)
    rows = conn.execute(
        f"SELECT * FROM merged_event WHERE {expr} IN ({q_marks}) "
        f"ORDER BY {expr} DESC, id DESC",
        tuple(days_list),
    ).fetchall()

    merged_events: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        # normalize day
        d["day"] = (str(d.get(date_col) or "")[:10]) if d.get(date_col) is not None else None
        # parse json cols
        for jc in existing_json_cols:
            d[jc] = safe_json_load(d.get(jc))
        merged_events.append(d)

    # Build a light 'events' array for frontends that still expect it.
    # We'll map merged_event rows into an event-like shape.
    events: List[Dict[str, Any]] = []
    for me in merged_events:
        events.append(
            {
                "day": me.get("day"),
                "platform": me.get("platform_name") or me.get("platform") or "",
                "track_name": me.get("track_name") or "",
                "artist_name": me.get("artist_name_raw") or me.get("artist_name") or "",
                "severity": me.get("max_severity") or me.get("severity") or "",
                "narrative": me.get("best_narrative") or me.get("narrative") or "",
                "best_rank_prev": me.get("best_rank_prev"),
                "best_rank_now": me.get("best_rank_now"),
                "best_delta": me.get("best_delta"),
                "charts": me.get("charts_json"),
                "tags": me.get("tags_json"),
                "source_event_ids": me.get("source_event_ids_json"),
                "id": me.get("id"),
            }
        )

    return {
        "meta": {"generated_at": utc_now_iso(), "source": "merged_event", "days": days},
        "days": days_list,
        "count": len(events),
        "events": events,
        "merged_events": merged_events,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB), help="Path to sqlite db (default backend/charts.db)")
    ap.add_argument("--days", type=int, default=int(os.getenv("EXPORT_DAYS", "7")), help="Window days (default 7)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Output json path")
    args = ap.parse_args()

    db_path = Path(args.db).resolve()
    out_path = Path(args.out).resolve()

    if not db_path.exists():
        raise SystemExit(f"❌ sqlite db not found: {db_path}")

    conn = connect(db_path)
    try:
        if has_table(conn, "merged_event"):
            payload = export_from_merged_event(conn, args.days)
        else:
            raise SystemExit("❌ table merged_event not found (pipeline requires merge_events.py step)")
    finally:
        conn.close()

    ensure_parent(out_path)
    tmp = out_path.with_suffix(".tmp.json")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(out_path)

    print(f"✅ Export done: {out_path}")
    print(f"- days: {len(payload.get('days') or [])}")
    print(f"- count: {payload.get('count')}")
    print(f"- source: {payload.get('meta', {}).get('source')}")


if __name__ == "__main__":
    main()
