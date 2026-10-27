"""高精度计时：Windows 下提高定时器分辨率，先睡眠后自旋等待到目标时刻。"""

from __future__ import annotations

import contextlib
import ctypes
import sys
import time

now = time.perf_counter

THREAD_PRIORITY_ERROR_RETURN = 0x7FFFFFFF  # GetThreadPriority 失败时的返回值


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


@contextlib.contextmanager
def raised_thread_priority():
    """在上下文内把当前线程设为最高优先级（THREAD_PRIORITY_TIME_CRITICAL，仅 Windows 生效），出来时恢复原来的。

    不恢复的话打完歌之后导航、文字识别也一直是最高优先级，电脑忙时会抢走别的程序的 CPU。
    设不上就照常运行；嵌套时里层恢复到外层设的最高优先级。
    """
    if sys.platform != "win32":
        yield
        return
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentThread.restype = ctypes.c_void_p
    kernel32.GetThreadPriority.argtypes = [ctypes.c_void_p]
    kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
    thread = kernel32.GetCurrentThread()
    old = kernel32.GetThreadPriority(thread)
    if old == THREAD_PRIORITY_ERROR_RETURN:
        old = 0  # THREAD_PRIORITY_NORMAL
    raised = bool(kernel32.SetThreadPriority(thread, 15))
    try:
        yield
    finally:
        if raised:
            kernel32.SetThreadPriority(thread, old)


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
