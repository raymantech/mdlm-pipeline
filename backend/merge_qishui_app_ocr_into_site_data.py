#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将汽水音乐 App OCR 的 4 个榜单（search/hot/new/artist）合并进主站 merged_events_latest.json。

- 汽水历史归档：data/qishui_events_history.json 长期保存全部汽水 OCR 事件（不裁剪）
- 站点 merged：只保留最近 N 天汽水 + 其它平台，轻量展示
- 平台：platform="抖音(汽水)"，source="douyin_qishui_app_ocr"
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PLATFORM = "抖音(汽水)"
SOURCE = "douyin_qishui_app_ocr"

CHART_CONFIG = {
    "hot": {"charts": ["热歌榜"]},
    "new": {"charts": ["新歌榜"]},
    "search": {"charts": ["热门搜索"]},
    "artist": {"charts": ["音乐人榜"]},
}


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_search_term(s: str) -> str:
    """
    热门搜索词归一化：去除 OCR 常见前后缀噪声，避免「小半」与「小半 热」等重复语义。
    """
    t = (s or "").strip()
    t = " ".join(t.split())
    # 循环剔除开头噪声前缀
    prefixes = ("+", "·", "•", "0-", "O-", "〇-", "0 ", "O ", "〇 ")
    while t:
        stripped = False
        for p in prefixes:
            if t.startswith(p):
                t = t[len(p):].strip()
                stripped = True
                break
        if not stripped:
            break
    # 去除结尾「 热」或「热」（先处理长的）
    while t:
        if t.endswith(" 热"):
            t = t[:-2].rstrip()
        elif t.endswith("热"):
            t = t[:-1].rstrip()
        else:
            break
    return t.strip() or "—"


def _safe_rank_now(item: Dict[str, Any]) -> int:
    try:
        r = item.get("rank", 0)
        return int(r) if r is not None else 0
    except (TypeError, ValueError):
        return 0


def item_to_event(item: Dict[str, Any], date_str: str, chart_type: str) -> Dict[str, Any]:
    cfg = CHART_CONFIG.get(chart_type, {"charts": [chart_type]})
    tags = ["汽水OCR"]
    charts = cfg["charts"]

    if chart_type in ("hot", "new"):
        track = (item.get("track_name") or "").strip() or "—"
        artist = (item.get("artist_name") or "").strip() or "—"
    elif chart_type == "search":
        raw = (item.get("track_name") or "").strip() or "—"
        track = normalize_search_term(raw) if raw != "—" else "—"
        artist = "—"
    else:
        track = (item.get("artist_name") or "").strip() or "—"
        artist = "—"

    rank_now = _safe_rank_now(item)

    return {
        "platform": PLATFORM,
        "source": SOURCE,
        "track": track,
        "artist": artist,
        "date": date_str,
        "rank_now": rank_now,
        "rank_prev": None,
        "delta": None,
        "severity": 0,
        "id": None,
        "narrative": "",
        "source_event_ids": [],
        "tags": tags,
        "charts": charts,
    }


