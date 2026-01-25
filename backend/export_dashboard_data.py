import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict


def root_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_db_path() -> Path:
    env = os.getenv("SQLITE_DB_PATH", "").strip()
    if env:
        p = Path(env)
        return p if p.is_absolute() else (root_dir() / p).resolve()
    return (root_dir() / "backend" / "charts.db").resolve()


def table_cols(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cur.fetchall()]


def pick_first(cols: List[str], cands: List[str]) -> Optional[str]:
    s = set(cols)
    for c in cands:
        if c in s:
            return c
    return None


def day_expr(col: str) -> str:
    if col in ("day","date","dt","d") or col.endswith("_day") or col.endswith("_date"):
        return col
    return f"substr({col},1,10)"


def default_out_path() -> Path:
    root = root_dir()
    p = root / "frontend" / "data"
    if p.exists():
        return (p / "merged_events_latest.json").resolve()
    p2 = root / "backend" / "data"
    p2.mkdir(parents=True, exist_ok=True)
    return (p2 / "merged_events_latest.json").resolve()


def build_field_map(cols: List[str]) -> Dict[str, Optional[str]]:
    return {
        "id": pick_first(cols, ["id","uuid"]),
        "platform": pick_first(cols, ["platform","source","src"]),
        "chart_name": pick_first(cols, ["chart_name","chart","list_name","chartTitle"]),
        "event_type": pick_first(cols, ["event_type","type","event"]),
        "song_id": pick_first(cols, ["song_id","track_id","sid","audio_id"]),
        "song_name": pick_first(cols, ["song_name","track_name","title","song","name"]),
        "artist": pick_first(cols, ["artist","singer","artist_name","author"]),
        "rank_before": pick_first(cols, ["rank_before","rank_prev","before_rank","prev_rank","rank0"]),
        "rank_after": pick_first(cols, ["rank_after","rank_now","after_rank","now_rank","rank1"]),
        "delta": pick_first(cols, ["delta","change","rank_change","diff"]),
        "meta_json": pick_first(cols, ["meta_json","meta","extra_json","raw_json","payload"]),
    }


def export(days: int, out: Optional[str]) -> Path:
    dbp = resolve_db_path()
    if not dbp.exists():
        raise FileNotFoundError(f"DB not found: {dbp}")

    conn = sqlite3.connect(str(dbp))
    try:
        cols = table_cols(conn, "merged_event")
        if not cols:
            raise RuntimeError("merged_event missing or empty schema")

        day_col = pick_first(cols, ["day","date","dt","d","merged_date","merge_date","event_date"])
        time_col = pick_first(cols, ["merged_at","created_at","updated_at","ts","timestamp","merged_time","created_time"])
        col_for_day = day_col or time_col
        if not col_for_day:
            raise RuntimeError("Cannot infer day column from merged_event")

        dsql = day_expr(col_for_day)

        cur = conn.cursor()
        # 取最新 N 个 distinct day
        cur.execute(f"SELECT {dsql} AS d FROM merged_event GROUP BY d ORDER BY d DESC LIMIT ?", (days,))
        day_list = [r[0] for r in cur.fetchall() if r and r[0]]
        if not day_list:
            raise RuntimeError("No merged_event rows to export")

        # 拉取这些天的全量 rows
        placeholders = ",".join(["?"] * len(day_list))
        cur.execute(f"SELECT * FROM merged_event WHERE {dsql} IN ({placeholders})", day_list)
        rows = cur.fetchall()

        idx = {c:i for i,c in enumerate(cols)}
        fmap = build_field_map(cols)

        def get(row, key: str):
            c = fmap.get(key)
            return row[idx[c]] if c and c in idx else None

        items = []
        for row in rows:
            raw_day = row[idx[col_for_day]]
            day = str(raw_day)[:10] if raw_day is not None else None

            meta_raw = get(row,"meta_json")
            meta = {}
            if meta_raw:
                try:
                    meta = json.loads(meta_raw) if isinstance(meta_raw,str) else meta_raw
                except Exception:
                    meta = {"_raw": str(meta_raw)}

            # 输出尽量兼容你 1.0/2.0 前端：events 数组 + day 字段
            items.append({
                "id": get(row,"id"),
                "day": day,
                "platform": get(row,"platform"),
                "chart_name": get(row,"chart_name"),
                "event_type": get(row,"event_type"),
                "song_id": get(row,"song_id"),
                "song_name": get(row,"song_name"),
                "artist": get(row,"artist"),
                "rank_before": get(row,"rank_before"),
                "rank_after": get(row,"rank_after"),
                "delta": get(row,"delta"),
                "meta": meta,
            })

        # 排序：day desc + delta desc
        items.sort(key=lambda x: (x.get("day") or "", x.get("delta") or 0), reverse=True)

        payload = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": f"last_{days}_days",
            "count": len(items),
            "days": sorted(set([x.get("day") for x in items if x.get("day")]), reverse=True),
            "events": items,
        }

        outp = Path(out).expanduser().resolve() if out else default_out_path()
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return outp
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=int(os.getenv("EXPORT_DAYS","7")))
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    p = export(days=max(1,args.days), out=args.out)
    print("✅ Export done:", p)


if __name__ == "__main__":
    main()