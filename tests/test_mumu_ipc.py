"""MuMu IPC 的 display_id 解析：重启游戏后要等到按包名能查到为止（#58）。"""

import threading

import pytest

from ournotes_auto.device import mumu_ipc


class FakeDll:
    """假的 external_renderer_ipc.dll：按调用次数依次返回 ``pkg_results`` 里的值。"""

    def __init__(self, pkg_results, default_result=0):
        self.pkg_results = list(pkg_results)
        self.default_result = default_result
        self.pkg_calls = 0
        self.default_calls = 0

    def nemu_get_display_id(self, handle, package, app_index):
        if package == b"default":
            self.default_calls += 1
            return self.default_result
        self.pkg_calls += 1
        if len(self.pkg_results) > 1:
            return self.pkg_results.pop(0)
        return self.pkg_results[0]


def make_ipc(monkeypatch, pkg_results, default_result=0):
    """建一个不走 DLL 的 MuMuIpc（handle 手动给上，跳过 connect）。"""
    monkeypatch.setattr(mumu_ipc, "find_dll", lambda install_dir: "fake.dll")
    monkeypatch.setattr(mumu_ipc, "_load_dll", lambda path: FakeDll(pkg_results, default_result))
    ipc = mumu_ipc.MuMuIpc("C:/MuMu", 1, package="com.bilibili.sirius.official")
    ipc.handle = 1
    return ipc


def test_refresh_falls_back_to_zero_when_game_gone(monkeypatch):
    """游戏没起来、包名和 default 都查不到时退回 0（原来的行为，保留）。"""
    ipc = make_ipc(monkeypatch, [-1], default_result=-1)
    assert ipc.refresh_display_id() == 0
    assert ipc.display_id == 0


def test_refresh_accepts_default_zero(monkeypatch):
    """default 返回 0（模拟器桌面）时也会被当成合法值——这正是 #58 的坑，所以重启不能用它。"""
    ipc = make_ipc(monkeypatch, [-1], default_result=0)
    assert ipc.refresh_display_id() == 0
    assert ipc.display_id == 0


def test_game_display_id_reports_minus_one_while_starting(monkeypatch):
    """游戏还没建好 display 时 game_display_id() 返回 -1，不写 display_id。"""
    ipc = make_ipc(monkeypatch, [-1])
    ipc.display_id = 3
    assert ipc.game_display_id() == -1
    assert ipc.display_id == 3  # 没被改


def test_wait_for_game_display_waits_until_ready(monkeypatch):
    """按包名要查几次才查到：等到查到才把 display_id 换过去，不采用中途的 default 值。"""
    ipc = make_ipc(monkeypatch, [-1, -1, 3], default_result=0)
    monkeypatch.setattr(mumu_ipc.time, "sleep", lambda s: None)
    assert ipc.wait_for_game_display(timeout_s=5.0)
    assert ipc.display_id == 3


def test_wait_for_game_display_times_out(monkeypatch):
    """一直查不到（游戏起不来）时超时返回 False，并且**不**把 display_id 改成桌面值。"""
    ipc = make_ipc(monkeypatch, [-1], default_result=0)
    ipc.display_id = 3
    monkeypatch.setattr(mumu_ipc.time, "sleep", lambda s: None)
    now = [0.0]
    monkeypatch.setattr(mumu_ipc.time, "monotonic", lambda: now[0])

    def advance(_s):
        now[0] += 1.0

    monkeypatch.setattr(mumu_ipc.time, "sleep", advance)
    assert not ipc.wait_for_game_display(timeout_s=3.0)
    assert ipc.display_id == 3  # 保持原值，不会变成 0（桌面）


def test_wait_for_game_display_stops_early(monkeypatch):
    """停止请求置位时立刻返回 False，不等满超时。"""
    ipc = make_ipc(monkeypatch, [-1])
    monkeypatch.setattr(mumu_ipc.time, "sleep", lambda s: None)
    stop = threading.Event()
    stop.set()
    assert not ipc.wait_for_game_display(timeout_s=60.0, stop=stop)
