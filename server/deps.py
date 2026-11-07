# -*- coding: utf-8 -*-
"""共享依赖、互斥锁与状态管理。"""
import asyncio
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core.config import load_config, apply_env_and_city, abspath, as_int, csv_list
from core.store import Store
from server.logger import bus_logger

task_lock = asyncio.Lock()

task_status = {
    "running": False,
    "task_type": None,
    "city": None,
    "started_at": None,
    "last_result": None,
}


def get_cfg(city_override=None):
    cfg, src_rows = load_config(abspath("config.ini"))
    if city_override:
        apply_env_and_city(cfg, src_rows, cli_overrides={"city": city_override})
    return cfg, src_rows


def get_store(cfg=None):
    if cfg is None:
        cfg, _ = get_cfg()
    db_path = abspath(cfg["radar"].get("db", "data/radar.sqlite"))
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return Store(
        db_path,
        bus_logger.log,
        gone_after=as_int(cfg, "radar", "gone_after_misses", 2),
        stable_sources=csv_list(cfg, "radar", "stable_sources", "bili"),
        confirm_runs=as_int(cfg, "radar", "confirm_runs", 2),
    )


def update_task_status(running: bool, task_type: str = None, city: str = None, result: dict = None):
    task_status["running"] = running
    task_status["task_type"] = task_type if running else None
    task_status["city"] = city if running else None
    task_status["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S") if running else task_status["started_at"]
    if result is not None:
        task_status["last_result"] = result
