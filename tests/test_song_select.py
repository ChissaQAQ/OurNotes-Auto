"""乐曲选择页：筛选面板 / 分类 / 随机选曲（用模拟的画面和 OCR 结果）。"""

import math

import cv2
import numpy as np
import pytest

from ournotes_auto.config import Config
from ournotes_auto.nav.navigator import GameNavigator
from ournotes_auto.nav.screens import Screen, classify, find, in_roi
from ournotes_auto.nav.song_select import (
    BTN_CATEGORY,
    BTN_FILTER,
    BTN_FILTER_CLOSE,
    BTN_FILTER_RESET,
    BTN_RANDOM,
    CATEGORIES,
    FILTER_BUTTONS_ROI,
    FILTER_ROI,
    FUNNEL_POINT,
    NO_RANDOM_TEXT,
    SongPick,
    filter_open,
    filter_option,
    funnel_active,
    list_empty,
    song_category,
    song_locked,
)
from ournotes_auto.result_reader import OcrItem
from ournotes_auto.runner import NavigationError
from test_navigator import load_items


def item(text, cx, cy, w=None):
    w = w or 16 * len(text)
    return OcrItem(cx - w / 2, cy - 12, w, 24, text)


# ---------------------------------------------------------------- 纯函数


@pytest.mark.parametrize(
    "ocr, want",
    [
        ("NORMA[", "NORMAL"),
        ("EXPERT", "EXPERT"),
        ("未FUL_COMBO", "未FULLCOMBO"),
        ("FULLCOMBO", "FULLCOMBO"),
        ("未 ALLPERFECT", "未ALLPERFECT"),
        ("ALLPERFECT", "ALLPERFECT"),
        ("未达成SS", "未达成SS"),
    ],
)
def test_filter_option_matches_ocr_variants(ocr, want):
    it = item(ocr, 900, 300)
    assert filter_option([it], want) is it


def test_filter_option_exact_and_roi():
    ap, not_ap = item("ALLPERFECT", 1060, 400), item("未ALLPERFECT", 820, 400)
    assert filter_option([not_ap, ap], "ALLPERFECT") is ap
    assert filter_option([ap], "未ALLPERFECT") is None
    assert filter_option([item("EXPERT", 500, 300)], "EXPERT") is None  # 面板外
    assert filter_option([item("HAR", 900, 300)], "HARD") is None  # 短选项不做模糊匹配
    # 两个「不指定」取上面的（游玩状况），下面的是演出效果
    low, high = item("不指定", 820, 600), item("不指定", 820, 200)
    assert filter_option([low, high], "不指定") is high


def test_page_helpers():
    assert song_category([item("原创", 110, 144)]) == "原创"
    assert song_category([item("全部", 110, 144)]) == "全部"
    assert song_category([item("全部", 900, 144)]) is None
    assert filter_open([item("重置", 818, 660), item("随机选曲", 967, 660), item("关闭", 1142, 660)])
    assert not filter_open([item("随机选曲", 967, 660), item("确定", 1164, 660)])
    assert list_empty([item("没有符合筛选条件的乐曲", 640, 345)])
    assert not list_empty([item("没有符合筛选条件的乐曲", 640, 600)])
    assert song_locked([item("解锁条件", 820, 550)])
    assert not song_locked([item("解锁条件", 400, 550)])
    # 随机选曲没得抽的提示（实机截图）：还是乐曲选择页，不是空列表
    toast = load_items("song_select_no_random")
    assert classify(toast) is Screen.SONG_SELECT and not list_empty(toast)
    assert find(toast, NO_RANDOM_TEXT) and not find(load_items("song_select"), NO_RANDOM_TEXT)


def _frame_with(hsv):
    frame = np.full((720, 1280, 3), 40, np.uint8)
    color = cv2.cvtColor(np.uint8([[hsv]]), cv2.COLOR_HSV2BGR)[0, 0]
    x, y = FUNNEL_POINT
    frame[y - 8 : y + 9, x - 8 : x + 9] = color
    return frame


def test_funnel_active():
    assert funnel_active(_frame_with((95, 200, 210)))
    assert not funnel_active(_frame_with((114, 150, 138)))  # 平时
    assert not funnel_active(_frame_with((60, 200, 230)))  # 颜色不对
    big = cv2.resize(_frame_with((95, 200, 210)), (1920, 1080), interpolation=cv2.INTER_NEAREST)
    assert funnel_active(big)


# ---------------------------------------------------------------- 模拟的乐曲选择页

