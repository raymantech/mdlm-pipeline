#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地发布：净化 upstream + 合并汽水 + 输出可上线 JSON

输入：data/upstream/merged_events_latest.json（下载的）、data/qishui_events_history.json（可选）、data/qishui_app_ocr_latest.json（若当日更新）
输出：static_site/data/merged_events_latest.json（最终可上线版本）

逻辑：
1. 读 upstream（不修改原文件）
2. 净化掉出榜/每日快照（仅对站点输出做）
3. 若存在当日 OCR 文件则先更新 history（同日替换）
4. 从 history 取汽水窗口（keep-days）注入站点
5. 站点整体 keep-days 裁剪
6. 写出最终 JSON
"""

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from merge_qishui_app_ocr_into_site_data import (
    PLATFORM,
    SOURCE,
    CHART_CONFIG,
    load_json,
    save_json,
    item_to_event,
    dedupe_qishui_events,
    prune_events_last_n_days,
    _is_qishui_ocr,
    _clean_qishui_tags,
    normalize_search_term,
)
from collections import Counter

# 导出时保证包含的字段（tags 必须保留，不得被漏掉）
EXPORT_FIELDS = [
    "platform", "track", "artist", "date", "charts", "tags",
    "rank_now", "rank_prev", "rank", "delta", "severity", "id",
    "narrative", "source_event_ids", "event_type", "item_key",
    "item_id", "platforms", "derived", "source", "prev_seen_date",
    "return_gap_days",
]

PLATFORM_ALIASES = {
    "抖音/汽水": PLATFORM,
    "抖音汽水": PLATFORM,
    "抖音汽水OCR": PLATFORM,
    "抖音OCR": PLATFORM,
    "汽水OCR": PLATFORM,
}

# 汽水 OCR 榜单白名单：仅保留这 4 个
QISHUI_CHART_WHITELIST = frozenset({"热歌榜", "新歌榜", "热门搜索", "音乐人榜"})

CHART_SEARCH = "热门搜索"


def _has_ascii_alnum(s: str) -> bool:
    """字符串是否包含英文字母或数字。"""
    return any(c.isascii() and c.isalnum() for c in (s or ""))


def split_search_terms(raw: str) -> List[str]:
    """
    仅对「热门搜索」榜使用：若 OCR 把多个热搜词粘在一行，按规则拆成多个词。
    - 若字符串包含空格且不含英文字母/数字（纯中文/符号为主），按空格切开；
    - 每个片段经 normalize_search_term 去噪，过滤掉 "—" 与空串；
    - 返回 1..N 个词。
    """
    s = (raw or "").strip()
    if not s:
        return []
    if " " not in s:
        term = normalize_search_term(s)
        return [term] if term and term != "—" else []
    if _has_ascii_alnum(s):
        # 含英文/数字，不按空格拆，整段归一化
        term = normalize_search_term(s)
        return [term] if term and term != "—" else []
    # 含空格且纯中文/符号：按空格拆
    parts = s.split()
    out: List[str] = []
    for p in parts:
        term = normalize_search_term(p).strip() if p else "—"
        if term and term != "—":
            out.append(term)
    return out


def _normalize_search_track_for_parse(s: str) -> str:
    """
    拼接后 track 的基本 normalize：全角空格、重复空格压缩、strip、去除末尾孤立徽标。
    只去除末尾「热」「新」作为徽标，不全局替换（避免误伤新年歌曲）。
    修正 OCR 常见误识别：行首「-」当「一」（如 -半一半 -> 一半一半）。
    """
    if not s:
        return ""
    t = (s or "").strip()
    # 行首 OCR 误识别：- 当作 一（如 -半一半、-半-半）
    t = re.sub(r"^-\s*", "一", t)
    # 全角空格转半角
    out: List[str] = []
    for c in t:
        if c == "\u3000":
            out.append(" ")
        else:
            out.append(c)
    t = "".join(out)
    # 多空格压成单空格
    t = re.sub(r"\s+", " ", t).strip()
    # 去除末尾徽标（热/新，可带前置空格）
    t = re.sub(r"[\s　]*(热|新)\s*$", "", t)
    t = re.sub(r"\s+(热|新)\s*$", "", t)
    return t.strip()


def _is_badge_only(text: str) -> bool:
    """仅当 text 为孤立「热」或「新」时视为徽标，过滤。"""
    t = (text or "").strip()
    return t in ("热", "新")


def parse_search_from_raw_lines(
    raw_lines: List[str],
    date_str: str,
    max_rank: int = 20,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    按 rank 锚点分段解析热门搜索榜 OCR 原始行。
    - 识别 rank 1~20 作为锚点
    - 将 rank i 与 rank i+1 之间的所有文本块（除徽标外）拼接为该 rank 的歌名
    - 仅过滤 text == "热" 或 "新" 的孤立单字块
    返回 (events, stats)，stats 含 parsed_ranks, missing_ranks, duplicate_tracks, warnings。
    """
    cfg = CHART_CONFIG.get("search", {"charts": [CHART_SEARCH]})
    charts = cfg["charts"]
    stats: Dict[str, Any] = {
        "parsed_ranks": [],
        "missing_ranks": [],
        "duplicate_tracks": [],
        "warnings": [],
    }

    # 1. 合并去重，保持顺序
    seen: set = set()
    lines: List[str] = []
    for L in raw_lines:
        s = (L or "").strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        lines.append(s)

    # 2. 解析每行 -> (rank_or_none, text)
    parsed: List[Tuple[Any, str]] = []
    for line in lines:
        m = re.match(r"^(\d{1,2})\s*$", line)  # 纯 rank
        if m:
            r = int(m.group(1))
            parsed.append((r if 1 <= r <= 20 else None, ""))
            continue
        m = re.match(r"^(\d{1,2})\s*(.*)$", line)  # rank + rest
        if m:
            r = int(m.group(1))
            rest = (m.group(2) or "").strip()
            if _is_badge_only(rest):
                rest = ""
            parsed.append((r if 1 <= r <= 20 else None, rest))
            continue
        # 孤儿行
        if _is_badge_only(line):
            parsed.append((None, None))  # 徽标，略过
        else:
            parsed.append((None, line))

    # 3. 按 rank 锚点分段：找到每个 rank 的首次出现位置
    rank_first: Dict[int, int] = {}
    for i, (r, _) in enumerate(parsed):
        if r is not None and r not in rank_first:
            rank_first[r] = i

    # 4. 按 rank 升序分段：对每对相邻 rank (r, s)，segment [rank_first[r], rank_first[s]) 内的内容
    #    若 s > r+1，中间有 gap：孤儿行分配给 r+1, r+2, ..., s-1
    rank_to_texts = {r: [] for r in range(1, max_rank + 1)}
    sorted_ranks = sorted(rank_first.keys())
    for idx, r in enumerate(sorted_ranks):
        start = rank_first[r]
        end = rank_first[sorted_ranks[idx + 1]] if idx + 1 < len(sorted_ranks) else len(parsed)
        _, first_rest = parsed[start]
        if first_rest:
            rank_to_texts[r].append(first_rest)
        orphans: List[str] = []
        for j in range(start + 1, end):
            _, text = parsed[j]
            if text is not None and text and not _is_badge_only(text):
                orphans.append(text)
        next_r = sorted_ranks[idx + 1] if idx + 1 < len(sorted_ranks) else max_rank + 1
        if next_r == r + 1:
            # 无 gap，孤儿归当前 rank
            rank_to_texts[r].extend(orphans)
        elif next_r > r + 1:
            # 有 gap：前 (next_r - r - 1) 个孤儿分配给 r+1..next_r-1
            gap_count = next_r - r - 1
            for i, o in enumerate(orphans):
                if i < gap_count:
                    rank_to_texts[r + 1 + i].append(o)
                else:
                    rank_to_texts[r].append(o)
        else:
            # next_r <= r（逆序），孤儿归当前 rank
            rank_to_texts[r].extend(orphans)

    # 5. 拼接、normalize、生成 events（空 track 跳过该 rank，不挤占）
    events: List[Dict[str, Any]] = []
    track_to_ranks: Dict[str, List[int]] = {}
    for r in range(1, max_rank + 1):
        texts = rank_to_texts.get(r, [])
        raw = " ".join(texts).strip() if texts else ""
        track = _normalize_search_track_for_parse(raw)
        if not track or track == "—":
            stats["missing_ranks"].append(r)
            stats["warnings"].append(f"date={date_str} rank={r} 拼接后为空，原始块={texts!r}")
            continue  # 跳过该 rank
        stats["parsed_ranks"].append(r)
        track_to_ranks.setdefault(track, []).append(r)
        events.append({
            "platform": PLATFORM,
            "source": SOURCE,
            "track": track,
            "artist": "—",
            "date": date_str,
            "rank_now": r,
            "rank_prev": None,
            "delta": None,
            "severity": 0,
            "id": None,
            "narrative": "",
            "source_event_ids": [],
            "tags": ["汽水OCR"],
            "charts": charts,
        })

    # 6. 检测重复 track
    for t, ranks in track_to_ranks.items():
        if t and t != "—" and len(ranks) > 1:
            stats["duplicate_tracks"].append((t, ranks))

    for t, ranks in stats["duplicate_tracks"]:
        stats["warnings"].append(f"同日同榜同名: track={t!r} ranks={ranks}")

    return events, stats


