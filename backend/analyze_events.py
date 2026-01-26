# FULL_PLATFORM_ANALYZER_V2_1 (daily_summary compatible)
import os
import sys
import json
import sqlite3
import argparse
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 确保时区处理模块在路径中
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from mdlm_config import db_path, load_env
from timezone_utils import (
    beijing_now_iso, beijing_today_iso, beijing_today,
    detected_at_for_day, get_target_date
)

load_env(override=True)

SQLITE_DB_PATH = str(db_path())

EVENT_DELTA_THRESHOLD = int(os.getenv("EVENT_DELTA_THRESHOLD", "10"))
EVENT_TOPK_FOR_EVENTS = int(os.getenv("EVENT_TOPK_FOR_EVENTS", "100"))


def iso_day(d: date) -> str:
    return d.isoformat()


def now_iso() -> str:
    """使用北京时间"""
    return beijing_now_iso()


def detected_iso(day: str) -> str:
    """Return an ISO timestamp whose date-part equals the target analysis day.

    We intentionally *do not* use now_iso() here, because backfill/merge flows
    query events by day using: substr(detected_at,1,10)=<day>.
    
    使用北京时间确保时区一致性。
    """
    return detected_at_for_day(day)


def safe_int(x) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def make_track_key(track: str, artist: str) -> str:
    return f"{(track or '').strip()}||{(artist or '').strip()}"


def stable_track_platform_id(platform: str, chart: str, track: str, artist: str) -> int:
    s = f"{platform}||{chart}||{track}||{artist}"
    return int(zlib.crc32(s.encode("utf-8")) & 0x7FFFFFFF)


def detect_chart_entry_columns(conn: sqlite3.Connection) -> Dict[str, Optional[str]]:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(chart_entry)").fetchall()]

    def pick(*cands):
        for c in cands:
            if c in cols:
                return c
        return None

    return {
        "rank": pick("rank"),
        "track_name": pick("track_name", "song_name", "name", "title"),
        "artist": pick("artist", "artist_name", "artist_name_raw", "singer_name", "singer"),
    }


def get_latest_snapshot_id_for_day(conn: sqlite3.Connection, chart_id: int, day: str) -> Optional[int]:
    row = conn.execute(
        """
        SELECT MAX(id)
        FROM chart_snapshot
        WHERE chart_id = ?
          AND substr(captured_at, 1, 10) = ?
        """,
        (chart_id, day),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def fetch_snapshot_entries(
    conn: sqlite3.Connection,
    snapshot_id: int,
    colmap: Dict[str, Optional[str]],
    top_n: int,
) -> List[Dict[str, Any]]:
    rank_col = colmap["rank"]
    track_col = colmap["track_name"]
    artist_col = colmap["artist"]

    if not rank_col or not track_col:
        raise RuntimeError("chart_entry 缺少 rank 或 track_name 列，无法分析。")

    select_cols = [f"{rank_col} as rank", f"{track_col} as track_name"]
    if artist_col:
        select_cols.append(f"{artist_col} as artist")
    else:
        select_cols.append("NULL as artist")

    sql = f"""
    SELECT {", ".join(select_cols)}
    FROM chart_entry
    WHERE snapshot_id = ?
      AND {rank_col} <= ?
    ORDER BY {rank_col} ASC
    """
    rows = conn.execute(sql, (snapshot_id, top_n)).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "rank": safe_int(r[0]),
                "track_name": (r[1] or "").strip(),
                "artist": (r[2] or "").strip() if r[2] else "",
            }
        )
    return out


