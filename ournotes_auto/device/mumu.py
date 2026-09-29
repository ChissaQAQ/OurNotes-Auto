"""基于 MuMu IPC 的截图源与触控后端。"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from ..config import DeviceConfig
from .mumu_ipc import MuMuIpc

logger = logging.getLogger(__name__)


class MuMuFrameSource:
    """``grab`` 只返回新渲染的帧：连续轮询截图，画面有变化才返回，时刻取首次截到该帧的截图完成时刻。

    游戏 60fps、单次截图约 3~7ms，因此每帧会被截到 2~3 次；若都返回，重复帧的时刻会晚于真实出帧，
    让同步拟合产生偏差和噪声。画面完全静止时每隔 ``idle_s`` 仍返回一帧，保证调用方不被卡住。
    演奏时连击数监视和演奏画面检查会在各自的线程里截图，所以整个过程加锁（DLL 的截图缓冲是共用的）。
    """

    def __init__(self, ipc: MuMuIpc, idle_s: float = 0.05):
        self.ipc = ipc
        self.idle_s = idle_s
        self._prev: np.ndarray | None = None
        self._last = 0.0
        self._lock = threading.Lock()

    @property
    def size(self) -> tuple[int, int]:
        return self.ipc.width, self.ipc.height

    def grab(self) -> tuple[np.ndarray, float]:
        with self._lock:
            while True:
                raw = self.ipc.capture_raw()
                t = time.perf_counter()
                sig = raw[::8, ::8, :3].copy()  # 抽样比较，约 0.1ms
                changed = self._prev is None or not np.array_equal(sig, self._prev)
                if changed or t - self._last >= self.idle_s:
                    self._prev = sig
                    self._last = t
                    return np.ascontiguousarray(raw[::-1, :, 2::-1]), t


class MuMuTouch:
    """触点编号 0~9 映射为 DLL 的 1~10；对已按下的触点再次 down 即移动。"""

    def __init__(self, ipc: MuMuIpc):
        self.ipc = ipc
        self._down: set[int] = set()

    def down(self, finger: int, x: int, y: int) -> None:
        self.ipc.finger_down(finger + 1, x, y)
        self._down.add(finger)

    def move(self, finger: int, x: int, y: int) -> None:
        self.ipc.finger_down(finger + 1, x, y)

    def up(self, finger: int) -> None:
        self.ipc.finger_up(finger + 1)
        self._down.discard(finger)

    def flush(self) -> None:
        pass

    def release_all(self) -> None:
        for f in sorted(self._down):
            self.ipc.finger_up(f + 1)
        self._down.clear()

    def tap(self, x: int, y: int, hold_s: float = 0.06) -> None:
        """界面操作用的单击（触点 9，避开演奏用的低编号触点）。"""
        self.down(9, x, y)
        time.sleep(hold_s)
        self.up(9)


def open_mumu(cfg: DeviceConfig) -> MuMuIpc:
    ipc = MuMuIpc(cfg.mumu_path, cfg.instance, package=cfg.package)
    ipc.connect()
    return ipc
