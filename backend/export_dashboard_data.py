#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Export dashboard JSON for frontend.

Goals:
1) Output a stable schema that frontend expects:
   - date, platform, chart, track, artist, rank_now, rank_prev, delta_rank,
     type, trend, tags, snapshot_id, chart_id, platform_id, meta, raw
2) Be resilient to DB schema differences (auto-detect columns/tables).
3) Support --latest (backward compatible with run_daily.py passing --latest).

Usage:
  python export_dashboard_data.py --days 7 --out frontend/data/merged_events_latest.json
  python export_dashboard_data.py --latest
"""

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, Set, List, Optional


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "backend" / "charts.db"
DEFAULT_OUT = ROOT / "frontend" / "data" / "merged_events_latest.json"


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def table_cols(conn: sqlite3.Connection, name: str) -> Set[str]:
    if not table_exists(conn, name):
        return set()
    rows = conn.execute(f"PRAGMA table_info({name})").fetchall()
    # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
    return {r[1] for r in rows}


def pick_first(cols: Set[str], candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in cols:
            return c
    return None


def safe_json_load(s: Optional[str]):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def normalize_str(x):
    if x is None:
        return None
    s = str(x).strip()
    return s if s != "" else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB), help="Path to charts.db")
    ap.add_argument("--days", type=int, default=7, help="How many days to export")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Output json path")
    ap.add_argument(
        "--latest",
        action="store_true",
        help="Compatibility flag: export latest data to default path",
    )
    args = ap.parse_args()

    db_path = Path(args.db)
    if args.latest:
        # keep it simple: latest means "recent 7 days" to the default path unless user overrides
        if not args.out:
            args.out = str(DEFAULT_OUT)

    if not db_path.exists():
        raise SystemExit(f"[FATAL] DB not found: {db_path}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # ---- Detect schema
    me_table = "merged_event" if table_exists(conn, "merged_event") else None
    if not me_table:
        raise SystemExit("[FATAL] table merged_event not found in DB")

    me_cols = table_cols(conn, me_table)
    chart_cols = table_cols(conn, "chart")
    plat_cols = table_cols(conn, "platform")
    tp_cols = table_cols(conn, "track_platform")
    track_cols = table_cols(conn, "track")

    # day/date column
    col_day = pick_first(me_cols, ["day", "date", "dt", "created_day"])
    if not col_day:
        # fallback: try snapshot_time
        col_day = pick_first(me_cols, ["created_at", "snapshot_time"])

    # required business columns
    col_event_type = pick_first(me_cols, ["event_type", "type", "event"])
    col_song = pick_first(me_cols, ["song_name", "track_name", "song", "track"])
    col_artist = pick_first(me_cols, ["artist", "artist_name", "singer", "author"])

    col_rank_now = pick_first(me_cols, ["rank_now", "rank", "current_rank"])
    col_rank_prev = pick_first(me_cols, ["rank_prev", "prev_rank", "last_rank"])
    col_delta = pick_first(me_cols, ["delta_rank", "rank_delta", "delta"])

    col_chart_id = pick_first(me_cols, ["chart_id"])
    col_platform_id = pick_first(me_cols, ["platform_id"])
    col_snapshot_id = pick_first(me_cols, ["snapshot_id"])
    col_track_id = pick_first(me_cols, ["track_id"])
    col_track_platform_id = pick_first(me_cols, ["track_platform_id"])

    col_chart_name_me = pick_first(me_cols, ["chart_name", "chart"])
    col_platform_me = pick_first(me_cols, ["platform", "source", "src"])

    col_trend = pick_first(me_cols, ["trend", "direction"])
    col_tags = pick_first(me_cols, ["tags"])
    col_meta = pick_first(me_cols, ["meta", "raw_obj", "raw", "payload"])

    # ---- Build JOINs (best effort)
    joins = []
    # chart join
    can_join_chart = (
        col_chart_id is not None and table_exists(conn, "chart") and "id" in chart_cols
    )
    if can_join_chart:
        joins.append("LEFT JOIN chart c ON c.id = me.chart_id")

    # platform join
    can_join_platform = table_exists(conn, "platform") and "id" in plat_cols and "name" in plat_cols
    if can_join_platform:
        # prefer me.platform_id, otherwise c.platform_id
        if col_platform_id:
            joins.append("LEFT JOIN platform p ON p.id = me.platform_id")
        elif can_join_chart and "platform_id" in chart_cols:
            joins.append("LEFT JOIN platform p ON p.id = c.platform_id")

    # track_platform + track join (optional)
    can_join_tp = col_track_platform_id and table_exists(conn, "track_platform") and "id" in tp_cols
    if can_join_tp:
        joins.append("LEFT JOIN track_platform tp ON tp.id = me.track_platform_id")
    can_join_track = table_exists(conn, "track") and "id" in track_cols
    if can_join_track:
        # best guess: me.track_id or tp.track_id
        if col_track_id:
            joins.append("LEFT JOIN track t ON t.id = me.track_id")
        elif can_join_tp and "track_id" in tp_cols:
            joins.append("LEFT JOIN track t ON t.id = tp.track_id")

    # ---- Select expressions (COALESCE to fill nulls)
    def sel(expr: str, alias: str) -> str:
        return f"{expr} AS {alias}"

    # platform name
    platform_expr_candidates = []
    if col_platform_me:
        platform_expr_candidates.append(f"me.{col_platform_me}")
    if can_join_platform:
        platform_expr_candidates.append("p.name")
    platform_expr = f"COALESCE({', '.join(platform_expr_candidates)})" if platform_expr_candidates else "NULL"

    # chart name
    chart_expr_candidates = []
    if col_chart_name_me:
        chart_expr_candidates.append(f"me.{col_chart_name_me}")
    if can_join_chart and "name" in chart_cols:
        chart_expr_candidates.append("c.name")
    chart_expr = f"COALESCE({', '.join(chart_expr_candidates)})" if chart_expr_candidates else "NULL"

    # track name
    track_expr_candidates = []
    if col_song:
        track_expr_candidates.append(f"me.{col_song}")
    if can_join_track:
        # try common name fields
        for fn in ["name", "track_name", "song_name", "title"]:
            if fn in track_cols:
                track_expr_candidates.append(f"t.{fn}")
                break
    track_expr = f"COALESCE({', '.join(track_expr_candidates)})" if track_expr_candidates else "NULL"

    # artist
    artist_expr_candidates = []
    if col_artist:
        artist_expr_candidates.append(f"me.{col_artist}")
    if can_join_track:
        for fn in ["artist", "artist_name", "singer", "author"]:
            if fn in track_cols:
                artist_expr_candidates.append(f"t.{fn}")
                break
    artist_expr = f"COALESCE({', '.join(artist_expr_candidates)})" if artist_expr_candidates else "NULL"

    # day/date
    day_expr = f"me.{col_day}" if col_day else "NULL"

    # ranks
    rank_now_expr = f"me.{col_rank_now}" if col_rank_now else "NULL"
    rank_prev_expr = f"me.{col_rank_prev}" if col_rank_prev else "NULL"
    delta_expr = f"me.{col_delta}" if col_delta else "NULL"

    event_type_expr = f"me.{col_event_type}" if col_event_type else "NULL"
    trend_expr = f"me.{col_trend}" if col_trend else "NULL"
    tags_expr = f"me.{col_tags}" if col_tags else "NULL"
    meta_expr = f"me.{col_meta}" if col_meta else "NULL"

    snapshot_expr = f"me.{col_snapshot_id}" if col_snapshot_id else "NULL"
    chart_id_expr = f"me.{col_chart_id}" if col_chart_id else "NULL"
    platform_id_expr = f"me.{col_platform_id}" if col_platform_id else "NULL"

    # ---- Time filter
    # day is stored as "YYYY-MM-DD" typically. We'll filter by date() when possible.
    # Use SQLite date() to compare strings safely.
    where_clause = ""
    params = []
    if col_day:
        where_clause = f"WHERE date({day_expr}) >= date('now', ?)"
        params = [f"-{int(args.days)} day"]

    sql = f"""
    SELECT
      {sel(day_expr, "day")},
      {sel(platform_expr, "platform")},
      {sel(chart_expr, "chart_name")},
      {sel(track_expr, "track_name")},
      {sel(artist_expr, "artist_name")},
      {sel(event_type_expr, "event_type")},
      {sel(rank_now_expr, "rank_now")},
      {sel(rank_prev_expr, "rank_prev")},
      {sel(delta_expr, "delta_rank")},
      {sel(trend_expr, "trend")},
      {sel(tags_expr, "tags")},
      {sel(snapshot_expr, "snapshot_id")},
      {sel(chart_id_expr, "chart_id")},
      {sel(platform_id_expr, "platform_id")},
      {sel(meta_expr, "meta")}
    FROM merged_event me
    {' '.join(joins)}
    {where_clause}
    ORDER BY date({day_expr}) DESC
    """

    rows = conn.execute(sql, params).fetchall()

    # ---- Build output: frontend-friendly stable schema
    events = []
    days = set()

    for r in rows:
        day = normalize_str(r["day"])
        days.add(day) if day else None

        # tags: could be json array or string
        tags_val = r["tags"]
        tags_json = safe_json_load(tags_val) if isinstance(tags_val, str) else None
        if tags_json is None and tags_val is not None:
            # keep as string
            tags_json = tags_val

        meta_val = r["meta"]
        meta_json = safe_json_load(meta_val) if isinstance(meta_val, str) else meta_val

        item = {
            # keep both day + date for compatibility
            "day": day,
            "date": day,

            # frontend expects these names
            "platform": normalize_str(r["platform"]) or "未知平台",
            "chart": normalize_str(r["chart_name"]) or "未知榜单",
            "track": normalize_str(r["track_name"]) or "未知歌曲",
            "artist": normalize_str(r["artist_name"]) or "未知艺人",

            "rank_now": r["rank_now"],
            "rank_prev": r["rank_prev"],
            "delta_rank": r["delta_rank"],

            "type": normalize_str(r["event_type"]) or "其他",
            "event_type": normalize_str(r["event_type"]) or "其他",

            "trend": normalize_str(r["trend"]) or "-",   # frontend常用 "-" 表示无趋势
            "tags": tags_json if tags_json is not None else [],

            # ids and raw fields (useful for debug)
            "snapshot_id": r["snapshot_id"],
            "chart_id": r["chart_id"],
            "platform_id": r["platform_id"],
            "meta": meta_json,
        }
        events.append(item)

    payload = {
        "mode": "latest" if args.latest else "range",
        "count": len(events),
        "days": sorted([d for d in days if d], reverse=True),
        "events": events,
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] export -> {out_path} (events={len(events)})")


if __name__ == "__main__":
    main()