"""日常领取：用画面 OCR 夹具模拟主界面和各日常页面。"""

import json
import math
import types
from pathlib import Path

import numpy as np
import pytest

from ournotes_auto.config import Config
from ournotes_auto.nav import daily
from ournotes_auto.nav.daily import (
    CLAIM_BUTTONS,
    DAILY_JOBS,
    OPT_IN_JOBS,
    day_tabs,
    gift_confirm,
    home_icon,
    page_title,
    practice_level_up,
    reward_ok,
)
from ournotes_auto.nav.navigator import GameNavigator
from ournotes_auto.nav.screens import center
from ournotes_auto.result_reader import OcrItem
from ournotes_auto.runner import NavigationError

SCREENS = Path(__file__).parent / "fixtures" / "screens"


def load_items(name: str) -> list[OcrItem]:
    data = json.loads((SCREENS / f"{name}.json").read_text(encoding="utf-8"))
    return [OcrItem(x, y, w, h, text) for x, y, w, h, text in data]

# 夹具 → 页面标题
PAGES = {
    "daily_studio": "录音室练习",
    "daily_studio_empty": "录音室练习",
    "daily_missions": "任务",
    "daily_missions_regular": "任务",
    "daily_pass": "任务通行证",
    "daily_pass_missions": "通行证任务",
    "daily_pass_missions_regular": "通行证任务",
    "daily_limited": "限定任务",
    "daily_beginner": "新手任务",
    "daily_gifts_empty": "礼物盒",
}

# 画面 -> [(点击位置, 下一个画面)]，点击落在 30px 内即触发
ROUTES = {
    "home": [
        ((1232, 515), "daily_studio"),
        ((1232, 243), "daily_missions"),
        ((1232, 340), "daily_pass"),
        ((40, 325), "daily_limited"),
        ((40, 152), "daily_beginner"),
        ((1232, 150), "daily_gifts_empty"),
    ],
    "daily_missions": [((133, 206), "daily_missions_regular"), ((497, 655), "home")],
    "daily_missions_regular": [((146, 138), "daily_missions"), ((497, 655), "home")],
    "daily_pass": [((1165, 38), "daily_pass_missions"), ((164, 40), "home")],
    "daily_pass_missions": [((148, 192), "daily_pass_missions_regular"), ((497, 657), "daily_pass")],
    "daily_pass_missions_regular": [((131, 118), "daily_pass_missions"), ((497, 657), "daily_pass")],
    "daily_gifts_empty": [((497, 651), "home")],
    "song_select": [((164, 40), "home")],
    # 领取后的弹窗：获得奖励 OK → 回到原页面（见 DailyGame.back）
    "daily_studio_reward": [((964, 659), "daily_studio_levelup")],
    "daily_studio_levelup": [((640, 660), "daily_studio_empty")],
}
for _page in ("daily_studio", "daily_studio_empty", "daily_limited", "daily_beginner"):
    ROUTES.setdefault(_page, []).append(((164, 40), "home"))

UNKNOWN_CONFIRM = [OcrItem(560, 300, 160, 30, "要领取吗？"), OcrItem(760, 640, 60, 32, "确定")]


class DailyGame:
    """同时充当截图源、触控和 OCR。``claimable`` 里的页面「一键领取」是亮的，点了弹出获得奖励、
    OK 后回到原页面并变灰；``popup`` 指定点了之后弹出的画面（默认 reward）。"""

    size = (1280, 720)

    def __init__(self, state="home", claimable=(), popup="reward", routes=None):
        self.state = state
        self.claimable = set(claimable)
        self.popup = popup
        self.routes = {**ROUTES, **(routes or {})}
        self.back = None
        self.taps: list[tuple[str, tuple[int, int]]] = []
        self.claimed: list[str] = []

    def grab(self):
        img = np.full((720, 1280, 3), 40, np.uint8)
        page = self.state if self.state in PAGES else None
        if page is not None and (page in self.claimable or PAGES[page] == "录音室练习"):
            x, y = CLAIM_BUTTONS[PAGES[page]]
            img[y - 20 : y + 20, x - 110 : x + 110] = (244, 175, 130)
        return img, 0.0

    def read(self, frame, roi=None):
        if self.state == "unknown_confirm":
            return list(UNKNOWN_CONFIRM)
        return load_items(self.state)

    def tap(self, x, y):
        p = (x, y)
        self.taps.append((self.state, p))
        if self.state == "reward" and math.dist(p, (640, 659)) < 30:
            self.state = self.back
            return
        page = PAGES.get(self.state)
        if page is not None and math.dist(p, CLAIM_BUTTONS[page]) < 30:
            if self.state in self.claimable:
                self.claimable.discard(self.state)
                self.claimed.append(self.state)
                self.back = self.state
                self.state = "daily_studio_reward" if self.state == "daily_studio" else self.popup
            return
        for target, nxt in self.routes.get(self.state, []):
            if math.dist(p, target) < 30:
                self.state = nxt
                return


