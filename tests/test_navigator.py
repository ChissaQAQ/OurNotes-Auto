"""GameNavigator：用画面 OCR 夹具模拟游戏的状态机测试 + 真实 OCR 读取结算页截图。"""

import json
import math
import threading
from concurrent.futures import Future
from pathlib import Path

import cv2
import numpy as np
import pytest

from ournotes_auto.config import Config
from ournotes_auto.nav import navigator
from ournotes_auto.nav.navigator import GameNavigator
from ournotes_auto.nav.screens import LB_RADIO, RESULT_ROW_Y, LbDrink
from ournotes_auto.result_reader import OcrItem
from ournotes_auto.runner import LbExhausted, NavigationError, ScreenFrozen

FIXTURES = Path(__file__).parent / "fixtures"
MODEL_DIR = Path("resource") / "model" / "ocr"


def load_items(name: str) -> list[OcrItem]:
    data = json.loads((FIXTURES / "screens" / f"{name}.json").read_text(encoding="utf-8"))
    return [OcrItem(x, y, w, h, text) for x, y, w, h, text in data]


def make_nav(source, touch, ocr) -> GameNavigator:
    cfg = Config()
    cfg.game.difficulty = "expert"
    nav = GameNavigator(cfg, source, touch, ocr, settle_s=0)
    nav._sleep = lambda s: None
    return nav


# ---------------------------------------------------------------- 状态机（假 OCR）

# 画面 -> [(点击位置, 下一个画面)]，点击落在 40px 内即触发
TRANSITIONS = {
    "song_select": [((1198, 555), "song_select"), ((1164, 660), "band_confirm"), ((966, 660), "song_select")],
    "band_confirm": [((1140, 648), "live_options"), ((56, 38), "song_select"), ((920, 665), "lb_setting")],
    "lb_setting": [((640, 658), "band_confirm")],
    "lb_recover": [((498, 663), "band_confirm")],
    "pause": [((356, 579), "abort_confirm")],
    "abort_confirm": [((781, 572), "band_confirm")],
    "live_options": [((782, 611), "loading"), ((499, 611), "band_confirm")],
    "live_clear": [((640, 650), "achievement")],
    "achievement": [((640, 652), "result")],
    "grade_up": [((641, 660), "reward_claimed")],
    "reward_claimed": [((641, 660), "result")],
    "result": [((1212, 396), "result_timing"), ((1090, 660), "result_reward")],
    "result_timing": [((1212, 396), "result"), ((1090, 660), "result_reward")],
    "result_reward": [((1090, 660), "result_exp")],
    "bond_up": [((640, 660), "bond_story_unlock")],
    "bond_story_unlock": [((640, 570), "result_exp")],
    "result_exp": [((1104, 660), "song_select")],
    # 活动期间：羁绊页只有「下一步」→ 活动故事解锁 → 活动pt达成奖励 → 活动结算页
    "result_exp_next": [((1105, 660), "event_story_unlock")],
    "event_story_unlock": [((640, 569), "event_pt_reward")],
    "event_pt_reward": [((640, 658), "result_event")],
    "result_event": [((1074, 658), "song_select")],
    # 日期变更 → 重新登录
    "date_change": [((640, 572), "title")],
    "title": [((950, 585), "login_bonus")],
    "login_bonus": [((640, 650), "reward")],
    "reward": [((641, 658), "notice")],
    "notice": [((640, 654), "home")],
    "home": [((1081, 650), "live_top")],
    "live_top": [((872, 350), "song_select")],
}

TOTALS = {"perfect": 340, "great": 2, "good": 0, "bad": 0, "miss": 0}
TIMING = {"perfect": (300, 38), "great": (1, 1), "good": (0, 0), "bad": (0, 0), "miss": (0, 0)}


class FakeGame:
    """同时充当截图源、触控和 OCR：截图就是画面名。"""

    size = (1920, 1080)

    def __init__(self, state: str):
        self.state = state
        self.taps: list[tuple[str, tuple[int, int]]] = []

    def grab(self):
        return self.state, 0.0

    def tap(self, x, y):
        p = (x * 1280 / self.size[0], y * 720 / self.size[1])
        self.taps.append((self.state, p))
        for target, nxt in TRANSITIONS.get(self.state, []):
            if math.dist(p, target) < 40:
                self.state = nxt
                return

    def read(self, frame, roi=None):
        return load_items(frame)

    def read_text(self, frame, roi):
        x, y, w, h = roi
        if frame == "band_confirm":
            return "24"  # 难度等级
        if y < 300:
            return "1234567"  # 分数
        if x > 1100:
            return "342"  # 最大连击
        row = min(RESULT_ROW_Y, key=lambda j: abs(RESULT_ROW_Y[j] - (y + h / 2)))
        if frame == "result":
            return str(TOTALS[row])
        assert frame == "result_timing"
        return str(TIMING[row][0 if x < 912 else 1])


def test_band_confirm_and_start_live():
    game = FakeGame("song_select")
    nav = make_nav(game, game, game)
    nav.ensure_band_confirm()
    assert game.state == "band_confirm"
    # 先选难度再确定
    assert [s for s, _ in game.taps] == ["song_select", "song_select"]
    assert math.dist(game.taps[0][1], (1198, 555)) < 5
    assert nav.selected_song()[:3] == ("迷星叫", "expert", 24)
    nav.start_live()
    assert game.state == "loading"


def test_read_result_and_leave():
    game = FakeGame("live_clear")
    nav = make_nav(game, game, game)
    rc = nav.read_result()
    assert rc.counts == TOTALS
    assert rc.fast == {j: v[0] for j, v in TIMING.items()}
    assert rc.slow == {j: v[1] for j, v in TIMING.items()}
    assert rc.combo == 342
    assert rc.score == 1234567
    assert game.state == "result_timing"  # ⇄ 只点了一次
    nav.leave_result()
    assert game.state == "song_select"


