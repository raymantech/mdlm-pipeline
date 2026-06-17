#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将抖音(汽水)榜单合并进主站 static_site 使用的 merged_events_latest.json。

- 输入：static_site/data/merged_events_latest.json、data/douyin_qishui_latest.json 及归档 douyin_qishui_YYYY-MM-DD.json
- 输出：覆盖 static_site/data/merged_events_latest.json（写入前备份为同目录 .bak）
- 不修改原有平台数据（QQ/网易云/酷狗）；先移除所有 platform=="抖音(汽水)" 再追加新数据（幂等）
- --days N：N=1 用 _latest；N>1 仅从 data/ 挑最近 N 个归档（按文件名日期降序），不把 _latest 当归档
"""

import json
import re
import shutil
import sys
import argparse
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from timezone_utils import beijing_now_iso

PLATFORM_QISHUI = "抖音(汽水)"
QISHUI_CHART = "热歌榜"
SOURCE_QISHUI = "douyin_qishui"
ARCHIVE_PATTERN = re.compile(r"^douyin_qishui_(\d{4}-\d{2}-\d{2})\.json$")


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _artist_from_track(entry: Dict[str, Any], track_name: str) -> str:
    """
    汽水 track 的艺人：优先字段，其次从 track_name 解析（分隔符：' - '、'·'、'/'、'feat.'），否则 "未知"。
    """
    for key in ("artist_name_raw", "artist", "artist_name", "artistName", "singerName"):
        val = entry.get(key)
        if val is None:
            continue
        if isinstance(val, str):
            s = val.strip()
            if s and s != "—":
                return s
        elif isinstance(val, list):
            parts = [str(x).strip() for x in val if x]
            if parts:
                return " / ".join(parts)
    if not track_name or not isinstance(track_name, str):
        return "未知"
    s = track_name.strip()
    # 分隔符：' - '、'·'、'/'、'feat.'（大小写不敏感），取后半段作为艺人
    for sep in (" - ", " · ", "/"):
        if sep in s:
            parts = s.split(sep, 1)
            if len(parts) >= 2:
                right = parts[1].strip()
                if right and len(right) < 80:
                    return right
    if re.search(r"\sfeat\.?\s", s, re.IGNORECASE):
        parts = re.split(r"\sfeat\.?\s", s, 1, flags=re.IGNORECASE)
        if len(parts) >= 2:
            right = parts[1].strip()
            if right and len(right) < 80:
                return right
    return "未知"


def qishui_track_to_event(entry: Dict[str, Any], date_str: str, index: int) -> Dict[str, Any]:
    """汽水榜单一条 track → 一条 event，复用现有 event schema，并设 source=douyin_qishui。"""
    rank = entry.get("rank")
    if rank is None:
        rank = index + 1
    track_name = (entry.get("track_name") or "").strip() or "—"
    artist = _artist_from_track(entry, track_name)

    return {
        "platform": PLATFORM_QISHUI,
        "track": track_name,
        "artist": artist,
        "date": date_str,
        "charts": [QISHUI_CHART],
        "tags": ["抖音热歌"],
        "rank_now": rank,
        "rank_prev": None,
        "delta": None,
        "severity": 0,
        "id": None,
        "narrative": "",
        "source_event_ids": [],
        "source": SOURCE_QISHUI,
    }


def normalize_for_key(s: str) -> str:
    """用于去重 key 的标准化：strip、小写、合并空白。"""
    if not s or not isinstance(s, str):
        return ""
    return " ".join(s.strip().lower().split())


def event_dedup_key(e: Dict[str, Any]) -> Tuple[str, str, str]:
    """去重 key：(date, normalized_track, normalized_artist)，同一天同歌只保留一条。"""
    return (
        (e.get("date") or "").strip(),
        normalize_for_key(e.get("track") or ""),
        normalize_for_key(e.get("artist") or ""),
    )


def collect_date_to_path(data_dir: Path, qishui_latest_path: Path, days: int) -> List[Tuple[str, Path]]:
    """
    收集最近 N 天的 (date, file_path)。
    - N=1：只用 _latest，date 从 fetched_at 取。
    - N>1：仅从 data/ 挑归档 douyin_qishui_YYYY-MM-DD.json，按文件名日期降序取前 N 个，不把 _latest 当归档。
    """
    if days == 1:
        if qishui_latest_path.exists():
            try:
                q = load_json(qishui_latest_path)
                fetched_at = (q.get("fetched_at") or "").strip()
                if len(fetched_at) >= 10:
                    return [(fetched_at[:10], qishui_latest_path)]
            except Exception:
                pass
        return []

    archive_date_to_path: Dict[str, Path] = {}
    for f in data_dir.iterdir():
        if not f.is_file():
            continue
        m = ARCHIVE_PATTERN.match(f.name)
        if m:
            d = m.group(1)
            archive_date_to_path[d] = f
    sorted_dates = sorted(archive_date_to_path.keys(), reverse=True)
    chosen = sorted_dates[:days]
    return [(d, archive_date_to_path[d]) for d in chosen]


def load_qishui_events_for_days(
    data_dir: Path,
    qishui_latest_path: Path,
    days: int,
) -> List[Dict[str, Any]]:
    """
    读取最近 N 天的汽水数据。N=1 用 _latest；N>1 用归档，event.date = 归档文件名中的日期。
    去重：key = (date, normalized_track, normalized_artist)，同一天同歌只保留一条。
    """
    date_paths = collect_date_to_path(data_dir, qishui_latest_path, days)
    seen_keys: set = set()
    out: List[Dict[str, Any]] = []

    for date_str, path in date_paths:
        if not path.exists():
            continue
        try:
            q = load_json(path)
        except Exception:
            continue
        tracks = q.get("tracks") or []
        for i, entry in enumerate(tracks):
            ev = qishui_track_to_event(entry, date_str, i)
            key = event_dedup_key(ev)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append(ev)
    return out


def run(
    site_merged_path: Path,
    qishui_path: Path,
    data_dir: Path,
    out_path: Path,
    days: int,
    backup: bool,
) -> None:
    if not site_merged_path.exists():
        raise FileNotFoundError(f"主站数据不存在: {site_merged_path}")

    data = load_json(site_merged_path)
    events: List[Dict[str, Any]] = list(data.get("events") or [])

    # 保留非「抖音(汽水)」的原有平台数据，不破坏 QQ/网易云/酷狗（幂等：重复运行不会重复追加汽水）
    existing = [e for e in events if (e.get("platform") or "") != PLATFORM_QISHUI]
    n_original = len(existing)

    qishui_events = load_qishui_events_for_days(data_dir, qishui_path, days)

    merged_events = existing + qishui_events
    data["events"] = merged_events
    if "total_events" in data:
        data["total_events"] = len(merged_events)
    data["generated_at"] = beijing_now_iso()

    if backup and out_path.exists():
        bak = out_path.with_suffix(out_path.suffix + ".bak")
        shutil.copy2(out_path, bak)
        print(f"  [备份] {out_path.name} -> {bak.name}")

    save_json(out_path, data)
    print(f"  [OK] 保留原有 {n_original} 条，合并汽水 {len(qishui_events)} 条（最近 {days} 天），共 {len(merged_events)} 条 -> {out_path}")


def main():
    parser = argparse.ArgumentParser(description="将抖音(汽水)榜单合并进主站 merged_events_latest.json")
    parser.add_argument(
        "--site-merged",
        type=Path,
        default=PROJECT_ROOT / "static_site" / "data" / "merged_events_latest.json",
        help="主站现有 merged_events_latest.json",
    )
    parser.add_argument(
        "--qishui",
        type=Path,
        default=PROJECT_ROOT / "data" / "douyin_qishui_latest.json",
        help="汽水榜单 JSON（_latest）",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=PROJECT_ROOT / "data",
        help="data 目录（用于查找 douyin_qishui_YYYY-MM-DD.json）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        metavar="N",
        help="合并最近 N 天：N=1 用 _latest；N>1 仅用归档 douyin_qishui_YYYY-MM-DD.json（按文件名日期降序取前 N 个），默认 1",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出路径（默认覆盖 --site-merged）",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="写入前不备份 .bak",
    )
    args = parser.parse_args()
    if args.days < 1:
        args.days = 1
    out = args.out if args.out is not None else args.site_merged
    run(
        args.site_merged,
        args.qishui,
        args.data_dir,
        out,
        days=args.days,
        backup=not args.no_backup,
    )


if __name__ == "__main__":
    main()
