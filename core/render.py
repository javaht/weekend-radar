# -*- coding: utf-8 -*-
"""报告渲染 —— 同一批数据, 两个出口。

设计原则:
  1) 按周末分组, 因为周州的决策单位是"这个周末去哪"。
  2) 每份报告必须带「抓取统计」块。没有它, 报告看起来正常但其实是空跑,
     这种静默失效比崩溃更危险 —— 崩溃至少会报错。
  3) 待核实的信息不伪装成已确认: 拿不到票价就写「待核实」, 不留空白。

render()      → Markdown, 落盘到 data/reports/*.md
render_text() → 纯文本, 发给 PushDeer。PushDeer 客户端的 Markdown 渲染器只是
                子集, 表格通常不渲染, 整份 Markdown 发过去在手机上只会看到一堆
                竖线和分隔线, 所以推送单走纯文本。
"""
import datetime

WEEKDAY_CN = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}

# 展期 >= 7 天（如持续一个月的菊花展/特展）单独成节, 不在每个周末重复刷屏。
# 它们确实"这周末也能去", 但重复 4 次只会把真正的当周活动挤掉。
LONG_RUN_DAYS = 7


# ---------------------------------------------------------------- 公共
def _layout(events, groups, today, window_days, meta):
    """把事件切成报告需要的几块。两个渲染器共用, 保证分组口径一致。"""
    longrun = []
    for ev in events:
        # 持续展出只针对 A 类（博物馆特展/画展艺术展/长档期漫展）。
        # B 类技术活动（沙龙/峰会/Meetup）和 C 类博览会不作为画展对待，按命中的具体周末日程展示在 W1/W2 等分组中。
        if ev.category in ("B", "C"):
            continue
        sd = meta["parse_date"](ev.start_date, today.year)
        ed = meta["parse_date"](ev.end_date, today.year) or sd
        if sd and ed and (ed - sd).days >= LONG_RUN_DAYS:
            longrun.append(ev)

    longrun_keys = {(e.name_norm, e.start_date, e.end_date) for e in longrun}
    by_group = {sat: [] for sat, _ in groups}
    unplaced = []
    for ev in events:
        if ev.category == "C":
            continue
        if (ev.name_norm, ev.start_date, ev.end_date) in longrun_keys:
            continue
        keys = meta["group_of"](ev)
        if not keys:
            unplaced.append(ev)
            continue
        for k in keys:
            if k in by_group:
                by_group[k].append(ev)

    weekend = []
    for sat, dates in groups:
        items = sorted(by_group.get(sat, []),
                       key=lambda e: (e.start_date, 0 if e.is_free else 1, e.name))
        label = " · ".join("%d/%d（%s）" % (d.month, d.day, WEEKDAY_CN[d.weekday()])
                           for d in dates)
        weekend.append((label, items))

    expo = sorted([e for e in events if e.category == "C"],
                  key=lambda e: (e.start_date, e.name))

    urgent = []
    for ev in events:
        if not ev.deadline:
            continue
        d = meta["parse_date"](ev.deadline, today.year)
        if not d:
            continue
        n = (d - today).days
        if 0 <= n <= 3:
            urgent.append((n, ev))
    urgent.sort(key=lambda x: x[0])

    return {
        "weekend": weekend,
        "longrun": sorted(longrun, key=lambda e: e.start_date),
        "unplaced": unplaced,
        "expo": expo,
        "urgent": urgent,
    }


def _header(today, window_days):
    win_end = today + datetime.timedelta(days=window_days)
    return "运行日：%s（周%s）｜时间窗：%s → %s（%d 天）" % (
        today.strftime("%Y-%m-%d"), WEEKDAY_CN[today.weekday()],
        today.strftime("%Y-%m-%d"), win_end.strftime("%Y-%m-%d"), window_days)


def _t_date(s):
    """2026-10-31 → 10-31; 非 ISO 串原样返回。"""
    s = str(s or "").strip()
    return s[5:] if len(s) >= 10 and s[4] == "-" else s


