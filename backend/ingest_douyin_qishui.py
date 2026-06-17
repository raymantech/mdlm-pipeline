#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
汽水热歌榜抓取脚本

从抖音汽水音乐页面抓取热歌榜数据并入库
数据源：https://www.douyin.com/qishui/playlist/7456953055192696858
"""

import os
import sys
import json
import re
import hashlib
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs

import httpx

# 确保模块在路径中
ROOT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT_DIR.parent  # 项目根目录 MDLM1.0
sys.path.insert(0, str(ROOT_DIR))

from mdlm_config import db_path, load_env
from timezone_utils import beijing_now_iso, beijing_today_iso, parse_date, get_target_date, detected_at_for_day

# 复用 ingest_douyin.py 中的函数
from ingest_douyin import (
    db_connect,
    get_platform_id,
    get_chart_id,
    insert_snapshot,
    insert_entries,
    delete_today_chart_snapshot,
)

# =========================================================
# ENV
# =========================================================
load_env(override=True)

SQLITE_DB_PATH = str(db_path())
PLATFORM_NAME = "抖音(汽水)"
CHART_NAME = "热歌榜"

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

FETCH_TIMEOUT = float(os.getenv("DOUYIN_FETCH_TIMEOUT", "15"))
FETCH_RETRY = int(os.getenv("DOUYIN_FETCH_RETRY", "2"))
FETCH_SLEEP = float(os.getenv("DOUYIN_FETCH_SLEEP", "1.0"))

DEFAULT_URL = "https://www.douyin.com/qishui/playlist/7456953055192696858"


# =========================================================
# HTML 解析器 - 抗改版实现
# =========================================================
class QishuiPlaylistParser(HTMLParser):
    """解析汽水音乐播放列表页面，使用"相关歌曲"文本作为锚点"""
    
    def __init__(self):
        super().__init__()
        self.tracks: List[Dict[str, Any]] = []
        self.in_track_section = False
        self.found_anchor = False  # 是否找到"相关歌曲"锚点
        self.current_track: Optional[Dict[str, Any]] = {}
        self.current_text = ""
        self.track_counter = 0
        
        # 锚点关键词（抗改版：支持多种可能的文本）
        self.anchor_keywords = ["相关歌曲", "推荐歌曲", "歌曲列表", "播放列表"]
        
    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        
        # 查找锚点：包含"相关歌曲"等关键词的元素
        if tag in ["div", "h2", "h3", "span", "p"]:
            class_name = attrs_dict.get("class", "")
            text_content = attrs_dict.get("text", "")
            
            # 检查是否包含锚点关键词
            if any(keyword in str(class_name) or keyword in str(text_content) for keyword in self.anchor_keywords):
                self.found_anchor = True
                self.in_track_section = True
                return
        
        # 在找到锚点后，查找歌曲列表项
        if self.found_anchor and tag in ["div", "li", "tr", "article"]:
            # 检查是否是歌曲项（通过常见的类名或属性）
            class_name = attrs_dict.get("class", "")
            data_id = attrs_dict.get("data-id", "")
            
            # 常见的歌曲项标识
            if any(keyword in str(class_name).lower() for keyword in ["song", "track", "item", "music", "playlist-item"]):
                self.current_track = {}
                self.track_counter += 1
    
    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        
        # 记录文本内容，用于后续提取
        self.current_text += text + " "
        
        # 如果找到锚点，开始收集歌曲信息
        if self.found_anchor and self.current_track:
            # 尝试提取歌曲名（通常是最明显的文本）
            if len(text) > 1 and len(text) < 50:  # 合理的歌曲名长度
                if "track_name" not in self.current_track:
                    # 过滤掉常见的非歌曲名文本
                    if not any(skip in text for skip in ["播放", "收藏", "分享", "下载", "更多", "相关", "推荐"]):
                        self.current_track["track_name"] = text
    
    def handle_endtag(self, tag):
        if tag in ["div", "li", "tr", "article"] and self.current_track:
            # 完成一个歌曲项的解析
            if "track_name" in self.current_track:
                self.tracks.append(self.current_track.copy())
            self.current_track = {}


def extract_playlist_id(url: str) -> str:
    """从 URL 中提取 playlist_id"""
    # https://www.douyin.com/qishui/playlist/7456953055192696858
    match = re.search(r'/playlist/(\d+)', url)
    if match:
        return match.group(1)
    return "unknown"


def generate_stable_track_id(playlist_id: str, track_name: str, artist: str = "") -> str:
    """
    生成稳定的 track_platform_id
    
    使用 playlist_id + track_name + artist 组合生成
    对文本进行简单清洗和截断
    """
    # 清洗：去除空格、转小写、去除特殊字符（保留中文、英文、数字）
    def clean_text(s: str) -> str:
        s = s.strip().lower()
        # 保留中文、英文、数字
        s = re.sub(r'[^\u4e00-\u9fa5a-z0-9]', '', s)
        # 截断到合理长度（避免过长）
        return s[:50]
    
    clean_name = clean_text(track_name)
    clean_artist = clean_text(artist) if artist else ""
    
    # 组合生成 ID
    parts = [playlist_id, clean_name]
    if clean_artist:
        parts.append(clean_artist[:20])  # 艺人名截断到20字符
    
    combined = "||".join(parts)
    
    # 使用 hash 生成固定长度的 ID（更稳定）
    hash_obj = hashlib.md5(combined.encode('utf-8'))
    return f"qishui:{hash_obj.hexdigest()[:16]}"


def parse_html_content(html: str, playlist_id: str) -> List[Dict[str, Any]]:
    """
    解析 HTML 内容，提取歌曲列表
    
    使用"相关歌曲"文本作为锚点定位列表
    """
    parser = QishuiPlaylistParser()
    parser.feed(html)
    
    tracks = parser.tracks
    
    # 如果解析器没有找到足够的歌曲，尝试备用方法：从 JSON 数据中提取
    if len(tracks) < 5:
        # 尝试从页面中的 JSON 数据提取
        tracks = extract_from_json_data(html, playlist_id)
    
    return tracks


def extract_from_json_data(html: str, playlist_id: str) -> List[Dict[str, Any]]:
    """
    备用方法：从页面中的 JSON 数据提取歌曲列表
    
    抖音页面通常会在 script 标签中包含 JSON 数据
    使用"相关歌曲"等关键词作为锚点定位数据
    """
    tracks: List[Dict[str, Any]] = []
    
    # 方法1: 查找 script 标签中的 JSON 数据
    script_pattern = r'<script[^>]*>(.*?)</script>'
    script_matches = re.finditer(script_pattern, html, re.DOTALL | re.IGNORECASE)
    
    for script_match in script_matches:
        script_content = script_match.group(1)
        
        # 查找包含歌曲数据的 JSON 对象
        # 常见的模式：window._SSR_HYDRATED_DATA 或 window.__INITIAL_STATE__
        json_patterns = [
            r'window\._SSR_HYDRATED_DATA\s*=\s*({.+?});',
            r'window\.__INITIAL_STATE__\s*=\s*({.+?});',
            r'window\.__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*({.+?});',
            r'"music_list"\s*:\s*\[(.+?)\]',
            r'"tracks"\s*:\s*\[(.+?)\]',
            r'"song_list"\s*:\s*\[(.+?)\]',
        ]
        
        for pattern in json_patterns:
            matches = re.finditer(pattern, script_content, re.DOTALL)
            for match in matches:
                try:
                    json_str = match.group(1)
                    # 尝试解析 JSON
                    data = json.loads(json_str)
                    
                    # 递归查找歌曲列表
                    music_list = find_music_list(data)
                    if music_list and len(music_list) > 0:
                        parsed = parse_music_list_to_tracks(music_list, playlist_id)
                        if parsed:
                            return parsed
                except (json.JSONDecodeError, KeyError, ValueError, AttributeError) as e:
                    # 静默失败，继续尝试下一个模式
                    continue
    
    # 方法2: 如果方法1失败，尝试直接搜索包含"相关歌曲"附近的 JSON 数据
    # 查找包含"相关歌曲"、"推荐歌曲"等关键词的区域
    anchor_keywords = ["相关歌曲", "推荐歌曲", "歌曲列表", "播放列表", "热歌榜"]
    for keyword in anchor_keywords:
        keyword_pos = html.find(keyword)
        if keyword_pos > 0:
            # 在关键词附近查找 JSON 数据
            search_start = max(0, keyword_pos - 5000)
            search_end = min(len(html), keyword_pos + 50000)
            search_area = html[search_start:search_end]
            
            # 尝试提取 JSON 数组
            json_array_pattern = r'\[({.+?})\]'
            matches = re.finditer(json_array_pattern, search_area, re.DOTALL)
            for match in matches:
                try:
                    json_str = "[" + match.group(0) + "]"
                    data_list = json.loads(json_str)
                    if isinstance(data_list, list) and len(data_list) > 0:
                        # 检查第一个元素是否包含歌曲字段
                        first_item = data_list[0]
                        if isinstance(first_item, dict) and any(
                            key in first_item for key in ["title", "name", "music_name", "song_name"]
                        ):
                            # 这可能是歌曲列表
                            music_list = find_music_list({"tracks": data_list})
                            if music_list:
                                # 使用相同的解析逻辑
                                for idx, item in enumerate(music_list[:100]):
                                   parsed = parse_music_list_to_tracks(data_list, playlist_id)
                                if parsed:
                                    # 更新 source
                                    for track in parsed:
                                        track["extra_metrics"]["source"] = "qishui_html_anchor"
                                    return parsed
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
    
    return tracks


def find_music_list(obj: Any, depth: int = 0) -> Optional[List]:
    """递归查找包含歌曲列表的数组（最大深度5）"""
    if depth > 5:
        return None
    
    if isinstance(obj, list):
        # 检查是否是歌曲列表
        if len(obj) > 0 and isinstance(obj[0], dict):
            # 检查第一个元素是否包含歌曲相关字段
            first = obj[0]
            if any(key in first for key in ["title", "name", "music_name", "song_name", "author", "artist"]):
                return obj
    
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and any(kw in key.lower() for kw in ["music", "track", "song", "playlist"]):
                result = find_music_list(value, depth + 1)
                if result:
                    return result
            result = find_music_list(value, depth + 1)
            if result:
                return result
    
    return None


def extract_from_json_data_aggressive(html: str, playlist_id: str) -> List[Dict[str, Any]]:
    """
    激进的 JSON 提取方法：查找所有 script 标签中的 JSON
    """
    tracks: List[Dict[str, Any]] = []
    
    # 提取所有 script 标签内容
    script_pattern = r'<script[^>]*>(.*?)</script>'
    scripts = re.findall(script_pattern, html, re.DOTALL | re.IGNORECASE)
    
    for script_content in scripts:
        # 尝试查找各种可能的 JSON 模式
        patterns = [
            # 全局变量赋值
            r'window\.\w+\s*=\s*({.+?});',
            r'var\s+\w+\s*=\s*({.+?});',
            r'let\s+\w+\s*=\s*({.+?});',
            r'const\s+\w+\s*=\s*({.+?});',
            # JSON 对象
            r'({[^{}]*"music"[^{}]*})',
            r'({[^{}]*"song"[^{}]*})',
            r'({[^{}]*"track"[^{}]*})',
            r'({[^{}]*"playlist"[^{}]*})',
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, script_content, re.DOTALL)
            for match in matches:
                try:
                    json_str = match.group(1)
                    # 尝试解析 JSON
                    data = json.loads(json_str)
                    
                    # 递归查找歌曲列表
                    music_list = find_music_list(data)
                    if music_list and len(music_list) > 0:
                        return parse_music_list_to_tracks(music_list, playlist_id)
                except (json.JSONDecodeError, ValueError):
                    continue
    
    return tracks


def extract_all_json_objects(html: str, playlist_id: str) -> List[Dict[str, Any]]:
    """
    提取所有可能的 JSON 对象，尝试解析
    """
    tracks: List[Dict[str, Any]] = []
    
    # 查找所有类似 JSON 的结构
    # 匹配大括号包围的内容
    json_like_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    matches = re.finditer(json_like_pattern, html, re.DOTALL)
    
    for match in matches:
        json_str = match.group(0)
        # 只处理足够大的 JSON（可能是数据对象）
        if len(json_str) < 100:
            continue
        
        try:
            data = json.loads(json_str)
            music_list = find_music_list(data)
            if music_list and len(music_list) > 0:
                parsed = parse_music_list_to_tracks(music_list, playlist_id)
                if parsed:
                    return parsed
        except (json.JSONDecodeError, ValueError):
            continue
    
    return tracks


def parse_music_list_to_tracks(music_list: List[Dict[str, Any]], playlist_id: str) -> List[Dict[str, Any]]:
    """将音乐列表解析为 tracks 格式"""
    tracks: List[Dict[str, Any]] = []
    
    for idx, item in enumerate(music_list[:100]):  # 限制最多100首
        if not isinstance(item, dict):
            continue
        
        track_name = (
            item.get("title") or 
            item.get("name") or 
            item.get("music_name") or 
            item.get("song_name") or
            item.get("track_name") or
            ""
        ).strip()
        
        if not track_name:
            continue
        
        artist = (
            item.get("author") or 
            item.get("artist") or 
            item.get("singer") or
            item.get("author_name") or
            item.get("artist_name") or
            ""
        ).strip()
        
        # 处理艺人列表
        if isinstance(artist, list):
            artist_names = []
            for a in artist:
                if isinstance(a, dict):
                    name = a.get("name") or a.get("author_name") or ""
                    if name:
                        artist_names.append(str(name).strip())
                else:
                    artist_names.append(str(a).strip())
            artist = " / ".join(filter(None, artist_names))
        
        # 提取可选字段
        album = (
            item.get("album") or 
            item.get("album_name") or
            ""
        ).strip()
        
        duration = item.get("duration") or item.get("time") or 0
        
        track_id = generate_stable_track_id(playlist_id, track_name, artist)
        
        extra_metrics = {
            "playlist_id": playlist_id,
            "source": "qishui_json",
        }
        if album:
            extra_metrics["album"] = album
        if duration:
            extra_metrics["duration"] = duration
        
        tracks.append({
            "rank": len(tracks) + 1,
            "track_platform_id": track_id,
            "track_name": track_name,
            "artist_name_raw": artist,
            "heat": 0,
            "extra_metrics": extra_metrics,
        })
    
    return tracks


def fetch_qishui_playlist(url: str, use_playwright: bool = False) -> Dict[str, Any]:
    """
    抓取汽水音乐播放列表页面
    
    参数：
    - url: 播放列表 URL
    - use_playwright: 是否使用 Playwright（仅用于读取可见 DOM）
    """
    playlist_id = extract_playlist_id(url)
    
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.douyin.com/",
        "Origin": "https://www.douyin.com",
    }
    
    # 如果有 Cookie，添加到请求头（可选）
    douyin_cookie = os.getenv("DOUYIN_COOKIE", "").strip()
    if douyin_cookie:
        headers["Cookie"] = douyin_cookie
    
    last_err = None
    
    # 如果使用 Playwright
    if use_playwright:
        try:
            from playwright.sync_api import sync_playwright
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=int(FETCH_TIMEOUT * 1000))
                
                # 等待页面加载
                page.wait_for_timeout(2000)
                
                # 获取页面 HTML
                html = page.content()
                browser.close()
                
                return {
                    "source": url,
                    "html": html,
                    "playlist_id": playlist_id,
                    "fetched_at": beijing_now_iso(),
                }
        except ImportError:
            print("  [WARN] Playwright not installed, falling back to httpx")
        except Exception as e:
            last_err = f"Playwright error: {e}"
    
    # 使用 httpx 作为主要方法
    for attempt in range(FETCH_RETRY):
        try:
            with httpx.Client(timeout=FETCH_TIMEOUT, headers=headers, follow_redirects=True) as client:
                r = client.get(url)
                
                if r.status_code == 200:
                    return {
                        "source": url,
                        "html": r.text,
                        "playlist_id": playlist_id,
                        "fetched_at": beijing_now_iso(),
                    }
                
                last_err = f"HTTP {r.status_code}"
                
        except httpx.TimeoutException:
            last_err = "Timeout"
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)}"
        
        if attempt < FETCH_RETRY - 1:
            import time
            time.sleep(FETCH_SLEEP)
    
    raise RuntimeError(f"抓取失败 ({url}): {last_err}")


def parse_qishui_tracks(payload: Dict[str, Any], limit: int = 100, debug: bool = False) -> List[Dict[str, Any]]:
    """解析汽水音乐返回的歌曲数据"""
    html = payload.get("html", "")
    playlist_id = payload.get("playlist_id", "unknown")
    
    if not html:
        raise RuntimeError("HTML 内容为空")
    
    if debug:
        # 保存 HTML 片段用于调试
        debug_dir = Path("debug_html")
        debug_dir.mkdir(exist_ok=True)
        debug_file = debug_dir / f"qishui_{playlist_id}.html"
        with open(debug_file, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"  [DEBUG] HTML 已保存到: {debug_file}")
        
        # 检查 HTML 长度和关键内容
        print(f"  [DEBUG] HTML 长度: {len(html)} 字符")
        if "music" in html.lower() or "song" in html.lower() or "track" in html.lower():
            print(f"  [DEBUG] HTML 中包含 music/song/track 关键词")
        if "playlist" in html.lower():
            print(f"  [DEBUG] HTML 中包含 playlist 关键词")
    
    # 解析 HTML
    tracks = parse_html_content(html, playlist_id)
    
    if not tracks:
        # 如果解析失败，尝试更激进的 JSON 提取
        if debug:
            print("  [DEBUG] 常规解析失败，尝试激进 JSON 提取...")
        tracks = extract_from_json_data_aggressive(html, playlist_id)
    
    if not tracks:
        # 最后尝试：查找所有可能的 JSON 对象
        if debug:
            print("  [DEBUG] 激进提取失败，尝试查找所有 JSON 对象...")
        tracks = extract_all_json_objects(html, playlist_id)
    
    if not tracks:
        error_msg = "未能从页面提取到歌曲数据"
        error_msg += "\n  可能原因："
        error_msg += "\n    1. 页面是动态渲染的，需要使用 --playwright 选项"
        error_msg += "\n    2. 页面结构已改变，需要更新解析逻辑"
        error_msg += "\n    3. 需要登录或 Cookie 认证"
        if debug:
            error_msg += f"\n  提示：HTML 已保存到 debug_html/qishui_{playlist_id}.html，请检查页面结构"
        error_msg += "\n  建议：尝试使用 --playwright 选项：python backend/ingest_douyin_qishui.py --playwright"
        raise RuntimeError(error_msg)
    
    # 限制数量
    tracks = tracks[:limit]
    
    # 确保每个 track 都有必要的字段
    for idx, track in enumerate(tracks):
        if "rank" not in track:
            track["rank"] = idx + 1
        
        if "track_platform_id" not in track:
            track_name = track.get("track_name", "")
            artist = track.get("artist_name_raw", "")
            track["track_platform_id"] = generate_stable_track_id(playlist_id, track_name, artist)
        
        # 确保有 artist_name_raw（允许为空）
        if "artist_name_raw" not in track:
            track["artist_name_raw"] = ""
        
        # 确保有 heat（允许为 0）
        if "heat" not in track:
            track["heat"] = 0
        
        # 确保有 extra_metrics
        if "extra_metrics" not in track:
            track["extra_metrics"] = {}
        
        track["extra_metrics"]["playlist_id"] = playlist_id
        track["extra_metrics"]["source"] = "qishui_playlist"
    
    return tracks


def ingest_qishui(
    url: str = DEFAULT_URL,
    top_n: int = 100,
    output_json: Optional[Path] = None,
    no_import: bool = False,
    use_playwright: bool = False,
    target_date: Optional[str] = None,
    archive: bool = False,
) -> None:
    """
    抓取汽水热歌榜数据并入库
    
    参数：
    - url: 播放列表 URL
    - top_n: 抓取前 N 首
    - output_json: 输出 JSON 文件路径（可选）
    - no_import: 是否跳过数据库导入
    - use_playwright: 是否使用 Playwright
    - target_date: 目标日期 (YYYY-MM-DD)，用于幂等入库
    - archive: 是否归档到日期文件
    """
    print(f"[qishui] 开始抓取汽水热歌榜: {url}")
    print(f"  目标数量: Top {top_n}")
    
    # 1. 抓取页面
    try:
        payload = fetch_qishui_playlist(url, use_playwright=use_playwright)
        print(f"  ✅ 页面抓取成功 (playlist_id={payload['playlist_id']})")
    except Exception as e:
        print(f"  ❌ 页面抓取失败: {e}")
        raise
    
    # 2. 解析歌曲列表
    try:
        debug_mode = os.getenv("QISHUI_DEBUG", "false").lower() in ("true", "1", "yes")
        tracks = parse_qishui_tracks(payload, limit=top_n, debug=debug_mode)
        print(f"  ✅ 解析成功，提取到 {len(tracks)} 首歌曲")
    except Exception as e:
        print(f"  ❌ 解析失败: {e}")
        print(f"  💡 提示：设置环境变量 QISHUI_DEBUG=true 可启用调试模式")
        raise
    
    # 3. 构建输出 JSON（格式 A：包含 source/fetched_at/tracks）
    result_json = {
        "source": payload["source"],
        "fetched_at": payload["fetched_at"],
        "playlist_id": payload["playlist_id"],
        "tracks": tracks,
    }
    
    # 4. 输出 JSON 文件
    # 4.1 默认输出到 data/douyin_qishui_latest.json（相对项目根目录）
    if output_json is None:
        output_json = PROJECT_ROOT / "data" / "douyin_qishui_latest.json"
    
    output_json.parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(result_json, f, ensure_ascii=False, indent=2)
    print(f"  ✅ JSON 已保存: {output_json}")
    
    # 4.2 归档文件（如果启用）
    if archive:
        if target_date:
            archive_date = target_date
        else:
            archive_date = beijing_today_iso()
        archive_path = PROJECT_ROOT / "data" / f"douyin_qishui_{archive_date}.json"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with open(archive_path, 'w', encoding='utf-8') as f:
            json.dump(result_json, f, ensure_ascii=False, indent=2)
        print(f"  ✅ 归档文件已保存: {archive_path}")
    
    # 5. 写入数据库（如果未跳过）
    if not no_import:
        conn = db_connect()
        try:
            platform_id = get_platform_id(conn, PLATFORM_NAME)
            chart_id = get_chart_id(conn, platform_id, CHART_NAME)
            
            # 确定目标日期（用于幂等入库）
            if target_date:
                target_day = target_date
            else:
                target_day = beijing_today_iso()
            
            # 删除目标日期已有的数据（幂等性保证）
            delete_today_chart_snapshot(conn, chart_id, target_day)
            
            # 插入快照（使用目标日期的日期部分，时间部分使用当前时间）
            captured_at = detected_at_for_day(target_day)
            
            snapshot_id = insert_snapshot(
                conn,
                chart_id,
                captured_at,
                top_n=len(tracks),
                raw_obj={
                    "playlist_id": payload["playlist_id"],
                    "source": payload["source"],
                    "fetched_at": payload["fetched_at"],
                },
            )
            
            # 插入条目
            insert_entries(conn, snapshot_id, tracks)
            
            print(f"  ✅ 数据库写入成功 (snapshot_id={snapshot_id}, date={target_day})")
            print(f"  ✅ 入库完成：{PLATFORM_NAME} - {CHART_NAME} Top{len(tracks)}")
            
        except Exception as e:
            print(f"  ❌ 数据库写入失败: {e}")
            raise
        finally:
            conn.close()
    else:
        print("  [SKIP] 跳过数据库导入 (--no-import)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="汽水热歌榜抓取脚本")
    parser.add_argument("--url", type=str, default=DEFAULT_URL, help="播放列表 URL")
    parser.add_argument("--top", type=int, default=100, help="抓取前 N 首（默认: 100）")
    parser.add_argument("--out", type=str, help="输出 JSON 文件路径（可选，默认: data/douyin_qishui_latest.json）")
    parser.add_argument("--no-import", action="store_true", help="跳过数据库导入")
    parser.add_argument("--playwright", action="store_true", help="使用 Playwright 抓取（可选）")
    parser.add_argument("--debug", action="store_true", help="启用调试模式（保存 HTML 文件）")
    parser.add_argument("--date", type=str, help="目标日期 (YYYY-MM-DD)，用于幂等入库（默认: 今天）")
    parser.add_argument("--archive", action="store_true", help="归档到日期文件 data/douyin_qishui_YYYY-MM-DD.json")
    
    args = parser.parse_args()
    
    # 设置调试模式
    if args.debug:
        os.environ["QISHUI_DEBUG"] = "true"
    
    # 解析输出路径
    if args.out:
        output_path = Path(args.out)
        # 如果是相对路径，相对于项目根目录
        if not output_path.is_absolute():
            output_path = PROJECT_ROOT / output_path
    else:
        output_path = None  # 使用默认路径
    
    # 解析目标日期
    target_date = None
    if args.date:
        try:
            # 验证日期格式
            parsed_date = parse_date(args.date)
            target_date = parsed_date.isoformat()
        except ValueError:
            print(f"  ❌ 日期格式错误: {args.date}，应为 YYYY-MM-DD")
            sys.exit(1)
    else:
        # 检查环境变量 TARGET_DATE
        target_date = get_target_date("TARGET_DATE", None)
    
    ingest_qishui(
        url=args.url,
        top_n=args.top,
        output_json=output_path,
        no_import=args.no_import,
        use_playwright=args.playwright,
        target_date=target_date,
        archive=args.archive,
    )
