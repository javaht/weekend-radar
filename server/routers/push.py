# -*- coding: utf-8 -*-
"""推送通道测试与消息触发接口。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from core.push import send as push_send, SUPPORTED_CHANNELS
from server.deps import get_cfg
from server.logger import bus_logger

router = APIRouter(prefix="/api/push", tags=["push"])


class PushTestRequest(BaseModel):
    channel: str
    title: Optional[str] = "周末活动雷达 - 测试推送"
    body: Optional[str] = "这是一条来自 Weekend Radar 控制台的测试通知。通道配置正常！"
    webhook: Optional[str] = None
    secret: Optional[str] = None
    pushkey: Optional[str] = None


@router.get("/channels")
def list_supported_channels():
    return {"channels": list(SUPPORTED_CHANNELS)}


@router.post("/test")
def test_push_channel(req: PushTestRequest):
    cfg, _ = get_cfg()
    pcfg = {s: dict(kv) for s, kv in cfg.items()}

    # 覆盖测试参数
    pcfg["push"]["channel"] = req.channel
    if req.webhook:
        pcfg["push"]["webhook"] = req.webhook
        if req.channel == "feishu":
            pcfg["push"]["feishu_webhook"] = req.webhook
        elif req.channel == "wecom":
            pcfg["push"]["wecom_webhook"] = req.webhook
        elif req.channel == "dingtalk":
            pcfg["push"]["dingtalk_webhook"] = req.webhook
    if req.secret:
        pcfg["push"]["secret"] = req.secret
        if req.channel == "feishu":
            pcfg["push"]["feishu_secret"] = req.secret
        elif req.channel == "dingtalk":
            pcfg["push"]["dingtalk_secret"] = req.secret
    if req.pushkey:
        pcfg["push"]["pushkey"] = req.pushkey
        if req.channel == "bark":
            pcfg["push"]["bark_key"] = req.pushkey
        elif req.channel == "serverchan":
            pcfg["push"]["serverchan_sendkey"] = req.pushkey

    bus_logger.log(f"触发通道测试: {req.channel}")
    res = push_send(pcfg, req.title, req.body, log=bus_logger.log)

    return {
        "ok": res.ok,
        "status": res.status,
        "channel": res.channel,
        "body": res.body,
        "err": res.err,
    }
