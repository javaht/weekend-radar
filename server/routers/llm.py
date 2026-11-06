# -*- coding: utf-8 -*-
"""LLM 可用模型列表拉取（供设置页「自动获取模型」按钮用）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from core.llm import fetch_model_ids
from server.deps import get_cfg
from server.config_io import MASK, validate_url_value
from server.auth import require_write

router = APIRouter(prefix="/api/llm", tags=["llm"])


class ModelsRequest(BaseModel):
    # 页面上正在编辑但尚未保存的临时覆盖值；"***" 表示沿用磁盘配置
    base_url: Optional[str] = None
    key: Optional[str] = None
    mode: Optional[str] = None
    provider: Optional[str] = None


@router.post("/models", dependencies=[Depends(require_write)])
def list_models(req: ModelsRequest):
    """用「当前配置 + 页面临时覆盖」去网关拉 /models，返回 id 列表。

    GET /config 对 base_url / key 做了掩码，前端回填的就是 "***"；
    这里遇到掩码一律回落到磁盘真实值，绝不会拿 "***" 当 URL 发出去。
    用户显式改过的明文值才覆盖，这样编辑中未保存也能立刻试。
    """
    cfg, _ = get_cfg()
    llm = dict(cfg["llm"])

    for field in ("base_url", "key", "mode", "provider"):
        val = getattr(req, field, None)
        if val is None:
            continue
        val = str(val).strip()
        if val == MASK or not val:
            # 掩码 / 空值 = 沿用已保存的真实配置
            continue
        if field == "base_url":
            try:
                validate_url_value("llm", field, val)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        llm[field] = val

    tmp = dict(cfg)
    tmp["llm"] = llm
    ids, note = fetch_model_ids(tmp, timeout=12)
    return {
        "ok": bool(ids),
        "models": ids,
        "count": len(ids),
        "current": str(llm.get("model") or ""),
        "note": note,
    }
