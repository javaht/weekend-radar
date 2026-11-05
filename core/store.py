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
import datetime
import json
import os
import sqlite3

from core.models import venue_same, price_anchor, price_tier as price_tier_of

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
  note        TEXT,
  fingerprint TEXT NOT NULL,
  first_seen  TEXT,
  last_seen   TEXT,
  miss_count  INTEGER DEFAULT 0,
  key_chg_count INTEGER DEFAULT 0,
  pending_fp  TEXT,
  sources     TEXT,
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


def _tri_in(v):
    """DB 里的 0/1/NULL 还原成 Python 三态，供派生计算用。"""
    if v is None:
        return None
    return True if v == 1 else False


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
                    "key_chg_count INTEGER DEFAULT 0",
                    "pending_fp TEXT",
                    "sources TEXT"):
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

    @staticmethod
    def _row_key(row):
        return (row["name_norm"], row["start_date"] or "", row["end_date"] or "")

    @staticmethod
    def _sources_of(ev):
        srcs = list(ev.extra.get("sources") or []) if ev.extra else []
        if ev.source and ev.source not in srcs:
            srcs.insert(0, ev.source)
        return ",".join(srcs)

    def _is_stable(self, row):
        """该活动是否被任一确定性源报告过（合并后 source 只记首个源, 不能只看它）。"""
        srcs = {x for x in (row["sources"] or "").split(",") if x}
        srcs.add(row["source"] or "")
        return bool(srcs & self.stable_sources)

    def active_map(self):
        """当前库里所有 active 的活动, 以唯一键索引。"""
        cur = self.conn.execute("SELECT * FROM events WHERE status='active'")
        return {self._row_key(row): row for row in cur}

    def expire(self, today_iso):
        """已结束的活动转为 expired: 不再参与差分, 也不会被误判成「取消」。"""
        cur = self.conn.execute(
            "UPDATE events SET status='expired' WHERE status='active'"
            " AND COALESCE(NULLIF(end_date,''), NULLIF(start_date,'')) < ?",
            (today_iso,))
        if cur.rowcount:
            self.log("  [i] %d 条活动已结束, 归档为 expired" % cur.rowcount)
        return cur.rowcount

    # 同名活动的开始日期相差多少天以内, 视为「同一场活动改了期/档期抖动」
    RESCHEDULE_DAYS = 60

    def _find_moved(self, ev, by_name, claimed, round_keys):
        """唯一键没命中时, 找同名、日期相近、本轮没被精确命中的旧记录。

        ★ 唯一键含日期, 所以改期 / 跨源合并导致的日期区间抖动都会换 key。
          不做这一步, 就会报一条假 NEW, 旧记录永远挂在库里, 「改期」也永远检不出。
        """
        try:
            sd = datetime.date.fromisoformat(ev.start_date)
        except (TypeError, ValueError):
            return None
        best, best_gap = None, None
        for row in by_name.get(ev.name_norm, ()):
            if row["id"] in claimed or self._row_key(row) in round_keys:
                continue
            try:
                gap = abs((datetime.date.fromisoformat(row["start_date"]) - sd).days)
            except (TypeError, ValueError):
                continue
            if gap <= self.RESCHEDULE_DAYS and (best_gap is None or gap < best_gap):
                best, best_gap = row, gap
        return best

    def _insert(self, ev, today_iso):
        """新活动入库。若同键的 gone/expired 旧行还在, 复活它而不是被唯一索引静默吞掉。"""
        dead = self.conn.execute(
            "SELECT id FROM events WHERE status!='active' AND name_norm=?"
            " AND IFNULL(start_date,'')=? AND IFNULL(end_date,'')=?",
            self._key(ev)).fetchone()
        if dead:
            self._write(dead, ev, today_iso, "", 0)
            self.conn.execute("UPDATE events SET first_seen=? WHERE id=?",
                              (today_iso, dead["id"]))
            return
        self.conn.execute(
            "INSERT INTO events"
            "(name,name_norm,start_date,end_date,venue,price_text,is_free,"
            " category,url,source,sources,deadline,has_goods,note,"
            " fingerprint,first_seen,last_seen,status)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active')",
            (ev.name, ev.name_norm, ev.start_date, ev.end_date, ev.venue,
             ev.price_text, _tri(ev.is_free), ev.category, ev.url, ev.source,
             self._sources_of(ev), ev.deadline, _tri(ev.has_goods),
             ev.note, ev.fingerprint, today_iso, today_iso))

    def apply(self, events, today_iso, all_sources_ok, source_stats):
        """把本轮结果并入库, 返回差分报告。

        ★ 变更确认机制（这套东西能长期跑而不天天误报的关键）：
          指纹变了不等于真变了 —— LLM 每轮转述的措辞/覆盖范围都会漂。
          所以一次"变化"要连续 confirm_runs 轮都变成**同一个**新指纹才认，
          未确认前保留旧值、不写进报告。代价是真实变更会晚一天体现，换来的是推送不抽风。
        """
        self.expire(today_iso)
        prev = self.active_map()
        by_name = {}
        for row in prev.values():
            by_name.setdefault(row["name_norm"], []).append(row)
        round_keys = {self._key(ev) for ev in events}
        claimed = set()
        new_list, changed_list, unchanged = [], [], 0

        for ev in events:
            k = self._key(ev)
            old = prev.get(k)
            if old is not None and old["id"] in claimed:
                old = None
            if old is None:
                old = self._find_moved(ev, by_name, claimed, round_keys)

            if old is None:
                self._insert(ev, today_iso)
                new_list.append(ev)
                continue
            claimed.add(old["id"])

            if old["fingerprint"] == ev.fingerprint:
                self._write(old, ev, today_iso, "", 0)
                unchanged += 1
                continue

            # 只有连续几轮都变成「同一个」新指纹才算数; 来回乱跳的不累计
            if old["pending_fp"] == ev.fingerprint:
                chg = (old["key_chg_count"] or 0) + 1
            else:
                chg = 1
            if chg < self.confirm_runs:
                # 未确认：只记计数与 last_seen，保留旧展示值，不进报告
                self.conn.execute(
                    "UPDATE events SET key_chg_count=?, pending_fp=?, last_seen=?,"
                    " miss_count=0 WHERE id=?",
                    (chg, ev.fingerprint, today_iso, old["id"]))
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
            for row in prev.values():
                if row["id"] in claimed:
                    continue
                # ★ LLM 抽取源不参与"取消"判定：它们每轮覆盖条数会波动，
                #   缺席往往只是这轮没抽到，不是活动办不成了。
                if not self._is_stable(row):
                    continue
                misses = (row["miss_count"] or 0) + 1
                if misses >= self.gone_after:
                    self.conn.execute(
                        "UPDATE events SET status='gone', miss_count=?, last_seen=? WHERE id=?",
                        (misses, today_iso, row["id"]))
                    gone_list.append(dict(row))
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
        # 改期后的新键可能撞上一条已归档(gone/expired)的旧行: 它已被本行取代, 删掉,
        # 否则 UPDATE 会触发唯一索引冲突
        self.conn.execute(
            "DELETE FROM events WHERE status!='active' AND id!=? AND name_norm=?"
            " AND IFNULL(start_date,'')=? AND IFNULL(end_date,'')=?",
            (old["id"],) + self._key(ev))
        self.conn.execute(
            "UPDATE events SET name=?,name_norm=?,start_date=?,end_date=?,"
            "venue=?,price_text=?,is_free=?,category=?,url=?,source=?,sources=?,"
            "deadline=?,has_goods=?,note=?,fingerprint=?,"
            "last_seen=?,status='active',miss_count=0,key_chg_count=?,"
            "pending_fp=NULL WHERE id=?",
            (ev.name, ev.name_norm, ev.start_date, ev.end_date, ev.venue,
             ev.price_text, _tri(ev.is_free), ev.category, ev.url, ev.source,
             self._sources_of(ev), ev.deadline, _tri(ev.has_goods),
             ev.note, ev.fingerprint, today_iso, chg,
             old["id"]))

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

    def query_events(self, status="active", category=None, is_free=None,
                     has_goods=None, keyword=None, price_tier=None,
                     start_date_ge=None, start_date_le=None,
                     limit=100, offset=0):
        """Web 端灵活查询 events 表。

        price_tier 是派生筛选(免费/¥100以下/¥100-300/¥300以上), 不走 SQL 列,
        因为档位要按 price_text 现算 —— 见 core/models.py 的 price_tier()。
        """
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if is_free is not None:
            clauses.append("is_free = ?")
            params.append(1 if is_free else 0)
        if has_goods is not None:
            clauses.append("has_goods = ?")
            params.append(1 if has_goods else 0)
        if keyword:
            kw = "%%%s%%" % keyword.strip()
            clauses.append("(name LIKE ? OR venue LIKE ? OR note LIKE ?)")
            params.extend([kw, kw, kw])
        if start_date_ge:
            clauses.append("start_date >= ?")
            params.append(start_date_ge)
        if start_date_le:
            clauses.append("start_date <= ?")
            params.append(start_date_le)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        if price_tier:
            # 档位筛选无法下推到 SQL: 先取全量再在内存里过滤
            cur = self.conn.execute(
                "SELECT * FROM events %s ORDER BY start_date ASC, id DESC" % where, params)
            raw = [dict(r) for r in cur.fetchall()]
            items = []
            for it in raw:
                tier = price_tier_of(it.get("price_text"),
                                     _tri_in(it.get("is_free")))
                if tier != price_tier:
                    continue
                it["price_tier"] = tier
                items.append(it)
            total = len(items)
            start = max(0, int(offset))
            return {"total": total,
                    "items": items[start:start + max(1, int(limit))]}

        cnt_cur = self.conn.execute("SELECT COUNT(*) AS total FROM events %s" % where, params)
        total = cnt_cur.fetchone()["total"]

        sql = ("SELECT * FROM events %s ORDER BY start_date ASC, id DESC LIMIT ? OFFSET ?"
               % where)
        cur = self.conn.execute(sql, params + [max(1, int(limit)), max(0, int(offset))])
        items = [dict(r) for r in cur.fetchall()]
        return {"total": total, "items": items}

    def get_active_events_as_objects(self):
        """读取所有 active 活动并转换为 Event 对象，供 _layout 分组渲染复用。"""
        from core.models import Event
        cur = self.conn.execute(
            "SELECT * FROM events WHERE status='active' ORDER BY start_date ASC"
        )
        events = []
        for r in cur.fetchall():
            row = dict(r)
            ev = Event(
                name=row.get("name") or "",
                start_date=row.get("start_date") or "",
                end_date=row.get("end_date") or "",
                venue=row.get("venue") or "",
                price_text=row.get("price_text") or "",
                is_free=True if row.get("is_free") == 1 else (False if row.get("is_free") == 0 else None),
                category=row.get("category") or "A",
                url=row.get("url") or "",
                source=row.get("source") or "",
                deadline=row.get("deadline") or "",
                has_goods=True if row.get("has_goods") == 1 else (False if row.get("has_goods") == 0 else None),
                note=row.get("note") or "",
            )
            ev.extra["db_id"] = row.get("id")
            ev.extra["sources"] = row.get("sources") or row.get("source") or ""
            ev.extra["first_seen"] = row.get("first_seen") or ""
            ev.extra["last_seen"] = row.get("last_seen") or ""
            events.append(ev)
        return events

    def get_runs(self, limit=20, offset=0):
        """查询运行历史批次。"""
        cnt_cur = self.conn.execute("SELECT COUNT(*) AS total FROM runs")
        total = cnt_cur.fetchone()["total"]

        cur = self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?",
            (max(1, int(limit)), max(0, int(offset)))
        )
        items = []
        for r in cur.fetchall():
            d = dict(r)
            if d.get("detail"):
                try:
                    d["detail"] = json.loads(d["detail"])
                except Exception:
                    pass
            items.append(d)
        return {"total": total, "items": items}

    def get_stats(self):
        """统计概览数据。"""
        cur = self.conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active, "
            "SUM(CASE WHEN status='gone' THEN 1 ELSE 0 END) AS gone, "
            "SUM(CASE WHEN status='expired' THEN 1 ELSE 0 END) AS expired "
            "FROM events"
        )
        row = cur.fetchone()
        run_cur = self.conn.execute("SELECT COUNT(*) AS total_runs FROM runs")
        runs_count = run_cur.fetchone()["total_runs"]
        last_date = self.last_run_date()
        return {
            "total_events": row["total"] or 0,
            "active_events": row["active"] or 0,
            "gone_events": row["gone"] or 0,
            "expired_events": row["expired"] or 0,
            "total_runs": runs_count or 0,
            "last_run_date": last_date,
        }

