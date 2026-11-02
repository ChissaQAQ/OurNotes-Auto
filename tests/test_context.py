"""打开设备时的分辨率检查；导航器用的重启游戏。"""

import logging
import threading
from types import SimpleNamespace

import pytest

from ournotes_auto.config import Config
from ournotes_auto.context import SetupError, check_resolution, game_restarter
from ournotes_auto.device import adb
from ournotes_auto.runner import NavigationError


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080), (2560, 1440), (1366, 768), (720, 1280)])
def test_16_9_ok(size):
    """16:9 都行；游戏还没打开时截到的可能是竖着的桌面。"""
    check_resolution(*size)


@pytest.mark.parametrize("size", [(1280, 800), (1920, 1200), (800, 1280), (0, 0)])
def test_other_aspect_rejected(size):
    with pytest.raises(SetupError, match="不是 16:9"):
        check_resolution(*size)


def test_other_resolution_logged(caplog):
    with caplog.at_level(logging.INFO, logger="ournotes_auto.context"):
        check_resolution(1280, 720)
        assert not caplog.records
        check_resolution(2560, 1440)
    assert "2560x1440" in caplog.text


class FakeIpc:
    display_id = 0

    def __init__(self):
        self.waited = 0

    def wait_for_game_display(self, stop=None):
        self.waited += 1
        self.display_id = 2


def test_game_restarter(monkeypatch):
    """重启后等游戏的屏幕建好（#58）。"""
    restarted = []
    monkeypatch.setattr(adb, "restart_app", lambda cfg: restarted.append(cfg))
    cfg, source = Config(), SimpleNamespace(ipc=FakeIpc())
    game_restarter(cfg, source, threading.Event())()
    assert restarted == [cfg.device] and source.ipc.waited == 1


@pytest.mark.parametrize("error", [adb.AdbError("adb connect 127.0.0.1:16416 失败：退出码 1"), FileNotFoundError("adb.exe")])
def test_game_restarter_adb_error(monkeypatch, error):
    """adb 出错时抛 NavigationError（任务把这次算作失败接着跑），不当作意外错误把整个任务停掉。"""

    def fail(cfg):
        raise error

    monkeypatch.setattr(adb, "restart_app", fail)
    source = SimpleNamespace(ipc=FakeIpc())
    with pytest.raises(NavigationError, match="重启游戏失败"):
        game_restarter(Config(), source, threading.Event())()
    assert source.ipc.waited == 0