def _safe_rank_now(item: Dict[str, Any]) -> int:
    try:
        r = item.get("rank", 0)
        return int(r) if r is not None else 0
    except (TypeError, ValueError):
        return 0


def _search_item_to_events(item: Dict[str, Any], date_str: str) -> List[Dict[str, Any]]:
    """
    热门搜索：一条 OCR item 可拆成多条 events（rank 保留原 rank 作参考），再参与去重与重排。
    """
    raw = (item.get("track_name") or "").strip() or "—"
    terms = split_search_terms(raw)
    if not terms:
        # 拆不出有效词则仍保留一条，track 用归一化结果或 "—"
        terms = [normalize_search_term(raw) if raw != "—" else "—"]
        if terms[0] == "—":
            return []
    rank_ref = _safe_rank_now(item)
    cfg = CHART_CONFIG.get("search", {"charts": [CHART_SEARCH]})
    events = []
    for track in terms:
        events.append({
            "platform": PLATFORM,
            "source": SOURCE,
            "track": track,
            "artist": "—",
            "date": date_str,
            "rank_now": rank_ref,
            "rank_prev": None,
            "delta": None,
            "severity": 0,
            "id": None,
            "narrative": "",
            "source_event_ids": [],
            "tags": ["汽水OCR"],
            "charts": cfg["charts"],
        })
    return events


def _is_qishui_like(e: Dict[str, Any]) -> bool:
    p = (e.get("platform") or "").strip()
    if p == PLATFORM or p in PLATFORM_ALIASES:
        return True
    tags = e.get("tags") or []
    return any("汽水" in str(t) for t in tags) if isinstance(tags, list) else False


def _is_drop_event(e: Dict[str, Any]) -> bool:
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
    for c in (e.get("charts") or []):
        if isinstance(c, str) and "每日快照" in c:
            return True
    return False


def _normalize_platform_in_place(events: List[Dict[str, Any]]) -> int:
    n = 0
    for e in events:
        p = (e.get("platform") or "").strip()
        if p in PLATFORM_ALIASES and p != PLATFORM:
            e["platform"] = PLATFORM
            n += 1
    return n


def _normalize_event_type_str(s: str) -> str:
    """去空格后比较用。"""
    return (s or "").replace(" ", "").strip()


_STABLE_ALIASES = frozenset({"top10稳定", "top10_stable", "top10stable"})
_DOMINANT_ALIASES = frozenset({"连续3天top10", "dominant", "c3_top10", "top10_3day"})


def _ensure_top10_tags_from_event_type(events: List[Dict[str, Any]]) -> int:
    """
    upstream 的 event_type（或 tags）中若存在 Top10稳定/连续3天Top10 及其英文别名，
    确保最终 tags 包含对应中文 tag。兼容空格写法（如 "连续 3 天 Top10"）。
    返回新增 tag 的 event 数量。
    """
    n = 0
    for e in events:
        tags = e.get("tags")
        if not isinstance(tags, list):
            tags = []
            e["tags"] = tags
        tags_set = set((t or "").strip() for t in tags if (t or "").strip())

        def add_if_match(val: str, cn_tag: str) -> bool:
            v = _normalize_event_type_str(val).lower()
            if not v:
                return False
            if cn_tag == "Top10稳定" and v in _STABLE_ALIASES:
                if cn_tag not in tags_set:
                    tags.append(cn_tag)
                    tags_set.add(cn_tag)
                    return True
            if cn_tag == "连续3天Top10" and (v in _DOMINANT_ALIASES or "连续" in val and "top10" in v):
                if cn_tag not in tags_set:
                    tags.append(cn_tag)
                    tags_set.add(cn_tag)
                    return True
            return False

        added = False
        et = e.get("event_type") or ""
        if add_if_match(et, "Top10稳定") or add_if_match(et, "连续3天Top10"):
            added = True
        for t in list(tags_set):
            if add_if_match(str(t), "Top10稳定") or add_if_match(str(t), "连续3天Top10"):
                added = True

        if added:
            e["tags"] = list(tags)
            n += 1
    return n


def _has_top10_signal(e: Dict[str, Any]) -> bool:
    """事件 tags 是否含 Top10稳定 或 连续3天Top10。"""
    for t in (e.get("tags") or []):
        if isinstance(t, str) and t.strip() in ("Top10稳定", "连续3天Top10"):
            return True
    return False