def _diff_lines(diff, meta, md=True):
    """「新增与变化」正文。本身就接近纯文本, 两种输出只差加粗/斜体标记。"""
    L = []
    if not diff or (not diff["new"] and not diff["changed"] and not diff["gone"]):
        if meta.get("has_baseline"):
            L.append("_与上一轮相比无变化。_" if md else "与上一轮相比无变化。")
        else:
            L.append("_首次运行，无对比基线。_" if md else "首次运行，无对比基线。")
        return L
    if diff["new"]:
        L.append(("**NEW（%d）**" if md else "NEW（%d）") % len(diff["new"]))
        for ev in diff["new"]:
            L.append("- %s ｜ %s ｜ %s" % (
                ev.start_date if md else _t_date(ev.start_date),
                ev.name, ev.venue or "—"))
        L.append("")
    if diff["changed"]:
        L.append(("**变化（%d）**" if md else "变化（%d）") % len(diff["changed"]))
        for c in diff["changed"]:
            ev = c["event"]
            detail = []
            if "改地址" in c["kind"]:
                detail.append("地址 %s → %s" % (c["old_venue"] or "—", ev.venue or "—"))
            if "改价" in c["kind"]:
                detail.append("价格 %s → %s" % (c["old_price"] or "—", ev.price_text or "—"))
            if "改期" in c["kind"]:
                detail.append("日期 %s → %s" % (c["old_start"] or "—", ev.start_date or "—"))
            L.append("- [%s] %s：%s" % (c["kind"], ev.name, "；".join(detail)))
        L.append("")
    if diff["gone"]:
        L.append(("**取消/下架（%d）**" if md else "取消/下架（%d）") % len(diff["gone"]))
        for row in diff["gone"]:
            L.append("- %s ｜ %s" % (
                (row["start_date"] or "—") if md else _t_date(row["start_date"] or "—"),
                row["name"]))
        L.append("")
    return L


# ---------------------------------------------------------------- Markdown
def _md_cell(s):
    if s is None:
        return "—"
    t = str(s).replace("|", "｜").replace("\n", " ").strip()
    return t or "—"


def _yes(v):
    return "★" if v else ""


def _feels(v):
    return "◆" if v else ""


def _price(ev):
    if ev.is_free is True:
        return "**免费**"
    if ev.price_text:
        return ev.price_text
    if ev.is_free is False:
        return "收费（价格待核实）"
    return "待核实"


def _link(ev):
    if not ev.url:
        return ev.source or "—"
    label = ev.source or "链接"
    return "[%s](%s)" % (label, ev.url)


