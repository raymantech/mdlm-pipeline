#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
黑马识别测试脚本

独立运行，不影响主项目流程
可用于本地测试和验证算法
"""

import sys
import argparse
from pathlib import Path

# 确保模块在路径中
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dark_horse import (
    analyze_dark_horses, 
    save_result,
    DOUYIN_TOP_N,
    MAINSTREAM_THRESHOLD,
)


def main():
    parser = argparse.ArgumentParser(
        description="黑马歌曲识别测试工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用默认配置运行
  python test_dark_horse.py
  
  # 使用模拟数据测试
  python test_dark_horse.py --mock
  
  # 指定本地 JSON 文件
  python test_dark_horse.py --json ../frontend/data/merged_events_latest.json
  
  # 指定线上 URL
  python test_dark_horse.py --url https://mdlm.hawnlink.cn/data/merged_events_latest.json
  
  # 自定义阈值
  python test_dark_horse.py --douyin-top 30 --mainstream-threshold 50
        """
    )
    
    parser.add_argument(
        "--json", "-j",
        type=str,
        default=None,
        help="本地 JSON 文件路径（默认: frontend/data/merged_events_latest.json）"
    )
    
    parser.add_argument(
        "--url", "-u",
        type=str,
        default="https://mdlm.hawnlink.cn/data/merged_events_latest.json",
        help="线上 JSON URL"
    )
    
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="输出文件路径（默认: backend/test_merged_result.json）"
    )
    
    parser.add_argument(
        "--mock", "-m",
        action="store_true",
        help="使用模拟数据测试（不调用真实 API）"
    )
    
    parser.add_argument(
        "--douyin-top", "-d",
        type=int,
        default=DOUYIN_TOP_N,
        help=f"抖音前 N 名视为热门（默认: {DOUYIN_TOP_N}）"
    )
    
    parser.add_argument(
        "--mainstream-threshold", "-t",
        type=int,
        default=MAINSTREAM_THRESHOLD,
        help=f"传统平台排名阈值（默认: {MAINSTREAM_THRESHOLD}）"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细输出"
    )
    
    args = parser.parse_args()
    
    # 确定路径
    json_path = None
    if args.json:
        json_path = Path(args.json)
        if not json_path.is_absolute():
            json_path = ROOT.parent / json_path
    else:
        # 默认路径
        json_path = ROOT.parent / "frontend" / "data" / "merged_events_latest.json"
    
    output_path = Path(args.output) if args.output else ROOT / "test_merged_result.json"
    
    # 打印配置
    print("=" * 60)
    print("🐴 黑马歌曲识别测试")
    print("=" * 60)
    print(f"  JSON 文件: {json_path}")
    print(f"  线上 URL: {args.url}")
    print(f"  输出文件: {output_path}")
    print(f"  使用模拟数据: {args.mock}")
    print(f"  抖音 Top N: {args.douyin_top}")
    print(f"  主流平台阈值: {args.mainstream_threshold}")
    print("=" * 60)
    
    # 临时修改全局阈值
    import dark_horse
    dark_horse.DOUYIN_TOP_N = args.douyin_top
    dark_horse.MAINSTREAM_THRESHOLD = args.mainstream_threshold
    
    # 执行分析
    result = analyze_dark_horses(
        json_path=json_path,
        json_url=args.url,
        use_mock=args.mock,
    )
    
    # 保存结果
    save_result(result, output_path)
    
    # 打印摘要
    summary = result["summary"]
    print("\n" + "=" * 60)
    print("📊 分析摘要")
    print("=" * 60)
    print(f"  抖音曲目数: {summary['douyin_tracks_count']}")
    print(f"  主流平台曲目数: {summary['mainstream_tracks_count']}")
    print(f"  分析曲目数: {summary['total_analyzed']}")
    print(f"  发现黑马数: {summary['dark_horse_count']}")
    
    # 打印黑马列表
    dark_horses = result["dark_horses"]
    if dark_horses:
        print("\n" + "=" * 60)
        print("🔥 黑马歌曲列表")
        print("=" * 60)
        
        for i, dh in enumerate(dark_horses[:15], 1):
            if dh["mainstream_rank"] is None:
                status = "🆕 未上榜"
                rank_str = "N/A"
            else:
                status = "📈 排名靠后"
                rank_str = f"#{dh['mainstream_rank']}"
            
            print(f"\n{i}. 【{dh['track_name']}】- {dh['artist']}")
            print(f"   ├─ 抖音排名: #{dh['douyin_rank']} (热度: {dh['douyin_heat']})")
            print(f"   ├─ 主流平台: {dh['mainstream_platform']} {rank_str}")
            print(f"   ├─ 状态: {status}")
            print(f"   └─ 信息差分数: {dh['gap_score']:.4f}")
    else:
        print("\n  ⚠️ 未发现黑马歌曲")
    
    # 提示前端页面
    print("\n" + "=" * 60)
    print("📄 前端展示")
    print("=" * 60)
    print(f"  结果已保存到: {output_path}")
    print(f"  打开前端页面: frontend/test_dark_horse.html")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