def ensure_event_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chart_id INTEGER NOT NULL,
            track_platform_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            severity INTEGER NOT NULL,
            detected_at TEXT NOT NULL,
            evidence TEXT,
            narrative TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_detected_at ON event(detected_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_chart_id ON event(chart_id)")
    conn.commit()


def ensure_daily_summary_table(conn: sqlite3.Connection) -> None:
    """
    兼容你历史库的 daily_summary：
    - 允许列名是 summary_text 或 text（两种都兼容）
    - 如果两者都没有，则自动补一个 summary_text
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary_date TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_summary_date ON daily_summary(summary_date)")
    conn.commit()

    cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_summary)").fetchall()]

    # 关键：正文列兼容
    if "summary_text" not in cols and "text" not in cols:
        conn.execute("ALTER TABLE daily_summary ADD COLUMN summary_text TEXT")
        conn.commit()

    # 其他统计列（可选）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_summary)").fetchall()]
    if "threshold" not in cols:
        conn.execute("ALTER TABLE daily_summary ADD COLUMN threshold INTEGER")
    if "total_charts" not in cols:
        conn.execute("ALTER TABLE daily_summary ADD COLUMN total_charts INTEGER")
    if "total_events" not in cols:
        conn.execute("ALTER TABLE daily_summary ADD COLUMN total_events INTEGER")
    conn.commit()


def clear_today_events(conn: sqlite3.Connection, day: str) -> None:
    conn.execute("DELETE FROM event WHERE substr(detected_at,1,10)=?", (day,))
    conn.commit()


def upsert_daily_summary(
    conn: sqlite3.Connection,
    day: str,
    threshold: int,
    total_charts: int,
    total_events: int,
    text: str,
) -> None:
    conn.execute("DELETE FROM daily_summary WHERE summary_date=?", (day,))

    cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_summary)").fetchall()]

    payload: Dict[str, Any] = {
        "summary_date": day,
        "created_at": now_iso(),
    }

    # ✅ 正文列：优先 summary_text，否则写 text
    if "summary_text" in cols:
        payload["summary_text"] = text
    elif "text" in cols:
        payload["text"] = text
    else:
        # 理论不会发生，因为 ensure_daily_summary_table 已补齐
        payload["summary_text"] = text

    if "threshold" in cols:
        payload["threshold"] = threshold
    if "total_charts" in cols:
        payload["total_charts"] = total_charts
    if "total_events" in cols:
        payload["total_events"] = total_events

    keys = ", ".join(payload.keys())
    qs = ", ".join(["?"] * len(payload))
    conn.execute(f"INSERT INTO daily_summary({keys}) VALUES ({qs})", tuple(payload.values()))
    conn.commit()


@dataclass
class EventRow:
    chart_id: int
    track_platform_id: int
    event_type: str
    severity: int
    evidence: Dict[str, Any]
    narrative: str


def insert_event(conn: sqlite3.Connection, ev: EventRow, day: str) -> None:
    conn.execute(
        """
        INSERT INTO event(chart_id, track_platform_id, event_type, severity, detected_at, evidence, narrative)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ev.chart_id,
            ev.track_platform_id,
            ev.event_type,
            ev.severity,
            detected_iso(day),
            json.dumps(ev.evidence, ensure_ascii=False),
            ev.narrative,
        ),
    )


def analyze_top10_behavior(
    conn: sqlite3.Connection,
    chart_id: int,
    platform_name: str,
    chart_name: str,
    today: str,
    today_entries: List[Dict[str, Any]],
    colmap: Dict[str, Optional[str]],
) -> List[EventRow]:
    out: List[EventRow] = []

    d0 = date.fromisoformat(today)
    days = [iso_day(d0 - timedelta(days=i)) for i in range(0, 3)]
    snap_ids: List[Optional[int]] = [get_latest_snapshot_id_for_day(conn, chart_id, d) for d in days]
    if snap_ids[0] is None:
        return out

    y_sid = snap_ids[1]
    y_map: Dict[str, Dict[str, Any]] = {}
    if y_sid:
        y_entries = fetch_snapshot_entries(conn, y_sid, colmap, 10)
        y_map = {make_track_key(x["track_name"], x["artist"]): x for x in y_entries}

    top10_today = [x for x in today_entries if (x.get("rank") is not None and x["rank"] <= 10)]
    top10_today_map = {make_track_key(x["track_name"], x["artist"]): x for x in top10_today}

    for k, t in top10_today_map.items():
        if k in y_map:
            r_now = t["rank"]
            r_prev = y_map[k]["rank"]
            if r_now is not None and r_prev is not None and r_now == r_prev:
                tid = stable_track_platform_id(platform_name, chart_name, t["track_name"], t["artist"])
                out.append(
                    EventRow(
                        chart_id=chart_id,
                        track_platform_id=tid,
                        event_type="TOP10_STABLE",
                        severity=2,
                        evidence={
                            "platform": platform_name,
                            "chart": chart_name,
                            "track_name": t["track_name"],
                            "artist": t["artist"],
                            "rank_prev": r_prev,
                            "rank_now": r_now,
                            "delta_rank": 0,
                            "tags": ["Top10稳定", "无变化"],
                        },
                        narrative=f"{platform_name}-{chart_name}：Top10稳定（#{r_now}）{t['track_name']}",
                    )
                )

    if all(sid is not None for sid in snap_ids):
        maps_3: List[Dict[str, Dict[str, Any]]] = []
        for sid in snap_ids:
            entries = fetch_snapshot_entries(conn, int(sid), colmap, 10)
            maps_3.append({make_track_key(x["track_name"], x["artist"]): x for x in entries})

        common_keys = set(maps_3[0].keys()) & set(maps_3[1].keys()) & set(maps_3[2].keys())
        for k in common_keys:
            t = maps_3[0][k]
            r0 = maps_3[0][k]["rank"]
            r1 = maps_3[1][k]["rank"]
            r2 = maps_3[2][k]["rank"]
            tid = stable_track_platform_id(platform_name, chart_name, t["track_name"], t["artist"])
            out.append(
                EventRow(
                    chart_id=chart_id,
                    track_platform_id=tid,
                    event_type="DOMINANT",
                    severity=3,
                    evidence={
                        "platform": platform_name,
                        "chart": chart_name,
                        "track_name": t["track_name"],
                        "artist": t["artist"],
                        "rank_prev": r1,
                        "rank_now": r0,
                        "delta_rank": (r0 - r1) if (r0 is not None and r1 is not None) else None,
                        "tags": ["连续3天Top10"],
                        "dominant_days": days,
                        "ranks_last3": [r0, r1, r2],
                    },
                    narrative=f"{platform_name}-{chart_name}：连续3天Top10（今日#{r0}，昨日#{r1}，前日#{r2}）{t['track_name']}",
                )
            )

    return out


