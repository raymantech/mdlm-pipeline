#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地一键净化 + 合并汽水 + 输出线上用 JSON

输入：inbox/github_site/merged_events_latest.json（从 GitHub 下载）
输出：out/merged_events_latest.json（已净化掉出榜 + 合并汽水 4 榜 + 保留最近 7 天）
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 复用 merge_qishui_app_ocr 的逻辑
from merge_qishui_app_ocr_into_site_data import (
    PLATFORM,
    SOURCE,
    load_json,
    save_json,
    item_to_event,
    rerank_search_events,
    dedupe_search_hard_only,
    dedupe_qishui_events,
    prune_events_last_n_days,
    _is_qishui_ocr,
    _clean_qishui_tags,
)

# 平台别名 -> 统一名
PLATFORM_ALIASES = {
    "抖音/汽水": PLATFORM,
    "抖音汽水": PLATFORM,
    "抖音汽水OCR": PLATFORM,
    "抖音OCR": PLATFORM,
    "汽水OCR": PLATFORM,
}


def _is_qishui_like(e: Dict[str, Any]) -> bool:
    """汽水相关事件（含无 source 的 base 数据）。"""
    p = (e.get("platform") or "").strip()
    if p == PLATFORM or p in PLATFORM_ALIASES:
        return True
    tags = e.get("tags") or []
    return any("汽水" in str(t) for t in tags) if isinstance(tags, list) else False


def _is_drop_event(e: Dict[str, Any]) -> bool:
    """charts/tags 任一命中「掉出榜」"""
    for c in (e.get("charts") or []):
        if isinstance(c, str) and "掉出榜" in c:
            return True
    for t in (e.get("tags") or []):
        if isinstance(t, str) and "掉出榜" in t:
            return True
    for k in ("type", "mTag", "_mTag"):
        v = e.get(k)
        if isinstance(v, str) and "掉出榜" in v:
            return True
    return False


def _is_daily_snapshot_event(e: Dict[str, Any]) -> bool:
    """charts 含「每日快照」"""
    for c in (e.get("charts") or []):
        if isinstance(c, str) and "每日快照" in c:
            return True
    return False


def _normalize_platform_in_place(events: List[Dict[str, Any]]) -> Dict[str, int]:
    """平台别名统一为 抖音(汽水)，返回各别名修复条数。"""
    counts: Dict[str, int] = {}
    for e in events:
        p = (e.get("platform") or "").strip()
        if p in PLATFORM_ALIASES and p != PLATFORM:
            e["platform"] = PLATFORM
            counts[p] = counts.get(p, 0) + 1
    return counts


def _clean_qishui_tags_broad(events: List[Dict[str, Any]]) -> int:
    """对汽水相关事件清洗 tags 为 ["汽水OCR"]，含无 source 的 base 数据。"""
    n = 0
    for e in events:
        if not _is_qishui_like(e):
            continue
        if e.get("tags") != ["汽水OCR"]:
            e["tags"] = ["汽水OCR"]
            n += 1
    return n


def _purify_base_events(events: List[Dict[str, Any]]) -> tuple:
    """
    净化 base events：去掉掉出榜/每日快照、平台归一化、汽水 tags 清洗。
    返回 (purified_events, stats_dict)
    """
    stats = {"drop": 0, "daily_snapshot": 0, "platform": {}, "qishui_tags": 0}
    kept = []
    for e in events:
        if _is_drop_event(e):
            stats["drop"] += 1
            continue
        if _is_daily_snapshot_event(e):
            stats["daily_snapshot"] += 1
            continue
        kept.append(e)

    platform_counts = _normalize_platform_in_place(kept)
    stats["platform"] = platform_counts

    qishui_tags_n = _clean_qishui_tags_broad(kept)
    stats["qishui_tags"] = qishui_tags_n

    return kept, stats


