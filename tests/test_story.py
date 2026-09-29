"""看故事：用画面 OCR 夹具 + 画上红点、菜单按钮的假画面模拟乐队故事、视角故事、羁绊故事的各个页面。"""

import json
import math
import types
from pathlib import Path

import cv2
import numpy as np
import pytest

from ournotes_auto.config import Config
from ournotes_auto.nav import daily, story
from ournotes_auto.nav.navigator import GameNavigator
from ournotes_auto.nav.screens import Screen, classify
from ournotes_auto.nav.story import (
    BAND_STORY_BADGE,
    BAND_TAB_BADGE_OFFSET,
    BAND_TABS,
    BOND_MEMBERS_TITLE,
    BOND_PAIRS_TITLE,
    BOND_STORY_BADGE,
    CHAPTER_BADGE,
    CHAPTERS_TITLE,
    EPISODES_TITLE,
    POV_TAB_BADGE,
    bond_episode_rows,
    bond_pairs,
    bond_popup,
    episode_cards,
    member_names,
    pov_cards,
    selected_episode,
    selected_pov,
    story_menu_open,
    story_title,
)
from ournotes_auto.result_reader import OcrItem
from ournotes_auto.runner import NavigationError

SCREENS = Path(__file__).parent / "fixtures" / "screens"
UNKNOWN_CONFIRM = [OcrItem(560, 300, 160, 30, "要领取吗？"), OcrItem(760, 640, 60, 32, "确定")]


def load_items(name: str) -> list[OcrItem]:
    data = json.loads((SCREENS / f"{name}.json").read_text(encoding="utf-8"))
    return [OcrItem(x, y, w, h, text) for x, y, w, h, text in data]


RED = (48, 48, 241)  # BGR，截图里红点的颜色
MENU_BLUE = (151, 89, 73)
CARD_X = {"第1话": 62, "第2话": 329, "第3话": 596, "第4话": 863}  # 底部列表「第N话」的中心 x（y≈652）
CARD_BADGE = (207, -67)  # 红点相对文字中心的位置
SELECTED_OCR_WIDEN = 44  # 实机上选中那张卡片的「第N话」框有时偏宽，中心往右挪约 20px
BTN_WITH_VOICE = (933, 663)
BTN_DOWNLOAD_CANCEL = (353, 663)
# 视角故事：MyGO 5 张卡片，名字中心 x（没拖动时），放得下 3 张半；往左拖一次移 407
POV_NAMES = ("灯", "爱音", "乐奈", "爽世", "立希")
POV_X0, POV_STEP, POV_SCROLL = 189, 267, 407
# 羁绊故事：高松灯的组合（bond_pairs 夹具）所在行的 y；弹窗里第2话锁着（上一行），第1话在下一行
PAIR_Y = {"灯&爱音": 185, "灯&乐奈": 299, "灯&爽世": 412, "灯&立希": 526}
BOND_ROW1 = (550, 265)
BOND_ROW2_LOCKED = (550, 154)


def near(p, q, r=30):
    return math.dist(p, q) < r


