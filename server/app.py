# -*- coding: utf-8 -*-
"""FastAPI 主应用入口。"""
import os
import sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core.config import abspath
from server.routers import events, radar, config, push, llm
from server.logger import bus_logger
from server.auth import allowed_origins

app = FastAPI(
    title="Weekend Radar Api",
    description="多城市线下活动情报与差分引擎 Web 控制台",
    version="1.0.0",
)

# CORS：服务默认只绑 127.0.0.1，这里也只放行本机来源；
# 绝不配 allow_origins=["*"] + allow_credentials=True（会把任意站点回显成可信来源）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _on_startup():
    # run_in_executor 里的任务在工作线程打日志，需要主 loop 才能线程安全地投递到 SSE 队列
    import asyncio
    bus_logger.set_loop(asyncio.get_running_loop())


app.include_router(events.router)
app.include_router(radar.router)
app.include_router(config.router)
app.include_router(push.router)
app.include_router(llm.router)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "app": "weekend-radar"}


# 静态前端资源挂载
web_dist = abspath("web/dist")
docs_dir = abspath("docs")

if os.path.isdir(web_dist):
    app.mount("/", StaticFiles(directory=web_dist, html=True), name="static_web")
elif os.path.isdir(docs_dir):
    app.mount("/", StaticFiles(directory=docs_dir, html=True), name="static_docs")