@pytest.fixture
def clock(monkeypatch):
    """假时钟：``_sleep`` 推进时间，等待循环不用真的等。"""
    now = [0.0]
    monkeypatch.setattr(daily, "time", types.SimpleNamespace(monotonic=lambda: now[0]))
    return now


def daily_nav(game, clock):
    nav = GameNavigator(Config(), game, game, game, settle_s=0)

    def sleep(s):
        clock[0] += s

    nav._sleep = sleep
    nav.save_debug = lambda *a, **k: None
    return nav


def test_page_helpers():
    for name, title in PAGES.items():
        assert page_title(load_items(name)) == title, name
    for name in ("home", "home_badges", "daily_studio_reward", "daily_studio_levelup", "song_select"):
        assert page_title(load_items(name)) is None, name
    ok = reward_ok(load_items("daily_studio_reward"))
    assert ok is not None and math.dist(center(ok), (964, 659)) < 5
    assert reward_ok(load_items("reward")) is not None
    ok = reward_ok(load_items("daily_pass_pt"))  # 获得通行证pt
    assert ok is not None and math.dist(center(ok), (640, 545)) < 5
    assert reward_ok(load_items("daily_missions")) is None
    assert reward_ok(UNKNOWN_CONFIRM) is None
    assert practice_level_up(load_items("daily_studio_levelup"))
    assert not practice_level_up(load_items("daily_studio"))
    assert [t for t, _ in day_tabs(load_items("daily_beginner"))] == [f"{i}天" for i in range(1, 7)]
    assert [t for t, _ in day_tabs(load_items("daily_limited"))] == ["1天"]
    assert day_tabs(load_items("daily_missions")) == []
    for name in ("daily_pass", "daily_studio", "daily_limited", "daily_beginner", "band_confirm", "settings"):
        assert home_icon(load_items(name)), name
    for name in ("home", "home_badges", "daily_missions", "result"):
        assert not home_icon(load_items(name)), name
    ok = gift_confirm(load_items("daily_gifts_confirm"))
    assert ok is not None and math.dist(center(ok), (782, 570)) < 5
    for name in ("daily_gifts_empty", "reward", "daily_studio_reward", "band_confirm"):
        assert gift_confirm(load_items(name)) is None, name
    assert gift_confirm(UNKNOWN_CONFIRM) is None


def test_run_daily_claims_lit_buttons(clock):
    claimable = {"daily_studio", "daily_missions_regular", "daily_pass_missions_regular", "daily_pass"}
    game = DailyGame("home", claimable)
    nav = daily_nav(game, clock)
    assert nav.run_daily([j for j in DAILY_JOBS if j not in OPT_IN_JOBS]) == []
    assert game.state == "home"
    assert not game.claimable
    # 通行证任务先领，任务通行证后领
    assert game.claimed == ["daily_studio", "daily_missions_regular", "daily_pass_missions_regular", "daily_pass"]
    # 灰的一键领取没点过
    for state, p in game.taps:
        if state in ("daily_limited", "daily_beginner", "daily_gifts_empty", "daily_missions"):
            assert math.dist(p, CLAIM_BUTTONS[PAGES[state]]) > 30, (state, p)
    # 四个任务分页、两个通行证任务分页、新手任务六天都切换过
    tabs = [p for s, p in game.taps if s.startswith("daily_missions") and p[0] < 300]
    assert len(tabs) == 4
    beginner = [p for s, p in game.taps if s == "daily_beginner" and p[0] > 1160]
    assert len(beginner) == 6
    # 没点过主界面入口以外的地方（招募、商店、通行证 pt 旁的「+」等）
    home_taps = {p for s, p in game.taps if s == "home"}
    assert home_taps <= {p for p, _ in ROUTES["home"]}
    assert all(math.dist(p, (836, 40)) > 30 for _, p in game.taps)