class StoryGame:
    """同时充当截图源、触控和 OCR。MyGO 的章节有 ``episodes`` 这几话，``read`` 是看过的；
    看完一话下一话才解锁，``max_unlocked`` 之后的一直锁着（比如要满足别的条件）。
    看完 ``unlocks`` 里的话，领完奖励后再弹「乐曲解锁」。"""

    size = (1280, 720)

    def __init__(
        self, read=("第1话",), max_unlocked=3, selected=None, after_skip="story_reward", unlocks=(), pov=(), bond=()
    ):
        self.state = "home"
        self.episodes = list(CARD_X)
        self.seen = set(read)
        self.max_unlocked = max_unlocked
        self.selected = selected or self.first_unread() or self.episodes[0]
        self.after_skip = after_skip
        self.unlocks = set(unlocks)
        self.tab = "ALL"
        self.ep_tab = "band"  # 话数选择页的分页：band / pov
        self.pov_unread = set(pov)  # 解锁了没看过的视角故事，其他的锁着
        self.pov_open = set(pov)
        self.pov_selected = POV_NAMES[0]
        self.pov_scroll = 0
        self.bond_unread = set(bond)  # 高松灯的组合里第1话没看过的
        self.pair = None
        self.context = None  # 正在看的：("band", 第N话) / ("pov", 成员) / ("bond", 组合)
        self.drag = None
        self.drags = 0
        self.locked_taps = 0
        self.frames_left = 0  # 加载 / 跳过后黑屏还剩几帧
        self.watched: list[str] = []
        self.taps: list[tuple[str, tuple[int, int]]] = []

    def first_unread(self):
        for i, ep in enumerate(self.episodes[: self.max_unlocked]):
            if ep not in self.seen:
                return ep
        return None

    def unread(self):
        ep = self.first_unread()
        return {ep} if ep else set()

    def band_unread(self):
        return bool(self.unread() or self.pov_unread)

    def pov_x(self):
        """看得到的视角故事卡片：成员名 → 名字中心 x。"""
        xs = {name: POV_X0 + POV_STEP * i - POV_SCROLL * self.pov_scroll for i, name in enumerate(POV_NAMES)}
        return {name: x for name, x in xs.items() if 20 <= x <= 960}

    def back_state(self):
        return "bond_popup" if self.context and self.context[0] == "bond" else "story_episodes"

    # ---------------------------------------------------------------- 截图 / OCR

    def grab(self):
        img = np.full((720, 1280, 3), 40, np.uint8)
        dots = []
        if self.state == "story_menu" and self.band_unread():
            dots.append(BAND_STORY_BADGE)
        if self.state == "story_menu" and self.bond_unread:
            dots.append(BOND_STORY_BADGE)
        if self.state == "story_chapters" and self.band_unread():
            tab = dict(BAND_TABS)["MyGO!!!!!"]
            dots.append((tab[0] + BAND_TAB_BADGE_OFFSET[0], tab[1] + BAND_TAB_BADGE_OFFSET[1]))
            if self.tab == "MyGO!!!!!":
                dots.append(CHAPTER_BADGE)
        if self.state == "story_episodes" and self.ep_tab == "band":
            for ep in self.unread():
                dots.append((CARD_X[ep] + CARD_BADGE[0], 652 + CARD_BADGE[1]))
        if self.state == "story_episodes" and self.ep_tab == "pov":
            dots += [(x + 80, 585) for name, x in self.pov_x().items() if name in self.pov_unread]
        if self.state == "story_episodes" and self.pov_unread:
            dots.append(POV_TAB_BADGE)
        if self.state == "bond_members" and self.bond_unread:
            dots += [(179, 101), (791, 128)]  # MyGO 分页、高松灯
        if self.state == "bond_pairs" and self.bond_unread:
            dots += [(1180, PAIR_Y[pair] - 43) for pair in self.bond_unread]
        if self.state == "bond_popup" and self.pair in self.bond_unread:
            dots.append((909, 220))
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
        if self.state == "story_episodes" and self.ep_tab == "pov":
            button = "观看故事" if self.pov_selected in self.pov_open else "解锁条件"
            items = [
                OcrItem(225, 25, 177, 26, EPISODES_TITLE),
                OcrItem(148, 23, 30, 32, "合"),
                OcrItem(76, 315, 114, 23, f"{self.pov_selected}视角Ver."),
                OcrItem(1097, 636, 97, 29, button),
            ]
            for name, x in self.pov_x().items():
                items += [OcrItem(x - 12 * len(name), 596, 24 * len(name), 24, name), OcrItem(x - 26, 618, 52, 20, "视角Ver.")]
            return items
        if self.state == "bond_popup":
            return [
                OcrItem(580, 22, 118, 33, "羁绊故事"),
                OcrItem(388, 120, 54, 25, "第2话"),
                OcrItem(386, 160, 107, 27, f"{self.pair}2"),
                OcrItem(386, 228, 55, 30, "第1话"),
                OcrItem(385, 270, 104, 27, f"{self.pair}1"),
                OcrItem(610, 647, 56, 31, "关闭"),
            ]
        if self.state == "story_episodes":
            items = [
                OcrItem(230, 25, 170, 24, EPISODES_TITLE),
                OcrItem(158, 28, 12, 22, "合"),
                OcrItem(80, 315, 52, 22, self.selected),
                OcrItem(1086, 640, 80, 22, "观看故事"),
            ]
            widen = {self.selected: SELECTED_OCR_WIDEN}
            return items + [OcrItem(x - 24, 641, 48 + widen.get(ep, 0), 22, ep) for ep, x in CARD_X.items()]
        if self.state == "black":
            return []
        if self.state == "unknown_confirm":
            return list(UNKNOWN_CONFIRM)
        return load_items(self.state)

    def read(self, frame, roi=None):
        items = self.read_items()
        if self.state in ("story_loading", "black"):
            self.frames_left -= 1
            if self.frames_left <= 0:
                self.state = "story_player" if self.state == "story_loading" else self.after_skip
        return items

    # ---------------------------------------------------------------- 触控

    def tap(self, x, y):
        p = (x, y)
        self.taps.append((self.state, p))
        s = self.state
        if s == "home" and near(p, (805, 625)):
            self.state = "story_menu"
        elif s == "story_menu":
            if near(p, (805, 625)):
                self.state = "home"
            elif near(p, (700, 440)):
                self.state, self.tab = "story_chapters", "ALL"
            elif near(p, (900, 440)):
                self.state = "bond_members"
        elif s == "story_chapters":
            for band, point in BAND_TABS:
                if near(p, point):
                    self.tab = band
            if near(p, (1143, 667)) and self.tab == "MyGO!!!!!":
                self.state = "story_episodes"
                self.selected = self.first_unread() or self.selected
            elif near(p, (164, 40), 12):
                self.state = "home"
        elif s == "story_episodes":
            if near(p, (765, 40)):
                self.ep_tab = "band"
            elif near(p, (1027, 40)):
                self.ep_tab, self.pov_scroll = "pov", 0
            elif near(p, (1127, 652)) and self.ep_tab == "pov":
                if self.pov_selected in self.pov_open:
                    self.state, self.context = "story_download", ("pov", self.pov_selected)
                else:
                    self.locked_taps += 1  # 选中锁着的卡片时这里是「解锁条件」
            elif near(p, (1127, 652)):
                self.state, self.context = "story_download", ("band", self.selected)
            elif near(p, (58, 38), 20):
                self.state = "story_chapters"
            elif near(p, (164, 40), 12):
                self.state = "home"
            elif self.ep_tab == "pov":
                for name, x in self.pov_x().items():
                    if near(p, (x, 620), 40):
                        self.pov_selected = name
            else:
                for ep, cx in CARD_X.items():
                    if near(p, (cx + 90, 622), 40) and self.episodes.index(ep) < self.max_unlocked:
                        self.selected = ep
        elif s == "story_download":
            if near(p, (642, 663)):
                self.state, self.frames_left = "story_loading", 2
            elif near(p, BTN_DOWNLOAD_CANCEL) or near(p, BTN_WITH_VOICE):
                self.state = self.back_state()
        elif s == "story_player" and near(p, (1201, 101)):
            self.state = "story_player_menu"
        elif s == "story_player_menu":
            if near(p, (1201, 168), 20):
                self.state = "story_skip"
            elif near(p, (1201, 101), 20):
                self.state = "story_player"
        elif s == "story_skip":
            if near(p, (756, 534)):
                kind, name = self.context
                if kind == "band":
                    self.seen.add(name)
                    self.watched.append(name)
                elif kind == "pov":
                    self.pov_unread.discard(name)
                    self.watched.append(f"{name}视角")
                else:
                    self.bond_unread.discard(name)
                    self.watched.append(f"{name}·第1话")
                self.state, self.frames_left = "black", 1
            elif near(p, (524, 534)):
                self.state = "story_player_menu"
        elif s == "story_reward" and near(p, (640, 659)):
            if self.watched[-1] in self.unlocks:
                self.state = "story_song_unlock"
            else:
                self.state = self.back_state()
                self.selected = self.first_unread() or self.selected
        elif s == "story_song_unlock" and near(p, (640, 570)):
            self.state = self.back_state()
            self.selected = self.first_unread() or self.selected
        elif s == "bond_members":
            if any(near(p, (x, 400), 60) for x in (302, 504, 701, 903, 1102)):
                self.state = "bond_pairs"
            elif near(p, (164, 40), 12):
                self.state = "home"
        elif s == "bond_pairs":
            for pair, y in PAIR_Y.items():
                if near(p, (997, y)):
                    self.state, self.pair = "bond_popup", pair
            if near(p, (58, 38), 20):
                self.state = "bond_members"
            elif near(p, (164, 40), 12):
                self.state = "home"
        elif s == "bond_popup":
            if near(p, BOND_ROW1) and self.pair in self.bond_unread:
                self.state, self.context = "story_download", ("bond", self.pair)
            elif near(p, (638, 662)):
                self.state = "bond_pairs"

    def down(self, contact, x, y):
        self.drag = [(x, y), (x, y)]

    def move(self, contact, x, y):
        self.drag[1] = (x, y)

    def up(self, contact):
        (x0, _), (x1, _) = self.drag
        if self.state == "story_episodes" and self.ep_tab == "pov" and x0 - x1 > 300:
            self.pov_scroll = 1
        self.drags += 1

    def flush(self):
        pass


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    fake = types.SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(daily, "time", fake)
    monkeypatch.setattr(story, "time", fake)
    return now


