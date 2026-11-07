# -*- coding: utf-8 -*-
"""配置读取 —— 根级 config.ini, 与 douyin-api-collector 同一套约定。

为什么文件缺失/写错一律回落到内置默认值:
  服务器上 cron 跑的时候没人盯着, 配置读失败时宁可跑出一份「默认配置的报告」
  报警, 也不要直接崩掉 —— 崩掉的话当天什么信号都没有。

[sources] 是动态段, 平台名不固定, 所以单独解析, 不进 DEFAULTS。
"""
import configparser
import os
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG_FILE = os.path.join(ROOT, "config.ini")

# 内置城市预设：切换 [radar] city 或传 --city 时，自动联动行政区划码、票务站点城市码、
# 子域名与场馆简称映射。未在此表中的城市也能工作（自动按城市名做 URL 编码与 LLM 过滤）。
CITY_PRESETS = {
    "北京": {
        "adcode": "110000",
        "damai_city": "101010100",
        "bendibao_city": "bj",
        "douban_city": "beijing",
        "artron_code": "1-%E5%8C%97%E4%BA%AC",
        "venue_aliases": [
            (r"^国展", "中国国际展览中心"),
            (r"^北展", "北京展览馆"),
            (r"^(?:全国农展馆?|农展馆?)", "全国农业展览馆"),
            (r"^(?:国博|国家博物馆)", "中国国家博物馆"),
            (r"^工体", "工人体育场"),
            (r"^首体", "首都体育馆"),
        ],
    },
    "上海": {
        "adcode": "310000",
        "damai_city": "101020100",
        "bendibao_city": "sh",
        "douban_city": "shanghai",
        "artron_code": "2-%E4%B8%8A%E6%B5%B7",
        "venue_aliases": [
            (r"^(?:上海)?(?:国家会展中心|国展)", "国家会展中心"),
            (r"^(?:上海)?新国际(?:博览中心)?", "上海新国际博览中心"),
            (r"^(?:上海)?世博展览馆", "上海世博展览馆"),
            (r"^(?:上博|上海博物馆)", "上海博物馆"),
        ],
    },
    "广州": {
        "adcode": "440100",
        "damai_city": "101280100",
        "bendibao_city": "gz",
        "douban_city": "guangzhou",
        "artron_code": "3-%E5%B9%BF%E4%B8%9C",
        "venue_aliases": [
            (r"^(?:广交会展馆|琶洲展馆)", "中国进出口商品交易会展馆"),
            (r"^(?:广州)?保利世贸(?:博览馆)?", "保利世贸博览馆"),
        ],
    },
    "深圳": {
        "adcode": "440300",
        "damai_city": "101280600",
        "bendibao_city": "sz",
        "douban_city": "shenzhen",
        "artron_code": "3-%E5%B9%BF%E4%B8%9C",
        "venue_aliases": [
            (r"^(?:深圳)?国际会展中心", "深圳国际会展中心"),
            (r"^(?:深圳)?会展中心", "深圳会展中心"),
        ],
    },
    "杭州": {
        "adcode": "330100",
        "damai_city": "101210100",
        "bendibao_city": "hz",
        "douban_city": "hangzhou",
        "artron_code": "8-%E6%B5%99%E6%B1%9F",
        "venue_aliases": [
            (r"^(?:杭州)?国际博览中心|杭博", "杭州国际博览中心"),
            (r"^(?:杭州)?大会展中心", "杭州大会展中心"),
        ],
    },
    "成都": {
        "adcode": "510100",
        "damai_city": "101270100",
        "bendibao_city": "cd",
        "douban_city": "chengdu",
        "artron_code": "22-%E5%9B%9B%E5%B7%9D",
        "venue_aliases": [
            (r"^(?:成都)?世纪城(?:新国际会展中心)?", "世纪城新国际会展中心"),
            (r"^中国西部国际博览城|西博城", "中国西部国际博览城"),
        ],
    },
    "武汉": {
        "adcode": "420100",
        "damai_city": "101200100",
        "bendibao_city": "wh",
        "douban_city": "wuhan",
        "artron_code": "16-%E6%B9%96%E5%8C%97",
        "venue_aliases": [],
    },
    "南京": {
        "adcode": "320100",
        "damai_city": "101190100",
        "bendibao_city": "nj",
        "douban_city": "nanjing",
        "artron_code": "7-%E6%B1%9F%E8%8B%8F",
        "venue_aliases": [],
    },
    "西安": {
        "adcode": "610100",
        "damai_city": "101110100",
        "bendibao_city": "xa",
        "douban_city": "xian",
        "artron_code": "26-%E9%99%95%E8%A5%BF",
        "venue_aliases": [],
    },
    "重庆": {
        "adcode": "500000",
        "damai_city": "101040100",
        "bendibao_city": "cq",
        "douban_city": "chongqing",
        "artron_code": "21-%E9%87%8D%E5%BA%86",
        "venue_aliases": [],
    },
    "苏州": {
        "adcode": "320500",
        "damai_city": "101190400",
        "bendibao_city": "suzhou",
        "douban_city": "suzhou",
        "artron_code": "7-%E6%B1%9F%E8%8B%8F",
        "venue_aliases": [],
    },
    "天津": {
        "adcode": "120000",
        "damai_city": "101030100",
        "bendibao_city": "tj",
        "douban_city": "tianjin",
        "artron_code": "14-%E5%A4%A9%E6%B4%A5",
        "venue_aliases": [],
    },
    "长沙": {
        "adcode": "430100",
        "damai_city": "101250100",
        "bendibao_city": "cs",
        "douban_city": "changsha",
        "artron_code": "17-%E6%B9%96%E5%8D%97",
        "venue_aliases": [],
    },
}

