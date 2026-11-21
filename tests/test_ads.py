"""看广告：用画面 OCR 夹具模拟主界面、恢复LIVE BOOST、招募页、录音室练习，广告用「前台窗口 + 顶部提示条」模拟。"""

import calendar
import dataclasses
import json
import math
import subprocess
import types
from pathlib import Path

import cv2
import numpy as np
import pytest

from ournotes_auto.config import Config
from ournotes_auto.nav import ads, daily, song_select
from ournotes_auto.nav.ads import (
    AD_JOBS,
    AD_TIMEOUT_S,
    ad_closing,
    ad_countdown,
    ad_granted,
    ad_progress,
    ad_recruit_button,
    ads_left,
    cm_banner,
    cm_recruit_got,
    home_lb,
    lb_ad_got,
    lb_ad_watch,
    same_panels,
    studio_ad_button,
    studio_ad_got,
    studio_ads_left,
    studio_band,
    studio_detail,
    studio_level_roi,
    studio_level_text,
    studio_map,
    studio_panels,
)
from ournotes_auto.nav.daily import (
    cm_recruit_cancel,
    cm_recruit_dialog,
    cm_recruit_ok,
    free_recruit_ok,
    recruit_show,
    recruit_top,
    studio_skip_close,
    studio_skip_dialog,
)
from ournotes_auto.nav.lang import localize
from ournotes_auto.nav.navigator import GameNavigator
from ournotes_auto.nav.screens import ARENA_BANDS as STUDIO_BANDS
from ournotes_auto.nav.screens import Screen, center, classify
from ournotes_auto.records import RecordStore, game_day
from ournotes_auto.result_reader import OcrItem

SCREENS = Path(__file__).parent / "fixtures" / "screens"
IMAGES = Path(__file__).resolve().parents[1] / "resource" / "image"
PACKAGE = Config().device.package
GAME = f"{PACKAGE}/com.bangdream.sirius.MainActivity"
AD = f"{PACKAGE}/com.google.android.gms.ads.AdActivity"
STORE = "com.android.vending/com.google.android.finsky.transparentmainactivity.HsdpAlias"

# 广告顶部的提示条（实机上读到的样子），和提示条下面的广告内容
BARS = {
    "count": [OcrItem(22, 22, 26, 24, "X"), OcrItem(57, 22, 129, 23, "6秒后可得奖励")],
    "multi": [OcrItem(904, 22, 352, 23, "第2个广告，共3个·14秒后可得奖励 ×")],
    "x": [OcrItem(1232, 22, 24, 25, "X")],
    "granted": [OcrItem(1125, 22, 132, 25, "奖励已发放 X")],
    "granted_left": [OcrItem(18, 17, 139, 30, "× 奖励已发放")],
    "progress": [],  # 没有文字，最上面有一条黄色的进度条
    "": [],
}
AD_CONTENT = [OcrItem(1065, 45, 165, 27, "GET IT NOW"), OcrItem(861, 604, 56, 32, "安装"), OcrItem(1245, 696, 34, 21, "广告")]
CLOSING = [
    OcrItem(462, 304, 116, 32, "关闭广告？"),
    OcrItem(464, 356, 152, 26, "您将会失去奖励"),
    OcrItem(668, 424, 48, 27, "关闭"),
    OcrItem(762, 424, 50, 27, "继续"),
]
BTN_RESUME = (787, 438)
BTN_FAILED_OK = (640, 570)  # 「广告获取失败。请稍后再试。」的 OK
WALL = float(calendar.timegm((2026, 11, 19, 14, 0, 0)))  # 假时钟为 0 时的时刻（北京时间 22:00）
FAIL = "fail"  # 广告的样子：游戏说「广告获取失败」
PASS = "pass"  # 广告的样子：游戏里开通了跳过广告，不放广告、约 1 秒后奖励直接到手（#146）
BTN_CLOSE_AD = (692, 438)

BTN_LB_ADD = (517, 44)
BTN_AD_TAB = (133, 290)
BTN_WATCH = (780, 662)
BTN_LB_CANCEL = (497, 662)
BTN_RECRUIT = (576, 660)
CM_BANNER = (117, 592)
BTN_CM = (1154, 578)
BTN_CM_OK = (782, 570)
BTN_CM_CANCEL = (497, 570)
BTN_TOP = (867, 659)
BTN_TOP_LAST = (1138, 659)
BTN_AGAIN = (1137, 659)
PAID_BUTTONS = [(440, 561), (680, 560), (918, 561), (1156, 559)]  # 招募页没选中 CM 招募时的四个招募按钮

BTN_STUDIO = (1232, 515)
BTN_STUDIO_BACK = (60, 40)
BTN_SKIP = (818, 658)
BTN_STUDIO_CLAIM = (1102, 659)  # 详情页的「领取奖励」（看广告时不该点）
BTN_SKIP_AD = (923, 572)
BTN_SKIP_ITEM = (638, 572)  # SKIP 弹窗的「道具」（用 SKIP 券，不该点）
BTN_SKIP_CLOSE = (355, 585)
BTN_STUDIO_OK = (964, 659)
# 录音室练习的地图拖到最左 / 最右时看得到的乐队（标志的点击位置 → 乐队），中间的两头都看得到
STUDIO_MAPS = {
    "studio_map": {(326, 329): "dumbrock", (681, 344): "mygo", (968, 197): "avemujica"},
    "studio_map_right": {(250, 197): "avemujica", (577, 265): "mewtype", (890, 319): "millsage"},
}
LEVELS = {"dumbrock": 24, "mygo": 34, "avemujica": 34, "mewtype": 24, "millsage": 24}


def load_items(name: str) -> list[OcrItem]:
    data = json.loads((SCREENS / f"{name}.json").read_text(encoding="utf-8"))
    return [OcrItem(x, y, w, h, localize(text)) for x, y, w, h, text in data]


@dataclasses.dataclass
class Seg:
    """广告的一段：放 ``secs`` 秒，顶部提示条是 ``bar``；``rewarded`` 时按返回键就关掉（奖励到手），
    否则问「关闭广告？」；``store`` 是广告弹出的商店页（开着时广告不走，按返回键才过去）。"""

    secs: float
    bar: str = ""
    rewarded: bool = False
    store: bool = False


SIMPLE = [Seg(14, "count"), Seg(math.inf, "granted", True)]


