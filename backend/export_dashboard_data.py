#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Export merged events JSON for the static frontend.

Frontend (frontend/app.js) reads: frontend/data/merged_events_latest.json
and expects either an array or an object containing an `events` array.

This exporter is:
- Python 3.9 compatible
- Schema-adaptive (reads merged_event columns via PRAGMA)
- Supports exporting all history or last N days
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_db_path() -> Path:
    """Resolve charts.db path.

    Rules:
    1) SQLITE_DB_PATH absolute -> use directly
    2) SQLITE_DB_PATH relative -> relative to project root
    3) default -> backend/charts.db
    """
    root = project_root()
    env = (os.getenv("SQLITE_DB_PATH") or "").strip()
    if env:
        p = Path(env)
        return p if p.is_absolute() else (root / p).resolve()
    return (root / "backend" / "charts.db").resolve()


def table_cols(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cur.fetchall()]


def pick_first(cols: List[str], candidates: List[str]) -> Optional[str]:
    s = set(cols)
    for c in candidates:
        if c in s:
            return c
    return None


def day_expr(col: str) -> str:
    # day/date columns are usually already YYYY-MM-DD
    if col in ("merge_date", "day", "date", "dt", "d") or col.endswith("_date") or col.endswith("_day"):
        return col
    return f"substr({col},1,10)"


def default_out_path() -> Path:
    root = project_root()
    p = root / "frontend" / "data"
    if p.exists():
        return (p / "merged_events_latest.json").resolve()
    # fallback
    p2 = root / "backend" / "data"
    p2.mkdir(parents=True, exist_ok=True)
    return (p2 / "merged_events_latest.json").resolve()


def json_load_maybe(s: Any) -> Any:
    if s is None:
        return None
    if isinstance(s, (dict, list)):
        return s
    if not isinstance(s, str):
        return s
    s = s.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s


def build_map(cols: List[str]) -> Dict[str, Optional[str]]:
    """Map merged_event schema -> frontend expected keys."""
    return {
        "id": pick_first(cols, ["id", "uuid"]),
        "date": pick_first(cols, ["merge_date", "day", "date", "dt", "d"]),
        "platform": pick_first(cols, ["platform_name", "platform", "source"]),
        "track": pick_first(cols, ["track_name", "track", "song_name", "title", "name"]),
        "artist": pick_first(cols, ["artist_name_raw", "artist", "singer", "artist_name"]),
        "tags": pick_first(cols, ["tags_json", "tags", "tag_json"]),
        "charts": pick_first(cols, ["charts_json", "charts", "chart_json"]),
        "severity": pick_first(cols, ["max_severity", "severity"]),
        "rank_prev": pick_first(cols, ["best_rank_prev", "rank_prev", "rank_before"]),
        "rank_now": pick_first(cols, ["best_rank_now", "rank_now", "rank_after"]),
        "delta": pick_first(cols, ["best_delta", "delta", "rank_change"]),
        "narrative": pick_first(cols, ["best_narrative", "narrative", "desc"]),
    }


def pick_day_col(cols: List[str]) -> str:
    # For grouping/filtering
    c = pick_first(cols, ["merge_date", "day", "date", "dt", "d", "created_at", "merged_at", "timestamp", "ts"])
    if not c:
        raise RuntimeError("merged_event 表中找不到可用于推导日期的列（merge_date/day/date/created_at 等）")
    return c


def select_days(conn: sqlite3.Connection, daycol: str, last_n: int) -> List[str]:
    cur = conn.cursor()
    dsql = day_expr(daycol)
    cur.execute(f"SELECT {dsql} AS d FROM merged_event GROUP BY d ORDER BY d DESC LIMIT ?", (last_n,))
    return [r[0] for r in cur.fetchall() if r and r[0]]


def export(*, mode_all: bool, days: Optional[int], out: Optional[str]) -> Path:
    db = resolve_db_path()
    if not db.exists():
        raise FileNotFoundError(f"charts.db not found: {db}")

    conn = sqlite3.connect(str(db))
    try:
        cols = table_cols(conn, "merged_event")
        if not cols:
            raise RuntimeError("merged_event 表不存在或为空")

        fmap = build_map(cols)
        idx = {c: i for i, c in enumerate(cols)}
        daycol = pick_day_col(cols)
        dsql = day_expr(daycol)

        where = ""
        params: List[Any] = []
        mode = "all"
        if not mode_all:
            n = days if days is not None else int(os.getenv("EXPORT_DAYS", "180"))
            ds = select_days(conn, daycol, n)
            if not ds:
                raise RuntimeError("merged_event 表中没有可导出的日期")
            where = f"WHERE {dsql} IN ({','.join(['?']*len(ds))})"
            params = ds
            mode = f"last_{len(ds)}_days"

        cur = conn.cursor()
        cur.execute(f"SELECT * FROM merged_event {where}", params)
        rows = cur.fetchall()

        def get(row: Tuple[Any, ...], key: str) -> Any:
            col = fmap.get(key)
            if col and col in idx:
                return row[idx[col]]
            return None

        events: List[Dict[str, Any]] = []
        for r in rows:
            # date
            d = get(r, "date")
            if d is None and daycol in idx:
                d = r[idx[daycol]]
            d = str(d)[:10] if d is not None else ""

            charts = json_load_maybe(get(r, "charts"))
            if charts is None:
                charts_list: List[str] = []
            elif isinstance(charts, list):
                charts_list = charts
            else:
                charts_list = [str(charts)]

            tags_raw = get(r, "tags")
            # Keep tags as JSON-string (frontend supports both string & list)
            if isinstance(tags_raw, list):
                tags_out = json.dumps(tags_raw, ensure_ascii=False)
            elif isinstance(tags_raw, str):
                tags_out = tags_raw
            else:
                tags_out = json.dumps([str(tags_raw)], ensure_ascii=False) if tags_raw is not None else "[]"

            ev = {
                "id": get(r, "id"),
                "date": d,
                "platform": (get(r, "platform") or "").strip(),
                "charts": charts_list,
                "track": (get(r, "track") or "").strip(),
                "artist": (get(r, "artist") or "").strip(),
                "rank_prev": get(r, "rank_prev"),
                "rank_now": get(r, "rank_now"),
                "delta": get(r, "delta"),
                "tags": tags_out,
                "severity": get(r, "severity") or 1,
                "narrative": (get(r, "narrative") or "").strip(),
            }
            events.append(ev)

        # Sort for nicer UX: date desc, severity desc, abs(delta) desc
        def _abs_int(x: Any) -> int:
            try:
                return abs(int(x))
            except Exception:
                return 0

        events.sort(key=lambda x: (x.get("date", ""), int(x.get("severity") or 0), _abs_int(x.get("delta"))), reverse=True)

        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "count": len(events),
            "events": events,
        }

        out_path = Path(out).expanduser().resolve() if out else default_out_path()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return out_path
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="Export all history")
    ap.add_argument("--days", type=int, default=None, help="Export last N days (default: env EXPORT_DAYS or 180)")
    ap.add_argument("--out", type=str, default=None, help="Output path")
    args = ap.parse_args()

    p = export(mode_all=args.all, days=args.days, out=args.out)
    print(f"✅ Export done: {p}")


if __name__ == "__main__":
    main()
