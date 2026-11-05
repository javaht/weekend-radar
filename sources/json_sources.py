# -*- coding: utf-8 -*-
"""JSON 源: 大麦、B站会员购。

这些接口都不是官方开放的, 随时可能改。所以:
  - 解析写得尽量宽容(递归找列表、字段多别名), 而不是死磕某个字段路径;
  - 拿不到就返回 ok=False + 原因, 让上层发健康告警, 绝不假装"今天没活动"。
"""
import datetime
import re
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


class OschinaSource:
    """开源中国活动。www.oschina.net/event 是纯 JS 页面(正文只有 31 字), 走 HTML+LLM
    必然天天失败; 页面背后的 apiv1 接口直接给开始/结束/报名截止/城市/收费, 所以走 JSON。

    已知: 列表按开始时间倒序, 不支持按城市过滤未来活动, 所以翻几页后本地按城市筛;
    「线上」活动不收(雷达只看线下)。

    GOTC(开源中国自家的年度大会)不在活动列表里, 只在 gotc.oschina.net。那个站没有数据接口,
    日期/地点写死在前端 JS 的配置对象里, 所以单独解析(见 _fetch_gotc)。
    """

    name = "oschina"
    kind = "J"
    MAX_PAGES = 4
    PAGE_SIZE = 50
    GOTC_URL = "https://gotc.oschina.net/"
    # 前端配置对象: {name:"GOTC 2026",full:"全球开源技术峰会",theme:"...",date:"2026.11.14",place:"..."}
    _GOTC_CONF_RE = re.compile(r'\{name:"(GOTC[^"]*)"[^{}]*?\}')
    _GOTC_REGISTER_RE = re.compile(r'register:"(https?://[^"]+)"')
    _CHUNK_IMPORT_RE = re.compile(r'import\("\./([\w.-]+\.js)"\)')

    @staticmethod
    def _js_field(obj_src, key):
        m = re.search(r'\b%s:"([^"]*)"' % key, obj_src)
        return m.group(1) if m else ""

    def _fetch_gotc(self):
        """返回 (Event 或 None, 错误串)。站点首页 → 主 JS → GOTC 页面的分包 → 配置对象。

        分包文件名带内容哈希, 每次发版都变, 所以每轮都从首页现找, 不写死。
        """
        home = self.fetcher.get(self.GOTC_URL)
        if not home.ok:
            return None, "GOTC 首页: %s" % home.err
        m = re.search(r'src="(/assets/[\w.-]+\.js)"', home.text or "")
        if not m:
            return None, "GOTC 首页没找到主 JS"
        root = self.GOTC_URL.rstrip("/")
        main = self.fetcher.get(root + m.group(1))
        if not main.ok:
            return None, "GOTC 主 JS: %s" % main.err
        js = main.text or ""
        # 路由表里: import("./index-xxxx.js") ... meta:{title:"GOTC 2026 · ..."}
        chunk = ""
        for im in self._CHUNK_IMPORT_RE.finditer(js):
            if "GOTC" in js[im.end():im.end() + 300]:
                chunk = im.group(1)
                break
        if chunk:
            page = self.fetcher.get(root + "/assets/" + chunk)
            if not page.ok:
                return None, "GOTC 页面分包: %s" % page.err
            js = page.text or ""
        conf = self._GOTC_CONF_RE.search(js)
        if not conf:
            return None, "GOTC 页面结构变了, 没找到 {name:\"GOTC...\"} 配置对象"
        src = conf.group(0)
        title = " ".join(x for x in (conf.group(1), self._js_field(src, "full")) if x)
        place = self._js_field(src, "place")
        if self.city_name not in place:
            return None, ""          # 今年不在本城市: 不是错误
        reg = self._GOTC_REGISTER_RE.search(js)
        ev = coerce_event({
            "name": title,
            "start_date": self._js_field(src, "date"),
            "venue": place,
            "url": self.GOTC_URL,
            "note": "；".join(x for x in (
                self._js_field(src, "theme"),
                "观众报名: %s" % reg.group(1) if reg else "") if x),
        }, self.name, default_category="B", fallback_url=self.GOTC_URL)
        if not ev:
            return None, "GOTC 日期解析失败: %r" % self._js_field(src, "date")
        return ev, ""

    def __init__(self, url, cfg, fetcher, log=print):
        self.url = url
        self.cfg = cfg
        self.fetcher = fetcher
        self.log = log
        city = str(cfg.get("city", "") or "北京").strip()
        if len(city) > 2 and city.endswith("市"):
            city = city[:-1]
        self.city_name = city

    def _in_city(self, item):
        where = "%s %s %s" % (item.get("province") or "", item.get("city") or "",
                              item.get("spot") or "")
        return self.city_name in where and "线上" not in where

    def fetch(self):
        import json as _json
        import datetime as _dt
        from html import unescape as _unescape
        headers = {"Referer": "https://www.oschina.net/event",
                   "Accept": "application/json, text/plain, */*"}
        # 比赛类活动开始得早、结束得晚, 往回多翻一些; 开始时间早于这条线就停
        stop_before = (_dt.date.today() - _dt.timedelta(days=180)).isoformat()
        items, raw = [], 0
        for page in range(1, self.MAX_PAGES + 1):
            res = self.fetcher.get_json(
                self.url, params={"pageNum": page, "pageSize": self.PAGE_SIZE},
                headers=headers)
            if not res.ok:
                if page == 1:
                    return SourceResult(self.name, False,
                                        err=res.err or ("HTTP %s" % res.status))
                break
            raw += len(res.text or "")
            try:
                obj = _json.loads(res.text)
            except Exception:
                return SourceResult(self.name, False, err="返回非 JSON", raw_bytes=raw)
            if obj.get("code") != 200 or not isinstance(obj.get("result"), list):
                return SourceResult(self.name, False,
                                    err="接口返回 code=%s %s" % (obj.get("code"), obj.get("message")),
                                    raw_bytes=raw)
            batch = obj["result"]
            items.extend(batch)
            if len(batch) < self.PAGE_SIZE or all(
                    str(i.get("startTime") or "") < stop_before for i in batch):
                break

        events = []
        for item in items:
            if not self._in_city(item):
                continue
            eid = item.get("id")
            fee_free = item.get("feeType") == 0
            detail = str(item.get("feeDetail") or "").strip()
            ev = coerce_event({
                "name": _unescape(str(item.get("topic") or "")),   # 标题里有 &amp; 之类实体
                "start_date": item.get("startTime"),
                "end_date": item.get("endTime"),
                "venue": item.get("spot") or item.get("city"),
                "price_text": "免费" if fee_free else (detail or "收费"),
                "is_free": True if fee_free else None,
                "url": ("https://www.oschina.net/event/%s" % eid) if eid else "",
                "deadline": str(item.get("joinDeadTime") or "")[:10],
                "note": "开源中国活动",
            }, self.name, default_category="B", fallback_url="https://www.oschina.net/event")
            if ev:
                events.append(ev)

        gotc, gerr = self._fetch_gotc()
        if gerr:
            # ★ 判失败而不是悄悄跳过: GOTC 是这个源存在的主要理由之一, 抓不到要有告警
            return SourceResult(self.name, False, err=gerr, raw_bytes=raw)
        if gotc and not any(e.name_norm == gotc.name_norm for e in events):
            events.append(gotc)
        return SourceResult(self.name, True, len(events), events,
                            note="接口 %d 条, %s 线下 %d 条%s" % (
                                len(items), self.city_name, len(events),
                                "（含 GOTC）" if gotc else ""),
                            raw_bytes=raw)


