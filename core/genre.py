# -*- coding: utf-8 -*-
"""活动品类 —— 给每条活动打一个稳定的「品类」标签，供用户按品类开关。

为什么不再用一长串 exclude_keywords:
  之前是在 config 里列 30 多个关键词(话剧/戏剧/喜剧/京剧...)直接丢弃。
  这套机制的问题是「一刀切」: 要么全丢, 要么全留, 用户无法只留音乐会而丢脱口秀。
  改成先归品类、再按品类开关, 既保留了丢弃能力, 又让开关的粒度变成品类而不是词。

判定顺序: 先看名字里的确定性关键词(戏/剧/喜剧等), 再看 category(A/B/C)兜底。
★ 只看名字不看 note: note 是 LLM 写的一句话看点, 波动大, 拿它判品类会来回跳。
"""
import re

# 品类目录: key 是配置里的开关名, value 是给人看的中文名。
# 顺序即前端展示顺序。
GENRES = {
    "expo":     "展会 / 博览会",
    "anime":    "漫展 / 同人",
    "museum":   "博物馆 / 美术展",
    "concert":  "音乐会 / 演唱会",
    "dance":    "舞蹈 / 芭蕾",
    "theater":  "话剧 / 音乐剧",
    "opera":    "戏曲 / 歌剧",
    "comedy":   "脱口秀 / 相声",
    "market":   "市集 / 快闪",
    "festival": "节庆 / 户外",
    "sports":   "运动 / 赛事",
    "tech":     "技术 / 开发者",
    "other":    "其他",
}

# 判定规则: (品类, 正则)。按顺序匹配, 先命中先得 —— 所以把更具体的放前面。
# ★ expo 必须排在 museum 之前: 否则「茶产业博览会」会先被 museum 的泛「展」字规则吃掉。
_RULES = [
    ("comedy",   re.compile(r"脱口秀|单口喜剧|即兴喜剧|开放麦|相声|漫才|喜剧|笑场|包袱|德云社|开心麻花|小剧场")),
    ("anime",    re.compile(r"漫展|同人|only|only展|动漫|二次元|cosplay|谷子|痛包|嘉年华|ACG|BACC|CPP|GA\d", re.I)),
    ("opera",    re.compile(r"戏曲|京剧|昆曲|越剧|黄梅戏|评剧|豫剧|歌剧|音乐剧|舞台剧")),
    ("theater",  re.compile(r"话剧|戏剧|沉浸剧|餐食剧|独角戏|戏剧场")),
    ("dance",    re.compile(r"舞蹈|芭蕾|舞剧|弗拉明戈|现代舞")),
    ("concert",  re.compile(r"音乐会|演唱会|交响|合唱|独奏|室内乐|管弦|民乐|音乐节|巡演")),
    ("market",   re.compile(r"市集|快闪|手作|摆摊|文创|集市")),
    # 节庆/户外: 红叶、银杏、菊花、赏花这类季节性公园活动
    ("festival", re.compile(r"红叶|银杏|菊花|赏花|樱花|梅花|潮玩节|庙会|灯会|冰雪节|文化节|旅游节|采摘")),
    ("sports",   re.compile(r"马拉松|赛事|运动会|骑行|徒步|球赛|电竞|比赛|越野|跑步")),
    ("expo",     re.compile(r"博览会|博会|展会|会展|交易会|展览会|峰会|论坛|大会|产业展")),
    ("museum",   re.compile(r"博物馆|美术馆|画展|艺术展|特展|展览|文博|故宫|国博|艺术馆|影像展|摄影展|大展|作品展|邀请展|文献展|科普展|油画|绘画|雕塑|艺术|展")),
    ("tech",     re.compile(r"开发者|开源|技术沙龙|meetup|hackathon|黑客松|AI|编程|代码|工程|demo", re.I)),
]


def genre_of(name, category=None):
    """按名字 + A/B/C 分类给出品类 key。永远返回 GENRES 里的某个 key。"""
    n = str(name or "")
    for g, pat in _RULES:
        if pat.search(n):
            return g
    # 名字没有信号时按抓取分类兜底。
    # ★ A 类兜底为 museum 而不是 other: A 类里没有关键词的条目实测几乎都是
    #   书画/文献展这类标题不带「展」字的展览(如「决不回刀——齐白石的金石文心」)。
    if category == "B":
        return "tech"
    if category == "C":
        return "expo"
    if category == "A":
        return "museum"
    return "other"


def genre_label(g):
    return GENRES.get(g, GENRES["other"])


def parse_disabled(raw):
    """把配置里的 `theater=off,comedy=off` 解析成被关闭的品类集合。"""
    disabled = set()
    for item in str(raw or "").replace("，", ",").split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        if v.strip().lower() in ("off", "false", "0", "no"):
            disabled.add(k.strip())
    return disabled


def dump_genres(disabled):
    """反向: 被关闭集合 -> 配置串。写回 config.ini 用。"""
    return ",".join("%s=off" % g for g in GENRES if g in disabled)
