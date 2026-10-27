import ctypes
import sys
import threading
from types import SimpleNamespace

import pytest

from ournotes_auto.planner import Action, TouchEvent
from ournotes_auto.player import clock
from ournotes_auto.player.clock import THREAD_PRIORITY_ERROR_RETURN, now, raised_thread_priority
from ournotes_auto.player.executor import Executor
from ournotes_auto.player.session import PlaySession


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


def test_executor_hold_releases_and_skips():
    """暂停（看不到演奏画面）时抬起按着的触点、这段时间的事件都不发；按下没发的触点之后的移动、抬起也不发。"""

    class Hold:
        def __init__(self, *states):
            self.states = list(states)

        def is_set(self):
            return self.states.pop(0)

    events = [
        TouchEvent(0, Action.DOWN, 0, 1, 1),
        TouchEvent(20, Action.DOWN, 1, 2, 2),
        TouchEvent(40, Action.MOVE, 0, 3, 3),
        TouchEvent(40, Action.UP, 1, 2, 2),
        TouchEvent(60, Action.DOWN, 2, 4, 4),
        TouchEvent(80, Action.UP, 0, 3, 3),
        TouchEvent(80, Action.UP, 2, 4, 4),
    ]
    backend = FakeBackend()
    stats = Executor(backend).run(events, now(), hold=Hold(False, True, False, False, False))
    assert [r[1:3] for r in backend.log] == [("down", 0), ("up", 0), ("down", 2), ("up", 2)]
    assert stats.sent == 3 and stats.held == 4 and not stats.aborted and not backend.released


# ---------------------------------------------------------------- 线程优先级

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="只在 Windows 上调线程优先级")


def _thread_priority() -> int:
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentThread.restype = ctypes.c_void_p
    kernel32.GetThreadPriority.argtypes = [ctypes.c_void_p]
    return kernel32.GetThreadPriority(kernel32.GetCurrentThread())


def _in_thread(fn):
    """在新线程里跑：新线程是普通优先级，不受前面的测试（以前打完不恢复）影响。"""
    out = []
    t = threading.Thread(target=lambda: out.append(fn()))
    t.start()
    t.join()
    assert out, "线程里出错了"
    return out[0]


@windows_only
def test_executor_priority_restored():
    """演奏时是最高优先级，打完（包括中途停止）恢复原来的：之后的导航、文字识别不再抢别的程序的 CPU。"""

    def body():
        before, seen = _thread_priority(), []
        backend = FakeBackend()
        backend.down = lambda finger, x, y: seen.append(_thread_priority())
        Executor(backend).run([TouchEvent(0, Action.DOWN, 0, 1, 1), TouchEvent(1, Action.UP, 0, 1, 1)], now())
        after = _thread_priority()
        stop = threading.Event()
        stop.set()
        assert Executor(FakeBackend()).run([TouchEvent(0, Action.DOWN, 0, 1, 1)], now(), stop=stop).aborted
        return before, seen, after, _thread_priority()

    assert _in_thread(body) == (0, [15], 0, 0)


@windows_only
def test_session_play_priority_restored():
    """同步和演奏整局都是最高优先级（里面 Executor 出来时不降下来），出了 play 恢复；出错也恢复。"""

    def _play(chart, stop, require_sync_ok, retry):
        with raised_thread_priority():
            pass
        return _thread_priority()

    def _fail(*args):
        raise RuntimeError("boom")

    def body():
        before = _thread_priority()
        inside = PlaySession.play(SimpleNamespace(_play=_play), None)
        after = _thread_priority()
        with pytest.raises(RuntimeError):
            PlaySession.play(SimpleNamespace(_play=_fail), None)
        return before, inside, after, _thread_priority()

    assert _in_thread(body) == (0, 15, 0, 0)


class FakeKernel32:
    def __init__(self, current=0, set_ok=True):
        self.current, self.set_ok, self.calls = current, set_ok, []
        self.GetCurrentThread = lambda: 1234
        self.GetThreadPriority = lambda thread: self.current
        self.SetThreadPriority = lambda thread, priority: self._set(priority)

    def _set(self, priority):
        self.calls.append(priority)
        return self.set_ok


@pytest.mark.parametrize(
    "current, set_ok, calls",
    [
        (2, True, [15, 2]),
        (THREAD_PRIORITY_ERROR_RETURN, True, [15, 0]),  # 读不到原来的：恢复成普通
        (0, False, [15]),  # 设不上：不用恢复
    ],
)
def test_priority_fallbacks(monkeypatch, current, set_ok, calls):
    kernel32 = FakeKernel32(current, set_ok)
    monkeypatch.setattr(clock.sys, "platform", "win32")
    monkeypatch.setattr(clock.ctypes, "WinDLL", lambda name: kernel32, raising=False)
    with pytest.raises(RuntimeError):  # 出错也恢复
        with raised_thread_priority():
            raise RuntimeError("boom")
    assert kernel32.calls == calls
