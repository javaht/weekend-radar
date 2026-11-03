# -*- coding: utf-8 -*-
"""源适配器基类 —— 三种契约, 决定了「谁来做提取」。

  JsonSource    : 接口已经是结构化数据, 代码直接映射字段。票价/时间是精确值。
  HtmlSource    : 页面结构杂, 交给 LLM 提取。
  SitemapSource : 只做链接发现, 不产出结构化事件(用于登录墙/反爬站点)。

为什么坚持 JSON 源不过 LLM:
  大麦和 B站接口直接给 priceStr, 让模型转述一遍只会引入误差(把 88 写成 80,
  把"预售"写成"现场"), 而票价和购票类型恰恰是最不能错的字段。
"""
import datetime

from core.models import Event
from core.timerange import parse_date, has_explicit_year


class SourceResult:
    __slots__ = ("name", "ok", "count", "events", "err", "note", "used_llm", "raw_bytes")

    def __init__(self, name, ok=False, count=0, events=None, err="", note="",
                 used_llm=False, raw_bytes=0):
        self.name = name
        self.ok = ok
        self.count = count
        self.events = events or []
        self.err = err
        self.note = note
        self.used_llm = used_llm
        self.raw_bytes = raw_bytes

    def __repr__(self):
        return "SourceResult(%s ok=%s n=%d err=%s)" % (
            self.name, self.ok, self.count, self.err)


def coerce_event(raw, source_name, default_category="A", fallback_url=""):
    """把 LLM / 接口给出的 dict 转成 Event。缺 name 或 start_date 直接丢弃。"""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    sd = _parse_flexible(raw.get("start_date") or raw.get("start_time"))
    if not sd:
        return None
    ed = _parse_flexible(raw.get("end_date") or raw.get("end_time")) or sd
    if ed < sd:
        ed = sd
    url = str(raw.get("url") or "").strip() or fallback_url
    cat = str(raw.get("category") or default_category).strip().upper()
    if cat not in ("A", "B", "C"):
        cat = default_category
    ev = Event(
        name=name,
        start_date=sd.isoformat(),
        end_date=ed.isoformat(),
        venue=str(raw.get("venue") or "").strip(),
        price_text=str(raw.get("price_text") or "").strip(),
        is_free=_tri(raw.get("is_free")),
        category=cat,
        url=url,
        source=source_name,
        deadline=str(raw.get("deadline") or "").strip(),
        has_goods=_tri(raw.get("has_goods")),
        i_friendly=_tri(raw.get("i_friendly")),
        note=str(raw.get("note") or "").strip(),
    )
    return ev


def _tri(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "是", "免费"):
        return True
    if s in ("false", "0", "no", "否", "收费"):
        return False
    return None


def _parse_flexible(value):
    """兼容 ISO 串、带时间串、epoch 秒/毫秒。

    补年份规则: 只给了 11/7 这种, 用今年; 若得到的日期比今天早 60 天以上,
    认为是明年(因为雷达只看未来), 加一年。这条规则避免年底/年初的跨年错误。

    ★ 只对「原文没写年份」的串补年。写明了 2025-11-20 的就是去年的活动
      (常见于 LLM 读到旧文章), 加一年会把它伪装成本月活动混进报告。
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e11:      # 毫秒
            v = v / 1000.0
        try:
            return datetime.datetime.fromtimestamp(v).date()
        except Exception:
            return None
    s = str(value).strip()
    if not s:
        return None
    today = datetime.date.today()
    d = parse_date(s, today.year)
    if d is None:
        return None
    if not has_explicit_year(s) and d < today - datetime.timedelta(days=60):
        try:
            d = d.replace(year=d.year + 1)
        except ValueError:
            pass
    return d
