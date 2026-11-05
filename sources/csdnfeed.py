# -*- coding: utf-8 -*-
"""CSDN 业界频道文章源: 转载 bot 分页发现 + LLM 标题过滤 + 详情页提取。

链路(2026-11 实测, 无登录无签名):
  1. 发现: https://blog.csdn.net/{bot}/article/list/{page} 是 SSR 纯 HTML,
     每页 40 条(标题+日期+链接), page1 ≈ 最近两天。业界每篇文章都被官方
     转载 bot 镜像成博客文章, 抓 bot 的列表 = 抓业界频道的全量流。
  2. 过滤: 业界流 90%+ 是软文(品牌推广/选购指南/软广), 直接全量进 LLM 提取
     既贵又把日报灌满垃圾。先让 LLM 只看「标题+摘要」挑活动预告类。
  3. 提取: 命中的进详情页 www.csdn.net/article/{date}/{id}, SSR JSON 里
     有 title/postTime/desc/content 全文, 交给 LLM 抽时间地点。

为什么不在发现时用 latestList(10 条/次): bot 分页 40 条/页且可回溯历史,
覆盖更稳; latestList 留作代码注释里的备选, 不实现。
"""
import re

from sources.base import SourceResult, coerce_event, extract_initial_state
from sources.html_sources import html_to_text

# 列表页条目: <a href=".../article/details/{id}" ...>标题</a> ... <span class="date">...</span>
_LIST_ITEM_RE = re.compile(
    r'href="([^"]*?/article/details/(\d+))"[^>]*>\s*(?:<span[^>]*>.*?</span>\s*)?'
    r'([^<]{2,150})\s*</a>.*?class="date"[^>]*>([^<]{4,20})<',
    re.S)
# 详情页 SSR: blog-article-content 组件
_DETAIL_PATH = ("pageData", "data", "blog-article-content")


