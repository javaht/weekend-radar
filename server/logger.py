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
        # startup 时保存主事件循环；SSE 队列都绑定在它上面
        self._loop = loop

    def log(self, msg):
        """可从任意线程调用（抓取任务跑在 run_in_executor 的工作线程里）。"""
        formatted = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(formatted, flush=True)
        self._history.append(formatted)

        loop = self._loop
        subscribers = list(self._subscribers)
        if loop is not None and subscribers:
            # ★ 跨线程操作 asyncio.Queue 必须经 call_soon_threadsafe，
            #   直接 put_nowait 会丢日志 / 触发 "Future attached to a different loop"。
            try:
                loop.call_soon_threadsafe(self._broadcast, formatted, subscribers)
            except RuntimeError:
                # loop 已关闭：退化为同步投递，仅用于进程退出前的收尾日志
                self._broadcast(formatted, subscribers)
        else:
            self._broadcast(formatted, subscribers)

    def _broadcast(self, formatted, subscribers):
        for q in subscribers:
            try:
                q.put_nowait(formatted)
            except Exception:
                self._subscribers.discard(q)

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
