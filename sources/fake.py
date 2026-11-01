# -*- coding: utf-8 -*-
"""离线假源 —— 让 --self-test 在没有网络、没有 LLM、没有配置的机器上也能跑通
整条链路。这是唯一能在部署前证明「差分和渲染逻辑是对的」的手段。
"""
from sources.base import SourceResult
from core.models import Event


class FakeSource:
    name = "fake"
    kind = "H"

    def __init__(self, events=None, fail=False, log=print):
        self.log = log
        self.fail = fail
        self._events = events if events is not None else self._default_events()

    @staticmethod
    def _default_events():
        """覆盖几个关键场景: 免费/收费、有实物、多日跨周末、C 类博览会。"""
        return [
            Event(name="假·开源游园会", start_date="2026-11-14", end_date="2026-11-14",
                  venue="通明湖会展中心", price_text="", is_free=True, category="B",
                  url="http://example.com/gotc", source="fake", deadline="",
                  has_goods=True, i_friendly=True, note="免费+市集+周边"),
            Event(name="假·同人only", start_date="2026-11-07", end_date="2026-11-08",
                  venue="某会展中心", price_text="¥80 预售", is_free=False, category="A",
                  url="http://example.com/only", source="fake", deadline="",
                  has_goods=True, i_friendly=None, note="双日"),
            Event(name="假·半导体博览会", start_date="2026-11-12", end_date="2026-11-14",
                  venue="国家会议中心", price_text="", is_free=True, category="C",
                  url="http://example.com/ic", source="fake", deadline="",
                  has_goods=None, i_friendly=None, note="周末有场"),
            Event(name="假·已结束活动", start_date="2026-09-01", end_date="2026-09-02",
                  venue="某地", price_text="免费", is_free=True, category="A",
                  url="", source="fake", note="应被过滤"),
        ]

    def fetch(self):
        if self.fail:
            return SourceResult(self.name, False, err="自测模拟失败")
        return SourceResult(self.name, True, len(self._events), list(self._events),
                            note="离线假数据")

    def probe(self):
        return {"ok": not self.fail, "status": 200, "bytes": 0,
                "note": "离线假数据（不联网）"}
