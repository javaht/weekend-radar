# -*- coding: utf-8 -*-
"""源注册表 —— 把 config.ini 的一行文本变成可执行的源对象。

加一个平台 = config 里加一行 + 跑 --probe 校准, 不改这里。
只有需要特殊逻辑的(大麦参数、B站参数、090expo 文章发现)才在下面登记。
"""
from core.config import parse_source_row
from sources.base import SourceResult
from sources.html_sources import HtmlSource, Expo090Source
from sources.json_sources import DamaiSource, BiliSource, CppSource, CnccSource

# 默认类目: A 展会/漫展/市集  B 技术  C 博览会
CATEGORY_HINTS = {
    "expo090": "A", "manzhan": "A", "huodongxing": "A", "bendibao": "A",
    "damai": "A", "bili": "A", "lianpu": "A", "huodong": "A", "xiudong": "A",
    "chnmuseum": "A", "chncpa": "A", "artron": "A", "cpp": "A",
    "gcores": "B", "taptap": "B", "indienova": "B", "oschina": "B",
    "segmentfault": "B", "csdn": "B", "xiaoheihe": "B", "openatom": "B",
    "datexpo": "C", "onezh": "C", "zhandaren": "C", "cnena": "C", "cncc": "C",
}

# 已知不可用: 写了也不去抓, probe 直接报红, 省得你以为配好了
KNOWN_UNAVAILABLE = {
    "xiaohongshu": "登录墙 + 强反爬, 且原文大量软广",
    "weibo": "超话流需登录态",
}


class UnsupportedSource:
    """声明了但没法实现的源 —— 显式失败, 不假装成功。"""

    def __init__(self, name, reason, log=print):
        self.name = name
        self.reason = reason
        self.log = log

    def fetch(self):
        return SourceResult(self.name, False, err="不支持: %s" % self.reason)


def build_sources(cfg, src_rows, fetcher, llm, today, window_end,
                  log=print, include_disabled=False, only=None):
    sources = []
    for name, val in (src_rows or {}).items():
        name = name.strip().lower()
        if only and name not in only:
            continue
        if name in KNOWN_UNAVAILABLE:
            sources.append(UnsupportedSource(name, KNOWN_UNAVAILABLE[name], log))
            continue
        parsed = parse_source_row(val)
        if not parsed:
            log("  [警告] [sources] %s 配置格式错误, 跳过: %r" % (name, val))
            continue
        if not parsed["enabled"] and not include_disabled:
            continue
        url = parsed["url"]
        kind = parsed["kind"]
        cat = CATEGORY_HINTS.get(name, "A")

        if kind == "J":
            if name == "damai":
                obj = DamaiSource(url, cfg["extra"], fetcher, log)
            elif name == "bili":
                obj = BiliSource(url, cfg["extra"], fetcher, log)
            elif name == "cpp":
                obj = CppSource(url, cfg["extra"], fetcher, log)
            elif name == "cncc":
                obj = CnccSource(url, cfg["extra"], fetcher, log)
            else:
                obj = UnsupportedSource(name, "未实现该 JSON 源", log)
        elif kind == "H":
            if name == "expo090":
                obj = Expo090Source(name, url, cfg["extra"], fetcher, llm,
                                    today, window_end, log, cat)
            else:
                obj = HtmlSource(name, url, cfg["extra"], fetcher, llm,
                                 today, window_end, log, cat)
        else:  # S: 只发现链接
            obj = UnsupportedSource(name, "站点地图模式仅做链接发现, 未实现", log)

        obj.enabled = parsed["enabled"]
        obj.url = url
        sources.append(obj)
    return sources
