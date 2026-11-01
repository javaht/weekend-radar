# -*- coding: utf-8 -*-
"""时间窗口与周末判定 —— 纯函数, 零依赖, 可单测。

为什么这部分绝不能交给 LLM:
  大模型算「11月7日是不是周六」会错, 而且错得很自信。星期几是确定性运算,
  必须由代码算。LLM 只负责从页面里把日期字符串抠出来。
"""
import datetime
import re

_DATE_PATTERNS = [
    re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})"),
    re.compile(r"^(\d{1,2})[-/月](\d{1,2})$"),  # 11/7 或 11月7日 —— 缺年份
]


def parse_date(text, default_year=None):
    """把各种日期串解析成 datetime.date。失败返回 None, 绝不猜。"""
    if not text:
        return None
    s = str(text).strip()
    m = _DATE_PATTERNS[0].search(s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _DATE_PATTERNS[1].search(s)
    if m and default_year:
        try:
            return datetime.date(int(default_year), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def is_weekend(d):
    return d.weekday() >= 5  # 5=周六 6=周日


def saturday_of(d):
    """返回 d 所属那个周末的周六。周日归到前一天的周六。"""
    if d.weekday() == 6:      # 周日
        return d - datetime.timedelta(days=1)
    if d.weekday() == 5:      # 周六
        return d
    return None               # 工作日不属于任何周末


def weekend_groups(today, window_days):
    """返回窗口内所有周末分组: [(sat_date, [所有属于该周末且在窗口内的日期])]。

    窗口 [today, today+window_days]，只保留周六周日。
    """
    end = today + datetime.timedelta(days=window_days)
    groups = {}
    d = today
    while d <= end:
        if is_weekend(d):
            sat = saturday_of(d)
            groups.setdefault(sat, [])
            if d not in groups[sat]:
                groups[sat].append(d)
        d += datetime.timedelta(days=1)
    return sorted(groups.items())


def event_weekend_dates(ev_start, ev_end, today, window_days):
    """活动落在哪些周末 —— 返回该周末的周六 date 集合。

    多日活动(如 11/6-11/9)会同时命中两个周末, 这是有意的:
    每个周末都能去, 报告里应该都出现。
    """
    if not ev_start:
        return set()
    ev_end = ev_end or ev_start
    if ev_end < ev_start:
        ev_end = ev_start
    end_of_window = today + datetime.timedelta(days=window_days)
    hits = set()
    d = max(ev_start, today)
    last = min(ev_end, end_of_window)
    while d <= last:
        if is_weekend(d):
            hits.add(saturday_of(d))
        d += datetime.timedelta(days=1)
    return hits


def is_expired(ev_end, today):
    """已结束的活动不进正表。"""
    return bool(ev_end) and ev_end < today


def days_until(target, today):
    if not target:
        return None
    return (target - today).days


def format_group_label(sat, dates):
    """生成 '10/31（六）– 11/1（日）' 这样的分组标题。"""
    if not dates:
        return "%d/%d" % (sat.month, sat.day)
    first, last = min(dates), max(dates)
    wd = "六日"
    if first == last:
        return "%d/%d（%s）" % (first.month, first.day, wd[first.weekday() - 5])
    return "%d/%d（六）– %d/%d（日）" % (first.month, first.day, last.month, last.day)
