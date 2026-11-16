# -*- coding: utf-8 -*-
"""进程内定时任务：按 data/schedules.json 里的条目，每天 / 每周固定时间触发正式执行。

为什么不用系统 crontab：
  Web 控制台本来就要求服务常驻（--serve / Docker），进程内调度不依赖
  用户是否装过 cron、也不受 macOS/Linux 差异影响；代价是服务停了就不会触发。

★ 单实例假设：uvicorn 单 worker。多 worker 部署会每个进程各触发一次。
★ 存储：data/schedules.json（运行时状态，不进 git）。一条 = 一个城市的一个时间点。
"""
import asyncio
import datetime
import json
import os
import uuid

from core.config import abspath
from server.deps import get_cfg, task_lock, task_status
from server.logger import bus_logger

TICK_SECONDS = 20
_SCHEDULES_FILE = abspath("data/schedules.json")
_STATE_FILE = abspath("data/schedule_last.json")


def _parse_hhmm(text):
    try:
        h, m = str(text or "").strip().split(":")[:2]
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except Exception:
        pass
    return 9, 0


def _parse_days(text):
    days = set()
    for part in str(text or "").split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= 7:
            days.add(int(part))
    return days or set(range(1, 8))


def normalize_item(raw, cfg=None):
    """把一条定时（来自 JSON / 旧 [schedule] / 接口）规整成内部结构。"""
    hour, minute = _parse_hhmm(raw.get("time", "09:00"))
    mode = str(raw.get("mode", "daily")).strip().lower()
    if mode not in ("daily", "weekly"):
        mode = "daily"
    weekdays = raw.get("weekdays")
    if isinstance(weekdays, (list, tuple)):
        days = {int(d) for d in weekdays if str(d).isdigit() and 1 <= int(d) <= 7}
    else:
        days = _parse_days(weekdays)
    if mode == "daily":
        days = set(range(1, 8))
    city = str(raw.get("city", "") or "").strip()
    item = {
        "id": str(raw.get("id") or uuid.uuid4().hex[:8]),
        "mode": mode,
        "time": "%02d:%02d" % (hour, minute),
        "weekdays": sorted(days),
        "city": city,
    }
    # 调度实际使用的城市：空 = 跟随 config 的 radar.city
    item["eff_city"] = city or str(((cfg or {}).get("radar") or {}).get("city", "北京") or "北京")
    return item


def _load_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_schedules(cfg=None, persist_migration=True):
    """读取全部定时；文件不存在时从旧 [schedule] 段迁移一次。"""
    if cfg is None:
        cfg, _ = get_cfg()
    raw = _load_json(_SCHEDULES_FILE, None)
    if raw is None:
        # 旧版单条 [schedule] → 新列表（仅当它当时是启用的）
        legacy = (cfg.get("schedule") or {})
        enabled = str(legacy.get("enabled", "false")).strip().lower() in ("1", "true", "yes", "on")
        raw = []
        if enabled:
            raw = [{
                "mode": legacy.get("mode", "daily"),
                "time": legacy.get("time", "09:00"),
                "weekdays": legacy.get("weekdays", ""),
                "city": legacy.get("city", ""),
            }]
        if persist_migration:
            try:
                _save_json(_SCHEDULES_FILE, raw)
            except Exception as e:
                bus_logger.log("[定时] 初始化 schedules.json 失败: %s" % e)
    items = [normalize_item(x, cfg) for x in raw if isinstance(x, dict)]
    return items


def save_schedules(items, cfg=None):
    if cfg is None:
        cfg, _ = get_cfg()
    clean = [normalize_item(x, cfg) for x in items]
    for x in clean:
        x.pop("eff_city", None)
    _save_json(_SCHEDULES_FILE, clean)
    return [normalize_item(x, cfg) for x in clean]


def next_run(item, now=None):
    """该条定时的下一次触发时间；没有下一次返回 None。"""
    now = now or datetime.datetime.now()
    hour, minute = _parse_hhmm(item.get("time"))
    mode = item.get("mode", "daily")
    days = set(item.get("weekdays") or [])
    for i in range(0, 9):
        day = (now + datetime.timedelta(days=i)).date()
        if mode == "weekly" and day.isoweekday() not in days:
            continue
        cand = datetime.datetime.combine(day, datetime.time(hour, minute))
        if cand > now:
            return cand
    return None


def _matches(item, now):
    hour, minute = _parse_hhmm(item.get("time"))
    if (now.hour, now.minute) != (hour, minute):
        return False
    if item.get("mode") == "weekly" and now.isoweekday() not in set(item.get("weekdays") or []):
        return False
    return True


class Scheduler:
    def __init__(self):
        self._task = None
        # {schedule_id: "YYYY-MM-DD HH:MM"}，持久化避免重启后同一分钟重复触发
        self._last = _load_json(_STATE_FILE, {}) or {}

    def start(self, loop):
        if self._task and not self._task.done():
            return
        self._task = loop.create_task(self._loop())

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self):
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                bus_logger.log("[定时] 调度异常: %s" % e)
            await asyncio.sleep(TICK_SECONDS)

    async def _tick(self):
        cfg, _ = get_cfg()
        items = load_schedules(cfg)
        now = datetime.datetime.now()
        stamp = now.strftime("%Y-%m-%d %H:%M")
        for item in items:
            if not _matches(item, now):
                continue
            if self._last.get(item["id"]) == stamp:
                continue
            self._last[item["id"]] = stamp
            try:
                _save_json(_STATE_FILE, self._last)
            except Exception as e:
                bus_logger.log("[定时] 写入状态失败: %s" % e)

            if task_lock.locked() or task_status.get("running"):
                bus_logger.log("[定时] %s 命中（%s），但已有任务在跑，本轮跳过" % (stamp, item["eff_city"]))
                continue
            bus_logger.log("[定时] %s 触发：城市 %s，%s %s" % (
                stamp, item["eff_city"],
                "每周" + ",".join(str(d) for d in item["weekdays"])
                if item["mode"] == "weekly" else "每天",
                item["time"]))
            # 延迟导入：radar 路由在模块级导入本文件，避免循环依赖
            from server.routers.radar import _execute_run_async
            asyncio.create_task(_execute_run_async(item["eff_city"], False, False, None))


scheduler = Scheduler()