# 面板内容坐标（面板在顶部时的屏幕坐标）：(文字, 组, 中心 x, 中心 y)
PANEL = [
    ("全部", "fav", 817, 197),
    ("收藏1", "fav", 1062, 197),
    ("EASY", "diff", 825, 380),
    ("NORMA[", "diff", 1080, 380),  # OCR 常这样读
    ("HARD", "diff", 825, 440),
    ("EXPERT", "diff", 1077, 440),
]
STATUS_TEXT = ["不指定", "未完成", "未达成SS", "未FUL_COMBO", "未ALLPERFECT", "已完成", "已达成SS", "FULLCOMBO", "ALLPERFECT"]
for i, text in enumerate(STATUS_TEXT):
    PANEL.append((text, "status", 830 + 240 * (i % 2), 708 + 60 * (i // 2)))
PANEL.append(("不指定", "effect", 830, 1500))  # 演出效果
SCROLL_MAX = 1100
DRAG_LOSS = 6  # 按住拖动时实际滚动比手指少的距离


class SongSelectGame:
    """同时充当截图源、触控、OCR 和封面识别。"""

    size = (1280, 720)

    def __init__(self, songs=(), category="原创", diff="EXPERT", status="不指定"):
        self.category = category
        self.selected = {"fav": "全部", "diff": diff, "status": status, "effect": "不指定"}
        self.panel = False
        self.scroll = 0
        self.songs = list(songs)  # 随机选曲依次抽到的 (musicId, 是否未解锁)；None 表示没得抽
        self.pick = None
        self.empty = False
        self.toast = 0  # 「没有可以随机选择的乐曲。」还要显示几帧
        self.covered = False
        self.taps = []
        self.drags = 0
        self._down = None
        self._items = []

    # 截图 / OCR
    def _panel_items(self):
        out = []
        for text, group, cx, cy in PANEL:
            y = cy - self.scroll
            it = item(text, cx, y)
            if in_roi(it, FILTER_ROI):
                out.append((it, self.selected[group] == text))
        return out

    def grab(self):
        frame = np.full((720, 1280, 3), 40, np.uint8)
        x, y = FUNNEL_POINT
        status_on = self.selected["status"] != "不指定"
        frame[y - 5 : y + 6, x - 5 : x + 6] = (210, 200, 20) if status_on else (138, 90, 80)
        items = [it for it in load_items("song_select") if it.text not in ("原创", "随机选曲")]
        items.append(item(self.category, 110, 144))
        if self.empty:
            items.append(item("没有符合筛选条件的乐曲", 640, 345))
        elif self.pick is not None and self.pick[1]:
            items.append(item("解锁条件", 820, 550))
        self.covered = self.toast > 0
        if self.covered:
            self.toast -= 1
            items.append(item("没有可以随机选择的乐曲。", 640, 360))
        if self.panel:
            items = [it for it in items if not in_roi(it, FILTER_ROI) and not in_roi(it, FILTER_BUTTONS_ROI)]
            for it, on in self._panel_items():
                items.append(it)
                rx, ry = round(it.x) - 33, round(it.cy)
                frame[ry - 6 : ry + 7, rx - 6 : rx + 7] = 230 if on else 71
            items += [item("重置", 818, 660), item("随机选曲", 967, 660), item("关闭", 1142, 660)]
        else:
            items.append(item("随机选曲", 967, 660))
        self._items = items
        return frame, 0.0

    def read(self, frame, roi=None):
        return list(self._items)

    def identify(self, crop, min_score=None, min_margin=None):
        if self.covered:  # 提示条挡住封面，认不出
            return None
        return (self.pick[0], 0.9) if self.pick and self.pick[0] else None

    # 触控
    def tap(self, x, y):
        p = (x, y)
        self.taps.append(p)
        near = lambda q: math.dist(p, q) < 30  # noqa: E731
        if self.panel:
            if near(BTN_FILTER_CLOSE):
                self.panel = False
            elif near(BTN_FILTER_RESET):
                self.selected.update(fav="全部", status="不指定", effect="不指定")
            else:
                for it, _ in self._panel_items():
                    if near((it.x + it.w / 2, it.cy)):
                        group = next(g for t, g, cx, cy in PANEL if t == it.text and abs(cy - self.scroll - it.cy) < 1)
                        self.selected[group] = it.text
            return
        if near(BTN_FILTER):
            self.panel, self.scroll = True, 0
        elif near(BTN_CATEGORY):
            self.category = CATEGORIES[(CATEGORIES.index(self.category) + 1) % len(CATEGORIES)]
        elif near(BTN_RANDOM):
            assert not self.empty, "列表为空时不该点随机选曲"
            nxt = self.songs.pop(0)
            if nxt is None:
                self.toast = 3
            else:
                self.pick = nxt
        else:
            raise AssertionError(f"意外的点击 {p}")

    def down(self, finger, x, y):
        self._down = self._last = (x, y)

    def move(self, finger, x, y):
        self._last = (x, y)

    def up(self, finger):
        assert self.panel and in_roi(item("x", *self._down, w=2), FILTER_ROI)
        dy = self._down[1] - self._last[1]
        self.scroll = min(SCROLL_MAX, max(0, self.scroll + dy - DRAG_LOSS))
        self.drags += 1
        self._down = None

    def flush(self):
        pass


def make_nav(game):
    nav = GameNavigator(Config(), game, game, game, settle_s=0, jackets=game)
    nav._sleep = lambda s: None
    return nav


def test_set_filter_difficulty_and_status():
    game = SongSelectGame()
    nav = make_nav(game)
    nav.set_song_filter("hard", "not_ap", reset=True)
    assert not game.panel
    assert game.selected["diff"] == "HARD" and game.selected["status"] == "未ALLPERFECT"
    assert game.drags >= 1  # 游玩状况在面板下面，要拖上来


def test_set_filter_normal_read_as_norma():
    game = SongSelectGame()
    nav = make_nav(game)
    nav.set_song_filter("normal")
    assert game.selected["diff"] == "NORMA["
    assert game.selected["status"] == "不指定" and game.drags == 0


def test_set_filter_reset_clears_status():
    game = SongSelectGame(status="FULLCOMBO")
    nav = make_nav(game)
    nav.set_song_filter(reset=True)
    assert game.selected["status"] == "不指定" and game.selected["diff"] == "EXPERT"
    assert game.drags == 0


def test_clear_status_filter():
    game = SongSelectGame(status="未ALLPERFECT")
    nav = make_nav(game)
    nav.clear_status_filter()
    assert game.selected["status"] == "不指定" and game.selected["effect"] == "不指定"
    assert not game.panel
    # 漏斗不是青色时不打开面板
    taps = len(game.taps)
    nav.clear_status_filter()
    nav.set_song_filter(status="any")  # 已经改回了
    assert len(game.taps) == taps


def test_set_filter_skips_status_already_set():
    game = SongSelectGame()
    nav = make_nav(game)
    nav.set_song_filter("expert", "not_ap", reset=True)
    drags = game.drags
    nav.set_song_filter("hard", "not_ap")  # 只换难度：游玩状况不用再拖出来选
    assert game.selected["diff"] == "HARD" and game.selected["status"] == "未ALLPERFECT"
    assert game.drags == drags
    taps = len(game.taps)
    nav.set_song_filter(status="not_ap")  # 没有要改的：不打开面板
    assert len(game.taps) == taps and not game.panel
    # 游戏里被改掉了（漏斗不是青色了）：照常去选
    game.selected["status"] = "不指定"
    nav.set_song_filter(status="not_ap")
    assert game.selected["status"] == "未ALLPERFECT" and game.drags > drags


def test_set_filter_forgets_status_after_title():
    game = SongSelectGame()
    nav = make_nav(game)
    nav.set_song_filter("expert", "not_ap", reset=True)
    game.selected["status"] = "未FUL_COMBO"  # 重新登录后变了（漏斗还是青色，看不出来）
    title = load_items("title")
    game.read = lambda frame, roi=None: title
    assert nav.look()[0] is Screen.TITLE
    del game.read
    nav.set_song_filter(status="not_ap")
    assert game.selected["status"] == "未ALLPERFECT"


def test_closes_open_panel_before_other_actions():
    game = SongSelectGame()
    game.panel = True
    nav = make_nav(game)
    assert nav.set_song_category("全部") == "原创"
    assert not game.panel and game.category == "全部"
    assert nav.set_song_category("全部") == "全部"
    assert nav.set_song_category("原创") == "全部" and game.category == "原创"


def test_random_song_reads_pick():
    game = SongSelectGame(songs=[(100055, False), (None, True)])
    nav = make_nav(game)
    assert nav.random_song() == SongPick(100055)
    assert nav.random_song() == SongPick(None, locked=True)


def test_random_song_waits_out_no_random_toast():
    # 没得抽时提示条挡住封面，等它消失再认：选中的还是原来那首
    game = SongSelectGame(songs=[(100021, False), None])
    nav = make_nav(game)
    assert nav.random_song() == SongPick(100021)
    assert nav.random_song() == SongPick(100021, only=True)
    assert game.toast == 0


def test_random_song_locked_under_toast_returns_at_once():
    # 提示条挡着封面，但「解锁条件」读得到：不用等提示条消失
    game = SongSelectGame(songs=[(100021, True), None])
    nav = make_nav(game)
    assert nav.random_song() == SongPick(100021, locked=True)
    assert nav.random_song() == SongPick(None, locked=True, only=True)
    assert game.toast == 2


def test_random_song_empty_list():
    game = SongSelectGame()
    game.empty = True
    nav = make_nav(game)
    assert nav.random_song() == SongPick(None, empty=True)
    assert game.taps == []


def test_missing_option_fails():
    game = SongSelectGame()
    nav = make_nav(game)
    nav.save_debug = lambda *a, **k: None
    with pytest.raises(NavigationError, match="找不到"):
        nav._open_filter()
        nav._select_filter_option("附带MV")
    assert game.drags == 3
