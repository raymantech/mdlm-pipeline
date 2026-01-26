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

    # 2) 数据抓取
    # 注意：ingest 脚本会使用当前北京日期，确保数据被正确归类
    run([PY, str(ROOT / "ingest_qq.py")], "Ingest QQ Music charts")
    run([PY, str(ROOT / "ingest_kugou.py")], "Ingest Kugou charts")

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

    # 5) 导出前端 JSON (仅最近7天)
    export_script = ROOT / "export_dashboard_data.py"
    if export_script.exists():
        out = os.getenv("EXPORT_OUT", 
                        str((ROOT.parent / "frontend" / "data" / "merged_events_latest.json").resolve()))
        days = os.getenv("EXPORT_DAYS", "7")
        
        run([PY, str(export_script), "--days", str(days), "--out", out],
            f"Export dashboard JSON (last {days} days)")
        
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