def story_nav(game, clock):
    nav = GameNavigator(Config(), game, game, game, settle_s=0)

    def sleep(s):
        clock[0] += s

    nav._sleep = sleep
    nav.save_debug = lambda *a, **k: None
    return nav


def test_story_page_helpers():
    assert story_menu_open(load_items("story_menu"))
    assert not story_menu_open(load_items("home"))
    assert story_title(load_items("story_chapters")) == CHAPTERS_TITLE
    assert story_title(load_items("story_chapters_all")) == CHAPTERS_TITLE
    assert story_title(load_items("story_episodes")) == EPISODES_TITLE
    for name in ("story_menu", "story_download", "story_player_menu", "story_skip", "story_reward"):
        assert story_title(load_items(name)) is None, name
    items = load_items("story_episodes")
    assert selected_episode(items) == "第2话"
    assert [name for name, _ in episode_cards(items)] == ["第1话", "第2话", "第3话"]
    assert selected_episode(load_items("story_chapters")) is None
    # 看完的奖励弹窗只点 OK；下载确认、跳过确认没有 OK / 确定
    assert daily.reward_ok(load_items("story_reward")) is not None
    # 乐曲解锁、故事解锁提示只有「关闭」
    for name in ("story_song_unlock", "story_unlock"):
        assert not daily.has_confirm(load_items(name)), name
    for name in ("story_download", "story_skip", "story_player_menu", "bond_download", "bond_player_menu"):
        assert not daily.has_confirm(load_items(name)), name
    assert daily.reward_ok(load_items("bond_reward")) is not None


