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
    CITY_PRESETS,
)
from core.fetch import Fetcher  # noqa: E402
from core.llm import LLM  # noqa: E402
from core.store import Store  # noqa: E402
from core.render import render, render_text, render_push_markdown  # noqa: E402
from core.push import send as push_send  # noqa: E402
from core.models import Event, venue_same, price_anchor, configure_city  # noqa: E402
from core.genre import genre_of, parse_disabled  # noqa: E402
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
        for field in ("has_goods",):
            if getattr(cur, field) is None and getattr(ev, field) is not None:
                setattr(cur, field, getattr(ev, field))

    if conflicts:
        log("  [警告] 检出 %d 处跨源场馆冲突, 已标注待核实" % conflicts)
    return merged_list


def _filter(events, today, days, weekend_only, exclude_keywords=(), target_city=None):
    """窗口 + 周末过滤 + 全局城市守卫。

    例外: C 类博览会有大量纯工作日场次, 但用户要求记录它们(标注有无周末场),
    所以 C 类不做周末过滤, 只做窗口过滤。

    噪音过滤: 抽取时会把「已取消」「不含门票」的票档也当活动提出来, 必须滤掉,
    否则报告里会出现"去了发现办不了"的条目。

    城市守卫: 当指定 target_city 时，若活动场馆或名称包含其他主要城市（如上海站/广州场等）
    且不含当前目标城市，作为防守网坚决拦截丢弃。
    """
    out = []
    win_end = today + datetime.timedelta(days=days)
    tc = None
    if target_city:
        tc = str(target_city).strip()
        if len(tc) > 2 and tc.endswith("市"):
            tc = tc[:-1]

    for ev in events:
        if tc:
            where_text = "%s %s %s" % (ev.venue or "", ev.name or "", ev.note or "")
            if tc not in where_text:
                hit_other = False
                for other in CITY_PRESETS:
                    if other == tc:
                        continue
                    if ("%s站" % other in (ev.name or "")) or ("%s场" % other in (ev.name or "")) or (other in (ev.venue or "")):
                        hit_other = True
                        break
                if hit_other:
                    continue

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
    events = _filter(events, today, days, weekend_only, exclude, target_city=city)
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


def _genre_filtered_diff(diff, disabled):
    """把 diff 里用户已关闭的品类剔掉, 只留下该触发推送的部分。"""
    if not diff or not disabled:
        return diff
    def keep(ev):
        return genre_of(ev.name, ev.category) not in disabled
    return {
        "new": [e for e in diff["new"] if keep(e)],
        "changed": [c for c in diff["changed"] if keep(c["event"])],
        "gone": [e for e in diff["gone"] if keep(Event(**e) if isinstance(e, dict) else e)],
        "unchanged": diff["unchanged"],
    }


def _write_report(cfg, text, today):
    out_dir = abspath(cfg["radar"].get("out_dir", "data/reports"))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s.md" % today.strftime("%Y-%m-%d"))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def _decide_push(cfg, stats, diff, events, today, force_push=False):
    """推送决策。

    ★ 为什么不"有任何差异就推"：
      LLM 抽取天然有抖动（同一天跑两次，新条目数会从 2 变到 8）。
      若按 raw diff 判定，几乎每天都"有变化"，「无变化不推」形同虚设。
      所以按变化的**性质**分级：取消和改地址是稀有且重要的，立刻推；
      新增要够多才算真事；改价抖动最多，只记录不触发。
    """
    if force_push:
        return True, "手动强制推送 (--force-push)"

    # ★ 品类开关: 用户在前端勾掉「脱口秀」, 那新增/变化/截止里的脱口秀就不该触发推送。
    #   注意这只影响**推送判定**, 不影响入库 —— 页面上仍然能查到这些活动。
    disabled = parse_disabled(cfg["push"].get("disabled_genres", ""))
    diff = _genre_filtered_diff(diff, disabled) if disabled else diff
    events = [e for e in events if genre_of(e.name, e.category) not in disabled] if disabled else events

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


def do_run(cfg, src_rows, dry_run=False, force_push=False):
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
    events = _filter(events, today, days, weekend_only, exclude, target_city=city)
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

    should, reason = _decide_push(cfg, stats, diff, events, today, force_push=force_push)
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


def do_serve(host="0.0.0.0", port=8000):
    """启动 FastAPI Web 服务控制台。"""
    try:
        import uvicorn
    except ImportError:
        log("[!] 缺少 uvicorn 依赖，请运行: pip install fastapi uvicorn")
        return EXIT_FAIL
    log("启动 Web 服务控制台: http://%s:%d" % (host if host != "0.0.0.0" else "127.0.0.1", port))
    uvicorn.run("server.app:app", host=host, port=port, reload=False)
    return EXIT_OK


def self_test():
    """离线自测: 不联网、不调模型、不需要配置（已抽离到 tests/test_offline.py）。"""
    from tests.test_offline import self_test as _do_self_test
    return _do_self_test(log=log)


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
    g.add_argument("--serve", action="store_true", help="启动 FastAPI Web 服务控制台")
    g.add_argument("--install-cron", action="store_true",
                   help="把每日 09:00 的定时任务写入 crontab（幂等）")
    ap.add_argument("--port", type=int, default=8000, help="配合 --serve：Web 服务端口（默认 8000）")
    ap.add_argument("--host", default="0.0.0.0", help="配合 --serve：绑定主机（默认 0.0.0.0）")
    ap.add_argument("--dry-run", action="store_true", help="配合 --run：全流程但不推送")
    ap.add_argument("--force-push", action="store_true", help="配合 --run：强制触发推送（忽略无实质变化与条数门槛）")
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
    if args.serve:
        return do_serve(host=args.host, port=args.port)

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
        return do_run(cfg, src_rows, dry_run=args.dry_run, force_push=args.force_push)
    return do_plan(cfg, src_rows)


if __name__ == "__main__":
    sys.exit(main())
