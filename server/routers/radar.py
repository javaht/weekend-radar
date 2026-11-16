# -*- coding: utf-8 -*-
"""雷达任务执行、逐源健康探测、离线单测与 SSE 实时日志。"""
import asyncio
import datetime
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import run
from server.deps import (
    task_lock, task_status, update_task_status, get_cfg,
    clear_stop, request_stop, stop_requested,
)
from server.logger import bus_logger
from server.auth import require_write
from tools.probe import run_probe
from tests.test_offline import self_test

router = APIRouter(prefix="/api/radar", tags=["radar"])


class RunTaskRequest(BaseModel):
    city: Optional[str] = None
    dry_run: bool = False
    force_push: bool = False
    channel: Optional[str] = None


class PlanTaskRequest(BaseModel):
    city: Optional[str] = None


class ProbeTaskRequest(BaseModel):
    city: Optional[str] = None


# 写操作必须过鉴权；日志流 / 状态是只读，单独放行（不挂在 router 级）
WRITE_DEPS = [Depends(require_write)]


@router.get("/status")
def get_status():
    # stopping：已发停止请求但当前源还没跑完。前端用它把状态从
    # 「RUN 中」切成「停止中」，避免看起来还在正常执行。
    return {**task_status, "stopping": bool(task_status.get("running") and stop_requested())}


@router.get("/logs/stream")
async def stream_logs():
    q = bus_logger.subscribe()

    async def event_generator():
        try:
            while True:
                # 持续监听队列新日志
                try:
                    line = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {line}\n\n"
                except asyncio.TimeoutError:
                    # 心跳保持连接
                    yield ": ping\n\n"
        finally:
            bus_logger.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/logs/clear", dependencies=WRITE_DEPS)
def clear_logs():
    bus_logger.clear_history()
    return {"ok": True}


async def _execute_run_async(city: Optional[str], dry_run: bool, force_push: bool, channel: Optional[str]):
    async with task_lock:
        update_task_status(True, "run", city)
        try:
            cfg, src_rows = get_cfg(city)
            if channel:
                cfg["push"]["channel"] = channel

            # 执行 run.do_run，将日志重定向到 bus_logger
            orig_log = run.log
            run.log = bus_logger.log
            try:
                loop = asyncio.get_event_loop()
                code = await loop.run_in_executor(
                    None,
                    lambda: run.do_run(cfg, src_rows, dry_run=dry_run,
                                       force_push=force_push, stop_check=stop_requested)
                )
                res = {"exit_code": code, "success": code == run.EXIT_OK,
                       "stopped": code == run.EXIT_STOPPED}
            finally:
                run.log = orig_log
            update_task_status(False, result=res)
        except Exception as e:
            bus_logger.log(f"[异常] 任务执行失败: {e}")
            update_task_status(False, result={"exit_code": -1, "error": str(e), "success": False})


async def _execute_plan_async(city: Optional[str]):
    async with task_lock:
        update_task_status(True, "plan", city)
        try:
            cfg, src_rows = get_cfg(city)
            orig_log = run.log
            run.log = bus_logger.log
            try:
                loop = asyncio.get_event_loop()
                code = await loop.run_in_executor(
                    None,
                    lambda: run.do_plan(cfg, src_rows)
                )
                res = {"exit_code": code, "success": code == run.EXIT_OK}
            finally:
                run.log = orig_log
            update_task_status(False, result=res)
        except Exception as e:
            bus_logger.log(f"[异常] Plan 执行失败: {e}")
            update_task_status(False, result={"exit_code": -1, "error": str(e), "success": False})


@router.post("/run", dependencies=WRITE_DEPS)
async def trigger_run(req: RunTaskRequest, bg_tasks: BackgroundTasks):
    if task_lock.locked() or task_status["running"]:
        raise HTTPException(status_code=409, detail="已有正在运行的任务，请等待其完成")
    # 清掉上一轮可能残留的停止标记，避免新任务刚起就被中止
    clear_stop()
    bg_tasks.add_task(_execute_run_async, req.city, req.dry_run, req.force_push, req.channel)
    return {"message": "任务已触发", "city": req.city, "dry_run": req.dry_run, "force_push": req.force_push}


@router.post("/stop", dependencies=WRITE_DEPS)
def stop_run():
    """请求中止当前运行任务。

    ★ 只置协作式停止标记：do_run 在采集循环的源之间检查，
      当前源跑完就退出，并且**放弃本轮入库**（残缺数据会被误判成取消）。
    """
    if not task_status["running"] or task_status.get("task_type") != "run":
        raise HTTPException(status_code=409, detail="当前没有可停止的运行任务")
    request_stop()
    bus_logger.log("[停止] 已收到停止请求，当前数据源结束后中止（本轮不入库、不推送）")
    return {"message": "已请求停止，当前数据源结束后中止"}


@router.post("/plan", dependencies=WRITE_DEPS)
async def trigger_plan(req: PlanTaskRequest, bg_tasks: BackgroundTasks):
    if task_lock.locked() or task_status["running"]:
        raise HTTPException(status_code=409, detail="已有正在运行的任务，请等待其完成")
    bg_tasks.add_task(_execute_plan_async, req.city)
    return {"message": "Plan 试跑已触发", "city": req.city}


@router.post("/probe", dependencies=WRITE_DEPS)
async def trigger_probe(req: ProbeTaskRequest):
    if task_lock.locked():
        raise HTTPException(status_code=409, detail="已有任务占用锁，请稍候")
    async with task_lock:
        cfg, src_rows = get_cfg(req.city)
        today, days, window_end = run._window(cfg)
        city = cfg["radar"].get("city", "北京")
        bus_logger.log("模式: Web 触发 --probe（逐源健康探测） 城市: %s" % city)

        orig_log = run.log
        run.log = bus_logger.log
        try:
            loop = asyncio.get_event_loop()
            def _run():
                _, llm, sources = run._build(cfg, src_rows, today, window_end, include_disabled=True)
                llm_rows = llm.probe()
                rows, ok_n, fail_n = run_probe(sources, llm, bus_logger.log)
                return {
                    "city": city,
                    "ok_count": ok_n,
                    "fail_count": fail_n,
                    "total": len(rows),
                    "sources": rows,
                    "llm_probe": [{"protocol": p, "ok": ok, "note": note} for p, ok, note in llm_rows]
                }
            result = await loop.run_in_executor(None, _run)
            return result
        finally:
            run.log = orig_log


@router.post("/self-test", dependencies=WRITE_DEPS)
async def trigger_self_test():
    if task_lock.locked():
        raise HTTPException(status_code=409, detail="已有任务占用锁，请稍候")
    async with task_lock:
        bus_logger.log("模式: Web 触发 --self-test（离线单元断言自测）")
        loop = asyncio.get_event_loop()
        code = await loop.run_in_executor(None, lambda: self_test(log=bus_logger.log))
        return {"exit_code": code, "success": code == 0}