def test_bond_up_after_result():
    """结算奖励页之后弹出羁绊等级的 RANK UP、羁绊故事解锁：点 OK、关闭继续，不当成玩家升级（LB 不回满）。"""
    game = FakeGame("bond_up")
    nav = make_nav(game, game, game)
    nav._lb_empty_at = 1.0
    nav.leave_result()
    assert game.state == "song_select"
    assert nav._lb_empty_at == 1.0
    assert [s for s, _ in game.taps][:2] == ["bond_up", "bond_story_unlock"]


def test_event_result_pages():
    """活动期间羁绊页只有「下一步」，后面是活动故事解锁、活动pt达成奖励、活动结算页（再次演出）。"""
    game = FakeGame("result_exp_next")
    nav = make_nav(game, game, game)
    nav.leave_result()
    assert game.state == "song_select"
    assert [s for s, _ in game.taps] == ["result_exp_next", "event_story_unlock", "event_pt_reward", "result_event"]


def test_unknown_result_page_taps_next():
    """没见过的结算页（认不出，右下角有「下一步」）：离开结算页时照样点「下一步」。"""

    class UnknownPage(FakeGame):
        def read(self, frame, roi=None):
            items = super().read(frame, roi)
            if frame == "result_exp_next":
                items = [it for it in items if it.text != "详情" and "绊EXP" not in it.text]
            return items

    game = UnknownPage("result_exp_next")
    nav = make_nav(game, game, game)
    assert navigator.classify(game.read("result_exp_next")) is navigator.Screen.RESULT_OTHER
    nav.leave_result()
    assert game.state == "song_select" and game.taps[0][0] == "result_exp_next"


def test_popup_over_result():
    """达成奖励列表 / GRADE UP（之后还有领取奖励）在结算页出现后才弹出、盖住判定数时，关掉后继续读。"""

    class PopupGame(FakeGame):
        grabs = 0

        def grab(self):
            if self.state == "result":
                self.grabs += 1
                if self.grabs == 2:
                    self.state = popups[0]
            return super().grab()

    for popups in (["achievement"], ["grade_up", "reward_claimed"]):
        game = PopupGame("result")
        nav = make_nav(game, game, game)
        rc = nav.read_result()
        assert rc.counts == TOTALS
        assert rc.fast == {j: v[0] for j, v in TIMING.items()}
        assert [s for s, _ in game.taps] == [*popups, "result"]


def test_choose_random_goes_back_to_song_select():
    game = FakeGame("band_confirm")
    nav = make_nav(game, game, game)
    nav.choose_next_song("current")
    assert game.taps == []
    nav.choose_next_song("random")
    assert [s for s, _ in game.taps] == ["band_confirm", "song_select"]
    assert math.dist(game.taps[-1][1], (966, 660)) < 5


def test_abort_needs_second_confirm():
    """暂停 → 终止 → 二次确认里点按钮行的「终止」（不是同名的标题）。"""
    game = FakeGame("pause")
    nav = make_nav(game, game, game)
    nav.leave_result()
    assert game.state == "band_confirm"
    assert [s for s, _ in game.taps] == ["pause", "abort_confirm"]


class PlayGame(FakeGame):
    """演奏画面（``playing``）：OCR 什么也认不出，右上角有暂停按钮。"""

    def read(self, frame, roi=None):
        return [] if frame == "playing" else super().read(frame, roi)


def play_nav(monkeypatch, state="playing"):
    monkeypatch.setitem(TRANSITIONS, "playing", [((1240, 38), "pause")])
    monkeypatch.setitem(
        TRANSITIONS, "pause", [*TRANSITIONS["pause"], ((640, 579), "retry_confirm"), ((923, 579), "playing")]
    )
    monkeypatch.setitem(TRANSITIONS, "retry_confirm", [((781, 572), "loading"), ((497, 572), "pause")])
    game = PlayGame(state)
    nav = make_nav(game, game, game)
    nav._in_play = lambda frame: frame == "playing"
    nav.save_debug = lambda *a: None
    return game, nav


def test_retry_live(monkeypatch):
    """首音符同步失败后：暂停 → 重试 → 确认重试，确认弹窗关掉（开始加载）就返回，好尽早开始同步。"""
    game, nav = play_nav(monkeypatch)
    nav.retry_live()
    assert game.state == "loading"
    assert [s for s, _ in game.taps] == ["playing", "pause", "retry_confirm"]
    assert tapped(game, (640, 579), radius=2) == ["pause"]
    assert tapped(game, (781, 572), radius=5) == ["retry_confirm"]  # 按钮行的「重试」，不是同名的标题


def test_retry_live_returns_when_play_screen_back(monkeypatch):
    """确认重试后歌曲立即开始：不做 OCR，一看到暂停按钮就返回（首音符马上就到）。"""
    game, nav = play_nav(monkeypatch)
    monkeypatch.setitem(TRANSITIONS, "retry_confirm", [((781, 572), "playing")])
    looks = []
    look = nav.look
    nav.look = lambda *a, **k: looks.append(game.state) or look(*a, **k)
    nav.retry_live()
    assert game.state == "playing"
    assert [s for s, _ in game.taps] == ["playing", "pause", "retry_confirm"]
    assert looks[-1] == "retry_confirm"  # 点完确认后没有再 OCR


def test_retry_live_without_confirm(monkeypatch):
    """点「重试」后没有弹出确认就直接重新开始：认不出的画面持续 3s 后返回。"""
    game, nav = play_nav(monkeypatch)
    monkeypatch.setitem(TRANSITIONS, "pause", [((640, 579), "loading")])
    fake_clock(monkeypatch, nav)
    nav.retry_live()
    assert game.state == "loading"
    assert [s for s, _ in game.taps] == ["playing", "pause"]


def test_retry_live_options(monkeypatch):
    """重试后又弹出演出前选项设置：和开始时一样点「演出」。"""
    game, nav = play_nav(monkeypatch)
    monkeypatch.setitem(TRANSITIONS, "retry_confirm", [((781, 572), "live_options")])
    nav.retry_live()
    assert game.state == "loading"
    assert [s for s, _ in game.taps] == ["playing", "pause", "retry_confirm", "live_options"]