def _update_history_from_qishui(
    history_events: List[Dict[str, Any]],
    new_qishui_events: List[Dict[str, Any]],
    date_str: str,
) -> Dict[str, int]:
    """更新 history：同日替换、去重、search 净化、search rank 重排。返回统计。"""
    from merge_qishui_app_ocr_into_site_data import _normalize_qishui_platform_in_place

    stats: Dict[str, int] = {"deleted_today": 0, "added": 0, "search_hard_drop": 0, "dedup_drop": 0}

    _normalize_qishui_platform_in_place(history_events)

    new_qishui_events = rerank_search_events(new_qishui_events, max_rank=20)

    n_before = len(history_events)
    history_events[:] = [e for e in history_events if not (_is_qishui_ocr(e) and e.get("date") == date_str)]
    stats["deleted_today"] = n_before - len(history_events)

    history_events.extend(new_qishui_events)
    stats["added"] = len(new_qishui_events)

    history_events[:], search_hard_drop = dedupe_search_hard_only(history_events)
    stats["search_hard_drop"] = search_hard_drop
    history_events[:], dedup_drop = dedupe_qishui_events(history_events)
    stats["dedup_drop"] = dedup_drop

    _clean_qishui_tags(history_events)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="本地一键净化+合并汽水，输出线上 merged JSON")
    parser.add_argument("--base", type=Path, default=PROJECT_ROOT / "inbox" / "github_site" / "merged_events_latest.json")
    parser.add_argument("--qishui", type=Path, default=PROJECT_ROOT / "data" / "qishui_app_ocr_latest.json")
    parser.add_argument("--history", type=Path, default=PROJECT_ROOT / "data" / "qishui_events_history.json")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "out" / "merged_events_latest.json")
    parser.add_argument("--keep-days", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-qishui", action="store_true", help="不合并汽水，仅净化+裁剪")
    args = parser.parse_args()

    base_path = args.base.resolve()
    qishui_path = args.qishui.resolve()
    history_path = args.history.resolve()
    out_path = args.out.resolve()
    keep_days = args.keep_days
    dry_run = args.dry_run
    no_qishui = args.no_qishui

    if not base_path.exists():
        print(f"  [ERROR] base 文件不存在: {base_path}")
        sys.exit(2)

    base_data = load_json(base_path)
    base_events: List[Dict[str, Any]] = base_data.get("events", [])
    n_base = len(base_events)
    print(f"  [INFO] base events 数量: {n_base}")

    # ---- 净化 base ----
    purified, stats = _purify_base_events(base_events)
    if stats["drop"] > 0:
        print(f"  [INFO] 删除「掉出榜」: {stats['drop']} 条")
    if stats["daily_snapshot"] > 0:
        print(f"  [INFO] 删除「每日快照」: {stats['daily_snapshot']} 条")
    for alias, cnt in stats["platform"].items():
        print(f"  [INFO] 平台名映射 {alias} -> {PLATFORM}: {cnt} 条")
    if stats["qishui_tags"] > 0:
        print(f"  [INFO] 汽水旧英文 tag 清理: {stats['qishui_tags']} 条")

    events = purified

    # ---- 合并汽水 ----
    if not no_qishui:
        history_events: List[Dict[str, Any]] = []
        if history_path.exists():
            history_data = load_json(history_path)
            history_events = history_data.get("events", [])
        else:
            print(f"  [INFO] history 不存在，当作空: {history_path}")

        if qishui_path.exists():
            qishui_data = load_json(qishui_path)
            date_str = (qishui_data.get("date") or "").strip()
            if date_str:
                charts_obj = qishui_data.get("charts") or {}
                new_qishui_events: List[Dict[str, Any]] = []
                for chart_type in ("hot", "new", "search", "artist"):
                    items = charts_obj.get(chart_type) or []
                    for item in items:
                        if isinstance(item, dict):
                            new_qishui_events.append(item_to_event(item, date_str, chart_type))

                if new_qishui_events:
                    hstats = _update_history_from_qishui(history_events, new_qishui_events, date_str)
                    print(f"  [INFO] 汽水 history 更新: 删除同日旧汽水 {hstats['deleted_today']} 条, 新增 {hstats['added']} 条")
                    print(f"  [INFO] search 硬重复丢弃: {hstats['search_hard_drop']} 条")
                    print(f"  [INFO] history 去重丢弃: {hstats['dedup_drop']} 条")

                    if not dry_run:
                        save_json(history_path, {"events": history_events})
            else:
                print(f"  [WARN] qishui 缺少 date 字段，跳过 history 更新")
        else:
            print(f"  [INFO] qishui OCR 不存在，跳过 history 更新，仍从 history 合并窗口: {qishui_path}")

        # 从 history 取最近 keep_days 汽水
        qishui_pruned = prune_events_last_n_days(history_events, keep_days)
        qishui_in_window = [e for e in qishui_pruned if _is_qishui_ocr(e)]

        # 移除 base 中旧汽水，追加汽水窗口（用 _is_qishui_like 覆盖无 source 的 base 数据）
        events = [e for e in events if not _is_qishui_like(e)]
        events.extend(qishui_in_window)
        print(f"  [INFO] 合并汽水窗口: {len(qishui_in_window)} 条（keep_days={keep_days}）")
        _clean_qishui_tags_broad(events)

    # ---- 最终 keep_days 裁剪 ----
    events = prune_events_last_n_days(events, keep_days)

    date_dist: Dict[str, int] = {}
    for e in events:
        d = e.get("date", "")
        if d:
            date_dist[d] = date_dist.get(d, 0) + 1

    payload = {
        "generated_at": "",
        "total_events": len(events),
        "total_days": len(date_dist),
        "date_distribution": dict(sorted(date_dist.items(), key=lambda x: x[0])),
        "events": events,
    }
    try:
        from timezone_utils import beijing_now_iso
        payload["generated_at"] = beijing_now_iso()
    except Exception:
        payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"

    print(f"  [INFO] 合并后总 events: {len(events)}, 总天数: {len(date_dist)} (应 <= {keep_days})")

    if dry_run:
        print(f"  [DRY-RUN] 跳过写入: {out_path}")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(out_path, payload)
    print(f"  [OK] 写入: {out_path}")


if __name__ == "__main__":
    main()