# 北京本地独立场馆源（切换到其他城市时默认跳过，避免把北京场馆活动混入外地报告）
BEIJING_ONLY_SOURCES = {"chnmuseum", "chncpa", "cncc"}

# 今日会展 (datexpo.com) 的城市筛选码。站点用 5 段数字编码而非城市名,
# 只能从首页城市导航里抓出来一次写死（站点改版时需要重新校准）。
DATEXPO_CITY_CODES = {
    "北京": "1_1_0_0_0",
    "上海": "1_21_0_0_0",
    "广州": "1_423_424_0_0",
    "深圳": "1_423_524_0_0",
    "杭州": "1_3133_3134_0_0",
    "成都": "1_2589_2590_0_0",
    "武汉": "1_1320_1321_0_0",
    "南京": "1_1643_1644_0_0",
    "西安": "1_2471_2472_0_0",
    "重庆": "1_62_0_0_0",
    "苏州": "1_1643_1692_0_0",
    "天津": "1_42_0_0_0",
    "长沙": "1_1436_1437_0_0",
}

DEFAULTS = {
    "radar": {
        "window_days": "30",
        "out_dir": "data/reports",
        "db": "data/radar.sqlite",
        "weekend_only": "true",
        "city": "北京",
        # 名字里含这些词就丢弃：抽到的是「不可用/非门票」条目，不是活动
        # ★ 品类级过滤(话剧/喜剧/戏曲...)已迁到 push.disabled_genres, 这里只留
        #   真正无意义的噪音词。
        "exclude_keywords": "取消,不含门票,已售罄,停办",
        # 连续缺席几轮才判定「取消」。1 = 一缺就判。
        # ★ 不要设为 1：LLM 抽取有波动，一缺就判会天天误报活动取消。
        "gone_after_misses": "2",
        # 只有确定性源（JSON 接口）参与「取消」判定；LLM 抽取源的覆盖条数会波动
        "stable_sources": "bili",
        # 一次变更连续几轮成立才认，压掉 LLM 措辞抖动
        "confirm_runs": "2",
    },
    "llm": {
        # provider: magic | openai | anthropic | deepseek | siliconflow | openrouter | dashscope | zhipu | ollama | custom
        "provider": "magic",
        "base_url": "http://127.0.0.1:3425",
        "mode": "auto",
        "model": "group/deepseek-v4-1-flash",
        "key": "",
        "key_file": "~/.config/magpie/caller-keys.json",
        "max_chars": "24000",
        "max_tokens": "8192",
        "temperature": "0",
        "timeout": "90",
    },
    "fetch": {
        "interval": "2",
        "timeout": "20",
        "retries": "3",
        "backoff_base": "2",
        # 只有 80 端口的站点; 这些域名 https 不通, 必须走 http
        "allow_http": "www.090expo.com,090expo.com,www.manzhan.com,manzhan.com",
    },
    # 各源自己的参数, 集中放这里, 免得每个源一个 section
    "extra": {
        "city": "北京",
        "damai_city": "101010100",
        "damai_keyword": "漫展",
        "bili_area": "110000",
        "bili_ptype": "展览",
        "bili_pagesize": "20",
        "cpp_city": "110000",
        "cpp_pagesize": "30",
        # 090expo 的「漫展演出排期表」文章 ID 每月变，留空自动按城市发现
        "expo090_article": "",
    },
    "push": {
        "enabled": "true",
        # 支持单渠道或多渠道逗号分隔: pushdeer, feishu, wecom, dingtalk, bark, telegram, serverchan
        "channel": "pushdeer",
        "format": "markdown",
        "endpoint": "https://api2.pushdeer.com/message/push",
        "pushkey": "",
        # 通用/各渠道轻量 Webhook 参数
        "webhook": "",
        "secret": "",
        "feishu_webhook": "",
        "feishu_secret": "",
        "wecom_webhook": "",
        "wecom_key": "",
        "dingtalk_webhook": "",
        "dingtalk_secret": "",
        "bark_url": "",
        "bark_key": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "serverchan_sendkey": "",
        "notify_on_no_change": "false",
        "notify_on_source_failure": "true",
        # 新增条数达到多少才触发推送，防止 LLM 抽取抖动骗出「有变化」
        "min_new_changes": "3",
        # ★ 品类级推送开关（前端「配置渠道」页勾选）。
        #   写成 "theater=off,comedy=off" = 「话剧/音乐剧」「脱口秀/相声」不推送；
        #   留空 = 所有品类都推。品类判定见 core/genre.py。
        "disabled_genres": "",
        # 推送正文上限, 超出截断并提示看文件
        "max_chars": "18000",
    },
}


