"""按绝对时刻执行触控事件序列。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from ..device.base import TouchBackend
from ..planner import Action, TouchEvent
from .clock import high_resolution_timer, now, raise_thread_priority, sleep_until

logger = logging.getLogger(__name__)


@dataclass
class ExecStats:
    sent: int = 0
    skipped_moves: int = 0
    batches: int = 0
    lateness_ms: list[float] = field(default_factory=list)  # 每批实际发送相对计划的迟到
    aborted: bool = False

    @property
    def max_late_ms(self) -> float:
        return max(self.lateness_ms, default=0.0)

    @property
    def mean_late_ms(self) -> float:
        return sum(self.lateness_ms) / len(self.lateness_ms) if self.lateness_ms else 0.0

    def late_count(self, threshold_ms: float = 4.0) -> int:
        return sum(1 for v in self.lateness_ms if v > threshold_ms)


class Executor:
    """把 :class:`TouchEvent` 按 ``t0 + (t_ms + offset_ms) / 1000`` 的主机时刻发送到触控后端。

    - 计划时刻相差不超过 ``batch_ms`` 的事件合并为一批，一次 flush；
    - 已落后超过 ``stale_move_ms`` 的移动事件直接跳过（按下/抬起始终发送），防止积压。
    """

    def __init__(
        self,
        backend: TouchBackend,
        batch_ms: float = 0.5,
        spin_ms: float = 1.5,
        stale_move_ms: float = 12.0,
    ):
        self.backend = backend
        self.batch_ms = batch_ms
        self.spin_s = spin_ms / 1000
        self.stale_move_ms = stale_move_ms

    @staticmethod
    def _send_order(batch: list[TouchEvent]) -> list[TouchEvent]:
        """同一批里先按下、后抬起。

        一个滑动在另一个滑动开头的同一时刻结束时（轨道相邻），先抬起再按下会漏掉后者的第一个中继。
        同一触点在这一批里抬起又按下的（finger_gap_ms 为 0 时）保持原顺序。
        """
        downs = {e.finger for e in batch if e.action is Action.DOWN}
        return sorted(batch, key=lambda e: e.action is Action.UP and e.finger not in downs)

    def run(
        self,
        events: list[TouchEvent],
        t0: float,
        offset_ms: float = 0.0,
        stop: threading.Event | None = None,
    ) -> ExecStats:
        stats = ExecStats()
        base = t0 + offset_ms / 1000
        n = len(events)
        i = 0
        with high_resolution_timer():
            raise_thread_priority()
            try:
                while i < n:
                    if stop is not None and stop.is_set():
                        stats.aborted = True
                        break
                    first = events[i].t_ms
                    j = i + 1
                    while j < n and events[j].t_ms - first <= self.batch_ms:
                        j += 1
                    due = base + first / 1000
                    # 长时间等待时分段睡眠，以便及时响应 stop
                    while stop is not None and due - now() > 0.2:
                        if stop.wait(min(0.1, due - now() - 0.15)):
                            break
                    if stop is not None and stop.is_set():
                        stats.aborted = True
                        break
                    sleep_until(due, self.spin_s)
                    late_ms = (now() - due) * 1000
                    for e in self._send_order(events[i:j]):
                        if e.action is Action.MOVE and late_ms > self.stale_move_ms:
                            stats.skipped_moves += 1
                            continue
                        if e.action is Action.DOWN:
                            self.backend.down(e.finger, e.x, e.y)
                        elif e.action is Action.MOVE:
                            self.backend.move(e.finger, e.x, e.y)
                        else:
                            self.backend.up(e.finger)
                        stats.sent += 1
                    self.backend.flush()
                    stats.batches += 1
                    stats.lateness_ms.append(late_ms)
                    i = j
            finally:
                if i < n:
                    try:
                        self.backend.release_all()
                    except Exception:  # noqa: BLE001 - 收尾时尽力而为
                        logger.exception("抬起全部触点失败")
        if stats.lateness_ms:
            logger.debug(
                "执行完毕：%d 事件 / %d 批，平均迟到 %.2fms，最大 %.2fms，>4ms %d 批，跳过移动 %d",
                stats.sent,
                stats.batches,
                stats.mean_late_ms,
                stats.max_late_ms,
                stats.late_count(),
                stats.skipped_moves,
            )
        return stats
