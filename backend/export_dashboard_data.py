#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "backend" / "charts.db"
OUT_PATH = ROOT / "frontend" / "data" / "merged_events_latest.json"


def load_events(conn, days: int):
    """
    读取最近 N 天的 merged_event
    """
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    rows = conn.execute(
        """
        SELECT
            day,
            platform,
            chart,
            event_type,
            track_name,
            artist,
            prev_rank,
            curr_rank,
            delta,
            score
        FROM merged_event
        WHERE day >= ?
        ORDER BY day DESC, score DESC
        """,
        (since,),
    ).fetchall()

    events = []
    for r in rows:
        events.append(
            {
                "day": r[0],
                "platform": r[1],
                "chart": r[2],
                "event_type": r[3],
                "track_name": r[4],
                "artist": r[5],
                "prev_rank": r[6],
                "curr_rank": r[7],
                "delta": r[8],
                "score": r[9],
            }
        )

    return events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="导出最近 N 天的数据（默认 7 天）",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=str(OUT_PATH),
        help="输出 JSON 路径",
    )
    args = parser.parse_args()

    db_path = DB_PATH
    out_path = Path(args.out)

    if not db_path.exists():
        raise FileNotFoundError(f"charts.db 不存在: {db_path}")

    print(f"[INFO] DB   = {db_path}")
    print(f"[INFO] OUT  = {out_path}")
    print(f"[INFO] DAYS = {args.days}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    events = load_events(conn, args.days)

    days = sorted({e["day"] for e in events}, reverse=True)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": days,
        "count": len(events),
        "events": events,
    }

    # ✅ GitHub Actions 下必须先建目录
    out_path.parent.mkdir(parents=True, exist_ok=True)

    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"✅ 导出完成：{out_path}")
    print(f"   days={len(days)} events={len(events)}")


if __name__ == "__main__":
    main()