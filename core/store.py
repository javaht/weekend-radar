# -*- coding: utf-8 -*-
"""SQLite 状态库 + 差分 —— 「新增与变化」的唯一数据来源。

为什么用 SQLite 而不是每次写一个 JSONL 快照:
  改地址/降价/取消 这三类变化需要「按活动身份」比对历史, 不是按文件比对。
  SQLite 有唯一键, 一条 UPDATE 就够; JSONL 得自己扫全量再求差集。

★ 关键安全语义:
  只有「本轮所有源都成功」时才允许把缺席的活动标成 gone。
  任何一个源失败, 该源独有的活动就会缺席 —— 如果照标 gone, 明天你会看到
  一堆"活动取消"的假警报, 实际上只是那个网站超时了。
"""
import json
import os
import sqlite3

from core.models import venue_same, price_anchor

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  name_norm   TEXT NOT NULL,
  start_date  TEXT,
  end_date    TEXT,
  venue       TEXT,
  price_text  TEXT,
  is_free     INTEGER,
  category    TEXT,
  url         TEXT,
  source      TEXT,
  deadline    TEXT,
  has_goods   INTEGER,
  i_friendly  INTEGER,
  note        TEXT,
  fingerprint TEXT NOT NULL,
  first_seen  TEXT,
  last_seen   TEXT,
  miss_count  INTEGER DEFAULT 0,
  key_chg_count INTEGER DEFAULT 0,
  status      TEXT DEFAULT 'active'
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_events
  ON events(name_norm, IFNULL(start_date,''), IFNULL(end_date,''));
CREATE INDEX IF NOT EXISTS ix_events_status ON events(status);