def test_retry_live_not_in_play(monkeypatch):
    """不在演奏画面（看不到暂停按钮，如歌已经放完）：什么都不点。"""
    game, nav = play_nav(monkeypatch, "live_clear")
    with pytest.raises(NavigationError, match="不重试"):
        nav.retry_live()
    assert game.taps == []


def test_retry_live_resumes_when_retry_does_nothing(monkeypatch):
    """点「重试」一直没反应：点「继续」接着放完这首歌（「终止」拿不到演出奖励），再报错。"""
    game, nav = play_nav(monkeypatch)
    monkeypatch.setitem(TRANSITIONS, "pause", [((923, 579), "playing")])
    fake_clock(monkeypatch, nav)
    with pytest.raises(NavigationError, match="重试没有生效"):
        nav.retry_live()
    assert game.state == "playing"
    assert len(tapped(game, (640, 579), radius=2)) >= 2
    assert tapped(game, (356, 579)) == []  # 没点「终止」


def test_retry_live_resumes_when_confirm_does_nothing(monkeypatch):
    """确认重试一直没反应：取消确认弹窗，回到暂停菜单点「继续」，再报错（不点「终止」）。"""
    game, nav = play_nav(monkeypatch)
    monkeypatch.setitem(TRANSITIONS, "retry_confirm", [((497, 572), "pause")])
    fake_clock(monkeypatch, nav)
    with pytest.raises(NavigationError, match="重试没有生效"):
        nav.retry_live()
    assert game.state == "playing"
    assert len(tapped(game, (781, 572), radius=5)) >= 2
    assert tapped(game, (497, 572), radius=5) == ["retry_confirm"]
    assert tapped(game, (356, 579)) == []


def test_stray_retry_confirm_is_cancelled(monkeypatch):
    """别处看到重试确认（演奏已经不管了）：取消，回到暂停菜单按终止走。"""
    game, nav = play_nav(monkeypatch, "retry_confirm")
    nav.leave_result()
    assert game.state == "band_confirm"
    assert [s for s, _ in game.taps] == ["retry_confirm", "pause", "abort_confirm"]
    assert tapped(game, (781, 572), radius=5) == ["abort_confirm"]


