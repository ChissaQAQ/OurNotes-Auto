"""乐曲选择页的列表：认缩略图、记位置、按曲目选歌（用画出来的假列表）。"""

import math

import cv2
import numpy as np
import pytest

from ournotes_auto.config import Config
from ournotes_auto.nav.jacket import JacketMatcher
from ournotes_auto.nav.navigator import GameNavigator
from ournotes_auto.nav.song_select import (
    BTN_CATEGORY,
    LIST_THUMB,
    LIST_THUMB_X,
    ROW_ABOVE_Y,
    ROW_BELOW_Y,
    ROW_PITCH,
    SELECT_JACKET_ROI,
    ListPositions,
    SongPick,
    list_rows,
    row_matcher,
    row_offset,
)
from test_jacket import jacket
from test_navigator import load_items
from test_song_select import item

IDS = [100000 + i for i in range(30)]
JACKETS = {mid: jacket(i) for i, mid in enumerate(IDS)}


def row_y(k: int) -> int:
    """相对中间第 k 行的缩略图上边缘。"""
    return ROW_ABOVE_Y + ROW_PITCH * k if k < 0 else ROW_BELOW_Y + ROW_PITCH * k


class ListGame:
    """画出来的乐曲选择页列表，同时充当截图源、触控和 OCR。"""

    size = (1280, 720)

    def __init__(self, order, selected=0, locked=(), fade=True):
        self.order = list(order)
        self.index = selected
        self.locked = set(locked)
        self.fade = fade  # 最上、最下两行渐隐
        self.category = "原创"
        self.taps = []
        self.drags = 0
        self._items = []
        self._rng = np.random.default_rng(0)

    @property
    def selected(self):
        return self.order[self.index]

    def grab(self):
        frame = self._rng.integers(20, 70, (720, 1280, 3), dtype=np.uint8)
        for k in range(-4, 5):
            i = self.index + k
            if k == 0 or not 0 <= i < len(self.order):
                continue
            y = row_y(k)
            if y + LIST_THUMB <= 0 or y >= 720:
                continue
            thumb = cv2.resize(JACKETS[self.order[i]], (LIST_THUMB, LIST_THUMB), interpolation=cv2.INTER_AREA)
            if self.fade and (y < 150 or y > 600):
                thumb = (thumb * 0.5 + 40).astype(np.uint8)
            y0, y1 = max(0, y), min(720, y + LIST_THUMB)
            frame[y0:y1, LIST_THUMB_X : LIST_THUMB_X + LIST_THUMB] = thumb[y0 - y : y1 - y]
        x, y, w, h = SELECT_JACKET_ROI
        big = cv2.resize(JACKETS[self.selected], (w, h), interpolation=cv2.INTER_AREA)
        if self.selected in self.locked:
            big[: h // 2, : w // 2] = 240  # 锁
        frame[y : y + h, x : x + w] = big
        items = [it for it in load_items("song_select") if it.text not in ("原创", "随机选曲")]
        items += [item(self.category, 110, 144), item("随机选曲", 967, 660)]
        if self.selected in self.locked:
            items.append(item("解锁条件", 820, 550))
        self._items = items
        return frame, 0.0

    def read(self, frame, roi=None):
        return list(self._items)

    def tap(self, x, y):
        self.taps.append((x, y))
        if math.dist((x, y), BTN_CATEGORY) < 30:
            self.category = "全部" if self.category == "原创" else "原创"
            return
        assert 190 < x < 700, f"意外的点击 {(x, y)}"
        k = row_offset(y - LIST_THUMB // 2)
        assert k is not None, f"点在了中间那行 {(x, y)}"
        self.index = min(len(self.order) - 1, max(0, self.index + k))

    def down(self, finger, x, y):
        self._down = self._last = (x, y)

    def move(self, finger, x, y):
        self._last = (x, y)

    def up(self, finger):
        dy = self._down[1] - self._last[1]
        self.index = min(len(self.order) - 1, max(0, self.index + round(dy / ROW_PITCH)))
        self.drags += 1

    def flush(self):
        pass


def make_nav(game):
    nav = GameNavigator(Config(), game, game, game, settle_s=0, jackets=JacketMatcher(JACKETS))
    nav._sleep = lambda s: None
    nav.save_debug = lambda *a, **k: None
    return nav


@pytest.fixture(autouse=True)
def no_hold(monkeypatch):
    """拖动结束时的停顿（不可打断的 time.sleep）在测试里跳过。"""
    import ournotes_auto.nav.song_select as ss

    monkeypatch.setattr(ss.time, "sleep", lambda s: None)


# ---------------------------------------------------------------- 纯函数


def test_row_offset():
    assert [row_offset(row_y(k)) for k in (-3, -2, -1, 1, 2, 3)] == [-3, -2, -1, 1, 2, 3]
    assert row_offset(97 + 20) == -2 and row_offset(441 - 20) == 1  # 还在动的列表
    assert row_offset(310) is None and row_offset(345) is None  # 中间那行


@pytest.mark.parametrize("fade", [False, True])
def test_list_rows(fade):
    game = ListGame(IDS, selected=10, fade=fade)
    frame, _ = game.grab()
    rows = list_rows(frame, row_matcher(JacketMatcher(JACKETS)))
    assert [(row_offset(y), mid) for y, mid in rows] == [(k, IDS[10 + k]) for k in (-2, -1, 1, 2, 3)]
    big = cv2.resize(frame, (1920, 1080), interpolation=cv2.INTER_LINEAR)
    assert [mid for _, mid in list_rows(big, row_matcher(JacketMatcher(JACKETS)))] == [mid for _, mid in rows]


def test_list_rows_ignores_unknown():
    game = ListGame(IDS, selected=10)
    frame, _ = game.grab()
    matcher = row_matcher(JacketMatcher({mid: JACKETS[mid] for mid in IDS[20:]}))
    assert list_rows(frame, matcher) == []


def test_list_positions():
    pos = ListPositions()
    assert pos.observe(None, []) is None
    assert pos.observe(5, [(-1, 4), (1, 6)]) == 0
    assert pos.observe(8, [(-2, 6), (-1, 7)]) == 3  # 和 6 重叠
    assert pos.pos == {4: -1, 5: 0, 6: 1, 7: 2, 8: 3}
    assert pos.at(3) == 8 and pos.at(1) == 6
    # 位置对不上（6 和 8 推出的中间不一致）：从这屏重新记
    assert pos.observe(20, [(-1, 6), (2, 8)]) == 0
    assert pos.pos == {20: 0, 6: -1, 8: 2}
    # 没有重叠也重新记
    assert pos.observe(30, []) == 0 and pos.pos == {30: 0}


def test_list_positions_replaces_stale():
    pos = ListPositions()
    pos.observe(1, [(1, 2)])
    pos.observe(1, [(1, 3)])  # 同一位置换成了另一首（上次认错）
    assert pos.pos == {1: 0, 3: 1} and pos.at(1) == 3


# ---------------------------------------------------------------- 按曲目选歌


def test_select_visible_row():
    game = ListGame(IDS, selected=10)
    nav = make_nav(game)
    assert nav.select_song(IDS[12]) == SongPick(IDS[12])
    assert game.selected == IDS[12] and game.drags == 0 and len(game.taps) == 1


def test_select_already_selected():
    game = ListGame(IDS, selected=10)
    nav = make_nav(game)
    assert nav.select_song(IDS[10]) == SongPick(IDS[10])
    assert game.taps == [] and game.drags == 0


def test_select_below_then_above():
    game = ListGame(IDS, selected=3)
    nav = make_nav(game)
    assert nav.select_song(IDS[25]) == SongPick(IDS[25])
    assert game.selected == IDS[25]
    # 往上找没见过的歌：先往下滚到底，再回头
    game.index, game.drags = 20, 0
    nav.list_positions.clear()
    assert nav.select_song(IDS[1]) == SongPick(IDS[1])
    assert game.drags > 5


def test_select_uses_known_positions():
    game = ListGame(IDS, selected=0)
    nav = make_nav(game)
    assert nav.select_song(IDS[29]) is not None  # 从头滚到尾，记下所有位置
    game.drags = 0
    assert nav.select_song(IDS[2]) == SongPick(IDS[2])
    assert game.drags <= 7  # 直接往上，不先往下
    assert nav.select_song(IDS[16]) == SongPick(IDS[16])


def test_select_missing_song():
    game = ListGame(IDS[:20], selected=5)
    nav = make_nav(game)
    assert nav.select_song(IDS[25]) is None
    assert game.selected in IDS[:20]


def test_select_locked_song():
    game = ListGame(IDS, selected=5, locked={IDS[7]})
    nav = make_nav(game)
    assert nav.select_song(IDS[7]) == SongPick(IDS[7], locked=True)
    assert game.selected == IDS[7]


def test_category_change_clears_positions():
    game = ListGame(IDS, selected=5)
    nav = make_nav(game)
    nav.select_song(IDS[6])
    assert nav.list_positions.pos
    nav.set_song_category("全部")
    assert nav.list_positions.pos == {}
