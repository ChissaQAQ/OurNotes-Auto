"""MuMu 截图失败时的重试与重连（#119：以前截图失败一次就当作意外错误把任务停掉）。"""

import time

import numpy as np
import pytest

from ournotes_auto.device import mumu
from ournotes_auto.device.mumu_ipc import MuMuIpcError


class FakeClock:
    """代替 mumu 模块里的 time：sleep 只推进假时钟，不真的等。"""

    perf_counter = staticmethod(time.perf_counter)

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FlakyIpc:
    """前 ``fails`` 次截图失败；``connect`` 后恢复（``connect_ok`` 为假时连不上），分辨率变成 ``size_after``。"""

    def __init__(self, fails, game_display=-1, connect_ok=True, size_after=(4, 3)):
        self.fails = fails
        self.game_display = game_display
        self.connect_ok = connect_ok
        self.size_after = size_after
        self.display_id = 0
        self.width, self.height = 4, 3
        self.captures = self.connects = 0

    def capture_raw(self):
        self.captures += 1
        if self.fails > 0:
            self.fails -= 1
            raise MuMuIpcError("nemu_capture_display", 4)
        return np.zeros((self.height, self.width, 4), np.uint8)

    def game_display_id(self):
        return self.game_display

    def connect(self):
        self.connects += 1
        if not self.connect_ok:
            raise MuMuIpcError("nemu_connect('C:/MuMu', 1)，模拟器实例是否已启动？", 0)
        self.fails = 0
        self.width, self.height = self.size_after


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(mumu, "time", c)
    return c


def test_capture_retries_until_it_works(clock):
    """截图失败几次后恢复：接着返回画面，不重连；游戏的 display 变了就换过去。"""
    ipc = FlakyIpc(fails=3, game_display=5)
    frame, _ = mumu.MuMuFrameSource(ipc).grab()
    assert frame.shape == (3, 4, 3)
    assert (ipc.captures, ipc.connects, ipc.display_id) == (4, 0, 5)
    assert clock.t == 3 * mumu.RECOVER_STEP_S


def test_capture_reconnects_after_retrying(clock):
    """重试 RECOVER_S 秒还是失败：重连一次，恢复了就接着用。"""
    ipc = FlakyIpc(fails=10**6)
    frame, _ = mumu.MuMuFrameSource(ipc).grab()
    assert frame.shape == (3, 4, 3)
    assert ipc.connects == 1
    assert clock.t >= mumu.RECOVER_S


def test_capture_gives_up_when_reconnect_fails(clock):
    """重连也连不上（模拟器闪退、卡死）：抛 MuMuIpcError，由命令行说明原因、请玩家重启 MuMu。"""
    ipc = FlakyIpc(fails=10**6, connect_ok=False)
    with pytest.raises(MuMuIpcError, match="nemu_connect"):
        mumu.MuMuFrameSource(ipc).grab()
    assert ipc.connects == 1


def test_capture_gives_up_when_resolution_changed(clock):
    """重连后分辨率变了：坐标都是按原来的分辨率算的，不接着用。"""
    ipc = FlakyIpc(fails=10**6, size_after=(8, 6))
    with pytest.raises(MuMuIpcError, match="分辨率从 4x3 变成了 8x6"):
        mumu.MuMuFrameSource(ipc).grab()
