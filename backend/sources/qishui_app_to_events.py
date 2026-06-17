#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
汽水 App（抖音侧）OCR 榜单 → 标准 events

- 读取 data/qishui_app_ocr_latest.json
- 生成 data/events_qishui_latest.json（events 数组）
- 每条 event 含 date, platform=DOUYIN_QISHUI, source, chart=hot|new, rank, track_name, artist_name,
  event_type=DOMINANT|NEW, fingerprint, ts(ISO8601)；与 merged_events 字段风格一致，便于后续合并。
"""

import json
import re
import argparse
from pathlib import Path
from typing import Any, Dict, List

# 脚本在 backend/sources/ 下，项目根目录为 backend 的上一级
ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent.parent

PLATFORM = "DOUYIN_QISHUI"
SOURCE = "qishui_app_screenshot_ocr_tencent"


def _normalize_text(s: str) -> str:
    """strip、多空格合并、去掉行首 rank 数字残留（如 "1. 歌名" -> "歌名"）。"""
    if not s or not isinstance(s, str):
        return ""
    t = s.strip()
    t = re.sub(r"\s+", " ", t)
    # 去掉行首 1. / 1) / 1、/ 1: 等
    t = re.sub(r"^\s*\d{1,2}\s*[\.\)\、\:]+\s*", "", t)
    return t.strip()


def _fingerprint(track: str, artist: str) -> str:
    """对 track/artist 做 normalize 后拼接，用于去重/标识。"""
    a = _normalize_text(track or "").lower()
    b = _normalize_text(artist or "").lower()
    return f"{a}|{b}"


def _ts_iso8601() -> str:
    """当前时间 ISO8601（北京时间）。"""
    try:
        import sys
        sys.path.insert(0, str(ROOT.parent))
        from timezone_utils import beijing_now_iso
        return beijing_now_iso()
    except Exception:
        from datetime import datetime, timezone, timedelta
        return datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0).isoformat()


def _entry_to_event(
    entry: Dict[str, Any],
    chart: str,
    date_str: str,
    event_type: str,
) -> Dict[str, Any]:
    rank = entry.get("rank")
    if rank is None and isinstance(entry.get("rank"), str):
        try:
            rank = int(re.sub(r"\D", "", str(entry["rank"])))
        except (ValueError, TypeError):
            rank = 0
    track_name = _normalize_text(entry.get("track_name") or "")
    artist_name = _normalize_text(entry.get("artist_name") or "")
    if not track_name:
        track_name = "—"
    return {
        "date": date_str[:10] if date_str else "",
        "platform": PLATFORM,
        "source": SOURCE,
        "chart": chart,
        "rank": rank,
        "track_name": track_name,
        "artist_name": artist_name,
        "event_type": event_type,
        "fingerprint": _fingerprint(track_name, artist_name),
        "ts": _ts_iso8601(),
        # 与 merged_events 风格一致，便于 export 侧 normalize
        "track": track_name,
        "artist": artist_name,
        "charts": [chart],
        "tags": [event_type],
        "rank_now": rank,
        "rank_prev": None,
        "delta": None,
        "severity": 1,
    }


def run(ocr_path: Path, out_path: Path) -> int:
    """读取 OCR JSON，生成 events 并写入 out_path。返回事件条数。"""
    if not ocr_path.exists():
        print(f"[WARN] OCR 文件不存在: {ocr_path}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"events": []}, f, ensure_ascii=False, indent=2)
        return 0

    with open(ocr_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    date_str = (data.get("date") or "").strip()[:10]
    if not date_str and data.get("generated_at"):
        date_str = str(data.get("generated_at", ""))[:10]

    charts_data = data.get("charts") or {}
    hot = charts_data.get("hot") or []
    new = charts_data.get("new") or []

    events: List[Dict[str, Any]] = []
    for entry in hot:
        if not isinstance(entry, dict):
            continue
        events.append(_entry_to_event(entry, "hot", date_str, "DOMINANT"))
    for entry in new:
        if not isinstance(entry, dict):
            continue
        events.append(_entry_to_event(entry, "new", date_str, "NEW"))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"events": events}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"  [OK] 汽水 App → events: {len(events)} 条 -> {out_path}")
    return len(events)


def main():
    parser = argparse.ArgumentParser(description="汽水 App OCR 榜单 → events_qishui_latest.json")
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=PROJECT_ROOT / "data" / "qishui_app_ocr_latest.json",
        help="OCR 输出路径",
    )
    parser.add_argument(
        "--out", "-o",
        type=Path,
        default=PROJECT_ROOT / "data" / "events_qishui_latest.json",
        help="输出 events JSON 路径",
    )
    args = parser.parse_args()
    run(args.input, args.out)


if __name__ == "__main__":
    main()