def test_pov_page_helpers():
    items = load_items("story_pov")
    assert story_title(items) == EPISODES_TITLE
    assert selected_pov(items) == "灯"
    # 「视角Ver.」读成「角Ver.」「见角Ver.」也认；只露出一半的第 4 张没有名字，不算
    assert [name for name, _ in pov_cards(items)] == ["灯", "爱音", "乐奈"]
    scrolled = load_items("story_pov_scrolled")
    assert [name for name, _ in pov_cards(scrolled)] == ["爱音", "乐奈", "爽世", "立希"]
    assert dict(pov_cards(scrolled))["爱音"] == (50, 608)
    # 乐队故事分页上没有视角卡片
    assert pov_cards(load_items("story_episodes")) == []
    assert selected_pov(load_items("story_episodes")) is None


def test_bond_page_helpers():
    members = load_items("bond_members")
    assert story_title(members) == BOND_MEMBERS_TITLE
    # 只要中文名（上面一行的罗马字不算），OCR 把「千」读成「干」也照样用位置
    assert [name for name, _ in member_names(members)] == ["干早爱音", "长崎爽世", "高松灯", "椎名立希", "要乐奈"]
    pairs = load_items("bond_pairs")
    assert story_title(pairs) == BOND_PAIRS_TITLE
    assert [name for name, _ in bond_pairs(pairs)] == ["灯&爱音", "灯&乐奈", "灯&爽世", "灯&立希"]
    # 组合的弹窗：标题在中间，不是页面标题；会被认成普通弹窗（所以流程里不能用 _dismiss 关）
    for name in ("bond_episodes", "bond_episodes_locked"):
        items = load_items(name)
        assert bond_popup(items), name
        assert story_title(items) is None, name
        assert classify(items) is Screen.POPUP, name
    assert [name for name, _ in bond_episode_rows(load_items("bond_episodes"))] == ["第2话", "第1话"]
    assert not bond_popup(members) and not bond_popup(load_items("story_menu"))


