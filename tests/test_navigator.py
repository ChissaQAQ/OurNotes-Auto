"""GameNavigator：用画面 OCR 夹具模拟游戏的状态机测试 + 真实 OCR 读取结算页截图。"""

import json
import math
import threading
from concurrent.futures import Future
from pathlib import Path

import cv2
import numpy as np
import pytest

from ournotes_auto import sources
from ournotes_auto.config import Config
from ournotes_auto.nav import navigator
from ournotes_auto.nav.lang import localize
from ournotes_auto.nav.navigator import GameNavigator
from ournotes_auto.nav.screens import (
    CLEAR_MARK_ROI,
    CP_RADIO,
    LB_RADIO,
    RESULT_ROW_Y,
    SELECT_TITLE_ROI,
    LbDrink,
    in_roi,
)
from ournotes_auto.nav.song_select import BTN_DIFFICULTY
from ournotes_auto.result_reader import OcrItem
from ournotes_auto.runner import (
    CpExhausted,
    GameUpdateRequired,
    LbExhausted,
    NavigationError,
    ScreenFrozen,
    ServerMaintenance,
)

FIXTURES = Path(__file__).parent / "fixtures"
MODEL_DIR = Path("resource") / "model" / "ocr"


def load_items(name: str) -> list[OcrItem]:
    data = json.loads((FIXTURES / "screens" / f"{name}.json").read_text(encoding="utf-8"))
    return [OcrItem(x, y, w, h, localize(text)) for x, y, w, h, text in data]


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
    "new_song": [((640, 627), "home")],
    "band_rank_up": [((640, 660), "result_exp")],
    "daily_pass_pt": [((640, 546), "home")],  # 当作没见过、只有 OK 的弹窗
    "birthday": [((1213, 36), "home")],  # 右上角「跳过」
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


def test_band_rank_up_after_result():
    """结算羁绊页之后弹出乐队RANK 的 RANK UP：点 OK 继续，不当成玩家升级。"""
    game = FakeGame("band_rank_up")
    nav = make_nav(game, game, game)
    nav._lb_empty_at = 1.0
    nav.leave_result()
    assert game.state == "song_select"
    assert nav._lb_empty_at == 1.0
    assert [s for s, _ in game.taps][:2] == ["band_rank_up", "result_exp"]


def test_unknown_ok_popup_is_dismissed():
    """认不出、只有 OK 的弹窗：点 OK 继续，存一张截图。"""
    game = FakeGame("daily_pass_pt")
    nav = make_nav(game, game, game)
    saved = []
    nav.save_debug = lambda name, *a: saved.append(name)
    nav.ensure_band_confirm()
    assert game.state == "band_confirm"
    assert [s for s, _ in game.taps][:2] == ["daily_pass_pt", "home"]
    assert saved == ["ok_popup"]


def test_birthday_is_skipped():
    """重新登录后的生日演出（一直循环播放）：点右上角「跳过」。"""
    game = FakeGame("birthday")
    nav = make_nav(game, game, game)
    assert nav.ensure_in_game() == navigator.Screen.HOME
    assert tapped(game, (1213, 36), radius=2) == ["birthday"]  # 按 OCR 到的按钮位置点


class BirthdayGame(FakeGame):
    """截图是真图像：故事播放画面（OCR 什么也认不出）右上角画出菜单按钮。"""

    def grab(self):
        img = np.full((720, 1280, 3), 40, np.uint8)
        if self.state == "story_player":
            cv2.circle(img, (1201, 101), 30, (151, 89, 73), -1)
            for y in (91, 101, 111):
                img[y - 1 : y + 2, 1188:1215] = 255
        return img, 0.0

    def read(self, frame, roi=None):
        return [] if self.state == "story_player" else load_items(self.state)