class LbGame(FakeGame):
    """乐队确认页 + LB 消耗设置弹窗（按当前选中项画出白色单选按钮）；``held`` 为 0 且消耗不为 0 时
    LIVE START 弹出恢复 LIVE BOOST（``popup``，实机大多直接开始、不消耗）。顶栏和弹窗上的持有数按 ``held``
    改写，``bar`` 指定顶栏的识别结果（模拟读错），``timer`` 为顶栏下一行的恢复倒计时（LB 没满时才有）。"""

    def __init__(self, cost: int, held: int = 24, popup: bool = True, bar: str | None = None, timer: str | None = None):
        super().__init__("band_confirm")
        self.cost = cost
        self.held = held
        self.popup = popup
        self.bar = bar
        self.timer = timer

    def grab(self):
        if self.state != "lb_setting":
            return super().grab()
        w, h = self.size
        img = np.full((h, w, 3), (71, 38, 38), np.uint8)
        x, y = LB_RADIO[self.cost]
        cv2.circle(img, (x * w // 1280, y * h // 720), 12, (255, 255, 255), -1)
        return img, 0.0

    def read(self, frame, roi=None):
        name = "lb_setting" if isinstance(frame, np.ndarray) else frame
        items = load_items(name)
        if name not in ("band_confirm", "lb_setting"):
            return items
        bar = f"{self.held}/10" if self.bar is None else self.bar
        texts = {"10": bar, "99": f"{self.held}/99"}
        items = [OcrItem(it.x, it.y, it.w, it.h, texts.get(it.text.rpartition("/")[2], it.text)) for it in items]
        if name == "band_confirm" and self.timer is not None:
            items.append(OcrItem(1037, 46, 73, 28, self.timer))
        return items

    def tap(self, x, y):
        p = (x * 1280 / self.size[0], y * 720 / self.size[1])
        if self.state == "lb_setting":
            self.cost = next((c for c, q in LB_RADIO.items() if math.dist(p, q) < 20), self.cost)
        if self.state == "band_confirm" and math.dist(p, (1140, 648)) < 40 and self.cost and not self.held:
            if self.popup:
                self.taps.append((self.state, p))
                self.state = "lb_recover"
                return
        super().tap(x, y)


def tapped(game, point, radius=20):
    return [s for s, p in game.taps if math.dist(p, point) < radius]


class RefillGame(LbGame):
    """在 LbGame 上加消耗设置弹窗的「恢复」→ 恢复LIVE BOOST（左边按 ``tab`` 画出选中的分页）→ 确认 → 已恢复。
    ``owned`` 为小型（+1）和普通（+10）LIVE BOOST饮料的持有数（为 0 的那行不显示），每点一次「+」多选一瓶；
    确认弹窗上的恢复数按 ``amount``（默认就是选中的）改写，``preview`` 改写恢复预览，确认后持有数加上选中的。
    ``drop`` 里的 before / after 模拟预览左边 / 右边的数字漏读（实机持有 1 时白色的「1」读不出来）。"""

    DRINK_Y = {1: 212, 10: 354}
    TABS = {"道具": (85, 146), "星钻": (72, 217), "观看广告": (72, 285)}

    def __init__(self, cost: int, held: int, owned=(24, 5), tab="道具", amount=None, preview=None, drop=(), **kw):
        super().__init__(cost, held, popup=False, **kw)
        self.owned = dict(zip((1, 10), owned))
        self.chosen = {1: 0, 10: 0}
        self.tab = tab
        self.amount = amount
        self.preview = preview
        self.drop = drop

    def gain(self) -> int:
        return sum(lb * n for lb, n in self.chosen.items())

    def grab(self):
        if self.state != "lb_recover_drinks":
            return super().grab()
        w, h = self.size
        img = np.full((h, w, 3), (71, 38, 38), np.uint8)
        for name, (x, y) in self.TABS.items():
            color = (176, 162, 84) if name == self.tab else (139, 84, 70)
            cv2.rectangle(img, ((x - 40) * w // 1280, (y - 25) * h // 720), ((x + 40) * w // 1280, (y + 25) * h // 720), color, -1)
        return img, 0.0

    def read(self, frame, roi=None):
        if isinstance(frame, np.ndarray):
            frame = self.state
        if frame == "lb_recover_drinks":
            items = []
            for it in load_items(frame):
                lb = 1 if it.y < 250 else 10
                if it.text.endswith("饮料") or "/" in it.text or it.text == "X":
                    if not self.owned[lb] and not self.chosen[lb]:
                        continue  # 没有的饮料不显示
                    if "/" in it.text:
                        it = OcrItem(it.x, it.y, it.w, it.h, f"{self.chosen[lb]}/{self.owned[lb]}")
                elif it.text == "3":
                    side = "before" if it.x < 760 else "after"
                    if side in self.drop:
                        continue
                    after = self.held + self.gain() if self.preview is None else self.preview
                    it = OcrItem(it.x, it.y, it.w, it.h, str(self.held if side == "before" else after))
                items.append(it)
            return items
        if frame == "lb_recover_confirm":
            n = self.gain() if self.amount is None else self.amount
            return [OcrItem(it.x, it.y, it.w, it.h, it.text.replace("恢复1点", f"恢复{n}点")) for it in load_items(frame)]
        return super().read(frame, roi)

    def tap(self, x, y):
        p = (x * 1280 / self.size[0], y * 720 / self.size[1])
        here = self.state

        def near(q, r=20):
            return math.dist(p, q) < r

        nxt = None
        if here == "lb_setting" and near((940, 575)):
            nxt = "lb_recover_drinks"
        elif here == "lb_recover_drinks":
            for lb, y0 in self.DRINK_Y.items():
                if near((910, y0)) and self.chosen[lb] < self.owned[lb]:
                    self.chosen[lb] += 1
            self.tab = next((t for t, q in self.TABS.items() if near(q, 40)), self.tab)
            if near((782, 663), 40) and self.gain():
                nxt = "lb_recover_confirm"
            elif near((498, 663), 40):
                self.chosen = {1: 0, 10: 0}
                nxt = "lb_setting"
        elif here == "lb_recover_confirm":
            if near((782, 572), 40):
                self.held += self.gain()
                self.owned = {lb: self.owned[lb] - n for lb, n in self.chosen.items()}
                self.chosen = {1: 0, 10: 0}
                nxt = "lb_recovered"
            elif near((497, 572), 40):
                nxt = "lb_recover_drinks"
        elif here == "lb_recovered" and near((640, 570), 40):
            nxt = "lb_setting"
        if here in ("lb_recover_drinks", "lb_recover_confirm", "lb_recovered") or nxt:
            self.taps.append((here, p))
            self.state = nxt or here
            return
        super().tap(x, y)


def refill_nav(game, limit=0, cost=3):
    cfg = Config()
    cfg.game.difficulty = "expert"
    cfg.game.lb_cost = cost
    cfg.game.lb_refill = True
    cfg.game.lb_refill_limit = limit
    nav = GameNavigator(cfg, game, game, game, settle_s=0)
    nav._sleep = lambda s: None
    return nav


def never_paid(game):
    """星钻、观看广告分页一次都没点过。"""
    return not tapped(game, RefillGame.TABS["星钻"], 40) and not tapped(game, RefillGame.TABS["观看广告"], 40)


def test_plan_refill():
    def drinks(*owned):
        return [LbDrink("", lb, 0, n, y) for (lb, n), y in zip(owned, (212, 354))]

    assert navigator.plan_refill(drinks((1, 24), (10, 5)), 3, None) == [3, 0]  # 先用小的
    assert navigator.plan_refill(drinks((1, 2), (10, 5)), 3, None) == [2, 1]  # 小的不够再用大的
    assert navigator.plan_refill(drinks((10, 5)), 3, None) == [1]
    assert navigator.plan_refill(drinks((10, 5)), 3, 9) == [0]  # 额度不够一瓶大的
    assert navigator.plan_refill(drinks((1, 24), (10, 5)), 3, 2) == [2, 0]  # 只补到额度
    assert navigator.plan_refill(drinks((1, 0), (10, 0)), 3, None) == [0, 0]
    assert navigator.plan_refill([LbDrink("", 1, 2, 3, 212)], 3, None) == [1]  # 已经选了 2 瓶


def test_refill_when_short():
    """持有少于每局消耗时从消耗设置进去，在「道具」页用小型饮料补够，再按配置的消耗开始。"""
    game = RefillGame(cost=3, held=1)
    nav = refill_nav(game)
    nav.start_live()
    assert game.state == "loading" and game.cost == 3
    assert game.held == 3 and game.owned == {1: 22, 10: 5}
    assert tapped(game, (910, 212)) == ["lb_recover_drinks"] * 2
    assert tapped(game, (782, 572)) == ["lb_recover_confirm"]  # 确认恢复
    assert tapped(game, (640, 570)) == ["lb_recovered"]
    assert never_paid(game)
    assert nav._refilled == 2 and not nav._refill_out
    # 持有够了不再打开弹窗
    game.state = "band_confirm"
    game.taps.clear()
    nav.start_live()
    assert [s for s, _ in game.taps] == ["band_confirm", "live_options"]


def test_refill_keeps_going_until_limit():
    game = RefillGame(cost=3, held=0)
    nav = refill_nav(game, limit=5)
    nav.start_live()
    assert game.held == 3 and nav._refill_left == 2
    game.state, game.held = "band_confirm", 0  # 打完一局用掉了
    nav.start_live()
    assert game.held == 2 and nav._refilled == 5 and nav._refill_out  # 只补到上限
    assert game.owned == {1: 19, 10: 5}
    game.state, game.held = "band_confirm", 0
    game.taps.clear()
    nav.start_live()
    assert game.state == "loading" and not tapped(game, (920, 665))  # 到上限后不再打开消耗设置
    assert never_paid(game)


def test_refill_unlimited_uses_big_drinks_after_small():
    game = RefillGame(cost=3, held=0, owned=(1, 2))
    nav = refill_nav(game)
    nav.start_live()
    assert game.held == 11 and game.owned == {1: 0, 10: 1}
    for _ in range(3):  # 11 → 8 → 5 → 2，持有 2 时再补一瓶大的
        game.state, game.held = "band_confirm", game.held - 3
        nav.start_live()
    assert game.held == 12 and game.owned == {1: 0, 10: 0}
    game.state, game.held = "band_confirm", 0
    nav.start_live()
    assert nav._refill_out and game.state == "loading"  # 道具用完，照原来的方式继续
    assert tapped(game, (498, 663)) == ["lb_recover_drinks"]  # 没有饮料时点取消
    assert never_paid(game)


def test_refill_out_then_stop():
    """清体力：道具里没有饮料时点取消，持有 0 就停下（LbExhausted），之后不再打开恢复。"""
    game = RefillGame(cost=3, held=0, owned=(0, 0))
    nav = refill_nav(game)
    with pytest.raises(LbExhausted):
        nav.start_live("stop")
    assert nav._refill_out and game.state == "band_confirm"
    assert tapped(game, (498, 663)) == ["lb_recover_drinks"]
    assert not tapped(game, (782, 663), 100)  # 没点 OK


@pytest.mark.parametrize("tab", ["星钻", "观看广告"])
def test_refill_never_on_other_tabs(tab):
    game = RefillGame(cost=3, held=0, tab=tab)
    nav = refill_nav(game)
    with pytest.raises(LbExhausted):
        nav.start_live("stop")
    assert game.held == 0 and nav._refill_out
    assert not [s for s, p in game.taps if s == "lb_recover_drinks" and not math.dist(p, (498, 663)) < 20]  # 只点了取消
    assert never_paid(game)


def test_refill_cancels_on_preview_mismatch():
    game = RefillGame(cost=3, held=0, preview=13)
    nav = refill_nav(game)
    nav.start_live()
    assert game.held == 0 and nav._refill_out and game.state == "loading"
    assert not tapped(game, (782, 663), 40)  # 预览对不上不点 OK
    assert game.chosen == {1: 0, 10: 0}  # 取消后选的都作废了


def test_refill_preview_missing_held_digit():
    """实机：持有 1 时预览「1 ▶ 3」只读到 3，用消耗设置弹窗上的持有数核对。"""
    game = RefillGame(cost=3, held=1, drop=("before",))
    nav = refill_nav(game)
    nav.start_live()
    assert game.held == 3 and nav._refilled == 2 and not nav._refill_out
    assert never_paid(game)


@pytest.mark.parametrize("drop, preview", [(("before",), 4), (("after",), None)])
def test_refill_cancels_on_unreadable_preview(drop, preview):
    game = RefillGame(cost=3, held=1, drop=drop, preview=preview)
    nav = refill_nav(game)
    nav.start_live()
    assert game.held == 1 and nav._refill_out and game.state == "loading"
    assert not tapped(game, (782, 663), 40)


def test_refill_cancels_on_confirm_mismatch():
    game = RefillGame(cost=3, held=0, amount=30)
    nav = refill_nav(game)
    nav.start_live()
    assert game.held == 0 and nav._refill_out and game.state == "loading"
    assert tapped(game, (497, 572)) == ["lb_recover_confirm"]  # 确认弹窗点取消
    assert not tapped(game, (782, 572))
    assert never_paid(game)


def test_refill_after_live_start_popup():
    """点 LIVE START 弹出恢复窗口（顶栏读多了）：取消后从消耗设置进去用道具补充，再开始。"""
    game = RefillGame(cost=3, held=0, bar="5/10")
    game.popup = True
    nav = refill_nav(game)
    nav.start_live()
    assert game.state == "loading" and game.held == 3 and game.cost == 3
    assert tapped(game, (498, 663))[0] == "lb_recover"
    assert never_paid(game)


def test_refill_off_never_recovers():
    game = RefillGame(cost=3, held=0)
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 3
    with pytest.raises(LbExhausted):
        nav.start_live("stop")
    assert not tapped(game, (940, 575), 60) and game.held == 0


def test_set_lb_cost_once():
    game = LbGame(cost=0)
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 3
    nav.start_live()
    assert game.cost == 3 and game.state == "loading"
    assert [s for s, _ in game.taps] == ["band_confirm", "lb_setting", "lb_setting", "band_confirm", "live_options"]
    # 设置过一次后不再打开弹窗
    game.state = "band_confirm"
    game.taps.clear()
    nav.start_live()
    assert [s for s, _ in game.taps] == ["band_confirm", "live_options"]


def test_lb_recover_is_cancelled_and_falls_back_to_zero():
    game = LbGame(cost=3, held=0)
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 3
    nav.start_live()
    assert game.cost == 0 and game.state == "loading"
    assert tapped(game, (498, 663)) == ["lb_recover"]  # 取消
    assert not any(math.dist(p, (782, 663)) < 100 for s, p in game.taps if s == "lb_recover")  # 绝不点 OK
    # 之后一段时间内直接消耗 0，不再反复弹恢复窗口
    game.state = "band_confirm"
    game.taps.clear()
    nav.start_live()
    assert game.cost == 0 and game.state == "loading" and "lb_recover" not in [s for s, _ in game.taps]


def test_lb_retries_configured_cost_later(monkeypatch):
    game = LbGame(cost=3, held=0)
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 3
    now = fake_clock(monkeypatch, nav)
    nav.start_live()
    assert game.cost == 0
    # 30 分钟后（LB 已自然恢复）再按配置改回 3
    now[0] += navigator.LB_EMPTY_RETRY_S
    game.state = "band_confirm"
    game.held = 1
    nav.start_live()
    assert game.cost == 3 and game.state == "loading"


def test_rank_up_retries_configured_cost():
    game = LbGame(cost=3, held=0)
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 3
    nav.start_live()
    assert game.cost == 0
    # 玩家升级时 LB 回满
    game.state = "rank_up"
    game.held = 24
    nav._common_step(*nav.look())
    game.state = "band_confirm"
    nav.start_live()
    assert game.cost == 3 and game.state == "loading"


def test_lb_recover_stops_when_asked():
    game = LbGame(cost=3, held=0, bar="")  # 顶栏没读出来，靠恢复窗口兜底
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 3
    with pytest.raises(LbExhausted):
        nav.start_live("stop")
    assert game.state == "band_confirm" and game.cost == 3
    assert tapped(game, (498, 663)) == ["lb_recover"]


def test_lb_stop_checks_held_before_start():
    """实机持有 0 时点 LIVE START 不弹恢复窗口、直接开始（不消耗），所以清体力要先看顶栏的持有数。"""
    game = LbGame(cost=3, held=0, popup=False)
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 3
    with pytest.raises(LbExhausted):
        nav.start_live("stop")
    assert game.state == "band_confirm" and game.cost == 3
    assert not tapped(game, (1140, 648))  # 没点 LIVE START
    assert tapped(game, (920, 665)) == ["band_confirm"]  # 打开消耗设置弹窗核对过
    # 不清体力时照常开始
    game.taps.clear()
    nav.start_live()
    assert game.state == "loading"


def test_lb_stop_rechecks_misread_bar():
    game = LbGame(cost=3, held=4, bar="0/10")  # 顶栏把 10/10 漏读成 0/10 之类
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 3
    nav.start_live("stop")
    assert game.state == "loading"
    assert tapped(game, (920, 665)) == ["band_confirm"]  # 核对时已设好消耗，不再打开第二次


def test_lb_status_reads_bar_and_timer():
    """挂机等 LB 恢复：读顶栏的持有数和恢复倒计时，不点任何东西；``check`` 时打开消耗设置弹窗核对（顺便设好消耗）。"""
    game = LbGame(cost=0, held=2, timer="©15:24")
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 3
    assert nav.lb_status() == (2, 924)
    assert not game.taps
    game.bar = "3/10"  # 顶栏读多了
    assert nav.lb_status() == (3, 924)
    assert nav.lb_status(check=True) == (2, 924)
    assert game.cost == 3 and game.state == "band_confirm"
    assert tapped(game, (920, 665)) == ["band_confirm"]
    assert not tapped(game, (1140, 648))  # 没点 LIVE START
    assert not tapped(game, (920, 575), radius=60)  # 弹窗里的「恢复」绝不点


def test_lb_status_falls_back_to_popup():
    game = LbGame(cost=3, held=5, bar="")  # 顶栏漏读
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 3
    assert nav.lb_status() == (5, None)
    assert tapped(game, (920, 665)) == ["band_confirm"] and game.state == "band_confirm"
    game.state = "song_select"
    nav.save_debug = lambda *a: None
    with pytest.raises(NavigationError, match="不在乐队确认页"):
        nav.lb_status()


def test_lb_recover_cancelled_while_navigating():
    game = FakeGame("lb_recover")
    nav = make_nav(game, game, game)
    nav.ensure_band_confirm()
    assert game.state == "band_confirm" and tapped(game, (498, 663)) == ["lb_recover"]


def test_invalid_lb_cost():
    cfg = Config()
    cfg.game.lb_cost = 4
    with pytest.raises(ValueError):
        GameNavigator(cfg, None, None, None)


def test_jackets_loaded_in_background():
    """封面在后台加载时，第一次用到才等它；等的时候可以停止，加载出错在用到的地方抛出。"""
    future = Future()
    nav = GameNavigator(Config(), None, None, None, jackets=future)
    threading.Timer(0.05, future.set_result, ["matcher"]).start()
    assert nav.jackets == "matcher" and nav.jackets == "matcher"
    stop = threading.Event()
    nav = GameNavigator(Config(), None, None, None, stop=stop, jackets=Future())
    stop.set()
    with pytest.raises(NavigationError, match="已停止"):
        nav.jackets
    future = Future()
    future.set_exception(OSError("disk full"))
    with pytest.raises(OSError):
        GameNavigator(Config(), None, None, None, jackets=future).jackets
    assert GameNavigator(Config(), None, None, None).jackets is None


def test_relogin_after_date_change():
    """日期变更 → 标题 → 登录奖励 → 获得奖励 → 公告 → 主界面 → 演出首页 → 乐曲选择 → 乐队确认。"""
    game = FakeGame("date_change")
    nav = make_nav(game, game, game)
    nav.ensure_band_confirm()
    assert game.state == "band_confirm"
    assert [s for s, _ in game.taps] == [
        "date_change",
        "title",
        "login_bonus",
        "reward",
        "notice",
        "home",
        "live_top",
        "song_select",
        "song_select",
    ]


def test_read_result_gives_up_on_date_change():
    game = FakeGame("date_change")
    nav = make_nav(game, game, game)
    nav.save_debug = lambda *a: None
    with pytest.raises(NavigationError, match="标题画面"):
        nav.read_result()
    assert game.taps == []


def test_ensure_in_game_from_title():
    """启动游戏：标题 → 登录奖励 → 获得奖励 → 公告 → 停在主界面，不再往演出里点。"""
    game = FakeGame("title")
    nav = make_nav(game, game, game)
    assert nav.ensure_in_game() == navigator.Screen.HOME
    assert [s for s, _ in game.taps] == ["title", "login_bonus", "reward", "notice"]


def test_ensure_in_game_already_in_game():
    game = FakeGame("band_confirm")
    nav = make_nav(game, game, game)
    assert nav.ensure_in_game() == navigator.Screen.BAND_CONFIRM
    assert game.taps == []


def test_ensure_in_game_closes_notify_prompt(monkeypatch):
    """标题画面上的「开启消息通知」：点右上角的 ⓧ 关掉，不点「去开启」。"""
    monkeypatch.setitem(TRANSITIONS, "title_notify", [((822, 171), "title")])
    game = FakeGame("title_notify")
    nav = make_nav(game, game, game)
    assert nav.ensure_in_game() == navigator.Screen.HOME
    assert tapped(game, (822, 171), radius=2) == ["title_notify"]
    assert [s for s, _ in game.taps] == ["title_notify", "title", "login_bonus", "reward", "notice"]


def test_title_without_tap_to_start_fails(monkeypatch):
    """标题画面一直没出现 TAP TO START（被认不出的弹窗挡住）：报错，不再一直等。"""
    game = FakeGame("title_loading")
    nav = make_nav(game, game, game)
    nav.save_debug = lambda *a: None
    now = fake_clock(monkeypatch, nav)
    start = now[0]
    with pytest.raises(NavigationError, match="没出现 TAP TO START"):
        nav.ensure_in_game()
    assert game.taps == [] and 120 < now[0] - start < 125


def test_connect_error_returns_to_title(monkeypatch):
    """连接失败：等一会儿再点「返回标题画面」重新登录；进到主界面后重新计数。"""
    monkeypatch.setitem(TRANSITIONS, "connect_error", [((640, 572), "title")])
    game = FakeGame("connect_error")
    nav = make_nav(game, game, game)
    now = fake_clock(monkeypatch, nav)
    start = now[0]
    assert nav.ensure_in_game() == navigator.Screen.HOME
    assert [s for s, _ in game.taps] == ["connect_error", "title", "login_bonus", "reward", "notice"]
    assert now[0] - start >= navigator.CONNECT_RETRY_WAIT_S
    assert nav._connect_errors == 0


def test_connect_error_gives_up(monkeypatch):
    """一直连不上：重试几次后报错，不无限重新登录。"""
    monkeypatch.setitem(TRANSITIONS, "connect_error", [((640, 572), "title")])
    monkeypatch.setitem(TRANSITIONS, "title", [((950, 585), "connect_error")])
    game = FakeGame("connect_error")
    nav = make_nav(game, game, game)
    nav.save_debug = lambda *a: None
    fake_clock(monkeypatch, nav)
    with pytest.raises(NavigationError, match="连接失败"):
        nav.ensure_in_game()
    assert len(tapped(game, (640, 572))) == navigator.MAX_CONNECT_RETRIES


def test_ensure_in_game_times_out(monkeypatch):
    game = FakeGame("loading")
    nav = make_nav(game, game, game)
    nav.save_debug = lambda *a: None
    fake_clock(monkeypatch, nav)
    with pytest.raises(NavigationError, match="未能进入游戏"):
        nav.ensure_in_game(timeout_s=30)
    assert game.taps == []


class StuckTitle(FakeGame):
    """标题画面点击无反应，直到重启游戏；重启后先看到一帧还在加载的标题画面。"""

    def __init__(self):
        super().__init__("title")
        self.restarts = 0

    def restart(self):
        self.restarts += 1
        self.state = "title_loading"

    def grab(self):
        frame = super().grab()
        if self.state == "title_loading":
            self.state = "title"
        return frame

    def tap(self, x, y):
        if self.state == "title" and not self.restarts:
            self.taps.append((self.state, (x * 1280 / self.size[0], y * 720 / self.size[1])))
            return
        super().tap(x, y)


def fake_clock(monkeypatch, nav):
    """导航里的等待改为推进假时钟（每次再加 0.5s 模拟截图与识别）。"""
    now = [1000.0]
    monkeypatch.setattr(navigator.time, "monotonic", lambda: now[0])

    def sleep(s):
        now[0] += s + 0.5

    nav._sleep = sleep
    return now


def test_stuck_title_restarts_game(monkeypatch):
    game = StuckTitle()
    nav = make_nav(game, game, game)
    nav.restart_app = game.restart
    nav.settle_s = 1.0
    fake_clock(monkeypatch, nav)
    nav.ensure_band_confirm()
    assert game.state == "band_confirm" and game.restarts == 1
    title_taps = tapped(game, (950, 585))
    # 每 3s 点一次，点了 60s 没反应才重启；重启后第一次点击就成功
    assert all(s == "title" for s in title_taps) and 18 <= len(title_taps) <= 24


def test_stuck_title_without_restart_fails(monkeypatch):
    game = StuckTitle()
    nav = make_nav(game, game, game)
    nav.save_debug = lambda *a: None
    nav.settle_s = 1.0
    fake_clock(monkeypatch, nav)
    with pytest.raises(NavigationError, match="点击无反应"):
        nav.ensure_band_confirm()
    assert game.restarts == 0


class CrashedGame(StuckTitle):
    """游戏闪退，停在认不出的画面（桌面），直到重新启动。"""

    def __init__(self):
        super().__init__()
        self.state = "loading"
        self.checks: list[float] = []

    def running(self):
        self.checks.append(navigator.time.monotonic())
        return self.restarts > 0


def test_crashed_game_restarts(monkeypatch):
    game = CrashedGame()
    nav = make_nav(game, game, game)
    nav.restart_app = game.restart
    nav.app_running = game.running
    now = fake_clock(monkeypatch, nav)
    start = now[0]
    nav.ensure_band_confirm()
    assert game.state == "band_confirm" and game.restarts == 1
    # 认不出画面 30s 后才检查进程（加载画面本来就会持续一阵）
    assert len(game.checks) == 1 and game.checks[0] - start >= navigator.APP_CHECK_S


def test_slow_loading_checks_process_periodically(monkeypatch):
    game = CrashedGame()
    game.restarts = 1  # 进程在，只是一直在加载
    nav = make_nav(game, game, game)
    nav.save_debug = lambda *a: None
    nav.app_running = game.running
    fake_clock(monkeypatch, nav)
    with pytest.raises(NavigationError, match="未能进入乐队确认页"):
        nav.ensure_band_confirm(timeout_s=100)
    assert len(game.checks) == 3 and game.restarts == 1


def test_crashed_game_without_restart_fails(monkeypatch):
    game = CrashedGame()
    nav = make_nav(game, game, game)
    nav.save_debug = lambda *a: None
    nav.app_running = game.running
    fake_clock(monkeypatch, nav)
    with pytest.raises(NavigationError, match="没有在运行"):
        nav.ensure_band_confirm()
    assert game.restarts == 0


class FrozenGame(FakeGame):
    """一直停在 ``state`` 上（点了也没反应）。截图是真图像，``moving`` 时每帧都不一样；识别结果仍按画面名。"""

    def __init__(self, state, moving=False, running=True, drop=()):
        super().__init__(state)
        self.moving, self.running, self.drop = moving, running, drop
        self.grabs = 0

    def grab(self):
        self.grabs += 1
        return np.full((90, 160, 3), self.grabs % 2 * 100 if self.moving else 40, np.uint8), 0.0

    def tap(self, x, y):
        self.taps.append((self.state, (x, y)))

    def read(self, frame, roi=None):
        return [it for it in load_items(self.state) if it.text not in self.drop]


def frozen_nav(monkeypatch, game):
    nav = make_nav(game, game, game)
    nav.save_debug = lambda *a: None
    nav.app_running = lambda: game.running
    return nav, fake_clock(monkeypatch, nav)


@pytest.mark.parametrize(
    "state, drop",
    [
        ("intro_card", ()),  # 认不出的画面
        ("result_exp_next", ("详情", "下一步")),  # 认不出、也没有「下一步」的结算页（当成还在播动画）
    ],
)
def test_frozen_screen_stops_early(monkeypatch, state, drop):
    """画面一直不动、游戏还在运行：FROZEN_S 后就抛 ScreenFrozen，不等到超时。"""
    game = FrozenGame(state, drop=drop)
    nav, now = frozen_nav(monkeypatch, game)
    start = now[0]
    with pytest.raises(ScreenFrozen, match="没有变化"):
        nav.ensure_in_game()
    assert navigator.FROZEN_S <= now[0] - start < navigator.FROZEN_S + 5
    assert game.taps == []


@pytest.mark.parametrize("moving, running, error", [(True, True, "未能进入游戏"), (False, False, "没有在运行")])
def test_moving_or_crashed_screen_is_not_frozen(monkeypatch, moving, running, error):
    """画面在动（加载动画）时照常等到超时；游戏不在运行时按闪退处理。"""
    game = FrozenGame("intro_card", moving=moving, running=running)
    nav, _ = frozen_nav(monkeypatch, game)
    with pytest.raises(NavigationError, match=error) as e:
        nav.ensure_in_game(timeout_s=150)
    assert not isinstance(e.value, ScreenFrozen)


def test_read_result_waits_through_still_screen(monkeypatch):
    """等结算时歌可能还在放（同步失败），画面不动也不算卡住。"""
    game = FrozenGame("intro_card")
    nav, _ = frozen_nav(monkeypatch, game)
    with pytest.raises(NavigationError, match="没有出现结算页") as e:
        nav.read_result(timeout_s=150)
    assert not isinstance(e.value, ScreenFrozen)


# ---------------------------------------------------------------- 真实 OCR（需要模型）

needs_ocr = pytest.mark.skipif(
    not all((MODEL_DIR / f).is_file() for f in ("det.onnx", "rec.onnx", "keys.txt")),
    reason="缺少 OCR 模型 resource/model/ocr",
)


@pytest.fixture(scope="module")
def ocr():
    from ournotes_auto.nav.ocr import MaaOcr

    return MaaOcr("resource")


class ImageSource:
    """结算页截图；点击 ⇄ 时切换到 FAST/SLOW 那张。"""

    def __init__(self, totals: str, timing: str, scale: float = 1.0):
        def load(name):
            img = cv2.imdecode(np.fromfile(next((FIXTURES / "results").glob(f"{name}.*")), np.uint8), cv2.IMREAD_COLOR)
            return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale != 1 else img

        self.images = [load(totals), load(timing)]
        self.index = 0
        h, w = self.images[0].shape[:2]
        self.size = (w, h)

    def grab(self):
        return self.images[self.index], 0.0

    def tap(self, x, y):
        w, h = self.size
        if math.dist((x * 1280 / w, y * 720 / h), (1212, 396)) < 30:
            self.index ^= 1


@needs_ocr
@pytest.mark.parametrize(
    "totals, timing, perfect, fast, slow, combo, score",
    [
        ("totals_ap", "timing_ap", 342, 84, 2, 342, 1111155),
        ("totals_expert", "timing_expert", 768, 333, 82, 768, 1400102),
    ],
)
def test_read_result_real_ocr(ocr, totals, timing, perfect, fast, slow, combo, score):
    src = ImageSource(totals, timing)
    nav = make_nav(src, src, ocr)
    rc = nav.read_result()
    assert rc.counts == {"perfect": perfect, "great": 0, "good": 0, "bad": 0, "miss": 0}
    assert rc.fast["perfect"] == fast and rc.slow["perfect"] == slow
    assert all(rc.fast[j] == rc.slow[j] == 0 for j in ("great", "good", "bad"))
    assert rc.combo == combo
    assert rc.score == score


@needs_ocr
def test_read_result_four_digits(ocr):
    """1188 在默认框里读成 T188（PNG 原图才复现）：知道谱面音符数时换框纠正。"""
    src = ImageSource("totals_1188", "timing_1188")
    rc = make_nav(src, src, ocr).read_result(expected_total=1188)
    assert rc.counts == {"perfect": 1188, "great": 0, "good": 0, "bad": 0, "miss": 0}
    assert (rc.fast["perfect"], rc.slow["perfect"]) == (72, 675)
    assert rc.score == 1330139  # 整屏识别会读成 330139
    assert rc.combo == 1188  # 整屏识别会读成 188


@needs_ocr
def test_read_result_real_ocr_scaled(ocr):
    """设备分辨率与 1280x720 不同时 ROI 要跟着缩放。"""
    src = ImageSource("totals_expert", "timing_expert", scale=1.5)
    rc = make_nav(src, src, ocr).read_result()
    assert rc.counts["perfect"] == 768
    assert (rc.fast["perfect"], rc.slow["perfect"]) == (333, 82)
    assert rc.score == 1400102
    assert rc.combo == 768