def test_story_skips_unread_episodes(clock):
    game = StoryGame(read=("第1话",), max_unlocked=3, unlocks=("第2话",))
    nav = story_nav(game, clock)
    assert nav.run_daily(["story"]) == []
    assert game.watched == ["第2话", "第3话"]
    assert game.state == "home"
    # 选的是无语音；没点过有语音、下载确认的取消，只在看故事的流程里点过跳过
    assert not any(s == "story_download" and (near(p, BTN_WITH_VOICE) or near(p, BTN_DOWNLOAD_CANCEL)) for s, p in game.taps)
    assert all(s == "story_skip" for s, p in game.taps if near(p, (756, 534)))
    assert any(s == "story_song_unlock" for s, _ in game.taps)  # 看完第2话的乐曲解锁点了关闭
    # 没有红点的乐队分页没点过
    other_tabs = [point for band, point in BAND_TABS if band != "MyGO!!!!!"]
    assert not any(near(p, q) for _, p in game.taps for q in other_tabs)


def test_story_selects_unread_card(clock):
    """进来时选中的不是没看过的那话：先点列表里有红点的卡片。"""
    game = StoryGame(read=("第1话", "第2话"), max_unlocked=4)
    nav = story_nav(game, clock)
    game.state = "story_episodes"
    game.selected = "第1话"
    assert nav._story_episodes("MyGO!!!!!") == 2
    assert game.watched == ["第3话", "第4话"]


def test_story_nothing_unread(clock):
    """故事菜单里乐队故事没有红点：收起菜单，不进去。"""
    game = StoryGame(read=("第1话", "第2话", "第3话"), max_unlocked=3)
    nav = story_nav(game, clock)
    assert nav.run_daily(["story"]) == []
    assert game.state == "home"
    assert not any(near(p, (700, 440)) for _, p in game.taps)


def test_story_unknown_confirm_is_not_tapped(clock):
    game = StoryGame(after_skip="unknown_confirm")
    nav = story_nav(game, clock)
    game.state = "story_episodes"
    with pytest.raises(NavigationError, match="认不出的弹窗"):
        nav._story_episodes("MyGO!!!!!")
    assert game.state == "unknown_confirm"
    assert all(s != "unknown_confirm" for s, _ in game.taps)


def test_story_watches_pov(clock):
    """乐队故事都看过了，视角故事有两个没看：看得到的先看，另一个在列表后面，往左拖一次才看得到。"""
    game = StoryGame(read=("第1话", "第2话", "第3话"), max_unlocked=3, pov=("爱音", "立希"))
    nav = story_nav(game, clock)
    assert nav.run_daily(["story"]) == []
    assert game.watched == ["爱音视角", "立希视角"]
    assert game.drags == 1
    assert game.state == "home"
    assert game.locked_taps == 0  # 锁着的卡片的「解锁条件」没点过


def test_story_watches_bond(clock):
    """羁绊故事：MyGO → 高松灯 → 有红点的组合 → 弹窗里没看过的第1话；锁着的第2话不点，看完才关弹窗。"""
    game = StoryGame(read=("第1话", "第2话", "第3话"), max_unlocked=3, bond=("灯&爽世", "灯&立希"))
    nav = story_nav(game, clock)
    assert nav.run_daily(["story"]) == []
    assert game.watched == ["灯&爽世·第1话", "灯&立希·第1话"]
    assert game.state == "home"
    assert not any(near(p, (700, 440)) for _, p in game.taps)  # 乐队故事没红点，不进
    assert not any(s == "bond_popup" and near(p, BOND_ROW2_LOCKED) for s, p in game.taps)
    # 没红点的组合没打开过；弹窗只在看完之后关（每个组合一次）
    opened = [p for s, p in game.taps if s == "bond_pairs" and near(p, (997, PAIR_Y["灯&爽世"]), 200)]
    assert all(near(p, (997, PAIR_Y["灯&爽世"])) or near(p, (997, PAIR_Y["灯&立希"])) for p in opened)
    assert len([p for s, p in game.taps if s == "bond_popup" and near(p, (638, 662))]) == 2


def test_story_band_then_bond(clock):
    """乐队故事和羁绊故事都有：先看乐队故事，回主界面再从故事菜单进羁绊故事。"""
    game = StoryGame(read=("第1话",), max_unlocked=2, pov=("爱音",), bond=("灯&爱音",))
    nav = story_nav(game, clock)
    assert nav.run_daily(["story"]) == []
    assert game.watched == ["第2话", "爱音视角", "灯&爱音·第1话"]
    assert game.state == "home"
