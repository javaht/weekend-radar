# -*- coding: utf-8 -*-
"""配置查看与在线修改、城市/模型预设字典。"""
import os
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any

from core.config import CITY_PRESETS, abspath
from core.llm import PROVIDER_PRESETS
from core.genre import GENRES, parse_disabled, dump_genres
from core.models import PRICE_TIERS, PRICE_TIER_LABELS
from server.deps import get_cfg
from server.config_io import (
    LOCAL_HEADER, mask_sections, replace_ini_lines,
    split_changes, validate_url_value,
)
from server.auth import require_write

router = APIRouter(prefix="/api", tags=["config"])


class UpdateConfigRequest(BaseModel):
    sections: Dict[str, Dict[str, Any]]
    sources: Dict[str, str] = None


@router.get("/presets")
def get_presets():
    cities = []
    for name, p in CITY_PRESETS.items():
        cities.append({
            "name": name,
            "adcode": p.get("adcode", ""),
            "damai_city": p.get("damai_city", ""),
            "bendibao_city": p.get("bendibao_city", ""),
            "douban_city": p.get("douban_city", ""),
        })

    providers = []
    for name, p in PROVIDER_PRESETS.items():
        providers.append({
            "name": name,
            "base_url": p.get("base_url", ""),
            "mode": p.get("mode", ""),
            "model": p.get("model", ""),
        })

    return {
        "cities": cities,
        "providers": providers,
        "genres": [{"id": k, "label": v} for k, v in GENRES.items()],
    }


@router.get("/price-tiers")
def get_price_tiers():
    """价格档位目录。前端筛选 pill 用。"""
    return {"tiers": [{"id": t, "label": PRICE_TIER_LABELS[t]} for t in PRICE_TIERS]}


@router.get("/genres")
def get_genres():
    """品类目录 + 当前哪些品类被关掉了（前端勾选框用）。"""
    cfg, _ = get_cfg()
    disabled = parse_disabled(cfg["push"].get("disabled_genres", ""))
    return {
        "genres": [{"id": k, "label": v, "enabled": k not in disabled}
                   for k, v in GENRES.items()],
    }


class GenrePrefsRequest(BaseModel):
    disabled: list[str] = []


@router.put("/genres", dependencies=[Depends(require_write)])
def update_genres(req: GenrePrefsRequest):
    """只改 [push] disabled_genres 这一行，不碰其他配置。"""
    unknown = [g for g in req.disabled if g not in GENRES]
    if unknown:
        raise HTTPException(status_code=400, detail="未知品类: %s" % ", ".join(unknown))
    # ★ 不能用 configparser.write()：会抹掉全部注释并压缩 [sources] 空格
    replace_ini_lines(
        abspath("config.ini"),
        {"push": {"disabled_genres": dump_genres(set(req.disabled))}},
    )
    cfg, _ = get_cfg()
    disabled = parse_disabled(cfg["push"].get("disabled_genres", ""))
    return {
        "message": "品类推送偏好已保存",
        "genres": [{"id": k, "label": v, "enabled": k not in disabled}
                   for k, v in GENRES.items()],
    }


def _config_payload(cfg, sources):
    """GET 与 PUT 共用的返回结构，保证前端拿到的 sources 始终是未渲染模板。

    ★ 若这里返回渲染后的 sources（占位符已替换成当前城市），前端回填后
      下一次自动保存就会把模板永久固化，13 城联动直接失效。
    """
    sections = mask_sections(cfg)
    # extra.city 是运行时派生的城市镜像，不是磁盘配置项
    sections.get("extra", {}).pop("city", None)
    return {
        "sections": sections,
        "sources": dict(getattr(sources, "templates", None) or sources),
    }


@router.get("/config")
def get_config():
    cfg, sources = get_cfg()
    return {"message": "ok", **_config_payload(cfg, sources)}


@router.get("/config/secret", dependencies=[Depends(require_write)])
def reveal_secret(section: str = "llm", key: str = "key"):
    """显式读取单个密钥字段的真实值，供设置页「显示」按钮使用。

    GET /config 一律掩码，但用户点「显示」时确实需要看到自己配的值。
    这是独立且受写保护的接口，避免为了一个显示动作把整份配置明文化。
    """
    cfg, _ = get_cfg()
    val = str((cfg.get(section) or {}).get(key, "") or "")
    if not val:
        return {"value": "", "configured": False}
    return {"value": val, "configured": True}


@router.put("/config", dependencies=[Depends(require_write)])
def update_config(req: UpdateConfigRequest):
    """在线修改配置 —— 原地逐行改值，保留注释与格式。

    ★ 两个关键约束：
      - 私有值（真实 Key / 内网网关 / 各渠道密钥）只写 config.local.ini，
        绝不写回被 git 跟踪的 config.ini；前端回填的 "***" 表示未修改，跳过。
      - 不用 configparser.write()，否则一次网页保存就毁掉 83 行注释。
    """
    ini_path = abspath("config.ini")
    if not os.path.exists(ini_path):
        raise HTTPException(status_code=500, detail="config.ini 不存在")
    local_path = os.path.splitext(ini_path)[0] + ".local.ini"

    changes = {}
    for sec_name, kv in (req.sections or {}).items():
        if sec_name == "sources":
            continue
        sec = changes.setdefault(sec_name, {})
        for k, v in kv.items():
            # extra.city 是运行时派生的城市镜像，不是磁盘配置项；
            # 写回去会在 [extra] 多出一行 city，且随每次切城市被改。
            if sec_name == "extra" and k == "city":
                continue
            if v is not None:
                sec[k] = str(v)
    if req.sources is not None:
        sec = changes.setdefault("sources", {})
        for k, v in req.sources.items():
            if v is not None:
                sec[k] = str(v)

    # 校验 URL 类字段（挡 file://、javascript: 等非 http 协议）
    for sec_name, kv in changes.items():
        for k, v in kv.items():
            try:
                validate_url_value(sec_name, k, v)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

    public, private = split_changes(changes, ini_path)
    try:
        replace_ini_lines(ini_path, public)
        if private:
            replace_ini_lines(local_path, private, create_header=LOCAL_HEADER)
    except Exception as e:
        raise HTTPException(status_code=500, detail="写入配置失败: %s" % e)

    new_cfg, new_sources = get_cfg()
    return {
        "message": "配置更新成功",
        **_config_payload(new_cfg, new_sources),
    }
