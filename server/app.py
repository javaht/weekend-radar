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
from server.routers import events, radar, config, push

app = FastAPI(
    title="Weekend Radar API",
    description="多城市线下活动情报与差分引擎 Web 控制台",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(events.router)
app.include_router(radar.router)
app.include_router(config.router)
app.include_router(push.router)


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
