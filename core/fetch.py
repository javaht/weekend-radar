# -*- coding: utf-8 -*-
"""HTTP 抓取 —— 重试 / 退避 / 限速 / HTTP 白名单。

★ 关键修复 (本文件存在的首要理由):
  090expo 和 manzhan 只在 80 端口提供服务, 443 直接拒绝连接。
  很多抓取链路会"帮忙"把 http:// 升级成 https://, 于是这两站必然失败,
  且失败原因看起来像"站点挂了"—— 这是最容易误判的一类假阴性。
  这里显式记录 allow_http 白名单, 命中就强制 http, 绝不升级。

退化策略:
  单页失败返回 FetchResult(ok=False), 不抛异常 —— 抛异常表示不可恢复。
"""
import random
import time
import urllib.parse

try:
    import requests
except ImportError:      # --self-test 在没有 requests 的机器上也要能跑
    requests = None

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 反爬/限流验证页的特征串。命中就把这次请求判为失败并写明原因,
# 否则验证页会被当成正常页面喂给 LLM, 抽出一堆垃圾活动。
# 只放"确定只出现在拦截页里"的串; 太通用的词(如裸 captcha)会误伤含验证码
# 组件的正常页面, 所以不用。
BAN_MARKERS = (
    "_____tmd_____",        # 阿里系 x5 滑块
    "x5secdata",
    "punish",
    "哎呀，访问太快",        # 活动行限流页标题
    "请完成安全验证",
    "滑动验证",
)


def _looks_blocked(text):
    head = (text or "")[:4000]
    return any(m in head for m in BAN_MARKERS)


class FetchResult:
    __slots__ = ("ok", "status", "text", "url", "err", "elapsed")

    def __init__(self, ok, status=0, text="", url="", err="", elapsed=0.0):
        self.ok = ok
        self.status = status
        self.text = text
        self.url = url
        self.err = err
        self.elapsed = elapsed

    def __repr__(self):
        return "FetchResult(ok=%s status=%s bytes=%d url=%s err=%s)" % (
            self.ok, self.status, len(self.text or ""), self.url, self.err)


def normalize_scheme(url, allow_http_hosts):
    """命中 allow_http 白名单的域名强制走 http。

    只在该域名确实只在 80 端口时才会命中; 判断权在配置里, 不在代码里。
    """
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return url
    host = (p.hostname or "").lower()
    if host and host in {h.lower() for h in allow_http_hosts}:
        if p.scheme != "http":
            return urllib.parse.urlunparse(p._replace(scheme="http"))
    return url


class Fetcher:
    """带限速与退避的抓取器。一个实例复用一个 Session。"""

    def __init__(self, interval=2.0, timeout=20, retries=3, backoff_base=2.0,
                 allow_http_hosts=None, log=print):
        self.interval = max(0.0, float(interval))
        self.timeout = float(timeout)
        self.retries = max(1, int(retries))
        self.backoff_base = float(backoff_base)
        self.allow_http_hosts = allow_http_hosts or []
        self.log = log
        self._last = 0.0
        self._session = None
        self.calls = 0

    def _sess(self):
        if self._session is None:
            if requests is None:
                raise RuntimeError("requests 未安装: pip install requests")
            s = requests.Session()
            s.trust_env = False          # 不吃系统代理, cron 环境里的代理变量不可信
            s.headers.update(DEFAULT_HEADERS)
            self._session = s
        return self._session

    def _throttle(self):
        """按配置的 interval 限速 —— 同一站点高频请求是最容易被封的原因。"""
        if self.interval <= 0:
            return
        gap = time.monotonic() - self._last
        if gap < self.interval:
            time.sleep(self.interval - gap)
        self._last = time.monotonic()

    def _sleep_backoff(self, attempt):
        delay = self.backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 1.0)
        time.sleep(min(delay, 30.0))

    def get(self, url, params=None, headers=None, expect_json=False):
        """GET 一个 URL。返回 FetchResult, 永不抛网络异常。"""
        target = normalize_scheme(url, self.allow_http_hosts)
        if target != url:
            self.log("  [i] 白名单命中, 改用 http: %s" % target)
        hdrs = dict(DEFAULT_HEADERS)
        if headers:
            hdrs.update(headers)
        last_err = ""
        for attempt in range(1, self.retries + 1):
            self._throttle()
            t0 = time.monotonic()
            try:
                r = self._sess().get(target, params=params, headers=hdrs,
                                     timeout=self.timeout)
                self.calls += 1
                elapsed = time.monotonic() - t0
                if r.status_code == 404:
                    return FetchResult(False, 404, "", target, "HTTP 404", elapsed)
                if r.status_code in (403, 407, 412):
                    # 反爬拦截: 重试只会加重封锁, 直接放弃该源
                    return FetchResult(False, r.status_code, "", target,
                                       "反爬拦截 HTTP %d" % r.status_code, elapsed)
                if r.status_code >= 500:
                    last_err = "HTTP %d" % r.status_code
                    if attempt < self.retries:
                        self._sleep_backoff(attempt)
                        continue
                    return FetchResult(False, r.status_code, "", target, last_err, elapsed)
                if r.status_code != 200:
                    return FetchResult(False, r.status_code, "", target,
                                       "HTTP %d" % r.status_code, elapsed)
                # ★ 先定编码再看内容: 响应头没写 charset 时 requests 按 ISO-8859-1 解码,
                #   GBK/UTF-8 中文页会变乱码, 「哎呀，访问太快」这类拦截特征就匹配不到。
                #   头里明确给了 charset 就信它, 不再跑较慢的 apparent_encoding。
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = r.apparent_encoding or r.encoding
                if _looks_blocked(r.text):
                    return FetchResult(False, 200, r.text, target,
                                       "反爬拦截（命中验证页，需 cookie 或换 IP）", elapsed)
                if expect_json:
                    try:
                        r.json()
                    except Exception:
                        return FetchResult(False, 200, r.text, target,
                                           "返回的不是 JSON", elapsed)
                return FetchResult(True, 200, r.text, target, "", elapsed)
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
                if attempt < self.retries:
                    self.log("  [警告] 第 %d 次请求失败, 退避重试: %s" % (attempt, last_err))
                    self._sleep_backoff(attempt)
                    continue
                return FetchResult(False, 0, "", target, last_err,
                                   time.monotonic() - t0)
        return FetchResult(False, 0, "", target, last_err)

    def get_json(self, url, params=None, headers=None):
        return self.get(url, params=params, headers=headers, expect_json=True)