class AdGame:
    """同时充当截图源、触控、OCR、前台窗口和返回键。``lb`` ``cm`` ``studio`` 是今天还剩的次数，``plan`` 是接下来每次广告的样子
    （用完了就是 SIMPLE；None 是广告放不出来、什么都不弹，FAIL 是约 1 秒后弹出「广告获取失败」，
    PASS 是不放广告、约 1 秒后奖励直接到手）。``levels`` 是各乐队的练习Lv。
    ``show``：观看 CM 招募到新成员，招募结算之前先停在展示画面上（整屏立绘，没有按钮），要点这么多下才继续。"""

    size = (1280, 720)

    def __init__(self, clock, state="home", lb=2, cm=3, plan=(), studio=1, levels=LEVELS, show=0):
        self.clock = clock
        self.state = state
        self.lb = lb
        self.cm = cm
        self.show = show
        self.show_next = "recruit_cm_result_last"  # 展示画面点完之后的画面
        self.show_at = clock[0]  # 展示画面出现的时刻
        self.show_times: list[float] = []  # 每次点展示画面的时刻
        self.plan = list(plan)
        self.ad: list[Seg] | None = None
        self.ad_at = 0.0
        self.dialog = False
        self.failed_at: float | None = None  # 「广告获取失败」弹出来的时刻（弹着时不是 None）
        self.failed_for: list[float] = []  # 每次「广告获取失败」弹了多久才被点掉
        self.pass_at: float | None = None  # 跳过广告：奖励在这个时刻直接到手（等着时不是 None）
        self.passed = 0  # 有几次是没放广告、奖励直接到手的
        self.after = ("", "")  # 广告关掉之后的画面：（奖励到手, 没到手）
        self.taps: list[tuple[str, tuple[int, int]]] = []
        self.backs: list[str] = []  # 每次按返回键时的前台窗口
        self.ad_taps: list[tuple[int, int]] = []  # 点在广告内容上的（不该有）
        self.watched = 0
        self.gems = "51,715"
        self.studio = studio
        self.levels = levels
        self.map = "studio_map_right"  # 录音室练习的地图现在拖在哪一头
        self.band: str | None = None  # 打开着详情页的乐队
        self.opened: list[str] = []  # 依次打开过哪些乐队的详情页
        self.unknown: set[str] = set()  # 详情页上标志认不出来的乐队
        self.skip_grey = False  # SKIP 弹窗里的「广告」按钮是灰的（详情页上还有角标）
        self.drag: list[int] | None = None

    # ---- 广告
    def _advance(self) -> None:
        if self.pass_at is not None and self.clock[0] >= self.pass_at:
            self.pass_at = None
            self.passed += 1
            self._end_ad(True)
        if self.ad is None:
            return
        elapsed, self.ad_at = self.clock[0] - self.ad_at, self.clock[0]
        if self.dialog or self.ad[0].store:
            return
        while elapsed > 0 and len(self.ad) > 1:
            used = min(elapsed, self.ad[0].secs)
            self.ad[0] = dataclasses.replace(self.ad[0], secs=self.ad[0].secs - used)
            elapsed -= used
            if self.ad[0].secs > 0:
                break
            self.ad.pop(0)
            if self.ad[0].store:
                break

    def _start_ad(self, rewarded: str, lost: str) -> None:
        plan = self.plan.pop(0) if self.plan else SIMPLE
        if plan is None:  # 广告放不出来：停在原来的画面
            return
        if plan == FAIL:  # 没要到广告：过 1 秒弹出「广告获取失败」，原来的弹窗还在下面
            self.failed_at = self.clock[0] + 1.0
            return
        if plan == PASS:  # 跳过广告：前台一直是游戏，过 1 秒奖励直接到手
            self.after, self.pass_at = (rewarded, lost), self.clock[0] + 1.0
            return
        self.ad, self.ad_at, self.dialog = [dataclasses.replace(s) for s in plan], self.clock[0], False
        self.after = (rewarded, lost)

    def _end_ad(self, rewarded: bool) -> None:
        self.ad, self.dialog = None, False
        self.state = self.after[0] if rewarded else self.after[1]
        if rewarded:
            self.watched += 1
            if self.state == "lb_recovered":
                self.lb -= 1
            elif self.state == "daily_studio_reward":
                self.studio -= 1
            else:
                self.cm -= 1
                self.state = "recruit_cm_result" if self.cm else "recruit_cm_result_last"
                if self.show:
                    self.show_next, self.state, self.show_at = self.state, "recruit_new_card", self.clock[0]

    def front_window(self) -> str:
        self._advance()
        if self.ad is None:
            return GAME
        return STORE if self.ad[0].store else AD

    def press_back(self) -> None:
        self._advance()
        self.backs.append(self.front_window())
        if self.ad is None:
            return
        if self.ad[0].store:
            self.ad.pop(0)
        elif self.dialog:
            self.dialog = False
        elif self.ad[0].rewarded:
            self._end_ad(True)
        else:
            self.dialog = True

    # ---- 截图、OCR
    def _failed(self) -> bool:
        return self.failed_at is not None and self.clock[0] >= self.failed_at

    def _lit(self) -> tuple[int, int] | None:
        """现在的画面上亮着的看广告按钮（在文字左边取样的位置）。"""
        if self._failed():
            return None
        if self.state == "lb_recover_ad" and self.lb:
            return BTN_WATCH[0] - 75, BTN_WATCH[1]
        if self.state == "recruit_cm" and self.cm:
            return BTN_CM[0] - 75, BTN_CM[1]
        if self.state == "studio_skip" and self.studio and not self.skip_grey:
            return BTN_SKIP_AD[0] - 75, BTN_SKIP_AD[1]
        return None

    def grab(self):
        self._advance()
        img = np.full((720, 1280, 3), 40, np.uint8)
        if self.ad is None and (p := self._lit()) is not None:
            img[p[1] - 15 : p[1] + 15, p[0] - 40 : p[0] + 200] = (244, 174, 129)
        if self.ad is not None and self.ad[0].bar == "progress":
            img[0:6, 0:800] = (70, 206, 247)
        if self.ad is None and self.state == "studio_detail" and self.band not in self.unknown:
            logo = cv2.imread(str(IMAGES / f"studio_band_{self.band}.png"))  # 详情页左上角的乐队标志
            img[10 : 10 + logo.shape[0], 666 : 666 + logo.shape[1]] = logo
        return img, 0.0

    def read_text(self, frame, roi=None):
        """只识别一小块：详情页右上角的练习Lv。"""
        return str(self.levels[self.band]) if self.state == "studio_detail" else ""

    def read(self, frame, roi=None):
        self._advance()
        if self.ad is not None:
            return [*BARS[self.ad[0].bar], *AD_CONTENT, *(CLOSING if self.dialog else [])]
        if self._failed():
            return load_items("ad_failed")
        name = self.state
        if name == "studio_detail" and not self.studio:
            name = "studio_detail_used"
        if name == "studio_skip" and (not self.studio or self.skip_grey):
            name = "studio_skip_used"
        items = load_items(name)
        left = {"lb_recover_ad_left": self.lb, "lb_recover_ad": self.lb, "recruit_cm": self.cm}.get(self.state)
        if left is not None:
            items = [dataclasses.replace(it, text=f"还剩{left}次") if it.text.startswith("还剩") else it for it in items]
        return [dataclasses.replace(it, text=self.gems) if it.text == "51,715" else it for it in items]

    # ---- 触控
    def tap(self, x, y):
        self._advance()
        p = (x, y)
        if self.ad is not None:
            if self.dialog and math.dist(p, BTN_RESUME) < 30:
                self.dialog = False
            elif self.dialog and math.dist(p, BTN_CLOSE_AD) < 30:
                self._end_ad(False)
            else:
                self.ad_taps.append(p)
            return
        if self._failed():  # 弹着「广告获取失败」：只有 OK 点得到
            self.taps.append(("ad_failed", p))
            if math.dist(p, BTN_FAILED_OK) < 30:
                self.failed_for.append(self.clock[0] - self.failed_at)
                self.failed_at = None
            return
        self.taps.append((self.state, p))
        near = lambda q: math.dist(p, q) < 30  # noqa: E731
        s = self.state
        if s == "home":
            if near(BTN_LB_ADD):
                self.state = "lb_recover_ad_left"
            elif near(BTN_RECRUIT):
                self.state = "recruit_cm_list"
            elif near(BTN_STUDIO):
                self.state = self.map
        elif s in STUDIO_MAPS:
            band = next((b for q, b in STUDIO_MAPS[s].items() if near(q)), None)
            if band is not None:
                self.band, self.state = band, "studio_detail"
                self.opened.append(band)
            elif near((164, 40)):
                self.state = "home"
        elif s == "studio_detail":
            if near(BTN_STUDIO_BACK):
                self.state = self.map
            elif near(BTN_SKIP):
                self.state = "studio_skip"
            elif near((164, 40)):
                self.state = "home"
        elif s == "studio_skip":
            if near(BTN_SKIP_CLOSE):
                self.state = "studio_detail"
            elif near(BTN_SKIP_AD) and self.studio and not self.skip_grey:
                self._start_ad("daily_studio_reward", "studio_skip")
        elif s == "daily_studio_reward":
            if near(BTN_STUDIO_OK):
                self.state = "studio_detail"
        elif s in ("lb_recover_ad_left", "lb_recover_ad"):
            if near(BTN_LB_CANCEL):
                self.state = "home"
            elif near(BTN_AD_TAB):
                self.state = "lb_recover_ad"
            elif s == "lb_recover_ad" and near(BTN_WATCH) and self.lb:
                self._start_ad("lb_recovered", "lb_recover_ad")
        elif s == "lb_recovered":
            if near((640, 570)):
                self.state = "home"
        elif s == "recruit_cm_list":
            if near(CM_BANNER):
                self.state = "recruit_cm"
            elif near((164, 40)):
                self.state = "home"
        elif s == "recruit_cm":
            if near(BTN_CM) and self.cm:
                self.state = "recruit_cm_confirm"
            elif near((164, 40)):
                self.state = "home"
        elif s == "recruit_cm_confirm":
            if near(BTN_CM_OK):
                self._start_ad("recruit_cm_result", "recruit_cm")
            elif near(BTN_CM_CANCEL):
                self.state = "recruit_cm"
        elif s == "recruit_new_card":  # 点哪里都算一下
            self.show_times.append(self.clock[0])
            self.show -= 1
            if self.show <= 0:
                self.state = self.show_next
        elif s == "recruit_cm_result":
            if near(BTN_TOP):
                self.state = "recruit_cm"
        elif s == "recruit_cm_result_last":
            if near(BTN_TOP_LAST):
                self.state = "recruit_cm"

    def down(self, slot, x, y):
        self.drag = [x, x]

    def move(self, slot, x, y):
        if self.drag is not None:
            self.drag[1] = x

    def up(self, slot):
        """录音室练习的地图：往右拖看到最左边，往左拖看到最右边。"""
        if self.drag is not None and self.state in STUDIO_MAPS and abs(self.drag[1] - self.drag[0]) > 100:
            self.map = self.state = "studio_map" if self.drag[1] > self.drag[0] else "studio_map_right"
        self.drag = None

    def flush(self):
        pass


@pytest.fixture
def clock(monkeypatch, tmp_path):
    """假时钟：``_sleep`` 推进时间，等待循环不用真的等；招募结果的截图存到临时目录。"""
    now = [0.0]
    fake = types.SimpleNamespace(monotonic=lambda: now[0], sleep=lambda s: None)
    monkeypatch.setattr(daily, "time", fake)
    monkeypatch.setattr(ads, "time", fake)
    monkeypatch.setattr(song_select, "time", fake)  # 拖动地图时不真的等
    monkeypatch.setattr(daily, "RECRUIT_SHOT_DIR", tmp_path / "recruit")
    monkeypatch.setattr(ads, "log_image", lambda path: None)
    return now


def ad_nav(game, clock, **kw):
    nav = GameNavigator(
        Config(), game, game, game, settle_s=0, front_window=game.front_window, press_back=game.press_back, **kw
    )

    def sleep(s):
        clock[0] += s

    nav._sleep = sleep
    nav.save_debug = lambda *a, **k: None
    nav.wall_clock = lambda: WALL + clock[0]
    return nav


