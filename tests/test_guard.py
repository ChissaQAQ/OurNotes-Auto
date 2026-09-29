"""PlayGuard：认出演奏画面右上角的暂停按钮；看不到一段时间就停止触控。"""

import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from ournotes_auto.player import guard as guard_mod
from ournotes_auto.player.guard import PlayGuard, load_template

FIXTURES = Path(__file__).parent / "fixtures" / "play"
TEMPLATE = load_template()


def corner_frame(name: str, size=(1280, 720)) -> np.ndarray:
    """把右上角 160x100 的截图贴到黑底 1280x720 画面上，再缩放到 ``size``。"""
    corner = cv2.imdecode(np.fromfile(FIXTURES / f"{name}_corner.png", np.uint8), cv2.IMREAD_COLOR)
    frame = np.zeros((720, 1280, 3), np.uint8)
    frame[0:100, 1120:1280] = corner
    return frame if size == (1280, 720) else cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


def test_template_ships():
    assert TEMPLATE is not None and TEMPLATE.shape == (44, 44, 3)


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080), (960, 540)])
def test_visible_only_on_play_screen(size):
    g = PlayGuard(None, TEMPLATE)
    assert g.visible(corner_frame("play", size))
    assert not g.visible(corner_frame("pause", size))  # 暂停菜单下按钮变暗模糊
    assert not g.visible(corner_frame("band", size))
    assert not g.visible(np.zeros((size[1], size[0], 3), np.uint8))


class Screens:
    """按顺序返回画面：前 ``play_n`` 次是演奏画面，之后是暂停菜单。"""

    def __init__(self, play_n: int):
        self.play_n = play_n
        self.grabs = 0

    def grab(self):
        self.grabs += 1
        return corner_frame("play" if self.grabs <= self.play_n else "pause"), 0.0


def test_lost_sets_abort():
    src = Screens(play_n=2)
    g = PlayGuard(src, TEMPLATE, lost_s=0.1, interval_s=0.02)
    abort = g.start()
    assert abort.wait(2.0)
    g.stop()
    assert g.lost and src.grabs >= 3


def test_play_screen_keeps_going_and_forwards_stop():
    src = Screens(play_n=10**6)
    stop = threading.Event()
    g = PlayGuard(src, TEMPLATE, lost_s=0.1, interval_s=0.02)
    abort = g.start(stop)
    time.sleep(0.3)
    assert not abort.is_set()
    stop.set()
    assert abort.wait(1.0)
    g.stop()
    assert not g.lost


def test_grab_error_stops_checking_but_forwards_stop():
    class Broken:
        def grab(self):
            raise OSError("capture failed")

    stop = threading.Event()
    g = PlayGuard(Broken(), TEMPLATE, lost_s=0.05, interval_s=0.02)
    abort = g.start(stop)
    time.sleep(0.2)
    assert not abort.is_set()
    stop.set()
    assert abort.wait(1.0)
    g.stop()
    assert not g.lost


def test_missing_template(tmp_path):
    assert load_template(tmp_path / "none.png") is None
    assert guard_mod.TEMPLATE_PATH.is_file()
