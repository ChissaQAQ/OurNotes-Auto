"""高精度计时：Windows 下提高定时器分辨率，先睡眠后自旋等待到目标时刻。"""

from __future__ import annotations

import contextlib
import ctypes
import sys
import time

now = time.perf_counter


@contextlib.contextmanager
def high_resolution_timer(period_ms: int = 1):
    """在上下文内把系统定时器分辨率提高到 ``period_ms``（仅 Windows 生效）。"""
    if sys.platform != "win32":
        yield
        return
    winmm = ctypes.WinDLL("winmm")
    winmm.timeBeginPeriod(period_ms)
    try:
        yield
    finally:
        winmm.timeEndPeriod(period_ms)


def raise_thread_priority() -> bool:
    """把当前线程设为最高优先级（THREAD_PRIORITY_TIME_CRITICAL），失败返回 False。"""
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentThread.restype = ctypes.c_void_p
    kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return bool(kernel32.SetThreadPriority(kernel32.GetCurrentThread(), 15))


def sleep_until(deadline: float, spin_s: float = 0.0015) -> float:
    """等待到 ``perf_counter() >= deadline``，返回实际到达时刻。

    距目标较远时用 sleep 让出 CPU，最后 ``spin_s`` 秒自旋以获得亚毫秒精度。
    """
    while True:
        t = now()
        remaining = deadline - t
        if remaining <= 0:
            return t
        if remaining > spin_s:
            time.sleep(remaining - spin_s)
