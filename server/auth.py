# -*- coding: utf-8 -*-
"""写接口的最小防护：绑定面判断 + token 校验 + 跨站 Origin 校验。

服务默认只绑 127.0.0.1（见 run.py --host），本机浏览器直连即可零配置使用。
三层防护各管一件事：

1. 默认只绑本机 —— 收掉最主要的暴露面；
2. 对外暴露时必须带 RADAR_WRITE_TOKEN，否则写接口一律 403；
3. 即使绑在本机，也挡住「恶意网页跨站打 127.0.0.1」：写请求带 Origin
   且不是本机来源时直接拒绝（CORS 只挡读响应，挡不住带副作用的写）。

GET 只读接口不设限，情报页 / SSE 日志流仍可匿名查看。
"""
import hmac
import os
from fastapi import Header, HTTPException, Request

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def allowed_origins():
    """允许访问写接口 / CORS 的来源，app.py 与这里共用同一份白名单。"""
    origins = ["http://127.0.0.1:8000", "http://localhost:8000"]
    extra = os.environ.get("RADAR_CORS_ORIGINS", "").strip()
    if extra:
        origins += [o.strip() for o in extra.split(",") if o.strip()]
    return origins


def writes_are_public():
    """是否处于「可能被非本机访问」的部署形态，需要写鉴权。"""
    if os.environ.get("RADAR_SERVE_PUBLIC", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    host = os.environ.get("RADAR_SERVE_HOST", "127.0.0.1").strip()
    return host not in LOCAL_HOSTS


def require_write(request: Request, x_radar_token: str = Header(default="")):
    # 跨站防护：带 Origin 且不是白名单来源，拒掉。curl 等本机命令行不带 Origin，正常放行。
    origin = (request.headers.get("origin") or "").strip()
    if origin and origin.rstrip("/") not in [o.rstrip("/") for o in allowed_origins()]:
        raise HTTPException(status_code=403, detail="跨站来源不允许写操作")

    token = os.environ.get("RADAR_WRITE_TOKEN", "").strip()
    if not token:
        # 没设 token 时，仅在本机绑定下放行；对外暴露且未设 token 直接拒绝，
        # 避免「以为有保护其实没有」。
        if writes_are_public():
            raise HTTPException(
                status_code=403,
                detail="服务已对外暴露但未配置 RADAR_WRITE_TOKEN，写操作已禁用",
            )
        return True
    if not hmac.compare_digest(x_radar_token or "", token):
        raise HTTPException(status_code=403, detail="无效或未提供的写操作 token")
    return True
