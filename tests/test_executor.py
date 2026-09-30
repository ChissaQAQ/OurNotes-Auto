import threading

import pytest

from ournotes_auto.planner import Action, TouchEvent
from ournotes_auto.player.clock import now
from ournotes_auto.player.executor import Executor


class FakeBackend:
    def __init__(self):
        self.log = []
        self.released = False

    def down(self, finger, x, y):
        self.log.append((now(), "down", finger, x, y))

    def move(self, finger, x, y):
        self.log.append((now(), "move", finger, x, y))

    def up(self, finger):
        self.log.append((now(), "up", finger))

    def flush(self):
        pass

    def release_all(self):
        self.released = True


def test_executor_timing():
    events = []
    for i in range(40):
        t = 50 + i * 7.3
        events += [TouchEvent(t, Action.DOWN, i % 4, 10, 10), TouchEvent(t + 20, Action.UP, i % 4, 10, 10)]
    events.sort(key=lambda e: e.t_ms)
    backend = FakeBackend()
    # CI 的虚拟机上开头会卡一下（第一批迟到 55~67ms，之后都准时），先空跑一次、多留提前量，只测稳定后的调度误差
    Executor(FakeBackend()).run([TouchEvent(0, Action.DOWN, 0, 1, 1)], now() + 0.02)
    t0 = now() + 0.2
    stats = Executor(backend).run(events, t0, offset_ms=5)
    assert stats.sent == len(events)
    assert not backend.released
    downs = [r for r in backend.log if r[1] == "down"]
    errs = [(r[0] - (t0 + (e.t_ms + 5) / 1000)) * 1000 for r, e in zip(downs, [e for e in events if e.action is Action.DOWN])]
    # 主机上的调度误差：一般在 1ms 以内，CI 机器上放宽
    assert max(errs) < 5, errs
    assert min(errs) > -0.1, errs


def test_executor_stop_releases():
    events = [TouchEvent(0, Action.DOWN, 0, 1, 1), TouchEvent(5000, Action.UP, 0, 1, 1)]
    backend = FakeBackend()
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    stats = Executor(backend).run(events, now(), stop=stop)
    assert stats.aborted and backend.released


def test_executor_skips_stale_moves():
    events = [
        TouchEvent(0, Action.DOWN, 0, 1, 1),
        TouchEvent(1, Action.MOVE, 0, 2, 2),
        TouchEvent(2, Action.UP, 0, 2, 2),
    ]
    backend = FakeBackend()
    stats = Executor(backend, stale_move_ms=10).run(events, now() - 1.0)  # 已经落后 1 秒
    assert stats.skipped_moves == 1
    assert [r[1] for r in backend.log] == ["down", "up"]


def test_executor_sends_down_before_up_in_batch():
    # 滑动 A（触点 0）结束的同一时刻滑动 B（触点 1）开始：先按下 B 再抬起 A；
    # 同一触点在这一刻抬起又按下（触点 2）保持先抬起
    events = [
        TouchEvent(0, Action.DOWN, 0, 1, 1),
        TouchEvent(0, Action.DOWN, 2, 5, 5),
        TouchEvent(30, Action.MOVE, 0, 2, 2),
        TouchEvent(30, Action.UP, 0, 2, 2),
        TouchEvent(30, Action.UP, 2, 5, 5),
        TouchEvent(30, Action.DOWN, 1, 3, 3),
        TouchEvent(30.2, Action.DOWN, 2, 6, 6),
        TouchEvent(60, Action.UP, 1, 3, 3),
        TouchEvent(60, Action.UP, 2, 6, 6),
    ]
    backend = FakeBackend()
    Executor(backend).run(events, now())
    order = [r[1:3] for r in backend.log]
    assert order == [
        ("down", 0), ("down", 2),
        ("move", 0), ("up", 2), ("down", 1), ("down", 2), ("up", 0),
        ("up", 1), ("up", 2),
    ]
