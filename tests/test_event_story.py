"""限时活动的活动故事、视角故事：用画面 OCR 夹具 + 画上红点的假画面模拟演出首页 → 活动页 → 活动故事话数选择。"""

import cv2
import numpy as np
import pytest

from ournotes_auto.nav.screens import find
from ournotes_auto.nav.story import (
    EVENT_ENTRY_ROI,
    EVENT_EPISODES_TITLE,
    EVENT_SELECTED_CORNERS,
    EVENT_STORY_BADGE_OFFSET,
    EVENT_TAB_BADGE,
    POV_TAB_BADGE,
    _same_title,
    event_cards,
    event_story_button,
    selected_event_episode,
    story_title,
)
from ournotes_auto.result_reader import OcrItem
from tests.test_story import RED, MENU_BLUE, clock, load_items, near, story_nav  # noqa: F401

TIME_X0, TIME_STEP = 241, 266  # 卡片右下角时长的中心 x
STORY_BUTTON = (1139, 397)
TEAL = (230, 230, 80)  # 选中卡片四角的光框（BGR）


def test_event_page_helpers():
    page = load_items("event_page")
    assert event_story_button(page) is not None
    assert story_title(page) is None
    assert event_story_button(load_items("event_live_top")) is None
    episodes = load_items("event_episodes")
    assert story_title(episodes) == EVENT_EPISODES_TITLE
    assert selected_event_episode(episodes) == "新曲"
    assert [name for name, _ in event_cards(episodes)] == ["新曲", "慢慢来"]
    pov = load_items("event_pov")
    assert selected_event_episode(pov) == "上吧"
    assert [name for name, _ in event_cards(pov)] == ["上吧", "满满地"]
    # 拖动过的列表：最左边那张只露出时长，标题认不出；简介里的「・」和卡片上的「·」算同一个标题
    scrolled = load_items("event_episodes_scrolled")
    assert [name for name, _ in event_cards(scrolled)] == ["", "绞尽脑汁", "The·梦限大", "交稿"]
    assert _same_title(selected_event_episode(scrolled), "The·梦限大")
    assert not _same_title(None, "交稿") and not _same_title("交稿", "绞尽脑汁")


@pytest.mark.parametrize(
    "lang, cards, pov",
    [
        ("en_", ["", "SqueezeOut", "TheYMMT", "Delivery"], ["HereIGo", "PackedTightly"]),
        ("ko_", ["", "쥐어짜내다", "7유메미타", "납품"], ["자시작하지", "꽉깍"]),
    ],
)
def test_event_page_helpers_other_languages(lang, cards, pov):
    """国际服 English / 한국어 界面：演出首页的 EVENT / 이벤트、活动页的按钮、话数选择页都认得出。"""
    assert find(load_items(f"{lang}event_live_top"), "活动", EVENT_ENTRY_ROI, exact=True) is not None
    assert event_story_button(load_items(f"{lang}event_page")) is not None
    episodes = load_items(f"{lang}event_episodes")
    assert story_title(episodes) == EVENT_EPISODES_TITLE
    assert [name for name, _ in event_cards(episodes)] == cards
    assert selected_event_episode(episodes) == cards[-1]
    assert [name for name, _ in event_cards(load_items(f"{lang}event_pov"))] == pov


