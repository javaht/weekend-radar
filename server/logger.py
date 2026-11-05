# -*- coding: utf-8 -*-
"""实时日志广播器与环形缓冲区 —— 支撑控制台终端与 Web SSE 流式输出。"""
import asyncio
import collections
import time


class EventBusLogger:
    def __init__(self, maxlen=500):
        self._history = collections.deque(maxlen=maxlen)
        self._subscribers = set()
        self._loop = None

    def set_loop(self, loop):
        self._loop = loop

    def log(self, msg):
        formatted = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(formatted, flush=True)
        self._history.append(formatted)

        # 广播给所有活动的 SSE 队列
        dead = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(formatted)
            except Exception:
                dead.append(q)
        for d in dead:
            self._subscribers.discard(d)

    def subscribe(self):
        q = asyncio.Queue()
        # 将历史记录灌入新订阅者
        for item in self._history:
            q.put_nowait(item)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        self._subscribers.discard(q)

    def get_history(self):
        return list(self._history)

    def clear_history(self):
        self._history.clear()


bus_logger = EventBusLogger()
