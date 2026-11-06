# -*- coding: utf-8 -*-
"""配置文件的「读掩码 / 写落盘」公共逻辑。

两个目标：
1. GET 出去的配置不能带真实密钥（Web 控制台未鉴权时尤其致命）；
2. 网页保存配置不能把私有值写进 git 跟踪的 config.ini，也不能用
   configparser.write() 抹掉 83 行注释、吃掉 [sources] 值里的空格。
所以统一走「逐行替换」：只改目标键的值，其余行原样保留。
"""
import configparser
import os

MASK = "***"

# 需要掩码的「凭证类」键。按 (section, key) 精确列，不用模糊匹配，
# 避免把 key_file / endpoint / base_url 这类非凭证项也误伤。
# ★ base_url 只是接口地址，不是密钥，保持明文方便确认与编辑；
#   它是否写进 config.local.ini 由 split_changes 的 local_existing 决定，
#   与这里的掩码名单是两件事。
SENSITIVE_KEYS = {
    ("llm", "key"),
    ("push", "pushkey"),
    ("push", "secret"),
    ("push", "webhook"),
    ("push", "feishu_webhook"),
    ("push", "feishu_secret"),
    ("push", "wecom_webhook"),
    ("push", "wecom_key"),
    ("push", "dingtalk_webhook"),
    ("push", "dingtalk_secret"),
    ("push", "bark_url"),
    ("push", "bark_key"),
    ("push", "telegram_bot_token"),
    ("push", "telegram_chat_id"),
    ("push", "serverchan_sendkey"),
}

LOCAL_HEADER = """# ============================================================
#  本机私有配置覆盖（已被 .gitignore 忽略，绝不提交）
#  这里放真实 API Key、内网网关地址这类不能进公开仓库的值。
#  加载顺序: 内置默认值 → config.ini → config.local.ini（本文件最后覆盖）
# ============================================================

"""


def mask_sections(cfg):
    """返回给前端用的配置副本：敏感值有内容就显示 ***，空值保持空。"""
    out = {}
    for sec, kv in (cfg or {}).items():
        out[sec] = {}
        for k, v in kv.items():
            if (sec, k) in SENSITIVE_KEYS and str(v or "").strip():
                out[sec][k] = MASK
            else:
                out[sec][k] = v
    return out


def _local_keys(path):
    """config.local.ini 里已经存在的键 —— 网页改这些键必须写回本地文件，
    否则会被本地覆盖层盖掉，看起来「保存成功但没生效」。"""
    p = configparser.ConfigParser(interpolation=None)
    try:
        p.read(path, encoding="utf-8")
    except Exception:
        return set()
    return {(sec, k) for sec in p.sections() for k in p.options(sec)}


def split_changes(changes, ini_path):
    """把待写入的键拆成 (公开 config.ini, 私有 config.local.ini) 两份。"""
    local_path = os.path.splitext(ini_path)[0] + ".local.ini"
    local_existing = _local_keys(local_path)
    public, private = {}, {}
    for sec, kv in (changes or {}).items():
        for k, v in kv.items():
            val = str(v)
            # 前端回填的掩码值 = 用户没改，直接跳过，避免把 *** 写进文件
            if val == MASK:
                continue
            target = private if ((sec, k) in SENSITIVE_KEYS or (sec, k) in local_existing) else public
            target.setdefault(sec, {})[k] = val
    return public, private


def replace_ini_lines(path, changes, create_header=""):
    """逐行替换指定键，保留注释/空行/原格式；只改目标键的值。

    返回被替换掉的键集合（调用方可用于诊断）。新键追加到对应段末尾。
    """
    changes = changes or {}
    if not changes:
        return set()
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(create_header)
        lines = []
    else:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    current_sec = None
    seen = {s: set() for s in changes}
    out = []
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_sec = stripped[1:-1]
            out.append(raw)
            continue
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            out.append(raw)
            continue
        if "=" not in raw or current_sec is None:
            out.append(raw)
            continue
        key = raw.split("=", 1)[0].strip()
        if current_sec in changes and key in changes[current_sec]:
            val = str(changes[current_sec][key])
            # configparser 读进来的值本身可能已带行尾注释（如 [sources] 的
            # "url  # 说明"），把它剥掉，注释只从原行提取，避免重复拼接。
            if "  #" in val:
                val = val.split("  #", 1)[0].rstrip()
            elif current_sec != "sources" and "#" in val:
                val = val.split("#", 1)[0].rstrip()
            # 保留原行等号左侧的写法（含 [sources] 的对齐空格），只换值，
            # 否则 "bendibao    = ..." 会被压成 "bendibao = ..."。
            lhs = raw.split("=", 1)[0]
            tail = ""
            # 行尾注释：非 sources 段直接按 # 切；sources 的 URL 里可能含 #(fragment)，
            # 只认 "空格 + #" 这种明确的注释边界，避免把 URL 截断。
            trimmed = raw.rstrip("\n")
            cut = trimmed.find("  #") if current_sec == "sources" else trimmed.find("#")
            if cut > 0:
                head, comment = trimmed[:cut], trimmed[cut + (3 if current_sec == "sources" else 1):]
                tail = "  #" + comment
                lhs = head.split("=", 1)[0]
            # 空值写成 "key ="，不带尾随空格（否则每次保存都产生无意义的 diff）
            out.append("%s=%s%s\n" % (lhs, (" " + val) if val else "", tail))
            seen[current_sec].add(key)
            continue
        out.append(raw)

    for sec, kv in changes.items():
        missing = [k for k in kv if k not in seen[sec]]
        if not missing:
            continue
        appended = False
        for i, raw in enumerate(out):
            if raw.strip() == "[%s]" % sec:
                j = i + 1
                last = i
                while j < len(out) and not out[j].strip().startswith("["):
                    if out[j].strip() and not out[j].strip().startswith("#"):
                        last = j
                    j += 1
                for k in missing:
                    last += 1
                    out.insert(last, "%s = %s\n" % (k, kv[k]))
                appended = True
                break
        if not appended:
            out.append("\n[%s]\n" % sec)
            for k in missing:
                out.append("%s = %s\n" % (k, kv[k]))

    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(out)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    return {(sec, key) for sec, keys in seen.items() for key in keys}


def validate_url_value(section, key, value):
    """写配置时校验 URL 类字段，挡掉 file:// / javascript: 等非 http(s) 协议。

    不按 IP 段封禁：本机 Magic 网关(127.0.0.1:3425) 与 Tailscale 网段
    (100.64.x.x) 都是合法部署形态，一刀切会把正常配置改坏。
    真正的 SSRF 面由「默认只绑 127.0.0.1 + 写接口鉴权」堵住。
    """
    if not (key == "base_url" or "webhook" in key or key.endswith("_url")):
        return
    v = str(value or "").strip()
    if v and not (v.startswith("http://") or v.startswith("https://")):
        raise ValueError("%s.%s 必须是 http:// 或 https:// 开头的地址" % (section, key))