class CsdnSource:
    """CSDN 技术会议。huiyi.csdn.net 已跳到 con.csdn.net(DevCon 技术会议平台), 纯 JS 页面,
    HTML+LLM 方式只能拿到空壳。页面背后的列表接口不需要登录, 直接给开始/结束/城市/线上线下。

    已知: 接口是 POST + JSON body(GET 会 500); page 从 0 开始。
    """

    name = "csdn"
    kind = "J"
    MAX_PAGES = 5
    PAGE_SIZE = 20
    SITE = "https://con.csdn.net"

    def __init__(self, url, cfg, fetcher, log=print):
        self.url = url
        self.cfg = cfg
        self.fetcher = fetcher
        self.log = log
        city = str(cfg.get("city", "") or "北京").strip()
        if len(city) > 2 and city.endswith("市"):
            city = city[:-1]
        self.city_name = city

    def _post(self, body):
        import json as _json
        # Fetcher 只有 GET, 这里复用它的 Session / 限速 / 退避
        self.fetcher._throttle()
        last = ""
        for attempt in range(1, self.fetcher.retries + 1):
            try:
                r = self.fetcher._sess().post(
                    self.url, json=body, timeout=self.fetcher.timeout,
                    headers={"Referer": self.SITE + "/",
                             "Accept": "application/json, text/plain, */*"})
                if r.status_code >= 500 and attempt < self.fetcher.retries:
                    last = "HTTP %d" % r.status_code
                    self.fetcher._sleep_backoff(attempt)
                    continue
                return _json.loads(r.text), len(r.text or "")
            except Exception as e:
                last = "%s: %s" % (type(e).__name__, e)
                if attempt < self.fetcher.retries:
                    self.fetcher._sleep_backoff(attempt)
                    continue
        raise RuntimeError(last)

    def fetch(self):
        items, raw = [], 0
        for page in range(self.MAX_PAGES):
            try:
                obj, n = self._post({"page": page, "size": self.PAGE_SIZE})
            except Exception as e:
                if page == 0:
                    return SourceResult(self.name, False, err=str(e))
                break
            raw += n
            if obj.get("code") != 200 or not isinstance(obj.get("data"), dict):
                return SourceResult(self.name, False,
                                    err="接口返回 code=%s %s" % (obj.get("code"), obj.get("msg")),
                                    raw_bytes=raw)
            data = obj["data"]
            batch = data.get("items") or []
            items.extend(batch)
            if not batch or page + 1 >= int(data.get("totalPages") or 1):
                break

        events = []
        for item in items:
            if item.get("ended") or item.get("format") == "ONLINE":
                continue
            where = "%s %s" % (item.get("cityName") or "", item.get("venue") or "")
            if self.city_name not in where:
                continue
            note = " · ".join(x for x in (item.get("meetingTypeDisplay"),
                                          item.get("organizerName")) if x)
            ev = coerce_event({
                "name": item.get("title"),
                "start_date": item.get("startTime"),
                "end_date": item.get("endTime"),
                "venue": item.get("venue") or item.get("cityName"),
                "url": "%s/%s" % (self.SITE, item["id"]) if item.get("id") else "",
                "note": note,
            }, self.name, default_category="B", fallback_url=self.SITE)
            if ev:
                events.append(ev)
        return SourceResult(self.name, True, len(events), events,
                            note="接口 %d 条, %s 线下未结束 %d 条" % (
                                len(items), self.city_name, len(events)),
                            raw_bytes=raw)


