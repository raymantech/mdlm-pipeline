# backend/run_daily.py
"""
MDLM 每日数据处理管道

功能：
1. 初始化数据库
2. 抓取 QQ/酷狗 榜单数据
3. 分析事件
4. 合并事件
5. 导出前端 JSON（仅最近7天）

注意：数据库 charts.db 保存完整历史，
      merged_events_latest.json 只保留最近7天用于前端展示
"""
import os
import sys
import argparse
import subprocess
from pathlib import Path

# 确保时区处理模块在路径中
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from timezone_utils import beijing_timestamp, beijing_today_iso, get_target_date

PY = sys.executable


def ts():
    """返回当前北京时间戳"""
    return beijing_timestamp()


def run(cmd, title):
    """执行命令并打印日志"""
    print(f"{ts()} [STEP] {title}")
    result = subprocess.run(cmd, check=True)
    print(f"{ts()} [OK]   {title}")
    return result


def main():
    parser = argparse.ArgumentParser(description="MDLM Daily Pipeline")
    parser.add_argument("--date", "-d", 
                        help="指定分析日期 (YYYY-MM-DD)，默认使用北京时间当天",
                        default=None)
    parser.add_argument("--enable-douyin-qishui", action="store_true",
                        help="启用抖音汽水热歌榜抓取（默认关闭）")
    parser.add_argument("--skip-kugou", action="store_true",
                        help="跳过酷狗音乐抓取（临时开关，用于解决数据库 schema 问题）")
    parser.add_argument("--only", choices=["qishui", "douyin-qishui"],
                        help="仅执行指定平台的抓取（用于本地调试，例如：--only qishui 只执行抖音汽水抓取+分析导出）")
    args = parser.parse_args()
    
    # 确定目标日期
    if args.date:
        target_date = args.date.strip()
    else:
        target_date = get_target_date()
    
    print(f"{ts()} [INFO] Target date: {target_date}")
    print(f"{ts()} [INFO] Beijing today: {beijing_today_iso()}")
    
    # 设置环境变量
    os.environ.setdefault("MDLM_DB", str(ROOT / "charts.db"))
    os.environ["TARGET_DATE"] = target_date
    
    db_path = Path(os.environ["MDLM_DB"])
    print(f"{ts()} [INFO] Database path: {db_path}")
    
    # 检查数据库是否已存在（从 data 分支恢复的）
    if db_path.exists():
        db_size = db_path.stat().st_size
        print(f"{ts()} [INFO] Existing database found, size: {db_size} bytes")
    else:
        print(f"{ts()} [INFO] No existing database, will create new one")

    # 1) 初始化数据库（如果不存在则创建表结构）
    run([PY, str(ROOT / "db_init.py")], "Init SQLite database")

    # 判断是否仅执行 qishui（用于本地调试）
    only_qishui = args.only in ("qishui", "douyin-qishui")
    
    # 2) 数据抓取
    if not only_qishui:
        # 注意：ingest 脚本会使用当前北京日期，确保数据被正确归类
        run([PY, str(ROOT / "ingest_qq.py")], "Ingest QQ Music charts")
        
        # 酷狗音乐（可临时跳过）
        skip_kugou = args.skip_kugou or os.getenv("SKIP_KUGOU", "").lower() in ("1", "true", "yes")
        if skip_kugou:
            print(f"{ts()} [SKIP] Kugou disabled")
        else:
            run([PY, str(ROOT / "ingest_kugou.py")], "Ingest Kugou charts")
        
        # 网易云音乐（独立脚本）
        netease_script = ROOT / "ingest_netease.py"
        if netease_script.exists():
            try:
                run([PY, str(netease_script)], "Ingest Netease Music charts")
            except Exception as e:
                print(f"{ts()} [WARN] Netease ingest failed: {e}")
    else:
        print(f"{ts()} [SKIP] QQ/Kugou/Netease ingest (--only qishui mode)")
    
    # 抖音汽水热歌榜开关判断（提前判断，用于决定是否跳过 ingest_douyin.py）
    enable_qishui = only_qishui or args.enable_douyin_qishui or os.getenv("ENABLE_DOUYIN_QISHUI", "").lower() in ("1", "true", "yes")
    
    # 抖音/汽水音乐（独立脚本）
    # 如果启用了抖音汽水热歌榜抓取，则跳过 ingest_douyin.py，避免同平台重复写入
    if not enable_qishui:
        douyin_script = ROOT / "ingest_douyin.py"
        if douyin_script.exists():
            try:
                run([PY, str(douyin_script)], "Ingest Douyin/Qishui Music charts")
            except Exception as e:
                print(f"{ts()} [WARN] Douyin ingest failed: {e}")
    else:
        print(f"{ts()} [SKIP] Douyin/Qishui Music charts (using Qishui Hot Chart instead)")
    
    # 抖音汽水热歌榜（独立脚本，需手动开启或 --only qishui）
    if enable_qishui:
        qishui_script = ROOT / "ingest_douyin_qishui.py"
        if qishui_script.exists():
            try:
                # 显式传递 --top 100，保证 TopN 语义一致性
                cmd = [PY, str(qishui_script), "--date", target_date, "--top", "100"]
                run(cmd, f"Ingest Douyin Qishui Hot Chart for {target_date} (Top 100)")
                
                # 检查实际抓取数量（从输出文件读取）
                qishui_output = ROOT.parent / "data" / "douyin_qishui_latest.json"
                if qishui_output.exists():
                    import json
                    try:
                        with open(qishui_output, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        actual_count = len(data.get("tracks", []))
                        if actual_count < 100:
                            print(f"{ts()} [INFO] Qishui actual tracks: {actual_count} (target: 100, less than expected but not an error)")
                    except Exception:
                        pass  # 忽略读取错误
            except Exception as e:
                print(f"{ts()} [WARN] Douyin Qishui ingest failed: {e}")
        else:
            print(f"{ts()} [WARN] Douyin Qishui script not found: {qishui_script}")
    else:
        print(f"{ts()} [SKIP] Douyin Qishui ingest disabled (use --enable-douyin-qishui or ENABLE_DOUYIN_QISHUI=1)")

    # 3) 事件分析 - 传递目标日期
    analyze_script = ROOT / "analyze_events.py"
    if analyze_script.exists():
        run([PY, str(analyze_script), "--day", target_date], 
            f"Analyze events for {target_date}")

    # 4) 事件合并 - 传递目标日期
    merge_script = ROOT / "merge_events.py"
    if merge_script.exists():
        run([PY, str(merge_script), "--day", target_date], 
            f"Merge events for {target_date}")

    # 5) 导出前端 JSON (增量合并模式)
    export_script = ROOT / "export_dashboard_data.py"
    if export_script.exists():
        out = os.getenv("EXPORT_OUT", 
                        str((ROOT.parent / "frontend" / "data" / "merged_events_latest.json").resolve()))
        days = os.getenv("EXPORT_DAYS", "0")  # 0 表示让增量合并逻辑自动处理
        history_url = os.getenv("HISTORY_JSON_URL", "")
        
        cmd = [PY, str(export_script), "--days", str(days), "--out", out]
        if history_url:
            cmd.extend(["--history-url", history_url])
        
        run(cmd, f"Export dashboard JSON (incremental merge)")
        
        # 打印导出结果统计
        out_path = Path(out)
        if out_path.exists():
            import json
            try:
                with open(out_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                event_count = len(data.get('events', []))
                print(f"{ts()} [INFO] Exported {event_count} events to {out_path}")
            except Exception as e:
                print(f"{ts()} [WARN] Could not read export file: {e}")

    print(f"{ts()} [DONE] Daily pipeline completed for {target_date}")


if __name__ == "__main__":
    main()