def render(events, groups, today, window_days, diff, source_stats, meta):
    """events: 已过滤到窗口内的 Event 列表; groups: timerange.weekend_groups 结果。
    Markdown 版, 写进 data/reports/*.md。"""
    lay = _layout(events, groups, today, window_days, meta)
    city = (meta or {}).get("city") or "北京"
    L = []
    L.append("# %s周末活动雷达 · %s" % (city, today.strftime("%Y-%m-%d")))
    L.append("")
    L.append("> " + _header(today, window_days))
    L.append("> 标注：★ 有实物（周边/集章/赠品/纸质证明/管饭）｜◆ i 人友好（事务性交流）")
    L.append("> 生成方式：%s" % meta.get("engine", ""))
    L.append("")

    idx = 0
    for label, items in lay["weekend"]:
        idx += 1
        L.append("## W%d · %s" % (idx, label))
        L.append("")
        if not items:
            L.append("_本周末无检索到符合条件的活动。_")
            L.append("")
            continue
        L.append("| 日期 | 活动 | 地点 | 费用 | 实物★ | i人◆ | 报名截止⚠️ | 一句话看点 | 来源 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for ev in items:
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                _md_cell(ev.start_date[5:] if ev.start_date else ""),
                _md_cell(ev.name), _md_cell(ev.venue), _price(ev),
                _yes(ev.has_goods), _feels(ev.i_friendly),
                _md_cell(ev.deadline), _md_cell(ev.note), _link(ev)))
        L.append("")

    if lay["longrun"]:
        L.append("## 持续展出中（这周末也能去，非周末限定）")
        L.append("")
        L.append("| 展期 | 活动 | 地点 | 费用 | 实物★ | i人◆ | 来源 |")
        L.append("|---|---|---|---|---|---|---|")
        for ev in lay["longrun"]:
            span = "%s ~ %s" % (ev.start_date[5:], (ev.end_date or ev.start_date)[5:]) \
                if ev.end_date and ev.end_date != ev.start_date else ev.start_date[5:]
            L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                _md_cell(span), _md_cell(ev.name), _md_cell(ev.venue),
                _price(ev), _yes(ev.has_goods), _feels(ev.i_friendly), _link(ev)))
        L.append("")

    if lay["unplaced"]:
        L.append("## 其他（日期未能归入周末）")
        L.append("")
        L.append("| 日期 | 活动 | 地点 | 费用 | 来源 |")
        L.append("|---|---|---|---|---|")
        for ev in lay["unplaced"][:20]:
            L.append("| %s | %s | %s | %s | %s |" % (
                _md_cell(ev.start_date), _md_cell(ev.name),
                _md_cell(ev.venue), _price(ev), _link(ev)))
        L.append("")

    L.append("## 博览会速览（B2B 为主）")
    L.append("")
    if not lay["expo"]:
        L.append("_本轮未检索到博览会条目。_")
        L.append("")
    else:
        L.append("| 日期 | 博览会 | 地点 | 费用/预约 | 周末属性 | 一句话看点 |")
        L.append("|---|---|---|---|---|---|")
        for ev in lay["expo"]:
            weekend_attr = "★周末场" if ev.extra.get("has_weekend") else "无周末场"
            L.append("| %s | %s | %s | %s | %s | %s |" % (
                _md_cell("%s ~ %s" % (ev.start_date, ev.end_date)
                         if ev.end_date and ev.end_date != ev.start_date
                         else ev.start_date),
                _md_cell(ev.name), _md_cell(ev.venue), _price(ev),
                weekend_attr, _md_cell(ev.note)))
        L.append("")
    L.append("> B2B 预登记一律走官网，不要用搜博网、聚展网、去展网等第三方中介链接。")
    L.append("")

    L.append("## 截止提醒（3 天内）")
    L.append("")
    if lay["urgent"]:
        L.append("| 剩 | 事项 | 说明 |")
        L.append("|---|---|---|")
        for n, ev in lay["urgent"]:
            L.append("| %d 天 | **%s** | %s |" % (n, _md_cell(ev.name), _md_cell(ev.deadline)))
    else:
        L.append("_3 天内无临近截止项。_")
    L.append("")

    L.append("## 新增与变化")
    L.append("")
    L.extend(_diff_lines(diff, meta))

    # 抓取统计 —— 这一段绝不删除
    L.append("## 本次抓取统计")
    L.append("")
    L.append("| 源 | 状态 | 抓到条数 | 备注 |")
    L.append("|---|---|---|---|")
    for s in source_stats:
        status = "✅" if s["ok"] else "❌ 失败"
        L.append("| %s | %s | %d | %s |" % (
            s["name"], status, s["count"], _md_cell(s["err"] or s.get("note", ""))))
    L.append("")
    L.append("LLM：%s，调用 %d 次%s" % (
        meta.get("llm_desc", "未使用"),
        meta.get("llm_calls", 0),
        "，**有截断**" if meta.get("llm_truncated") else ""))
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 纯文本
def _t_span(ev):
    if ev.end_date and ev.end_date != ev.start_date:
        return "%s ~ %s" % (_t_date(ev.start_date), _t_date(ev.end_date))
    return _t_date(ev.start_date)


def _t_price(ev):
    if ev.is_free is True:
        return "免费"
    if ev.price_text:
        return ev.price_text
    if ev.is_free is False:
        return "收费（价格待核实）"
    return "待核实"


def _t_join(parts, sep=" ｜ "):
    return sep.join(str(p) for p in parts if p)


