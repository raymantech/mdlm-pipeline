#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台信息差算法 - 黑马歌曲识别

核心逻辑：
1. 获取抖音热门曲目（List_A）
2. 获取传统平台榜单数据（List_B）
3. 通过模糊匹配和排名对比，识别"信息差"黑马

黑马定义：
- 在抖音上排名靠前（Top 20）
- 但在传统平台（网易云/QQ/酷狗）不存在或排名靠后（100+）
"""

import os
import sys
import re
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

import httpx

# 确保模块在路径中
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from timezone_utils import beijing_timestamp, beijing_today_iso

# =========================================================
# 配置
# =========================================================
FETCH_TIMEOUT = 15
FETCH_RETRY = 2

# 黑马识别阈值
DOUYIN_TOP_N = 20          # 抖音前 N 名视为热门
MAINSTREAM_THRESHOLD = 100  # 传统平台排名超过此值视为"未火"
DEFAULT_RANK_PENALTY = 200  # 未上榜时的默认排名（用于计算分数）

# 网易云"抖音排行榜" ID
NETEASE_DOUYIN_CHART_ID = 2250011882  # 抖音排行榜


@dataclass
class TrackInfo:
    """歌曲信息"""
    track_name: str
    artist: str
    rank: int
    platform: str
    heat: float = 0
    track_id: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DarkHorseTrack:
    """黑马歌曲"""
    track_name: str
    artist: str
    douyin_rank: int
    douyin_heat: float
    mainstream_rank: Optional[int]  # None 表示未上榜
    mainstream_platform: str
    gap_score: float
    is_dark_horse: bool
    discovery_time: str
    match_key: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =========================================================
# 文本处理与模糊匹配
# =========================================================
def normalize_track_name(name: str) -> str:
    """
    标准化歌曲名称，用于模糊匹配
    
    处理：
    - 去除空格
    - 转小写
    - 去除常见后缀：(Live), (正式版), (完整版), (DJ版) 等
    - 去除特殊字符
    """
    if not name:
        return ""
    
    s = name.strip().lower()
    
    # 移除括号内的内容
    s = re.sub(r'\([^)]*\)', '', s)
    s = re.sub(r'\[[^\]]*\]', '', s)
    s = re.sub(r'（[^）]*）', '', s)
    s = re.sub(r'【[^】]*】', '', s)
    
    # 移除常见后缀
    suffixes = [
        'live', '现场版', '正式版', '完整版', 'dj版', 'remix',
        'cover', '翻唱', '伴奏', 'instrumental', 'acoustic',
        '男版', '女版', '合唱版', 'feat.', 'ft.'
    ]
    for suffix in suffixes:
        s = s.replace(suffix, '')
    
    # 移除特殊字符，只保留中文、英文、数字
    s = re.sub(r'[^\u4e00-\u9fa5a-z0-9]', '', s)
    
    return s.strip()


def normalize_artist_name(name: str) -> str:
    """标准化艺人名称"""
    if not name:
        return ""
    
    s = name.strip().lower()
    
    # 处理多艺人分隔符
    s = re.sub(r'[/、&,，]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    
    # 移除特殊字符
    s = re.sub(r'[^\u4e00-\u9fa5a-z0-9\s]', '', s)
    
    return s.strip()


def generate_match_key(track_name: str, artist: str = "") -> str:
    """生成用于匹配的唯一键"""
    norm_track = normalize_track_name(track_name)
    norm_artist = normalize_artist_name(artist)
    
    # 主要用歌名匹配，艺人作为辅助
    key_str = f"{norm_track}"
    if norm_artist:
        # 取艺人名的前几个字符作为辅助
        key_str += f"_{norm_artist[:10]}"
    
    return key_str


def fuzzy_match(track_a: TrackInfo, track_b: TrackInfo) -> bool:
    """模糊匹配两首歌曲是否为同一首"""
    key_a = generate_match_key(track_a.track_name, track_a.artist)
    key_b = generate_match_key(track_b.track_name, track_b.artist)
    
    # 完全匹配
    if key_a == key_b:
        return True
    
    # 歌名匹配（不考虑艺人）
    norm_a = normalize_track_name(track_a.track_name)
    norm_b = normalize_track_name(track_b.track_name)
    
    if norm_a and norm_b and norm_a == norm_b:
        return True
    
    # 包含关系匹配（处理缩略名）
    if norm_a and norm_b:
        if len(norm_a) > 3 and len(norm_b) > 3:
            if norm_a in norm_b or norm_b in norm_a:
                return True
    
    return False


# =========================================================
# 数据获取
# =========================================================
def fetch_netease_douyin_chart(limit: int = 50) -> List[TrackInfo]:
    """
    从网易云获取"抖音排行榜"数据
    """
    url = "https://music.163.com/api/playlist/detail"
    params = {
        "id": str(NETEASE_DOUYIN_CHART_ID),
        "n": str(limit),
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://music.163.com/",
    }
    
    for attempt in range(FETCH_RETRY):
        try:
            with httpx.Client(timeout=FETCH_TIMEOUT, headers=headers, follow_redirects=True) as client:
                r = client.get(url, params=params)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("code") == 200:
                        return parse_netease_tracks(data, "抖音排行榜")
        except Exception as e:
            print(f"  [WARN] Netease fetch attempt {attempt + 1} failed: {e}")
    
    return []


def parse_netease_tracks(data: Dict[str, Any], chart_name: str) -> List[TrackInfo]:
    """解析网易云返回的歌曲数据"""
    result = data.get("result") or data.get("playlist") or {}
    tracks = result.get("tracks") or []
    
    out: List[TrackInfo] = []
    for idx, track in enumerate(tracks):
        if not isinstance(track, dict):
            continue
        
        song_id = track.get("id")
        song_name = (track.get("name") or "").strip()
        
        if not song_name:
            continue
        
        # 艺人
        artists = track.get("artists") or track.get("ar") or []
        artist_names = []
        for ar in artists:
            if isinstance(ar, dict):
                name = (ar.get("name") or "").strip()
                if name:
                    artist_names.append(name)
        artist = " / ".join(artist_names)
        
        # 热度
        popularity = track.get("popularity") or track.get("pop") or 0
        
        out.append(TrackInfo(
            track_name=song_name,
            artist=artist,
            rank=idx + 1,
            platform=chart_name,
            heat=float(popularity),
            track_id=f"netease:{song_id}" if song_id else "",
        ))
    
    return out


def fetch_mainstream_from_json(json_path: Path) -> List[TrackInfo]:
    """从本地 JSON 文件读取传统平台榜单数据"""
    if not json_path.exists():
        return []
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        events = data.get("events", [])
        out: List[TrackInfo] = []
        
        for ev in events:
            track_name = ev.get("track") or ev.get("track_name") or ""
            artist = ev.get("artist") or ev.get("artist_name") or ""
            platform = ev.get("platform") or ""
            rank = ev.get("rank_now") or ev.get("best_rank_now")
            
            if not track_name or not platform:
                continue
            
            # 只取主流平台
            if platform not in ["QQ音乐", "网易云音乐", "酷狗音乐"]:
                continue
            
            out.append(TrackInfo(
                track_name=track_name,
                artist=artist,
                rank=int(rank) if rank else 999,
                platform=platform,
                heat=0,
            ))
        
        return out
    except Exception as e:
        print(f"  [WARN] Failed to load JSON: {e}")
        return []


def fetch_mainstream_from_url(url: str) -> List[TrackInfo]:
    """从线上 URL 获取传统平台榜单数据"""
    try:
        with httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            r = client.get(url)
            if r.status_code == 200:
                data = r.json()
                events = data.get("events", [])
                out: List[TrackInfo] = []
                
                for ev in events:
                    track_name = ev.get("track") or ev.get("track_name") or ""
                    artist = ev.get("artist") or ev.get("artist_name") or ""
                    platform = ev.get("platform") or ""
                    rank = ev.get("rank_now") or ev.get("best_rank_now")
                    
                    if not track_name or not platform:
                        continue
                    
                    if platform not in ["QQ音乐", "网易云音乐", "酷狗音乐"]:
                        continue
                    
                    out.append(TrackInfo(
                        track_name=track_name,
                        artist=artist,
                        rank=int(rank) if rank else 999,
                        platform=platform,
                        heat=0,
                    ))
                
                return out
    except Exception as e:
        print(f"  [WARN] Failed to fetch from URL: {e}")
    
    return []


# =========================================================
# Mock 数据（用于测试或 API 不可用时）
# =========================================================
MOCK_DOUYIN_TRACKS = [
    TrackInfo("APT.", "ROSÉ / Bruno Mars", 1, "抖音排行榜", 100),
    TrackInfo("再见白兔", "玖壹壹", 2, "抖音排行榜", 98),
    TrackInfo("一笑江湖", "闻人听書_", 3, "抖音排行榜", 95),
    TrackInfo("黑桃A", "塞壬唱片", 4, "抖音排行榜", 93),
    TrackInfo("爱你胜过爱自己", "王奕", 5, "抖音排行榜", 91),
    TrackInfo("千千万万", "深海鱼子酱", 6, "抖音排行榜", 89),
    TrackInfo("苏幕遮", "奇然/沈谧仁", 7, "抖音排行榜", 87),
    TrackInfo("孤独患者", "陈奕迅", 8, "抖音排行榜", 85),
    TrackInfo("我记得", "赵雷", 9, "抖音排行榜", 83),
    TrackInfo("给你的歌", "别安", 10, "抖音排行榜", 81),
    TrackInfo("测试黑马曲目A", "未知歌手", 11, "抖音排行榜", 79),
    TrackInfo("测试黑马曲目B", "神秘艺人", 12, "抖音排行榜", 77),
    TrackInfo("潜力新歌", "新人歌手", 13, "抖音排行榜", 75),
    TrackInfo("信息差测试", "Demo Singer", 14, "抖音排行榜", 73),
    TrackInfo("黑马候选", "Anonymous", 15, "抖音排行榜", 71),
]

MOCK_MAINSTREAM_TRACKS = [
    TrackInfo("APT.", "ROSÉ / Bruno Mars", 3, "QQ音乐", 95),
    TrackInfo("一笑江湖", "闻人听書_", 15, "网易云音乐", 85),
    TrackInfo("孤独患者", "陈奕迅", 8, "QQ音乐", 90),
    TrackInfo("我记得", "赵雷", 25, "酷狗音乐", 80),
    TrackInfo("爱你", "王心凌", 12, "QQ音乐", 88),
    TrackInfo("晴天", "周杰伦", 5, "网易云音乐", 92),
    TrackInfo("稻香", "周杰伦", 18, "酷狗音乐", 87),
]


def get_mock_douyin_tracks() -> List[TrackInfo]:
    """获取模拟的抖音热歌数据"""
    print("  [INFO] Using mock Douyin data")
    return MOCK_DOUYIN_TRACKS.copy()


def get_mock_mainstream_tracks() -> List[TrackInfo]:
    """获取模拟的传统平台数据"""
    print("  [INFO] Using mock mainstream data")
    return MOCK_MAINSTREAM_TRACKS.copy()


# =========================================================
# 黑马识别算法
# =========================================================
def calculate_gap_score(douyin_rank: int, mainstream_rank: Optional[int]) -> float:
    """
    计算信息差分数
    
    公式：gap_score = (51 - douyin_rank) / (mainstream_rank or DEFAULT_RANK_PENALTY)
    
    分数越高，黑马属性越强：
    - 抖音排名越高（数字小），分子越大
    - 传统平台排名越低或未上榜，分母越大，但未上榜时使用惩罚值
    """
    if douyin_rank <= 0:
        return 0
    
    numerator = max(51 - douyin_rank, 1)
    denominator = mainstream_rank if mainstream_rank and mainstream_rank > 0 else DEFAULT_RANK_PENALTY
    
    score = numerator / denominator
    return round(score, 4)


def find_in_mainstream(
    track: TrackInfo, 
    mainstream_tracks: List[TrackInfo]
) -> Tuple[Optional[int], str]:
    """
    在传统平台榜单中查找歌曲
    
    返回：(排名, 平台名) 或 (None, "未上榜")
    """
    best_rank = None
    best_platform = "未上榜"
    
    for m_track in mainstream_tracks:
        if fuzzy_match(track, m_track):
            if best_rank is None or m_track.rank < best_rank:
                best_rank = m_track.rank
                best_platform = m_track.platform
    
    return best_rank, best_platform


def identify_dark_horses(
    douyin_tracks: List[TrackInfo],
    mainstream_tracks: List[TrackInfo],
    douyin_top_n: int = DOUYIN_TOP_N,
    mainstream_threshold: int = MAINSTREAM_THRESHOLD,
) -> List[DarkHorseTrack]:
    """
    识别黑马歌曲
    
    条件：
    1. 抖音排名在 top_n 以内
    2. 传统平台未上榜 或 排名超过 threshold
    """
    dark_horses: List[DarkHorseTrack] = []
    discovery_time = beijing_timestamp()
    
    for track in douyin_tracks:
        if track.rank > douyin_top_n:
            continue
        
        # 在传统平台查找
        mainstream_rank, mainstream_platform = find_in_mainstream(track, mainstream_tracks)
        
        # 判断是否为黑马
        is_dark_horse = False
        if mainstream_rank is None:
            # 未上榜 = 绝对黑马
            is_dark_horse = True
        elif mainstream_rank > mainstream_threshold:
            # 排名靠后 = 潜在黑马
            is_dark_horse = True
        
        # 计算分数
        gap_score = calculate_gap_score(track.rank, mainstream_rank)
        
        # 生成匹配键
        match_key = generate_match_key(track.track_name, track.artist)
        
        dark_horses.append(DarkHorseTrack(
            track_name=track.track_name,
            artist=track.artist,
            douyin_rank=track.rank,
            douyin_heat=track.heat,
            mainstream_rank=mainstream_rank,
            mainstream_platform=mainstream_platform,
            gap_score=gap_score,
            is_dark_horse=is_dark_horse,
            discovery_time=discovery_time,
            match_key=match_key,
        ))
    
    # 按 gap_score 降序排序
    dark_horses.sort(key=lambda x: (x.is_dark_horse, x.gap_score), reverse=True)
    
    return dark_horses


# =========================================================
# 主函数
# =========================================================
def analyze_dark_horses(
    json_path: Optional[Path] = None,
    json_url: Optional[str] = None,
    use_mock: bool = False,
) -> Dict[str, Any]:
    """
    执行黑马分析
    
    参数：
    - json_path: 本地 JSON 文件路径
    - json_url: 线上 JSON URL
    - use_mock: 是否使用模拟数据
    
    返回：分析结果字典
    """
    print("[dark_horse] Starting analysis...")
    print(f"  Beijing time: {beijing_timestamp()}")
    
    # 1. 获取抖音热歌数据 (List_A)
    print("\n[Step 1] Fetching Douyin hot tracks...")
    if use_mock:
        douyin_tracks = get_mock_douyin_tracks()
    else:
        douyin_tracks = fetch_netease_douyin_chart(limit=50)
        if not douyin_tracks:
            print("  [WARN] API failed, falling back to mock data")
            douyin_tracks = get_mock_douyin_tracks()
    
    print(f"  Got {len(douyin_tracks)} Douyin tracks")
    
    # 2. 获取传统平台数据 (List_B)
    print("\n[Step 2] Fetching mainstream platform tracks...")
    mainstream_tracks: List[TrackInfo] = []
    
    if use_mock:
        mainstream_tracks = get_mock_mainstream_tracks()
    else:
        # 优先从本地文件读取
        if json_path and json_path.exists():
            mainstream_tracks = fetch_mainstream_from_json(json_path)
            print(f"  Loaded {len(mainstream_tracks)} tracks from local JSON")
        
        # 如果本地没有，尝试从 URL 获取
        if not mainstream_tracks and json_url:
            mainstream_tracks = fetch_mainstream_from_url(json_url)
            print(f"  Loaded {len(mainstream_tracks)} tracks from URL")
        
        # 如果都失败，使用 mock 数据
        if not mainstream_tracks:
            print("  [WARN] No mainstream data, falling back to mock")
            mainstream_tracks = get_mock_mainstream_tracks()
    
    print(f"  Got {len(mainstream_tracks)} mainstream tracks")
    
    # 3. 执行黑马识别
    print("\n[Step 3] Identifying dark horses...")
    dark_horses = identify_dark_horses(douyin_tracks, mainstream_tracks)
    
    # 统计
    total_analyzed = len(dark_horses)
    dark_horse_count = sum(1 for d in dark_horses if d.is_dark_horse)
    
    print(f"  Analyzed: {total_analyzed} tracks")
    print(f"  Dark horses found: {dark_horse_count}")
    
    # 4. 构建结果
    result = {
        "generated_at": beijing_timestamp(),
        "analysis_date": beijing_today_iso(),
        "summary": {
            "douyin_tracks_count": len(douyin_tracks),
            "mainstream_tracks_count": len(mainstream_tracks),
            "total_analyzed": total_analyzed,
            "dark_horse_count": dark_horse_count,
            "douyin_top_n": DOUYIN_TOP_N,
            "mainstream_threshold": MAINSTREAM_THRESHOLD,
        },
        "dark_horses": [d.to_dict() for d in dark_horses if d.is_dark_horse],
        "all_tracks": [d.to_dict() for d in dark_horses],
    }
    
    return result


def save_result(result: Dict[str, Any], output_path: Path) -> None:
    """保存分析结果"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] Result saved to: {output_path}")


if __name__ == "__main__":
    # 默认路径
    default_json = ROOT.parent / "frontend" / "data" / "merged_events_latest.json"
    default_url = "https://mdlm.hawnlink.cn/data/merged_events_latest.json"
    output_path = ROOT / "test_merged_result.json"
    
    # 执行分析
    result = analyze_dark_horses(
        json_path=default_json,
        json_url=default_url,
        use_mock=False,
    )
    
    # 保存结果
    save_result(result, output_path)
    
    # 打印黑马歌曲
    print("\n" + "=" * 50)
    print("🐴 Dark Horse Tracks (Top 10):")
    print("=" * 50)
    for i, dh in enumerate(result["dark_horses"][:10], 1):
        status = "🔥" if dh["mainstream_rank"] is None else "📈"
        print(f"{i}. {status} {dh['track_name']} - {dh['artist']}")
        print(f"   抖音: #{dh['douyin_rank']} | 主流平台: {dh['mainstream_platform']} #{dh['mainstream_rank'] or '未上榜'}")
        print(f"   Gap Score: {dh['gap_score']}")
