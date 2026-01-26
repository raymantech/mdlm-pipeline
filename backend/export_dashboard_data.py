#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出前端展示用的 JSON 数据 - 增量累加版

核心逻辑：
1. 读取历史数据（从现有 JSON 文件）
2. 从数据库导出新数据
3. 增量合并 + 去重
4. 全量写回

去重策略：
- 使用 (platform, track, artist, date) 作为唯一标识
- 同一标识的记录只保留最新的一条
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import sqlite3
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# 确保时区处理模块在路径中
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from timezone_utils import beijing_today, beijing_timestamp, date_range_beijing

DEFAULT_DB = ROOT / "charts.db"


def _json_loads_maybe(s: Any, default):
    if s is None:
        return default
    if isinstance(s, (list, dict)):
        return s
    if not isinstance(s, str):
        return default
    s = s.strip()
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def _ensure_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def _charts_to_names(charts_json: Any) -> List[str]:
    """
    charts_json usually comes from merged_event.charts_json
    """
    charts = _ensure_list(charts_json)
    out: List[str] = []
    for c in charts:
        if c is None:
            continue
        if isinstance(c, str):
            name = c.strip()
            if name:
                out.append(name)
        elif isinstance(c, dict):
            name = (c.get("chart_name") or c.get("name") or "").strip()
            if name:
                out.append(name)
    # de-dup, keep order
    seen = set()
    uniq = []
    for n in out:
        if n in seen:
            continue
        seen.add(n)
        uniq.append(n)
    return uniq


def generate_event_key(event: Dict[str, Any]) -> str:
    """
    生成事件的唯一标识符
    使用 (platform, track, artist, date) 组合
    
    兼容两种格式：
    - 新格式: track, artist, date
    - 旧格式: track_name, artist_name, day
    """
    platform = (event.get("platform") or "").strip().lower()
    
    # 兼容新旧字段名
    track = (event.get("track") or event.get("track_name") or "").strip().lower()
    artist = (event.get("artist") or event.get("artist_name") or "").strip().lower()
    date = (event.get("date") or event.get("day") or "").strip()
    
    # 创建稳定的哈希键
    key_str = f"{platform}||{track}||{artist}||{date}"
    return hashlib.md5(key_str.encode('utf-8')).hexdigest()


def normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    统一事件格式，确保使用新版字段名
    """
    normalized = {}
    
    # 标准字段（新格式优先）
    normalized["platform"] = event.get("platform") or ""
    normalized["track"] = event.get("track") or event.get("track_name") or ""
    normalized["artist"] = event.get("artist") or event.get("artist_name") or ""
    normalized["date"] = event.get("date") or event.get("day") or ""
    
    # 其他字段
    normalized["charts"] = event.get("charts") or []
    normalized["tags"] = event.get("tags") or []
    normalized["rank_now"] = event.get("rank_now") or event.get("best_rank_now")
    normalized["rank_prev"] = event.get("rank_prev") or event.get("best_rank_prev")
    normalized["delta"] = event.get("delta") or event.get("best_delta")
    normalized["severity"] = event.get("severity") or event.get("max_severity") or 1
    normalized["id"] = event.get("id")
    normalized["narrative"] = event.get("narrative") or ""
    normalized["source_event_ids"] = event.get("source_event_ids") or []
    
    return normalized


def load_existing_events(json_path: Path) -> List[Dict[str, Any]]:
    """
    加载现有的 JSON 文件中的事件
    如果文件不存在或解析失败，返回空列表
    """
    if not json_path.exists():
        print(f"[load] No existing file found at {json_path}")
        return []
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        events = data.get('events', [])
        if not isinstance(events, list):
            print(f"[load] Invalid events format in {json_path}")
            return []
        
        print(f"[load] Loaded {len(events)} existing events from {json_path}")
        return events
    except Exception as e:
        print(f"[load] Failed to load {json_path}: {e}")
        return []


def fetch_from_url(url: str) -> List[Dict[str, Any]]:
    """
    从 URL 获取历史数据（用于 GitHub Actions 环境）
    """
    try:
        import urllib.request
        import ssl
        
        # 创建不验证 SSL 的上下文（某些环境可能需要）
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        print(f"[fetch] Downloading from {url}...")
        with urllib.request.urlopen(url, timeout=30, context=ctx) as response:
            data = json.loads(response.read().decode('utf-8'))
        
        events = data.get('events', [])
        print(f"[fetch] Downloaded {len(events)} events from URL")
        return events
    except Exception as e:
        print(f"[fetch] Failed to fetch from {url}: {e}")
        return []


def export_from_db(db_path: Path, days: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    从数据库导出事件
    
    如果 days=None，导出所有数据
    如果 days>0，只导出最近 N 天
    """
    if not db_path.exists():
        print(f"[db] Database not found: {db_path}")
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # 构建查询
        if days and days > 0:
            start_date, end_date = date_range_beijing(days)
            sql = """
            SELECT
              id, merge_date, platform_name, track_name, artist_name_raw,
              tags_json, charts_json, best_rank_prev, best_rank_now,
              best_delta, max_severity
            FROM merged_event
            WHERE merge_date >= ?
            ORDER BY merge_date DESC, max_severity DESC, id DESC
            """
            rows = conn.execute(sql, (start_date,)).fetchall()
            print(f"[db] Exporting events from {start_date} to {end_date} ({days} days)")
        else:
            sql = """
            SELECT
              id, merge_date, platform_name, track_name, artist_name_raw,
              tags_json, charts_json, best_rank_prev, best_rank_now,
              best_delta, max_severity
            FROM merged_event
            ORDER BY merge_date DESC, max_severity DESC, id DESC
            """
            rows = conn.execute(sql).fetchall()
            print(f"[db] Exporting all events from database")

        events: List[Dict[str, Any]] = []
        for r in rows:
            tags = _ensure_list(_json_loads_maybe(r["tags_json"], []))
            charts = _charts_to_names(_json_loads_maybe(r["charts_json"], []))

            item = {
                "platform": r["platform_name"],
                "charts": charts,
                "track": r["track_name"],
                "artist": r["artist_name_raw"],
                "rank_now": r["best_rank_now"],
                "rank_prev": r["best_rank_prev"],
                "delta": r["best_delta"],
                "date": r["merge_date"],
                "tags": tags,
                "severity": r["max_severity"],
                "id": r["id"],
            }
            events.append(item)

        print(f"[db] Exported {len(events)} events from database")
        return events
    finally:
        conn.close()