def dedupe_search_by_normalized_track(events: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """
    已废弃用于丢弃：不再用 normalized_track 作为去重 key 删除事件。
    仅保留用于兼容调用；实际去重请用 dedupe_search_hard_only。
    返回 (events 原样, 0)。
    """
    return list(events), 0


def dedupe_search_hard_only(events: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """
    对 search 榜（charts[0]=="热门搜索"）仅做两种硬重复去重，不做归一化去重：
    A) 同日同榜 rank_now 重复：保留 rank_now 最小且 track 更长的一条（或首次出现）
    B) 同日同榜 track 完全一致：保留 rank_now 更小的一条
    normalize_search_term 仅用于展示清洗，不作为丢弃依据。
    """
    chart_search = "热门搜索"
    search_events = [e for e in events if (e.get("charts") or [""])[0] == chart_search]
    other_events = [e for e in events if e not in search_events]
    if not search_events:
        return list(events), 0

    n_initial = len(search_events)
    # A) 按 (date, chart0, rank_now) 分组，每组保留 track 更长的一条（同长保留首次）
    rank_key_to_best: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for e in search_events:
        date = e.get("date", "")
        chart0 = (e.get("charts") or [""])[0] or ""
        rank = e.get("rank_now") if e.get("rank_now") is not None else 999
        r = rank if isinstance(rank, int) else 999
        key = (date, chart0, r)
        track = (e.get("track") or "").strip()
        existing = rank_key_to_best.get(key)
        if existing is None or len(track) > len((existing.get("track") or "").strip()):
            rank_key_to_best[key] = e
    after_a = list(rank_key_to_best.values())
    # B) 按 (date, chart0, track) 完全一致去重，保留 rank_now 更小的一条
    track_key_to_best: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for e in after_a:
        date = e.get("date", "")
        chart0 = (e.get("charts") or [""])[0] or ""
        track = (e.get("track") or "").strip()
        key = (date, chart0, track)
        rank = e.get("rank_now") if e.get("rank_now") is not None else 999
        r = rank if isinstance(rank, int) else 999
        existing = track_key_to_best.get(key)
        if existing is None or r < (existing.get("rank_now") if existing.get("rank_now") is not None else 999):
            track_key_to_best[key] = e
    search_deduped = list(track_key_to_best.values())
    drop_count = n_initial - len(search_deduped)
    search_sorted = sorted(search_deduped, key=lambda e: (e.get("date", ""), e.get("rank_now") or 999))
    out = other_events + search_sorted
    return out, drop_count


def rerank_search_events(events: List[Dict[str, Any]], max_rank: int = 20) -> List[Dict[str, Any]]:
    """
    对 search 榜做 rank 重排补齐：按 rank_now 升序，rank_now<=0 排最后，重赋 rank_now=1..N（最多 max_rank）。
    非 search 榜不改动。
    """
    search_events = [e for e in events if (e.get("charts") or [""])[0] == "热门搜索"]
    other_events = [e for e in events if e not in search_events]
    if not search_events:
        return events
    search_sorted = sorted(
        search_events,
        key=lambda e: ((e.get("rank_now") or 0) <= 0, e.get("rank_now") or 999),
    )
    search_sorted = search_sorted[:max_rank]
    for i, e in enumerate(search_sorted, 1):
        e["rank_now"] = i
    return other_events + search_sorted


def dedupe_qishui_events(events: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """
    对汽水 OCR 事件去重：key=(date, charts[0], rank_now)；rank_now==0 则用 (date, charts[0], track)。
    保留第一次出现，返回 (deduped_events, drop_count)。
    """
    seen: set = set()
    out: List[Dict[str, Any]] = []
    drop_count = 0
    for e in events:
        if e.get("platform") != PLATFORM or e.get("source") != SOURCE:
            out.append(e)
            continue
        date = e.get("date", "")
        charts = e.get("charts") or []
        chart0 = charts[0] if charts else ""
        rank = e.get("rank_now", 0)
        track = (e.get("track") or "").strip()
        if rank and rank != 0:
            key = (date, chart0, rank)
        else:
            key = (date, chart0, track)
        if key in seen:
            drop_count += 1
            continue
        seen.add(key)
        out.append(e)
    return out, drop_count


def prune_events_last_n_days(events: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    """只保留最近 n 个 distinct date 的事件（date 按字符串降序）。"""
    dates = sorted(set(e.get("date", "") for e in events if e.get("date")), reverse=True)
    keep_dates = set(dates[:n])
    return [e for e in events if e.get("date") in keep_dates]


def _is_qishui_ocr(e: Dict[str, Any]) -> bool:
    return e.get("platform") == PLATFORM and e.get("source") == SOURCE


def normalize_qishui_event(e: Dict[str, Any]) -> bool:
    """汽水OCR事件：tags 统一为 ["汽水OCR"]，不改 charts。返回是否发生变更。"""
    if not _is_qishui_ocr(e):
        return False
    old = e.get("tags")
    e["tags"] = ["汽水OCR"]
    return old != ["汽水OCR"]


def _clean_qishui_tags(events: List[Dict[str, Any]]) -> int:
    """对汽水 OCR 事件清洗 tags：移除 hot_song/new_song/search_term/artist_rank，统一为 ["汽水OCR"]。返回清洗条数。"""
    n = 0
    for e in events:
        if not _is_qishui_ocr(e):
            continue
        if e.get("tags") != ["汽水OCR"]:
            e["tags"] = ["汽水OCR"]
            n += 1
    return n


def _is_qishui_related(e: Dict[str, Any]) -> bool:
    """汽水 OCR 或汽水相关事件（用于平台归一化）。"""
    if e.get("source") == SOURCE:
        return True
    tags = e.get("tags") or []
    if isinstance(tags, list) and any("汽水" in str(t) for t in tags):
        return True
    p = (e.get("platform") or "").strip()
    return p in ("抖音/汽水", "抖音汽水", "抖音(汽水)")


QISHUI_PLATFORM_VARIANTS = ("抖音/汽水", "抖音汽水")  # 抖音(汽水)=PLATFORM 已是标准

def _normalize_qishui_platform_in_place(events: List[Dict[str, Any]]) -> int:
    """将汽水平台变体（抖音/汽水、抖音汽水等）归一化为 抖音(汽水)，返回修复条数。"""
    n = 0
    for e in events:
        if not _is_qishui_related(e):
            continue
        p = (e.get("platform") or "").strip()
        if p in QISHUI_PLATFORM_VARIANTS:
            e["platform"] = PLATFORM
            n += 1
    return n


def is_drop_event(e: Dict[str, Any]) -> bool:
    """charts/tags/type/mTag 任一命中「掉出榜」则为掉出榜事件。"""
    charts = e.get("charts") or []
    tags = e.get("tags") or []
    t = e.get("type") or ""
    mtag = e.get("mTag") or e.get("_mTag") or ""
    if not isinstance(charts, list):
        charts = [charts] if charts else []
    if not isinstance(tags, list):
        tags = [tags] if tags else []
    for c in charts:
        if isinstance(c, str) and "掉出榜" in c:
            return True
    for t0 in tags:
        if isinstance(t0, str) and "掉出榜" in t0:
            return True
    if isinstance(t, str) and "掉出榜" in t:
        return True
    if isinstance(mtag, str) and "掉出榜" in str(mtag):
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="将汽水 App OCR 榜单合并进主站 merged_events_latest.json")
    parser.add_argument(
        "--site-merged",
        type=Path,
        default=PROJECT_ROOT / "static_site" / "data" / "merged_events_latest.json",
        help="主站 merged_events_latest.json 路径",
    )
    parser.add_argument(
        "--qishui",
        type=Path,
        default=PROJECT_ROOT / "data" / "qishui_app_ocr_latest.json",
        help="汽水 OCR 输出 JSON 路径",
    )
    parser.add_argument(
        "--history",
        type=Path,
        default=PROJECT_ROOT / "data" / "qishui_events_history.json",
        help="汽水事件历史归档路径（长期保存，不裁剪）",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=14,
        help="站点 merged 只保留最近 N 天的 distinct date",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印 deleted/added 等统计，不写入 history 和 site merged",
    )
    args = parser.parse_args()

    site_path = args.site_merged
    dry_run = args.dry_run
    qishui_path = args.qishui
    history_path = args.history
    keep_days = args.keep_days

    skip_site = False
    if not site_path.exists():
        if dry_run:
            skip_site = True
        else:
            print(f"  [ERROR] 主站文件不存在: {site_path}")
            sys.exit(2)
    if not qishui_path.exists():
        print(f"  [ERROR] 汽水 OCR 文件不存在: {qishui_path}")
        sys.exit(2)

    qishui_data = load_json(qishui_path)
    date_str = (qishui_data.get("date") or "").strip()
    if not date_str:
        print("  [ERROR] qishui_app_ocr_latest.json 缺少 date 字段")
        sys.exit(2)

    charts_obj = qishui_data.get("charts") or {}

    # ---- 1) 生成今日新汽水事件 ----
    new_qishui_events: List[Dict[str, Any]] = []
    for chart_type in ("hot", "new", "search", "artist"):
        items = charts_obj.get(chart_type)
        if items is None or not isinstance(items, list) or not items:
            print(f"  [INFO] 新增 {chart_type} 榜: 0 条（榜不存在或为空）")
            continue
        added = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            ev = item_to_event(item, date_str, chart_type)
            new_qishui_events.append(ev)
            added += 1
        print(f"  [INFO] 新增 {chart_type} 榜: {added} 条")

    added_count = len(new_qishui_events)

    # ---- 1.5) search 榜 rank 重排：按 rank_now 升序取前 20，强制 1..20（不做语义去重，保持 20 条）----
    new_qishui_events = rerank_search_events(new_qishui_events, max_rank=20)
    search_after_rerank = [e for e in new_qishui_events if (e.get("charts") or [""])[0] == "热门搜索"]
    n_search = len(search_after_rerank)
    print(f"  [INFO] 热门搜索最终条数: {n_search}")
    if n_search < 20:
        print(f"  [WARN] 热门搜索不足 20 条，缺少 {20 - n_search} 条")
        norm_to_raw: Dict[str, List[str]] = {}
        for e in search_after_rerank:
            raw = (e.get("track") or "").strip()
            norm = normalize_search_term(raw) if raw else ""
            norm_to_raw.setdefault(norm, []).append(raw)
        dup_groups = {k: v for k, v in norm_to_raw.items() if len(v) > 1}
        if dup_groups:
            print(f"  [DEBUG] 归一化后同词分组: {dup_groups}")

    n_new_norm = sum(1 for e in new_qishui_events if normalize_qishui_event(e))
    if n_new_norm > 0:
        print(f"  [INFO] 新汽水事件 tags 归一化: {n_new_norm} 条")

    # ---- 2) 更新 history：删除同日旧汽水，追加新事件，去重，写回 ----
    if history_path.exists():
        history_data = load_json(history_path)
        history_events: List[Dict[str, Any]] = history_data.get("events", [])
    else:
        history_events = []
        print(f"  [INFO] history 文件不存在，当作空: {history_path}")

    norm_history = _normalize_qishui_platform_in_place(history_events)
    if norm_history > 0:
        print(f"  [INFO] history 平台归一化修复: {norm_history} 条")

    n_history_before = len(history_events)
    history_events = [e for e in history_events if not (_is_qishui_ocr(e) and e.get("date") == date_str)]
    deleted_today = n_history_before - len(history_events)
    history_events.extend(new_qishui_events)
    history_events, search_hard_drop = dedupe_search_hard_only(history_events)
    history_events, dedup_drop_count = dedupe_qishui_events(history_events)

    clean_tags = _clean_qishui_tags(history_events)
    if clean_tags > 0:
        print(f"  [INFO] history 汽水 tags 清洗: {clean_tags} 条")

    if not dry_run:
        save_json(history_path, {"events": history_events})

    print(f"  [INFO] 读取 history 事件数: {n_history_before}")
    print(f"  [INFO] history 删除同日旧汽水: {deleted_today} 条")
    print(f"  [INFO] 新增汽水事件数: {added_count}")
    print(f"  [INFO] search 硬重复丢弃: {search_hard_drop} 条")
    print(f"  [INFO] history 去重丢弃: {dedup_drop_count} 条")
    print(f"  [{('DRY-RUN' if dry_run else 'OK')}] {'跳过写入' if dry_run else 'history 写入'}: {history_path}")

    # ---- 3) 更新站点 merged：从 history 取汽水并 prune 到最近 N 天，替换站点旧汽水，再整体裁剪 ----
    qishui_pruned = prune_events_last_n_days(history_events, keep_days)
    qishui_in_window = [e for e in qishui_pruned if _is_qishui_ocr(e)]
    n_site_norm = sum(1 for e in qishui_in_window if normalize_qishui_event(e))
    if n_site_norm > 0:
        print(f"  [INFO] site 汽水事件 tags 归一化（清历史残留）: {n_site_norm} 条")
    print(f"  [INFO] site 将写入汽水事件: {len(qishui_in_window)} 条（keep_days={keep_days}）")

    if skip_site:
        print(f"  [DRY-RUN] site merged 不存在，已跳过站点合并，仅输出 history 统计: {site_path}")
        return

    site_data = load_json(site_path)
    site_events: List[Dict[str, Any]] = site_data.get("events", [])
    n_site_original = len(site_events)

    norm_site = _normalize_qishui_platform_in_place(site_events)
    if norm_site > 0:
        print(f"  [INFO] site 平台归一化修复: {norm_site} 条")

    site_events = [e for e in site_events if not _is_qishui_ocr(e)]
    deleted_site_all_qishui = n_site_original - len(site_events)
    site_events.extend(qishui_in_window)
    added_site_qishui = len(qishui_in_window)

    n_before_prune = len(site_events)
    site_events = prune_events_last_n_days(site_events, keep_days)
    n_after_prune = len(site_events)

    # 过滤掉出榜事件（charts/tags/type/mTag 任一命中「掉出榜」）
    drop_count = sum(1 for ev in site_events if is_drop_event(ev))
    site_events = [ev for ev in site_events if not is_drop_event(ev)]
    if drop_count > 0:
        print(f"  [INFO] 过滤掉出榜事件: {drop_count} 条")

    print(f"  [INFO] 站点原 events 数量: {n_site_original}")
    print(f"  [INFO] 站点删除旧汽水事件: {deleted_site_all_qishui} 条")
    print(f"  [INFO] 站点追加 prune 后汽水: {added_site_qishui} 条")
    print(f"  [INFO] keep-days 裁剪前: {n_before_prune}，裁剪后: {n_after_prune}，keep_days={keep_days}")

    if dry_run:
        print(f"  [DRY-RUN] 跳过站点写入: {site_path}")
        return

    date_dist: Dict[str, int] = {}
    for e in site_events:
        d = e.get("date", "")
        if d:
            date_dist[d] = date_dist.get(d, 0) + 1

    site_data["events"] = site_events
    site_data["total_events"] = len(site_events)
    site_data["total_days"] = len(date_dist)
    site_data["date_distribution"] = dict(sorted(date_dist.items(), key=lambda x: x[0]))
    try:
        from timezone_utils import beijing_now_iso
        site_data["generated_at"] = beijing_now_iso()
    except Exception:
        site_data["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"

    save_json(site_path, site_data)
    print(f"  [OK] 站点写入: {site_path}")
    print(f"  [OK] 总 events: {len(site_events)}，总天数: {len(date_dist)}")


if __name__ == "__main__":
    main()