class CsdnFeedSource:
    name = "csdnfeed"
    kind = "H"   # 需要 LLM, 属于 H 契约, 但发现逻辑特殊所以单独一个类

    # 只翻第 1 页(≈最近两天, 40 条)。雷达每天跑, 不会漏; 想回溯更多改这里
    LIST_PAGES = 1
    BOT = "weixin_51159214"   # 「科技资讯」官方转载 bot
    SITE = "https://www.csdn.net"

    def __init__(self, url, cfg, fetcher, llm, today, window_end, log=print):
        # url 来自 config [sources] 行, 仅作占位; 真实抓取地址由本类决定
        self.url = url
        self.cfg = cfg
        self.fetcher = fetcher
        self.llm = llm
        self.today = today
        self.window_end = window_end
        self.log = log

    # ---------------------------------------------------------------- 发现
    def _discover(self):
        """抓 bot 列表页, 返回 [(article_id, date, title), ...]。"""
        items = []
        for page in range(1, self.LIST_PAGES + 1):
            url = "https://blog.csdn.net/%s/article/list/%d" % (self.BOT, page)
            res = self.fetcher.get(url, headers={"Referer": "https://blog.csdn.net/"})
            if not res.ok:
                if page == 1:
                    return None, res.err or ("HTTP %s" % res.status)
                break
            batch = _LIST_ITEM_RE.findall(res.text or "")
            if not batch:
                if page == 1:
                    return None, "列表页未解析出条目(页面结构可能已改版)"
                break
            for href, aid, title, date in batch:
                items.append((aid, date.strip(), title.strip()))
        return items, ""

    # ---------------------------------------------------------------- 过滤
    def _filter_candidates(self, items):
        """LLM 只看标题+发布时间, 挑出「活动预告/报名」类文章。

        返回 (ids 命中列表, err)。err 非空则本源判失败 —— 宁可告警也不放垃圾进日报。
        """
        lines = ["%s | %s" % (d, t) for aid, d, t in items]
        body = "\n".join(lines)
        system = (
            "你是文章分类器。输入是 CSDN 业界频道文章列表「发布日期 | 标题」。\n"
            "只输出 JSON 数组, 不要解释。\n"
            "任务: 挑出属于【线下/线上活动预告或报名】的条目——"
            "技术沙龙、Meetup、开发者大会、峰会、Workshop、赛事、直播预告、训练营等。\n"
            "排除: 产品软文、品牌推广、选购指南、行情分析、新闻评论、招聘广告。\n"
            "拿不准的宁可排除。输出被选中条目的标题原文: [\"标题1\",\"标题2\"]。"
            "没有一条符合就输出 []。"
        )
        prompt = "文章列表共 %d 条:\n%s" % (len(lines), body)
        res = self.llm.complete(prompt, system)
        if not res.ok:
            return None, "LLM 过滤失败: %s" % res.err
        text = (res.text or "").strip()
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return None, "LLM 过滤输出无法解析: %r" % text[:120]
        try:
            import json as _json
            picked = _json.loads(m.group(0))
        except Exception:
            return None, "LLM 过滤输出不是合法 JSON: %r" % text[:120]
        if not isinstance(picked, list):
            return None, "LLM 过滤输出不是数组"
        picked_set = {str(x).strip() for x in picked}
        hits = [(aid, d, t) for aid, d, t in items if t in picked_set]
        return hits, ""

    # ---------------------------------------------------------------- 提取
    def _fetch_detail(self, aid, date):
        """详情页 -> (正文纯文本, err)。date 是列表页给的 YYYY-MM-DD HH:MM:SS。

        www.csdn.net/article/{date}/{id} 的 SSR JSON 里直接有全文, 首选;
        但实测该 URL 会偶发 404(CMS 缓存滞后, 几分钟后自愈), 所以失败时
        兜底抓 blog 镜像页(HTML 正文), 再失败才判这条丢失。
        """
        www_url = "%s/article/%s/%s" % (self.SITE, date[:10], aid)
        res = self.fetcher.get(www_url, headers={"Referer": self.SITE + "/"})
        if res.ok:
            st = extract_initial_state(res.text)
            node = st
            for key in _DETAIL_PATH:
                node = (node or {}).get(key)
                if not isinstance(node, dict):
                    node = None
                    break
            if node and node.get("content"):
                return html_to_text(node["content"]), ""
            return None, "详情页 SSR 无正文(结构可能已改版)"
        # 兜底: blog 镜像页(不依赖 CMS date 段, 无偶发 404)
        blog_url = "https://blog.csdn.net/%s/article/details/%s" % (self.BOT, aid)
        res2 = self.fetcher.get(blog_url, headers={"Referer": "https://blog.csdn.net/"})
        if res2.ok and len(res2.text or "") > 5000:
            text = html_to_text(res2.text)
            if len(text) > 200:
                return text, ""
        return None, res.err or ("HTTP %s" % res.status)

    def fetch(self):
        items, err = self._discover()
        if err:
            return SourceResult(self.name, False, err=err)
        raw = len(items)

        hits, ferr = self._filter_candidates(items)
        if ferr:
            return SourceResult(self.name, False, err=ferr, used_llm=True)
        self.log("  [i] csdnfeed: %d 篇文章, LLM 挑出 %d 篇疑似活动预告" % (raw, len(hits)))
        if not hits:
            return SourceResult(self.name, True, 0, [],
                                note="列表 %d 篇, 无活动预告类" % raw,
                                used_llm=True)

        events = []
        for aid, date, title in hits:
            text, derr = self._fetch_detail(aid, date)
            if derr:
                self.log("  [警告] csdnfeed 详情失败 %s: %s" % (title[:30], derr))
                continue
            if not text:
                continue
            lr = self.llm.extract_events(
                text, self.name,
                self.today.isoformat(), self.window_end.isoformat(),
                default_category="B",
                declared_url="%s/article/%s/%s" % (self.SITE, date[:10], aid))
            if not lr.ok:
                self.log("  [警告] csdnfeed 提取失败 %s: %s" % (title[:30], lr.err))
                continue
            for raw_ev in (lr.text if isinstance(lr.text, list) else []):
                ev = coerce_event(raw_ev, self.name, default_category="B",
                                  fallback_url="%s/article/%s/%s" % (
                                      self.SITE, date[:10], aid))
                if ev:
                    events.append(ev)

        note = "列表 %d 篇 → 命中 %d 篇 → 抽出 %d 条" % (raw, len(hits), len(events))
        return SourceResult(self.name, True, len(events), events,
                            note=note, used_llm=True)

    def probe(self):
        """probe 要便宜: 只验证列表页抓得到、解析得出条目, 不打 LLM。"""
        items, err = self._discover()
        if err:
            return {"ok": False, "status": 0, "bytes": 0, "note": err}
        sample = " / ".join(t for _, _, t in items[:2])
        return {"ok": len(items) > 0, "status": 200, "bytes": len(items),
                "note": "列表 %d 条, 例: %s" % (len(items), sample[:60])}
