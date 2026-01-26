"""
事件合并模块

将同一天内同一首歌的多个事件合并为一条记录，
便于前端展示和 Notion 同步
"""
import os
import re
import sys
import json
import sqlite3
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 确保时区处理模块在路径中
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mdlm_config import db_path, load_env
from timezone_utils import beijing_today_iso, get_target_date

load_env(override=True)

SQLITE_DB_PATH = str(db_path())


def safe_int(x) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\[.*?\]", "", s)
    s = re.sub(r"\s+", " ", s)
    s = s.replace("feat.", "").replace("ft.", "")
    return s.strip()


def ensure_merged_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS merged_event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        merge_date TEXT NOT NULL,
        merge_key TEXT NOT NULL,
        platform_name TEXT,
        track_name TEXT,
        artist_name_raw TEXT,
        tags_json TEXT,
        charts_json TEXT,
        max_severity INTEGER,
        best_rank_prev INTEGER,
        best_rank_now INTEGER,
        best_delta INTEGER,
        best_narrative TEXT,
        source_event_ids_json TEXT,
        max_heat REAL,
        max_digg INTEGER,
        max_collect INTEGER,
        created_at TEXT NOT NULL
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_merged_event_date ON merged_event(merge_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_merged_event_key ON merged_event(merge_key)")

    # 简单迁移逻辑
    try:
        conn.execute("SELECT max_heat FROM merged_event LIMIT 1")
    except sqlite3.OperationalError:
        print("  [merge_events] Migrating: Adding max_heat, max_digg, max_collect to merged_event")
        conn.execute("ALTER TABLE merged_event ADD COLUMN max_heat REAL")
        conn.execute("ALTER TABLE merged_event ADD COLUMN max_digg INTEGER")
        conn.execute("ALTER TABLE merged_event ADD COLUMN max_collect INTEGER")

    conn.commit()


def delete_today_merged(conn: sqlite3.Connection, day: str) -> None:
    conn.execute("DELETE FROM merged_event WHERE merge_date = ?", (day,))
    conn.commit()


def fetch_today_events(conn: sqlite3.Connection, day: str) -> List[Dict[str, Any]]:
    """
    从 event 表读取指定日期的事件
    """
    rows = conn.execute("""
    SELECT id, chart_id, track_platform_id, event_type, severity, detected_at, evidence, narrative
    FROM event
    WHERE substr(detected_at, 1, 10) = ?
    ORDER BY severity DESC, detected_at DESC
    """, (day,)).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        evidence = {}
        try:
            evidence = json.loads(r[6]) if r[6] else {}
        except Exception:
            evidence = {}

        out.append({
            "id": r[0],
            "chart_id": r[1],
            "track_platform_id": r[2],
            "event_type": r[3] or "",
            "severity": safe_int(r[4]) or 1,
            "detected_at": r[5] or "",
            "evidence": evidence if isinstance(evidence, dict) else {},
            "narrative": r[7] or "",
        })
    return out


def pick_best_by_delta(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    选择代表性事件：
    - 优先选择绝对值 delta 最大的
    - 否则选择 severity 最高的
    """
    def score(ev: Dict[str, Any]) -> Tuple[int, int]:
        e = ev.get("evidence") or {}
        d = safe_int(e.get("delta_rank"))
        abs_d = abs(d) if isinstance(d, int) else -1
        sev = safe_int(ev.get("severity")) or 1
        return (abs_d, sev)

    best = events[0]
    best_s = score(best)
    for ev in events[1:]:
        s = score(ev)
        if s > best_s:
            best, best_s = ev, s
    return best


def merge(day: str) -> None:
    """
    合并指定日期的事件
    """
    db_file = Path(SQLITE_DB_PATH).resolve()
    if not db_file.exists():
        raise SystemExit(f"❌ 找不到 SQLite: {db_file}")

    conn = sqlite3.connect(str(db_file))
    try:
        ensure_merged_table(conn)
        delete_today_merged(conn, day)

        events = fetch_today_events(conn, day)
        if not events:
            print(f"⚠️ {day} 的 event 表没有数据，先运行 analyze_events.py")
            return

        # 分组：同一首歌同一天可能同时有 TOP10_STABLE(2) 与 DOMINANT(3)，
        # 如果只按歌名合并会被 max_severity=3 覆盖，导致筛选不到 severity=2
        buckets: Dict[str, List[Dict[str, Any]]] = {}

        for ev in events:
            e = ev.get("evidence") or {}
            platform = (e.get("platform") or "").strip()
            track = (e.get("track_name") or "").strip()
            artist = (e.get("artist") or "").strip()

            base_key = f"{platform}||{normalize_text(track)}||{normalize_text(artist)}"

            et = (ev.get("event_type") or "").strip()
            if et == "TOP10_STABLE":
                group = "STABLE"      # severity=2
            elif et == "DOMINANT":
                group = "DOMINANT"    # severity=3
            elif et == "DAILY_SNAPSHOT":
                group = "SNAPSHOT"    # severity=1
            else:
                group = "OTHER"

            merge_key = f"{base_key}||{group}"
            buckets.setdefault(merge_key, []).append(ev)

        created_at = f"{day}T00:00:00"
        merged_count = 0

        for merge_key, evs in buckets.items():
            best = pick_best_by_delta(evs)
            be = best.get("evidence") or {}

            platform = (be.get("platform") or "").strip()
            track = (be.get("track_name") or "").strip()
            artist = (be.get("artist") or "").strip()

            tags: List[str] = []
            charts: List[str] = []
            source_ids: List[int] = []
            max_sev = 1

            for ev in evs:
                source_ids.append(int(ev["id"]))
                max_sev = max(max_sev, safe_int(ev.get("severity")) or 1)

                e = ev.get("evidence") or {}

                # 1) charts
                ch = (e.get("chart") or "").strip()
                if ch:
                    charts.append(ch)

                # 2) evidence.tags
                t = e.get("tags") or []
                if isinstance(t, list):
                    tags.extend([x for x in t if isinstance(x, str) and x.strip()])

                # 3) robust fallback：如果 evidence.tags 丢了，也强制给 TOP10_STABLE 注入
                et = (ev.get("event_type") or "").strip()
                if et == "TOP10_STABLE":
                    tags.append("Top10稳定")
                    tags.append("无变化")

                # 4) always include event_type as tag
                if et:
                    tags.append(et)

            # 清洗 + 去重（保留顺序）
            cleaned: List[str] = []
            seen = set()
            for t in tags:
                t2 = (t or "").strip()
                if not t2:
                    continue
                if t2 in seen:
                    continue
                seen.add(t2)
                cleaned.append(t2)
            tags = cleaned

            # charts 去重
            charts = sorted(set([c for c in charts if c]))

            best_rank_prev = safe_int(be.get("rank_prev"))
            best_rank_now = safe_int(be.get("rank_now"))
            best_delta = safe_int(be.get("delta_rank"))

            narrative = (best.get("narrative") or "").strip()
            if charts:
                narrative = f"{narrative}（涉及榜单：{' / '.join(charts)}）"

            # 标注分组
            try:
                group = (merge_key.split("||")[-1] or "").strip()
            except Exception:
                group = ""
            if group:
                narrative = f"{narrative}（分组：{group}）"

            conn.execute("""
            INSERT INTO merged_event(
                merge_date, merge_key, platform_name, track_name, artist_name_raw,
                tags_json, charts_json,
                max_severity, best_rank_prev, best_rank_now, best_delta,
                best_narrative, source_event_ids_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                day,
                merge_key,
                platform,
                track,
                artist,
                json.dumps(tags, ensure_ascii=False),
                json.dumps(charts, ensure_ascii=False),
                max_sev,
                best_rank_prev,
                best_rank_now,
                best_delta,
                narrative,
                json.dumps(source_ids, ensure_ascii=False),
                created_at
            ))
            merged_count += 1

        conn.commit()
        print(f"✅ 合并完成：{day} merged_event 写入 {merged_count} 条（由 {len(events)} 条 event 合并）")

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Merge events for a specific day")
    parser.add_argument("--day", "-d", 
                        help="Target day (YYYY-MM-DD). Default: Beijing today or TARGET_DATE env",
                        default=None)
    args = parser.parse_args()
    
    # 确定目标日期
    if args.day:
        target_day = args.day.strip()
    else:
        target_day = get_target_date()
    
    print(f"[merge_events] Processing day: {target_day}")
    merge(target_day)


if __name__ == "__main__":
    main()
