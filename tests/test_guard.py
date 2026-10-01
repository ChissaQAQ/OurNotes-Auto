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


def test_pause_visible_matches_guard():
    """导航重试前用同一个函数确认还在演奏画面。"""
    assert guard_mod.pause_visible(corner_frame("play"), TEMPLATE)
    assert not guard_mod.pause_visible(corner_frame("pause"), TEMPLATE)


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


def life_frame(life: int, size=(1280, 720)) -> np.ndarray:
    """把右上角 280x64 的演奏画面截图（生命值 ``life``）贴到黑底 1280x720 画面上，再缩放到 ``size``。"""
    crop = cv2.imdecode(np.fromfile(FIXTURES / f"life_{life}.png", np.uint8), cv2.IMREAD_COLOR)
    frame = np.zeros((720, 1280, 3), np.uint8)
    frame[0:64, 1000:1280] = crop
    return frame if size == (1280, 720) else cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080), (960, 540)])
def test_life_fill(size):
    """生命值条亮着的长度和生命值成正比（1000 为满，超过 1000 叠青色，300 以下变红）。"""
    fills = {life: guard_mod.life_fill(life_frame(life, size)) for life in (1400, 1000, 700, 442, 300, 0)}
    assert fills[1400] > 0.97 and fills[1000] > 0.97
    for life in (700, 442, 300):
        assert fills[life] == pytest.approx(life / 1000, abs=0.08)
    assert fills[0] < guard_mod.LIFE_EMPTY
    assert guard_mod.pause_visible(life_frame(0, size), TEMPLATE)  # 生命值归零也还在演奏画面上


class Lives:
    """按顺序返回演奏画面：前 ``alive_n`` 次生命值 700，之后归零。"""

    def __init__(self, alive_n: int):
        self.alive_n = alive_n
        self.grabs = 0

    def grab(self):
        self.grabs += 1
        return life_frame(700 if self.grabs <= self.alive_n else 0), 0.0


def test_life_zero_sets_abort():
    src = Lives(alive_n=2)
    g = PlayGuard(src, TEMPLATE, lost_s=0.1, interval_s=0.02, life_zero_s=0.1)
    abort = g.start()
    assert abort.wait(2.0)
    g.stop()
    assert g.life_zero and not g.lost and src.grabs >= 4


def test_life_zero_check_off_by_default():
    g = PlayGuard(Lives(alive_n=0), TEMPLATE, lost_s=0.1, interval_s=0.02)
    abort = g.start()
    time.sleep(0.3)
    assert not abort.is_set()
    g.stop()
    assert not g.life_zero


def test_life_already_zero_after_retry():
    """生命值归零后重试，游戏不恢复生命值，一开始就是 0：这一局不看生命值，照常打。"""
    g = PlayGuard(Lives(alive_n=0), TEMPLATE, lost_s=0.1, interval_s=0.02, life_zero_s=0.05)
    abort = g.start()
    time.sleep(0.3)
    assert not abort.is_set()
    g.stop()
    assert not g.life_zero and not g.lost


def test_life_not_checked_off_play_screen():
    """看不到演奏画面时不看生命值（那个位置可能是别的东西），按看不到演奏画面处理。"""

    class Paused(Lives):
        def grab(self):
            frame, t = super().grab()
            return (frame if self.grabs <= self.alive_n else corner_frame("pause")), t

    g = PlayGuard(Paused(alive_n=2), TEMPLATE, lost_s=0.1, interval_s=0.02, life_zero_s=0.01)
    abort = g.start()
    assert abort.wait(2.0)
    g.stop()
    assert g.lost and not g.life_zero