CREATE TABLE IF NOT EXISTS runs(
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date  TEXT,
  total     INTEGER,
  added     INTEGER,
  changed   INTEGER,
  gone      INTEGER,
  sources_ok INTEGER,
  sources_fail INTEGER,
  detail    TEXT
);
"""


def _tri(v):
    return None if v is None else (1 if v else 0)


class Store:
    def __init__(self, path, log=print, gone_after=2, stable_sources=None,
                 confirm_runs=2):
        self.path = path
        self.log = log
        # 连续缺席多少轮才判定"取消"
        self.gone_after = max(1, int(gone_after))
        # ★ 只有确定性源才允许判"取消"。LLM 抽取的源每轮覆盖条数会波动
        #   （实测同一批数据在 26-39 条之间跳），把它们的缺席当取消会天天误报。
        self.stable_sources = set(stable_sources or [])
        # 一次变更要连续几轮成立才认。
        # ★ 不要设为 1：LLM 转述措辞会漂，设 1 会天天报"改价/改地址"并触发推送。
        self.confirm_runs = max(1, int(confirm_runs))
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        for col in ("miss_count INTEGER DEFAULT 0",
                    "key_chg_count INTEGER DEFAULT 0"):
            try:
                self.conn.execute("ALTER TABLE events ADD COLUMN " + col)
            except Exception:
                pass
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def _key(self, ev):
        return (ev.name_norm, ev.start_date or "", ev.end_date or "")

    def active_map(self):
        """当前库里所有 active 的活动, 以唯一键索引。"""
        cur = self.conn.execute("SELECT * FROM events WHERE status='active'")
        out = {}
        for row in cur:
            k = (row["name_norm"], row["start_date"] or "", row["end_date"] or "")
            out[k] = row
        return out

    def apply(self, events, today_iso, all_sources_ok, source_stats):
        """把本轮结果并入库, 返回差分报告。

        ★ 变更确认机制（这套东西能长期跑而不天天误报的关键）：
          指纹变了不等于真变了 —— LLM 每轮转述的措辞/覆盖范围都会漂。
          所以一次"变化"要连续 confirm_runs 轮都成立才认，未确认前保留旧值、
          不写进报告。代价是真实变更会晚一天体现，换来的是推送不抽风。
        """
        prev = self.active_map()
        seen_keys = set()
        new_list, changed_list, unchanged = [], [], 0

        for ev in events:
            k = self._key(ev)
            seen_keys.add(k)
            old = prev.get(k)

            if old is None:
                row = (
                    ev.name, ev.name_norm, ev.start_date, ev.end_date, ev.venue,
                    ev.price_text, _tri(ev.is_free), ev.category, ev.url, ev.source,
                    ev.deadline, _tri(ev.has_goods), _tri(ev.i_friendly), ev.note,
                    ev.fingerprint,
                )
                self.conn.execute(
                    "INSERT OR IGNORE INTO events"
                    "(name,name_norm,start_date,end_date,venue,price_text,is_free,"
                    " category,url,source,deadline,has_goods,i_friendly,note,"
                    " fingerprint,first_seen,last_seen,status)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active')",
                    row + (today_iso, today_iso))
                new_list.append(ev)
                continue

            if old["fingerprint"] == ev.fingerprint:
                self._write(old, ev, today_iso, "", 0)
                unchanged += 1
                continue

            chg = (old["key_chg_count"] or 0) + 1
            if chg < self.confirm_runs:
                # 未确认：只记计数与 last_seen，保留旧展示值，不进报告
                self.conn.execute(
                    "UPDATE events SET key_chg_count=?, last_seen=?, miss_count=0"
                    " WHERE id=?", (chg, today_iso, old["id"]))
                unchanged += 1
                continue

            kind = self._classify(old, ev)
            self._write(old, ev, today_iso, kind, 0)
            if kind:
                changed_list.append({
                    "event": ev,
                    "old_venue": old["venue"],
                    "old_price": old["price_text"],
                    "old_start": old["start_date"],
                    "kind": kind,
                })
            else:
                unchanged += 1

        gone_list = []
        if all_sources_ok:
            for k, row in prev.items():
                if k in seen_keys:
                    continue
                # ★ LLM 抽取源不参与"取消"判定：它们每轮覆盖条数会波动，
                #   缺席往往只是这轮没抽到，不是活动办不成了。
                if (row["source"] or "") not in self.stable_sources:
                    continue
                misses = (row["miss_count"] or 0) + 1
                if misses >= self.gone_after:
                    self.conn.execute(
                        "UPDATE events SET status='gone', miss_count=?, last_seen=? WHERE id=?",
                        (misses, today_iso, row["id"]))
                    gone_list.append(row)
                else:
                    # 先记一次缺席，下一轮还缺再判取消
                    self.conn.execute("UPDATE events SET miss_count=? WHERE id=?",
                                      (misses, row["id"]))
        elif prev:
            self.log("  [警告] 有源失败, 本轮跳过「取消」判定, 避免误报")
        self.conn.commit()

        self.conn.execute(
            "INSERT INTO runs(run_date,total,added,changed,gone,sources_ok,sources_fail,detail)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (today_iso, len(events), len(new_list), len(changed_list), len(gone_list),
             sum(1 for s in source_stats if s["ok"]),
             sum(1 for s in source_stats if not s["ok"]),
             json.dumps([{k: s[k] for k in ("name", "ok", "count", "err")}
                         for s in source_stats], ensure_ascii=False)))
        self.conn.commit()

        return {"new": new_list, "changed": changed_list, "gone": gone_list,
                "unchanged": unchanged}

    def _write(self, old, ev, today_iso, kind, chg):
        """把本轮的展示字段写回去，并重置缺席/变更计数。"""
        self.conn.execute(
            "UPDATE events SET name=?,name_norm=?,start_date=?,end_date=?,"
            "venue=?,price_text=?,is_free=?,category=?,url=?,source=?,"
            "deadline=?,has_goods=?,i_friendly=?,note=?,fingerprint=?,"
            "last_seen=?,status='active',miss_count=0,key_chg_count=? WHERE id=?",
            (ev.name, ev.name_norm, ev.start_date, ev.end_date, ev.venue,
             ev.price_text, _tri(ev.is_free), ev.category, ev.url, ev.source,
             ev.deadline, _tri(ev.has_goods), _tri(ev.i_friendly), ev.note,
             ev.fingerprint, today_iso, chg, old["id"]))

    @staticmethod
    def _classify(old_row, ev):
        """一条记录到底改了什么实质内容。没有实质变化返回空串。

        判定全部走归一后的值：票价比 price_anchor（最低价），场馆用 venue_same。
        直接比原串会把 LLM 的措辞波动当成变更。
        """
        kinds = []
        if not venue_same(old_row["venue"], ev.venue):
            kinds.append("改地址")
        if price_anchor(old_row["price_text"]) != price_anchor(ev.price_text):
            kinds.append("改价")
        if (old_row["start_date"] or "") != (ev.start_date or ""):
            kinds.append("改期")
        return "+".join(kinds)

    def last_run_date(self):
        cur = self.conn.execute("SELECT run_date FROM runs ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        return row["run_date"] if row else ""
