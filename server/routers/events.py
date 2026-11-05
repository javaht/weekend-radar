# -*- coding: utf-8 -*-
"""活动查询、周末分组、历史批次与报告接口。"""
import datetime
import os
from fastapi import APIRouter, Query, HTTPException
from dataclasses import asdict

from core.render import _layout
from core import timerange as TR
from core.config import as_int, abspath
from core.genre import genre_of
from core.models import price_tier, PRICE_TIER_LABELS
from server.deps import get_cfg, get_store

router = APIRouter(prefix="/api", tags=["events"])

# 「即将结束」阈值: 剩 3 天及以内算紧急。实测 118 条里命中 32%, 是唯一
# 有足够区分度且零维护的派生标签(纯日期计算)。
ENDING_SOON_DAYS = 3
# 「新发现」阈值: 首次入库距今 3 天内。连续跑一周后这个标签才有意义,
# 刚初始化时会全量命中。
NEW_DAYS = 3


def _derived_tags(ev, today):
    """给一条活动算派生标签。全部是纯计算, 不依赖源是否提供字段。"""
    tags = []
    sd = TR.parse_date(ev.start_date, today.year)
    ed = TR.parse_date(ev.end_date, today.year) or sd
    if ed and 0 <= (ed - today).days <= ENDING_SOON_DAYS:
        tags.append("ending_soon")
    # 「新发现」看 first_seen(首次入库日), 不是 start_date。
    # ★ 这两个概念不能混: start_date 是活动开办日(未来), first_seen 是我们
    #   第一次抓到它的那天(过去)。「本周新活动」要的是后者。
    fs = TR.parse_date((ev.extra or {}).get("first_seen"), today.year)
    if fs and 0 <= (today - fs).days <= NEW_DAYS:
        tags.append("new")
    if sd and 0 <= (sd - today).days <= NEW_DAYS:
        tags.append("starting_soon")
    return tags


def _serialize_event(ev, today=None):
    if hasattr(ev, "__dict__"):
        d = asdict(ev)
        d["name_norm"] = getattr(ev, "name_norm", "")
        d["genre"] = genre_of(ev.name, ev.category)
        d["price_tier"] = price_tier(ev.price_text, ev.is_free)
        d["price_tier_label"] = PRICE_TIER_LABELS.get(d["price_tier"], "")
        d["derived_tags"] = _derived_tags(ev, today or datetime.date.today())
        return d
    return ev


@router.get("/events")
def list_events(
    status: str = Query("active", description="状态: active / gone / expired 或空"),
    category: str = Query(None, description="分类: A / B / C"),
    is_free: bool = Query(None, description="是否免费"),
    has_goods: bool = Query(None, description="是否有周边/物料"),
    keyword: str = Query(None, description="关键词模糊搜索"),
    price_tier: str = Query(None, description="价格档位: free / under100 / mid / high / unknown"),
    start_date_ge: str = Query(None, description="开始日期起始"),
    start_date_le: str = Query(None, description="开始日期截止"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    store = get_store()
    try:
        return store.query_events(
            status=status if status else None,
            category=category,
            is_free=is_free,
            has_goods=has_goods,
            keyword=keyword,
            price_tier=price_tier,
            start_date_ge=start_date_ge,
            start_date_le=start_date_le,
            limit=limit,
            offset=offset,
        )
    finally:
        store.close()


@router.get("/events/grouped")
def get_grouped_events(city: str = Query(None, description="指定城市")):
    cfg, _ = get_cfg(city)
    today = datetime.date.today()
    days = as_int(cfg, "radar", "window_days", 30)
    groups = TR.weekend_groups(today, days)

    def group_of(ev):
        sd = TR.parse_date(ev.start_date, today.year)
        ed = TR.parse_date(ev.end_date, today.year) or sd
        return TR.event_weekend_dates(sd, ed, today, days)

    meta = {
        "city": cfg["radar"].get("city", "北京"),
        "group_of": group_of,
        "parse_date": TR.parse_date,
    }

    store = get_store(cfg)
    try:
        events = store.get_active_events_as_objects()
        layout = _layout(events, groups, today, days, meta)

        def ser(e, extra=None):
            d = _serialize_event(e, today)
            # 本周末可去: 只有「持续展出」和「博览会」视图需要这个角标,
            # 周末分组里本来就是本周末, 标了是冗余。
            sd = TR.parse_date(e.start_date, today.year)
            ed = TR.parse_date(e.end_date, today.year) or sd
            d["this_weekend"] = bool(sd and TR.event_weekend_dates(sd, ed, today, 7))
            if extra:
                d.update(extra)
            return d

        serialized_weekends = []
        for label, items in layout["weekend"]:
            serialized_weekends.append({
                "label": label,
                "count": len(items),
                "items": [ser(e) for e in items]
            })

        return {
            "city": meta["city"],
            "today": today.isoformat(),
            "window_days": days,
            "total_active": len(events),
            "weekends": serialized_weekends,
            "longrun": [ser(e) for e in layout["longrun"]],
            "expo": [ser(e) for e in layout["expo"]],
            "urgent": [ser(e, {"days_left": n}) for n, e in layout["urgent"]],
            "unplaced": [ser(e) for e in layout["unplaced"]],
        }
    finally:
        store.close()


@router.get("/events/stats")
def get_stats():
    store = get_store()
    try:
        return store.get_stats()
    finally:
        store.close()


@router.get("/runs")
def list_runs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    store = get_store()
    try:
        return store.get_runs(limit=limit, offset=offset)
    finally:
        store.close()


@router.get("/reports")
def list_reports():
    cfg, _ = get_cfg()
    out_dir = abspath(cfg["radar"].get("out_dir", "data/reports"))
    if not os.path.isdir(out_dir):
        return {"reports": []}
    files = []
    for f in sorted(os.listdir(out_dir), reverse=True):
        if f.endswith(".md"):
            p = os.path.join(out_dir, f)
            files.append({
                "date": f[:-3],
                "filename": f,
                "size": os.path.getsize(p),
                "mtime": os.path.getmtime(p),
            })
    return {"reports": files}


@router.get("/reports/{date}")
def get_report_content(date: str):
    cfg, _ = get_cfg()
    out_dir = abspath(cfg["radar"].get("out_dir", "data/reports"))
    filename = f"{date}.md" if not date.endswith(".md") else date
    p = os.path.join(out_dir, filename)
    if not os.path.isfile(p):
        raise HTTPException(status_code=404, detail="Report not found")
    with open(p, "r", encoding="utf-8") as f:
        content = f.read()
    return {"date": date, "content": content}