class SourceRows(dict):
    """[sources] 行容器，附带一份「未渲染」的原始模板。

    apply_env_and_city 每次都从 templates 重新渲染，而不是在已渲染结果上叠加，
    否则 --city / Web 切城市时第二次替换找不到 {douban_city} 这类占位符，
    URL 会永远停留在上一次的城市（实测 --city 上海仍抓 /city/beijing）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.templates = {}


def load_config(path=None):
    """返回 (cfg, sources)。cfg 是带默认值的三层 dict, sources 是原始行 dict。

    ★ 叠加顺序: 内置默认值 → config.ini → config.local.ini。
      后者已被 .gitignore 忽略, 用来放真实 API Key / 内网网关地址这类
      不能进公开仓库的值。仓库里的 config.ini 只留占位与注释。
    """
    path = path or CONFIG_FILE
    cfg = {s: dict(kv) for s, kv in DEFAULTS.items()}
    sources = SourceRows()
    if not os.path.exists(path):
        return cfg, sources

    # config.ini 之后再叠一层 config.local.ini(不入库): 真实 Key 与内网地址放这里
    overlay = os.path.splitext(path)[0] + ".local.ini"
    parsers = [path] + ([overlay] if os.path.exists(overlay) else [])

    for cfg_path in parsers:
        # ★ interpolation=None 必须显式给: URL 里全是 %E5%8C%97 这种百分号转义,
        #   默认的 BasicInterpolation 会把它当插值语法直接抛异常。
        p = configparser.ConfigParser(interpolation=None)
        try:
            p.read(cfg_path, encoding="utf-8")
        except Exception:
            continue
        for section in cfg:
            if not p.has_section(section):
                continue
            for key in cfg[section]:
                if not p.has_option(section, key):
                    continue
                val = (p.get(section, key) or "").strip()
                # 空值 = 用默认, 这样配置文件里可以留 comment 掉的空壳
                if val:
                    cfg[section][key] = val
        if p.has_section("sources"):
            for key, val in p.items("sources"):
                raw = (val or "").strip()
                sources[key] = raw
                # 保留未渲染模板，供后续 --city / 切城市重新渲染
                sources.templates[key] = raw
    apply_env_and_city(cfg, sources)
    return cfg, sources


def normalize_city_name(raw_city):
    """把 '上海市' / '上海' 统一成 '上海'。"""
    s = str(raw_city or "北京").strip()
    if len(s) > 2 and s.endswith("市"):
        s = s[:-1]
    return s or "北京"


def get_city_preset(city_name):
    """获取城市预设字典；未知城市返回基于城市名的通用回退配置。"""
    c = normalize_city_name(city_name)
    if c in CITY_PRESETS:
        return dict(CITY_PRESETS[c])
    enc = urllib.parse.quote(c)
    return {
        "adcode": "",
        "damai_city": "",
        "bendibao_city": "",
        "douban_city": "",
        "artron_code": "0-%s" % enc,
        "venue_aliases": [],
    }


def apply_env_and_city(cfg, sources, cli_overrides=None):
    """合并环境变量、CLI 参数，并根据最终确定的 city 联动源参数与 URL。"""
    cli_overrides = cli_overrides or {}

    # 1. 环境变量覆盖（容器化 / CI 场景零配置文件也能跑）
    env_map = [
        ("RADAR_CITY", "radar", "city"),
        ("RADAR_WINDOW_DAYS", "radar", "window_days"),
        ("RADAR_LLM_PROVIDER", "llm", "provider"),
        ("RADAR_LLM_BASE_URL", "llm", "base_url"),
        ("RADAR_LLM_MODE", "llm", "mode"),
        ("RADAR_LLM_MODEL", "llm", "model"),
        ("RADAR_LLM_KEY", "llm", "key"),
        ("RADAR_PUSH_ENABLED", "push", "enabled"),
        ("RADAR_PUSH_CHANNEL", "push", "channel"),
        ("RADAR_PUSH_FORMAT", "push", "format"),
        ("RADAR_PUSH_WEBHOOK", "push", "webhook"),
        ("RADAR_PUSH_KEY", "push", "pushkey"),
        ("RADAR_PUSH_SECRET", "push", "secret"),
        ("RADAR_FEISHU_WEBHOOK", "push", "feishu_webhook"),
        ("RADAR_FEISHU_SECRET", "push", "feishu_secret"),
        ("RADAR_WECOM_WEBHOOK", "push", "wecom_webhook"),
        ("RADAR_WECOM_KEY", "push", "wecom_key"),
        ("RADAR_DINGTALK_WEBHOOK", "push", "dingtalk_webhook"),
        ("RADAR_DINGTALK_SECRET", "push", "dingtalk_secret"),
        ("RADAR_BARK_URL", "push", "bark_url"),
        ("RADAR_BARK_KEY", "push", "bark_key"),
        ("RADAR_TELEGRAM_BOT_TOKEN", "push", "telegram_bot_token"),
        ("RADAR_TELEGRAM_CHAT_ID", "push", "telegram_chat_id"),
        ("RADAR_SERVERCHAN_SENDKEY", "push", "serverchan_sendkey"),
    ]
    for env_key, sec, key in env_map:
        val = os.environ.get(env_key, "").strip()
        if val:
            cfg[sec][key] = val

    # 2. CLI 参数覆盖（最高优先级）
    if cli_overrides.get("city"):
        cfg["radar"]["city"] = str(cli_overrides["city"]).strip()
    if cli_overrides.get("provider"):
        cfg["llm"]["provider"] = str(cli_overrides["provider"]).strip()
    if cli_overrides.get("model"):
        cfg["llm"]["model"] = str(cli_overrides["model"]).strip()
    if cli_overrides.get("base_url"):
        cfg["llm"]["base_url"] = str(cli_overrides["base_url"]).strip()
    if cli_overrides.get("channel"):
        cfg["push"]["channel"] = str(cli_overrides["channel"]).strip()

    # 3. 联动城市参数与源 URL
    city = normalize_city_name(cfg["radar"].get("city", "北京"))
    cfg["radar"]["city"] = city
    cfg["extra"]["city"] = city
    preset = get_city_preset(city)
    city_url = urllib.parse.quote(city)

    if city != "北京":
        # 若 extra 仍停留在北京默认值，则自动切换为目标城市码
        if preset.get("adcode"):
            if cfg["extra"].get("bili_area") in ("", "110000"):
                cfg["extra"]["bili_area"] = preset["adcode"]
            if cfg["extra"].get("cpp_city") in ("", "110000"):
                cfg["extra"]["cpp_city"] = preset["adcode"]
        if preset.get("damai_city") and cfg["extra"].get("damai_city") in ("", "101010100"):
            cfg["extra"]["damai_city"] = preset["damai_city"]
        # 090expo 若固定了北京排期文章，切城市后清空以便自动去列表页发现目标城市文章
        if "7772" in str(cfg["extra"].get("expo090_article", "")):
            cfg["extra"]["expo090_article"] = ""

    # 渲染或替换 [sources] 里的城市占位符与北京默认 URL
    # ★ 每次都从「未渲染模板」重新生成，不能在上一轮已替换的结果上再替换。
    #   sources 可能是普通 dict（测试/外部调用），此时退回用当前值当模板。
    templates = getattr(sources, "templates", None)
    base = dict(templates) if templates else dict(sources)
    if base:
        tpl_vars = {
            "city": city,
            "city_url": city_url,
            "bendibao_city": preset.get("bendibao_city") or "bj",
            "douban_city": preset.get("douban_city") or "beijing",
            "artron_code": preset.get("artron_code") or ("0-%s" % city_url),
            "datexpo_code": DATEXPO_CITY_CODES.get(city, "1_1_0_0_0"),
        }
        rendered = {}
        for sname, raw_row in list(base.items()):
            sname_low = sname.strip().lower()
            # 非北京城市自动关闭北京专属场馆源（国博/国家大剧院/国家会议中心）
            if city != "北京" and sname_low in BEIJING_ONLY_SOURCES:
                parsed = parse_source_row(raw_row)
                if parsed and parsed["enabled"]:
                    rendered[sname] = "%s|off|%s" % (parsed["kind"], parsed["url"])
                else:
                    rendered[sname] = raw_row
                continue

            new_row = raw_row
            for k, v in tpl_vars.items():
                new_row = new_row.replace("{" + k + "}", v)

            if city != "北京":
                # 兼容手工写死北京 URL 的旧配置（模板里没有占位符）
                new_row = new_row.replace("city=%E5%8C%97%E4%BA%AC", "city=%s" % city_url)
                new_row = new_row.replace("1-%E5%8C%97%E4%BA%AC-0", "%s-0" % tpl_vars["artron_code"])
                if preset.get("bendibao_city"):
                    new_row = new_row.replace("m.bj.bendibao.com", "m.%s.bendibao.com" % preset["bendibao_city"])
                if preset.get("douban_city"):
                    new_row = new_row.replace("beijing.douban.com", "%s.douban.com" % preset["douban_city"])
                    new_row = new_row.replace("/city/beijing", "/city/%s" % preset["douban_city"])
                    new_row = new_row.replace("/event/beijing", "/event/%s" % preset["douban_city"])
                    new_row = new_row.replace("/beijing/", "/%s/" % preset["douban_city"])
            rendered[sname] = new_row
        sources.clear()
        sources.update(rendered)
        if templates is not None:
            templates.clear()
            templates.update(base)


def as_int(cfg, section, key, fallback):
    try:
        return int(str(cfg[section][key]).strip())
    except Exception:
        return fallback


def as_float(cfg, section, key, fallback):
    try:
        return float(str(cfg[section][key]).strip())
    except Exception:
        return fallback


def as_bool(cfg, section, key, fallback=False):
    raw = str(cfg[section].get(key, "")).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return fallback


def csv_list(cfg, section, key, fallback=""):
    raw = str(cfg[section].get(key, fallback) or "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def abspath(rel):
    """配置里的相对路径一律相对项目根, 不依赖 cwd —— cron 的 cwd 不可信。"""
    if not rel:
        return rel
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel)


def parse_source_row(val):
    """解析 [sources] 的一行:  "H|on |http://example.com/page"。

    返回 dict, 解析失败返回 None。空格容忍, 因为手工编辑很容易多打空格。
    """
    # 行尾允许写 # 注释（configparser 的 inline_comment 在自定义行格式里不生效，
    # 手工在 [sources] 行尾加说明会把注释拼进 URL，导致必然 404）
    val = (val or "").split("#", 1)[0]
    parts = [x.strip() for x in val.split("|")]
    if len(parts) < 3:
        return None
    kind, enabled, url = parts[0].upper(), parts[1].lower(), parts[2]
    if kind not in ("H", "J", "S"):
        return None
    if not url.startswith("http"):
        return None
    return {
        "kind": kind,
        "enabled": enabled in ("on", "true", "1", "yes"),
        "url": url,
    }
