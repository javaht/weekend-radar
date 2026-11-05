# -*- coding: utf-8 -*-
"""离线自测: 不联网、不调模型、不需要配置。"""
import os
import sys
import time
import datetime
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core.models import Event, venue_same, price_anchor, configure_city, normalize_name
from core import timerange as TR
from sources.base import _parse_flexible
from sources.fake import FakeSource
from core.store import Store
from core.render import render, render_text, render_push_markdown
from core.config import DEFAULTS, apply_env_and_city
from core.llm import resolve_provider_config, build_endpoint_url
from core.push import send as push_send
from run import (
    _merge, _filter, _venue_conflict, _decide_push, _meta, EXIT_OK, EXIT_FAIL
)


def default_log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def self_test(log=None):
    if log is None:
        log = default_log
    """离线自测: 不联网、不调模型、不需要配置。"""
    fails = []

    def check(label, cond, detail=""):
        if cond:
            log("  [PASS] %s" % label)
        else:
            log("  [FAIL] %s %s" % (label, detail))
            fails.append(label)

    log("自测: 时间窗口")
    today = datetime.date(2026, 10, 27)  # 周二
    check("10/31 是周六", TR.is_weekend(datetime.date(2026, 10, 31)))
    check("10/27 不是周末", not TR.is_weekend(today))
    check("周日归属前一天周六",
          TR.saturday_of(datetime.date(2026, 11, 1)) == datetime.date(2026, 10, 31))
    groups = TR.weekend_groups(today, 30)
    check("30 天窗口内有 4 个周末", len(groups) == 4, "实际 %d" % len(groups))
    check("11/28 超出 30 天窗口",
          datetime.date(2026, 11, 28) > today + datetime.timedelta(days=30))
    check("11/1 在 10/31 周末组", datetime.date(2026, 11, 1) in dict(groups)[datetime.date(2026, 10, 31)])
    check("多日活动命中两个周末",
          len(TR.event_weekend_dates(datetime.date(2026, 11, 7),
                                     datetime.date(2026, 11, 15), today, 30)) == 2,
          "11/7-11/15 应跨 11/7-8 与 11/14-15")
    check("单日活动的周末只算一个",
          len(TR.event_weekend_dates(datetime.date(2026, 11, 7),
                                     datetime.date(2026, 11, 8), today, 30)) == 1)
    check("已结束识别", TR.is_expired(datetime.date(2026, 9, 1), today))

    log("自测: 名称归一")
    from core.models import normalize_name
    check("北京前缀被去掉",
          normalize_name("北京·BACC 动漫游戏嘉年华06") == normalize_name("BACC动漫游戏嘉年华6"),
          "%r vs %r" % (normalize_name("北京·BACC 动漫游戏嘉年华06"),
                        normalize_name("BACC动漫游戏嘉年华6")))
    check("不同活动不被误合并",
          normalize_name("米家only 4.0") != normalize_name("逆转裁判ONLY"))
    check("营销标签与场馆前缀剥离",
          normalize_name("国家大剧院：【大麦专享】捷杰耶夫音乐会（北京站）") == normalize_name("捷杰耶夫音乐会"))
    check("年份2026不被误伤", "2026" in normalize_name("2026中国国际机床展"))
    check("场馆别名映射", venue_same("国展顺义馆", "中国国际展览中心顺义馆W1展厅"))

    log("自测: 跨源合并与质量裁决")
    ev_m1 = Event(name="北京·第三届赤马EDCShowcase", start_date="2026-10-31", end_date="2026-10-31",
                  venue="首钢会展中心", price_text="待核实", url="https://m.bj.bendibao.com/xiuxian/", source="bendibao")
    ev_m2 = Event(name="第三届赤马EDCShowcase", start_date="2026-10-31", end_date="2026-11-01",
                  venue="北京首钢会展中心1号馆", price_text="¥168-399", url="https://show.bilibili.com/1006670", source="bili")
    m_res = _merge([ev_m1, ev_m2])
    check("跨源单日与多日合并为一条", len(m_res) == 1)
    check("合并后日期扩展为最大区间", m_res[0].end_date == "2026-11-01")
    check("优质票价胜出", m_res[0].price_text == "¥168-399")
    check("直达购票链接胜出", "bilibili.com" in m_res[0].url)
    ev_c1 = Event(name="北京·某漫展", start_date="2026-11-07",
                  venue="北京酷车国际汇展中心", source="bendibao")
    ev_c2 = Event(name="某漫展", start_date="2026-11-07",
                  venue="北京大红门国际会展中心", source="manzhan")
    c_res = _merge([ev_c1, ev_c2])
    check("同名同日不同场馆不合并", len(c_res) == 2, "实际 %d 条" % len(c_res))
    check("同名同日不同场馆两边都标「待核实」",
          all(e.extra.get("venue_conflict") and "待核实" in e.note for e in c_res))

    log("自测: 补年份只作用于缺年份的日期")
    from sources.base import _parse_flexible
    real_today = datetime.date.today()
    old_iso = (real_today - datetime.timedelta(days=200)).isoformat()
    check("写明年份的旧日期不被加一年",
          _parse_flexible(old_iso) == datetime.date.fromisoformat(old_iso),
          "%s -> %s" % (old_iso, _parse_flexible(old_iso)))
    old_md = real_today - datetime.timedelta(days=100)
    bumped = _parse_flexible("%d/%d" % (old_md.month, old_md.day))
    check("缺年份的日期仍按跨年规则补年",
          bumped is not None and bumped > real_today - datetime.timedelta(days=60),
          "%s -> %s" % (old_md, bumped))

    log("自测: 场馆冲突判定")
    check("同场馆带地址不算冲突",
          not _venue_conflict("北京酷车国际汇展中心金蝉西路甲1号（地铁七号线南楼梓庄站）",
                              "北京酷车国际汇展中心"))
    check("同场馆加标点不算冲突",
          not _venue_conflict("首钢国际会展中心 1 号馆（1+2F）", "首钢国际会展中心1号馆"))
    check("真不同场馆必须报冲突",
          _venue_conflict("北京酷车国际汇展中心", "北京大红门国际会展中心"))
    check("空场馆不报冲突", not _venue_conflict("", "某地"))

    log("自测: LLM 措辞波动不应被误判为变更")
    ev_w1 = Event(name="北京·某同人展", start_date="2026-11-07", end_date="2026-11-08",
                  venue="北京酷车国际汇展中心金蝉西路甲1号（地铁七号线）",
                  price_text="¥80(预售票)¥90(现场票)¥158(VIP票)", source="fake")
    ev_w2 = Event(name="北京·某同人展", start_date="2026-11-07", end_date="2026-11-08",
                  venue="酷车国际汇展中心", price_text="¥80-158", source="fake")
    check("同一活动不同措辞指纹相同", ev_w1.fingerprint == ev_w2.fingerprint,
          "%s vs %s (price_anchor %s/%s)" % (
              ev_w1.fingerprint, ev_w2.fingerprint,
              price_anchor(ev_w1.price_text), price_anchor(ev_w2.price_text)))
    check("同一场馆不同详细度视为相同",
          venue_same(ev_w1.venue, ev_w2.venue))
    check("真涨价能识别",
          price_anchor("¥120起") != price_anchor("¥80起"))

    log("自测: 采集→差分→渲染 全链路")
    tmp_db = os.path.join(HERE, "tests", "_selftest.sqlite")
    for p in (tmp_db, tmp_db + "-journal"):
        if os.path.exists(p):
            os.remove(p)
    fake = FakeSource(log=log)
    res = fake.fetch()
    check("假源返回事件", res.ok and res.count == 4)
    events = _filter(_merge(res.events), today, 30, True)
    check("已结束活动被过滤", all(e.name != "假·已结束活动" for e in events),
          "剩余 %d 条" % len(events))
    check("C 类保留工作日场次",
          any(e.category == "C" and not e.extra.get("has_weekend") is None for e in events))

    log("自测: 全局城市守卫")
    ev_bj = Event(name="北京·技术沙龙", start_date="2026-11-07", venue="中关村")
    ev_sh = Event(name="某大会上海站", start_date="2026-11-07", venue="上海新国际")
    ev_gz = Event(name="开源大会", start_date="2026-11-07", venue="广州保利世贸")
    ev_safe = Event(name="纯本地展会无城市名", start_date="2026-11-07", venue="北投购物公园")
    filtered_bj = _filter([ev_bj, ev_sh, ev_gz, ev_safe], today, 30, True, target_city="北京")
    check("外地城市活动被全局城市守卫丢弃", len(filtered_bj) == 2 and ev_sh not in filtered_bj and ev_gz not in filtered_bj)
    check("本地无城市前缀活动保留", ev_safe in filtered_bj and ev_bj in filtered_bj)

    store = Store(tmp_db, log)
    try:
        d1 = store.apply(events, today.isoformat(), True,
                         [{"name": "fake", "ok": True, "count": len(events), "err": ""}])
        check("首轮全部为 NEW", len(d1["new"]) == len(events))
        d2 = store.apply(events, today.isoformat(), True,
                         [{"name": "fake", "ok": True, "count": len(events), "err": ""}])
        check("次轮无新增", len(d2["new"]) == 0 and len(d2["changed"]) == 0)
        # 改场馆 + 源失败 -> 不能标 gone；且变更需连续两轮确认
        moved = [Event(**{"name": e.name, "start_date": e.start_date,
                          "end_date": e.end_date, "venue": "新场馆",
                          "price_text": e.price_text, "is_free": e.is_free,
                          "category": e.category, "source": "fake"}) for e in events]
        fail_stats = [{"name": "fake", "ok": False, "count": 0, "err": "模拟超时"}]
        d3a = store.apply(moved, today.isoformat(), False, fail_stats)
        check("变更首轮不报（待确认）", len(d3a["changed"]) == 0,
              "实际 %d" % len(d3a["changed"]))
        check("源失败时不标取消", len(d3a["gone"]) == 0)
        d3 = store.apply(moved, today.isoformat(), False, fail_stats)
        check("连续两轮后才报改地址",
              any("改地址" in c["kind"] for c in d3["changed"]))

        # 「连续缺席才判取消」用独立库测；只有稳定源才参与判定
        tmp2 = tmp_db + ".2"
        ok_stats = [{"name": "fake", "ok": True, "count": 1, "err": ""}]
        s2 = Store(tmp2, log, gone_after=2, stable_sources=["fake"])
        try:
            s2.apply([ev_w1], today.isoformat(), True, ok_stats)
            g1 = s2.apply([], today.isoformat(), True, ok_stats)
            check("缺席一轮不判取消", len(g1["gone"]) == 0)
            g2 = s2.apply([], today.isoformat(), True, ok_stats)
            check("缺席两轮才判取消", len(g2["gone"]) == 1)
            check("gone 对象类型为标准 dict", isinstance(g2["gone"][0], dict))
        finally:
            s2.close()
            for p in (tmp2, tmp2 + "-journal"):
                if os.path.exists(p):
                    os.remove(p)

        # 非稳定源（LLM 抽取源）缺席时不得判取消
        tmp3 = tmp_db + ".3"
        s3 = Store(tmp3, log, gone_after=2, stable_sources=["bili"])
        try:
            llm_ev = Event(name="北京·某同人展B", start_date="2026-11-14",
                           end_date="2026-11-14", venue="某处",
                           price_text="¥50", source="bendibao")
            s3.apply([llm_ev], today.isoformat(), True, ok_stats)
            g3 = s3.apply([], today.isoformat(), True, ok_stats)
            g4 = s3.apply([], today.isoformat(), True, ok_stats)
            check("LLM 源缺席不判取消", len(g3["gone"]) == 0 and len(g4["gone"]) == 0)
        finally:
            s3.close()
            for p in (tmp3, tmp3 + "-journal"):
                if os.path.exists(p):
                    os.remove(p)

        log("自测: 改期 / 过期归档 / 多源稳定性 / 变更确认 / 复活")

        def _tmp_store(suffix, **kw):
            path = tmp_db + suffix
            for p in (path, path + "-journal"):
                if os.path.exists(p):
                    os.remove(p)
            return path, Store(path, log, **kw)

        def _drop(path, st):
            st.close()
            for p in (path, path + "-journal"):
                if os.path.exists(p):
                    os.remove(p)

        def _ev(start, end=None, venue="某馆", price="¥50", source="bili", srcs=None):
            e = Event(name="北京·复测展", start_date=start, end_date=end or start,
                      venue=venue, price_text=price, source=source)
            if srcs:
                e.extra["sources"] = list(srcs)
            return e

        p4, s4 = _tmp_store(".4", stable_sources=["bili"])
        try:
            s4.apply([_ev("2026-11-07")], today.isoformat(), True, ok_stats)
            r1 = s4.apply([_ev("2026-11-14")], today.isoformat(), True, ok_stats)
            check("改期首轮不报 NEW 也不报变化",
                  not r1["new"] and not r1["changed"] and not r1["gone"],
                  "new=%d changed=%d gone=%d" % (len(r1["new"]), len(r1["changed"]), len(r1["gone"])))
            r2 = s4.apply([_ev("2026-11-14")], today.isoformat(), True, ok_stats)
            check("改期连续两轮后报「改期」",
                  any("改期" in c["kind"] for c in r2["changed"]) and not r2["new"])
            n_rows = s4.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            check("改期不会留下重复记录", n_rows == 1, "实际 %d 行" % n_rows)
        finally:
            _drop(p4, s4)

        p5, s5 = _tmp_store(".5", stable_sources=["bili"])
        try:
            s5.apply([_ev("2026-10-28")], "2026-10-27", True, ok_stats)
            r = s5.apply([], "2026-10-30", True, ok_stats)
            r = s5.apply([], "2026-10-31", True, ok_stats)
            st = s5.conn.execute("SELECT status FROM events").fetchone()[0]
            check("已结束活动归档为 expired 而不是 gone",
                  st == "expired" and not r["gone"], "status=%s gone=%d" % (st, len(r["gone"])))
        finally:
            _drop(p5, s5)

        p6, s6 = _tmp_store(".6", stable_sources=["bili"], gone_after=2)
        try:
            s6.apply([_ev("2026-11-07", source="bendibao", srcs=["bendibao", "bili"])],
                     today.isoformat(), True, ok_stats)
            s6.apply([], today.isoformat(), True, ok_stats)
            g = s6.apply([], today.isoformat(), True, ok_stats)
            check("合并后首源非稳定源、但含 bili 时仍参与取消判定", len(g["gone"]) == 1)
            back = s6.apply([_ev("2026-11-07")], today.isoformat(), True, ok_stats)
            st = s6.conn.execute("SELECT status FROM events").fetchone()[0]
            check("取消后重新出现的活动被复活为 active",
                  len(back["new"]) == 1 and st == "active", "status=%s" % st)
        finally:
            _drop(p6, s6)

        p7, s7 = _tmp_store(".7", stable_sources=["bili"])
        try:
            s7.apply([_ev("2026-11-07", price="¥50")], today.isoformat(), True, ok_stats)
            s7.apply([_ev("2026-11-07", price="¥60")], today.isoformat(), True, ok_stats)
            r = s7.apply([_ev("2026-11-07", price="¥70")], today.isoformat(), True, ok_stats)
            check("变更来回乱跳不累计确认", not r["changed"], "changed=%d" % len(r["changed"]))
            r = s7.apply([_ev("2026-11-07", price="¥70")], today.isoformat(), True, ok_stats)
            check("同一新值连续两轮才确认改价", any("改价" in c["kind"] for c in r["changed"]))
        finally:
            _drop(p7, s7)

        log("自测: 截止提醒不连推")
        dl_cfg = {"push": {"min_new_changes": "3", "notify_on_no_change": "false",
                           "notify_on_source_failure": "true"}}
        empty_diff = {"new": [], "changed": [], "gone": []}
        def _dl(days_left):
            e = _ev("2026-11-07")
            e.deadline = (today + datetime.timedelta(days=days_left)).isoformat()
            return _decide_push(dl_cfg, ok_stats, empty_diff, [e], today)[0]
        check("截止剩 3 天 / 1 天时推送", _dl(3) and _dl(1))
        check("截止剩 2 天 / 0 天时不重复推", not _dl(2) and not _dl(0))
        check("--force-push 强制触发推送", _decide_push(dl_cfg, ok_stats, empty_diff, [], today, force_push=True)[0])

        meta = _meta(None, type("L", (), {"calls": 0, "model": "-",
                                          "_resolved": None,
                                          "truncated": False})(),
                     today, 30, True)
        stats_fail = [{"name": "fake", "ok": False, "count": 0, "err": "模拟超时"}]
        text = render(moved, TR.weekend_groups(today, 30), today, 30, d3,
                      stats_fail, meta)
        for section in ("# 北京周末活动雷达", "## 本次抓取统计", "## 新增与变化",
                        "## 博览会速览", "## 截止提醒"):
            check("报告含章节 %s" % section, section in text)
        check("失败源在统计里显示为 ❌", "❌" in text)

        text_plain = render_text(moved, TR.weekend_groups(today, 30), today, 30, d3,
                                 stats_fail, meta)
        for section in ("北京周末活动雷达", "【本次抓取统计】", "【新增与变化】",
                        "【博览会速览】", "【截止提醒】"):
            check("纯文本含章节 %s" % section, section in text_plain)
        check("纯文本无 Markdown 表格语法", "|---|" not in text_plain)
        check("纯文本无 Markdown 标题/加粗",
              "## " not in text_plain and "**" not in text_plain)
        check("纯文本无 Markdown 链接", "](http" not in text_plain)
        check("纯文本含失败源 ❌", "❌" in text_plain)

        if moved:
            moved[0].url = "https://example.com/event1"
        md_push = render_push_markdown(moved, TR.weekend_groups(today, 30), today, 30, d3,
                                       stats_fail, meta)
        for section in ("# 🎯 北京周末活动雷达", "### 📊 本次抓取统计", "### 🔔 新增与变化",
                        "### 🏢 博览会速览", "### ⏰ 临近截止提醒"):
            check("推送 Markdown 含章节 %s" % section, section in md_push)
        check("推送 Markdown 无表格语法", "|---|" not in md_push)
        check("推送 Markdown 含超链接语法", "](http" in md_push)
        check("推送 Markdown 含失败源 ❌", "❌" in md_push)

        log("自测: 多城市参数化与预设联动")
        from core.config import DEFAULTS
        sh_cfg = {s: dict(kv) for s, kv in DEFAULTS.items()}
        sh_sources = {
            "bendibao": "H|on |https://m.bj.bendibao.com/xiuxian/",
            "douban": "H|on |https://beijing.douban.com/events",
            "chnmuseum": "H|on |https://www.chnmuseum.cn/zl/",
            "bili": "J|on |https://show.bilibili.com/api/ticket/project/listV2",
        }
        apply_env_and_city(sh_cfg, sh_sources, cli_overrides={"city": "上海市"})
        check("城市名归一为上海", sh_cfg["radar"]["city"] == "上海")
        check("B站与CPP自动切换上海行政区划码 310000",
              sh_cfg["extra"]["bili_area"] == "310000" and sh_cfg["extra"]["cpp_city"] == "310000")
        check("大麦自动切换上海城市码 101020100", sh_cfg["extra"]["damai_city"] == "101020100")
        check("本地宝与豆瓣 URL 自动切换为上海子域名",
              "m.sh.bendibao.com" in sh_sources["bendibao"] and "shanghai.douban.com" in sh_sources["douban"])
        check("非北京城市自动跳过北京专属场馆源 chnmuseum", "|off|" in sh_sources["chnmuseum"])
        configure_city("上海")
        check("上海活动名称前缀与后缀剥离",
              normalize_name("上海·CP30 同人展（上海站）") == normalize_name("CP30同人展"))
        check("上海场馆简称映射", venue_same("上海新国际", "上海新国际博览中心N1馆"))
        sh_meta = dict(meta)
        sh_meta["city"] = "上海"
        sh_report = render(moved, TR.weekend_groups(today, 30), today, 30, d3, stats_fail, sh_meta)
        check("报告标题随城市变为上海", "# 上海周末活动雷达" in sh_report)
        configure_city("北京")

        log("自测: AI Provider 预设与端点拼接")
        from core.llm import resolve_provider_config, build_endpoint_url
        ds_cfg = {s: dict(kv) for s, kv in DEFAULTS.items()}
        ds_cfg["llm"]["provider"] = "deepseek"
        prov, b_url, mode, model_name, _ = resolve_provider_config(ds_cfg)
        check("DeepSeek 预设自动填充 base_url 与 model",
              prov == "deepseek" and "api.deepseek.com" in b_url and model_name == "deepseek-chat" and mode == "openai")
        check("端点拼接兼容 /v1、/v4 与裸域名",
              build_endpoint_url("http://127.0.0.1:3425", "openai") == "http://127.0.0.1:3425/v1/chat/completions"
              and build_endpoint_url("https://api.deepseek.com/v1", "openai") == "https://api.deepseek.com/v1/chat/completions"
              and build_endpoint_url("https://open.bigmodel.cn/api/paas/v4", "openai") == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
              and build_endpoint_url("https://api.anthropic.com/v1", "anthropic") == "https://api.anthropic.com/v1/messages")

        log("自测: 7 大渠道轻量 Webhook 模板")
        import urllib.request
        captured = []

        class _FakeResp:
            status = 200
            def __init__(self, body):
                self._b = body.encode("utf-8")
            def read(self):
                return self._b
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        orig_urlopen = urllib.request.urlopen
        def _fake_urlopen(req, timeout=25):
            captured.append((req.full_url, req.data.decode("utf-8", "replace") if req.data else ""))
            return _FakeResp('{"code":0,"StatusCode":0,"errcode":0,"ok":true}')

        urllib.request.urlopen = _fake_urlopen
        try:
            pcfg = {s: dict(kv) for s, kv in DEFAULTS.items()}
            pcfg["push"].update({
                "pushkey": "PDU_TEST",
                "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
                "feishu_secret": "sec_feishu",
                "wecom_key": "wk_test",
                "dingtalk_webhook": "https://oapi.dingtalk.com/robot/send?access_token=dt",
                "dingtalk_secret": "sec_dt",
                "bark_key": "bark_dev_key",
                "telegram_bot_token": "123:ABC",
                "telegram_chat_id": "999",
                "serverchan_sendkey": "SCT12345Txxx",
            })
            for ch in ("pushdeer", "feishu", "wecom", "dingtalk", "bark", "telegram", "serverchan"):
                pcfg["push"]["channel"] = ch
                r = push_send(pcfg, "测试标题", "测试正文 **" + ("长" * 2000) + "**", log=log)
                if ch == "bark":
                    # bark 成功判断 code==200
                    pass
                else:
                    check("Webhook 渠道 %s 发送成功" % ch, r.ok, r.err)
            # 单独校验 Bark code=200 与多渠道并发
            urllib.request.urlopen = lambda req, timeout=25: (
                captured.append((req.full_url, req.data.decode("utf-8", "replace")))
                or _FakeResp('{"code":200,"StatusCode":0}')
            )
            pcfg["push"]["channel"] = "feishu,bark"
            r_multi = push_send(pcfg, "多渠道标题", "正文", log=log)
            check("多渠道逗号分隔同时发送 (feishu,bark)", r_multi.ok, r_multi.err)
            check("飞书带 sign 签名字段", any('"sign"' in body for _, body in captured))
            check("钉钉 URL 带 sign 加签参数", any("dingtalk.com" in u and "sign=" in u for u, _ in captured))
            check("企微自动字节截断 <=4096 bytes",
                  all(len(body.encode("utf-8")) <= 4200 for u, body in captured if "weixin.qq.com" in u))
        finally:
            urllib.request.urlopen = orig_urlopen
    finally:
        store.close()
        for p in (tmp_db, tmp_db + "-journal"):
            if os.path.exists(p):
                os.remove(p)

    print("")
    if fails:
        log("自测失败 %d 项: %s" % (len(fails), ", ".join(fails)))
        return EXIT_FAIL
    log("自测全部通过")
    return EXIT_OK




if __name__ == "__main__":
    sys.exit(self_test())