def analyze_chart(
    conn: sqlite3.Connection,
    chart_id: int,
    platform_name: str,
    chart_name: str,
    today: str,
    yesterday: str,
    colmap: Dict[str, Optional[str]],
) -> List[EventRow]:
    sid_today = get_latest_snapshot_id_for_day(conn, chart_id, today)
    sid_yest = get_latest_snapshot_id_for_day(conn, chart_id, yesterday)

    if not sid_today:
        return []

    today_entries = fetch_snapshot_entries(conn, sid_today, colmap, EVENT_TOPK_FOR_EVENTS)

    if not sid_yest:
        return analyze_top10_behavior(conn, chart_id, platform_name, chart_name, today, today_entries, colmap)

    yest_entries = fetch_snapshot_entries(conn, sid_yest, colmap, EVENT_TOPK_FOR_EVENTS)
    today_map = {make_track_key(x["track_name"], x["artist"]): x for x in today_entries}
    yest_map = {make_track_key(x["track_name"], x["artist"]): x for x in yest_entries}

    events: List[EventRow] = []

    for k, t in today_map.items():
        if k not in yest_map:
            rank_now = t["rank"]
            sev = 3 if rank_now is not None and rank_now <= 20 else 2
            tid = stable_track_platform_id(platform_name, chart_name, t["track_name"], t["artist"])
            events.append(
                EventRow(
                    chart_id=chart_id,
                    track_platform_id=tid,
                    event_type="ENTRY",
                    severity=sev,
                    evidence={
                        "platform": platform_name,
                        "chart": chart_name,
                        "track_name": t["track_name"],
                        "artist": t["artist"],
                        "rank_prev": None,
                        "rank_now": rank_now,
                        "delta_rank": None,
                        "tags": ["新进榜"],
                    },
                    narrative=f"{platform_name}-{chart_name}：新进榜 #{rank_now} {t['track_name']}",
                )
            )

    for k, y in yest_map.items():
        if k not in today_map:
            rank_prev = y["rank"]
            tid = stable_track_platform_id(platform_name, chart_name, y["track_name"], y["artist"])
            events.append(
                EventRow(
                    chart_id=chart_id,
                    track_platform_id=tid,
                    event_type="EXIT",
                    severity=3,
                    evidence={
                        "platform": platform_name,
                        "chart": chart_name,
                        "track_name": y["track_name"],
                        "artist": y["artist"],
                        "rank_prev": rank_prev,
                        "rank_now": None,
                        "delta_rank": None,
                        "tags": ["掉出榜"],
                    },
                    narrative=f"{platform_name}-{chart_name}：掉出榜（昨日#{rank_prev}）{y['track_name']}",
                )
            )

    thr = EVENT_DELTA_THRESHOLD
    for k, t in today_map.items():
        if k not in yest_map:
            continue
        rank_now = t["rank"]
        rank_prev = yest_map[k]["rank"]
        if rank_now is None or rank_prev is None:
            continue
        delta = rank_now - rank_prev
        if abs(delta) < thr:
            continue

        tid = stable_track_platform_id(platform_name, chart_name, t["track_name"], t["artist"])
        if delta <= -thr:
            events.append(
                EventRow(
                    chart_id=chart_id,
                    track_platform_id=tid,
                    event_type="SURGE",
                    severity=3,
                    evidence={
                        "platform": platform_name,
                        "chart": chart_name,
                        "track_name": t["track_name"],
                        "artist": t["artist"],
                        "rank_prev": rank_prev,
                        "rank_now": rank_now,
                        "delta_rank": delta,
                        "tags": ["暴涨"],
                    },
                    narrative=f"{platform_name}-{chart_name}：暴涨 {rank_prev}→{rank_now}（{delta}）{t['track_name']}",
                )
            )
        elif delta >= thr:
            events.append(
                EventRow(
                    chart_id=chart_id,
                    track_platform_id=tid,
                    event_type="DROP",
                    severity=3,
                    evidence={
                        "platform": platform_name,
                        "chart": chart_name,
                        "track_name": t["track_name"],
                        "artist": t["artist"],
                        "rank_prev": rank_prev,
                        "rank_now": rank_now,
                        "delta_rank": delta,
                        "tags": ["暴跌"],
                    },
                    narrative=f"{platform_name}-{chart_name}：暴跌 {rank_prev}→{rank_now}（+{delta}）{t['track_name']}",
                )
            )

    events.extend(analyze_top10_behavior(conn, chart_id, platform_name, chart_name, today, today_entries, colmap))
    return events


