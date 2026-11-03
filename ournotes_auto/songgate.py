"""「停止时打完当前这首」：打歌时收到停止请求，等这首打完（记下成绩、离开结算页）再停止。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class SongGate:
    """``song()`` 包住一首歌（从点 LIVE START 到离开结算页）。:meth:`request` 收到停止请求时：
    没在打歌就立即置位 ``stop``；正在打歌就等这首结束（出错也算）再置位，之后不再开始新的一首。
    直接置位 ``stop``（立即停止）照旧，这里不拦。"""

    def __init__(self, stop: threading.Event):
        self.stop = stop
        self._lock = threading.Lock()
        self.in_song = False
        self.pending = False  # 收到了停止请求

    def request(self) -> bool:
        """收到停止请求；返回是否正在打歌（是的话打完这首再停止）。"""
        with self._lock:  # 在锁里置位：song() 看到收到过请求时 stop 一定已经置位（不会把「已停止」记成失败）
            self.pending = True
            if not self.in_song:
                self.stop.set()
            return self.in_song

    @contextmanager
    def song(self) -> Iterator[None]:
        from .runner import NavigationError

        with self._lock:
            if self.pending:
                raise NavigationError("已停止")
            self.in_song = True
        try:
            yield
        except BaseException as e:
            if self._leave():
                # 置位后 _loop 看到已停止就不再记这个错误，这里记下原因（含截图路径）
                logger.warning("这首歌没打完（%s），停止", e)
                self.stop.set()
            raise
        if self._leave():
            logger.info("这首歌打完了，停止")
            self.stop.set()

    def _leave(self) -> bool:
        """离开一首歌；返回是否该停止了（收到过停止请求、还没停止）。"""
        with self._lock:
            self.in_song = False
            return self.pending and not self.stop.is_set()