def _prune_events_keep_top10_dates(events: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    """
    裁剪到最近 n 个 distinct date，但含 Top10稳定/连续3天Top10 的日期必须保留，
    避免汽水近期日期将 QQ/网易/酷狗的 Top10 信号挤出窗口。
    """
    dates = sorted(set(e.get("date", "") for e in events if e.get("date")), reverse=True)
    keep_dates = set(dates[:n])
    for e in events:
        if _has_top10_signal(e):
            d = e.get("date", "")
            if d:
                keep_dates.add(d)
    return [e for e in events if e.get("date") in keep_dates]


def _clean_qishui_tags_broad(events: List[Dict[str, Any]]) -> int:
    n = 0
    for e in events:
        if not _is_qishui_like(e):
            continue
        if e.get("tags") != ["汽水OCR"]:
            e["tags"] = ["汽水OCR"]
            n += 1
    return n


def _purify_events(events: List[Dict[str, Any]]) -> tuple:
    """净化：去掉掉出榜/每日快照、平台归一化、汽水 tags 清洗。返回 (purified, stats)。"""
    stats = {"drop": 0, "daily_snapshot": 0}
    kept = []
    for e in events:
        if _is_drop_event(e):
            stats["drop"] += 1
            continue
        if _is_daily_snapshot_event(e):
            stats["daily_snapshot"] += 1
            continue
        kept.append(e)

    _normalize_platform_in_place(kept)
    stats["qishui_tags"] = _clean_qishui_tags_broad(kept)
    return kept, stats


def _update_history_from_qishui(
    history_events: List[Dict[str, Any]],
    new_qishui_events: List[Dict[str, Any]],
    date_str: str,
) -> None:
    from merge_qishui_app_ocr_into_site_data import _normalize_qishui_platform_in_place

    _normalize_qishui_platform_in_place(history_events)

    history_events[:] = [e for e in history_events if not (_is_qishui_ocr(e) and e.get("date") == date_str)]
    history_events.extend(new_qishui_events)
    # 热门搜索：按 (date, chart, normalized_term) 去重，再按 date 重排取前 20
    history_events[:], _ = dedupe_search_by_normalized_term(history_events)
    history_events[:] = rerank_search_events_per_date(history_events, max_rank=20)
    history_events[:], _ = dedupe_qishui_events(history_events)
    _clean_qishui_tags(history_events)


def _isolate_qishui_and_whitelist(events: List[Dict[str, Any]]) -> tuple:
    """
    抖音(汽水) 数据源硬隔离 + 榜单白名单：
    1) platform==抖音(汽水) 且 source!=douyin_qishui_app_ocr -> 删除
    2) platform==抖音(汽水) 且 source==douyin_qishui_app_ocr 且 charts[0] 不在白名单 -> 删除
    返回 (filtered_events, n_non_ocr_deleted, n_non_whitelist_deleted)
    """
    n_non_ocr = 0
    n_non_whitelist = 0
    kept = []
    for e in events:
        p = (e.get("platform") or "").strip()
        if p != PLATFORM:
            kept.append(e)
            continue
        # platform == 抖音(汽水)
        src = (e.get("source") or "").strip()
        if src != SOURCE:
            n_non_ocr += 1
            continue
        # 汽水 OCR 事件：榜单白名单校验
        charts = e.get("charts") or []
        chart0 = charts[0] if charts else ""
        if chart0 not in QISHUI_CHART_WHITELIST:
            n_non_whitelist += 1
            continue
        kept.append(e)
    return kept, n_non_ocr, n_non_whitelist


# 热门搜索伪条目：不允许写入最终结果（与 OCR 脚本一致）
SEARCH_PSEUDO_TITLES = frozenset({
    "半-半", "半一半", "-半一半", "半- 半", "半 -半", "半- 半",
    "—半一半", "一半-半",
})

# 热门搜索 validity check：track_name 不得包含的 UI 噪声词
SEARCH_UI_NOISE_WORDS = frozenset({"热门搜索", "热歌榜", "新歌榜", "音乐人榜", "会员", "汽水"})
# 异常符号（出现则判定解析失真，如 OCR 错 ROI）
SEARCH_INVALID_CHARS = frozenset("£€¥§©®™°±×÷")


def _check_search_items_validity(
    items: List[Dict[str, Any]],
    min_ranks: int = 18,
    max_rank: int = 20,
) -> Tuple[bool, str]:
    """
    search 榜 validity check：通过才允许使用当日 OCR 结果。
    - items 须覆盖 rank 1..20 至少 min_ranks 个且无重复 rank
    - 每个 track_name：长度 2~20、不含异常符号（£ 等）、不含 UI 噪声词
    返回 (valid, reason)。
    """
    if not items:
        return False, "items_empty"
    ranks_seen: Dict[int, int] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        r = it.get("rank")
        if r is not None and 1 <= r <= max_rank:
            ranks_seen[r] = ranks_seen.get(r, 0) + 1
    if len(ranks_seen) < min_ranks:
        return False, f"ranks_covered={len(ranks_seen)}<{min_ranks}"
    if any(c > 1 for c in ranks_seen.values()):
        return False, "duplicate_ranks"
    for it in items:
        if not isinstance(it, dict):
            continue
        name = (it.get("track_name") or "").strip()
        if len(name) < 2:
            return False, f"track_name_too_short: {name!r}"
        if len(name) > 20:
            return False, f"track_name_too_long: {name[:30]!r}..."
        if any(c in SEARCH_INVALID_CHARS for c in name):
            return False, f"track_name_invalid_char: {name!r}"
        for w in SEARCH_UI_NOISE_WORDS:
            if w in name:
                return False, f"track_name_ui_noise {w!r} in {name!r}"
    return True, "ok"


def _is_search_track_noise(track: str) -> bool:
    """True 表示应删除："—"、长度<2、仅符号/空白、伪条目（半-半 等）。"""
    if not track or track == "—":
        return True
    t = (track or "").strip()
    if len(t) < 2:
        return True
    if t in SEARCH_PSEUDO_TITLES:
        return True
    # 归一化后判伪：去空格/连字符后为「一半一半」噪声变体
    norm = re.sub(r"[\s\-—]+", "", t)
    if norm in ("半半", "一半半"):
        return True
    # 仅符号/空白：无字母、数字、CJK
    if not any(c.isalnum() or ("\u4e00" <= c <= "\u9fff") for c in t):
        return True
    return False


def _normalized_term(e: Dict[str, Any]) -> str:
    """热门搜索事件的归一化词（用于去重 key）。"""
    return normalize_search_term((e.get("track") or "").strip() or "")


def dedupe_search_by_normalized_term(events: List[Dict[str, Any]]) -> tuple:
    """
    对「热门搜索」按 key=(date, chart, normalized_term) 去重，保留 rank_now 更小的那条。
    其他榜不改。返回 (events, drop_count)。
    """
    search_events = [e for e in events if (e.get("charts") or [""])[0] == CHART_SEARCH]
    other_events = [e for e in events if e not in search_events]
    if not search_events:
        return list(events), 0
    n_initial = len(search_events)
    key_to_best: Dict[tuple, Dict[str, Any]] = {}
    for e in search_events:
        date = e.get("date", "")
        chart0 = (e.get("charts") or [""])[0] or ""
        norm = _normalized_term(e)
        key = (date, chart0, norm)
        rank = e.get("rank_now") if e.get("rank_now") is not None else 999
        r = rank if isinstance(rank, int) else 999
        existing = key_to_best.get(key)
        if existing is None or r < (existing.get("rank_now") if existing.get("rank_now") is not None else 999):
            key_to_best[key] = e
    search_deduped = list(key_to_best.values())
    drop_count = n_initial - len(search_deduped)
    out = other_events + search_deduped
    return out, drop_count


def rerank_search_events_per_date(events: List[Dict[str, Any]], max_rank: int = 20) -> List[Dict[str, Any]]:
    """
    对「热门搜索」按 date 分组，每组内按 rank_now 升序（<=0 放最后），取前 max_rank 条，重写 rank_now=1..N。
    其他榜不改。
    """
    search_events = [e for e in events if (e.get("charts") or [""])[0] == CHART_SEARCH]
    other_events = [e for e in events if e not in search_events]
    if not search_events:
        return events
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for e in search_events:
        d = e.get("date", "")
        by_date.setdefault(d, []).append(e)
    reranked = []
    for date_str in sorted(by_date.keys()):
        group = by_date[date_str]
        group_sorted = sorted(
            group,
            key=lambda e: ((e.get("rank_now") or 0) <= 0, e.get("rank_now") or 999),
        )
        group_sorted = group_sorted[:max_rank]
        for i, e in enumerate(group_sorted, 1):
            e["rank_now"] = i
        reranked.extend(group_sorted)
    return other_events + reranked


def _get_last_successful_search_from_history(
    history_events: List[Dict[str, Any]],
    min_ranks: int = 20,
) -> List[Dict[str, Any]]:
    """
    从 history 中取上一份可用的 search 榜（同日 20 条，按 date 降序取第一份满足条件的）。
    """
    def _is_search(e: Dict[str, Any]) -> bool:
        return (e.get("platform") or "").strip() == PLATFORM and (
            (e.get("charts") or [""])[0] or ""
        ) == CHART_SEARCH

    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for e in history_events:
        if not _is_search(e):
            continue
        d = e.get("date", "")
        if d:
            by_date.setdefault(d, []).append(e)
    for date_str in sorted(by_date.keys(), reverse=True):
        group = by_date[date_str]
        group_ranks = len(set(e.get("rank_now") for e in group if e.get("rank_now")))
        if len(group) >= min_ranks or group_ranks >= min_ranks:
            return list(group)[:min_ranks]
    return []


def _apply_search_cleanup_to_history(
    history_events: List[Dict[str, Any]],
    *,
    log: bool = True,
) -> None:
    """
    对 history 中每个 date 的「热门搜索」执行：normalize + 按 (date, chart, normalized_term) 去重 + 重排前 20。
    用于每次 build 时修复历史残留（保证旧日期不再出现小半/雨爱重复）。其他榜不改。原地修改 history_events。
    """
    def is_search(e: Dict[str, Any]) -> bool:
        return (
            (e.get("platform") or "").strip() == PLATFORM
            and (e.get("source") or "").strip() == SOURCE
            and ((e.get("charts") or [""])[0] or "") == CHART_SEARCH
        )

    search_by_date: Dict[str, List[Dict[str, Any]]] = {}
    rest: List[Dict[str, Any]] = []
    for e in history_events:
        if not is_search(e):
            rest.append(e)
            continue
        d = e.get("date", "")
        if d:
            search_by_date.setdefault(d, []).append(e)

    if not search_by_date:
        return

    cleaned_per_date: Dict[str, List[Dict[str, Any]]] = {}
    for date_str in sorted(search_by_date.keys()):
        group = search_by_date[date_str]
        before_n = len(group)
        # 1) normalize track
        for e in group:
            raw = (e.get("track") or "").strip()
            e["track"] = normalize_search_term(raw) if raw else "—"
        # 2) 删除噪声
        group = [e for e in group if not _is_search_track_noise(e.get("track") or "")]
        # 3) 按 (date, chart, normalized_term) 去重，保留 rank_now 更小
        group, _ = dedupe_search_by_normalized_term(group)
        # 4) 重排：rank_now<=0 放最后，取前 20，rank_now=1..N
        group = rerank_search_events_per_date(group, max_rank=20)
        after_n = len(group)
        cleaned_per_date[date_str] = group
        if log:
            print(f"  [INFO] search 榜 date={date_str} 清洗前={before_n} 条, 清洗后={after_n} 条")

    # 原地替换：保留非 search + 各 date 清洗后的 search
    history_events.clear()
    history_events.extend(rest)
    for date_str in sorted(cleaned_per_date.keys()):
        history_events.extend(cleaned_per_date[date_str])


def _normalize_for_item_key(s: str) -> str:
    """strip、小写(英文)、全角转半角、多空格压成单空格、括号外空格去掉；缺失用空串。"""
    if s is None:
        return ""
    t = str(s).strip()
    # 全角转半角（数字、英文、空格）
    out: List[str] = []
    for c in t:
        if "\uff01" <= c <= "\uff5e":  # 全角 ！～ 块
            out.append(chr(ord(c) - 0xfee0))
        elif c == "\u3000":
            out.append(" ")
        else:
            out.append(c)
    t = "".join(out)
    # 英文小写
    t = t.lower()
    # 多空格压成单空格
    t = re.sub(r"\s+", " ", t).strip()
    # 括号前后空格去掉，使 "失眠 (情绪版)" 与 "失眠(情绪版)" 一致
    t = re.sub(r"\s*\(\s*", "(", t)
    t = re.sub(r"\s*\)\s*", ")", t)
    return t.strip()


def get_item_key(event: Dict[str, Any]) -> str:
    """
    稳定唯一标识，用于衍生事件聚合/去重。
    - 仅当 event["item_id"] 非空时视为内容 id：item_key = str(item_id)
    - 否则：item_key = normalize(track) + "__" + normalize(artist)
    注意：不用 event["id"]（事件 id），否则每条事件唯一导致多榜无法聚合。
    缺失 track/artist 用空串占位。
    """
    item_id = event.get("item_id")
    if item_id is not None and str(item_id).strip() != "":
        return str(item_id).strip()
    track = _normalize_for_item_key((event.get("track") or "").strip() or "")
    artist = _normalize_for_item_key((event.get("artist") or "").strip() or "")
    return f"{track}__{artist}"


def _item_key(e: Dict[str, Any]) -> tuple:
    """(platform, chart_type, item_key) 用于回归榜 last_seen 维度。"""
    platform = (e.get("platform") or "").strip()
    charts = e.get("charts") or []
    chart_type = (charts[0] if charts else "") or (e.get("chart_type") or "") or ""
    return (platform, chart_type, get_item_key(e))


def _cross_platform_item_id(e: Dict[str, Any]) -> tuple:
    """跨平台同一曲目标识：(track, artist)。"""
    track = (e.get("track") or "").strip() or "—"
    artist = (e.get("artist") or "").strip() or "—"
    return (track, artist)


def _is_on_chart_record(e: Dict[str, Any]) -> bool:
    """在榜记录：有有效 rank（rank_now/rank 等）。"""
    for key in ("rank_now", "rank", "rankNow"):
        v = e.get(key)
        if v is not None:
            try:
                r = int(v)
                if r > 0:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def compute_return_events(
    events_for_calc: List[Dict[str, Any]],
    events_for_publish: List[Dict[str, Any]],
    gap_days: int = 7,
) -> List[Dict[str, Any]]:
    """
    回归榜：必须用裁剪前的更长窗口 events_for_calc 计算 last_seen；
    events_for_publish 仅用于限制输出日期 publish_dates。
    同一 (platform, chart_type, item_key) 曾出现且间隔 >= gap_days 再次在榜则记为回归。
    去重：(date, platform, chart_type, item_key) 只生成 1 条。
    """
    publish_dates = {e.get("date", "") for e in events_for_publish if e.get("date")}
    if not publish_dates:
        return []

    # 按 date 升序，以便按时间维护 last_seen（key = (platform, chart_type, item_key)）
    sorted_calc = sorted(
        [e for e in events_for_calc if e.get("date")],
        key=lambda x: (x.get("date", ""), _item_key(x)),
    )
    last_seen: Dict[tuple, str] = {}
    return_keys_emitted: set = set()  # (date, platform, chart_type, item_key)
    out: List[Dict[str, Any]] = []

    for e in sorted_calc:
        date_str = e.get("date", "")
        if not date_str:
            continue
        key = _item_key(e)  # (platform, chart_type, item_key)
        if not _is_on_chart_record(e):
            continue
        prev = last_seen.get(key)
        if prev is not None:
            try:
                d_prev = datetime.strptime(prev, "%Y-%m-%d")
                d_curr = datetime.strptime(date_str, "%Y-%m-%d")
                gap = (d_curr - d_prev).days
                if gap >= gap_days and date_str in publish_dates:
                    dedupe_key = (date_str, key[0], key[1], key[2])  # item_key 已是 str
                    if dedupe_key not in return_keys_emitted:
                        return_keys_emitted.add(dedupe_key)
                        rank_val = e.get("rank_now") or e.get("rank") or 0
                        try:
                            rank_val = int(rank_val) if rank_val is not None else 0
                        except (TypeError, ValueError):
                            rank_val = 0
                        item_key_str = key[2]
                        item_id_val = e.get("item_id") or e.get("id")
                        derived = {
                            "date": date_str,
                            "platform": e.get("platform", ""),
                            "chart_type": key[1],
                            "charts": e.get("charts") or ([key[1]] if key[1] else []),
                            "item_key": item_key_str,
                            "item_id": item_id_val,
                            "track": e.get("track", ""),
                            "artist": e.get("artist", ""),
                            "rank": rank_val,
                            "rank_now": rank_val,
                            "event_type": "回归榜",
                            "derived": True,
                            "prev_seen_date": prev,
                            "return_gap_days": gap,
                        }
                        out.append(derived)
            except (ValueError, TypeError):
                pass
        last_seen[key] = date_str

    return out


def _event_type_or_tags_trigger(e: Dict[str, Any], trigger_types: tuple) -> bool:
    """事件是否为触发类型：event_type 在 trigger_types 或 tags 含中文触发类型。"""
    et = (e.get("event_type") or "").strip()
    if et in trigger_types:
        return True
    tags = e.get("tags") or []
    for t in tags:
        if isinstance(t, str) and t in trigger_types:
            return True
    return False


def compute_multi_chart_surge_events(
    events_publish: List[Dict[str, Any]],
    min_platforms: int = 2,
    trigger_types: tuple = ("暴涨", "新进榜"),
) -> List[Dict[str, Any]]:
    """
    多榜爆发：触发事件仍限 event_type/tags 含「暴涨」「新进榜」。
    聚合维度 (date, item_key)；同一 (date, item_key) 只生成 1 条。
    platform="多平台"，chart_type="多榜"，platforms=sorted(platform_set)，item_key 必填。
    """
    trigger_events = [
        e for e in events_publish
        if _event_type_or_tags_trigger(e, trigger_types)
    ]
    by_key: Dict[tuple, Dict[str, Any]] = {}  # (date, item_key) -> { platforms, best_event }
    for e in trigger_events:
        date_str = e.get("date", "") or ""
        if not date_str:
            continue
        item_key = get_item_key(e)
        key = (date_str, item_key)
        platform = (e.get("platform") or "").strip() or "—"
        if key not in by_key:
            by_key[key] = {"platforms": set(), "best": e}
        by_key[key]["platforms"].add(platform)
        cur = by_key[key]["best"]
        r_cur = cur.get("rank_now") or cur.get("rank") or 999
        r_e = e.get("rank_now") or e.get("rank") or 999
        try:
            r_cur = int(r_cur) if r_cur is not None else 999
            r_e = int(r_e) if r_e is not None else 999
        except (TypeError, ValueError):
            pass
        if r_e < r_cur:
            by_key[key]["best"] = e

    out: List[Dict[str, Any]] = []
    for (date_str, item_key_str), data in by_key.items():
        platforms_set = data["platforms"]
        if len(platforms_set) < min_platforms:
            continue
        best = data["best"]
        item_id_val = best.get("item_id") or best.get("id")
        if item_id_val is not None and str(item_id_val).strip() == "":
            item_id_val = None
        ev = {
            "date": date_str,
            "platform": "多平台",
            "chart_type": "多榜",
            "charts": ["多榜"],
            "item_key": item_key_str,
            "item_id": item_id_val,
            "track": best.get("track", ""),
            "artist": best.get("artist", ""),
            "rank": best.get("rank_now") or best.get("rank"),
            "rank_now": best.get("rank_now") or best.get("rank"),
            "event_type": "多榜爆发",
            "platforms": sorted(list(platforms_set)),
            "derived": True,
        }
        out.append(ev)
    return out


def _prepare_events_for_export(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    导出前准备：保留所有字段，确保 tags 存在且为 list。
    不得 pop/del tags，导出白名单需包含 tags。
    """
    out = []
    for e in events:
        obj = dict(e)
        if "tags" not in obj or obj["tags"] is None:
            obj["tags"] = []
        elif not isinstance(obj["tags"], list):
            obj["tags"] = list(obj["tags"]) if obj["tags"] else []
        out.append(obj)
    return out


def _merge_event_into(base: Dict[str, Any], other: Dict[str, Any]) -> None:
    """
    将 other 的字段合并进 base（原地修改 base）。
    重点：tags 做集合并集，charts 合并去重，source_event_ids 合并去重。
    """
    # tags 集合并集
    base_tags = set()
    for t in base.get("tags") or []:
        if isinstance(t, str) and t.strip():
            base_tags.add(t.strip())
    for t in other.get("tags") or []:
        if isinstance(t, str) and t.strip():
            base_tags.add(t.strip())
    base["tags"] = sorted(list(base_tags))  # 排序保证稳定

    # charts 合并去重
    base_charts = list(base.get("charts") or [])
    seen_ch = set(c for c in base_charts if c)
    for c in other.get("charts") or []:
        if c and c not in seen_ch:
            seen_ch.add(c)
            base_charts.append(c)
    base["charts"] = base_charts

    # source_event_ids 合并去重
    base_ids = set()
    for i in base.get("source_event_ids") or []:
        if i is not None:
            base_ids.add(i)
    for i in other.get("source_event_ids") or []:
        if i is not None:
            base_ids.add(i)
    if base_ids:
        base["source_event_ids"] = sorted(list(base_ids))

    # narrative: 若 base 为空则用 other 的
    if not (base.get("narrative") or "").strip() and (other.get("narrative") or "").strip():
        base["narrative"] = (other.get("narrative") or "").strip()

    # severity: 取更高
    s_base = base.get("severity")
    s_other = other.get("severity")
    if s_other is not None:
        try:
            so = int(s_other)
            sb = int(s_base) if s_base is not None else 0
            if so > sb:
                base["severity"] = so
        except (TypeError, ValueError):
            pass

    # delta/rank_prev: 若 base 为 None 则用 other 的
    if base.get("delta") is None and other.get("delta") is not None:
        base["delta"] = other["delta"]
    if base.get("rank_prev") is None and other.get("rank_prev") is not None:
        base["rank_prev"] = other["rank_prev"]


def _normalize_track_for_dict(track: str) -> str:
    """
    用于跨平台字典构建的 track 归一化：
    - strip 前后空格
    - 全角转半角
    - 多空格压缩为 1 个
    - 去掉常见后缀标识（可选）："(Live)"、"（情绪版）" 等
    """
    if not track:
        return ""
    t = str(track).strip()
    # 全角转半角（数字、英文、空格、标点）
    out: List[str] = []
    for c in t:
        if "\uff01" <= c <= "\uff5e":  # 全角 ！～ 块
            out.append(chr(ord(c) - 0xfee0))
        elif c == "\u3000":  # 全角空格
            out.append(" ")
        else:
            out.append(c)
    t = "".join(out)
    # 多空格压缩为单空格
    t = re.sub(r"\s+", " ", t).strip()
    # 去掉常见后缀标识（可选，先不做也可）
    # 暂时保留，后续可以扩展
    return t


def _build_cross_platform_artist_dict(events: List[Dict[str, Any]]) -> Dict[str, Counter]:
    """
    构建跨平台 artist 字典（只用非抖音平台事件）：
    - key = normalize(track)
    - value = Counter(artist) 计数
    
    过滤条件：
    - track 非空
    - artist 非空且不为 "—"
    - platform != "抖音(汽水)"
    """
    track_to_artists: Dict[str, Counter] = {}
    for e in events:
        platform = (e.get("platform") or "").strip()
        if platform == PLATFORM:
            continue
        track = (e.get("track") or "").strip()
        artist = (e.get("artist") or "").strip()
        if not track or track == "—":
            continue
        if not artist or artist == "—":
            continue
        norm_track = _normalize_track_for_dict(track)
        if not norm_track:
            continue
        if norm_track not in track_to_artists:
            track_to_artists[norm_track] = Counter()
        track_to_artists[norm_track][artist] += 1
    return track_to_artists


def _is_artist_noise_for_search(artist: str) -> bool:
    """热门搜索 artist 补全：若 top1 含明显英文噪声则视为无效。"""
    a = (artist or "").strip()
    if not a:
        return True
    if "/" in a or "company" in a.lower() or " inde " in a.lower():
        return True
    # 全英文且较长（如 Top Barry / INDEcompany 风格）视为噪声
    if len(a) > 15 and all(c.isascii() or c.isspace() for c in a):
        return True
    return False


def _infer_artist_from_dict(
    event: Dict[str, Any],
    artist_dict: Dict[str, Counter],
    min_votes: int = 2,
    min_confidence: float = 0.7,
    is_search_chart: bool = False,
) -> tuple:
    """
    对单个事件尝试从字典补全 artist。
    返回 (success: bool, artist: str, confidence: float, candidates: List[Dict])
    """
    track = (event.get("track") or "").strip()
    if not track or track == "—":
        return (False, "", 0.0, [])
    norm_track = _normalize_track_for_dict(track)
    if not norm_track:
        return (False, "", 0.0, [])
    counter = artist_dict.get(norm_track)
    if not counter or len(counter) == 0:
        return (False, "", 0.0, [])
    # 取 top1 artist 的票数和总票数
    top_items = counter.most_common(3)
    if not top_items:
        return (False, "", 0.0, [])
    top1_artist, v1 = top_items[0]
    total = sum(counter.values())
    confidence = v1 / total if total > 0 else 0.0
    # 热门搜索：额外过滤英文噪声
    if is_search_chart and _is_artist_noise_for_search(top1_artist):
        return (False, "", 0.0, [])
    # 仅当满足阈值条件才补全
    if v1 >= min_votes and confidence >= min_confidence:
        candidates = [{"artist": a, "votes": v} for a, v in top_items]
        return (True, top1_artist, confidence, candidates)
    return (False, "", 0.0, [])


def _complete_artists_for_qishui(
    events: List[Dict[str, Any]],
    min_votes: int = 2,
    min_confidence: float = 0.7,
    search_fallback_dates: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """
    对 platform == "抖音(汽水)" 且 artist 为空/为 "—"/为 None 的事件，
    尝试从其它平台事件中用同名 track 的投票结果补全 artist。
    若 search_fallback_dates 含某日，则该日热门搜索榜跳过 artist 补全（避免 fallback 引入不一致）。
    返回统计信息字典。
    """
    search_fallback_dates = search_fallback_dates or set()
    # 1. 构建跨平台字典（只用非抖音平台事件）
    artist_dict = _build_cross_platform_artist_dict(events)
    
    # 2. 统计和补全
    qishui_total = 0
    missing_artist_count = 0
    completed_count = 0
    completed_by_track: Dict[str, int] = {}
    
    search_missing = 0
    search_completed = 0
    search_samples: List[Tuple[str, str, float]] = []
    for e in events:
        platform = (e.get("platform") or "").strip()
        if platform != PLATFORM:
            continue
        qishui_total += 1
        is_search = ((e.get("charts") or [""])[0] or "") == CHART_SEARCH
        # 当日 search 使用 fallback 时：热门搜索不补 artist，保持 "—"
        if is_search and (e.get("date") or "") in search_fallback_dates:
            continue
        mv, mc = (3, 0.85) if is_search else (min_votes, min_confidence)
        artist = e.get("artist")
        if artist is None or (isinstance(artist, str) and (not artist.strip() or artist.strip() == "—")):
            missing_artist_count += 1
            if is_search:
                search_missing += 1
            track = (e.get("track") or "").strip()
            success, inferred_artist, confidence, candidates = _infer_artist_from_dict(
                e, artist_dict, min_votes=mv, min_confidence=mc, is_search_chart=is_search
            )
            if success:
                e["artist"] = inferred_artist
                e["artist_source"] = "inferred_from_other_platforms"
                e["artist_confidence"] = round(confidence, 3)
                if candidates:
                    e["artist_candidates"] = candidates[:3]  # top3
                completed_count += 1
                if track:
                    completed_by_track[track] = completed_by_track.get(track, 0) + 1
                if is_search:
                    search_completed += 1
                    if len(search_samples) < 5:
                        search_samples.append((track, inferred_artist, confidence))

    if search_missing > 0:
        print(f"  [INFO] 热门搜索 artist 缺失: {search_missing}, 补全成功: {search_completed}, "
              f"样例 Top5: {[(t, a, round(c,2)) for t,a,c in search_samples[:5]]}")

    # 3. 准备统计信息
    stats = {
        "qishui_total": qishui_total,
        "missing_artist_count": missing_artist_count,
        "completed_count": completed_count,
        "success_rate": round(completed_count / missing_artist_count * 100, 1) if missing_artist_count > 0 else 0.0,
        "top10_tracks": sorted(completed_by_track.items(), key=lambda x: -x[1])[:10],
    }
    return stats


def _dedupe_publish_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    publish 层去重：当 key 碰撞时合并字段（tags/charts 等）而不是丢弃。
    merge_key = (date, platform, chart_type, item_key)，不含 event_type，
    使「每日快照」与「连续3天Top10」「Top10稳定」等可合并为一条，保留全量 tags。
    衍生事件（回归榜、多榜爆发）用 event_type 区分，key 含 event_type 避免误合并。
    """
    # merge_key -> index in out（用于合并）
    merge_to_idx: Dict[tuple, int] = {}
    out: List[Dict[str, Any]] = []

    for e in events:
        date_str = e.get("date", "") or ""
        platform = (e.get("platform") or "").strip() or ""
        charts = e.get("charts") or []
        chart_type = (charts[0] if charts else "") or (e.get("chart_type") or "") or ""
        event_type = (e.get("event_type") or "").strip()
        item_key = e.get("item_key")
        if item_key is None or (isinstance(item_key, str) and not item_key.strip()):
            item_key = get_item_key(e)

        # 衍生事件（回归榜、多榜爆发）用完整 key，不与其他合并
        is_derived = event_type in ("回归榜", "多榜爆发")
        if is_derived:
            key = (date_str, platform, chart_type, event_type, item_key)
            if key in merge_to_idx:
                # 衍生事件自身重复则跳过
                continue
            merge_to_idx[key] = len(out)
            out.append(dict(e))
            continue

        # 非衍生事件：用 merge_key（不含 event_type）以便与每日快照等合并
        merge_key = (date_str, platform, chart_type, item_key)
        if merge_key in merge_to_idx:
            idx = merge_to_idx[merge_key]
            _merge_event_into(out[idx], e)
        else:
            merge_to_idx[merge_key] = len(out)
            out.append(dict(e))

    return out


def _clean_history_search_only(
    history_events: List[Dict[str, Any]],
    keep_dates: set,
    dry_run: bool,
) -> tuple:
    """
    仅对 platform==抖音(汽水) and source==douyin_qishui_app_ocr 且 charts[0]==热门搜索
    且在 keep_dates 内的事件做清洗：normalize、删噪声、按 (date, chart, normalized_term) 去重、重排前 20。
    返回 (cleaned_events 或 None 若 dry_run, stats_dict)。
    """
    stats = {
        "dates_cleaned": 0,
        "noise_removed": 0,
        "norm_dedup_dropped": 0,
        "dates_under_20": [],
        "history_path": None,
    }

    def is_target(e: Dict[str, Any]) -> bool:
        return (
            (e.get("platform") or "").strip() == PLATFORM
            and (e.get("source") or "").strip() == SOURCE
            and ((e.get("charts") or [""])[0] or "") == CHART_SEARCH
        )

    to_clean_by_date: Dict[str, List[Dict[str, Any]]] = {}
    rest: List[Dict[str, Any]] = []
    for e in history_events:
        if not is_target(e):
            rest.append(e)
            continue
        d = e.get("date", "")
        if d not in keep_dates:
            rest.append(e)
            continue
        to_clean_by_date.setdefault(d, []).append(e)

    if not to_clean_by_date:
        return (history_events if not dry_run else None, stats)

    stats["dates_cleaned"] = len(to_clean_by_date)
    cleaned_per_date: Dict[str, List[Dict[str, Any]]] = {}

    for date_str, group in to_clean_by_date.items():
        if dry_run:
            group = [dict(e) for e in group]
        before_n = len(group)
        # 1) normalize track
        for e in group:
            raw = (e.get("track") or "").strip()
            e["track"] = normalize_search_term(raw) if raw else "—"
        # 2) 删除噪声
        group = [e for e in group if not _is_search_track_noise(e.get("track") or "")]
        stats["noise_removed"] += before_n - len(group)
        if not group:
            cleaned_per_date[date_str] = []
            continue
        # 3) 按 (date, chart, normalized_term) 去重，保留 rank_now 更小
        n_before_dedup = len(group)
        group, drop_n = dedupe_search_by_normalized_term(group)
        stats["norm_dedup_dropped"] += drop_n
        # 4) 重排：rank_now<=0 放最后，取前 20，rank_now=1..N
        group = rerank_search_events_per_date(group, max_rank=20)
        if len(group) < 20:
            stats["dates_under_20"].append(date_str)
        cleaned_per_date[date_str] = group
        if before_n != len(group):
            print(f"  [INFO] search 榜 date={date_str} 清洗前={before_n} 条, 清洗后={len(group)} 条")

    out = rest + [e for d in sorted(cleaned_per_date.keys()) for e in cleaned_per_date[d]]
    return (out if not dry_run else None, stats)


def main() -> None:
    # 优先使用 static_site/data/upstream/（通常为本地下载的最新原始数据），否则回退到 data/upstream/
    _upstream_default = PROJECT_ROOT / "static_site" / "data" / "upstream" / "merged_events_latest.json"
    if not _upstream_default.exists():
        _upstream_default = PROJECT_ROOT / "data" / "upstream" / "merged_events_latest.json"
    parser = argparse.ArgumentParser(description="本地发布：净化 upstream + 合并汽水 -> static_site")
    parser.add_argument("--upstream", type=Path, default=_upstream_default)
    parser.add_argument("--history", type=Path, default=PROJECT_ROOT / "data" / "qishui_events_history.json")
    parser.add_argument("--qishui", type=Path, default=PROJECT_ROOT / "data" / "qishui_app_ocr_latest.json")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "static_site" / "data" / "merged_events_latest.json")
    parser.add_argument("--keep-days", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-qishui", action="store_true", help="不合并汽水")
    parser.add_argument("--clean-history-only", action="store_true", help="仅清洗 history 并写回，不生成站点 out 文件")
    parser.add_argument("--history-clean-days", type=int, default=60, help="只清洗最近 N 天的汽水事件（按 distinct date）")
    parser.add_argument("--clean-history-dry-run", action="store_true", help="只打印将删除/重排统计，不写回")
    args = parser.parse_args()

    upstream_path = args.upstream.resolve()
    history_path = args.history.resolve()
    qishui_path = args.qishui.resolve()
    out_path = args.out.resolve()
    keep_days = args.keep_days
    dry_run = args.dry_run
    no_qishui = args.no_qishui

    # 仅清洗 history 模式
    if args.clean_history_only:
        if not history_path.exists():
            print(f"  [ERROR] history 不存在: {history_path}")
            sys.exit(2)
        history_data = load_json(history_path)
        history_events: List[Dict[str, Any]] = list(history_data.get("events", []))
        dates_sorted = sorted(
            set(e.get("date", "") for e in history_events if e.get("date")),
            reverse=True,
        )
        keep_dates = set(dates_sorted[: args.history_clean_days])
        cleaned, stats = _clean_history_search_only(
            history_events,
            keep_dates,
            dry_run=args.clean_history_dry_run,
        )
        stats["history_path"] = str(history_path)
        print(f"  [INFO] 被清洗的 date 数量: {stats['dates_cleaned']}")
        print(f"  [INFO] search 删除噪声条数: {stats['noise_removed']}")
        print(f"  [INFO] search 按归一化词去重丢弃数量: {stats['norm_dedup_dropped']}")
        if stats["dates_under_20"]:
            print(f"  [WARN] search 重排后不足 20 的日期列表: {stats['dates_under_20']}")
        print(f"  [INFO] 写回 history 路径: {stats['history_path']}")
        if not args.clean_history_dry_run and cleaned is not None:
            save_json(history_path, {"events": cleaned})
            print(f"  [OK] 已写回: {history_path}")
        else:
            print(f"  [DRY-RUN] 未写回")
        return

    if not upstream_path.exists():
        print(f"  [ERROR] upstream 不存在: {upstream_path}")
        sys.exit(2)

    print(f"  [INFO] 使用 upstream: {upstream_path}")
    upstream_data = load_json(upstream_path)
    upstream_events: List[Dict[str, Any]] = upstream_data.get("events", [])
    print(f"  [INFO] upstream events: {len(upstream_events)}")

    # 临时打印 upstream event_type Top20
    _et_cnt: Dict[str, int] = {}
    for e in upstream_events:
        v = (e.get("event_type") or "").strip()
        if v:
            _et_cnt[v] = _et_cnt.get(v, 0) + 1
    _et_top = sorted(_et_cnt.items(), key=lambda x: -x[1])[:20]
    if _et_top:
        print(f"  [DEBUG] upstream event_type Top20: " + ", ".join(f"{k}={v}" for k, v in _et_top))
    else:
        print(f"  [DEBUG] upstream event_type: 空（可能用 tags 存储）")

    # event_type -> tags 归一化：确保 Top10稳定/连续3天Top10 进入 tags
    events_for_norm = list(upstream_events)
    n_norm = _ensure_top10_tags_from_event_type(events_for_norm)
    if n_norm > 0:
        print(f"  [INFO] event_type->tags 归一化: {n_norm} 条事件补充 Top10稳定/连续3天Top10")

    # 1. 净化掉出榜/每日快照（仅对输出做，upstream 不动）
    events, stats = _purify_events(events_for_norm)
    if stats["drop"] > 0:
        print(f"  [INFO] 净化删除「掉出榜」: {stats['drop']} 条")
    if stats["daily_snapshot"] > 0:
        print(f"  [INFO] 净化删除「每日快照」: {stats['daily_snapshot']} 条")
    if stats.get("qishui_tags", 0) > 0:
        print(f"  [INFO] 汽水 tags 清洗: {stats['qishui_tags']} 条")

    # 2–4. 若存在当日 OCR 则更新 history，从 history 取汽水窗口注入
    search_fallback_dates: Set[str] = set()
    if not no_qishui:
        history_events: List[Dict[str, Any]] = []
        if history_path.exists():
            history_data = load_json(history_path)
            history_events = history_data.get("events", [])
            # 每次 build：对 history 中每个 date 的 search 榜做 normalize+去重+重排（修复历史残留）
            _apply_search_cleanup_to_history(history_events, log=True)
        else:
            print(f"  [INFO] history 不存在，当作空: {history_path}")

        if qishui_path.exists():
            qishui_data = load_json(qishui_path)
            date_str = (qishui_data.get("date") or "").strip()
            if date_str:
                charts_obj = qishui_data.get("charts") or {}
                debug_obj = qishui_data.get("debug") or {}
                new_qishui_events: List[Dict[str, Any]] = []
                # 热门搜索榜：validity check + 完整性 + 防错位 + 回退
                search_debug = debug_obj.get("search") or {}
                search_parse_failed = search_debug.get("search_parse_failed", False)
                search_items = charts_obj.get("search") or []
                # 排查 ROI/分段：输出 debug 关键字段
                anchor_found = search_debug.get("anchor_found", None)
                raw_lines_strict = search_debug.get("raw_lines_strict") or []
                print(f"  [INFO] debug.search anchor_found={anchor_found}, raw_lines_strict len={len(raw_lines_strict)} preview={raw_lines_strict[:5]!r}")
                # 去除伪条目后计算缺失 rank（不允许错位：缺 rank 则回退）
                search_items_clean = [
                    it for it in search_items
                    if isinstance(it, dict) and not _is_search_track_noise((it.get("track_name") or "").strip())
                ]
                ranks_in_items = sorted(set(it.get("rank") for it in search_items_clean if it.get("rank") and 1 <= it.get("rank") <= 20))
                missing_ranks_from_items = [r for r in range(1, 21) if r not in ranks_in_items]
                search_complete_stats = search_debug.get("search_complete_stats") or {}
                search_ranks_parsed = search_complete_stats.get("search_ranks_parsed") or ranks_in_items
                missing_before_fill = search_complete_stats.get("missing_ranks_before_fill") or []
                missing_after_fill = search_complete_stats.get("missing_ranks_after_fill") or missing_ranks_from_items
                # validity check：rank 覆盖、无重复、track_name 长度/异常符号/UI 词
                search_valid, search_valid_reason = _check_search_items_validity(search_items, min_ranks=18, max_rank=20)
                use_search_fallback = (
                    search_parse_failed
                    or len(search_items) < 20
                    or len(missing_ranks_from_items) > 0
                    or not search_valid
                )
                fallback_reason = (
                    f"validity_fail={search_valid_reason}"
                    if not search_valid
                    else (missing_after_fill or missing_ranks_from_items)
                )
                if not search_valid:
                    print(f"  [WARN] search validity check failed: {search_valid_reason}")
                print(f"  [INFO] search ranks parsed: {search_ranks_parsed}")
                print(f"  [INFO] missing_ranks (before fill): {missing_before_fill}")
                print(f"  [INFO] missing_ranks (after fill): {missing_after_fill}")
                if use_search_fallback:
                    last_search = _get_last_successful_search_from_history(history_events)
                    if last_search:
                        # 回退时也过滤伪条目，避免把历史中的「半-半」等写入当日
                        last_search = [e for e in last_search if not _is_search_track_noise(e.get("track") or "")]
                        for e in last_search:
                            ev = dict(e)
                            ev["date"] = date_str
                            new_qishui_events.append(ev)
                        search_fallback_dates.add(date_str)
                        print(f"  [WARN] search fallback used: true, reason={fallback_reason!r}")
                        print(f"  [WARN] search 榜 date={date_str} 回退到 history 上一份 ({len(last_search)} 条)")
                    else:
                        for item in search_items_clean:
                            if isinstance(item, dict):
                                new_qishui_events.extend(_search_item_to_events(item, date_str))
                        print(f"  [WARN] search fallback used: false (无 history)，使用当日 OCR 条数: {len(search_items_clean)}")
                else:
                    for item in search_items_clean:
                        if isinstance(item, dict):
                            new_qishui_events.extend(_search_item_to_events(item, date_str))
                    print(f"  [INFO] search 榜 date={date_str} 使用 bbox 解析结果: {len(search_items_clean)} 条")
                    print(f"  [INFO] search fallback used: false")
                for chart_type in ("hot", "new", "artist"):
                    items = charts_obj.get(chart_type) or []
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        new_qishui_events.append(item_to_event(item, date_str, chart_type))
                if new_qishui_events:
                    _update_history_from_qishui(history_events, new_qishui_events, date_str)
                    print(f"  [INFO] history 同日替换更新: 新增 {len(new_qishui_events)} 条")
                    if not dry_run:
                        history_path.parent.mkdir(parents=True, exist_ok=True)
                        save_json(history_path, {"events": history_events})
        else:
            print(f"  [INFO] qishui OCR 不存在，跳过 history 更新: {qishui_path}")

        qishui_pruned = prune_events_last_n_days(history_events, keep_days)
        qishui_in_window = [e for e in qishui_pruned if _is_qishui_ocr(e)]
        events = [e for e in events if not _is_qishui_like(e)]
        events.extend(qishui_in_window)
        _clean_qishui_tags_broad(events)
        print(f"  [INFO] 注入汽水窗口: {len(qishui_in_window)} 条（keep_days={keep_days}）")
        # 本次 build 最终注入窗口里 4 榜各自条数
        qishui_by_chart_inject: Dict[str, int] = {}
        for e in qishui_in_window:
            ch = (e.get("charts") or [""])[0] or "—"
            qishui_by_chart_inject[ch] = qishui_by_chart_inject.get(ch, 0) + 1
        print(f"  [INFO] 注入窗口 4 榜条数: 热歌榜={qishui_by_chart_inject.get('热歌榜', 0)}, 新歌榜={qishui_by_chart_inject.get('新歌榜', 0)}, 热门搜索={qishui_by_chart_inject.get('热门搜索', 0)}, 音乐人榜={qishui_by_chart_inject.get('音乐人榜', 0)}")

    # 5. 站点整体 keep-days 裁剪（裁剪前保留全量用于回归榜计算）
    # 扩展 keep_dates：含 Top10稳定/连续3天Top10 的日期必须保留，避免被汽水近期日期挤出
    events_cleaned_all = list(events)
    events = _prune_events_keep_top10_dates(events, keep_days)

    # 6. 抖音(汽水) 数据源硬隔离 + 榜单白名单
    events, n_non_ocr, n_non_whitelist = _isolate_qishui_and_whitelist(events)
    if n_non_ocr > 0:
        print(f"  [INFO] 删除非 OCR 抖音事件: {n_non_ocr} 条")
    if n_non_whitelist > 0:
        print(f"  [WARN] 丢弃非白名单榜单汽水事件: {n_non_whitelist} 条")

    qishui_events = [e for e in events if _is_qishui_ocr(e)]
    qishui_by_chart: Dict[str, int] = {}
    for e in qishui_events:
        ch = (e.get("charts") or [""])[0] or "—"
        qishui_by_chart[ch] = qishui_by_chart.get(ch, 0) + 1
    print(f"  [INFO] 抖音(汽水) 事件: {len(qishui_events)} 条")
    for ch in ("热歌榜", "新歌榜", "热门搜索", "音乐人榜"):
        print(f"  [INFO]   - {ch}: {qishui_by_chart.get(ch, 0)} 条")

    # 衍生分析事件：回归榜、多榜爆发（只写入发布文件，不写 history）
    return_events = compute_return_events(events_cleaned_all, events, gap_days=7)
    multi_events = compute_multi_chart_surge_events(events, min_platforms=2)
    events.extend(return_events)
    events.extend(multi_events)
    if return_events:
        print(f"  [INFO] 衍生 回归榜: {len(return_events)} 条")
    if multi_events:
        print(f"  [INFO] 衍生 多榜爆发: {len(multi_events)} 条")
    events = _dedupe_publish_events(events)

    # 跨平台字典补全 artist（针对抖音(汽水)事件）；当日 search fallback 的日期不补热门搜索 artist
    artist_completion_stats = _complete_artists_for_qishui(
        events, min_votes=2, min_confidence=0.7, search_fallback_dates=search_fallback_dates
    )
    print(f"  [SUMMARY] 抖音 artist 缺失: {artist_completion_stats['missing_artist_count']}, "
          f"补全成功: {artist_completion_stats['completed_count']}, "
          f"成功率: {artist_completion_stats['success_rate']}%")
    if artist_completion_stats['top10_tracks']:
        top10_str = ", ".join(f"{track}({count})" for track, count in artist_completion_stats['top10_tracks'])
        print(f"  [SUMMARY] 补全 top10 track: {top10_str}")

    # 衍生事件验收 summary（仅打印）
    return_count = sum(1 for e in events if (e.get("event_type") or "").strip() == "回归榜")
    multi_count = sum(1 for e in events if (e.get("event_type") or "").strip() == "多榜爆发")
    multi_list = [e for e in events if (e.get("event_type") or "").strip() == "多榜爆发"]
    multi_by_platform_combo: Dict[tuple, int] = {}
    for e in multi_list:
        plats = tuple(e.get("platforms") or [])
        multi_by_platform_combo[plats] = multi_by_platform_combo.get(plats, 0) + 1
    top5_combos = sorted(multi_by_platform_combo.items(), key=lambda x: -x[1])[:5]
    multi_date_itemkey = [(e.get("date"), e.get("item_key") or get_item_key(e)) for e in multi_list]
    multi_dup = len(multi_date_itemkey) - len(set(multi_date_itemkey))
    print(f"  [SUMMARY] 回归榜: {return_count} 条 | 多榜爆发: {multi_count} 条")
    if top5_combos:
        print(f"  [SUMMARY] 多榜爆发 platforms 组合 Top5: " + ", ".join(f"{'+'.join(c)}={n}" for c, n in top5_combos))
    print(f"  [SUMMARY] 同日同 item_key 多榜爆发重复数: {multi_dup} (应为 0)")

    # merged 后 tags 计数 Top20（验收 连续3天Top10 / Top10稳定 不再为 0）
    tag_count: Dict[str, int] = {}
    for e in events:
        for t in (e.get("tags") or []):
            if isinstance(t, str) and t.strip():
                tag_count[t.strip()] = tag_count.get(t.strip(), 0) + 1
    top_tags = sorted(tag_count.items(), key=lambda x: -x[1])[:20]
    print(f"  [SUMMARY] merged 后 tags 计数 Top20: " + ", ".join(f"{k}={v}" for k, v in top_tags))
    c3 = tag_count.get("连续3天Top10", 0)
    t10 = tag_count.get("Top10稳定", 0)
    snap = tag_count.get("每日快照", 0)
    print(f"  [SUMMARY] 连续3天Top10={c3}, Top10稳定={t10}, 每日快照={snap} (连续3天Top10/Top10稳定 应>0)")

    date_dist: Dict[str, int] = {}
    for e in events:
        d = e.get("date", "")
        if d:
            date_dist[d] = date_dist.get(d, 0) + 1

    # 导出前确保 tags 不被丢弃（使用同一变量写出）
    out_events = _prepare_events_for_export(events)
    payload = {
        "generated_at": "",
        "total_events": len(out_events),
        "total_days": len(date_dist),
        "date_distribution": dict(sorted(date_dist.items(), key=lambda x: x[0])),
        "events": out_events,
    }
    try:
        from timezone_utils import beijing_now_iso
        payload["generated_at"] = beijing_now_iso()
    except Exception:
        payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"

    print(f"  [INFO] 最终 events: {len(out_events)}, 总天数: {len(date_dist)}")

    # 写文件前 debug：用写出变量验证 tags 计数（应 >0）
    _tag_cnt: Dict[str, int] = {}
    for e in out_events:
        for t in (e.get("tags") or []):
            if isinstance(t, str) and t.strip():
                _tag_cnt[t.strip()] = _tag_cnt.get(t.strip(), 0) + 1
    _c3 = _tag_cnt.get("连续3天Top10", 0)
    _t10 = _tag_cnt.get("Top10稳定", 0)
    _snap = _tag_cnt.get("每日快照", 0)
    print(f"  [DEBUG 写前] 连续3天Top10={_c3}, Top10稳定={_t10}, 每日快照={_snap} (写出变量)")

    if dry_run:
        print(f"  [DRY-RUN] 跳过写入: {out_path}")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(out_path, payload)
    print(f"  [OK] 写入: {out_path}")


if __name__ == "__main__":
    main()