def test_run_daily_selected_jobs(clock):
    game = DailyGame("song_select", {"daily_gifts_empty"})
    nav = daily_nav(game, clock)
    assert nav.run_daily(["gifts"]) == []
    assert game.claimed == ["daily_gifts_empty"]
    assert [s for s, _ in game.taps][0] == "song_select"  # 先点主页按钮回到主界面
    with pytest.raises(ValueError):
        nav.run_daily(["shop"])


def test_missing_entry_is_skipped(clock):
    """没有限定任务入口（点了停在主界面）时跳过，不算出错；打开的是别的页面也跳过。"""
    routes = {"home": [p for p in ROUTES["home"] if p[1] != "daily_limited"] + [((40, 152), "daily_limited")]}
    game = DailyGame("home", routes=routes)
    nav = daily_nav(game, clock)
    assert nav.run_daily(["limited", "beginner"]) == []
    assert game.state == "home"
    limited = [p for s, p in game.taps if s == "home" and p == (40, 325)]
    assert len(limited) == 2  # 点了没反应再点一次


def test_unknown_confirm_is_not_tapped(clock):
    """领取后出现认不出的确认框：不点，该项记为出错；回不到主界面时整体报错。"""
    game = DailyGame("home", {"daily_gifts_empty"}, popup="unknown_confirm")
    nav = daily_nav(game, clock)
    with pytest.raises(NavigationError, match="主界面"):
        nav.run_daily(["gifts"])
    assert game.state == "unknown_confirm"
    assert all(s != "unknown_confirm" for s, _ in game.taps)


def test_gift_confirm_ok_is_tapped(clock):
    """礼物盒一键领取后弹出「是否一键领取礼物？」：点 OK，不点取消。"""
    routes = {"daily_gifts_confirm": [((782, 570), "reward")]}
    game = DailyGame("home", {"daily_gifts_empty"}, popup="daily_gifts_confirm", routes=routes)
    nav = daily_nav(game, clock)
    assert nav.run_daily(["gifts"]) == []
    assert game.claimed == ["daily_gifts_empty"]
    assert game.state == "home"
    confirm_taps = [p for s, p in game.taps if s == "daily_gifts_confirm"]
    assert len(confirm_taps) == 1 and math.dist(confirm_taps[0], (782, 570)) < 5


def test_go_home_cancels_leftover_gift_confirm(clock):
    """上次停在了「是否一键领取礼物？」：回主界面时点取消。"""
    routes = {"daily_gifts_confirm": [((496, 570), "daily_gifts_empty"), ((782, 570), "reward")]}
    game = DailyGame("daily_gifts_confirm", routes=routes)
    nav = daily_nav(game, clock)
    nav.go_home()
    assert game.state == "home"
    taps = [p for s, p in game.taps if s == "daily_gifts_confirm"]
    assert len(taps) == 1 and math.dist(taps[0], (496, 570)) < 5


def test_failed_job_does_not_stop_others(clock):
    """某一项出错（这里是切换分页后页面不对）时记下来，接着做后面的项目。"""
    routes = {"daily_missions": [((133, 206), "daily_gifts_empty"), ((497, 655), "home")]}
    game = DailyGame("home", {"daily_gifts_empty"}, routes=routes)
    nav = daily_nav(game, clock)
    assert nav.run_daily(["missions", "gifts"]) == ["任务"]
    assert game.claimed == ["daily_gifts_empty"]
    assert game.state == "home"
