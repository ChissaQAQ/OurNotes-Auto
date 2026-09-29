"""GameNavigator：用画面 OCR 夹具模拟游戏的状态机测试 + 真实 OCR 读取结算页截图。"""

import json
import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from ournotes_auto.config import Config
from ournotes_auto.nav import navigator
from ournotes_auto.nav.navigator import GameNavigator
from ournotes_auto.nav.screens import LB_RADIO, RESULT_ROW_Y
from ournotes_auto.result_reader import OcrItem
from ournotes_auto.runner import LbExhausted, NavigationError

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


class LbGame(FakeGame):
    """乐队确认页 + LB 消耗设置弹窗（按当前选中项画出白色单选按钮）；``held`` 为 0 且消耗不为 0 时
    LIVE START 弹出恢复 LIVE BOOST（``popup``，实机大多直接开始、不消耗）。顶栏和弹窗上的持有数按 ``held``
    改写，``bar`` 指定顶栏的识别结果（模拟读错）。"""

    def __init__(self, cost: int, held: int = 24, popup: bool = True, bar: str | None = None):
        super().__init__("band_confirm")
        self.cost = cost
        self.held = held
        self.popup = popup
        self.bar = bar

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
        return [OcrItem(it.x, it.y, it.w, it.h, texts.get(it.text.rpartition("/")[2], it.text)) for it in items]

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