def _t_event(ev, show_deadline=False):
    """一个活动两到三行: 首行日期+名称, 次行属性, 末行链接。窄屏也能扫。"""
    lines = [_t_join([_t_span(ev), ev.name or "（未命名）"], sep=" ")]
    tail = _t_join([
        ev.venue,
        _t_price(ev),
        (_yes(ev.has_goods) + _feels(ev.i_friendly)) or "",
        ("截止 " + _t_date(ev.deadline)) if (show_deadline and ev.deadline) else "",
        ev.note,
    ])
    if tail:
        lines.append("    " + tail)
    if ev.url:
        lines.append("    " + ev.url)
    return "\n".join(lines)


def render_text(events, groups, today, window_days, diff, source_stats, meta):
    """纯文本版, 发给 PushDeer。

    为什么不直接发 Markdown: PushDeer 客户端的 Markdown 渲染器只实现了子集,
    表格基本不渲染, 整份 Markdown 发过去只会看到 `| 日期 | 活动 |` 这样的原文。
    """
    lay = _layout(events, groups, today, window_days, meta)
    city = (meta or {}).get("city") or "北京"
    L = []
    L.append("%s周末活动雷达 · %s" % (city, today.strftime("%Y-%m-%d")))
    L.append(_header(today, window_days))
    L.append("标注：★ 有实物（周边/集章/赠品/纸质证明/管饭）｜◆ i 人友好（事务性交流）")
    L.append("")

    idx = 0
    for label, items in lay["weekend"]:
        idx += 1
        L.append("【W%d】%s" % (idx, label))
        L.append("")
        if not items:
            L.append("本周末无检索到符合条件的活动。")
            L.append("")
            continue
        for ev in items:
            L.append(_t_event(ev, show_deadline=True))
        L.append("")

    if lay["longrun"]:
        L.append("【持续展出中】这周末也能去，非周末限定")
        L.append("")
        for ev in lay["longrun"]:
            L.append(_t_join([_t_span(ev), ev.name], sep=" "))
            tail = _t_join([ev.venue, _t_price(ev),
                            _yes(ev.has_goods) + _feels(ev.i_friendly)])
            if tail:
                L.append("    " + tail)
            if ev.url:
                L.append("    " + ev.url)
        L.append("")

    if lay["unplaced"]:
        L.append("【其他】日期未能归入周末")
        L.append("")
        for ev in lay["unplaced"][:20]:
            L.append(_t_join([_t_date(ev.start_date), ev.name], sep=" "))
            tail = _t_join([ev.venue, _t_price(ev)])
            if tail:
                L.append("    " + tail)
            if ev.url:
                L.append("    " + ev.url)
        L.append("")

    L.append("【博览会速览】B2B 为主")
    L.append("")
    if not lay["expo"]:
        L.append("本轮未检索到博览会条目。")
        L.append("")
    else:
        for ev in lay["expo"]:
            L.append(_t_join([_t_span(ev), ev.name], sep=" "))
            weekend_attr = "★周末场" if ev.extra.get("has_weekend") else "无周末场"
            tail = _t_join([ev.venue, _t_price(ev), weekend_attr, ev.note])
            if tail:
                L.append("    " + tail)
        L.append("")
    L.append("B2B 预登记一律走官网，不要用搜博网、聚展网、去展网等第三方中介链接。")
    L.append("")

    L.append("【截止提醒】3 天内")
    L.append("")
    if lay["urgent"]:
        for n, ev in lay["urgent"]:
            L.append("剩 %d 天 ｜ %s ｜ %s" % (n, ev.name, _t_date(ev.deadline)))
    else:
        L.append("3 天内无临近截止项。")
    L.append("")

    L.append("【新增与变化】")
    L.append("")
    L.extend(_diff_lines(diff, meta, md=False))
    L.append("")

    L.append("【本次抓取统计】")
    L.append("")
    for s in source_stats:
        L.append("%s ｜ %s ｜ %d 条%s" % (
            s["name"], "✅" if s["ok"] else "❌ 失败", s["count"],
            (" ｜ " + (s["err"] or s.get("note", "")))
            if (s["err"] or s.get("note")) else ""))
    L.append("")
    L.append("LLM：%s，调用 %d 次%s" % (
        meta.get("llm_desc", "未使用"),
        meta.get("llm_calls", 0),
        "，有截断" if meta.get("llm_truncated") else ""))
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 手机端 Markdown (PushDeer 卡片排版)
def _md_push_event(ev, show_deadline=False, show_date=True, extra_note=None):
    """手机端 Markdown 卡片排版:
    - **[活动名](URL)**
      > 📅 日期 ｜ 📍 场馆 ｜ 💰 票价 ｜ ★有实物 ｜ ◆i人友好 ｜ ⚠️截止 MM-DD ｜ 看点
    """
    title = "[%s](%s)" % (ev.name, ev.url) if ev.url else (ev.name or "（未命名）")
    tags = []
    if show_date:
        span = _t_span(ev)
        if span:
            tags.append("📅 %s" % span)
    if ev.venue:
        tags.append("📍 %s" % ev.venue)
    if ev.price_text or ev.is_free is not None:
        tags.append("💰 %s" % _t_price(ev))
    if ev.has_goods:
        tags.append("★有实物")
    if ev.i_friendly:
        tags.append("◆i人友好")
    if show_deadline and ev.deadline:
        tags.append("⚠️截止 %s" % _t_date(ev.deadline))
    note = extra_note or ev.note
    if note:
        tags.append(note)
    line = "- **%s**" % title
    if tags:
        line += "\n  > " + " ｜ ".join(tags)
    return line


