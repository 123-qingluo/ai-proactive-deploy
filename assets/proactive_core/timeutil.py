"""时间窗、时区、相对时间解析。"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


class TimeError(ValueError):
    pass


def get_tz(name: str | None):
    if not name or name == "local":
        return None
    if ZoneInfo is None:
        raise TimeError("当前 Python 没有 zoneinfo，请使用 timezone=local 或安装 tzdata")
    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise TimeError(f"无效时区 {name!r}: {exc}") from exc


def parse_hhmm(value: str) -> int:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (value or "").strip())
    if not m:
        raise TimeError(f"时间格式必须是 HH:MM: {value!r}")
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour == 24 and minute == 0:
        return 24 * 60
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise TimeError(f"时间越界: {value!r}")
    return hour * 60 + minute


def to_local(ts: float, tz) -> datetime:
    return datetime.fromtimestamp(ts, tz) if tz else datetime.fromtimestamp(ts)


def make_dt(year: int, month: int, day: int, hour: int, minute: int, tz) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=tz) if tz else datetime(year, month, day, hour, minute)


def minute_of_day(ts: float, tz) -> int:
    dt = to_local(ts, tz)
    return dt.hour * 60 + dt.minute


def date_key(ts: float, tz) -> str:
    return to_local(ts, tz).strftime("%Y-%m-%d")


def day_start(ts: float, tz) -> float:
    dt = to_local(ts, tz)
    return float(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def hour_key(ts: float, tz) -> str:
    return to_local(ts, tz).strftime("%Y-%m-%dT%H")


def in_window(ts: float, start: str, end: str, tz) -> bool:
    s, e = parse_hhmm(start), parse_hhmm(end)
    t = minute_of_day(ts, tz)
    if s == e:
        return False
    if s < e:
        return s <= t < e
    return t >= s or t < e


def windows_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """判断两个日窗口是否重叠；支持跨午夜窗口。"""
    def segments(start: str, end: str):
        s, e = parse_hhmm(start), parse_hhmm(end)
        if s == e:
            return []
        if s < e:
            return [(s, e)]
        return [(s, 1440), (0, e)]

    for s1, e1 in segments(a_start, a_end):
        for s2, e2 in segments(b_start, b_end):
            if max(s1, s2) < min(e1, e2):
                return True
    return False


WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _period_hour(text: str) -> int:
    if re.search(r"早上|上午|清晨", text):
        return 10
    if re.search(r"中午", text):
        return 12
    if re.search(r"下午", text):
        return 15
    if re.search(r"傍晚", text):
        return 18
    if re.search(r"晚上|今晚|明晚|夜里", text):
        return 20
    return 12


def parse_due_hint(text: str, now: float, tz) -> float | None:
    """从承诺句里解析一个到期时间；失败返回 None（不猜）。"""
    if not text:
        return None
    base = to_local(now, tz)
    target: datetime | None = None
    hour = _period_hour(text)

    m = re.search(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*[日号]?", text)
    if m:
        try:
            target = make_dt(int(m.group(1)), int(m.group(2)), int(m.group(3)), hour, 0, tz)
        except ValueError:
            return None

    if target is None:
        m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            year = base.year
            try:
                target = make_dt(year, month, day, hour, 0, tz)
            except ValueError:
                return None
            if target < base:
                try:
                    target = make_dt(year + 1, month, day, hour, 0, tz)
                except ValueError:
                    return None

    if target is None:
        if "大后天" in text:
            target = base + timedelta(days=3)
        elif "后天" in text:
            target = base + timedelta(days=2)
        elif "明天" in text or "明日" in text or "明晚" in text:
            target = base + timedelta(days=1)
        elif "今天" in text or "今日" in text or "今晚" in text:
            target = base

    if target is None:
        m = re.search(r"下个?月\s*(初|中|底|末)", text)
        if m:
            year, month = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
            day = 1 if m.group(1) == "初" else (15 if m.group(1) == "中" else calendar.monthrange(year, month)[1])
            target = make_dt(year, month, day, hour, 0, tz)
        elif re.search(r"下个?月", text):
            year, month = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
            target = make_dt(year, month, 1, hour, 0, tz)

    if target is None:
        m = re.search(r"(下下?周|下个?星期)\s*([一二三四五六日天])", text)
        if m:
            offset = 2 if m.group(1).startswith("下下") else 1
            monday = base - timedelta(days=base.weekday())
            target = monday + timedelta(weeks=offset, days=WEEKDAY[m.group(2)])
        elif "下周末" in text:
            monday = base - timedelta(days=base.weekday())
            target = monday + timedelta(weeks=2, days=5)
        elif re.search(r"这周末|本周末|周末", text):
            monday = base - timedelta(days=base.weekday())
            target = monday + timedelta(days=5)
            if target.date() < base.date():
                target += timedelta(weeks=1)

    if target is None:
        m = re.search(r"月底|月末", text)
        if m:
            target = make_dt(base.year, base.month, calendar.monthrange(base.year, base.month)[1], hour, 0, tz)

    if target is None:
        m = re.search(r"(?:周|星期)([一二三四五六日天])", text)
        if m:
            delta = (WEEKDAY[m.group(1)] - base.weekday()) % 7
            target = base + timedelta(days=delta)

    if target is None:
        return None

    if isinstance(target, datetime):
        result = target
    else:  # pragma: no cover
        result = make_dt(target.year, target.month, target.day, hour, 0, tz)
    result = result.replace(hour=hour, minute=0, second=0, microsecond=0)
    return float(result.timestamp())