def assert_clean(game):
    """广告里的东西没点过；返回键只在广告（或它弹出的商店页）在前台时按过；花星钻的按钮没点过。"""
    assert game.ad_taps == []
    assert all(w != GAME for w in game.backs), game.backs
    for state, p in game.taps:
        if state == "recruit_cm_list":
            assert all(math.dist(p, q) > 30 for q in PAID_BUTTONS), p
        if state == "recruit_cm_result":
            assert math.dist(p, BTN_AGAIN) > 30, p
        if state == "studio_detail":
            assert math.dist(p, BTN_STUDIO_CLAIM) > 30, p
        if state == "studio_skip":
            assert math.dist(p, BTN_SKIP_ITEM) > 30, p


# ------------------------------------------------------------ 识别


def test_ad_bar_helpers():
    assert ad_countdown(BARS["count"]) == 6
    assert ad_countdown(BARS["multi"]) == 14
    assert ad_countdown([OcrItem(52, 22, 145, 23, "57 秒后可得奖励")]) == 57
    for name in ("x", "granted", ""):
        assert ad_countdown([*BARS[name], *AD_CONTENT]) is None, name
    # 提示条下面广告自己的文字不算
    assert ad_countdown([OcrItem(500, 300, 200, 30, "5秒后可得奖励")]) is None
    assert not ad_granted([OcrItem(500, 300, 200, 30, "奖励已发放")])
    assert ad_granted(BARS["granted"]) and ad_granted(BARS["granted_left"])
    for name in ("count", "multi", "x", ""):
        assert not ad_granted([*BARS[name], *AD_CONTENT]), name
    close, resume = ad_closing([*BARS["count"], *AD_CONTENT, *CLOSING])
    assert math.dist(center(close), BTN_CLOSE_AD) < 5 and math.dist(center(resume), BTN_RESUME) < 5
    assert ad_closing([*BARS["count"], *AD_CONTENT]) is None
    assert ad_closing([it for it in CLOSING if "关闭广告" not in it.text]) is None
    # 最上面的黄色进度条：从左边开始、颜色均匀的才算
    img = np.full((720, 1280, 3), 255, np.uint8)
    assert not ad_progress(img) and not ad_progress("home")
    img[0:6, 0:300] = (70, 206, 247)
    assert ad_progress(img) and ad_progress(np.repeat(np.repeat(img, 2, axis=0), 2, axis=1))
    img[0:6, 0:300] = (60, 60, 250)
    assert not ad_progress(img)


def test_lb_ad_helpers():
    assert home_lb(load_items("home")) == 23
    assert home_lb(load_items("lb_recover_ad")) is None
    for name, left in (("lb_recover_ad_left", 2), ("lb_recover_ad", 2), ("lb_recover_ad_zero", 0)):
        items = load_items(name)
        assert classify(items) is Screen.LB_RECOVER, name
        assert ads_left(items, ads.LB_AD_LEFT_ROI) == left, name
    assert ads_left(load_items("lb_recover"), ads.LB_AD_LEFT_ROI) is None  # 老夹具：分页上没有角标
    watch = lb_ad_watch(load_items("lb_recover_ad"))
    assert watch is not None and math.dist(center(watch), BTN_WATCH) < 5
    # 次数用完时文字一样（按钮是灰的，另外看颜色）
    assert lb_ad_watch(load_items("lb_recover_ad_zero")) is not None
    # 「道具」页右下角是 OK，没有「观看」
    for name in ("lb_recover_ad_left", "lb_recover", "lb_recover_drinks", "home"):
        assert lb_ad_watch(load_items(name)) is None, name


def test_cm_recruit_helpers():
    banner = cm_banner(load_items("recruit_cm_list"))
    assert banner is not None and math.dist(banner, CM_BANNER) < 10
    assert cm_banner(load_items("recruit_cm")) is not None  # 选中了也认得（再点一次没有影响）
    for name in ("recruit_free", "recruit_used", "home", "recruit_cm_confirm"):
        assert cm_banner(load_items(name)) is None, name
    button = ad_recruit_button(load_items("recruit_cm"))
    assert button is not None and math.dist(center(button), (1154, 593)) < 5
    for name in ("recruit_cm_list", "recruit_free", "recruit_used", "recruit_cm_confirm"):
        assert ad_recruit_button(load_items(name)) is None, name
    for name, left in (("recruit_cm", 2), ("recruit_cm_zero", 0)):
        assert ads_left(load_items(name), ads.AD_RECRUIT_ROI) == left, name
    # 没选中 CM 招募时，别的招募按钮下面的「还剩1次」（每日 1 次限定的付费招募）不在这个范围里
    assert ads_left(load_items("recruit_cm_list"), ads.AD_RECRUIT_ROI) is None
    items = load_items("recruit_cm_confirm")
    assert cm_recruit_dialog(items)
    ok = cm_recruit_ok(items)
    assert ok is not None and math.dist(center(ok), BTN_CM_OK) < 5
    assert math.dist(center(cm_recruit_cancel(items)), BTN_CM_CANCEL) < 5
    assert free_recruit_ok(items) is None  # 免费招募不认这个弹窗
    for text in ("将消耗200个星钻，进行1次招募。", "将消耗0个付费星钻，进行1次招募。", "观看广告，进行10次招募。"):
        bad = [dataclasses.replace(it, text=text) if "进行1次招募" in it.text else it for it in items]
        assert cm_recruit_ok(bad) is None, text
    assert cm_recruit_ok([it for it in items if "不会消耗星钻" not in it.text]) is None
    for name in ("recruit_confirm", "recruit_cm", "daily_gifts_confirm", "lb_recover_confirm"):
        assert cm_recruit_ok(load_items(name)) is None and cm_recruit_cancel(load_items(name)) is None, name
    # 招募结算：还能再招募时「招募TOP」在中间、右边是「再次招募」；今天最后一次只有右下角的「招募TOP」
    top = recruit_top(load_items("recruit_cm_result"))
    assert top is not None and math.dist(center(top), BTN_TOP) < 5
    last = recruit_top(load_items("recruit_cm_result_last"))
    assert last is not None and math.dist(center(last), BTN_TOP_LAST) < 5
    assert all("再次招募" not in it.text for it in load_items("recruit_cm_result_last"))


# ------------------------------------------------------------ 恢复 LIVE BOOST


def test_lb_watches_until_none_left(clock):
    """还剩 2 次：看两次广告（一次有倒计时，一次是放完才出现「奖励已发放」的），次数用完后点取消回主界面。"""
    plan = [SIMPLE, [Seg(37, ""), Seg(math.inf, "granted_left", True)]]
    game = AdGame(clock, plan=plan)
    nav = ad_nav(game, clock)
    assert nav.run_ads(["lb"]) == []
    assert (game.state, game.lb, game.watched) == ("home", 0, 2)
    assert game.backs == [AD, AD]
    assert len([p for s, p in game.taps if s == "lb_recover_ad" and math.dist(p, BTN_WATCH) < 30]) == 2
    assert_clean(game)


def test_lb_none_left_is_not_tapped(clock):
    """角标写着还剩 0 次：不切分页、不点「观看」，点取消。"""
    game = AdGame(clock, lb=0)
    nav = ad_nav(game, clock)
    assert nav.run_ads(["lb"]) == []
    assert [s for s, _ in game.taps] == ["home", "lb_recover_ad_left"]
    assert math.dist(game.taps[-1][1], BTN_LB_CANCEL) < 30
    assert (game.state, game.backs) == ("home", [])


def test_lb_grey_button_is_not_tapped(clock):
    """角标没读出来、「观看」是灰的：不点。"""
    game = AdGame(clock, lb=0)
    read = game.read
    game.read = lambda frame, roi=None: [it for it in read(frame) if not it.text.startswith("还剩")]
    nav = ad_nav(game, clock)
    assert nav.run_ads(["lb"]) == []
    assert all(math.dist(p, BTN_WATCH) > 30 for _, p in game.taps)
    assert (game.state, game.backs) == ("home", [])


def test_ad_store_page_and_silent_reward(clock):
    """连播的广告：中途弹出商店页（按返回键关掉），倒计时走完只剩 ✕（没有「奖励已发放」）时按返回键关掉。"""
    plan = [[Seg(10, "multi"), Seg(0, store=True), Seg(6, "multi"), Seg(math.inf, "x", True)]]
    game = AdGame(clock, lb=1, plan=plan)
    nav = ad_nav(game, clock)
    assert nav.run_ads(["lb"]) == []
    assert (game.state, game.lb) == ("home", 0)
    assert game.backs == [STORE, AD]
    assert_clean(game)


def test_ad_back_too_early_resumes(clock):
    """倒计时没了但奖励还没到手：按返回键弹出「关闭广告？」，点「继续」，过一阵再按才关掉；「关闭」没点过。"""
    plan = [[Seg(8, "count"), Seg(12, "x"), Seg(math.inf, "x", True)]]
    game = AdGame(clock, lb=1, plan=plan)
    nav = ad_nav(game, clock)
    assert nav.run_ads(["lb"]) == []
    assert (game.state, game.lb, game.watched) == ("home", 0, 1)
    assert len(game.backs) >= 2 and set(game.backs) == {AD}
    assert_clean(game)