class CsdnCalendarSource:
    """CSDN 首页「活动日历」(event-calendar): SSR JSON, 无需登录无签名。

    字段极干净(ext.event_date 是 MM-DD, ext.label 是城市), 直接代码映射, 不过 LLM。
    数量少(通常 2-5 条), 但都是 CSDN 运营人工挂上去的活动, 信噪比极高。

    URL 是外链(活动行/主办方官网), 日期只到"月-日"——年份由代码补(雷达只看未来)。
    """

    name = "csdncalendar"
    kind = "J"
    HOME = "https://www.csdn.net/"

    def __init__(self, url, cfg, fetcher, log=print):
        self.url = url
        self.cfg = cfg
        self.fetcher = fetcher
        self.log = log
        city = str(cfg.get("city", "") or "北京").strip()
        if len(city) > 2 and city.endswith("市"):
            city = city[:-1]
        self.city_name = city

    def fetch(self):
        from sources.base import extract_initial_state
        res = self.fetcher.get(self.HOME,
                               headers={"Referer": "https://www.csdn.net/"})
        if not res.ok:
            return SourceResult(self.name, False,
                                err=res.err or ("HTTP %s" % res.status))
        st = extract_initial_state(res.text)
        items = []
        if st:
            try:
                items = (((st.get("pageData") or {}).get("data") or {})
                         .get("event-calendar") or [])
            except Exception:
                items = []
        if not isinstance(items, list) or not items:
            return SourceResult(self.name, False,
                                err="首页未找到 event-calendar 组件(页面结构可能已改版)",
                                raw_bytes=len(res.text or ""))

        today = datetime.date.today()
        events = []
        for it in items:
            if not isinstance(it, dict):
                continue
            ext = it.get("ext") or {}
            md = str(ext.get("event_date") or "")
            m = re.match(r"^(\d{1,2})[-/月](\d{1,2})", md)
            if not m:
                continue
            try:
                d = datetime.date(today.year, int(m.group(1)), int(m.group(2)))
            except ValueError:
                continue
            # 只有 MM-DD: 已过去 60 天以上的认为是明年(跨年), 与 coerce_event 补年规则一致
            if d < today - datetime.timedelta(days=60):
                try:
                    d = d.replace(year=d.year + 1)
                except ValueError:
                    continue
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            url = str(it.get("url") or "").strip()
            note = "CSDN 活动日历"
            label = str(ext.get("label") or "").strip()
            venue = label or "见链接"
            # 活动日历多为全国运营位; 城市标签与当前城市无关时也保留(note 里注明城市),
            # 因为链接里常有报名入口, 城市过滤交给上层 LLM 去重/过滤
            if label and self.city_name not in (label + venue):
                note += " · %s" % label
            ev = coerce_event({
                "name": title,
                "start_date": d.isoformat(),
                "venue": venue,
                "url": url,
                "note": note,
            }, self.name, default_category="B", fallback_url=self.HOME)
            if ev:
                events.append(ev)
        return SourceResult(self.name, True, len(events), events,
                            note="首页 event-calendar %d 条" % len(events),
                            raw_bytes=len(res.text or ""))
