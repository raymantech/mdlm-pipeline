#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将汽水热歌榜数据合并进主站读取的「最新榜单 JSON」。

- 读取 backend/test_merged_result.json（或指定路径）
- 读取 data/douyin_qishui_latest.json
- 为每条记录补充统一字段 platform（前端显示与筛选用）
- 汽水条目 platform='抖音(汽水)'，其余沿用 mainstream_platform
- 输出到 frontend/public/merged_latest.json（主站可改为读取此文件）
"""

import json
import argparse
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent

# 主站统一显示名
QISHUI_PLATFORM_DISPLAY = "抖音(汽水)"


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_qishui_track(entry: Dict[str, Any], index: int) -> Dict[str, Any]:
    """将汽水 JSON 的一条 track 转成与主站 all_tracks 一致的结构，缺失字段给默认值。"""
    rank = entry.get("rank") or (index + 1)
    track_name = (entry.get("track_name") or "").strip() or "—"
    artist_raw = entry.get("artist_name_raw") or entry.get("artist") or ""
    artist = (artist_raw.strip() if isinstance(artist_raw, str) else "") or ""
    heat = entry.get("heat")
    if heat is None:
        heat = 0
    try:
        heat = float(heat)
    except (TypeError, ValueError):
        heat = 0

    return {
        "track_name": track_name,
        "artist": artist,
        "douyin_rank": rank,
        "douyin_heat": heat,
        "mainstream_rank": None,
        "mainstream_platform": QISHUI_PLATFORM_DISPLAY,
        "platform": QISHUI_PLATFORM_DISPLAY,
        "gap_score": None,
        "is_dark_horse": False,
        "discovery_time": entry.get("fetched_at") or "",
        "match_key": track_name,
        "douyin_digg": 0,
        "douyin_collect": 0,
        "heat": heat,
        "rank": rank,
    }


def ensure_platform(t: Dict[str, Any]) -> None:
    """为已有条目补充 platform 字段（与 mainstream_platform 一致，便于前端筛选）。"""
    if "platform" in t:
        return
    p = (t.get("mainstream_platform") or "").strip()
    t["platform"] = p if p else "未上榜"


def merge(
    merged_path: Path,
    qishui_path: Path,
    out_path: Path,
) -> None:
    merged = load_json(merged_path)

    # 为现有 dark_horses / all_tracks 补充 platform
    for key in ("dark_horses", "all_tracks"):
        arr = merged.get(key)
        if not isinstance(arr, list):
            continue
        for t in arr:
            if isinstance(t, dict):
                ensure_platform(t)

    # 加载汽水数据
    if not qishui_path.exists():
        print(f"  [WARN] 汽水文件不存在，跳过合并: {qishui_path}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"  [OK] 已写入（未合并汽水）: {out_path}")
        return

    qishui = load_json(qishui_path)
    tracks = qishui.get("tracks") or []
    if not tracks:
        print(f"  [WARN] 汽水文件无 tracks，跳过合并")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"  [OK] 已写入: {out_path}")
        return

    # 汽水条目统一 platform 显示名（若源里有汽水音乐等也统一成抖音(汽水)）
    qishui_normalized = []
    for i, entry in enumerate(tracks):
        row = normalize_qishui_track(entry, i)
        row["platform"] = QISHUI_PLATFORM_DISPLAY
        qishui_normalized.append(row)

    # 合并进 all_tracks
    all_tracks = list(merged.get("all_tracks") or [])
    all_tracks.extend(qishui_normalized)
    merged["all_tracks"] = all_tracks

    # 可选：在 summary 里记录汽水条数
    summary = merged.get("summary") or {}
    summary["qishui_tracks_count"] = len(qishui_normalized)
    merged["summary"] = summary

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"  [OK] 已合并 {len(qishui_normalized)} 条汽水数据 -> {out_path}")


def main():
    parser = argparse.ArgumentParser(description="合并汽水榜单到主站 JSON")
    parser.add_argument(
        "--merged",
        type=Path,
        default=ROOT / "test_merged_result.json",
        help="主站当前读取的 merged JSON",
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
        default=PROJECT_ROOT / "frontend" / "public" / "merged_latest.json",
        help="输出路径（主站读取此文件）",
    )
    args = parser.parse_args()

    merge(args.merged, args.qishui, args.out)


if __name__ == "__main__":
    main()
