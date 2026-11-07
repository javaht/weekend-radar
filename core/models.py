# -*- coding: utf-8 -*-
"""数据模型 —— 所有源最终都收敛到这里, 下游只认 Event。

指纹（fingerprint）的用途:
  判断「这条活动相比上轮有没有实质变化」。只比指纹而不比整条记录,
  是因为 last_seen 每次都会变, 比整条会天天误报 NEW。
"""
import hashlib
import re
from dataclasses import dataclass, field, asdict

# 归一化时去掉的噪音: 空白、常见装饰标点、地区前缀
_STRIP_RE = re.compile(r"[\s·・\-—_/\\|:：,，.。!！?？()（）\[\]【】「」『』\"'“”‘’]+")
# 场馆串里的地址噪音：括号内容、门牌号、地铁指引等
_VENUE_NOISE_RE = re.compile(r"[（(][^）)]*[）)]")
_VENUE_PUNCT_RE = re.compile(r"[\s·・\-—_/\|:：,，.。!！?？]")
_PRICE_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
# 修复前导零正则: 仅匹配非数字后面的 0+数字 (如 06 -> 6)，避免把 2026 换成 226
_LEADING_ZERO_RE = re.compile(r"(?<!\d)0+(\d+)")

# 知名场馆/机构冒号前缀 (如 "国家大剧院：捷杰耶夫音乐会" -> "捷杰耶夫音乐会")
_INSTITUTION_PREFIX_RE = re.compile(
    r"^(?:国家大剧院|北京保利剧院|保利剧院|天桥艺术中心|北京国际会议中心|"
    r"国家会议中心|中国国家博物馆|故宫博物院|中国美术馆|首都博物馆|"
    r"中国国际展览中心|首钢会展中心|首都国际会展中心|上海大剧院|上海文化广场|"
    r"上海博物馆|中华艺术宫|浦东美术馆|西岸美术馆)[·・\-—:：\s]+"
)

_DEFAULT_BJ_ALIASES = [
    (r"^国展", "中国国际展览中心"),
    (r"^北展", "北京展览馆"),
    (r"^(?:全国农展馆?|农展馆?)", "全国农业展览馆"),
    (r"^(?:国博|国家博物馆)", "中国国家博物馆"),
    (r"^工体", "工人体育场"),
    (r"^首体", "首都体育馆"),
]

CURRENT_CITY = "北京"
_CITY_PREFIX_RE = None
_NOISE_TAGS_RE = None
_SUFFIX_CITY_RE = None
VENUE_ALIASES = []


def configure_city(city_name="北京", venue_aliases=None):
    """按目标城市动态编译名称清洗正则与场馆简称映射。"""
    global CURRENT_CITY, _CITY_PREFIX_RE, _NOISE_TAGS_RE, _SUFFIX_CITY_RE, VENUE_ALIASES
    c = str(city_name or "北京").strip()
    if len(c) > 2 and c.endswith("市"):
        c = c[:-1]
    CURRENT_CITY = c or "北京"
    esc = re.escape(CURRENT_CITY)

    _CITY_PREFIX_RE = re.compile(r"^(?:%s|%s市|北京|北京市)[·・\-—:：\s]*" % (esc, esc))
    _NOISE_TAGS_RE = re.compile(
        r"[【\[（(](?:%s|%s站|%s场|北京|北京站|北京场|大麦|大麦专享|秀动|猫眼|专题|品牌展会|"
        r"早鸟|早鸟特惠|特展|常设展|巡展|预售|现场票|官方[预售]*)[】\]）)]" % (esc, esc, esc)
    )
    _SUFFIX_CITY_RE = re.compile(r"[·・\-—\s]*(?:%s站|%s场|北京站|北京场)$" % (esc, esc))

    if venue_aliases is None:
        try:
            from core.config import get_city_preset
            raw_aliases = get_city_preset(CURRENT_CITY).get("venue_aliases") or []
        except Exception:
            raw_aliases = _DEFAULT_BJ_ALIASES if CURRENT_CITY == "北京" else []
    else:
        raw_aliases = venue_aliases

    compiled = []
    for pat, repl in raw_aliases:
        compiled.append((re.compile(pat) if isinstance(pat, str) else pat, repl))
    VENUE_ALIASES = compiled


configure_city("北京", _DEFAULT_BJ_ALIASES)


def normalize_venue(venue):
    """场馆名归一：去括号地址、去标点空白、映射别名。

    只用于「是不是同一个地方」的判断，不用于展示。
    """
    if not venue:
        return ""
    s = _CITY_PREFIX_RE.sub("", str(venue).strip())
    s = _VENUE_NOISE_RE.sub("", s)
    s = _VENUE_PUNCT_RE.sub("", s).lower()
    for pat, repl in VENUE_ALIASES:
        s = pat.sub(repl, s)
    return s


def venue_same(a, b):
    """两个场馆串是否指同一场馆。

    ★ 必须容忍详细程度差异：不同源给「北京酷车国际汇展中心金蝉西路甲1号」
    和「北京酷车国际汇展中心」，直接比字符串会天天报假冲突，
    把真冲突（如 BACC 的大红门 vs 酷车）淹掉。
    """
    na, nb = normalize_venue(a), normalize_venue(b)
    if not na or not nb:
        return True          # 有一方缺失就不判冲突
    return na.startswith(nb) or nb.startswith(na)