def test_ad_progress_bar_gone_is_closed(clock):
    """没有提示条、只有进度条的连播广告：中途弹出商店页按返回键关掉，进度条没了就按返回键关掉，不用等到 AD_BLIND_S。"""
    plan = [[Seg(30, "progress"), Seg(0, store=True), Seg(20, "progress"), Seg(math.inf, "", True)]]
    game = AdGame(clock, lb=1, plan=plan)
    nav = ad_nav(game, clock)
    assert nav.run_ads(["lb"]) == []
    assert (game.state, game.lb, game.watched) == ("home", 0, 1)
    assert game.backs == [STORE, AD]
    assert clock[0] < ads.AD_BLIND_S
    assert_clean(game)


def test_ad_without_bar_is_closed_blind(clock):
    """从头到尾什么提示都没有的广告：放了 AD_BLIND_S 之后隔一阵按一次返回键，没到手（问「关闭广告？」）就点「继续」。"""
    plan = [[Seg(100, ""), Seg(math.inf, "", True)]]
    game = AdGame(clock, lb=1, plan=plan)
    nav = ad_nav(game, clock)
    assert nav.run_ads(["lb"]) == []
    assert (game.state, game.lb, game.watched) == ("home", 0, 1)
    assert game.backs == [AD, AD]
    assert_clean(game)


def test_ad_not_shown_is_an_error(clock):
    """点了「观看」广告没放出来（Google 不给广告、什么都没弹）：这一项记为出错（没要到广告），不按返回键，
    恢复LIVE BOOST 弹窗关掉，最后回到主界面。"""
    game = AdGame(clock, plan=[None])
    nav = ad_nav(game, clock)
    assert nav.run_ads(["lb"], waits=()) == [AD_JOBS["lb"]]
    assert (game.state, game.lb, game.backs, game.watched) == ("home", 2, [], 0)
    assert nav.ads_unavailable == ("lb",)
    assert [p for s, p in game.taps if s == "lb_recover_ad"][-1] == BTN_LB_CANCEL


def test_no_ads_anywhere_does_not_block(clock):
    """哪里的广告都放不出来（不是 Google Play 版等）：三项各等 AD_START_S、隔一阵再试两遍后记为出错，不按返回键、
    不重启游戏，十几分钟内结束，最后停在主界面（后面的任务照常开始）。"""
    game = AdGame(clock, plan=[None] * 9)
    restarts = []
    nav = ad_nav(game, clock, restart_app=lambda: restarts.append(clock[0]))
    assert nav.run_ads(list(AD_JOBS)) == list(AD_JOBS.values())
    assert (game.state, game.lb, game.cm, game.studio, game.watched) == ("home", 2, 3, 1, 0)
    assert game.backs == [] and restarts == [] and game.ad_taps == [] and game.plan == []
    assert nav.ads_unavailable == tuple(AD_JOBS)
    assert 9 * ads.AD_START_S + sum(ads.AD_RETRY_WAITS_S) < clock[0] < 900
    assert_clean(game)


def test_all_used_up_does_not_block(clock):
    """三项今天的次数都用完了：都不点广告按钮，不算出错，一分钟内结束，停在主界面。"""
    game = AdGame(clock, lb=0, cm=0, studio=0)
    nav = ad_nav(game, clock)
    assert nav.run_ads(list(AD_JOBS)) == []
    assert (game.state, game.watched, game.backs, game.ad_taps) == ("home", 0, [], [])
    assert clock[0] < 60
    assert_clean(game)


def test_ad_that_never_ends_is_abandoned(clock):
    """广告一直不给奖励：AD_TIMEOUT_S 后按返回键、点「关闭」放弃，这一项记为出错；最后回到主界面。"""
    game = AdGame(clock, plan=[[Seg(math.inf, "count")]])
    nav = ad_nav(game, clock)
    assert nav.run_ads(["lb"]) == [AD_JOBS["lb"]]
    assert clock[0] > AD_TIMEOUT_S
    assert (game.state, game.lb, game.watched, game.ad) == ("home", 2, 0, None)
    assert_clean(game)


def test_ad_that_cannot_be_closed_restarts_the_game(clock):
    """返回键、「关闭」都关不掉的广告：重启游戏。"""
    game = AdGame(clock, plan=[[Seg(math.inf, "count")]])
    game.press_back = lambda: game.backs.append(game.front_window())  # 返回键没反应
    restarts = []

    def restart():
        restarts.append(clock[0])
        game.ad, game.state = None, "home"

    nav = ad_nav(game, clock, restart_app=restart)
    assert nav.run_ads(["lb"]) == [AD_JOBS["lb"]]
    assert len(restarts) == 1 and game.state == "home"
    assert game.ad_taps == []


def test_leave_ad_only_touches_ads(clock):
    """上次看到一半停下的广告：先关掉。游戏在前台、前台是别的应用时什么都不按。"""
    game = AdGame(clock)
    nav = ad_nav(game, clock)
    nav.leave_ad()
    assert game.backs == []
    game.front_window = lambda: "com.android.launcher3/.Launcher"
    nav.leave_ad()
    assert game.backs == []
    game = AdGame(clock)
    game._start_ad("home", "home")
    game.ad = [Seg(math.inf, "granted", True)]
    nav = ad_nav(game, clock)
    nav.leave_ad()
    assert game.backs == [AD] and game.ad is None
    # adb 出错时不按
    nav.front_window = lambda: (_ for _ in ()).throw(subprocess.SubprocessError("adb"))
    nav.leave_ad()
    assert game.backs == [AD]


def test_ads_need_adb(clock):
    """没有接上 adb（读前台窗口、返回键）：报错，什么都不点。"""
    game = AdGame(clock)
    nav = ad_nav(game, clock)
    nav.front_window = None
    assert nav.run_ads(["lb", "recruit", "studio"]) == [AD_JOBS["recruit"], AD_JOBS["lb"], AD_JOBS["studio"]]
    assert game.taps == []
    with pytest.raises(ValueError, match="未知的看广告项目"):
        nav.run_ads(["story"])


# ------------------------------------------------------------ 录音室练习的 SKIP


def skip_taps(game):
    return [p for s, p in game.taps if s == "studio_detail" and math.dist(p, BTN_SKIP) < 30]


def test_studio_helpers(tmp_path):
    items = load_items("studio_map")
    assert studio_map(items) and not studio_detail(items)
    assert studio_panels(items) == list(STUDIO_MAPS["studio_map"])
    assert studio_panels(load_items("studio_map_right")) == list(STUDIO_MAPS["studio_map_right"])
    # 贴着画面边上、只露出一半的标志不点
    assert studio_panels([OcrItem(-20, 400, 100, 24, "05:33:39"), OcrItem(1200, 400, 100, 24, "05:33:39")]) == []
    assert same_panels([(326, 329), (681, 344)], [(324, 331), (683, 340)])
    assert not same_panels([(326, 329), (681, 344)], [(250, 329), (605, 344)])
    assert not same_panels([(326, 329)], [(326, 329), (681, 344)]) and not same_panels([], []) and not same_panels(None, [(1, 1)])
    detail, used = load_items("studio_detail"), load_items("studio_detail_used")
    for page in (detail, used):
        assert studio_detail(page) and not studio_map(page) and studio_level_text(page) == 34
        assert not studio_skip_dialog(page) and studio_skip_close(page) is None and studio_ad_button(page) is None
    assert studio_ads_left(detail) == 1 and studio_ads_left(used) is None
    # SKIP 弹窗：还有次数时才有「广告」按钮；「关闭」两种都有
    skip, skip_used = load_items("studio_skip"), load_items("studio_skip_used")
    for page in (skip, skip_used):
        assert studio_skip_dialog(page) and not studio_detail(page) and not studio_map(page)
        assert math.dist(center(studio_skip_close(page)), BTN_SKIP_CLOSE) < 5
    assert math.dist(center(studio_ad_button(skip)), BTN_SKIP_AD) < 5
    assert studio_ad_button(skip_used) is None
    assert studio_ad_button([it for it in skip if "观看1次广告" not in it.text]) is None
    assert not studio_skip_dialog(load_items("daily_studio_reward"))
    # 练习Lv 的数字：前面的锁和数字之间空着一段，只留数字
    img = np.zeros((720, 1280, 3), np.uint8)
    assert studio_level_roi(img) == ads.STUDIO_LV_ROI and studio_level_roi("detail") == ads.STUDIO_LV_ROI
    img[15:40, 1012:1028] = img[15:40, 1032:1050] = 255
    digits = studio_level_roi(img)
    img[15:40, 980:995] = 255  # 锁
    assert studio_level_roi(img) == digits and 995 < digits[0] <= 1012 and digits[0] + digits[2] == 1056
    big = studio_level_roi(np.repeat(np.repeat(img, 2, axis=0), 2, axis=1))
    assert abs(big[0] - digits[0]) <= 1
    assert studio_level_text([OcrItem(978, 11, 72, 35, "日 24")]) == 24
    assert studio_level_text([OcrItem(1010, 11, 40, 35, "7"), OcrItem(866, 55, 182, 19, "乐队等级达到C3时解锁")]) == 7
    assert studio_level_text(load_items("studio_map")) is None


