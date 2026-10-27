"""电脑和本进程用了多少内存（只看 Windows；读不到时返回 None 或空串，从不抛异常）。

挂机几个小时后进程因分配不到内存直接退出（#94，OpenBLAS「Memory allocation still failed」）：
日志里记下内存，好看出是本程序越用越多还是电脑的内存被别的程序占满了。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import NamedTuple

GB = 1024**3
MB = 1024**2
# 还能提交的内存少于这么多时算快用完了：再分配大块内存（截图、文字识别）可能失败
LOW_COMMIT = 1 * GB


class MemoryStatus(NamedTuple):
    total: int  # 物理内存
    avail: int  # 可用的物理内存
    commit_limit: int  # 提交上限（物理内存 + 页面文件）
    commit_avail: int  # 还能提交多少


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def memory_status() -> MemoryStatus | None:
    if sys.platform != "win32":
        return None
    try:
        st = _MemoryStatusEx()
        st.dwLength = ctypes.sizeof(st)
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.c_void_p]
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return MemoryStatus(st.ullTotalPhys, st.ullAvailPhys, st.ullTotalPageFile, st.ullAvailPageFile)
    except Exception:  # noqa: BLE001 - 只是记日志用，读不到就算了
        return None


def process_private_bytes() -> int | None:
    """本进程的私有内存（任务管理器「详细信息」里的「提交大小」）。"""
    if sys.platform != "win32":
        return None
    try:
        pmc = _ProcessMemoryCountersEx()
        pmc.cb = ctypes.sizeof(pmc)
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return None
        return pmc.PrivateUsage
    except Exception:  # noqa: BLE001 - 同上
        return None


def describe(status: MemoryStatus | None = None) -> str:
    """「内存可用 3.2/15.9 GB，提交 12.1/24.0 GB」；读不到时为空串。"""
    if status is None:
        status = memory_status()
    if status is None:
        return ""
    used = status.commit_limit - status.commit_avail
    return (
        f"内存可用 {status.avail / GB:.1f}/{status.total / GB:.1f} GB，"
        f"提交 {used / GB:.1f}/{status.commit_limit / GB:.1f} GB"
    )


def low(status: MemoryStatus | None = None) -> bool:
    """电脑还能提交的内存是不是快用完了（读不到时当作没有）。"""
    if status is None:
        status = memory_status()
    return status is not None and status.commit_avail < LOW_COMMIT
