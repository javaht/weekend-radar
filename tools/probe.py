# -*- coding: utf-8 -*-
"""逐源健康探测 —— 上线前唯一可信的「哪些源能通」依据。

为什么必须有这个模式:
  方案里我能查到的都是"文档说有这个接口", 但站点是否可达、接口是否改版,
  只有从目标机器上真实打一发才知道。probe 输出红绿表, 由人决定某个源去留。

只做 GET, 不调 LLM —— 让探测本身便宜且快。
"""
from core.config import parse_source_row


def probe_source(src):
    """对单个源做一次轻量探测, 不触发 LLM。"""
    if hasattr(src, "probe"):
        try:
            return src.probe()
        except Exception as e:
            return {"ok": False, "status": 0, "bytes": 0,
                    "note": "%s: %s" % (type(e).__name__, e)}
    # JSON 源: fetch 本身不依赖 LLM, 直接跑
    try:
        res = src.fetch()
        return {"ok": res.ok, "status": 200 if res.ok else 0,
                "bytes": res.raw_bytes,
                "note": ("%d 条 %s" % (res.count, res.note)).strip() if res.ok else res.err}
    except Exception as e:
        return {"ok": False, "status": 0, "bytes": 0,
                "note": "%s: %s" % (type(e).__name__, e)}


def run_probe(sources, llm, log=print):
    """返回 (rows, ok_count, fail_count)。"""
    rows = []
    for src in sources:
        name = getattr(src, "name", "?")
        enabled = getattr(src, "enabled", True)
        log("  [探测] %s %s ..." % (name, "（未启用，仅探测）" if not enabled else ""))
        r = probe_source(src)
        rows.append({
            "name": name,
            "enabled": enabled,
            "ok": bool(r.get("ok")),
            "status": r.get("status", 0),
            "bytes": r.get("bytes", 0),
            "note": r.get("note", ""),
            "url": getattr(src, "url", ""),
        })
    ok = sum(1 for r in rows if r["ok"])
    fail = len(rows) - ok
    return rows, ok, fail


def format_probe(rows, llm_rows):
    """生成给人看的红绿表。"""
    L = []
    L.append("源健康表")
    L.append("")
    L.append("| 源 | 启用 | 状态 | HTTP | 字节 | 说明 |")
    L.append("|---|---|---|---|---|---|")
    for r in rows:
        mark = "✅" if r["ok"] else "❌"
        L.append("| %s | %s | %s | %s | %s | %s |" % (
            r["name"], "on" if r["enabled"] else "off", mark,
            r["status"] or "—", r["bytes"] or "—",
            (r["note"] or "").replace("|", "｜")[:80]))
    L.append("")
    L.append("LLM 网关协议")
    L.append("")
    L.append("| 协议 | 结果 | 说明 |")
    L.append("|---|---|---|")
    for proto, ok, note in (llm_rows or []):
        L.append("| %s | %s | %s |" % (proto, "✅" if ok else "❌",
                                       (note or "").replace("|", "｜")[:90]))
    if not llm_rows:
        L.append("| — | — | 未探测 |")
    L.append("")
    return "\n".join(L)