def test_studio_band_templates(clock):
    """详情页左上角的标志按模板认乐队；没有标志（认不出）时是 None。"""
    game = AdGame(clock, state="studio_detail")
    nav = ad_nav(game, clock)
    assert set(ads.STUDIO_BAND_MODES) == {"highest", *STUDIO_BANDS} and ads.STUDIO_BANDS == tuple(STUDIO_BANDS)
    for band in STUDIO_BANDS:
        game.band = band
        nav.look()
        assert nav._studio_band() == band
        assert nav._studio_level(nav._items) == LEVELS[band]
    game.unknown = {"mygo"}
    game.band = "mygo"
    nav.look()
    assert nav._studio_band() is None
    assert studio_band("detail", {}) == (None, 0.0)


def test_studio_skip_on_highest_band(clock):
    """练习Lv 最高的乐队：地图拖到两头，每个乐队的详情页看一遍（两头都看得到的不重复算），回到最高的那个
    （一样高时是先看到的）点 SKIP →「广告」→ 广告 → 奖励 OK。「领取奖励」「道具」没点过。"""
    game = AdGame(clock)
    nav = ad_nav(game, clock)
    assert nav.run_ads(["studio"]) == []
    assert (game.state, game.studio, game.watched, game.band) == ("home", 0, 1, "mygo")
    assert game.opened == ["dumbrock", "mygo", "avemujica", "millsage", "mewtype", "avemujica", "mygo"]
    assert len(skip_taps(game)) == 1 and game.backs == [AD]
    assert [p for s, p in game.taps if s == "daily_studio_reward"] == [BTN_STUDIO_OK]
    assert_clean(game)


def test_studio_skip_on_highest_band_at_the_other_end(clock):
    """最高的乐队在地图的另一头、标志还没认出来：照样回到它那里用。"""
    game = AdGame(clock, levels={**LEVELS, "mewtype": 40})
    game.unknown = {"mewtype"}
    nav = ad_nav(game, clock)
    assert nav.run_ads(["studio"]) == []
    assert (game.state, game.watched, game.band, game.opened[-1]) == ("home", 1, "mewtype", "mewtype")
    assert_clean(game)


def test_studio_skip_on_chosen_band(clock):
    """指定乐队：一路看过去，看到它就停下来用，不管练习Lv。"""
    game = AdGame(clock)
    nav = ad_nav(game, clock)
    assert nav.run_ads(["studio"], studio_band="millsage") == []
    assert (game.state, game.studio, game.watched, game.band) == ("home", 0, 1, "millsage")
    assert game.opened == ["dumbrock", "mygo", "avemujica", "millsage"]
    assert_clean(game)
    with pytest.raises(ValueError, match="未知的乐队"):
        nav.run_ads(["studio"], studio_band="roselia")


def test_studio_skip_none_left_is_not_tapped(clock):
    """详情页的 SKIP 上没有「广告剩余N次」的角标（今天的用完了）：不点 SKIP，回主界面。"""
    game = AdGame(clock, studio=0)
    nav = ad_nav(game, clock)
    assert nav.run_ads(["studio"]) == []
    assert (game.state, game.opened, game.backs, skip_taps(game)) == ("home", ["dumbrock"], [], [])


def test_studio_skip_grey_ad_button_is_not_tapped(clock):
    """SKIP 弹窗里的「广告」按钮是灰的、没有角标：只点「关闭」。"""
    game = AdGame(clock)
    game.skip_grey = True
    nav = ad_nav(game, clock)
    assert nav.run_ads(["studio"], studio_band="dumbrock") == []
    assert [p for s, p in game.taps if s == "studio_skip"] == [BTN_SKIP_CLOSE]
    assert (game.state, game.studio, game.backs) == ("home", 1, [])
    assert_clean(game)


def test_studio_band_not_found_is_an_error(clock):
    """指定的乐队在地图上没找到（标志认不出）：报错，不用在别的乐队上。"""
    game = AdGame(clock)
    game.unknown = {"mewtype"}
    nav = ad_nav(game, clock)
    assert nav.run_ads(["studio"], studio_band="mewtype") == [AD_JOBS["studio"]]
    assert (game.state, game.studio, game.watched, skip_taps(game)) == ("home", 1, 0, [])


def test_studio_ad_not_shown_is_an_error(clock):
    """点了「广告」广告没放出来：关掉 SKIP 弹窗，这一项记为出错。"""
    game = AdGame(clock, plan=[None])
    nav = ad_nav(game, clock)
    assert nav.run_ads(["studio"], studio_band="dumbrock", waits=()) == [AD_JOBS["studio"]]
    assert [p for s, p in game.taps if s == "studio_skip"] == [BTN_SKIP_AD, BTN_SKIP_CLOSE]
    assert (game.state, game.studio, game.backs, game.watched) == ("home", 1, [], 0)


def test_studio_without_reward_is_an_error(clock):
    """广告关掉了但没有练习奖励（回到了 SKIP 弹窗）：关掉弹窗、报错，不当成领过了。"""
    game = AdGame(clock, plan=[[Seg(10, "count"), Seg(math.inf, "x")]])

    def close_without_reward():
        game.backs.append(game.front_window())
        game._end_ad(False)

    game.press_back = close_without_reward
    nav = ad_nav(game, clock)
    assert nav.run_ads(["studio"], studio_band="dumbrock") == [AD_JOBS["studio"]]
    assert (game.state, game.studio, game.watched, game.backs) == ("home", 1, 0, [AD])
    assert_clean(game)


def test_go_home_from_studio_skip(clock):
    """上次停在了 SKIP 弹窗：回主界面时只点「关闭」。"""
    game = AdGame(clock, state="studio_skip")
    game.band = "mygo"
    nav = ad_nav(game, clock)
    nav.go_home()
    assert game.state == "home"
    assert [p for s, p in game.taps if s == "studio_skip"] == [BTN_SKIP_CLOSE]
    assert_clean(game)


# ------------------------------------------------------------ 观看 CM 招募


def test_cm_recruit_watches_until_none_left(clock, tmp_path):
    """还剩 3 次：选中「观看CM招募」，看三次广告各招募一次，每次存一张截图；「再次招募」、别的招募按钮没点过。"""
    game = AdGame(clock)
    nav = ad_nav(game, clock)
    assert nav.run_ads(["recruit"]) == []
    assert (game.state, game.cm, game.watched) == ("home", 0, 3)
    shots = list((tmp_path / "recruit").glob("*.jpg"))
    # 截图的文件名带 _cm（统计页的「招募记录」靠它分免费招募和观看 CM 招募），存了的都记下来
    assert shots and all(p.name.endswith("_cm.jpg") for p in shots) and set(nav.recruit_shots) == set(shots)
    assert len([p for s, p in game.taps if s == "recruit_cm_confirm"]) == 3
    assert all(math.dist(p, BTN_CM_OK) < 30 for s, p in game.taps if s == "recruit_cm_confirm")
    assert [p for s, p in game.taps if s == "recruit_cm_result"] == [BTN_TOP, BTN_TOP]
    assert [p for s, p in game.taps if s == "recruit_cm_result_last"] == [BTN_TOP_LAST]
    assert game.backs == [AD] * 3
    assert_clean(game)


def test_cm_recruit_none_left_is_not_tapped(clock):
    """还剩 0 次（按钮是灰的）：选中横幅后什么都不点，回主界面。"""
    game = AdGame(clock, cm=0)
    nav = ad_nav(game, clock)
    assert nav.run_ads(["recruit"]) == []
    assert [p for s, p in game.taps if s == "recruit_cm"] == [(164, 40)]
    assert (game.state, game.backs) == ("home", [])


def test_cm_recruit_without_banner_is_skipped(clock):
    """招募页上没有「观看CM招募」：拖几次列表都没有就跳过，别的横幅、招募按钮不点。"""
    game = AdGame(clock)
    read = game.read
    game.read = lambda frame, roi=None: [it for it in read(frame) if it.text != "观看"]
    nav = ad_nav(game, clock)
    drags = []
    nav._drag = lambda *a, **k: drags.append(a)
    assert nav.run_ads(["recruit"]) == []
    assert len(drags) == ads.CM_FIND_TRIES - 1
    assert [p for s, p in game.taps if s == "recruit_cm_list"] == [(164, 40)]
    assert game.state == "home"


def test_cm_recruit_paid_dialog_is_cancelled(clock):
    """确认招募上写的不是看广告、不消耗星钻：点取消、报错，不点 OK。"""
    game = AdGame(clock)
    read = game.read

    def bad(frame, roi=None):
        items = read(frame)
        if game.state == "recruit_cm_confirm":
            items = [dataclasses.replace(it, text="将消耗200个星钻，进行1次招募。") if "进行1次招募" in it.text else it for it in items]
        return items

    game.read = bad
    nav = ad_nav(game, clock)
    assert nav.run_ads(["recruit"]) == [AD_JOBS["recruit"]]
    assert [p for s, p in game.taps if s == "recruit_cm_confirm"] == [BTN_CM_CANCEL]
    assert (game.state, game.cm, game.backs) == ("home", 3, [])


