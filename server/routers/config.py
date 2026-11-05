# -*- coding: utf-8 -*-
"""配置查看与在线修改、城市/模型预设字典。"""
import configparser
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any

from core.config import CITY_PRESETS, abspath
from core.llm import PROVIDER_PRESETS
from core.genre import GENRES, parse_disabled, dump_genres
from core.models import PRICE_TIERS, PRICE_TIER_LABELS
from server.deps import get_cfg

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


@router.put("/genres")
def update_genres(req: GenrePrefsRequest):
    """只改 [push] disabled_genres 这一行，不碰其他配置。"""
    from server.deps import get_cfg as _get_cfg
    import configparser
    ini_path = abspath("config.ini")
    p = configparser.ConfigParser(interpolation=None)
    if os.path.exists(ini_path):
        p.read(ini_path, encoding="utf-8")
    if not p.has_section("push"):
        p.add_section("push")
    unknown = [g for g in req.disabled if g not in GENRES]
    if unknown:
        raise HTTPException(status_code=400, detail="未知品类: %s" % ", ".join(unknown))
    p.set("push", "disabled_genres", dump_genres(set(req.disabled)))

    tmp = ini_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        p.write(f)
    os.replace(tmp, ini_path)

    cfg, _ = _get_cfg()
    disabled = parse_disabled(cfg["push"].get("disabled_genres", ""))
    return {
        "message": "品类推送偏好已保存",
        "genres": [{"id": k, "label": v, "enabled": k not in disabled}
                   for k, v in GENRES.items()],
    }


@router.get("/config")
def get_config():
    cfg, sources = get_cfg()
    return {
        "sections": cfg,
        "sources": sources,
    }


@router.put("/config")
def update_config(req: UpdateConfigRequest):
    """在线修改 config.ini —— 原地改值, 不重写整个文件。

    ★ 为什么不用 configparser 的 write():
      它会丢掉全部注释, 且把 [sources] 里的 "H|on |url" 值里的空格吃掉。
      这个文件里有大量说明性注释(占 78 行), 一次网页保存就全没了。
      所以这里改成逐行替换: 只改目标键的值, 其余行原样保留。
    """
    ini_path = abspath("config.ini")
    if not os.path.exists(ini_path):
        raise HTTPException(status_code=500, detail="config.ini 不存在")

    # 把要改的键收集成 {section: {key: value}}
    changes = {}
    for sec_name, kv in (req.sections or {}).items():
        if sec_name == "sources":
            continue
        sec = changes.setdefault(sec_name, {})
        for k, v in kv.items():
            if v is not None:
                sec[k] = str(v)
    if req.sources is not None:
        sec = changes.setdefault("sources", {})
        for k, v in req.sources.items():
            if v is not None:
                sec[k] = str(v)

    with open(ini_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    current_sec = None
    seen = {s: set() for s in changes}   # 每个段里已改到哪些键
    out = []
    for raw in lines:
        stripped = raw.strip()
        # 段头
        if stripped.startswith("[") and stripped.endswith("]"):
            current_sec = stripped[1:-1]
            out.append(raw)
            continue
        # 注释/空行原样保留
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            out.append(raw)
            continue
        if "=" not in raw or current_sec is None:
            out.append(raw)
            continue
        key = raw.split("=", 1)[0].strip()
        if current_sec in changes and key in changes[current_sec]:
            val = changes[current_sec][key]
            # 保留行尾注释: "key = value  # 说明"
            tail = ""
            if "#" in raw and not raw.strip().startswith("#"):
                head, tail = raw.split("#", 1)
                # [sources] 的 URL 里不会有 #, 但保险起见只对非 sources 段保留行尾注释
                if current_sec == "sources":
                    tail = ""
                else:
                    tail = "  #" + tail.rstrip("\n")
            out.append("%s = %s%s\n" % (key, val, tail))
            seen[current_sec].add(key)
            continue
        out.append(raw)

    # 新键(原文件里没有的)追加到对应段末尾
    for sec, kv in changes.items():
        missing = [k for k in kv if k not in seen[sec]]
        if not missing:
            continue
        appended = False
        for i, raw in enumerate(out):
            if raw.strip() == "[%s]" % sec:
                # 找到该段的最后一行内容位置
                j = i + 1
                last = i
                while j < len(out) and not out[j].strip().startswith("["):
                    if out[j].strip() and not out[j].strip().startswith("#"):
                        last = j
                    j += 1
                for k in missing:
                    out.insert(last + 1, "%s = %s\n" % (k, kv[k]))
                    last += 1
                appended = True
                break
        if not appended:
            out.append("\n[%s]\n" % sec)
            for k in missing:
                out.append("%s = %s\n" % (k, kv[k]))

    tmp_path = ini_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(out)
        os.replace(tmp_path, ini_path)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise HTTPException(status_code=500, detail=f"写入 config.ini 失败: {e}")

    new_cfg, new_sources = get_cfg()
    return {
        "message": "配置更新成功",
        "sections": new_cfg,
        "sources": new_sources,
    }