def test_birthday_story_is_skipped(monkeypatch):
    """生日演出跳过后接着放生日故事：打开右上角菜单 → SKIP → 确认跳过。"""
    monkeypatch.setitem(TRANSITIONS, "birthday", [((1213, 36), "story_player")])
    monkeypatch.setitem(TRANSITIONS, "story_player", [((1201, 101), "story_player_menu")])
    monkeypatch.setitem(TRANSITIONS, "story_player_menu", [((1201, 168), "story_skip")])
    monkeypatch.setitem(TRANSITIONS, "story_skip", [((756, 532), "home")])
    game = BirthdayGame("birthday")
    nav = make_nav(game, game, game)
    assert nav.ensure_in_game() == navigator.Screen.HOME
    assert [s for s, _ in game.taps] == ["birthday", "story_player", "story_player_menu", "story_skip"]
    assert tapped(game, (756, 532), radius=2) == ["story_skip"]  # 按 OCR 到的「跳过」点，不点「取消」


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
    改写，``bar`` 指定顶栏的识别结果（模拟读错），``timer`` 为顶栏下一行的恢复倒计时（LB 没满时才有），
    ``setting`` 为弹窗用的截图（活动期间每行还写着「活动pt」「挑战pt」）。"""

    def __init__(
        self,
        cost: int,
        held: int = 24,
        popup: bool = True,
        bar: str | None = None,
        timer: str | None = None,
        setting: str = "lb_setting",
    ):
        super().__init__("band_confirm")
        self.cost = cost
        self.held = held
        self.popup = popup
        self.bar = bar
        self.timer = timer
        self.setting = setting

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
        items = load_items(self.setting if name == "lb_setting" else name)
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


def test_set_lb_cost_during_event():
    """活动期间 LB 消耗设置弹窗上也有「挑战pt」，不能当成挑战pt消耗设置。"""
    game = LbGame(cost=0, held=8, setting="lb_setting_event")
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 3
    nav.start_live()
    assert game.cost == 3 and game.state == "loading"
    assert [s for s, _ in game.taps] == ["band_confirm", "lb_setting", "lb_setting", "band_confirm", "live_options"]


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


def test_lb_auto_follows_held(monkeypatch):
    """挂机消耗 0（auto）：持有 LB 时每局消耗 1，没有时消耗 0；顶栏的持有数和当前设置对得上就不开弹窗。"""
    game = LbGame(cost=3, held=5)
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 0
    now = fake_clock(monkeypatch, nav)

    def start(held, bar=None):
        game.state, game.held, game.bar = "band_confirm", held, bar
        game.taps.clear()
        nav.start_live("auto")
        assert game.state == "loading"
        return tapped(game, (920, 665))  # 打开消耗设置弹窗的次数

    assert start(5) == ["band_confirm"] and game.cost == 1
    assert start(4) == [] and game.cost == 1
    assert start(0) == ["band_confirm"] and game.cost == 0  # 用完了改为 0，不弹恢复窗口
    assert "lb_recover" not in [s for s, _ in game.taps]
    assert start(0) == [] and game.cost == 0
    assert start(1) == ["band_confirm"] and game.cost == 1  # 恢复了一个
    # 顶栏读错（实际 4 个却读成 0）：弹窗上看到有 LB，照样消耗 1
    assert start(4, bar="0/10") == ["band_confirm"] and game.cost == 1
    # 顶栏读多了（实际 0 个）：弹窗核对后改为 0
    game.cost = 0
    nav._lb_cost = 0
    assert start(0, bar="2/10") == ["band_confirm"] and game.cost == 0
    # 顶栏没读出来、已经按 0 打着：过了 LB_EMPTY_RETRY_S 才开弹窗看一次
    nav._lb_empty_at = now[0]
    assert start(3, bar="") == [] and game.cost == 0
    now[0] += navigator.LB_EMPTY_RETRY_S
    assert start(3, bar="") == ["band_confirm"] and game.cost == 1
    assert not tapped(game, (920, 575), radius=60)  # 弹窗里的「恢复」绝不点


def test_lb_auto_recover_popup_falls_back_to_zero():
    """auto 时顶栏没读出来、持有 0 点 LIVE START 弹出恢复窗口：点取消，改为消耗 0 接着打，不用道具。"""
    game = LbGame(cost=1, held=0, bar="")
    nav = make_nav(game, game, game)
    nav.cfg.game.lb_cost = 0
    nav.cfg.game.lb_refill = True
    nav._lb_cost = 1
    nav.start_live("auto")
    assert game.state == "loading" and game.cost == 0
    assert tapped(game, (498, 663)) == ["lb_recover"]
    assert not tapped(game, (940, 575), 60)
    assert nav._lb_empty_at is not None


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


def test_new_song_then_band_confirm():
    """新增乐曲的「追加翻唱乐曲！」演出：点 TAP TO NEXT 回到主界面，再进自由演出。"""
    game = FakeGame("new_song")
    nav = make_nav(game, game, game)
    nav.ensure_band_confirm()
    assert game.state == "band_confirm"
    assert [s for s, _ in game.taps][:3] == ["new_song", "home", "live_top"]


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


def test_restart_game(monkeypatch):
    """游戏变卡了重启：重新登录，停在主界面；没法重启时什么都不做。"""
    game = FakeGame("song_select")
    nav = make_nav(game, game, game)
    fake_clock(monkeypatch, nav)
    assert not nav.restart_game() and game.taps == []
    restarts = []

    def restart():
        restarts.append(game.state)
        game.state = "title"

    nav.restart_app = restart
    assert nav.restart_game()
    assert restarts == ["song_select"] and game.state == "home"


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


@pytest.mark.parametrize("call", ["ensure_band_confirm", "ensure_in_game", "go_home", "read_result"])
def test_maintenance_fails_at_once(call):
    """服务器维护页：任何导航都立刻报错（什么都不点、不干等超时），错误里带上维护时间。"""
    game = FakeGame("maintenance")
    nav = make_nav(game, game, game)
    with pytest.raises(ServerMaintenance, match="2026/10/02 11:00 ~ 2026/10/02 16:00"):
        getattr(nav, call)()
    assert game.taps == []


def test_relogin_after_maintenance(monkeypatch):
    """开服后：维护页点「返回标题画面」→ 标题 → 登录奖励 → 获得奖励 → 公告 → 主界面。"""
    monkeypatch.setitem(TRANSITIONS, "maintenance", [((640, 650), "title")])
    game = FakeGame("maintenance")
    nav = make_nav(game, game, game)
    assert nav.relogin() == navigator.Screen.HOME
    assert tapped(game, (638, 650), radius=2) == ["maintenance"]  # 按 OCR 到的按钮位置点
    assert [s for s, _ in game.taps] == ["maintenance", "title", "login_bonus", "reward", "notice"]


def test_login_downloads_data(monkeypatch):
    """游戏更新后登录先弹「数据下载」：点右边的 OK（不点取消），之后照常领登录奖励进到主界面。"""
    monkeypatch.setitem(TRANSITIONS, "title", [((950, 585), "data_download")])
    monkeypatch.setitem(TRANSITIONS, "data_download", [((782, 655), "login_bonus"), ((497, 654), "title")])
    game = FakeGame("title")
    nav = make_nav(game, game, game)
    assert nav.ensure_in_game() == navigator.Screen.HOME
    assert tapped(game, (782, 655), radius=2) == ["data_download"]  # 按 OCR 到的按钮位置点
    assert [s for s, _ in game.taps] == ["title", "data_download", "login_bonus", "reward", "notice"]


@pytest.mark.parametrize("call", ["ensure_in_game", "ensure_band_confirm", "relogin"])
def test_update_required_fails_at_once(call):
    """要求更新安装包（检测到新版本）：立刻报错停下，「前往商店」不点。"""
    game = FakeGame("update_required")
    nav = make_nav(game, game, game)
    nav.save_debug = lambda *a: None
    with pytest.raises(GameUpdateRequired, match="手动更新游戏") as e:
        getattr(nav, call)()
    assert isinstance(e.value, ScreenFrozen)  # 任务和挂机都按画面卡住处理：直接停止
    assert game.taps == []


def test_relogin_still_in_maintenance(monkeypatch):
    monkeypatch.setitem(TRANSITIONS, "maintenance", [((640, 650), "title")])
    monkeypatch.setitem(TRANSITIONS, "title", [((950, 585), "maintenance")])
    game = FakeGame("maintenance")
    nav = make_nav(game, game, game)
    with pytest.raises(ServerMaintenance):
        nav.relogin()
    assert [s for s, _ in game.taps] == ["maintenance", "title"]


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
    """画面一直不动、游戏还在运行、不能重启游戏：FROZEN_S 后就抛 ScreenFrozen，不等到超时。"""
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


@pytest.mark.parametrize("moving", [False, True])
def test_read_result_waits_through_still_screen(monkeypatch, moving):
    """等结算时歌可能还在放（同步失败），画面不动、一直认不出也不算卡住，不重启游戏。"""
    game = FrozenGame("intro_card", moving=moving)
    nav, _ = frozen_nav(monkeypatch, game)
    nav.restart_app = lambda: pytest.fail("不该重启游戏")
    with pytest.raises(NavigationError, match="没有出现结算页") as e:
        nav.read_result(timeout_s=400)
    assert not isinstance(e.value, ScreenFrozen)


class StuckGame(FrozenGame):
    """停在认不出的画面上（``moving`` 时一直在动，比如循环播放的演出），点了没反应；重启游戏后从标题画面
    照常登录，``again`` 时重启后还是停在原来的画面上。"""

    def __init__(self, state, moving=False, again=False):
        super().__init__(state, moving=moving)
        self.stuck, self.again = state, again
        self.restarts: list[float] = []

    def restart(self):
        self.restarts.append(navigator.time.monotonic())
        self.state = self.stuck if self.again else "title"

    def tap(self, x, y):
        FakeGame.tap(self, x, y)


@pytest.mark.parametrize("moving, wait", [(False, navigator.FROZEN_S), (True, navigator.UNKNOWN_STUCK_S)])
def test_stuck_screen_restarts_game(monkeypatch, moving, wait):
    """保底：画面停住 FROZEN_S、一直在动但认不出 UNKNOWN_STUCK_S，就重启游戏重新登录，不停下任务。"""
    game = StuckGame("intro_card", moving=moving)
    nav, now = frozen_nav(monkeypatch, game)
    nav.restart_app = game.restart
    start = now[0]
    assert nav.ensure_in_game() == navigator.Screen.HOME
    assert len(game.restarts) == 1 and wait <= game.restarts[0] - start < wait + 5
    assert [s for s, _ in game.taps] == ["title", "login_bonus", "reward", "notice"]


@pytest.mark.parametrize("moving, error", [(False, ScreenFrozen), (True, NavigationError)])
def test_stuck_screen_restarts_are_limited(monkeypatch, moving, error):
    """重启后还是卡住：回到主界面之前最多重启 MAX_RESTARTS 次，用完了报错停下。"""
    game = StuckGame("intro_card", moving=moving, again=True)
    nav, _ = frozen_nav(monkeypatch, game)
    nav.restart_app = game.restart
    with pytest.raises(error, match="没有变化" if error is ScreenFrozen else "认不出"):
        nav.ensure_in_game(timeout_s=1000)
    assert len(game.restarts) == navigator.MAX_RESTARTS


def test_loading_screen_is_not_stuck(monkeypatch):
    """下载、加载中的画面（一直在动）不算卡住，照常等到超时。"""
    game = StuckGame("intro_card", moving=True)
    game.read = lambda frame, roi=None: [*load_items("intro_card"), OcrItem(1000, 650, 200, 30, "NOW LOADING")]
    nav, _ = frozen_nav(monkeypatch, game)
    nav.restart_app = game.restart
    with pytest.raises(NavigationError, match="未能进入游戏"):
        nav.ensure_in_game(timeout_s=400)
    assert game.restarts == []


# ---------------------------------------------------------------- 挑战演出

SKIP = (780, 665)  # 挑战演出乐队确认页的「跳过 还剩n次」，绝不能点
CP_OK = (782, 570)
CP_CANCEL = (496, 570)


class ChallengeGame(FakeGame):
    """演出首页（有「挑战演出」）→ 挑战演出乐曲选择 → 乐队确认 → 挑战pt消耗设置。

    乐曲选择页左边是 ``songs``，第 ``sel`` 首选中、总在中间那一行（y=324），点哪一行就选中哪一首；点难度按钮
    改 ``diff``。右侧面板是选中的歌，(曲名, 难度) 在 ``ap`` 里时有 ALL PERFECT 标记；面板在点击后要再过 ``lag``
    次识别才跟上（模拟切换动画）。
    消耗设置弹窗按选中项画出白色单选按钮，点 OK 才改 ``cost``、点取消还原。顶栏和弹窗上的 CP 持有数按 ``held``
    改写，``bar`` 指定顶栏的识别结果（模拟读错）。"""

    MOVES = {
        "live_top_challenge": [((870, 637), "challenge_song_select"), ((873, 348), "song_select")],
        "challenge_song_select": [((1163, 658), "challenge_band_confirm"), ((56, 38), "live_top_challenge")],
        "challenge_band_confirm": [
            ((1140, 648), "live_options"),
            ((56, 38), "challenge_song_select"),
            ((920, 665), "challenge_cp_setting"),
        ],
        "song_select": [((56, 38), "live_top_challenge")],
    }
    ROW_GAP = 115

    def __init__(
        self,
        state="live_top_challenge",
        cost=400,
        held=3708,
        bar=None,
        songs=("A曲", "B曲", "C曲"),
        sel=0,
        ap=(),
        lag=0,
    ):
        super().__init__(state)
        self.cost = self.pending = cost
        self.held = held
        self.bar = bar
        self.songs = list(songs)
        self.sel = sel
        self.diff = "expert"
        self.ap = set(ap)
        self.lag = lag
        self.stale = 0
        self.panel = (self.songs[sel], (self.songs[sel], self.diff) in self.ap)

    def rows(self):
        for j, title in enumerate(self.songs):
            cy = 324 + (j - self.sel) * self.ROW_GAP
            if 60 <= cy <= 640:
                yield j, title, cy

    def grab(self):
        if self.state != "challenge_cp_setting":
            return super().grab()
        w, h = self.size
        img = np.full((h, w, 3), (71, 38, 38), np.uint8)
        x, y = CP_RADIO[self.pending]
        cv2.circle(img, (x * w // 1280, y * h // 720), 12, (255, 255, 255), -1)
        return img, 0.0

    def read(self, frame, roi=None):
        name = "challenge_cp_setting" if isinstance(frame, np.ndarray) else frame
        items = load_items(name)
        if name == "challenge_song_select":  # 换成 songs 里的歌
            items = [it for it in items if it.y < 60 or not 250 <= it.x + it.w / 2 <= 540 or it.text.startswith("Lv")]
            items = [it for it in items if not in_roi(it, SELECT_TITLE_ROI) and not in_roi(it, CLEAR_MARK_ROI)]
            items += [OcrItem(300, cy - 12, 160, 24, title) for _, title, cy in self.rows()]
            if self.stale:
                self.stale -= 1
            else:
                self.panel = (self.songs[self.sel], (self.songs[self.sel], self.diff) in self.ap)
            items.append(OcrItem(784, 423, 272, 32, self.panel[0]))
            if self.panel[1]:
                items.append(OcrItem(1064, 210, 120, 24, "ALLPERFECT"))
        held = {"challenge_band_confirm": str(self.held) if self.bar is None else self.bar}
        held["challenge_cp_setting"] = str(self.held)
        if name in held:
            items = [OcrItem(it.x, it.y, it.w, it.h, held[name] if it.text == "3708" else it.text) for it in items]
        return items

    def tap(self, x, y):
        p = (x * 1280 / self.size[0], y * 720 / self.size[1])
        here = self.state
        if here == "challenge_cp_setting":
            self.pending = next((c for c, q in CP_RADIO.items() if math.dist(p, q) < 20), self.pending)
            if math.dist(p, CP_OK) < 40:
                self.cost = self.pending
            elif math.dist(p, CP_CANCEL) < 40:
                self.pending = self.cost
            if math.dist(p, CP_OK) < 40 or math.dist(p, CP_CANCEL) < 40:
                self.state = "challenge_band_confirm"
            self.taps.append((here, p))
            return
        if here == "challenge_song_select" and abs(p[0] - 420) < 120:
            self.sel = next((j for j, _, cy in self.rows() if abs(p[1] - cy) < 40), self.sel)
            self.stale = self.lag
        if here == "challenge_song_select":
            for d, q in BTN_DIFFICULTY.items():
                if math.dist(p, q) < 40:
                    self.diff, self.stale = d, self.lag
        for target, nxt in self.MOVES.get(here, []):
            if math.dist(p, target) < 40:
                self.taps.append((here, p))
                self.state = nxt
                return
        super().tap(x, y)


def challenge_nav(game, cost=200):
    cfg = Config()
    cfg.game.difficulty = "expert"
    cfg.loop.challenge = True
    cfg.game.challenge_cost = cost
    nav = GameNavigator(cfg, game, game, game, settle_s=0)
    nav._sleep = lambda s: None
    nav.save_debug = lambda *a, **k: None
    return nav


def test_challenge_enter_and_set_cost():
    """演出首页 → 挑战演出 → EXPERT、确定 → 乐队确认；第一局前把每局消耗从 400 改成 200（核对过才点 OK）。"""
    game = ChallengeGame()
    nav = challenge_nav(game)
    nav.ensure_band_confirm()
    assert game.state == "challenge_band_confirm"
    assert [s for s, _ in game.taps] == ["live_top_challenge", "challenge_song_select", "challenge_song_select"]
    nav.start_live()
    assert game.cost == 200 and game.state == "loading"
    assert tapped(game, CP_RADIO[200]) == ["challenge_cp_setting"]
    assert tapped(game, CP_OK) == ["challenge_cp_setting"] and not tapped(game, CP_CANCEL)
    # 之后顶栏的 CP 够就不再打开弹窗
    game.state = "challenge_band_confirm"
    nav.start_live()
    assert len(tapped(game, (920, 665))) == 1 and game.state == "loading"
    assert not tapped(game, SKIP, 30)


def test_challenge_cost_already_set():
    game = ChallengeGame("challenge_band_confirm", cost=200)
    nav = challenge_nav(game)
    nav.start_live()
    assert tapped(game, CP_OK) == ["challenge_cp_setting"] and not tapped(game, CP_RADIO[200])
    assert nav._cp_cost == 200 and game.state == "loading"


def test_challenge_keep_cost_only_cancels():
    """不改游戏里的设置时只看一眼当前消耗，点「取消」关掉，绝不点 OK。"""
    game = ChallengeGame("challenge_band_confirm", cost=800)
    nav = challenge_nav(game, cost=None)
    nav.start_live()
    assert tapped(game, CP_CANCEL) == ["challenge_cp_setting"] and not tapped(game, CP_OK)
    assert game.cost == 800 and nav._cp_cost == 800 and game.state == "loading"


@pytest.mark.parametrize("bar", [None, ""])
def test_challenge_cp_exhausted(bar):
    """CP 不够一局（顶栏没读到时用弹窗里的持有数核对）就停下，不点 LIVE START。"""
    game = ChallengeGame("challenge_band_confirm", cost=200, held=150, bar=bar)
    nav = challenge_nav(game)
    with pytest.raises(CpExhausted, match="持有 150"):
        nav.start_live()
    assert isinstance(CpExhausted("x"), LbExhausted)  # 和 LB 用完一样结束
    assert game.state == "challenge_band_confirm" and not tapped(game, (1140, 648))


def test_challenge_cp_rechecked_when_bar_short():
    """顶栏读成不够时打开弹窗核对，弹窗上够就照常开始。"""
    game = ChallengeGame("challenge_band_confirm", cost=200, bar="100")
    nav = challenge_nav(game)
    nav._cp_cost = 200
    nav.start_live()
    assert tapped(game, CP_OK) == ["challenge_cp_setting"] and game.state == "loading"


def test_stray_cp_setting_is_cancelled():
    game = ChallengeGame("challenge_cp_setting", cost=400)
    nav = challenge_nav(game)
    nav.ensure_band_confirm()
    assert game.state == "challenge_band_confirm" and tapped(game, CP_CANCEL) == ["challenge_cp_setting"]
    assert not tapped(game, CP_OK) and game.cost == 400


def test_challenge_mode_leaves_free_live():
    """挑战演出时停在自由演出的乐队确认页：连续两次认出后返回，退到演出首页再进挑战演出。"""
    game = ChallengeGame("band_confirm")
    nav = challenge_nav(game)
    nav.ensure_band_confirm()
    assert game.state == "challenge_band_confirm"
    assert [s for s, _ in game.taps] == [
        "band_confirm",
        "song_select",
        "live_top_challenge",
        "challenge_song_select",
        "challenge_song_select",
    ]


def test_free_live_leaves_challenge():
    game = ChallengeGame("challenge_band_confirm")
    nav = make_nav(game, game, game)
    nav.ensure_band_confirm()
    assert game.state == "band_confirm"
    assert [s for s, _ in game.taps][:3] == ["challenge_band_confirm", "challenge_song_select", "live_top_challenge"]
    assert not tapped(game, SKIP, 30) and not tapped(game, (920, 665))


def test_challenge_not_open():
    """不在活动期间（演出首页没有「挑战演出」）：多看几次还是没有就报错，不去点别的。"""
    game = ChallengeGame("live_top")
    nav = challenge_nav(game)
    with pytest.raises(NavigationError, match="挑战演出"):
        nav.ensure_band_confirm()
    assert game.taps == []


def test_invalid_challenge_cost():
    cfg = Config()
    cfg.game.challenge_cost = 300
    with pytest.raises(ValueError, match="challenge_cost"):
        GameNavigator(cfg, None, None, None)


@pytest.mark.parametrize("count, sel", [(3, 0), (3, 1), (3, 2), (6, 5), (6, 2)])
def test_next_challenge_song(count, sel):
    """选中下一首；最后一首之后回到第一首（列表长时一直往上点）。"""
    game = ChallengeGame("challenge_song_select", songs=[f"{j}曲" for j in range(count)], sel=sel)
    nav = challenge_nav(game)
    nav.next_challenge_song()
    assert game.sel == (sel + 1) % count and game.state == "challenge_song_select"


def test_next_challenge_song_from_band_confirm():
    game = ChallengeGame("challenge_band_confirm", sel=1)
    nav = challenge_nav(game)
    nav.next_challenge_song()
    assert game.sel == 2 and game.state == "challenge_song_select"
    assert not tapped(game, SKIP, 30)


def test_next_challenge_song_single():
    game = ChallengeGame("challenge_song_select", songs=["A曲"])
    nav = challenge_nav(game)
    nav.next_challenge_song()
    assert game.sel == 0 and game.taps == []


@pytest.mark.parametrize("lag", [0, 1])
def test_challenge_song_ap(lag):
    """选上难度后读右侧面板：曲名对上选中行、连续两次读数一样才算数（实测点难度后 0.2 秒内面板就跟上了）。"""
    game = ChallengeGame("challenge_song_select", sel=1, ap={("B曲", "expert"), ("C曲", "hard")}, lag=lag)
    nav = challenge_nav(game)
    assert nav.challenge_song_ap("expert") == ("B曲", True)
    assert nav.challenge_song_ap("hard") == ("B曲", False) and game.diff == "hard"
    nav.next_challenge_song()
    assert nav.challenge_song_ap("hard") == ("C曲", True)
    assert tapped(game, BTN_DIFFICULTY["hard"]) == ["challenge_song_select"] * 2
    assert not tapped(game, SKIP, 30)


def test_challenge_song_ap_from_band_confirm():
    game = ChallengeGame("challenge_band_confirm", ap={("A曲", "expert")})
    nav = challenge_nav(game)
    assert nav.challenge_song_ap("expert") == ("A曲", True) and game.state == "challenge_song_select"
    assert not tapped(game, SKIP, 30)


def test_challenge_song_ap_panel_mismatch():
    """面板上的曲名一直对不上选中行：报错，不乱猜。"""
    game = ChallengeGame("challenge_song_select", lag=100)
    game.panel, game.stale = ("别的歌", False), 100
    nav = challenge_nav(game)
    with pytest.raises(NavigationError, match="AP"):
        nav.challenge_song_ap("expert")


def test_challenge_ap_first_run():
    """ap_first：跳过已 AP 的，打第一首没 AP 的（打完再次演出回到乐曲选择页）。"""
    game = ChallengeGame("challenge_song_select", ap={("A曲", "expert"), ("B曲", "expert")})
    nav = challenge_nav(game)
    src = sources.ChallengeApFirst(["expert"])
    assert src.advance(nav, True)
    assert game.songs[game.sel] == "C曲" and src.difficulty == "expert"
    nav.ensure_band_confirm(src.difficulty)
    assert game.state == "challenge_band_confirm" and game.diff == "expert"


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