def merge_events(
    existing_events: List[Dict[str, Any]], 
    new_events: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    合并历史事件和新事件，去重
    
    去重策略：
    - 使用 generate_event_key() 生成唯一标识
    - 新事件优先（覆盖旧的同标识事件）
    - 所有事件统一格式化
    - 最终按日期降序排序
    """
    # 使用字典进行快速去重，新事件覆盖旧事件
    merged: Dict[str, Dict[str, Any]] = {}
    
    # 先添加历史事件（格式化后）
    for event in existing_events:
        normalized = normalize_event(event)
        key = generate_event_key(normalized)
        merged[key] = normalized
    
    existing_count = len(merged)
    
    # 再添加新事件（会覆盖相同 key 的历史事件）
    new_added = 0
    updated = 0
    for event in new_events:
        normalized = normalize_event(event)
        key = generate_event_key(normalized)
        if key not in merged:
            new_added += 1
        else:
            updated += 1
        merged[key] = normalized
    
    # 转换回列表并排序（按日期降序，然后按严重程度降序）
    result = list(merged.values())
    result.sort(key=lambda x: (x.get("date", ""), x.get("severity", 0)), reverse=True)
    
    print(f"[merge] Existing unique: {existing_count}, New/Updated: {new_added + updated}, Total: {len(result)}")
    
    return result


def get_date_distribution(events: List[Dict[str, Any]]) -> Dict[str, int]:
    """统计每个日期的事件数量"""
    dist: Dict[str, int] = {}
    for ev in events:
        d = ev.get("date", "unknown")
        dist[d] = dist.get(d, 0) + 1
    return dict(sorted(dist.items(), reverse=True))


def main():
    ap = argparse.ArgumentParser(description="Export dashboard JSON with incremental merge")
    ap.add_argument("--db", default=str(DEFAULT_DB), 
                    help="Path to charts.db (default: backend/charts.db)")
    ap.add_argument("--days", type=int, 
                    default=int(os.getenv("EXPORT_DAYS", "0")),
                    help="Days to export from DB (0=all, default from EXPORT_DAYS env)")
    ap.add_argument("--out", 
                    default=str((ROOT.parent / "frontend" / "data" / "merged_events_latest.json").resolve()),
                    help="Output JSON path")
    ap.add_argument("--history-url",
                    default=os.getenv("HISTORY_JSON_URL", ""),
                    help="URL to fetch existing history (for GitHub Actions)")
    ap.add_argument("--no-merge", action="store_true",
                    help="Don't merge with existing, just export from DB")
    args = ap.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[export] Beijing today: {beijing_today().isoformat()}")
    print(f"[export] Output path: {out_path}")

    # 1. 加载历史数据
    existing_events: List[Dict[str, Any]] = []
    
    if not args.no_merge:
        # 优先从本地文件加载
        if out_path.exists():
            existing_events = load_existing_events(out_path)
        
        # 如果本地没有，尝试从 URL 获取
        if not existing_events and args.history_url:
            existing_events = fetch_from_url(args.history_url)

    # 2. 从数据库导出新数据
    # 如果有历史数据，只导出最近的数据进行合并
    # 如果没有历史数据，导出全部
    export_days = args.days if args.days > 0 else None
    if existing_events and not export_days:
        # 有历史数据时，默认只导出最近 30 天进行合并（优化性能）
        export_days = 30
        print(f"[export] Has existing data, exporting last {export_days} days for merge")
    
    new_events = export_from_db(db_path, days=export_days)

    # 3. 合并去重
    if args.no_merge or not existing_events:
        final_events = new_events
        print(f"[export] No merge, using {len(final_events)} events from DB")
    else:
        final_events = merge_events(existing_events, new_events)

    # 4. 生成日期分布统计
    date_dist = get_date_distribution(final_events)
    
    # 只显示最近 10 天的分布
    print(f"[export] Date distribution (recent 10 days):")
    for i, (d, count) in enumerate(date_dist.items()):
        if i >= 10:
            print(f"  ... and {len(date_dist) - 10} more days")
            break
        print(f"  {d}: {count} events")

    # 5. 写入文件
    payload = {
        "generated_at": beijing_timestamp(),
        "total_events": len(final_events),
        "total_days": len(date_dist),
        "date_distribution": date_dist,
        "events": final_events,
    }
    
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Exported {len(final_events)} events -> {out_path}")


if __name__ == "__main__":
    main()