def _md_push_diff(diff, meta):
    """手机端「新增与变化」卡片流排版，与周末分组具有一致的颜色对比与超链接跳转。"""
    L = []
    if not diff or (not diff.get("new") and not diff.get("changed") and not diff.get("gone")):
        if meta and meta.get("has_baseline"):
            L.append("_与上一轮相比无变化。_")
        else:
            L.append("_首次运行，无对比基线。_")
        return L

    if diff.get("new"):
        L.append("**🔥 NEW 新增（%d）**" % len(diff["new"]))
        L.append("")
        for ev in diff["new"]:
            L.append(_md_push_event(ev, show_deadline=False, show_date=True))
        L.append("")

    if diff.get("changed"):
        L.append("**🔄 变更提醒（%d）**" % len(diff["changed"]))
        L.append("")
        for c in diff["changed"]:
            ev = c.get("event")
            title = "[%s](%s)" % (ev.name, ev.url) if (ev and ev.url) else (ev.name if ev else "（未命名）")
            kind = c.get("kind", "变更")
            details = []
            if "改地址" in kind:
                details.append("📍 地址 %s → %s" % (c.get("old_venue") or "—", ev.venue or "—"))
            if "改价" in kind:
                details.append("💰 价格 %s → %s" % (c.get("old_price") or "—", ev.price_text or "—"))
            if "改期" in kind:
                details.append("📅 日期 %s → %s" % (c.get("old_start") or "—", ev.start_date or "—"))
            line = "- **[%s] %s**" % (kind, title)
            if details:
                line += "\n  > " + " ｜ ".join(details)
            L.append(line)
        L.append("")

    if diff.get("gone"):
        L.append("**❌ 取消 / 下架（%d）**" % len(diff["gone"]))
        L.append("")
        for row in diff["gone"]:
            # row 是 sqlite3.Row(无 .get); markdown 报告版(_diff_lines)用下标访问, 这里统一转 dict
            if not isinstance(row, dict):
                row = dict(row)
            name = row.get("name") or "（未命名）"
            date = _t_date(row.get("start_date") or "—")
            line = "- **%s**\n  > 🕒 档期 %s ｜ ⚠️ 已从源平台下架或取消" % (name, date)
            L.append(line)
        L.append("")

    return L


