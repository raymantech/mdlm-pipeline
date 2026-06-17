#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
汽水音乐 App 截图榜单解析（DeepSeek 多模态）

使用 DeepSeek 多模态能力直接解析榜单截图，输出结构化 JSON（图片直传，不使用 OCR）。

运行示例：
  export DEEPSEEK_API_KEY=xxxx
  python backend/ocr_qishui_app_screenshots_deepseek.py
  python backend/ocr_qishui_app_screenshots_deepseek.py --dir /path/to/screenshots --out data/test.json
"""

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent

SOURCE_LABEL = "qishui_app_screenshot_llm_deepseek"
DEFAULT_OUT = PROJECT_ROOT / "data" / "qishui_app_llm_latest.json"

# DeepSeek API（OpenAI 兼容）
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-chat"

MAX_ENTRIES = 20


def beijing_today_iso() -> str:
    """北京时间当天 YYYY-MM-DD（不依赖项目其他模块）。"""
    beijing = timezone(timedelta(hours=8))
    return datetime.now(beijing).date().isoformat()


def beijing_now_iso() -> str:
    """当前时间 ISO8601（北京时间）。"""
    beijing = timezone(timedelta(hours=8))
    return datetime.now(beijing).replace(microsecond=0).isoformat()


def _check_env() -> str:
    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        print("  [ERROR] 缺少 DEEPSEEK_API_KEY，请设置环境变量：")
        print("    export DEEPSEEK_API_KEY=xxxx")
        sys.exit(1)
    return api_key


def _image_to_base64(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _get_image_media_type(path: Path) -> str:
    suf = (path.suffix or "").lower()
    if suf in (".png",):
        return "image/png"
    if suf in (".jpg", ".jpeg",):
        return "image/jpeg"
    return "image/png"


def _build_prompt(chart_type: str) -> str:
    return f"""以下图片是汽水音乐 App 的【{chart_type}】截图，请直接从图片中识别榜单内容。