def price_anchor(price_text):
    """票价的稳定锚点：取所有数字里最小的那个（即最低票价）。

    为什么不直接拿整串做指纹：LLM 每天转述同一场活动，
    「¥80(预售票)¥90(现场票)¥158(VIP票)」和「¥80-158」是同一件事，
    但字符串不同。拿最低价做锚点，这两种写法都归一到 80。
    """
    nums = [float(x) for x in _PRICE_NUM_RE.findall(str(price_text or ""))]
    if not nums:
        return ""
    return "%.2f" % min(nums)


# 价格档位: 免费 / 100 以下 / 100-300 / 300 以上 / 未知
PRICE_TIERS = ("free", "under100", "mid", "high", "unknown")
PRICE_TIER_LABELS = {
    "free": "免费",
    "under100": "¥100 以下",
    "mid": "¥100-300",
    "high": "¥300 以上",
    "unknown": "价格待核实",
}

# 「有票但不给价」的常见措辞。这些明确表示**要买票**, 所以 is_free 应为 False,
# 而不是「未知」。区分它们和空串很重要:
#   空串 = 页面没提票价 (真未知, 可能是免费展览)
#   「购票见详情」= 明确要买票 (收费, 只是没给数字)
PAID_HINT_RE = re.compile(r"购票|买票|订票|票价|预售|门票|元|￥|¥|收费|报名费")


def infer_is_free(price_text, is_free=None):
    """补全 is_free 的三态。

    ★ 这是本轮修的判定 bug: 之前「购票见详情」被当成 is_free=None(未知),
      导致 81 条活动里大量本该标「收费」的条目落进未知区, 「仅看免费」筛出来的
      6 条还混着这些。有购票字样就必须是 False。
    """
    if is_free is True or is_free is False:
        return is_free
    s = str(price_text or "").strip()
    if not s:
        return None                      # 页面没提, 真未知
    if "免费" in s:
        return True
    if PAID_HINT_RE.search(s):
        return False
    return None


def price_tier(price_text, is_free=None):
    """把票价串归到价格档位。供前端做价格筛选。

    以最低价为准(price_anchor): 有 0 元档就算「免费」档存在, 用户可以零成本去。
    """
    if is_free is True:
        return "free"
    s = str(price_text or "").strip()
    if not s:
        return "unknown"
    if "免费" in s:
        return "free"
    anchor = price_anchor(s)
    if not anchor:
        return "unknown"
    try:
        v = float(anchor)
    except ValueError:
        return "unknown"
    if v <= 0:
        return "free"
    if v < 100:
        return "under100"
    if v < 300:
        return "mid"
    return "high"


def normalize_name(name):
    """把「北京·BACC 动漫游戏嘉年华06」和「BACC动漫游戏嘉年华6」尽量收敛。

    只做保守归一（去标点空白/去北京前缀/数字补齐/营销标签剥离）, 不做模糊匹配 ——
    宁可漏合并, 不可错合并, 错合并会把两场活动变成'取消'。
    """
    if not name:
        return ""
    s = str(name).strip()
    s = _NOISE_TAGS_RE.sub("", s)
    s = _SUFFIX_CITY_RE.sub("", s)
    s = _CITY_PREFIX_RE.sub("", s)
    s = _INSTITUTION_PREFIX_RE.sub("", s)
    s = _STRIP_RE.sub("", s).lower()
    s = _LEADING_ZERO_RE.sub(r"\g<1>", s)
    return s


@dataclass
class Event:
    name: str = ""
    start_date: str = ""          # ISO, 空表示日期未知
    end_date: str = ""            # ISO, 单日活动与 start_date 相同
    venue: str = ""
    price_text: str = ""          # 原始票价串, 不解析成数字
    is_free: object = None        # True/False/None(未知) —— 三态, 不要用 False 表示未知
    category: str = "A"           # A 展会/漫展/市集/演艺  B 技术  C 博览会
    url: str = ""
    source: str = ""
    deadline: str = ""            # 报名截止, 多数为空
    has_goods: object = None      # 有实物(周边/集章/赠品) —— 三态
    note: str = ""                # 一句话看点
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.end_date:
            self.end_date = self.start_date
        if self.name:
            self.name = str(self.name).strip()

    @property
    def name_norm(self):
        return normalize_name(self.name)

    @property
    def fingerprint(self):
        """变更指纹 —— 只放「实质」字段，全部先归一。

        ★ 刻意不含原始 venue / price_text：
          LLM 每次转述的措辞会变，拿原串做指纹会让「同一天跑两次」被判定为
          「14 条改价、2 条取消」这种纯属虚构的变化（实测踩过）。
          场馆用 normalize_venue，票价用 price_anchor（最低价）。
        """
        raw = "|".join([
            self.name_norm, self.start_date or "", self.end_date or "",
            price_anchor(self.price_text), normalize_venue(self.venue)[:6],
        ])
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]

    def to_row(self):
        d = asdict(self)
        d["extra"] = ""  # 不进库
        d["name_norm"] = self.name_norm
        d["fingerprint"] = self.fingerprint
        return d


def tri_to_db(v):
    """三态布尔入库: None -> NULL, 避免和 False 混淆。"""
    return None if v is None else (1 if v else 0)


def tri_from_db(v):
    return None if v is None else bool(v)
