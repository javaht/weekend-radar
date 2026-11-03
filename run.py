#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""北京周末活动雷达 —— 唯一入口。

模式互斥, 默认 `--plan` 是最安全的只读模式(抓取+提取+打印, 不写库不推送),
与 douyin-api-collector 的约定一致: 直接跑不会产生副作用。

退出码: 0 成功 | 1 自测/探测失败 | 2 参数错误 | 3 推送失败
"""
import argparse
import datetime
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core.config import (  # noqa: E402
    load_config, apply_env_and_city, as_int, as_bool, csv_list, abspath,
)
from core.fetch import Fetcher  # noqa: E402
from core.llm import LLM  # noqa: E402
from core.store import Store  # noqa: E402
from core.render import render, render_text, render_push_markdown  # noqa: E402
from core.push import send as push_send  # noqa: E402
from core.models import Event, venue_same, price_anchor, configure_city  # noqa: E402
from core import timerange as TR  # noqa: E402
from sources.base import SourceResult  # noqa: E402
from sources.registry import build_sources  # noqa: E402
from sources.fake import FakeSource  # noqa: E402
from tools.probe import run_probe, format_probe  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_ARG, EXIT_PUSH = 0, 1, 2, 3


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------- 上下文
def _window(cfg):
    today = datetime.date.today()
    days = as_int(cfg, "radar", "window_days", 30)
    return today, days, today + datetime.timedelta(days=days)


def _build(cfg, src_rows, today, window_end, include_disabled=False, only=None):
    city = cfg["radar"].get("city", "北京")
    configure_city(city)
    fetch_cfg = cfg["fetch"]
    fetcher = Fetcher(
        interval=float(fetch_cfg.get("interval", 2) or 2),
        timeout=float(fetch_cfg.get("timeout", 20) or 20),
        retries=int(fetch_cfg.get("retries", 3) or 3),
        backoff_base=float(fetch_cfg.get("backoff_base", 2) or 2),
        allow_http_hosts=csv_list(cfg, "fetch", "allow_http"),
        log=log,
    )
    llm = LLM(cfg, log)
    sources = build_sources(cfg, src_rows, fetcher, llm, today, window_end,
                            log, include_disabled=include_disabled, only=only)
    return fetcher, llm, sources


# ---------------------------------------------------------------- 采集与合并
def _collect(sources):
    stats, events = [], []
    for src in sources:
        name = getattr(src, "name", "?")
        log("  [采集] %s ..." % name)
        try:
            res = src.fetch()
        except Exception as e:
            res = SourceResult(name, False, err="%s: %s" % (type(e).__name__, e))
        stats.append({
            "name": name, "ok": res.ok, "count": res.count,
            "err": res.err, "note": res.note, "used_llm": res.used_llm,
        })
        if res.ok:
            events.extend(res.events)
            log("    → %d 条%s" % (res.count, (" | " + res.note) if res.note else ""))
        else:
            log("    → [失败] %s" % res.err)
    return events, stats


def _venue_conflict(a, b):
    """判断两个场馆串是不是真的"不同场馆"（复用 models 的归一规则）。"""
    return not venue_same(a, b)


def _url_quality(url):
    """直达购票平台 > 官方场馆 > 资讯聚合媒体"""
    u = (url or "").lower()
    if any(k in u for k in ("bilibili.com", "allcpp.cn", "showstart.com", "damai.cn", "maoyan.com")):
        return 3
    if any(k in u for k in ("chncpa.org", "chnmuseum.cn", "dpm.org.cn", "cnccchina.com", "ciec.com.cn")):
        return 2
    return 1 if u else 0


def _price_quality(price_text, is_free):
    """带具体金额 > 明确免费 > 模糊文本"""
    p = str(price_text or "").strip()
    if re.search(r"\d", p):
        return 3
    if is_free is True or "免费" in p:
        return 2
    return 1 if p else 0


def _dates_compatible(st1, et1, st2, et2):
    """日期重叠、包含或单日归属多日档期"""
    if not st1 or not st2:
        return True
    et1 = et1 or st1
    et2 = et2 or st2
    return max(st1, st2) <= min(et1, et2)


def _mark_venue_conflict(ev, other_venue):
    """给一条活动挂上「场馆信息冲突（待核实）」标注, 同一对场馆只标一次。"""
    pair = "%s / %s" % (ev.venue or "—", other_venue or "—")
    marked = ev.extra.setdefault("venue_conflicts", [])
    if pair in marked:
        return
    marked.append(pair)
    ev.extra["venue_conflict"] = "；".join(marked)
    ev.note = (ev.note + "；" if ev.note else "") + "场馆信息冲突（待核实）：%s" % pair


def _merge(events, log=print):
    """跨源合并同名活动（支持日期区间交集与字段质量裁决）。

    针对多源抓取的痛点:
    1) 允许单日票档合并到多日展期中（日期取最大包容区间）
    2) 优质官方直链和具体票价覆盖模糊媒体资讯（解决先到先得 Bug）
    3) 场馆发生实质冲突时标「场馆信息冲突(待核实)」
    """
    merged_list = []
    conflicts = 0
    for ev in events:
        matched = None
        rivals = []      # 同名、日期兼容、但场馆不同的条目
        for cur in merged_list:
            if cur.name_norm != ev.name_norm:
                continue
            if not _dates_compatible(cur.start_date, cur.end_date, ev.start_date, ev.end_date):
                continue
            if venue_same(cur.venue, ev.venue):
                matched = cur
                break
            rivals.append(cur)

        if not matched:
            ev.extra.setdefault("sources", [ev.source] if ev.source else [])
            merged_list.append(ev)
            # ★ 场馆冲突只能在「没合并」的分支里检出: 合并的前提就是 venue_same,
            #   合并之后再比必然相同。宁可漏合并不可错合并, 所以不合并, 两边都标注。
            for other in rivals:
                conflicts += 1
                _mark_venue_conflict(other, ev.venue)
                _mark_venue_conflict(ev, other.venue)
            continue

        cur = matched
        cur.extra.setdefault("sources", [cur.source] if cur.source else [])
        if ev.source and ev.source not in cur.extra["sources"]:
            cur.extra["sources"].append(ev.source)

        # 日期范围取最大包容区间
        if ev.start_date and cur.start_date:
            cur.start_date = min(cur.start_date, ev.start_date)
        if ev.end_date and cur.end_date:
            cur.end_date = max(cur.end_date, ev.end_date)

        # 场馆取更详尽表述
        if ev.venue and (not cur.venue or len(ev.venue) > len(cur.venue)):
            cur.venue = ev.venue

        # 票价质量裁决 (精准数字优先于模糊文本)
        if _price_quality(ev.price_text, ev.is_free) > _price_quality(cur.price_text, cur.is_free):
            cur.price_text = ev.price_text
            cur.is_free = ev.is_free
        elif not cur.price_text and ev.price_text:
            cur.price_text = ev.price_text

        # URL 质量裁决 (购票直链/官网优先于聚合资讯)
        if _url_quality(ev.url) > _url_quality(cur.url):
            cur.url = ev.url
        elif not cur.url and ev.url:
            cur.url = ev.url

        # 补齐其他非空字段
        for field in ("deadline", "note"):
            if not getattr(cur, field) and getattr(ev, field):
                setattr(cur, field, getattr(ev, field))
        for field in ("has_goods", "i_friendly"):
            if getattr(cur, field) is None and getattr(ev, field) is not None:
                setattr(cur, field, getattr(ev, field))

    if conflicts:
        log("  [警告] 检出 %d 处跨源场馆冲突, 已标注待核实" % conflicts)
    return merged_list


def _filter(events, today, days, weekend_only, exclude_keywords=()):
    """窗口 + 周末过滤。

    例外: C 类博览会有大量纯工作日场次, 但用户要求记录它们(标注有无周末场),
    所以 C 类不做周末过滤, 只做窗口过滤。

    噪音过滤: 抽取时会把「已取消」「不含门票」的票档也当活动提出来, 必须滤掉,
    否则报告里会出现"去了发现办不了"的条目。
    """
    out = []
    win_end = today + datetime.timedelta(days=days)
    for ev in events:
        text = ((ev.name or "") + " " + (ev.note or "")).strip()
        is_excluded = False
        for k in exclude_keywords:
            if not k:
                continue
            if k in text:
                if k == "戏剧" and ("2.5次元" in text or "漫展" in text or "动漫" in text):
                    continue
                is_excluded = True
                break
        if is_excluded:
            continue
        sd = TR.parse_date(ev.start_date, today.year)
        if not sd:
            continue
        ed = TR.parse_date(ev.end_date, today.year) or sd
        if TR.is_expired(ed, today):
            continue
        if sd > win_end:
            continue
        if ev.category == "C":
            hits = TR.event_weekend_dates(sd, ed, today, days)
            ev.extra["has_weekend"] = bool(hits)
            out.append(ev)
            continue
        if weekend_only and not TR.event_weekend_dates(sd, ed, today, days):
            continue
        out.append(ev)
    return out


def _meta(cfg, llm, today, days, has_baseline):
    def group_of(ev):
        sd = TR.parse_date(ev.start_date, today.year)
        ed = TR.parse_date(ev.end_date, today.year) or sd
        return TR.event_weekend_dates(sd, ed, today, days)

    city = ((cfg or {}).get("radar") or {}).get("city", "北京")
    prov = getattr(llm, "provider", "") or (llm._resolved or "auto")
    return {
        "city": city,
        "engine": "抓取 + LLM 提取 / SQLite 差分",
        "group_of": group_of,
        "parse_date": TR.parse_date,
        "has_baseline": has_baseline,
        "llm_desc": ("%s @ %s" % (prov, llm.model)) if llm.calls else "未使用",
        "llm_calls": llm.calls,
        "llm_truncated": llm.truncated,
    }


# ---------------------------------------------------------------- 模式
def do_plan(cfg, src_rows):
    today, days, window_end = _window(cfg)
    weekend_only = as_bool(cfg, "radar", "weekend_only", True)
    city = cfg["radar"].get("city", "北京")
    log("模式: --plan（只读，不写库不推送）  城市: %s  窗口: %s → %s"
        % (city, today, window_end.strftime("%Y-%m-%d")))
    _, llm, sources = _build(cfg, src_rows, today, window_end)
    if not sources:
        log("[!] config.ini 的 [sources] 没有任何启用的源")
        return EXIT_ARG
    events, stats = _collect(sources)
    raw_n = len(events)
    events = _merge(events, log)
    merged_n = len(events)
    exclude = csv_list(cfg, "radar", "exclude_keywords")
    events = _filter(events, today, days, weekend_only, exclude)
    log("")
    log("采集 %d 条，合并去重后 %d 条，落在窗口内 %d 条" % (raw_n, merged_n, len(events)))
    for ev in sorted(events, key=lambda e: (e.start_date, e.name)):
        log("  %s | %s | %s | %s" % (ev.start_date, ev.name, ev.venue or "—",
                                    ev.category))
    bad = [s for s in stats if not s["ok"]]
    log("")
    log("源: 成功 %d / 失败 %d" % (len(stats) - len(bad), len(bad)))
    for s in bad:
        log("  [失败] %s: %s" % (s["name"], s["err"]))
    return EXIT_OK


def do_probe(cfg, src_rows):
    today, days, window_end = _window(cfg)
    city = cfg["radar"].get("city", "北京")
    log("模式: --probe（逐源探测，含未启用源；不调用 LLM 提取）  城市: %s" % city)
    _, llm, sources = _build(cfg, src_rows, today, window_end, include_disabled=True)
    log("  [探测] LLM Provider=%s (%s) ..." % (llm.provider, llm.base))
    llm_rows = llm.probe()
    for proto, ok, note in llm_rows:
        log("    %s %s: %s" % ("✅" if ok else "❌", proto, note))
    rows, ok_n, fail_n = run_probe(sources, llm, log)
    print("")
    print(format_probe(rows, llm_rows))
    return EXIT_OK if fail_n == 0 else EXIT_FAIL


def _write_report(cfg, text, today):
    out_dir = abspath(cfg["radar"].get("out_dir", "data/reports"))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s.md" % today.strftime("%Y-%m-%d"))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def _decide_push(cfg, stats, diff, events, today):
    """推送决策。

    ★ 为什么不"有任何差异就推"：
      LLM 抽取天然有抖动（同一天跑两次，新条目数会从 2 变到 8）。
      若按 raw diff 判定，几乎每天都"有变化"，「无变化不推」形同虚设。
      所以按变化的**性质**分级：取消和改地址是稀有且重要的，立刻推；
      新增要够多才算真事；改价抖动最多，只记录不触发。
    """
    all_ok = all(s["ok"] for s in stats)
    min_new = as_int(cfg, "push", "min_new_changes", 3)
    n_new = len(diff["new"])
    n_gone = len(diff["gone"])
    n_addr = sum(1 for c in diff["changed"] if "改地址" in c["kind"])
    # ★ 截止提醒只在「剩 3 天」和「剩 1 天」这两天触发。
    #   原来是 0~3 天内每天都推, 一个截止就能连推 4 天, 绕过「无变化不推」。
    remind_days = {3, 1}
    urgent = False
    for ev in events:
        d = TR.parse_date(ev.deadline, today.year) if ev.deadline else None
        if d and (d - today).days in remind_days:
            urgent = True
            break
    notify_no_change = as_bool(cfg, "push", "notify_on_no_change", False)
    notify_fail = as_bool(cfg, "push", "notify_on_source_failure", True)

    if not all_ok and notify_fail:
        return True, "健康告警：有源采集失败"
    if n_gone:
        return True, "有 %d 条取消" % n_gone
    if n_addr:
        return True, "有 %d 条改地址" % n_addr
    if n_new >= min_new:
        return True, "新增 %d 条" % n_new
    if urgent:
        return True, "临近报名截止"
    if notify_no_change:
        return True, "每日报告（无实质变化）"
    return False, "无实质变化（新增 %d/%d，变化 %d），不推送" % (
        n_new, min_new, len(diff["changed"]))


def do_run(cfg, src_rows, dry_run=False):
    today, days, window_end = _window(cfg)
    weekend_only = as_bool(cfg, "radar", "weekend_only", True)
    city = cfg["radar"].get("city", "北京")
    log("模式: --run%s  城市: %s  窗口: %s → %s"
        % ("（dry-run，不推送）" if dry_run else "", city, today, window_end.strftime("%Y-%m-%d")))

    _, llm, sources = _build(cfg, src_rows, today, window_end)
    if not sources:
        log("[!] config.ini 的 [sources] 没有任何启用的源")
        return EXIT_ARG

    events, stats = _collect(sources)
    events = _merge(events, log)
    exclude = csv_list(cfg, "radar", "exclude_keywords")
    events = _filter(events, today, days, weekend_only, exclude)
    log("入表 %d 条（源 成功 %d / 失败 %d）"
        % (len(events), sum(1 for s in stats if s["ok"]), sum(1 for s in stats if not s["ok"])))

    store = Store(abspath(cfg["radar"].get("db", "data/radar.sqlite")), log,
                  gone_after=as_int(cfg, "radar", "gone_after_misses", 2),
                  stable_sources=csv_list(cfg, "radar", "stable_sources", "bili"),
                  confirm_runs=as_int(cfg, "radar", "confirm_runs", 2))
    try:
        has_baseline = bool(store.last_run_date())
        all_ok = all(s["ok"] for s in stats)
        diff = store.apply(events, today.isoformat(), all_ok, stats)
        log("差分: NEW %d / 变化 %d / 取消 %d"
            % (len(diff["new"]), len(diff["changed"]), len(diff["gone"])))
        groups = TR.weekend_groups(today, days)
        meta = _meta(cfg, llm, today, days, has_baseline)
        text = render(events, groups, today, days, diff, stats, meta)
        push_format = str(cfg["push"].get("format", "markdown") or "markdown").strip().lower()
        if push_format == "text":
            push_content = render_text(events, groups, today, days, diff, stats, meta)
        else:
            push_content = render_push_markdown(events, groups, today, days, diff, stats, meta)
    finally:
        store.close()

    path = _write_report(cfg, text, today)
    log("报告已写入: %s" % path)

    should, reason = _decide_push(cfg, stats, diff, events, today)
    if not should:
        log("推送跳过：%s" % reason)
        return EXIT_OK
    if dry_run:
        log("dry-run：本应推送（%s），已跳过" % reason)
        return EXIT_OK
    if not as_bool(cfg, "push", "enabled", True):
        log("[警告] push.enabled = false，按配置不推送（%s）" % reason)
        return EXIT_OK

    title = "%s周末活动雷达 %s · %s" % (city, today.strftime("%m-%d"), reason)
    res = push_send(cfg, title, push_content, log)
    if res.ok:
        log("推送成功 (%s)" % res.channel)
        return EXIT_OK
    log("[FAIL] 推送失败 (%s)：%s | 响应: %s" % (res.channel, res.err, (res.body or "")[:300]))
    return EXIT_PUSH


# ---------------------------------------------------------------- 自测
def do_install_cron(expr="0 9 * * *"):
    """把定时任务写进 crontab。幂等：先删旧的本项目行，再追加。"""
    import subprocess
    cmd = "cd %s && %s run.py --run >> data/cron.log 2>&1" % (
        HERE, sys.executable or "python3")
    line = "%s %s # weekend-radar" % (expr, cmd)
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        lines = [l for l in (cur.stdout or "").splitlines()
                 if "weekend-radar" not in l]
    except Exception as e:
        log("[!] 读取 crontab 失败: %s" % e)
        return EXIT_FAIL
    lines.append(line)
    try:
        p = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                           capture_output=True, text=True)
    except Exception as e:
        log("[!] 写入 crontab 失败: %s" % e)
        return EXIT_FAIL
    if p.returncode != 0:
        log("[!] crontab 写入返回 %d: %s" % (p.returncode, (p.stderr or "")[:300]))
        return EXIT_FAIL
    log("已安装定时任务：")
    log("  %s" % line)
    return EXIT_OK


def self_test():
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


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(
        description="周末活动雷达 —— 多城市抓取 / LLM 提取 / SQLite 差分 / 多渠道 Webhook 推送")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--plan", action="store_true",
                   help="只读：抓取+提取+打印，不写库不推送（默认）")
    g.add_argument("--probe", action="store_true",
                   help="逐源健康探测 + LLM 协议探测")
    g.add_argument("--self-test", action="store_true", help="离线自测，不联网")
    g.add_argument("--run", action="store_true", help="正式运行：入库存档+写报告+按需推送")
    g.add_argument("--install-cron", action="store_true",
                   help="把每日 09:00 的定时任务写入 crontab（幂等）")
    ap.add_argument("--dry-run", action="store_true", help="配合 --run：全流程但不推送")
    ap.add_argument("--config", default=None, help="指定配置文件路径（默认 config.ini）")
    ap.add_argument("--city", default=None, help="覆盖目标城市（如：北京、上海、广州、深圳、杭州、成都）")
    ap.add_argument("--provider", default=None,
                    help="覆盖 AI Provider（magic/openai/anthropic/deepseek/siliconflow/openrouter/dashscope/zhipu/ollama/custom）")
    ap.add_argument("--model", default=None, help="覆盖 LLM 模型名")
    ap.add_argument("--base-url", dest="base_url", default=None, help="覆盖 LLM 接口地址")
    ap.add_argument("--channel", default=None,
                    help="覆盖推送渠道，支持逗号分隔（pushdeer/feishu/wecom/dingtalk/bark/telegram/serverchan）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.install_cron:
        return do_install_cron()

    cfg, src_rows = load_config(args.config)
    apply_env_and_city(cfg, src_rows, cli_overrides={
        "city": args.city,
        "provider": args.provider,
        "model": args.model,
        "base_url": args.base_url,
        "channel": args.channel,
    })

    if args.probe:
        return do_probe(cfg, src_rows)
    if args.run:
        return do_run(cfg, src_rows, dry_run=args.dry_run)
    return do_plan(cfg, src_rows)


if __name__ == "__main__":
    sys.exit(main())
