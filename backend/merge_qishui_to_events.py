#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将汽水热歌榜合并进主站静态站使用的 events 格式 JSON。

主站（static_site）读取：data/merged_events_latest.json
格式：{ "generated_at": "...", "events": [ { platform, track, artist, date, charts, tags, rank_now, rank_prev, delta, severity, ... } ] }

- 若提供 --events，则在该 events 基础上追加汽水条目
- 若不提供，则仅输出汽水条目（平台标识统一为「抖音(汽水)」）
"""

import json
import argparse
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent

QISHUI_PLATFORM = "抖音(汽水)"
QISHUI_CHART = "热歌榜"


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def qishui_track_to_event(entry: Dict[str, Any], date_str: str, index: int) -> Dict[str, Any]:
    """将汽水 JSON 的一条 track 转成静态站 events 项，缺失字段给默认值。"""
    rank = entry.get("rank") or (index + 1)
    track_name = (entry.get("track_name") or "").strip() or "—"
    artist_raw = entry.get("artist_name_raw") or entry.get("artist") or ""
    artist = (artist_raw.strip() if isinstance(artist_raw, str) else "") or "—"

    return {
        "platform": QISHUI_PLATFORM,
        "platform_name": QISHUI_PLATFORM,
        "track": track_name,
        "track_name": track_name,
        "artist": artist,
        "artist_name_raw": artist,
        "date": date_str,
        "charts": [QISHUI_CHART],
        "tags": ["其他"],
        "rank_now": rank,
        "rank_prev": None,
        "delta": None,
        "severity": 0,
        "id": None,
        "narrative": "",
        "source_event_ids": [],
    }


def run(
    events_path: Path | None,
    qishui_path: Path,
    out_path: Path,
) -> None:
    events: List[Dict[str, Any]] = []
    generated_at = ""

    if events_path and events_path.exists():
        data = load_json(events_path)
        events = list(data.get("events") or [])
        generated_at = data.get("generated_at") or ""

    if not qishui_path.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out = {"generated_at": generated_at or None, "events": events}
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"  [OK] 未合并汽水（文件不存在）-> {out_path}")
        return

    qishui = load_json(qishui_path)
    tracks = qishui.get("tracks") or []
    fetched_at = (qishui.get("fetched_at") or "").strip()
    date_str = fetched_at[:10] if len(fetched_at) >= 10 else ""

    for i, entry in enumerate(tracks):
        ev = qishui_track_to_event(entry, date_str, i)
        events.append(ev)

    if not generated_at and fetched_at:
        generated_at = fetched_at

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = {"generated_at": generated_at, "events": events}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"  [OK] 已合并 {len(tracks)} 条汽水数据 -> {out_path}")


def main():
    parser = argparse.ArgumentParser(description="合并汽水榜单到主站 events JSON")
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="现有 merged_events_latest.json 路径（可选，无则仅输出汽水）",
    )
    parser.add_argument(
        "--qishui",
        type=Path,
        default=PROJECT_ROOT / "data" / "douyin_qishui_latest.json",
        help="汽水最新 JSON",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "static_site" / "data" / "merged_events_latest.json",
        help="输出路径（主站 data/merged_events_latest.json）",
    )
    args = parser.parse_args()
    run(args.events, args.qishui, args.out)


if __name__ == "__main__":
    main()
