#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
汽水音乐 App 截图榜单 OCR（腾讯云 GeneralBasicOCR）

【稳定骨架 v3】
- 只做一件事：读取四张截图，调用腾讯云 GeneralBasicOCR（带 bbox），解析出 rank 1..20 的列表项
- 解析策略：以"左侧 rank 数字列"为唯一锚点，按 y-band 找右侧最近文本作为 name
- 统一后处理 normalize_name：清理 OCR 噪声、去除 rank 粘连、去除徽标等
- 最低可用阈值：每榜 parse 出 >=16 条就算成功；否则回退使用上一次 data/qishui_app_ocr_latest.json 里对应榜单的数据

输入：/Users/ray/Desktop/qishui_inbox/
  - qishui_search.png / qishui_hot.png / qishui_new.png / qishui_artist.png
输出：
  - data/qishui_app_ocr_latest.json
"""

import base64
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUT_JSON = DATA_DIR / "qishui_app_ocr_latest.json"

# 默认截图目录
DEFAULT_INBOX_DIR = Path("/Users/ray/Desktop/qishui_inbox")

SOURCE_LABEL = "qishui_app_screenshot_ocr_tencent"

# Rank 数字正则：1-20
RANK_RE = re.compile(r"^\d{1,2}$")

# UI 文案关键词（用于过滤无效条目）
UI_NOISE_WORDS = {"热门搜索", "热歌榜", "新歌榜", "音乐人榜", "会员", "汽水"}


@dataclass
class Det:
    """OCR 检测结果"""
    text: str
    left: float
    right: float
    top: float
    bottom: float
    cx: float  # center x
    cy: float  # center y

    @property
    def w(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def h(self) -> float:
        return max(0.0, self.bottom - self.top)


def _check_env() -> Tuple[str, str, str]:
    """检查腾讯云环境变量"""
    sid = (os.environ.get("TENCENT_SECRET_ID") or "").strip()
    sk = (os.environ.get("TENCENT_SECRET_KEY") or "").strip()
    region = (os.environ.get("TENCENT_REGION") or "ap-guangzhou").strip()
    if not sid or not sk:
        print("[ERROR] 缺少腾讯云密钥环境变量：TENCENT_SECRET_ID / TENCENT_SECRET_KEY")
        sys.exit(1)
    return sid, sk, region


def _image_to_base64(path: Path) -> str:
    """图片转 base64"""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _run_tencent_general_basic_ocr(image_path: Path, sid: str, sk: str, region: str) -> List[Det]:
    """
    调用腾讯云 GeneralBasicOCR，返回带 bbox 的检测结果
    """
    try:
        from tencentcloud.common.credential import Credential
        from tencentcloud.ocr.v20181119 import ocr_client, models
    except Exception as e:
        print("[ERROR] 未安装腾讯云 OCR SDK：pip install tencentcloud-sdk-python-ocr")
        raise

    cred = Credential(sid, sk)
    client = ocr_client.OcrClient(cred, region)

    req = models.GeneralBasicOCRRequest()
    req.ImageBase64 = _image_to_base64(image_path)

    resp = client.GeneralBasicOCR(req)
    dets: List[Det] = []

    for item in (resp.TextDetections or []):
        text = (getattr(item, "DetectedText", "") or "").strip()
        poly = getattr(item, "Polygon", None)

        # 提取 Polygon 4 个点的坐标
        xs: List[float] = []
        ys: List[float] = []
        if poly:
            for pt in poly:
                x = getattr(pt, "X", None)
                y = getattr(pt, "Y", None)
                if x is not None and y is not None:
                    xs.append(float(x))
                    ys.append(float(y))

        if not xs or not ys:
            # 没有 bbox 就跳过：本骨架必须用 bbox 稳定解析
            continue

        left, right = min(xs), max(xs)
        top, bottom = min(ys), max(ys)
        cx = (left + right) / 2.0
        cy = (top + bottom) / 2.0

        dets.append(Det(text=text, left=left, right=right, top=top, bottom=bottom, cx=cx, cy=cy))

    return dets


def clean_item_text(s: str) -> str:
    """
    统一清洗函数：清理 OCR 噪声、序号、标签等
    
    规则：
    1. s = s.strip()
    2. 去掉前导垃圾字符：反复剥离直到首字符是 [0-9A-Za-z中文] 或字符串为空
    3. 去掉开头序号：严格限制为 1..20 才能剥离
       - 仅当开头数字 n 在 1..20 且后面有非空内容时，才剥离 n 及其后的分隔符（空格/点/顿号）
       - 如果字符串是纯数字（如 "707"），绝对不能清成空，必须保留原值
    4. 去掉行尾标签：若末尾是单独的 热 或 新（前面是空格或直接相邻），删掉它
    5. 最后再 strip() 一次；如果结果为空返回空
    6. 极小白名单纠错：如果清洗后的结果严格等于 "半一半"，则返回 "一半一半"
    """
    if not s:
        return ""
    
    s = s.strip()
    if not s:
        return ""
    
    # 2. 去掉前导垃圾字符：反复剥离直到首字符是 [0-9A-Za-z中文] 或字符串为空
    # 使用循环确保彻底清理，例如 "-半一半" -> "半一半"
    while s:
        # 检查首字符是否是有效字符
        first_char = s[0]
        if first_char.isdigit() or first_char.isalpha() or ('\u4e00' <= first_char <= '\u9fff'):
            break
        # 去掉所有前导非中英文数字字符
        old_s = s
        s = re.sub(r"^[^0-9A-Za-z\u4e00-\u9fff]+", "", s)
        s = s.strip()
        if s == old_s or not s:
            break
    
    if not s:
        return ""
    
    # 3. 去掉开头序号：严格限制为 1..20 才能剥离
    # 只有当开头数字在 1..20 范围内，且后面还有非空内容时，才剥离
    # 如果字符串是纯数字（如 "707"），绝对不能清成空
    m = re.match(r'^\s*(\d{1,2})([\.、\s]+)?(.*)$', s)
    if m:
        prefix_num = int(m.group(1))
        remaining = m.group(3) if m.group(3) else ""
        # 仅当开头数字 n 在 1..20 且后面有非空内容时，才剥离
        if 1 <= prefix_num <= 20 and remaining.strip():
            s = remaining.strip()
        # 否则不动 s（保留纯数字歌名如 "707"）
    
    # 4. 去掉行尾标签：若末尾是单独的 热 或 新（前面是空格或直接相邻）
    s = re.sub(r"\s+[热新]$", "", s)  # 空格+热/新
    s = re.sub(r"[热新]$", "", s)  # 直接相邻的热/新
    
    # 5. 最后再 strip() 一次
    s = s.strip()
    
    # 6. 去掉前导非法字符（如 -, ·, _, ., 空格等）
    # 仅去除"前导"非法字符，不要全局替换，不要删除中间的 -，不要影响英文歌名
    s = re.sub(r'^[^\w\u4e00-\u9fa5]+', '', s)
    
    # 7. 极小白名单纠错：如果清洗后的结果严格等于 "半一半"，则返回 "一半一半"
    if s == "半一半":
        s = "一半一半"
    
    return s


def normalize_name(rank: int, name: str, chart_type: str) -> str:
    """
    统一的后处理 normalize_name：
    1. strip 末尾徽标：空格+热/新；并丢弃独立 token "热"/"新"
    2. 去掉行首所有噪声符号：^[^0-9A-Za-z\u4e00-\u9fff]+
    3. 去掉与当前 rank 相同的行首粘连数字（仅当 name 以 f"{rank}" 开头，且后续字符不是数字时才剥离）
    4. 清理开头的 "£0" / "(0" / "0 " / "O " 等 OCR 垃圾前缀
    5. 最终再做一次 strip
    """
    if not name:
        return ""
    
    s = name.strip()
    
    # 1. 去掉末尾徽标：空格+热/新
    s = re.sub(r"\s+[热新]$", "", s)
    
    # 2. 去掉行首所有噪声符号（保留数字、字母、中文）
    s = re.sub(r"^[^0-9A-Za-z\u4e00-\u9fff]+", "", s)
    
    # 3. 去掉与当前 rank 相同的行首粘连数字
    # 规则：仅当 name 以 f"{rank}" 开头，且后续字符不是数字时才剥离
    rank_prefix = str(rank)
    if s.startswith(rank_prefix):
        # 检查后续字符
        remaining = s[len(rank_prefix):]
        if remaining and not remaining[0].isdigit():
            # 后续不是数字，可以安全剥离 rank 前缀
            s = remaining
        # 如果后续是数字，说明可能是歌名本身包含数字（如 "707"），不剥离
    
    # 4. 清理开头的 OCR 垃圾前缀
    # 匹配 "£0" / "(0" / "0 " / "O " 等
    s = re.sub(r"^£0\s*", "", s)
    s = re.sub(r"^\(0\s*", "", s)
    s = re.sub(r"^0\s+", "", s)
    s = re.sub(r"^O\s+", "", s)
    
    # 5. 最终 strip
    s = s.strip()
    
    return s


def _is_valid_entry(name: str, chart_key: str = "") -> bool:
    """
    判断是否为有效条目：
    - name 长度 >= 2
    - 对于 search/hot/new：允许纯数字歌名（len >= 2 且 int(name) 不在 1..20）
    - 对于 artist：不允许纯数字
    - 不是 UI 文案（热门搜索/热歌榜/新歌榜/音乐人榜/会员/汽水）
    """
    if not name or len(name) < 2:
        return False
    
    # 纯数字处理：对于 search/hot/new 的 track_name，允许符合条件的纯数字
    if name.isdigit():
        if chart_key in ("search", "hot", "new"):
            # 允许：len(name) >= 2 且 int(name) 不在 1..20
            num_value = int(name)
            if len(name) >= 2 and not (1 <= num_value <= 20):
                # 允许纯数字歌名（如 "707"）
                pass
            else:
                # 长度不够或值在 1..20 范围内，视为无效（可能是 rank 数字）
                return False
        else:
            # artist 榜或其他情况，不允许纯数字
            return False
    
    # 不包含 UI 文案关键词
    for noise in UI_NOISE_WORDS:
        if noise in name:
            return False
    
    return True


def _is_likely_track_name(text: str) -> bool:
    """
    判断文本是否像歌名：
    - 优先包含中文/英文字符且长度>1
    - 排除纯数字/纯符号
    """
    if not text or len(text) < 2:
        return False
    
    # 纯数字或纯符号
    if text.isdigit():
        return False
    
    # 检查是否包含中文或英文字符
    has_chinese = any('\u4e00' <= c <= '\u9fff' for c in text)
    has_english = any(c.isalpha() for c in text)
    
    return has_chinese or has_english


def _cluster_lines_by_y(dets: List[Det], image_width: float) -> List[List[Det]]:
    """
    将 detections 按 y 坐标聚类成行
    返回按 y 排序的行列表
    """
    if not dets:
        return []
    
    # 计算 median bbox height
    heights = sorted([d.h for d in dets if d.h > 0])
    if not heights:
        return []
    median_h = heights[len(heights) // 2]
    line_threshold = 0.45 * median_h
    
    # 按 cy 排序
    sorted_dets = sorted(dets, key=lambda d: d.cy)
    
    lines: List[List[Det]] = []
    current_line: List[Det] = []
    current_y = None
    
    for d in sorted_dets:
        if current_y is None:
            current_y = d.cy
            current_line = [d]
        elif abs(d.cy - current_y) <= line_threshold:
            # 同一行
            current_line.append(d)
        else:
            # 新行
            if current_line:
                lines.append(current_line)
            current_line = [d]
            current_y = d.cy
    
    if current_line:
        lines.append(current_line)
    
    return lines


def _parse_by_line_order_fallback(dets: List[Det], chart_key: str, image_width: float) -> Tuple[List[Dict[str, Any]], Dict[int, List[str]], List[List[Det]]]:
    """
    按物理行序生成 1..20 的 fallback：
    1. 把 OCR 文本框按 y 聚类成 20 行左右
    2. 为每个物理行保留 candidates 列表（按 x 从左到右排序）
    3. 逐个尝试 clean_item_text 后选择第一个有效的
    4. 如果某个 rank 最终无有效文本，再尝试：
       a) 该行 candidates 的下一个有效文本
       b) 若仍无，允许从相邻 y 最近的行的候选里挑一个"未被使用过"的有效文本（只允许一次）
    5. 用行序强制 rank=1..20 赋值
    
    返回：(items, rank_candidates_dict, lines) 其中：
    - items: 解析出的条目列表
    - rank_candidates_dict: 存储每个 rank 的候选文本列表
    - lines: 物理行列表（用于相邻行查找）
    """
    # 过滤掉独立的"热"/"新"徽标 token 和 rank 数字
    filtered_dets = []
    for d in dets:
        text_stripped = d.text.strip()
        # 跳过独立的徽标 token
        if text_stripped in {"热", "新"}:
            if d.w < image_width * 0.08:
                continue
        # 跳过 rank 数字
        if RANK_RE.match(text_stripped) and 1 <= int(text_stripped) <= 20:
            continue
        filtered_dets.append(d)
    
    # 按 y 聚类成行
    lines = _cluster_lines_by_y(filtered_dets, image_width)
    
    items: List[Dict[str, Any]] = []
    rank_candidates_dict: Dict[int, List[str]] = {}
    used_texts: set = set()  # 记录已使用的文本，避免重复使用
    
    # 第一遍：为每个 rank 尝试当前行的候选
    for rank in range(1, 21):
        line_idx = rank - 1
        if line_idx >= len(lines):
            break
        
        line = lines[line_idx]
        # 按 x 排序，获取候选文本列表
        line_sorted = sorted(line, key=lambda d: d.left)
        line_texts = [d.text.strip() for d in line_sorted]
        
        # 保存候选文本列表
        rank_candidates_dict[rank] = line_texts
        
        # 逐个尝试候选文本，选择第一个有效的
        best_text = None
        for text in line_texts:
            if not text or text in used_texts:
                continue
            
            # 清洗文本
            cleaned_name = clean_item_text(text)
            
            # 检查是否有效
            if cleaned_name and _is_valid_entry(cleaned_name, chart_key):
                best_text = cleaned_name
                used_texts.add(text)
                break
        
        # 如果当前行没找到，尝试拼接整行
        if not best_text:
            raw_text = " ".join(line_texts).strip()
            if raw_text and raw_text not in used_texts:
                cleaned_name = clean_item_text(raw_text)
                if cleaned_name and _is_valid_entry(cleaned_name, chart_key):
                    best_text = cleaned_name
                    used_texts.add(raw_text)
        
        if best_text:
            # 构建 item
            if chart_key == "artist":
                item = {"rank": rank, "track_name": "", "artist_name": best_text}
            else:
                item = {"rank": rank, "track_name": best_text, "artist_name": ""}
            items.append(item)
    
    # 第二遍：对于仍缺的 rank，从相邻行找候选（只允许一次）
    missing_ranks = [r for r in range(1, 21) if r not in [it["rank"] for it in items]]
    
    for rank in missing_ranks:
        line_idx = rank - 1
        
        # 尝试从相邻行找候选
        candidates_from_adjacent = []
        
        # 检查上一行
        if line_idx > 0 and line_idx - 1 < len(lines):
            prev_line = lines[line_idx - 1]
            prev_line_sorted = sorted(prev_line, key=lambda d: d.left)
            for d in prev_line_sorted:
                text = d.text.strip()
                if text and text not in used_texts:
                    candidates_from_adjacent.append((d.cy, text))
        
        # 检查下一行
        if line_idx + 1 < len(lines):
            next_line = lines[line_idx + 1]
            next_line_sorted = sorted(next_line, key=lambda d: d.left)
            for d in next_line_sorted:
                text = d.text.strip()
                if text and text not in used_texts:
                    candidates_from_adjacent.append((d.cy, text))
        
        # 按 y 距离排序（选择最近的）
        if candidates_from_adjacent:
            current_line_y = lines[line_idx][0].cy if line_idx < len(lines) else 0.0
            candidates_from_adjacent.sort(key=lambda x: abs(x[0] - current_line_y))
            
            # 尝试第一个候选
            for _, text in candidates_from_adjacent:
                cleaned_name = clean_item_text(text)
                if cleaned_name and _is_valid_entry(cleaned_name, chart_key):
                    used_texts.add(text)
                    if chart_key == "artist":
                        item = {"rank": rank, "track_name": "", "artist_name": cleaned_name}
                    else:
                        item = {"rank": rank, "track_name": cleaned_name, "artist_name": ""}
                    items.append(item)
                    break
    
    # 按 rank 排序
    items.sort(key=lambda x: x["rank"])
    
    return items, rank_candidates_dict, lines


def _dedupe_repair_pass(items: List[Dict[str, Any]], rank_candidates_dict: Dict[int, List[str]], chart_key: str) -> Tuple[List[Dict[str, Any]], List[int]]:
    """
    去重修复：扫描 1..20，如果 track_name 与上一条相同（或在全榜内出现重复且相邻），
    则尝试从该 rank 的候选文本选择下一个最合理的替代。
    
    返回：(修复后的 items 列表, 发现重复的 rank 列表)
    """
    if not items:
        return items, []
    
    # 构建 rank -> item 的映射
    items_dict: Dict[int, Dict[str, Any]] = {it["rank"]: it for it in items}
    
    # 构建 name -> ranks 的映射（用于检测全榜重复）
    name_to_ranks: Dict[str, List[int]] = {}
    for it in items:
        name = (it.get("track_name") or it.get("artist_name") or "").strip()
        if name:
            name_to_ranks.setdefault(name, []).append(it["rank"])
    
    repaired_items = []
    duplicates_found = []
    
    for rank in range(1, 21):
        if rank not in items_dict:
            continue
        
        item = items_dict[rank]
        current_name = (item.get("track_name") or item.get("artist_name") or "").strip()
        
        # 检查是否与上一条重复
        prev_rank = rank - 1
        is_duplicate = False
        if prev_rank in items_dict:
            prev_name = (items_dict[prev_rank].get("track_name") or items_dict[prev_rank].get("artist_name") or "").strip()
            if current_name == prev_name and current_name:
                is_duplicate = True
        
        # 检查是否在全榜内出现重复且相邻
        if not is_duplicate and current_name:
            ranks_with_same_name = name_to_ranks.get(current_name, [])
            if len(ranks_with_same_name) > 1:
                # 检查是否有相邻的重复
                for other_rank in ranks_with_same_name:
                    if other_rank != rank and abs(other_rank - rank) <= 1:
                        is_duplicate = True
                        break
        
        if is_duplicate:
            duplicates_found.append(rank)
            
            # 尝试从候选文本中选择替代
            candidates = rank_candidates_dict.get(rank, [])
            replacement_found = False
            
            for candidate_text in candidates:
                if not candidate_text or not candidate_text.strip():
                    continue
                
                # 清洗候选文本
                cleaned_candidate = clean_item_text(candidate_text)
                
                # 检查替代条件：清洗后长度>=2、不是纯数字、且与相邻条目不同
                if not cleaned_candidate or len(cleaned_candidate) < 2:
                    continue
                if cleaned_candidate.isdigit():
                    continue
                
                # 检查是否与相邻条目不同
                is_different = True
                for check_rank in [rank - 1, rank + 1]:
                    if check_rank in items_dict:
                        check_name = (items_dict[check_rank].get("track_name") or items_dict[check_rank].get("artist_name") or "").strip()
                        if cleaned_candidate == check_name:
                            is_different = False
                            break
                
                if is_different and cleaned_candidate != current_name:
                    # 找到替代，更新 item
                    if chart_key == "artist":
                        item = {"rank": rank, "track_name": "", "artist_name": cleaned_candidate}
                    else:
                        item = {"rank": rank, "track_name": cleaned_candidate, "artist_name": ""}
                    items_dict[rank] = item
                    replacement_found = True
                    break
            
            if not replacement_found:
                print(f"[WARN] rank={rank} 发现重复 name='{current_name}'，但未找到替代候选")
        
        repaired_items.append(item)
    
    return repaired_items, duplicates_found


def parse_ranked_list_from_detections(dets: List[Det], chart_key: str, image_width: float) -> Tuple[List[Dict[str, Any]], Dict[int, List[str]]]:
    """
    从 detections 解析榜单列表
    
    策略：
    1. 找到所有 rank 数字框（1..20）
    2. 对每个 rank，按 y-band 找右侧最近文本作为 name
    3. 使用 normalize_name 后处理
    4. 过滤无效条目
    
    返回：(items, rank_candidates_dict) 其中 rank_candidates_dict 存储每个 rank 的候选文本列表
    """
    # 1. 找到所有 rank 数字框
    rank_dets: Dict[int, Det] = {}
    for d in dets:
        text_stripped = d.text.strip()
        if RANK_RE.match(text_stripped):
            rank_num = int(text_stripped)
            if 1 <= rank_num <= 20:
                # 如果同一个 rank 出现多次，保留 y 坐标更小的（更靠上）
                if rank_num not in rank_dets or d.cy < rank_dets[rank_num].cy:
                    rank_dets[rank_num] = d
    
    # 2. 过滤掉独立的"热"/"新"徽标 token
    filtered_dets = []
    for d in dets:
        text_stripped = d.text.strip()
        # 跳过独立的徽标 token
        if text_stripped in {"热", "新"}:
            # 检查宽度，如果太窄则跳过（避免误杀歌名里的"热"/"新"）
            if d.w < image_width * 0.08:
                continue
        filtered_dets.append(d)
    
    # 3. 对每个 rank，按 y-band 找右侧最近文本
    items: List[Dict[str, Any]] = []
    rank_candidates_dict: Dict[int, List[str]] = {}  # rank -> [候选文本列表]
    
    for rank in range(1, 21):
        if rank not in rank_dets:
            continue
        
        rank_det = rank_dets[rank]
        rank_y = rank_det.cy
        rank_right = rank_det.right
        
        # 计算 y-band：rank 的 y ± 0.5 * rank 的高度
        y_band_half = rank_det.h * 0.5
        y_min = rank_y - y_band_half
        y_max = rank_y + y_band_half
        
        # 找到 y-band 内、rank 右侧的文本
        candidates: List[Tuple[float, str]] = []  # (distance_from_rank_right, text)
        
        for d in filtered_dets:
            # 跳过 rank 数字本身
            text_stripped = d.text.strip()
            if RANK_RE.match(text_stripped) and 1 <= int(text_stripped) <= 20:
                continue
            
            # 检查是否在 y-band 内
            if y_min <= d.cy <= y_max:
                # 检查是否在 rank 右侧
                if d.left > rank_right:
                    # 计算距离（用于排序，优先选择最近的）
                    distance = d.left - rank_right
                    candidates.append((distance, d.text.strip()))
        
        # 按距离排序，取最近的几个文本拼接
        candidates.sort(key=lambda x: x[0])
        
        # 保存候选文本列表（用于去重修复）
        candidate_texts = [text for _, text in candidates[:10]]  # 保存前10个候选
        rank_candidates_dict[rank] = candidate_texts
        
        # 拼接文本（取前 5 个最近的，避免拼接过多）
        name_parts = []
        for _, text in candidates[:5]:
            text_clean = text.strip()
            # 跳过独立的徽标 token
            if text_clean in {"热", "新"}:
                continue
            if text_clean:
                name_parts.append(text_clean)
        
        raw_name = " ".join(name_parts).strip()
        
        # 使用 normalize_name 后处理
        normalized_name = normalize_name(rank, raw_name, chart_key)
        
        # 使用 clean_item_text 统一清洗
        cleaned_name = clean_item_text(normalized_name)
        
        # 过滤无效条目
        if not _is_valid_entry(cleaned_name, chart_key):
            continue
        
        # 构建 item
        if chart_key == "artist":
            item = {"rank": rank, "track_name": "", "artist_name": cleaned_name}
        else:
            item = {"rank": rank, "track_name": cleaned_name, "artist_name": ""}
        
        items.append(item)
    
    # 按 rank 排序
    items.sort(key=lambda x: x["rank"])
    
    return items, rank_candidates_dict


def _load_previous_output() -> Dict[str, Any]:
    """加载上一次的输出"""
    if OUT_JSON.exists():
        try:
            return json.load(open(OUT_JSON, "r", encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _fallback_chart(prev: Dict[str, Any], chart_key: str) -> List[Dict[str, Any]]:
    """回退使用上一次对应榜单的数据"""
    charts = (prev or {}).get("charts") or {}
    lst = charts.get(chart_key) or []
    if isinstance(lst, list):
        # 确保是 list[dict]，只保留 rank/track_name/artist_name
        result = []
        for x in lst:
            if isinstance(x, dict):
                result.append({
                    "rank": x.get("rank"),
                    "track_name": x.get("track_name", ""),
                    "artist_name": x.get("artist_name", ""),
                })
        return result
    return []


def main() -> None:
    """主函数"""
    sid, sk, region = _check_env()
    prev = _load_previous_output()

    # 以本地时区(UTC+8)写入 generated_at/date
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    date_str = now.strftime("%Y-%m-%d")

    charts_out: Dict[str, List[Dict[str, Any]]] = {}
    min_ok = 16  # 最低可用阈值

    # 四张截图
    chart_configs = [
        ("search", "qishui_search.png"),
        ("hot", "qishui_hot.png"),
        ("new", "qishui_new.png"),
        ("artist", "qishui_artist.png"),
    ]

    for chart_key, img_filename in chart_configs:
        img_path = DEFAULT_INBOX_DIR / img_filename

        if not img_path.exists():
            print(f"[WARN] 缺少截图 {chart_key}: {img_path} -> fallback previous")
            charts_out[chart_key] = _fallback_chart(prev, chart_key)
            continue

        print(f"[INFO] 处理 {chart_key}: {img_path}")
        dets = _run_tencent_general_basic_ocr(img_path, sid, sk, region)
        
        # 计算图片宽度（从所有 detections 的 right 取最大值）
        image_width = max((d.right for d in dets), default=0.0)
        
        items, rank_candidates_dict = parse_ranked_list_from_detections(dets, chart_key, image_width)

        # Debug 输出
        parsed_ranks = [it["rank"] for it in items]
        strict_ranks_set = set(parsed_ranks)
        all_ranks_set = set(range(1, 21))
        missing_ranks = sorted(all_ranks_set - strict_ranks_set)
        
        print(f"[INFO] {chart_key} parsed={len(items)} dets={len(dets)} image_width={image_width:.1f}")
        print(f"[DEBUG] {chart_key} strict_ranks 覆盖: {sorted(parsed_ranks)}")
        if missing_ranks:
            print(f"[DEBUG] {chart_key} 缺失 ranks: {missing_ranks}")
        else:
            print(f"[DEBUG] {chart_key} strict_ranks 完整覆盖 1..20 ✓")
        
        # 对于 hot/new/artist，如果 strict_ranks 覆盖不完整，使用行序 fallback
        fallback_lines = None  # 保存用于 debug
        if chart_key in ("hot", "new", "artist") and missing_ranks:
            print(f"[INFO] {chart_key} 使用行序 fallback 补齐缺失 ranks")
            fallback_items, fallback_candidates_dict, fallback_lines = _parse_by_line_order_fallback(dets, chart_key, image_width)
            
            # 合并候选字典
            for rank, candidates in fallback_candidates_dict.items():
                if rank not in rank_candidates_dict:
                    rank_candidates_dict[rank] = candidates
                else:
                    # 合并候选列表，去重
                    combined = rank_candidates_dict[rank] + candidates
                    rank_candidates_dict[rank] = list(dict.fromkeys(combined))  # 保持顺序的去重
            
            # 合并：优先使用 strict_ranks 的结果，缺失的用 fallback 补齐
            items_dict = {it["rank"]: it for it in items}
            for fallback_item in fallback_items:
                fallback_rank = fallback_item["rank"]
                if fallback_rank not in items_dict:
                    items_dict[fallback_rank] = fallback_item
            
            # 重新构建 items 列表，按 rank 排序
            items = [items_dict[r] for r in sorted(items_dict.keys()) if 1 <= r <= 20]
            
            # 更新 parsed_ranks
            parsed_ranks = [it["rank"] for it in items]
            strict_ranks_set = set(parsed_ranks)
            missing_ranks = sorted(all_ranks_set - strict_ranks_set)
            print(f"[INFO] {chart_key} fallback 后 parsed={len(items)}, 缺失 ranks: {missing_ranks}")
            
            # 对于 new 榜，如果最终仍缺 rank，则仅对缺失的 rank 回退 previous 的对应 rank/行
            if chart_key == "new" and missing_ranks:
                prev_chart = _fallback_chart(prev, "new")
                prev_items_dict = {it["rank"]: it for it in prev_chart if isinstance(it, dict) and it.get("rank")}
                
                for missing_rank in missing_ranks:
                    if missing_rank in prev_items_dict:
                        prev_item = prev_items_dict[missing_rank]
                        # 只回退缺失的 rank
                        items_dict[missing_rank] = prev_item
                        print(f"[INFO] new rank={missing_rank} 回退到 previous: '{prev_item.get('track_name') or prev_item.get('artist_name', '')}'")
                
                # 重新构建 items 列表
                items = [items_dict[r] for r in sorted(items_dict.keys()) if 1 <= r <= 20]
                parsed_ranks = [it["rank"] for it in items]
                missing_ranks = sorted(all_ranks_set - set(parsed_ranks))
                print(f"[INFO] new 回退后 parsed={len(items)}, 缺失 ranks: {missing_ranks}")
        
        # 去重修复：在行序 fallback 补齐后执行
        fallback_lines_for_debug = None  # 保存 fallback_lines 用于 debug
        if chart_key in ("hot", "new", "artist"):
            items_before_dedup = [dict(it) for it in items]  # 深拷贝用于对比
            items, duplicates_found = _dedupe_repair_pass(items, rank_candidates_dict, chart_key)
            
            # 对 new 榜打印重复检测结果和修复前后对比
            if chart_key == "new" and duplicates_found:
                print(f"[DEBUG] {chart_key} 重复检测: 发现重复 ranks={duplicates_found}")
                print(f"[DEBUG] {chart_key} 修复前后对比:")
                for rank in duplicates_found:
                    before_item = next((it for it in items_before_dedup if it["rank"] == rank), None)
                    after_item = next((it for it in items if it["rank"] == rank), None)
                    if before_item and after_item:
                        before_name = (before_item.get("track_name") or before_item.get("artist_name") or "").strip()
                        after_name = (after_item.get("track_name") or after_item.get("artist_name") or "").strip()
                        print(f"  rank={rank}: '{before_name}' -> '{after_name}'")
        
        # new 榜 rank=8 debug：在写入 charts 前
        if chart_key == "new":
            rank_8_exists = any(it.get("rank") == 8 for it in items)
            if not rank_8_exists:
                print(f"[DEBUG] new rank=8 missing after fallback")
                
                # 获取 rank=8 对应的物理行 candidates
                rank_8_candidates = rank_candidates_dict.get(8, [])
                if rank_8_candidates:
                    print(f"[DEBUG] new rank=8 物理行 candidates (清洗前): {rank_8_candidates}")
                    
                    # 分析每个候选
                    for idx, candidate_raw in enumerate(rank_8_candidates):
                        cleaned = clean_item_text(candidate_raw)
                        
                        # 判断无效原因
                        reason = None
                        if not cleaned:
                            reason = "empty"
                        elif len(cleaned) < 2:
                            reason = "too_short"
                        elif cleaned.isdigit():
                            num_value = int(cleaned)
                            if len(cleaned) >= 2 and not (1 <= num_value <= 20):
                                # 纯数字但应该通过验证
                                is_valid = _is_valid_entry(cleaned, "new")
                                reason = f"pure_digit_should_pass (is_valid={is_valid})"
                            else:
                                reason = "pure_digit_filtered"
                        else:
                            # 检查 UI 文案
                            has_ui_word = any(noise in cleaned for noise in UI_NOISE_WORDS)
                            if has_ui_word:
                                reason = "ui_word"
                            else:
                                is_valid = _is_valid_entry(cleaned, "new")
                                reason = f"other (is_valid={is_valid})"
                        
                        print(f"  candidate[{idx}]: raw='{candidate_raw}' -> cleaned='{cleaned}' | reason: {reason}")
                        
                        # 如果清洗后是纯数字（len>=2），明确打印
                        if cleaned and cleaned.isdigit() and len(cleaned) >= 2:
                            num_value = int(cleaned)
                            is_valid = _is_valid_entry(cleaned, "new")
                            print(f"    -> 纯数字 '{cleaned}' (int={num_value}), is_valid_entry('{cleaned}', 'new')={is_valid}")
                else:
                    print(f"[DEBUG] new rank=8 无 candidates 数据")
            else:
                rank_8_item = next((it for it in items if it.get("rank") == 8), None)
                if rank_8_item:
                    name = rank_8_item.get("track_name") or rank_8_item.get("artist_name", "")
                    print(f"[DEBUG] new rank=8 exists: name='{name}'")
        
        # 打印前 10 条预览
        print(f"[DEBUG] {chart_key} 前10条预览:")
        for i, it in enumerate(items[:10], 1):
            track_or_artist = it.get("track_name") or it.get("artist_name", "")
            print(f"  {i}. rank={it['rank']}, name='{track_or_artist}'")

        # 最终输出统计
        final_count = len(items)
        print(f"[INFO] {chart_key} final_count={final_count}, missing_ranks={missing_ranks}")

        if final_count < min_ok:
            print(f"[WARN] {chart_key} 解析仅 {final_count} 条(<{min_ok}) -> fallback previous")
            charts_out[chart_key] = _fallback_chart(prev, chart_key)
        else:
            charts_out[chart_key] = items
        
        # search 榜兜底清洗：在写入 charts 前，确保去掉前导符号并纠错
        if chart_key == "search":
            search_items = charts_out[chart_key]
            for it in search_items:
                t = it.get("track_name", "") or ""
                # 去前导符号
                t = re.sub(r'^[^0-9A-Za-z\u4e00-\u9fa5]+', '', t)
                # 特定纠错
                if t == "半一半":
                    t = "一半一半"
                it["track_name"] = t

        print(f"[INFO] {chart_key} 最终输出: {len(charts_out[chart_key])} 条")

    # 构建输出对象
    out_obj = {
        "generated_at": now.isoformat(timespec="seconds"),
        "date": date_str,
        "source": SOURCE_LABEL,
        "charts": charts_out,
    }

    # 写入文件
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out_obj, f, ensure_ascii=False, indent=2)

    print(f"[OK] 已写入: {OUT_JSON}")
    print(f"     热门搜索 {len(charts_out.get('search', []))} 条，热歌榜 {len(charts_out.get('hot', []))} 条，新歌榜 {len(charts_out.get('new', []))} 条，音乐人榜 {len(charts_out.get('artist', []))} 条")


if __name__ == "__main__":
    main()
