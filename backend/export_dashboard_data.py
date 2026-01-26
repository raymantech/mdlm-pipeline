#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出前端展示用的 JSON 数据

策略说明：
- merged_events_latest.json 只保留最近 N 天的数据（默认7天）
- 完整历史数据保存在 charts.db 数据库中，不会丢失
- 前端只加载最近数据，提升加载速度

Frontend expects:
- data.events: list of items with keys:
  platform (str), charts (list[str]), track (str), artist (str),
  rank_now (int|None), delta (int|None), date (YYYY-MM-DD),
  tags (list[str])  # e.g. ["ENTRY","DROP","SURGE",...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保时区处理模块在路径中
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from timezone_utils import beijing_today, beijing_timestamp, date_range_beijing

DEFAULT_DB = ROOT / "charts.db"


def _json_loads_maybe(s: Any, default):
    if s is None:
        return default
    if isinstance(s, (list, dict)):
        return s
    if not isinstance(s, str):
        return default
    s = s.strip()
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def _ensure_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def _charts_to_names(charts_json: Any) -> List[str]:
    """
    charts_json usually comes from merged_event.charts_json, e.g.:
    - [{"chart_id":..., "chart_name":"热歌榜", ...}, ...]
    - or ["热歌榜", ...]
    """
    charts = _ensure_list(charts_json)
    out: List[str] = []
    for c in charts:
        if c is None:
            continue
        if isinstance(c, str):
            name = c.strip()
            if name:
                out.append(name)
        elif isinstance(c, dict):
            name = (c.get("chart_name") or c.get("name") or "").strip()
            if name:
                out.append(name)
    # de-dup, keep order
    seen = set()
    uniq = []
    for n in out:
        if n in seen:
            continue
        seen.add(n)
        uniq.append(n)
    return uniq


def export_events(db_path: Path, days: int) -> List[Dict[str, Any]]:
    """
    从数据库导出最近 N 天的事件
    
    注意：使用北京时间计算日期范围
    """
    if not db_path.exists():
        raise SystemExit(f"[FATAL] db not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # 使用北京时间计算日期范围
        start_date, end_date = date_range_beijing(days)
        
        print(f"[export] Beijing today: {beijing_today().isoformat()}")
        print(f"[export] Date range: {start_date} ~ {end_date} ({days} days)")
        
        sql = """
        SELECT
          id,
          merge_date,
          platform_name,
          track_name,
          artist_name_raw,
          tags_json,
          charts_json,
          best_rank_prev,
          best_rank_now,
          best_delta,
          max_severity
        FROM merged_event
        WHERE merge_date >= ?
        ORDER BY merge_date DESC, max_severity DESC, id DESC
        """
        rows = conn.execute(sql, (start_date,)).fetchall()

        events: List[Dict[str, Any]] = []
        for r in rows:
            tags = _ensure_list(_json_loads_maybe(r["tags_json"], []))
            charts = _charts_to_names(_json_loads_maybe(r["charts_json"], []))

            item = {
                "platform": r["platform_name"],
                "charts": charts,
                "track": r["track_name"],
                "artist": r["artist_name_raw"],
                "rank_now": r["best_rank_now"],
                "rank_prev": r["best_rank_prev"],
                "delta": r["best_delta"],  # rank_now - rank_prev (负数=上升/变好)
                "date": r["merge_date"],
                "tags": tags,
                # optional fields (frontend will ignore if unused)
                "severity": r["max_severity"],
                "id": r["id"],
            }
            events.append(item)

        return events
    finally:
        conn.close()


def get_date_distribution(events: List[Dict[str, Any]]) -> Dict[str, int]:
    """统计每个日期的事件数量"""
    dist: Dict[str, int] = {}
    for ev in events:
        d = ev.get("date", "unknown")
        dist[d] = dist.get(d, 0) + 1
    return dict(sorted(dist.items()))


def main():
    ap = argparse.ArgumentParser(description="Export dashboard JSON for frontend")
    ap.add_argument("--db", default=str(DEFAULT_DB), 
                    help="Path to charts.db (default: backend/charts.db)")
    ap.add_argument("--days", type=int, 
                    default=int(os.getenv("EXPORT_DAYS", "7")), 
                    help="How many days to include (default: 7)")
    ap.add_argument("--out", 
                    default=str((ROOT.parent / "frontend" / "data" / "merged_events_latest.json").resolve()),
                    help="Output JSON path")
    args = ap.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    events = export_events(db_path=db_path, days=args.days)

    # 打印日期分布统计
    date_dist = get_date_distribution(events)
    print(f"[export] Date distribution:")
    for d, count in date_dist.items():
        print(f"  {d}: {count} events")

    payload = {
        "generated_at": beijing_timestamp(),
        "days": args.days,
        "total_events": len(events),
        "date_distribution": date_dist,
        "events": events,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Exported {len(events)} events -> {out_path}")


if __name__ == "__main__":
    main()