def test_cm_recruit_stops_when_gems_change(clock):
    """招募后星钻数变了：这一项记为出错，不再接着看。"""
    game = AdGame(clock)
    end = game._end_ad

    def spend(rewarded):
        end(rewarded)
        game.gems = "51,515"

    game._end_ad = spend
    nav = ad_nav(game, clock)
    assert nav.run_ads(["recruit"]) == [AD_JOBS["recruit"]]
    assert (game.state, game.watched) == ("home", 1)
    assert_clean(game)


def test_cm_recruit_without_reward_is_an_error(clock):
    """广告关掉了但没有招募（一直停在招募页）：报错，不当成招募过了。"""
    game = AdGame(clock, plan=[[Seg(10, "count"), Seg(math.inf, "x")]])

    def close_without_reward():  # 按返回键直接关掉，不给奖励
        game.backs.append(game.front_window())
        game._end_ad(False)

    game.press_back = close_without_reward
    nav = ad_nav(game, clock)
    assert nav.run_ads(["recruit"]) == [AD_JOBS["recruit"]]
    assert (game.state, game.cm, game.watched, game.backs) == ("home", 3, 0, [AD])


def test_recruit_show_helper():
    """招募演出里「认不出、也没有东西可点」的画面：新成员的展示画面算；有按钮、确认框、花星钻之类的字，在加载，
    或者是招募的各个页面、弹窗时都不算。"""
    items = load_items("recruit_new_card")
    assert classify(items) is Screen.UNKNOWN and recruit_show(Screen.UNKNOWN, items)
    # 背景上滚动的大字每一帧读出来都不一样，有时只剩右下角的卡名、成员名；演出转场时可能什么字都没有
    assert recruit_show(Screen.UNKNOWN, [OcrItem(985, 565, 177, 31, "朝着流星呐喊"), OcrItem(992, 603, 173, 61, "高松灯")])
    assert recruit_show(Screen.UNKNOWN, [])
    assert not recruit_show(Screen.LOGIN_BONUS, items)  # 认成了别的画面
    for text in ("OK", "确定", "关闭", "取消", "将消耗200个星钻", "再次招募", "购买", "NOW LOADING"):
        assert not recruit_show(Screen.UNKNOWN, [*items, OcrItem(600, 400, 160, 30, text)]), text
    assert not recruit_show(Screen.UNKNOWN, [*items, OcrItem(1180, 25, 60, 30, "SKIP")])  # 有 SKIP 就点 SKIP
    for name in (
        "recruit_cm_result",
        "recruit_cm_result_last",
        "recruit_result",
        "recruit_cm",
        "recruit_cm_list",
        "recruit_cm_confirm",
        "recruit_confirm",
        "recruit_gift",
        "recruit_used",
        "recruit_free",
        "recruit_ticket",
        "ad_failed",
        "video_skip_confirm",
    ):
        items = load_items(name)
        assert not recruit_show(classify(items), items), name


def test_cm_recruit_new_member_show_is_tapped_through(clock, caplog):
    """招募到新成员时的展示画面（整屏立绘，没有按钮，点一下才继续）：认不出一阵子后点画面上方，隔一阵再点，
    过去之后照常截图、点「招募TOP」。以前在这里干等到超时，最后要重启游戏。"""
    game = AdGame(clock, cm=1, show=2)
    nav = ad_nav(game, clock)
    with caplog.at_level("INFO", logger="ournotes_auto.nav.daily"):
        assert nav.run_ads(["recruit"]) == []
    assert (game.state, game.cm, game.watched) == ("home", 0, 1)
    assert [p for s, p in game.taps if s == "recruit_new_card"] == [daily.TAP_RECRUIT_SHOW] * 2
    first, second = game.show_times
    assert daily.RECRUIT_SHOW_S <= first - game.show_at < daily.RECRUIT_SHOW_S + 6  # 广告关掉后还要过一会儿才开始看画面
    assert daily.RECRUIT_SHOW_RETAP_S <= second - first < daily.RECRUIT_SHOW_RETAP_S + 3
    assert caplog.text.count("演出停在认不出的画面上") == 1
    assert [p for s, p in game.taps if s == "recruit_cm_result_last"] == [BTN_TOP_LAST]
    assert len(nav.recruit_shots) == 1
    assert_clean(game)


@pytest.mark.parametrize("window", [AD, STORE, None, "missing"])
def test_recruit_show_is_not_tapped_unless_game_ui_is_in_front(clock, window):
    """认不出的画面上，前台不是游戏自己的界面（广告又弹出来了、广告弹出的商店页）、读不到前台窗口、没接上 adb 时
    什么都不点，照旧等到超时报错。"""
    game = AdGame(clock, state="recruit_new_card", show=1)
    nav = ad_nav(game, clock)
    nav.front_window = None if window == "missing" else lambda: window
    with pytest.raises(daily.NavigationError, match="90s 没有回到招募页"):
        nav._settle_recruit(what="观看CM招募")
    assert game.taps == [] and game.state == "recruit_new_card"


def test_recruit_show_front_window_error_is_not_tapped(clock):
    """查前台窗口时 adb 出错：当成查不了，不点。"""
    game = AdGame(clock, state="recruit_new_card", show=1)
    nav = ad_nav(game, clock)

    def broken():
        raise subprocess.TimeoutExpired("adb", 15)

    nav.front_window = broken
    with pytest.raises(daily.NavigationError, match="没有回到招募页"):
        nav._settle_recruit(what="观看CM招募")
    assert game.taps == []


def test_recruit_show_taps_are_limited(clock):
    """点了也过不去：每隔 RECRUIT_SHOW_RETAP_S 点一次，最多 RECRUIT_SHOW_TAPS 次，之后照旧等到超时报错。"""
    game = AdGame(clock, state="recruit_new_card", show=99)
    nav = ad_nav(game, clock)
    with pytest.raises(daily.NavigationError, match="没有回到招募页"):
        nav._settle_recruit(what="观看CM招募", timeout_s=300)
    assert game.taps == [("recruit_new_card", daily.TAP_RECRUIT_SHOW)] * daily.RECRUIT_SHOW_TAPS
    gaps = [b - a for a, b in zip(game.show_times, game.show_times[1:])]
    assert all(daily.RECRUIT_SHOW_RETAP_S <= gap < daily.RECRUIT_SHOW_RETAP_S + 3 for gap in gaps)


@pytest.mark.parametrize("extra", ["OK", "取消", "消耗星钻", "购买", "NOW LOADING"])
def test_recruit_show_with_buttons_or_risky_text_is_not_tapped(clock, extra):
    """认不出的画面上有按钮、确认框、花星钻之类的字，或者在加载：不点，照旧等到超时报错。"""
    game = AdGame(clock, state="recruit_new_card", show=1)
    read = game.read
    game.read = lambda frame, roi=None: [*read(frame), OcrItem(600, 400, 160, 30, extra)]
    nav = ad_nav(game, clock)
    with pytest.raises(daily.NavigationError, match="没有回到招募页"):
        nav._settle_recruit(what="观看CM招募")
    assert game.taps == []


def test_go_home_from_cm_dialogs(clock):
    """上次停在了观看 CM 招募的确认招募 / 最后一次的招募结算：回主界面时只点「取消」/「招募TOP」。"""
    game = AdGame(clock, state="recruit_cm_confirm")
    nav = ad_nav(game, clock)
    nav.go_home()
    assert game.state == "home"
    assert [p for s, p in game.taps if s == "recruit_cm_confirm"] == [BTN_CM_CANCEL]
    game = AdGame(clock, state="recruit_cm_result_last", cm=0)
    nav = ad_nav(game, clock)
    nav.go_home()
    assert game.state == "home"
    assert [p for s, p in game.taps if s == "recruit_cm_result_last"] == [BTN_TOP_LAST]


def test_run_ads_order(clock):
    """三项都选：先招募，再恢复 LB，最后录音室练习的 SKIP。"""
    game = AdGame(clock, lb=1, cm=1)
    nav = ad_nav(game, clock)
    assert list(AD_JOBS) == ["recruit", "lb", "studio"]
    assert nav.run_ads(["studio", "lb", "recruit"], studio_band="dumbrock") == []
    assert (game.state, game.lb, game.cm, game.studio, game.watched) == ("home", 0, 0, 0, 3)
    states = [s for s, _ in game.taps]
    assert states.index("recruit_cm_list") < states.index("lb_recover_ad_left") < states.index("studio_detail")
    assert_clean(game)


def test_chores_recorded(clock, tmp_path):
    """每看成一个广告在日常记录里记一行（观看CM招募的带截图的文件名）；今天的次数用完了、广告放不出来的不记。"""
    from ournotes_auto.records import RecordStore

    store = RecordStore(tmp_path / "data")
    game = AdGame(clock, lb=2, cm=2)
    nav = ad_nav(game, clock, chores=store)
    assert nav.run_ads(list(AD_JOBS)) == []
    got = store.chore_history(limit=None)
    assert [c.kind for c in got] == ["ad_recruit", "ad_recruit", "ad_lb", "ad_lb", "ad_studio"] and got == nav.chores
    assert [c.shot for c in got[:2]] == [p.name for p in nav.recruit_shots] and all(c.shot.endswith("_cm.jpg") for c in got[:2])
    assert [c.shot for c in got[2:]] == ["", "", ""]

    game = AdGame(clock, lb=0, cm=0, studio=0)
    nav = ad_nav(game, clock, chores=store)
    assert nav.run_ads(list(AD_JOBS)) == [] and nav.chores == []
    game = AdGame(clock, plan=[None, None, None])
    nav = ad_nav(game, clock, chores=store)
    assert nav.run_ads(list(AD_JOBS), waits=()) == list(AD_JOBS.values()) and nav.chores == []
    assert len(store.chore_history(limit=None)) == 5