def render_push_markdown(events, groups, today, window_days, diff, source_stats, meta):
    """手机端 Markdown 卡片排版, 发给 PushDeer。

    针对 PushDeer 客户端特性设计:
    1) PushDeer 支持加粗、标题、列表、超链接与引用块，但不支持 GFM 表格（会显示为乱码管道符）。
    2) 将活动排版为流式卡片，活动名称即超链接，点击可直接跳到详情/购票页，免去暴露大段冗长 URL。
    3) 属性集中在卡片引用块内（含 Emoji 图标），窄屏折行美观。
    """
    lay = _layout(events, groups, today, window_days, meta)
    city = (meta or {}).get("city") or "北京"
    L = []
    L.append("# 🎯 %s周末活动雷达 · %s" % (city, today.strftime("%m-%d")))
    L.append("")
    L.append("> 📅 %s" % _header(today, window_days))
    L.append("> 💡 标注：★ 有实物（周边/集章/赠品/纸质证明/管饭）｜◆ i 人友好（事务性交流）")
    L.append("")

    # 截止提醒
    L.append("### ⏰ 临近截止提醒（3 天内）")
    L.append("")
    if lay["urgent"]:
        for n, ev in lay["urgent"]:
            link = "[%s](%s)" % (ev.name, ev.url) if ev.url else ev.name
            L.append("- **%s**：剩 **%d** 天（%s 截止）" % (link, n, _t_date(ev.deadline)))
    else:
        L.append("_3 天内无临近截止项。_")
    L.append("")

    # 新增与变化
    diff_lines = _md_push_diff(diff, meta)
    if diff_lines:
        L.append("### 🔔 新增与变化")
        L.append("")
        L.extend(diff_lines)
        L.append("")

    # 周末活动分组
    idx = 0
    for label, items in lay["weekend"]:
        idx += 1
        L.append("### 📅 【W%d】%s" % (idx, label))
        L.append("")
        if not items:
            L.append("_本周末无检索到符合条件的活动。_")
            L.append("")
            continue
        for ev in items:
            L.append(_md_push_event(ev, show_deadline=True, show_date=True))
        L.append("")

    # 持续展出
    if lay["longrun"]:
        L.append("### 🏛️ 持续展出中（非周末限定）")
        L.append("")
        for ev in lay["longrun"]:
            L.append(_md_push_event(ev, show_deadline=False, show_date=True))
        L.append("")

    # 其他未归入周末
    if lay["unplaced"]:
        L.append("### 📌 其他（日期未能归入周末）")
        L.append("")
        for ev in lay["unplaced"][:20]:
            L.append(_md_push_event(ev, show_deadline=False, show_date=True))
        L.append("")

    # 博览会
    L.append("### 🏢 博览会速览（B2B 为主）")
    L.append("")
    if not lay["expo"]:
        L.append("_本轮未检索到博览会条目。_")
        L.append("")
    else:
        for ev in lay["expo"]:
            weekend_attr = "★周末场" if (ev.extra and ev.extra.get("has_weekend")) else "无周末场"
            note = weekend_attr + (" ｜ " + ev.note if ev.note else "")
            L.append(_md_push_event(ev, show_deadline=False, show_date=True, extra_note=note))
        L.append("")
    L.append("> 💡 *B2B 预登记一律走官网，不要用搜博网、聚展网、去展网等第三方中介链接。*")
    L.append("")

    # 本次抓取统计
    L.append("### 📊 本次抓取统计")
    L.append("")
    for s in source_stats:
        status = "✅" if s["ok"] else "❌ 失败"
        err = s.get("err", "")
        note = s.get("note", "")
        err_note = (" ｜ " + (err or note)) if (err or note) else ""
        L.append("- **%s**：%s %d 条%s" % (s["name"], status, s["count"], err_note))
    L.append("")
    L.append("> 🤖 LLM：%s，调用 %d 次%s" % (
        meta.get("llm_desc", "未使用"),
        meta.get("llm_calls", 0),
        "，**有截断**" if meta.get("llm_truncated") else ""))
    L.append("")
    return "\n".join(L)
