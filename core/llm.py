# -*- coding: utf-8 -*-
"""LLM 客户端 —— 支持主流 AI Provider 预设与本机网关双协议。

支持的 provider 预设:
  - magic       : 本机 Magic 网关 (127.0.0.1:3425)，双协议自动探测，读 caller-keys.json
  - openai      : OpenAI 官方或兼容接口 (https://api.openai.com/v1)
  - anthropic   : Anthropic 官方或兼容接口 (https://api.anthropic.com/v1)
  - deepseek    : DeepSeek 官方 (https://api.deepseek.com/v1)
  - siliconflow : 硅基流动 (https://api.siliconflow.cn/v1)
  - openrouter  : OpenRouter (https://openrouter.ai/api/v1)
  - dashscope   : 阿里云百炼 / 通义千问 (https://dashscope.aliyuncs.com/compatible-mode/v1)
  - zhipu       : 智谱 BigModel (https://open.bigmodel.cn/api/paas/v4)
  - ollama      : 本地 Ollama (http://127.0.0.1:11434/v1)
  - custom      : 自定义 base_url + mode + model

退化:
  任何异常都返回 LLMResult(ok=False, err=...), 由上层把该源标记失败并触发健康告警。
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

_MAGIC_DEFAULT_URL = "http://127.0.0.1:3425"
_MAGIC_DEFAULT_MODEL = "group/deepseek-v4-1-flash"

PROVIDER_PRESETS = {
    "magic": {
        "base_url": _MAGIC_DEFAULT_URL,
        "mode": "auto",
        "model": _MAGIC_DEFAULT_MODEL,
        "env_keys": ("RADAR_LLM_KEY",),
        "use_key_file": True,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "mode": "openai",
        "model": "gpt-4o-mini",
        "env_keys": ("RADAR_LLM_KEY", "OPENAI_API_KEY"),
        "use_key_file": False,
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "mode": "anthropic",
        "model": "claude-haiku-5-5",
        "env_keys": ("RADAR_LLM_KEY", "ANTHROPIC_API_KEY"),
        "use_key_file": False,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "mode": "openai",
        "model": "deepseek-chat",
        "env_keys": ("RADAR_LLM_KEY", "DEEPSEEK_API_KEY"),
        "use_key_file": False,
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "mode": "openai",
        "model": "deepseek-ai/DeepSeek-V3",
        "env_keys": ("RADAR_LLM_KEY", "SILICONFLOW_API_KEY"),
        "use_key_file": False,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "mode": "openai",
        "model": "deepseek/deepseek-chat",
        "env_keys": ("RADAR_LLM_KEY", "OPENROUTER_API_KEY"),
        "use_key_file": False,
    },
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "mode": "openai",
        "model": "qwen-plus",
        "env_keys": ("RADAR_LLM_KEY", "DASHSCOPE_API_KEY"),
        "use_key_file": False,
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "mode": "openai",
        "model": "glm-4-flash",
        "env_keys": ("RADAR_LLM_KEY", "ZHIPU_API_KEY"),
        "use_key_file": False,
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "mode": "openai",
        "model": "qwen2.5:7b",
        "env_keys": ("RADAR_LLM_KEY",),
        "use_key_file": False,
    },
    "custom": {
        "base_url": "",
        "mode": "openai",
        "model": "",
        "env_keys": ("RADAR_LLM_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"),
        "use_key_file": False,
    },
}


def build_endpoint_url(base_url, proto):
    """智能拼接接口路径：兼容以 /、/v1、/v4 或完整路径结尾的 base_url。"""
    b = str(base_url or "").strip().rstrip("/")
    if not b:
        return ""
    if proto == "openai":
        if b.endswith("/chat/completions"):
            return b
        if re.search(r"/v\d+$", b):
            return b + "/chat/completions"
        return b + "/v1/chat/completions"
    else:
        if b.endswith("/messages"):
            return b
        if re.search(r"/v\d+$", b):
            return b + "/messages"
        return b + "/v1/messages"


def build_models_url(base_url, mode="openai"):
    """把 base_url 拼成 /models 列表地址，兼容 OpenAI / Anthropic 两种风格。

    base_url 可能已经是 .../v1、.../v4、裸域名，甚至直接以 /chat/completions 结尾。
    """
    b = str(base_url or "").strip().rstrip("/")
    if not b:
        return []
    # 已经是完整 chat 端点时回退到它的上级 /vN
    b = re.sub(r"/(chat/completions|messages)$", "", b)
    if b.endswith("/models"):
        return [b]
    cands = []
    if re.search(r"/v\d+$", b):
        cands.append(b + "/models")
    else:
        cands.append(b + "/v1/models")
    cands.append(b + "/models")
    # 去重保序
    seen, out = set(), []
    for u in cands:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fetch_model_ids(cfg, timeout=12, log=print):
    """从当前 LLM 网关拉取可用模型 id 列表。

    返回 (ids, note)。任何一步失败都不抛异常，note 里带可读原因，方便前端直接展示。
    只取 id / name，绝不把响应里的凭证或其他字段回给前端。
    """
    _, base_url, mode, _, _ = resolve_provider_config(cfg)
    key = load_key(cfg, log)
    headers = {"User-Agent": "weekend-radar/1.0", "Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer %s" % key
    # Anthropic 兼容网关通常也要 x-apis-key
    if str(mode).strip().lower() == "anthropic":
        if key:
            headers["x-apis-key"] = key
        headers["anthropic-version"] = "2023-06-01"

    urls = build_models_url(base_url, mode)
    errors = []
    for url in urls:
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
            data = json.loads(raw)
            items = data.get("data") or data.get("models") or []
            ids = []
            for x in items:
                if isinstance(x, str):
                    ids.append(x)
                elif isinstance(x, dict):
                    mid = x.get("id") or x.get("name") or x.get("model")
                    if mid:
                        ids.append(str(mid))
            ids = [x for x in dict.fromkeys(ids) if x.strip()]
            if ids:
                return ids, "共 %d 个模型（%s）" % (len(ids), url.split("//")[-1].split("/")[0])
            errors.append("%s: 返回空列表" % url.split("//")[-1])
        except urllib.error.HTTPError as e:
            errors.append("HTTP %s" % e.code)
        except Exception as e:
            errors.append("%s: %s" % (type(e).__name__, str(e)[:80]))
    return [], "获取失败: " + "; ".join(errors[:3])


class LLMResult:
    __slots__ = ("ok", "text", "err", "proto", "model", "truncated")

    def __init__(self, ok, text="", err="", proto="", model=""):
        self.ok = ok
        self.text = text
        self.err = err
        self.proto = proto
        self.model = model
        self.truncated = False   # 本次调用的输入是否被截断（按次返回, 不跨源串味）

    def __repr__(self):
        return "LLMResult(ok=%s proto=%s len=%d err=%s)" % (
            self.ok, self.proto, len(self.text or ""), self.err)


def resolve_provider_config(cfg):
    """结合 [llm] provider 预设与用户显式配置，解析出 (provider, base_url, mode, model, preset)。"""
    llm_cfg = cfg.get("llm") or {}
    provider = str(llm_cfg.get("provider", "magic") or "magic").strip().lower()
    if provider == "local":
        provider = "magic"
    preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"])

    raw_base = str(llm_cfg.get("base_url", "") or "").strip().rstrip("/")
    raw_mode = str(llm_cfg.get("mode", "") or "").strip().lower()
    raw_model = str(llm_cfg.get("model", "") or "").strip()

    if provider != "magic":
        # 切换了非 magic 的 provider 时，若 base_url / model 仍停留在 magic 默认值，则使用该 provider 的预设值
        base_url = preset["base_url"] if (not raw_base or raw_base == _MAGIC_DEFAULT_URL) else raw_base
        model = preset["model"] if (not raw_model or raw_model == _MAGIC_DEFAULT_MODEL) else raw_model
        mode = preset["mode"] if (not raw_mode or raw_mode == "auto") else raw_mode
    else:
        base_url = raw_base or preset["base_url"]
        model = raw_model or preset["model"]
        mode = raw_mode or preset["mode"]

    return provider, base_url, mode, model, preset


def load_key(cfg, log=print):
    """取 LLM 凭证: config [llm] key 优先 -> 对应 Provider 环境变量 -> caller-keys.json。"""
    llm_cfg = cfg.get("llm") or {}
    key = str(llm_cfg.get("key", "") or "").strip()
    if key:
        return key
    provider, _, _, _, preset = resolve_provider_config(cfg)
    for env_name in preset.get("env_keys", ()):
        val = os.environ.get(env_name, "").strip()
        if val:
            return val
    if not preset.get("use_key_file", False) and provider != "magic":
        return ""
    kf = str(llm_cfg.get("key_file", "") or "").strip()
    if not kf:
        return ""
    path = os.path.expanduser(kf)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data if isinstance(data, list) else []:
            secret = (item or {}).get("secret")
            if secret:
                return str(secret).strip()
    except Exception as e:
        log("  [!] 读取 key_file 失败 (%s): %s" % (path, e))
    return ""


class LLM:
    """最小可用的对话客户端。只做一件事: 给一段文本, 要一段 JSON 回来。"""

    def __init__(self, cfg, log=print):
        self.provider, self.base, self.mode, self.model, _ = resolve_provider_config(cfg)
        self.temperature = cfg["llm"].get("temperature", "0")
        self.timeout = float(cfg["llm"].get("timeout", "90") or 90)
        self.max_chars = int(cfg["llm"].get("max_chars", "24000") or 24000)
        self.max_tokens = int(cfg["llm"].get("max_tokens", "8192") or 8192)
        self.key = load_key(cfg, log)
        self.log = log
        self.city = str((cfg.get("radar") or {}).get("city", "北京") or "北京").strip()
        if len(self.city) > 2 and self.city.endswith("市"):
            self.city = self.city[:-1]
        self._resolved = None if self.mode == "auto" else self.mode
        self.calls = 0
        self.truncated = False

    # ---------- 底层请求 ----------
    def _post_url(self, url, payload, headers):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        h = {"Content-Type": "application/json", "User-Agent": "weekend-radar/1.0"}
        h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")

    def _call_openai(self, prompt, system):
        headers = {"Authorization": "Bearer %s" % self.key} if self.key else {}
        payload = {
            "model": self.model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": prompt}],
            "temperature": float(self.temperature or 0),
            "max_tokens": self.max_tokens,
        }
        url = build_endpoint_url(self.base, "openai")
        status, body = self._post_url(url, payload, headers)
        obj = json.loads(body)
        choices = obj.get("choices") or []
        if not choices:
            raise RuntimeError("响应无 choices: %s" % body[:200])
        choice = choices[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        if choice.get("finish_reason") == "length" and not content:
            raise RuntimeError("LLM 思考/生成耗尽上限 (finish_reason=length, max_tokens=%d)" % self.max_tokens)
        return content, obj.get("model", self.model)

    def _call_anthropic(self, prompt, system):
        headers = {
            "x-api-key": self.key or "magic",
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": float(self.temperature or 0),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        url = build_endpoint_url(self.base, "anthropic")
        status, body = self._post_url(url, payload, headers)
        obj = json.loads(body)
        blocks = obj.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        if not text:
            raise RuntimeError("响应无 content: %s" % body[:200])
        return text, obj.get("model", self.model)

    # ---------- 对外 ----------
    def complete(self, prompt, system=""):
        if not self.base:
            return LLMResult(False, err="未配置 llm.base_url")
        order = []
        if self._resolved in ("openai", "anthropic"):
            order = [self._resolved]
        else:
            order = ["openai", "anthropic"]
        last = ""
        for proto in order:
            try:
                self.calls += 1
                if proto == "openai":
                    text, model = self._call_openai(prompt, system)
                else:
                    text, model = self._call_anthropic(prompt, system)
                if self.mode == "auto":
                    self._resolved = proto
                return LLMResult(True, text, proto=proto, model=model)
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:200]
                except Exception:
                    pass
                last = "%s HTTP %s %s" % (proto, e.code, detail)
            except Exception as e:
                last = "%s %s: %s" % (proto, type(e).__name__, e)
            self.log("  [警告] LLM %s 协议失败: %s" % (proto, last))
        return LLMResult(False, err=last)

    def probe(self):
        """--probe 用: 探测当前配置的协议（auto 时两种协议各打一发）, 返回 [(proto, ok, 说明)]。"""
        out = []
        protos = ("openai", "anthropic") if self.mode == "auto" else (self.mode,)
        for proto in protos:
            try:
                if proto == "openai":
                    text, model = self._call_openai("回复一个词: ok", "")
                else:
                    text, model = self._call_anthropic("回复一个词: ok", "")
                out.append((proto, True, "provider=%s model=%s 回复=%r" % (self.provider, model, text[:40])))
            except urllib.error.HTTPError as e:
                out.append((proto, False, "HTTP %s" % e.code))
            except Exception as e:
                out.append((proto, False, "%s: %s" % (type(e).__name__, e)))
        return out

    # ---------- 结构化提取 ----------
    def extract_events(self, page_text, source_name, today_iso, window_end_iso,
                       default_category="A", declared_url=""):
        """把一页 HTML/文本转成事件 JSON 数组。失败返回 LLMResult(ok=False)。"""
        truncated = len(page_text) > self.max_chars
        body = page_text[:self.max_chars]
        self.truncated = self.truncated or truncated
        system = (
            "你是严谨的活动信息抽取器。只输出 JSON 数组, 不要任何解释、不要 markdown 代码块。\n"
            "规则:\n"
            "1) 只抽取页面中明确出现的信息, 绝不编造。看不到就填 null。\n"
            "2) 日期统一成 YYYY-MM-DD。未知填 null。\n"
            "3) category: A=展会/漫展/市集/快闪/博物馆特展/画展艺术展/音乐会/演唱会/舞蹈演出(不收戏剧/话剧); B=AI/开源/开发者/技术沙龙; C=大中型博览会(B2B)。\n"
            "4) is_free: 明确免费填 true; 有明确票价填 false; 不确定填 null。\n"
            "5) has_goods: 页面提到周边/集章/赠品/纸质证书/管饭才填 true, 否则 null。\n"
            "7) 每条必须有 name 和 start_date, 缺任一则丢弃该条。\n"
            "8) 只要中国%s的活动, 其他城市丢弃。\n"
            "输出格式: [{\"name\":\"\",\"start_date\":\"\",\"end_date\":null,\"venue\":null,"
            "\"price_text\":null,\"is_free\":null,\"category\":\"A\",\"url\":null,"
            "\"deadline\":null,\"has_goods\":null,\"note\":null}]"
        ) % self.city
        prompt = (
            "来源页面: %s\n今天: %s, 时间窗口截止: %s\n"
            "请从下面的页面文本中抽取活动。\n---\n%s\n---" % (
                declared_url or source_name, today_iso, window_end_iso, body)
        )
        res = self.complete(prompt, system)
        if not res.ok:
            return res
        arr = self._parse_json_array(res.text)
        if arr is None:
            return LLMResult(False, err="LLM 输出无法解析为 JSON 数组: %r" % (res.text[:160],),
                             proto=res.proto, model=res.model)
        res.text = arr  # 复用字段装解析后的结果
        res.truncated = truncated
        return res

    @staticmethod
    def _parse_json_array(text):
        if not text:
            return None
        s = text.strip()
        m = _JSON_FENCE.search(s)
        if m:
            s = m.group(1).strip()
        start = s.find("[")
        end = s.rfind("]")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(s[start:end + 1])
        except Exception:
            return None
        if not isinstance(obj, list):
            return None
        out = []
        for item in obj:
            if isinstance(item, dict) and item.get("name") and item.get("start_date"):
                out.append(item)
        return out