# ------------------------------------------------------------ 没要到广告（「广告获取失败。请稍后再试。」）


def pending(store, clock):
    return store.ads_pending(PACKAGE, game_day(WALL + clock[0]))


def test_ad_failed_popup_fixture():
    items = load_items("ad_failed")
    assert classify(items) is Screen.OK_POPUP
    assert ads.find(items, ads.AD_FAILED_TEXT) is not None
    # 别的画面上没有这几个字
    for name in ("lb_recover_ad", "lb_recovered", "recruit_cm_confirm", "studio_skip", "home"):
        assert ads.find(load_items(name), ads.AD_FAILED_TEXT) is None, name


def test_ad_failed_popup_is_dismissed_and_retried(clock, tmp_path):
    """游戏说「广告获取失败」：马上点掉 OK（不干等 AD_START_S）、关掉恢复LIVE BOOST，隔一阵再做；这次放出来了就照常看完，
    不算出错、存档里也不留。"""
    store = RecordStore(tmp_path)
    game = AdGame(clock, plan=[FAIL])
    nav = ad_nav(game, clock, chores=store)
    assert nav.run_ads(["lb"], waits=(60.0,)) == []
    assert (game.state, game.lb, game.watched, nav.ads_unavailable) == ("home", 0, 2, ())
    assert len(game.failed_for) == 1 and game.failed_for[0] < 5
    assert [p for s, p in game.taps if s == "ad_failed"] == [BTN_FAILED_OK]
    assert pending(store, clock) is None and len(store.chore_history(limit=None)) == 2
    assert_clean(game)


def test_ad_unavailable_after_retries_is_saved_for_later(clock, tmp_path):
    """隔一阵再试了两遍还是「广告获取失败」：记为出错，记进存档（挂机过一阵再试），没有用掉次数。"""
    store = RecordStore(tmp_path)
    game = AdGame(clock, plan=[FAIL] * 3)
    nav = ad_nav(game, clock, chores=store)
    assert nav.run_ads(["lb"]) == [AD_JOBS["lb"]]
    assert (game.state, game.lb, game.watched, game.plan, nav.ads_unavailable) == ("home", 2, 0, [], ("lb",))
    assert len(game.failed_for) == 3 and max(game.failed_for) < 5
    assert sum(ads.AD_RETRY_WAITS_S) < clock[0] < sum(ads.AD_RETRY_WAITS_S) + 120
    got = pending(store, clock)
    assert (got["jobs"], got["studio_band"], got["tries"]) == (["lb"], "highest", 0)
    assert got["next"] == pytest.approx(WALL + clock[0] + ads.AD_LATER_GAPS_S[0])
    assert store.chore_history(limit=None) == []
    assert_clean(game)


def test_ad_failed_does_not_block_other_jobs(clock, tmp_path):
    """招募没要到广告：先做完恢复 LB、录音室 SKIP，再回头做招募。"""
    store = RecordStore(tmp_path)
    game = AdGame(clock, plan=[FAIL])
    nav = ad_nav(game, clock, chores=store)
    assert nav.run_ads(list(AD_JOBS), waits=(60.0,)) == []
    assert (game.state, game.lb, game.cm, game.studio, game.watched) == ("home", 0, 0, 0, 6)
    kinds = [c.kind for c in store.chore_history(limit=None)]
    assert kinds == ["ad_lb", "ad_lb", "ad_studio", "ad_recruit", "ad_recruit", "ad_recruit"]
    assert pending(store, clock) is None
    assert_clean(game)


def test_cm_ad_failed_cancels_confirm(clock):
    """观看CM招募没要到广告：点掉「广告获取失败」的 OK 后，下面的「确认招募」点取消（没有招募、星钻没动）。"""
    game = AdGame(clock, plan=[FAIL])
    nav = ad_nav(game, clock)
    assert nav.run_ads(["recruit"], waits=()) == [AD_JOBS["recruit"]]
    assert (game.state, game.cm, game.watched, game.gems, nav.ads_unavailable) == ("home", 3, 0, "51,715", ("recruit",))
    assert [p for s, p in game.taps if s == "recruit_cm_confirm"] == [BTN_CM_OK, BTN_CM_CANCEL]
    assert_clean(game)


def test_studio_ad_failed_closes_skip(clock):
    """录音室练习 SKIP 没要到广告：点掉 OK 后关掉 SKIP 弹窗，「道具」不碰。"""
    game = AdGame(clock, plan=[FAIL])
    nav = ad_nav(game, clock)
    assert nav.run_ads(["studio"], studio_band="dumbrock", waits=()) == [AD_JOBS["studio"]]
    assert [p for s, p in game.taps if s == "studio_skip"] == [BTN_SKIP_AD, BTN_SKIP_CLOSE]
    assert (game.state, game.studio, game.watched, nav.ads_unavailable) == ("home", 1, 0, ("studio",))
    assert_clean(game)


def test_ad_failed_while_stopping(clock):
    """隔一阵再试之前被停掉：照常抛出，不接着做。"""
    game = AdGame(clock, plan=[FAIL])
    nav = ad_nav(game, clock)
    sleep = nav._sleep

    def stopping(s):
        if s >= 60:
            nav.stop.set()
            raise daily.NavigationError("已停止")
        sleep(s)

    nav._sleep = stopping
    with pytest.raises(daily.NavigationError, match="已停止"):
        nav.run_ads(["lb"])
    assert game.watched == 0 and game.plan == []


def test_retry_ads_later(clock, tmp_path):
    """挂机用的再试：没到时间什么都不做；到了时间只做存档里记着的项目，还是没要到就把下次再试的时间往后推，
    要到了就从存档里去掉。换了一个导航器（另一个任务的进程）也读得到。"""
    store = RecordStore(tmp_path)
    game = AdGame(clock, plan=[FAIL])
    nav = ad_nav(game, clock, chores=store)
    assert nav.run_ads(["lb", "studio"], waits=()) == [AD_JOBS["lb"]]  # 录音室 SKIP 照常做成了
    assert (game.lb, game.studio, game.watched) == (2, 0, 1)
    first = pending(store, clock)["next"]

    nav = ad_nav(game, clock, chores=store)  # 之后的挂机
    now = WALL + clock[0]
    taps = len(game.taps)
    assert nav.ads_next(now) == first and nav.ads_due(now) == [] and nav.retry_ads(now) == []
    assert len(game.taps) == taps

    clock[0] += ads.AD_LATER_GAPS_S[0] + 1
    now = WALL + clock[0]
    assert nav.ads_due(now) == ["lb"]
    game.plan = [FAIL]
    assert nav.retry_ads(now) == [AD_JOBS["lb"]]
    got = pending(store, clock)
    assert (got["jobs"], got["tries"]) == (["lb"], 1) and got["next"] == pytest.approx(now + ads.AD_LATER_GAPS_S[1])
    assert (game.state, game.lb, game.watched, len(game.failed_for)) == ("home", 2, 1, 2)
    assert nav.ads_due(WALL + clock[0]) == []  # 刚试过，不马上又试

    clock[0] += ads.AD_LATER_GAPS_S[1] + 1
    now = WALL + clock[0]
    assert nav.retry_ads(now) == []
    assert (game.state, game.lb, game.studio, game.watched) == ("home", 0, 0, 3)
    assert pending(store, clock) is None and nav.ads_next(WALL + clock[0]) is None
    assert_clean(game)


def test_retry_ads_gaps_grow_and_expire_with_game_day(clock, tmp_path):
    """一直要不到：间隔越来越长（到最后一档不再变长）；过了游戏的日期变更就不再试（新的一天由「看广告」任务重新做）。"""
    store = RecordStore(tmp_path)
    game = AdGame(clock, plan=[FAIL] * 20)
    nav = ad_nav(game, clock, chores=store)
    clock[0] = 6 * 3600.0  # 北京时间 4:00，游戏的一天刚开始
    nav.run_ads(["lb"], waits=())
    gaps = []
    for _ in range(len(ads.AD_LATER_GAPS_S) + 1):
        clock[0] = pending(store, clock)["next"] - WALL + 1
        now = WALL + clock[0]
        assert nav.retry_ads(now) == [AD_JOBS["lb"]]
        gaps.append(round(pending(store, clock)["next"] - now))
    assert gaps == [3600, 7200, 14400, 14400, 14400] and game.watched == 0
    tomorrow = WALL + 30 * 3600.0  # 第二天 4:00
    assert nav.ads_next(tomorrow) is None and nav.ads_due(tomorrow) == [] and nav.retry_ads(tomorrow) == []
    assert store.ads_pending(PACKAGE, game_day(WALL + clock[0])) is not None  # 旧的留在存档里，只是不再用


