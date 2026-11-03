# -*- coding: utf-8 -*-
"""HTML 源: 抓页面原文 → LLM 提取。

为什么不做 CSS 选择器:
  这 18 个站点各写一套选择器, 是 18 份随时会坏的代码, 且改版后只能人工排查。
  统一交给 LLM, 改版时最多是"某源条数变少", probe 能立刻看见, 不用改代码。

090expo 特殊: 每月一篇排期文章, 文章 ID 随月份变, 所以先发现再抓取。
"""
import re
from html import unescape as _unescape

from sources.base import SourceResult, coerce_event
from core.timerange import parse_date

# 把 HTML 压成纯文本。保留表格的行结构, 否则日期和活动名会粘连。
_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>")
_BR_RE = re.compile(r"(?i)<\s*(br|/p|/div|/tr|/li|/h\d)[^>]*>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"[ \t\u3000\xa0]+")
_MULTI_NL = re.compile(r"\n{3,}")


def html_to_text(html):
    if not html:
        return ""
    s = _SCRIPT_RE.sub(" ", html)
    s = _BR_RE.sub("\n", s)
    s = _TAG_RE.sub(" ", s)
    s = _unescape(s)   # 覆盖全部实体(含 &#x...;); &nbsp; 解出的 \xa0 由 _WS_RE 吃掉
    s = _WS_RE.sub(" ", s)
    s = _MULTI_NL.sub("\n\n", s)
    return "\n".join(line.strip() for line in s.splitlines() if line.strip())


class HtmlSource:
    """通用 HTML 源。"""

    name = "html"
    kind = "H"

    def __init__(self, name, url, cfg, fetcher, llm, today, window_end,
                 log=print, default_category="A"):
        self.name = name
        self.url = url
        self.cfg = cfg
        self.fetcher = fetcher
        self.llm = llm
        self.today = today
        self.window_end = window_end
        self.log = log
        self.default_category = default_category

    def resolve_url(self):
        """允许子类改写: 先发现真实要抓的页面。"""
        return self.url

    def probe(self):
        """只抓不发模型 —— probe 要便宜, 判断的是"这个站通不通"。"""
        try:
            target = self.resolve_url()
        except Exception as e:
            return {"ok": False, "status": 0, "bytes": 0,
                    "note": "解析入口失败: %s" % e}
        res = self.fetcher.get(target)
        if not res.ok:
            return {"ok": False, "status": res.status, "bytes": 0,
                    "note": "%s | %s" % (res.err, target)}
        text = html_to_text(res.text)
        ok = len(text) >= 200
        note = "正文 %d 字 | %s" % (len(text), target)
        if not ok:
            note = "正文过短(%d 字)，可能需登录或纯 JS 渲染 | %s" % (len(text), target)
        return {"ok": ok, "status": res.status,
                "bytes": len(res.text or ""), "note": note}

    def fetch(self):
        target = self.resolve_url()
        res = self.fetcher.get(target)
        if not res.ok:
            return SourceResult(self.name, False, err=res.err or ("HTTP %s" % res.status))
        text = html_to_text(res.text)
        if len(text) < 200:
            return SourceResult(
                self.name, False,
                err="页面正文过短(%d 字), 可能需登录或纯 JS 渲染" % len(text),
                raw_bytes=len(res.text or ""))
        lr = self.llm.extract_events(
            text, self.name,
            self.today.isoformat(), self.window_end.isoformat(),
            default_category=self.default_category, declared_url=target)
        if not lr.ok:
            return SourceResult(self.name, False, err="LLM 失败: %s" % lr.err,
                                used_llm=True, raw_bytes=len(res.text or ""))
        raw_items = lr.text if isinstance(lr.text, list) else []
        events = []
        for raw in raw_items:
            ev = coerce_event(raw, self.name,
                              default_category=self.default_category,
                              fallback_url=target)
            if ev:
                events.append(ev)
        note = "页面 %d 字" % len(text)
        if getattr(lr, "truncated", False):
            note += "（已截断）"
        return SourceResult(self.name, True, len(events), events,
                            note=note, used_llm=True, raw_bytes=len(res.text or ""))


class Expo090Source(HtmlSource):
    """零距离会展: 每月一篇排期文章, ID 随月份变化, 需要先发现。"""

    name = "expo090"
    kind = "H"

    def resolve_url(self):
        """找当月的「北京漫展演出排期表」文章。

        现实: 该站列表页只展示最近 30 条分类文章, 北京漫展那篇常常翻不到,
        所以优先用 config 里 [extra] expo090_article 指定的 URL;
        没指定或抓不到时才去列表页扫。
        「文章 ID 每月变」这件事无法靠代码推断, 需要人工改一次配置。
        """
        pinned = str(self.cfg.get("expo090_article", "") or "").strip()
        if pinned:
            check = self.fetcher.get(pinned)
            if check.ok and len(check.text or "") > 1000:
                return pinned
            self.log("  [警告] expo090 指定文章不可用, 回退到列表页探测: %s" % pinned)

        res = self.fetcher.get(self.url)
        if not res.ok:
            return self.url
        html = res.text or ""
        year = self.today.year
        month = self.today.month
        city_name = str(self.cfg.get("city", "北京") or "北京").strip()
        if len(city_name) > 2 and city_name.endswith("市"):
            city_name = city_name[:-1]
        base = re.match(r"(https?://[^/]+)", self.url)
        root = base.group(1) if base else "http://www.090expo.com"
        # 优先当月的, 其次下月(月底时下月排期通常已出)
        for m in (month, month % 12 + 1):
            for href, title in re.findall(
                    r"\"(/news/exnews_\d+\.html)\"[^>]*>([^<]{0,80})", html):
                if str(year) in title and ("%d月" % m) in title and city_name in title:
                    candidate = root + href
                    check = self.fetcher.get(candidate)
                    if check.ok and len(check.text or "") > 1000:
                        self.log("  [i] 090expo 命中当月文章: %s" % candidate)
                        return candidate
        return self.url
