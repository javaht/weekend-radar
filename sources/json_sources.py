# -*- coding: utf-8 -*-
"""JSON 源: 大麦、B站会员购。

这些接口都不是官方开放的, 随时可能改。所以:
  - 解析写得尽量宽容(递归找列表、字段多别名), 而不是死磕某个字段路径;
  - 拿不到就返回 ok=False + 原因, 让上层发健康告警, 绝不假装"今天没活动"。
"""
import time

from sources.base import SourceResult, coerce_event


def _find_list(obj, wanted_keys, depth=0):
    """在任意嵌套 JSON 里找第一个「元素是 dict 且含指定字段」的列表。

    写死路径在接口改版时必然崩; 递归找能撑更久。
    """
    if depth > 6:
        return None
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and any(k in item for k in wanted_keys):
                return obj
        for item in obj:
            found = _find_list(item, wanted_keys, depth + 1)
            if found:
                return found
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            found = _find_list(v, wanted_keys, depth + 1)
            if found:
                return found
    return None


def _first(d, *names):
    for n in names:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return None


def _cents_to_yuan(v):
    """B站价格单位是分 -> 元。已是小数(元)则原样返回。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f / 100.0 if abs(f) >= 100 else f


def _fmt_yuan(v):
    if v is None:
        return "?"
    return str(int(v)) if abs(v - int(v)) < 1e-6 else ("%.2f" % v)


class DamaiSource:
    """大麦搜索接口。city=101010100 是北京。"""

    name = "damai"
    kind = "J"

    def __init__(self, url, cfg, fetcher, log=print):
        self.url = url
        self.cfg = cfg
        self.fetcher = fetcher
        self.log = log
        self.city = str(cfg.get("damai_city", "101010100"))
        self.keyword = str(cfg.get("damai_keyword", "漫展"))

    def fetch(self):
        params = {
            "city": self.city,
            "keyword": self.keyword,
            "ctl": "",
            "order": "1",
            "page": "1",
            "ts": str(int(time.time() * 1000)),
        }
        headers = {"Referer": "https://search.damai.cn/",
                   "Accept": "application/json, text/plain, */*"}
        res = self.fetcher.get_json(self.url, params=params, headers=headers)
        if not res.ok:
            return SourceResult(self.name, False, err=res.err or ("HTTP %s" % res.status))
        try:
            import json as _json
            obj = _json.loads(res.text)
        except Exception:
            return SourceResult(self.name, False, err="返回非 JSON", raw_bytes=len(res.text))
        lst = _find_list(obj, ("nameNoHtml", "name", "projectName", "showTime"))
        if not lst:
            return SourceResult(self.name, False,
                                err="未找到结果列表(接口可能已改版, 用 --probe 复核)",
                                raw_bytes=len(res.text))
        events = []
        for item in lst:
            name = _first(item, "nameNoHtml", "name", "projectName", "nameHtml")
            start = _first(item, "showTime", "show_time", "startTime", "start_time")
            if not name or not start:
                continue
            pid = _first(item, "id", "projectId", "project_id", "itemId")
            url = ("https://detail.damai.cn/item.htm?id=%s" % pid) if pid else ""
            ev = coerce_event({
                "name": name,
                "start_date": start,
                "venue": _first(item, "venueName", "venue_name", "venue"),
                "price_text": _first(item, "priceStr", "price_str", "price"),
                "url": url,
                "note": _first(item, "categoryName", "category_name"),
            }, self.name, default_category="A", fallback_url=self.url)
            if ev:
                events.append(ev)
        return SourceResult(self.name, True, len(events), events,
                            note="关键词=%s" % self.keyword, raw_bytes=len(res.text))


class BiliSource:
    """B站会员购。已知坑: pagesize>20 被拒; page=2 会重复首页, 所以只取单页。"""

    name = "bili"
    kind = "J"

    def __init__(self, url, cfg, fetcher, log=print):
        self.url = url
        self.cfg = cfg
        self.fetcher = fetcher
        self.log = log
        self.area = str(cfg.get("bili_area", "110000"))
        self.p_type = str(cfg.get("bili_ptype", "展览"))
        self.pagesize = min(20, int(cfg.get("bili_pagesize", 20) or 20))

    def fetch(self):
        params = {
            "area": self.area,
            "p_type": self.p_type,
            "page": "1",
            "pagesize": str(self.pagesize),
            "version": "134",
            "platform": "web",
            "filter": "",
        }
        headers = {"Referer": "https://show.bilibili.com/",
                   "Accept": "application/json, text/plain, */*"}
        res = self.fetcher.get_json(self.url, params=params, headers=headers)
        if not res.ok:
            return SourceResult(
                self.name, False,
                err=(res.err or ("HTTP %s" % res.status)) +
                    "(路径含被打码的 api 段, 若 404 需在 config 里换真实路径)",
                raw_bytes=len(res.text or ""))
        try:
            import json as _json
            obj = _json.loads(res.text)
        except Exception:
            return SourceResult(self.name, False, err="返回非 JSON",
                                raw_bytes=len(res.text or ""))
        lst = _find_list(obj, ("project_name", "projectName", "name"))
        if not lst:
            return SourceResult(self.name, False, err="未找到项目列表", raw_bytes=len(res.text))
        events = []
        for item in lst:
            name = _first(item, "project_name", "projectName", "name")
            start = _first(item, "start_time", "startTime", "start_unix")
            if not name or not start:
                continue
            pid = _first(item, "project_id", "projectId", "id")
            url = ("https://show.bilibili.com/platform/detail.html?id=%s" % pid) if pid else ""
            price = None
            lo = _first(item, "price_low", "priceLow")
            hi = _first(item, "price_high", "priceHigh")
            # ★ B站接口的价格单位是「分」。不除以 100 会写出 ¥8000-15800 这种
            #   离谱数字（实际 ¥80-158），是上一版真实踩到的坑。
            lo = _cents_to_yuan(lo)
            hi = _cents_to_yuan(hi)
            if lo is not None and hi is not None and lo != hi:
                price = "¥%s-%s" % (_fmt_yuan(lo), _fmt_yuan(hi))
            elif lo is not None:
                price = "¥%s起" % _fmt_yuan(lo)
            ev = coerce_event({
                "name": name,
                "start_date": start,
                "end_date": _first(item, "end_time", "endTime", "end_unix"),
                "venue": _first(item, "venue_name", "venueName"),
                "price_text": price,
                "url": url,
                "note": _first(item, "city_name", "district_name"),
            }, self.name, default_category="A", fallback_url=self.url)
            if ev:
                events.append(ev)
        return SourceResult(self.name, True, len(events), events,
                            note="p_type=%s" % self.p_type, raw_bytes=len(res.text))



class CppSource:
    """CPP 无差别同人站。默认 city=110000（北京），随 [radar] city 自动切换行政区划码。"""

    name = "cpp"
    kind = "J"

    def __init__(self, url, cfg, fetcher, log=print):
        self.url = url
        self.cfg = cfg
        self.fetcher = fetcher
        self.log = log
        self.city = str(cfg.get("cpp_city", "110000"))
        self.city_name = str(cfg.get("city", "北京") or "北京").strip()
        if len(self.city_name) > 2 and self.city_name.endswith("市"):
            self.city_name = self.city_name[:-1]
        self.pagesize = min(50, int(cfg.get("cpp_pagesize", 30) or 30))

    def fetch(self):
        params = {
            "city": self.city,
            "pageNo": "1",
            "pageSize": str(self.pagesize),
        }
        headers = {
            "Referer": "https://cp.allcpp.cn/",
            "Accept": "application/json, text/plain, */*",
        }
        res = self.fetcher.get_json(self.url, params=params, headers=headers)
        if not res.ok:
            return SourceResult(self.name, False, err=res.err or ("HTTP %s" % res.status))
        try:
            import json as _json
            obj = _json.loads(res.text)
        except Exception:
            return SourceResult(self.name, False, err="返回非 JSON", raw_bytes=len(res.text or ""))
        
        lst = obj.get("result", {}).get("list", []) if isinstance(obj, dict) else []
        if not lst:
            lst = _find_list(obj, ("name", "enterTime", "cityName")) or []
        
        import datetime
        events = []
        for item in lst:
            city_name = str(item.get("cityName") or "")
            addr = item.get("enterAddress") or ""
            if city_name and (self.city_name not in city_name) and (self.city_name not in addr):
                continue
            name = item.get("name")
            start_ms = item.get("enterTime")
            end_ms = item.get("endTime")
            if not name or not start_ms:
                continue
            try:
                start_date = datetime.date.fromtimestamp(start_ms / 1000).isoformat()
            except Exception:
                continue
            try:
                end_date = datetime.date.fromtimestamp(end_ms / 1000).isoformat() if end_ms else start_date
            except Exception:
                end_date = start_date
            
            eid = item.get("id") or item.get("eventId")
            url = ("https://www.allcpp.cn/allcpp/event/getEventMain.do?id=%s" % eid) if eid else ""
            area = item.get("areaName") or ""
            venue = ("%s %s %s" % (self.city_name, area, addr)).strip() if (area or addr) else self.city_name
            ticket_status = item.get("ticketStatus")
            price_text = "未开票" if ticket_status == 0 else "购票见详情"
            ev = coerce_event({
                "name": name,
                "start_date": start_date,
                "end_date": end_date,
                "venue": venue,
                "price_text": price_text,
                "url": url,
                "note": item.get("type", "同人展"),
            }, self.name, default_category="A", fallback_url=self.url)
            if ev:
                events.append(ev)
        return SourceResult(self.name, True, len(events), events, note="city=%s" % self.city, raw_bytes=len(res.text or ""))


class CnccSource:
    """国家会议中心官方活动排期。按月拉取。"""

    name = "cncc"
    kind = "J"

    def __init__(self, url, cfg, fetcher, log=print):
        self.url = url
        self.cfg = cfg
        self.fetcher = fetcher
        self.log = log

    def fetch(self):
        import datetime
        today = datetime.date.today()
        m1 = today.strftime("%Y-%m")
        # 下个月
        m2 = (today.replace(day=1) + datetime.timedelta(days=32)).strftime("%Y-%m")
        headers = {
            "Referer": "https://www.cnccchina.com/activity.html",
            "Accept": "application/json, text/javascript, */*",
        }
        
        events = []
        total_bytes = 0
        import json as _json
        for m in [m1, m2]:
            res = self.fetcher.get_json(self.url, params={"month": m}, headers=headers)
            if not res.ok:
                continue
            total_bytes += len(res.text or "")
            try:
                arr = _json.loads(res.text)
            except Exception:
                continue
            if not isinstance(arr, list):
                continue
            for item in arr:
                title = item.get("title")
                st = (item.get("start_time") or "").replace(".", "-").strip()
                et = (item.get("end_time") or "").replace(".", "-").strip()
                if not title or not st:
                    continue
                ev = coerce_event({
                    "name": title,
                    "start_date": st,
                    "end_date": et or st,
                    "venue": "国家会议中心",
                    "price_text": "展会/见官网预约",
                    "url": "https://www.cnccchina.com/activity.html",
                    "note": item.get("desc") or "博览会",
                }, self.name, default_category="C", fallback_url=self.url)
                if ev:
                    events.append(ev)
        
        return SourceResult(self.name, True, len(events), events, note="months=%s,%s" % (m1, m2), raw_bytes=total_bytes)
