"""电脑和本进程的内存读数（#94）：只用来记日志、提示，读不到时不抛异常。"""

import sys

import pytest

from ournotes_auto import sysmem
from ournotes_auto.sysmem import GB, MemoryStatus


def test_describe_and_low():
    status = MemoryStatus(16 * GB, 3 * GB, 24 * GB, 12 * GB)
    assert sysmem.describe(status) == "内存可用 3.0/16.0 GB，提交 12.0/24.0 GB"
    assert not sysmem.low(status)
    assert sysmem.low(status._replace(commit_avail=sysmem.LOW_COMMIT - 1))
    assert not sysmem.low(status._replace(commit_avail=sysmem.LOW_COMMIT))


def test_unreadable(monkeypatch):
    """读不到：描述为空串、不算快用完了。"""
    monkeypatch.setattr(sysmem, "memory_status", lambda: None)
    assert sysmem.describe() == ""
    assert not sysmem.low()


def test_ctypes_failure(monkeypatch):
    def boom(name):
        raise OSError(name)

    monkeypatch.setattr(sysmem.ctypes, "WinDLL", boom, raising=False)
    monkeypatch.setattr(sysmem.sys, "platform", "win32")
    assert sysmem.memory_status() is None
    assert sysmem.process_private_bytes() is None


@pytest.mark.skipif(sys.platform != "win32", reason="只在 Windows 上读")
def test_real_values():
    status = sysmem.memory_status()
    assert status is not None
    assert 0 < status.avail <= status.total <= status.commit_limit
    assert 0 <= status.commit_avail <= status.commit_limit
    own = sysmem.process_private_bytes()
    assert own is not None and 1 * sysmem.MB < own < status.commit_limit
    assert sysmem.describe(status).startswith("内存可用 ")
