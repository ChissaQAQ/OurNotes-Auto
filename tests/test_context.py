"""打开设备时的分辨率检查。"""

import logging

import pytest

from ournotes_auto.context import SetupError, check_resolution


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