def test_pending_ads_keep_jobs_not_run_this_time(clock, tmp_path):
    """存档里记着招募、恢复 LB 没要到广告；之后只运行了恢复 LB（做成了）：招募照旧留着。"""
    store = RecordStore(tmp_path)
    game = AdGame(clock, plan=[FAIL, FAIL])
    nav = ad_nav(game, clock, chores=store)
    assert nav.run_ads(["recruit", "lb"], waits=()) == [AD_JOBS["recruit"], AD_JOBS["lb"]]
    assert pending(store, clock)["jobs"] == ["recruit", "lb"]
    assert nav.run_ads(["lb"], waits=()) == []
    assert pending(store, clock)["jobs"] == ["recruit"] and game.lb == 0


def test_ads_later_needs_adb_and_store(clock, tmp_path):
    """没有接上读前台窗口 / 返回键、没有存档的导航器：不再试，也不报错。"""
    store = RecordStore(tmp_path)
    game = AdGame(clock, plan=[FAIL])
    ad_nav(game, clock, chores=store).run_ads(["lb"], waits=())
    later = WALL + clock[0] + ads.AD_LATER_GAPS_S[0] + 1
    bare = GameNavigator(Config(), game, game, game, settle_s=0, chores=store)
    assert bare.ads_next(later) is None and bare.ads_due(later) == [] and bare.retry_ads(later) == []
    nav = ad_nav(game, clock)  # 没有存档
    assert nav.ads_next(later) is None and nav.retry_ads(later) == []
    game = AdGame(clock, plan=[FAIL])
    assert ad_nav(game, clock).run_ads(["lb"], waits=()) == [AD_JOBS["lb"]]


def test_ad_failed_text_inside_an_ad_is_not_tapped(clock):
    """截图上有「广告获取失败」几个字、可前台已经是广告了（截图之后广告才弹出来）：不点。"""
    game = AdGame(clock, plan=[FAIL])
    nav = ad_nav(game, clock)
    game.failed_at = 0.0
    fronts = iter([AD])
    nav._front = lambda: next(fronts)
    assert nav._ad_failed(GAME, nav._ad_items()) is False and game.taps == []
    nav._front = lambda: GAME
    assert nav._ad_failed(GAME, nav._ad_items()) is True and game.taps == [("ad_failed", BTN_FAILED_OK)]


# ------------------------------------------------------------ 跳过广告的权益（#146）


def test_ad_got_helpers():
    """奖励到手的画面：恢复 LB 的「已恢复LIVE BOOST。」、录音室的「录音室练习奖励」/ LEVEL UP、招募的演出 / 结算 / 赠品；
    点「观看」之前的弹窗、「广告获取失败」都不算。"""
    assert lb_ad_got(load_items("lb_recovered"))
    for name in ("lb_recover_ad", "lb_recover_ad_left", "home", "ad_failed", "daily_studio_reward", "recruit_cm_result"):
        assert not lb_ad_got(load_items(name)), name
    assert studio_ad_got(load_items("daily_studio_reward")) and studio_ad_got(load_items("daily_studio_levelup"))
    for name in ("studio_skip", "studio_skip_used", "studio_detail", "studio_map", "ad_failed", "lb_recovered"):
        assert not studio_ad_got(load_items(name)), name
    for name in ("recruit_ticket", "recruit_cm_result", "recruit_cm_result_last", "recruit_gift"):
        assert cm_recruit_got(load_items(name)), name  # 招募演出（右上角 SKIP）、招募结算、招募赠品
    for name in ("recruit_cm_confirm", "recruit_cm", "recruit_cm_list", "ad_failed", "home", "lb_recovered"):
        assert not cm_recruit_got(load_items(name)), name
    # 「确认招募」还开着就不算（哪怕画面上读到了 SKIP）
    assert not cm_recruit_got([*load_items("recruit_cm_confirm"), OcrItem(1180, 25, 60, 30, "SKIP")])


def test_lb_ad_skipped_by_pass(clock, tmp_path, caplog):
    """#146：游戏里开通了跳过广告，点了「观看」不放广告、「已恢复LIVE BOOST。」直接弹出来（前台一直是游戏）：
    几秒内认出来，照常点 OK、记一次，不干等 AD_START_S 再报「广告没有放出来」；返回键一次都没按。"""
    store = RecordStore(tmp_path)
    game = AdGame(clock, plan=[PASS, PASS])
    nav = ad_nav(game, clock, chores=store)
    with caplog.at_level("INFO", logger="ournotes_auto.nav.ads"):
        assert nav.run_ads(["lb"]) == []
    assert (game.state, game.lb, game.passed, game.backs, nav.ads_unavailable) == ("home", 0, 2, [], ())
    assert [c.kind for c in store.chore_history(limit=None)] == ["ad_lb", "ad_lb"]
    assert caplog.text.count("没有放广告，奖励直接到手了") == 2 and "广告没有放出来" not in caplog.text
    assert clock[0] < ads.AD_START_S * 2  # 两次加起来也不到原来干等一次的两倍，远小于报错前等的时间
    assert [p for s, p in game.taps if s == "lb_recovered"] == [(640, 570)] * 2
    assert pending(store, clock) is None
    assert_clean(game)


def test_all_ads_skipped_by_pass(clock, tmp_path):
    """三项都不放广告、奖励直接到手：招募照常存截图、点「招募TOP」，录音室照常点 OK；别的按钮没点，返回键没按。"""
    store = RecordStore(tmp_path)
    game = AdGame(clock, plan=[PASS] * 6)
    nav = ad_nav(game, clock, chores=store)
    assert nav.run_ads(list(AD_JOBS), studio_band="mygo") == []
    assert (game.state, game.lb, game.cm, game.studio, game.passed, game.backs) == ("home", 0, 0, 0, 6, [])
    kinds = [c.kind for c in store.chore_history(limit=None)]
    assert kinds == ["ad_recruit"] * 3 + ["ad_lb"] * 2 + ["ad_studio"]
    assert len(nav.recruit_shots) == 3
    assert [p for s, p in game.taps if s == "recruit_cm_result"] == [BTN_TOP, BTN_TOP]
    assert [p for s, p in game.taps if s == "recruit_cm_result_last"] == [BTN_TOP_LAST]
    assert_clean(game)


def test_pass_and_real_ads_mixed(clock, caplog):
    """有的次数放广告、有的不放：各按各的处理（不是靠「没要到广告」之后再试才做完的）。"""
    game = AdGame(clock, plan=[PASS, SIMPLE])
    nav = ad_nav(game, clock)
    with caplog.at_level("INFO", logger="ournotes_auto.nav.ads"):
        assert nav.run_ads(["lb"]) == []
    assert (game.state, game.lb, game.passed, game.watched, game.backs) == ("home", 0, 1, 2, [AD])
    assert caplog.text.count("没有放广告，奖励直接到手了") == 1 and "没要到广告" not in caplog.text
    assert_clean(game)


def test_reward_screen_must_stay_to_count(clock):
    """保底：奖励到手的画面只闪了一帧（认错了）不算，照旧等到 AD_START_S 当成没放出来。"""
    game = AdGame(clock, state="lb_recover_ad", plan=[None])
    read, calls = game.read, [0]

    def flicker(frame, roi=None):
        calls[0] += 1
        return load_items("lb_recovered") if calls[0] == 2 else read(frame)

    game.read = flicker
    nav = ad_nav(game, clock)
    assert nav._watch_ad(GAME, "看广告恢复LB", lb_ad_got) is False
    assert ads.AD_START_S < clock[0] < ads.AD_START_S + 10 and game.taps == [] and game.backs == []


def test_reward_screen_with_ad_in_front_does_not_count(clock):
    """保底：这一帧像是奖励到手了，可截完图前台已经是广告（广告正好弹出来，广告右上角也常有「跳过」）：不算。"""
    game = AdGame(clock, state="lb_recovered")
    nav = ad_nav(game, clock)
    fronts = [0]

    def front():  # 每一轮开头读到的是游戏，截完图再读是广告
        fronts[0] += 1
        return GAME if fronts[0] % 2 else AD

    nav._front = front
    assert nav._watch_ad(GAME, "看广告恢复LB", lb_ad_got) is False
    assert clock[0] > ads.AD_START_S and game.taps == []
    # 不传 got（旧的调用方式）时和以前一样
    nav._front = lambda: GAME
    clock[0] = 0.0
    assert nav._watch_ad(GAME, "看广告恢复LB") is False and clock[0] > ads.AD_START_S


def test_reward_not_granted_after_skip_is_still_an_error(clock):
    """保底：就算把没放广告的情况错认成奖励到手了，各项目之后照旧自己核对：录音室的奖励没弹出来就关掉 SKIP 弹窗、报错，
    不会记成做过了。"""
    game = AdGame(clock, state="studio_skip", plan=[None])
    game.band = "mygo"
    nav = ad_nav(game, clock)
    nav._watch_ad = lambda window, what, got=None: True  # 假装认成了奖励到手
    nav._watch_or_fail(GAME, "录音室练习SKIP", "「广告」", nav._studio_close_skip, studio_ad_got)
    with pytest.raises(daily.NavigationError, match="没有拿到练习奖励"):
        nav._studio_done("录音室练习SKIP")
    assert (game.studio, game.state, game.backs) == (1, "studio_detail", [])