规则（必须严格遵守）：
1. 只输出 Top 20，rank 为 1 到 20 的整数。
2. 每条记录包含：rank（整数）, track_name（歌曲名）, artist_name（艺人名；识别不到则填空字符串 ""）。
3. 必须忽略 UI 噪音：返回、搜索、榜单标题、按钮、icon、时间、标签等均不要输出。
4. 只输出一个 JSON 数组，格式为：[{{"rank": 1, "track_name": "歌曲名", "artist_name": "艺人名"}}, ...]
5. 按 rank 从小到大排序，去重。
6. 只允许输出 JSON，不要解释文字，不要 Markdown 代码块，不要多余字段。"""


def _call_deepseek_vision(
    image_path: Path,
    chart_type: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
) -> tuple[str, str, Optional[Dict[str, Any]]]:
    """
    调用 DeepSeek 多模态 API 解析一张榜单图。
    返回 (model_used, raw_response_full, parsed_list_or_None)。
    raw_response_full：完整 API 响应体（JSON 字符串），用于 debug。
    """
    try:
        import requests
    except ImportError:
        print("  [ERROR] 请安装 requests: pip install requests")
        sys.exit(1)

    b64 = _image_to_base64(image_path)
    media_type = _get_image_media_type(image_path)
    data_uri = f"data:{media_type};base64,{b64}"

    prompt = _build_prompt(chart_type)
    # DeepSeek 多模态：每张图单独一条 user message；先传图片再传文本；必须用 content 中的 image / image_url 传图，不得把 base64 拼进 prompt
    # 官方常见两种：1) type "image_url" + image_url.url（data URI，OpenAI 兼容） 2) type "image" + image.data + image.format
    # 优先使用 image_url（data URI），与 OpenAI 多模态一致
    content_parts: List[Dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": data_uri}},
        {"type": "text", "text": prompt},
    ]
    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": content_parts},
    ]
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        raw = str(e)
        if hasattr(e, "response") and e.response is not None and getattr(e.response, "text", None):
            raw = e.response.text or raw
        return model, raw, None
    except Exception as e:
        return model, str(e), None

    # debug 中保留完整模型返回（整个 API 响应体）
    full_raw = json.dumps(data, ensure_ascii=False, indent=2)

    choice = (data.get("choices") or [None])[0]
    if not choice:
        print("  [WARN] API 返回无 choices，完整响应已写入 debug.raw_response")
        return data.get("model") or model, full_raw, None
    msg = choice.get("message") or {}
    content = (msg.get("content") or "").strip()
    model_used = data.get("model") or model

    # 尝试从 content 中提取 JSON（可能被包在 ```json ... ``` 里）
    parsed = None
    if content:
        # 先直接解析
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            pass
        if parsed is None:
            # 尝试去掉 markdown 代码块
            m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
            if m:
                try:
                    parsed = json.loads(m.group(1).strip())
                except json.JSONDecodeError:
                    pass
        if parsed is None:
            # 尝试找第一个 [ 或 {
            for start in ("[", "{"):
                idx = content.find(start)
                if idx >= 0:
                    try:
                        parsed = json.loads(content[idx:])
                        break
                    except json.JSONDecodeError:
                        continue

    # 模型返回非 JSON 时不静默：打印警告，完整响应已写入 debug.raw_response
    if content and parsed is None:
        print("  [WARN] 模型返回非 JSON，已写入 debug.raw_response，请检查图片是否被正确识别")

    return model_used, full_raw, parsed


def _normalize_chart_entries(raw: Any) -> List[Dict[str, Any]]:
    """
    将模型返回的结构规范化为 List[{rank, track_name, artist_name}]。
    支持：直接数组、或 {"items": [...]} / {"list": [...]} 等。
    """
    entries: List[Dict[str, Any]] = []
    if raw is None:
        return entries

    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("items") or raw.get("list") or raw.get("entries") or raw.get("chart") or []
        if not isinstance(items, list):
            items = []
    else:
        return entries

    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        rank_val = item.get("rank")
        if rank_val is None:
            rank_val = item.get("rank_num") or item.get("index")
        try:
            rank = int(rank_val)
        except (TypeError, ValueError):
            continue
        if rank < 1 or rank > MAX_ENTRIES or rank in seen:
            continue
        seen.add(rank)
        track_name = (item.get("track_name") or item.get("title") or item.get("song") or item.get("name") or "")
        if isinstance(track_name, str):
            track_name = track_name.strip()[:500]
        else:
            track_name = str(track_name).strip()[:500]
        artist_name = (item.get("artist_name") or item.get("artist") or item.get("singer") or "")
        if isinstance(artist_name, str):
            artist_name = artist_name.strip()[:200]
        else:
            artist_name = str(artist_name).strip()[:200]
        entries.append({
            "rank": rank,
            "track_name": track_name or "",
            "artist_name": artist_name or "",
        })

    entries.sort(key=lambda x: x["rank"])
    return entries[:MAX_ENTRIES]


def process_one_image(
    image_path: Path,
    chart_type: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
) -> tuple[List[Dict[str, Any]], str, str, Optional[Dict[str, Any]]]:
    """
    处理一张图，返回 (entries, model_used, raw_response, parsed_raw)。
    若解析失败，entries 为空列表。
    """
    if not image_path.exists():
        print(f"  [WARN] 文件不存在，跳过: {image_path}")
        return [], "", "", None

    model_used, raw, parsed = _call_deepseek_vision(image_path, chart_type, api_key, model)
    entries = _normalize_chart_entries(parsed)
    print(f"  [INFO] {image_path.name} ({chart_type}) -> 解析 {len(entries)} 条")
    return entries, model_used, raw, parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="汽水音乐 App 截图榜单解析（DeepSeek 多模态）")
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path("/Users/ray/Desktop/qishui_inbox"),
        help="截图所在文件夹",
    )
    parser.add_argument(
        "--hot",
        type=str,
        default="qishui_hot.png",
        help="热歌榜截图文件名",
    )
    parser.add_argument(
        "--new",
        type=str,
        default="qishui_new.png",
        help="新歌榜截图文件名",
    )
    parser.add_argument(
        "--date",
        type=str,
        default="",
        help="日期 YYYY-MM-DD，默认北京时间当天",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="输出 JSON 路径",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help="DeepSeek 模型名（默认 deepseek-chat）",
    )
    args = parser.parse_args()

    api_key = _check_env()
    date_str = (args.date or beijing_today_iso()).strip()
    generated_at = beijing_now_iso()

    hot_path = args.dir / args.hot
    new_path = args.dir / args.new

    hot_entries, hot_model, hot_raw, _ = process_one_image(hot_path, "热歌榜", api_key, args.model)
    new_entries, new_model, new_raw, _ = process_one_image(new_path, "新歌榜", api_key, args.model)

    # debug：model 取任一次调用的值；raw_response 保留两次调用的原始返回（便于解析失败时排查）
    debug_model = hot_model or new_model or args.model
    raw_parts = []
    if hot_raw:
        raw_parts.append(f"[热歌榜]\n{hot_raw}")
    if new_raw:
        raw_parts.append(f"[新歌榜]\n{new_raw}")
    debug_raw = "\n\n".join(raw_parts)

    out_data: Dict[str, Any] = {
        "generated_at": generated_at,
        "date": date_str,
        "source": SOURCE_LABEL,
        "charts": {
            "hot": hot_entries,
            "new": new_entries,
        },
        "debug": {
            "model": debug_model,
            "raw_response": debug_raw,
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)

    print(f"  [OK] 已写入: {args.out}")
    print(f"       热歌榜 {len(hot_entries)} 条，新歌榜 {len(new_entries)} 条")


if __name__ == "__main__":
    main()
