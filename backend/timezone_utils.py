"""
时区统一处理模块

所有业务日期计算统一使用北京时间 (Asia/Shanghai, UTC+8)
确保 GitHub Actions (UTC) 和本地开发环境的行为一致
"""
import os
from datetime import datetime, date, timedelta, timezone

# 北京时区 UTC+8
BEIJING_TZ = timezone(timedelta(hours=8))


def beijing_now() -> datetime:
    """获取当前北京时间"""
    return datetime.now(BEIJING_TZ)


def beijing_today() -> date:
    """获取当前北京日期"""
    return beijing_now().date()


def beijing_today_iso() -> str:
    """获取当前北京日期的 ISO 格式字符串 (YYYY-MM-DD)"""
    return beijing_today().isoformat()


def beijing_now_iso() -> str:
    """获取当前北京时间的 ISO 格式字符串 (YYYY-MM-DDTHH:MM:SS)"""
    return beijing_now().replace(microsecond=0).isoformat()


def beijing_timestamp() -> str:
    """获取当前北京时间的时间戳字符串 (YYYY-MM-DD HH:MM:SS)"""
    return beijing_now().strftime("%Y-%m-%d %H:%M:%S")


def parse_date(date_str: str) -> date:
    """解析日期字符串为 date 对象"""
    if not date_str or not date_str.strip():
        return beijing_today()
    return date.fromisoformat(date_str.strip())


def get_target_date(env_var: str = "TARGET_DATE", default: str = None) -> str:
    """
    获取目标日期
    
    优先级：
    1. 环境变量指定的日期
    2. 默认值参数
    3. 当前北京日期
    """
    env_date = os.getenv(env_var, "").strip()
    if env_date:
        try:
            # 验证格式
            date.fromisoformat(env_date)
            return env_date
        except ValueError:
            pass
    
    if default:
        return default
    
    return beijing_today_iso()


def days_ago_beijing(days: int) -> str:
    """获取 N 天前的北京日期"""
    return (beijing_today() - timedelta(days=days)).isoformat()


def date_range_beijing(days: int) -> tuple:
    """
    获取最近 N 天的日期范围
    
    返回 (start_date, end_date) 格式为 YYYY-MM-DD
    包含今天，所以实际是 days 天的数据
    """
    today = beijing_today()
    start = today - timedelta(days=max(days, 1) - 1)
    return (start.isoformat(), today.isoformat())


def detected_at_for_day(day: str) -> str:
    """
    为指定分析日期生成 detected_at 时间戳
    
    确保 detected_at 的日期部分等于目标分析日期，
    这样 merge_events 按 substr(detected_at,1,10) 查询时能正确匹配
    """
    try:
        d = date.fromisoformat(day)
    except ValueError:
        d = beijing_today()
    
    # 使用当前北京时间的时分秒
    now = beijing_now()
    dt = datetime(d.year, d.month, d.day, now.hour, now.minute, now.second, tzinfo=BEIJING_TZ)
    return dt.replace(microsecond=0).isoformat()


# 兼容旧代码的别名
now_iso = beijing_now_iso
today_iso = beijing_today_iso
