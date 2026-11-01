# -*- coding: utf-8 -*-
"""多渠道轻量 Webhook 推送模块（零第三方依赖，纯标准库实现）。

内置 7 种交付渠道模板（[push] channel 支持单选或多渠道逗号分隔，如 feishu,bark）:
  1. pushdeer   : PushDeer (endpoint + pushkey)
  2. feishu     : 飞书自定义机器人 Webhook (支持可选 secret 签名校验 + 交互式 Markdown 卡片)
  3. wecom      : 企业微信群机器人 Webhook (支持 webhook URL 或 wecom_key，自动 4096 字节安全截断)
  4. dingtalk   : 钉钉群机器人 Webhook (支持可选 secret 加签 + Markdown / 纯文本)
  5. bark       : Bark iOS 推送 (支持 bark_url 或 bark_key)
  6. telegram   : Telegram Bot API (telegram_bot_token + telegram_chat_id)
  7. serverchan : Server酱 Turbo / Server酱3 (serverchan_sendkey 或自定义 webhook)

错误处理原则:
  失败就如实返回 (ok=False, err=原因), 报告文件照常落盘, 退出码非 0 ——
  让人知道"今天的推送没送到", 而不是以为"今天没活动"。
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

SUPPORTED_CHANNELS = (
    "pushdeer", "feishu", "wecom", "dingtalk", "bark", "telegram", "serverchan"
)


class PushResult:
    __slots__ = ("ok", "status", "body", "err", "channel")

    def __init__(self, ok, status=0, body="", err="", channel="pushdeer"):
        self.ok = ok
        self.status = status
        self.body = body
        self.err = err
        self.channel = channel

    def __repr__(self):
        return "PushResult(channel=%s ok=%s status=%s err=%s)" % (
            self.channel, self.ok, self.status, self.err)


def _truncate_chars(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n…（已截断，完整内容见报告文件）"


def _truncate_utf8_bytes(text, max_bytes=4000):
    """按 UTF-8 字节数安全截断（用于企业微信 4096 字节硬限制），不切碎多字节汉字。"""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    suffix = "\n\n…（已截断，完整内容见报告文件）"
    suffix_bytes = len(suffix.encode("utf-8"))
    cut = raw[:max(100, max_bytes - suffix_bytes)].decode("utf-8", "ignore")
    return cut + suffix


def _http_post(url, data_bytes, content_type="application/json; charset=utf-8", timeout=25, log=print, tag="Push"):
    req = urllib.request.Request(
        url, data=data_bytes,
        headers={"Content-Type": content_type, "User-Agent": "weekend-radar/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return True, r.status, body, ""
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        log("  [!] %s HTTP %s: %s" % (tag, e.code, detail))
        return False, e.code, detail, "HTTP %s" % e.code
    except Exception as e:
        log("  [!] %s 请求异常: %s: %s" % (tag, type(e).__name__, e))
        return False, 0, "", "%s: %s" % (type(e).__name__, e)


def _feishu_sign(timestamp, secret):
    """飞书自定义机器人 HMAC-SHA256 签名：key 为 '{timestamp}\\n{secret}'，msg 为空。"""
    string_to_sign = "%s\n%s" % (timestamp, secret)
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def _dingtalk_signed_url(url, secret):
    """钉钉自定义机器人 HMAC-SHA256 加签参数追加。"""
    timestamp = str(int(round(time.time() * 1000)))
    string_to_sign = "%s\n%s" % (timestamp, secret)
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    sep = "&" if "?" in url else "?"
    return "%s%stimestamp=%s&sign=%s" % (url, sep, timestamp, sign)


# ---------------------------------------------------------------- 各渠道实现

def _send_pushdeer(pcfg, title, desp, push_type, timeout, log):
    endpoint = str(pcfg.get("endpoint", "") or "").strip()
    pushkey = str(pcfg.get("pushkey", "") or "").strip()
    if not endpoint or not pushkey:
        return PushResult(False, err="未配置 push.endpoint / push.pushkey", channel="pushdeer")
    payload = urllib.parse.urlencode({
        "pushkey": pushkey,
        "text": title,
        "desp": desp,
        "type": push_type,
    }).encode("utf-8")
    ok, status, body, err = _http_post(
        endpoint, payload, "application/x-www-form-urlencoded", timeout, log, "PushDeer"
    )
    if not ok:
        return PushResult(False, status, body, err, channel="pushdeer")
    try:
        obj = json.loads(body)
    except Exception:
        return PushResult(False, status, body, "响应不是 JSON", channel="pushdeer")
    if obj.get("code") == 0:
        return PushResult(True, status, body, channel="pushdeer")
    return PushResult(False, status, body, "code=%s" % obj.get("code"), channel="pushdeer")


def _send_feishu(pcfg, title, desp, push_type, timeout, log):
    url = str(pcfg.get("feishu_webhook") or pcfg.get("webhook") or "").strip()
    if not url:
        return PushResult(False, err="未配置 push.feishu_webhook (或 push.webhook)", channel="feishu")
    secret = str(pcfg.get("feishu_secret") or pcfg.get("secret") or "").strip()
    if push_type == "markdown":
        data = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": [
                    {"tag": "markdown", "content": desp},
                ],
            },
        }
    else:
        data = {
            "msg_type": "text",
            "content": {"text": "%s\n\n%s" % (title, desp)},
        }
    if secret:
        ts = str(int(time.time()))
        data["timestamp"] = ts
        data["sign"] = _feishu_sign(ts, secret)
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    ok, status, body, err = _http_post(url, raw, timeout=timeout, log=log, tag="飞书")
    if not ok:
        return PushResult(False, status, body, err, channel="feishu")
    try:
        obj = json.loads(body)
    except Exception:
        return PushResult(False, status, body, "响应不是 JSON", channel="feishu")
    if obj.get("code") == 0 or obj.get("StatusCode") == 0:
        return PushResult(True, status, body, channel="feishu")
    return PushResult(False, status, body, "code=%s msg=%s" % (obj.get("code"), obj.get("msg")), channel="feishu")


def _send_wecom(pcfg, title, desp, push_type, timeout, log):
    url = str(pcfg.get("wecom_webhook") or pcfg.get("webhook") or "").strip()
    wkey = str(pcfg.get("wecom_key") or pcfg.get("pushkey") or "").strip()
    if not url and wkey:
        url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=%s" % urllib.parse.quote(wkey)
    if not url:
        return PushResult(False, err="未配置 push.wecom_webhook 或 push.wecom_key", channel="wecom")
    combined = ("# %s\n\n%s" if push_type == "markdown" else "%s\n\n%s") % (title, desp)
    content = _truncate_utf8_bytes(combined, max_bytes=4000)
    mtype = "markdown" if push_type == "markdown" else "text"
    data = {"msgtype": mtype, mtype: {"content": content}}
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    ok, status, body, err = _http_post(url, raw, timeout=timeout, log=log, tag="企业微信")
    if not ok:
        return PushResult(False, status, body, err, channel="wecom")
    try:
        obj = json.loads(body)
    except Exception:
        return PushResult(False, status, body, "响应不是 JSON", channel="wecom")
    if obj.get("errcode") == 0:
        return PushResult(True, status, body, channel="wecom")
    return PushResult(False, status, body, "errcode=%s errmsg=%s" % (obj.get("errcode"), obj.get("errmsg")), channel="wecom")


def _send_dingtalk(pcfg, title, desp, push_type, timeout, log):
    url = str(pcfg.get("dingtalk_webhook") or pcfg.get("webhook") or "").strip()
    if not url:
        return PushResult(False, err="未配置 push.dingtalk_webhook (或 push.webhook)", channel="dingtalk")
    secret = str(pcfg.get("dingtalk_secret") or pcfg.get("secret") or "").strip()
    if secret:
        url = _dingtalk_signed_url(url, secret)
    if push_type == "markdown":
        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": "# %s\n\n%s" % (title, desp),
            },
        }
    else:
        data = {
            "msgtype": "text",
            "text": {"content": "%s\n\n%s" % (title, desp)},
        }
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    ok, status, body, err = _http_post(url, raw, timeout=timeout, log=log, tag="钉钉")
    if not ok:
        return PushResult(False, status, body, err, channel="dingtalk")
    try:
        obj = json.loads(body)
    except Exception:
        return PushResult(False, status, body, "响应不是 JSON", channel="dingtalk")
    if obj.get("errcode") == 0:
        return PushResult(True, status, body, channel="dingtalk")
    return PushResult(False, status, body, "errcode=%s errmsg=%s" % (obj.get("errcode"), obj.get("errmsg")), channel="dingtalk")


def _send_bark(pcfg, title, desp, push_type, timeout, log):
    url = str(pcfg.get("bark_url") or pcfg.get("webhook") or "").strip().rstrip("/")
    bkey = str(pcfg.get("bark_key") or pcfg.get("pushkey") or "").strip().strip("/")
    if not url and bkey:
        url = "https://api.day.app/%s" % urllib.parse.quote(bkey)
    elif url and bkey and not url.endswith(bkey):
        url = "%s/%s" % (url, urllib.parse.quote(bkey))
    if not url:
        return PushResult(False, err="未配置 push.bark_url 或 push.bark_key", channel="bark")
    data = {
        "title": title,
        "body": _truncate_chars(desp, 4000),
        "group": "weekend-radar",
        "isArchive": 1,
    }
    if push_type == "markdown":
        data["markdown"] = _truncate_chars(desp, 4000)
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    ok, status, body, err = _http_post(url, raw, timeout=timeout, log=log, tag="Bark")
    if not ok:
        return PushResult(False, status, body, err, channel="bark")
    try:
        obj = json.loads(body)
    except Exception:
        return PushResult(False, status, body, "响应不是 JSON", channel="bark")
    if obj.get("code") == 200:
        return PushResult(True, status, body, channel="bark")
    return PushResult(False, status, body, "code=%s message=%s" % (obj.get("code"), obj.get("message")), channel="bark")


def _send_telegram(pcfg, title, desp, push_type, timeout, log):
    token = str(pcfg.get("telegram_bot_token") or "").strip()
    chat_id = str(pcfg.get("telegram_chat_id") or "").strip()
    url = str(pcfg.get("webhook") or "").strip()
    if not url and token:
        url = "https://api.telegram.org/bot%s/sendMessage" % token
    if not url or not chat_id:
        return PushResult(False, err="未配置 push.telegram_bot_token / push.telegram_chat_id", channel="telegram")
    text = _truncate_chars("%s\n\n%s" % (title, desp), 4000)
    data = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    ok, status, body, err = _http_post(url, raw, timeout=timeout, log=log, tag="Telegram")
    if not ok:
        return PushResult(False, status, body, err, channel="telegram")
    try:
        obj = json.loads(body)
    except Exception:
        return PushResult(False, status, body, "响应不是 JSON", channel="telegram")
    if obj.get("ok") is True:
        return PushResult(True, status, body, channel="telegram")
    return PushResult(False, status, body, "description=%s" % obj.get("description"), channel="telegram")


def _send_serverchan(pcfg, title, desp, push_type, timeout, log):
    sendkey = str(pcfg.get("serverchan_sendkey") or pcfg.get("pushkey") or "").strip()
    url = str(pcfg.get("webhook") or "").strip()
    if not url and sendkey:
        m = None
        if sendkey.startswith("sctp"):
            import re
            m = re.match(r"^sctp(\d+)t", sendkey)
        if m:
            url = "https://%s.push.ft07.com/send/%s.send" % (m.group(1), urllib.parse.quote(sendkey))
        else:
            url = "https://sctapi.ftqq.com/%s.send" % urllib.parse.quote(sendkey)
    if not url:
        return PushResult(False, err="未配置 push.serverchan_sendkey (或 push.webhook)", channel="serverchan")
    data = {"title": title, "desp": desp}
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    ok, status, body, err = _http_post(url, raw, timeout=timeout, log=log, tag="Server酱")
    if not ok:
        return PushResult(False, status, body, err, channel="serverchan")
    try:
        obj = json.loads(body)
    except Exception:
        return PushResult(False, status, body, "响应不是 JSON", channel="serverchan")
    if obj.get("code") == 0 or (isinstance(obj.get("data"), dict) and obj["data"].get("errno") == 0):
        return PushResult(True, status, body, channel="serverchan")
    return PushResult(False, status, body, "code=%s message=%s" % (obj.get("code"), obj.get("message")), channel="serverchan")


_CHANNEL_HANDLERS = {
    "pushdeer": _send_pushdeer,
    "feishu": _send_feishu,
    "wecom": _send_wecom,
    "dingtalk": _send_dingtalk,
    "bark": _send_bark,
    "telegram": _send_telegram,
    "serverchan": _send_serverchan,
}


def send(cfg, title, body, log=print, timeout=25):
    """按 [push] channel 推送消息（支持单渠道或多渠道逗号分隔）。"""
    pcfg = cfg.get("push") or {}
    raw_channels = str(pcfg.get("channel", "pushdeer") or "pushdeer")
    channels = [c.strip().lower() for c in raw_channels.split(",") if c.strip()]
    if not channels:
        channels = ["pushdeer"]

    push_type = str(pcfg.get("format", "markdown") or "markdown").strip().lower()
    if push_type not in ("markdown", "text"):
        push_type = "markdown"

    limit = 18000
    try:
        limit = int(pcfg.get("max_chars", "18000") or 18000)
    except Exception:
        pass
    desp = _truncate_chars(body, limit)

    results = []
    for ch in channels:
        handler = _CHANNEL_HANDLERS.get(ch)
        if not handler:
            res = PushResult(
                False,
                err="不支持的推送渠道 %r（可选: %s）" % (ch, ", ".join(SUPPORTED_CHANNELS)),
                channel=ch,
            )
        else:
            res = handler(pcfg, title, desp, push_type, timeout, log)
        results.append(res)

    if len(results) == 1:
        return results[0]

    all_ok = all(r.ok for r in results)
    errs = ["%s:%s" % (r.channel, r.err) for r in results if not r.ok]
    bodies = ["%s:%s" % (r.channel, (r.body or "")[:120]) for r in results]
    return PushResult(
        all_ok,
        status=200 if all_ok else 0,
        body=" | ".join(bodies),
        err="; ".join(errs),
        channel=",".join(channels),
    )