def build_daily_summary(day: str, threshold: int, charts: List[Tuple[int, str, str]], events: List[EventRow]) -> str:
    by_type: Dict[str, int] = {}
    by_platform: Dict[str, int] = {}
    for e in events:
        by_type[e.event_type] = by_type.get(e.event_type, 0) + 1
        p = (e.evidence or {}).get("platform", "")
        if p:
            by_platform[p] = by_platform.get(p, 0) + 1

    platforms = " / ".join([f"{k}:{v}" for k, v in sorted(by_platform.items(), key=lambda x: (-x[1], x[0]))]) or "无"
    return "\n".join(
        [
            f"{day} 榜单监测摘要（阈值：名次变化≥{threshold}）",
            f"- 覆盖榜单：{len(charts)} 个；今日产出事件：{len(events)} 条",
            f"- 平台分布：{platforms}",
            f"- 新进榜：{by_type.get('ENTRY', 0)}；掉出榜：{by_type.get('EXIT', 0)}；暴涨：{by_type.get('SURGE', 0)}；暴跌：{by_type.get('DROP', 0)}；Top10稳定：{by_type.get('TOP10_STABLE', 0)}；连续3天Top10：{by_type.get('DOMINANT', 0)}",
        ]
    )


def main() -> None:
    db_path = Path(SQLITE_DB_PATH).resolve()
    if not db_path.exists():
        raise SystemExit(f"❌ 找不到 SQLite: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        ensure_event_table(conn)
        ensure_daily_summary_table(conn)

        # ---- choose analysis day ----
        parser = argparse.ArgumentParser(description="Analyze events from chart snapshots.")
        parser.add_argument("--day", help="Target day (YYYY-MM-DD). Default: latest snapshot day.", default=None)
        args, _ = parser.parse_known_args()

        def _latest_snapshot_day() -> str:
            row = conn.execute("SELECT max(substr(captured_at,1,10)) FROM chart_snapshot").fetchone()
            return (row[0] or beijing_today_iso())

        def _prev_snapshot_day(day: str) -> str:
            row = conn.execute(
                "SELECT max(substr(captured_at,1,10)) FROM chart_snapshot WHERE substr(captured_at,1,10) < ?",
                (day,),
            ).fetchone()
            return row[0] or iso_day(date.fromisoformat(day) - timedelta(days=1))

        # 优先使用命令行参数，其次使用环境变量 TARGET_DATE，最后使用数据库中最新快照日期
        if isinstance(args.day, str) and args.day.strip():
            today = args.day.strip()
        else:
            # 尝试从环境变量获取
            env_date = get_target_date(default=None)
            if env_date:
                today = env_date
            else:
                today = _latest_snapshot_day()
        yesterday = _prev_snapshot_day(today)

        colmap = detect_chart_entry_columns(conn)

        print("✅ 事件分析开始")
        clear_today_events(conn, today)

        charts = conn.execute(
            """
            SELECT c.id, c.name, p.name
            FROM chart c
            JOIN platform p ON c.platform_id = p.id
            ORDER BY p.name, c.name
            """
        ).fetchall()

        print(f"== charts loaded: {len(charts)} ==")
        cnt: Dict[str, int] = {}
        for _, _, pn in charts:
            cnt[str(pn)] = cnt.get(str(pn), 0) + 1
        print("== charts by platform ==")
        for k in sorted(cnt.keys()):
            print(f"{k} : {cnt[k]}")

        all_events: List[EventRow] = []
        for chart_id, chart_name, platform_name in charts:
            evs = analyze_chart(
                conn=conn,
                chart_id=int(chart_id),
                platform_name=str(platform_name),
                chart_name=str(chart_name),
                today=today,
                yesterday=yesterday,
                colmap=colmap,
            )
            all_events.extend(evs)

        for ev in all_events:
            insert_event(conn, ev, today)
        conn.commit()

        summary_text = build_daily_summary(today, EVENT_DELTA_THRESHOLD, charts, all_events)
        upsert_daily_summary(conn, today, EVENT_DELTA_THRESHOLD, len(charts), len(all_events), summary_text)

        print("✅ 事件分析完成")
        print(summary_text)

    finally:
        conn.close()


if __name__ == "__main__":
    main()