class EventGame:
    """同时充当截图源、触控和 OCR。活动故事 ``story`` 依次解锁，``story_read`` 是看过的；视角故事 ``pov`` 都解锁了。"""

    size = (1280, 720)

    def __init__(
        self,
        story=("新曲", "慢慢来", "指南针"),
        story_read=(),
        pov=("上吧", "满满地"),
        pov_read=(),
        event=True,
        garbled=False,
        stuck=(),
    ):
        self.state = "home"
        self.garbled = garbled  # 简介里的大字标题读得和卡片上不一样（韩文 OCR 常见）
        self.stuck = set(stuck)  # 跳过后红点还在的话
        self.event = event
        self.lists = {"story": list(story), "pov": list(pov)}
        self.read_ = {"story": set(story_read), "pov": set(pov_read)}
        self.tab = "story"
        self.selected = {"story": self.first_unread("story") or story[0], "pov": pov[0] if pov else None}
        self.context = None
        self.frames_left = 0
        self.watched: list[str] = []
        self.taps: list[tuple[str, tuple[int, int]]] = []

    def unlocked(self, tab):
        names = self.lists[tab]
        if tab == "pov":
            return names
        n = next((i for i, name in enumerate(names) if name not in self.read_[tab]), len(names))
        return names[: n + 1]

    def unread(self, tab):
        return [n for n in self.unlocked(tab) if n not in self.read_[tab]]

    def first_unread(self, tab):
        return next(iter(self.unread(tab)), None)

    def any_unread(self):
        return bool(self.unread("story") or self.unread("pov"))

    # ---------------------------------------------------------------- 截图 / OCR

    def grab(self):
        img = np.full((720, 1280, 3), 40, np.uint8)
        dots = []
        if self.state == "event_page" and self.any_unread():
            dots.append((STORY_BUTTON[0] + EVENT_STORY_BADGE_OFFSET[0], STORY_BUTTON[1] + EVENT_STORY_BADGE_OFFSET[1]))
        if self.state == "episodes":
            if self.unread("story"):
                dots.append(EVENT_TAB_BADGE)
            if self.unread("pov"):
                dots.append(POV_TAB_BADGE)
            for i, name in enumerate(self.lists[self.tab]):
                if name in self.unread(self.tab):
                    dots.append((TIME_X0 + TIME_STEP * i + 35, 585))
            if self.selected[self.tab] in self.lists[self.tab]:
                x = TIME_X0 + TIME_STEP * self.lists[self.tab].index(self.selected[self.tab])
                for (dx, dy), _ in EVENT_SELECTED_CORNERS:
                    cv2.rectangle(img, (x + dx - 10, 656 + dy - 10), (x + dx + 10, 656 + dy + 10), TEAL, -1)
        for x, y in dots:
            cv2.circle(img, (x, y), 12, RED, -1)
        if self.state in ("story_player", "story_player_menu"):
            cv2.circle(img, (1201, 101), 30, MENU_BLUE, -1)
            for y in (91, 101, 111):
                img[y - 1 : y + 2, 1188:1215] = 255
        if self.state == "black":
            img[:] = 0
        return img, 0.0

    def read_items(self):
        if self.state == "live_top":
            items = load_items("event_live_top")
            return items if self.event else [it for it in items if it.text not in ("活动", "EVENT")]
        if self.state == "event_page":
            return load_items("event_page")
        if self.state == "episodes":
            items = [
                OcrItem(226, 26, 175, 22, EVENT_EPISODES_TITLE),
                OcrItem(148, 23, 30, 32, "合"),
                OcrItem(714, 23, 99, 29, "活动故事"),
                OcrItem(978, 23, 99, 28, "视角故事"),
                OcrItem(72, 357, 77, 41, f"{self.selected[self.tab]}乱码" if self.garbled else self.selected[self.tab]),
                OcrItem(1077, 637, 97, 28, "观看故事"),
            ]
            for i, name in enumerate(self.lists[self.tab]):
                x = TIME_X0 + TIME_STEP * i
                items += [OcrItem(x - 30, 645, 60, 20, "04:46"), OcrItem(x - 200, 665, 24 * len(name), 24, name)]
            return items
        if self.state == "black":
            return []
        return load_items(self.state)

    def read(self, frame, roi=None):
        items = self.read_items()
        if self.state in ("story_loading", "black"):
            self.frames_left -= 1
            if self.frames_left <= 0:
                self.state = "story_player" if self.state == "story_loading" else "story_reward"
        return items

    # ---------------------------------------------------------------- 触控

    def tap(self, x, y):
        p = (x, y)
        self.taps.append((self.state, p))
        s = self.state
        if s == "home" and near(p, (1081, 650)):
            self.state = "live_top"
        elif s == "live_top" and self.event and near(p, (1142, 639)):
            self.state = "event_page"
        elif s == "event_page" and near(p, STORY_BUTTON):
            self.state = "episodes"
        elif s in ("live_top", "event_page", "episodes") and near(p, (164, 40), 12):
            self.state = "home"
        elif s == "episodes":
            if near(p, (765, 40)):
                self.tab = "story"
            elif near(p, (1027, 40)):
                self.tab = "pov"
            elif near(p, (1127, 652)):
                self.state, self.context = "story_download", (self.tab, self.selected[self.tab])
            else:
                for i, name in enumerate(self.lists[self.tab]):
                    if near(p, (TIME_X0 + TIME_STEP * i - 100, 615), 40) and name in self.unlocked(self.tab):
                        self.selected[self.tab] = name
        elif s == "story_download" and near(p, (642, 663)):
            self.state, self.frames_left = "story_loading", 2
        elif s == "story_player" and near(p, (1201, 101)):
            self.state = "story_player_menu"
        elif s == "story_player_menu" and near(p, (1201, 168), 20):
            self.state = "story_skip"
        elif s == "story_skip" and near(p, (756, 534)):
            tab, name = self.context
            if name not in self.stuck:
                self.read_[tab].add(name)
            self.watched.append(name)
            self.state, self.frames_left = "black", 1
        elif s == "story_reward" and near(p, (640, 659)):
            self.state = "episodes"
            if self.context[0] == "story":
                self.selected["story"] = self.first_unread("story") or self.selected["story"]

    def down(self, contact, x, y):
        pass

    def move(self, contact, x, y):
        pass

    def up(self, contact):
        pass

    def flush(self):
        pass


def test_event_story_watches_both_tabs(clock):
    game = EventGame(story_read=("新曲",), pov_read=("上吧",))
    nav = story_nav(game, clock)
    assert nav.run_daily(["event"]) == []
    assert game.watched == ["慢慢来", "指南针", "满满地"]
    assert game.state == "home"
    # 活动页上只点过「活动故事」（不点交换所、乐队编队、演出）
    assert [p for s, p in game.taps if s == "event_page"] == [STORY_BUTTON]


def test_event_story_nothing_unread(clock):
    game = EventGame(story_read=("新曲", "慢慢来", "指南针"), pov_read=("上吧", "满满地"))
    nav = story_nav(game, clock)
    assert nav.run_daily(["event"]) == []
    assert game.watched == []
    assert not any(s == "episodes" for s, _ in game.taps)


def test_event_story_without_event(clock):
    game = EventGame(event=False)
    nav = story_nav(game, clock)
    assert nav.run_daily(["event"]) == []
    assert game.watched == []
    assert not any(s == "event_page" for s, _ in game.taps)


def test_event_story_title_misread(clock):
    """简介里的大字标题和卡片上读得不一样时，按光框认选中的卡片，照样看完。"""
    game = EventGame(story_read=("新曲",), pov_read=("上吧",), garbled=True)
    nav = story_nav(game, clock)
    assert nav.run_daily(["event"]) == []
    assert game.watched == ["慢慢来", "指南针", "满满地"]


def test_event_story_stuck_badge(clock):
    """跳过后红点还在：同一话不反复看，报失败。"""
    game = EventGame(story_read=("新曲", "慢慢来", "指南针"), pov_read=("上吧",), stuck=("满满地",))
    nav = story_nav(game, clock)
    assert nav.run_daily(["event"]) == ["活动故事"]
    assert game.watched == ["满满地"]
