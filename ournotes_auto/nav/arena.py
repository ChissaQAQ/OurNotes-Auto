"""激奏舞台（多人对局）：转盘定歌、只打 EXPERT，按名次加减段位分（AP）。

画面顺序（坐标均为 1280x720 设计尺寸）::

    演出首页 -[激奏舞台]-> 激奏舞台首页（右上角 LB，乐队标志右边 ▶▶ 换乐队，下面「消耗LB」「LIVE START」）
    -[LIVE START]-> 匹配确认 -[OK]-> 正在匹配（可取消）→ 匹配房间（等成员到齐，不能取消）→ 成员已确定 → 转盘
    → 难度选择（约 28 秒倒计时，右边封面下是曲名）-[EXPERT → 准备完毕]-> 等其他人准备好 → 演奏（没有暂停按钮）
    → LIVE CLEAR → 排名 -[下一步]->（每日奖励、段位提升、首次段位达成奖励）→ 结果「结果 3rd AP+15」-[下一步]->
    → 排名（判定数）-[下一步]-> 结算 → 和自由演出一样 -[再次演出]-> 激奏舞台首页

- 首页的「消耗LB」打开的是和乐队确认页一样的 LIVE BOOST消耗设置，LB 的处理同自由演出
  （:meth:`~ournotes_auto.nav.navigator.GameNavigator._prepare_lb`），在难度选择页记一次「全部消耗」。
- 难度选择页上也有「消耗LB」，不点。
- 演奏画面左上角有 SCORE，看到它时歌还没开始（约 2 秒后第一个音符），从这时开始首音符同步；
  没有暂停按钮，所以不能从头重试。
- 结算页的判定数和自由演出的读法一样。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2
import numpy as np

from ..player.guard import load_template
from ..result_reader import OcrItem
from ..runner import ArenaOutcome, SongLabel
from .jacket import crop_jacket
from .screens import (
    ARENA_AP_ROI,
    ARENA_BANDS,
    OK_POPUP_BLOCK,
    RELOGIN_SCREENS,
    Rect,
    Screen,
    arena_ap_text,
    arena_ap_total,
    arena_placement,
    arena_result,
    arena_song,
    find,
)

logger = logging.getLogger(__name__)

BTN_ARENA_ENTRY = (1141, 349)  # 演出首页：激奏舞台（下面的「激奏演出」不点）
BTN_BAND_NEXT = (1125, 300)  # 激奏舞台首页：乐队标志右边的 ▶▶
BTN_ARENA_START = (1140, 640)  # 激奏舞台首页：LIVE START
BTN_MATCH_OK = (782, 660)  # 匹配确认：左取消右 OK
MATCH_OK_ROI: Rect = (640, 630, 300, 60)
BTN_MATCH_CANCEL = (498, 660)
MATCH_CANCEL_ROI: Rect = (340, 630, 300, 60)
MATCHING_CANCEL_ROI: Rect = (440, 540, 400, 60)  # 「正在匹配」弹窗的取消（匹配房间里没有）
BTN_ARENA_EXPERT = (1200, 545)  # 难度选择
BTN_ARENA_READY = (1140, 640)  # 难度选择：准备完毕
ARENA_READY_ROI: Rect = (1000, 610, 280, 60)
BTN_ARENA_RESULT_NEXT = (640, 660)  # 结果弹窗：下一步
ARENA_RESULT_NEXT_ROI: Rect = (440, 630, 400, 60)
BTN_ARENA_RANKING_NEXT = (1140, 660)  # 排名：下一步
# 段位提升没有按钮，点一下继续：升到新段位（BEGINNER→BRONZE）时点上面的乐队标志没反应、点下面空白处才行，两处轮流点
TAP_ARENA_RANK_UP = ((640, 600), (640, 300))
# 认不出的段位提升（#101，黄金以上卡住）：结算途中有「RANK UP」、没有 OK（羁绊等级、玩家等级的 RANK UP 都有 OK）的画面
# 停了这么久，也当成段位提升点
ARENA_RANK_UP_GUESS_S = 3.0
# 段位提升的「RANK UP」是上半部的大字（只在这里找：T.G.W CARD 页下面也有「RANK UP까지」这样的进度文字）
RANK_UP_TITLE_ROI: Rect = (0, 0, 1280, 360)
PLAY_SCORE_ROI: Rect = (0, 0, 300, 170)  # 演奏画面左上角的 SCORE
ARENA_BAND_ROI: Rect = (870, 285, 200, 90)  # 首页的乐队标志（模板 arena_band_<乐队>.png 在里面找）
ARENA_BAND_MIN = 0.6
ARENA_RETAP_S = 3.0  # 点了 LIVE START 还停在首页这么久就再点（匹配中途会闪一下首页，点过 OK 后多等一会儿）
ARENA_CONFIRMED_RETAP_S = 8.0
ARENA_MAX_RETAPS = 3
ARENA_SCREENS = frozenset(
    (
        Screen.ARENA_MATCH_CONFIRM,
        Screen.ARENA_MATCHING,
        Screen.ARENA_DIFFICULTY,
        Screen.ARENA_RESULT,
        Screen.ARENA_RANKING,
        Screen.ARENA_RANK_UP,
    )
)
# 演奏后到结算页之间会出现的其他画面（每日奖励、首次段位达成奖励、羁绊等级提升等），都点掉
ARENA_RESULT_POPUPS = frozenset(
    (
        Screen.LIVE_END,
        Screen.ACHIEVEMENT,
        Screen.POPUP,
        Screen.GRADE_UP,
        Screen.REWARD,
        Screen.OK_POPUP,
        Screen.BOND_UP,
        Screen.RANK_UP,
    )
)


def rank_up_like(items: list[OcrItem]) -> bool:
    """像段位提升：上半部有「RANK UP」，没有 OK，不在演奏中（左上角 SCORE），也没有购买、星钻之类的字。"""
    return (
        find(items, "ANKUP", RANK_UP_TITLE_ROI) is not None
        and not find(items, "OK", exact=True)
        and not find(items, "SCORE", PLAY_SCORE_ROI)
        and not any(find(items, text) for text in OK_POPUP_BLOCK)
    )


class ArenaMixin:
    """:class:`~ournotes_auto.nav.navigator.GameNavigator` 的激奏舞台操作。

    用到它的 ``look`` ``tap`` ``ocr`` ``_sleep`` ``_fail`` ``_button`` ``_common_step`` ``_prepare_lb`` 等。
    """

    _lb_cost: int | None
    _lb_all: bool
    _arena_lb: int | None
    _rank_up_taps: int
    _band_templates: dict[str, np.ndarray] | None

    def _enter_arena(self, items: list[OcrItem]) -> bool:
        """演出首页：点「激奏舞台」。"""
        self.tap(self._button(items, "激奏舞台", BTN_ARENA_ENTRY), "激奏舞台")
        return True

    def _arena_step(self, screen: Screen, items: list[OcrItem]) -> bool:
        """不在对局中时遇到激奏舞台对局的画面（上次中途停下等）：匹配确认点取消、正在匹配能取消就取消，
        已经进了对局就跟着走完（难度选择点准备完毕，结果、排名点下一步）。做了操作返回 True。"""
        if screen is Screen.ARENA_MATCH_CONFIRM:
            self.tap(self._button(items, "取消", BTN_MATCH_CANCEL, MATCH_CANCEL_ROI), "取消（匹配）")
        elif screen is Screen.ARENA_MATCHING:
            if not (it := find(items, "取消", MATCHING_CANCEL_ROI, exact=True)):
                return False  # 匹配房间：等对局开始
            self.tap((round(it.x + it.w / 2), round(it.y + it.h / 2)), "取消匹配")
        elif screen is Screen.ARENA_DIFFICULTY:
            if not (it := find(items, "准备完毕", ARENA_READY_ROI, exact=True)):
                return False
            self.tap((round(it.x + it.w / 2), round(it.y + it.h / 2)), "准备完毕")
        elif screen is Screen.ARENA_RESULT:
            self.tap(self._button(items, "下一步", BTN_ARENA_RESULT_NEXT, ARENA_RESULT_NEXT_ROI), "下一步")
        elif screen is Screen.ARENA_RANKING:
            self.tap(self._button(items, "下一步", BTN_ARENA_RANKING_NEXT), "下一步")
        elif screen is Screen.ARENA_RANK_UP:
            self.tap(TAP_ARENA_RANK_UP[self._rank_up_taps % len(TAP_ARENA_RANK_UP)], "RANK UP")
            self._rank_up_taps += 1
        else:
            return False
        return True

    # ------------------------------------------------------------ 乐队

    def _match_band(self, frame: np.ndarray) -> str | None:
        if self._band_templates is None:
            self._band_templates = {}
            for band in ARENA_BANDS:
                tpl = load_template(Path(f"resource/image/arena_band_{band}.png"))
                if tpl is not None:
                    self._band_templates[band] = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
            if not self._band_templates:
                logger.warning("没有激奏舞台的乐队模板（resource/image/arena_band_*.png），认不出选的是哪个乐队")
        region = crop_jacket(frame, ARENA_BAND_ROI)
        size = ARENA_BAND_ROI[2:]
        if region.shape[1::-1] != size:
            region = cv2.resize(region, size, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        scores = {b: float(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED).max()) for b, t in self._band_templates.items()}
        best = max(scores, key=scores.get, default=None)
        logger.debug("激奏舞台乐队匹配度：%s", {b: round(s, 2) for b, s in scores.items()})
        return best if best is not None and scores[best] >= ARENA_BAND_MIN else None

    def arena_band(self) -> str | None:
        """激奏舞台首页上现在选的乐队（:data:`ARENA_BANDS` 之一），不在首页或认不出时为 None（换乐队的动画中多看几次）。"""
        for _ in range(3):
            screen, _ = self.look()
            if screen is Screen.ARENA_TOP and isinstance(self._frame, np.ndarray):
                if (band := self._match_band(self._frame)) is not None:
                    return band
            self._sleep(0.5)
        return None

    def arena_select_band(self, band: str, timeout_s: float = 90.0) -> None:
        """回到激奏舞台首页，按 ▶▶ 换到 ``band``，每换一次核对一次；转了一圈还没换到时报错。"""
        for _ in range(len(ARENA_BANDS) + 1):
            self.ensure_band_confirm(timeout_s=timeout_s)
            now = self.arena_band()
            if now == band:
                return
            if now is None:
                raise self._fail("激奏舞台首页认不出现在选的是哪个乐队")
            self.tap(BTN_BAND_NEXT, "▶▶")
            self._sleep(self.settle_s)
        raise self._fail(f"激奏舞台首页换不到乐队 {band}")

    # ------------------------------------------------------------ 对局

    def start_arena(self, lb_short: str = "zero", timeout_s: float = 300.0) -> SongLabel:
        """激奏舞台首页：（按配置设好 LB 消耗后）LIVE START → 匹配确认 OK → 等匹配、转盘定歌，
        在难度选择页读出曲名和 EXPERT 等级后返回（倒计时约 28 秒，要赶紧认歌、准备）。

        LB 的处理同 :meth:`start_live`（``lb_short`` 也一样）。``timeout_s`` 内没匹配到：还在「正在匹配」就取消，报错。
        """
        want, auto_pick = self._prepare_lb(lb_short)
        self.tap(BTN_ARENA_START, "LIVE START")
        deadline = time.monotonic() + timeout_s
        busy_at = time.monotonic()  # 上次点按钮、或看到对局在进行的时刻
        confirmed = False
        retaps = recovers = 0
        items: list[OcrItem] = []
        screen = Screen.UNKNOWN
        while time.monotonic() < deadline:
            self._sleep(0.5)
            screen, items = self.look()
            now = time.monotonic()
            if screen is Screen.ARENA_DIFFICULTY:
                self._arena_lb = -1 if self._lb_all else self._lb_cost
                self._lb_started()
                return self._arena_song(items)
            if screen is Screen.ARENA_MATCH_CONFIRM:
                self.tap(self._button(items, "OK", BTN_MATCH_OK, MATCH_OK_ROI), "OK（开始匹配）")
                confirmed = True
                busy_at = now
            elif screen is Screen.ARENA_TOP:
                if now - busy_at > (ARENA_CONFIRMED_RETAP_S if confirmed else ARENA_RETAP_S):
                    if retaps >= ARENA_MAX_RETAPS:
                        raise self._fail(f"点了 {ARENA_MAX_RETAPS + 1} 次 LIVE START 都没有开始匹配")
                    retaps += 1
                    self.tap(BTN_ARENA_START, "LIVE START")
                    busy_at = now
                    confirmed = False
            elif screen is Screen.LB_RECOVER:
                recovers = self._on_lb_recover(items, want, auto_pick, lb_short, recovers)
                self.tap(BTN_ARENA_START, "LIVE START")
                busy_at = time.monotonic()
                confirmed = False
            elif screen in (Screen.ARENA_MATCHING, Screen.UNKNOWN):
                busy_at = now  # 匹配中、转盘
            elif screen in RELOGIN_SCREENS:
                raise self._fail("游戏回到了标题画面，没有开始对局")
            elif not self._common_step(screen, items):
                raise self._fail("开始激奏舞台匹配后出现意外画面")
        if screen is Screen.ARENA_MATCHING and (it := find(items, "取消", MATCHING_CANCEL_ROI, exact=True)):
            self.tap((round(it.x + it.w / 2), round(it.y + it.h / 2)), "取消匹配")
            self._sleep(self.settle_s)
        raise self._fail(f"{timeout_s:.0f}s 内没有匹配到对局")

    def _arena_song(self, items: list[OcrItem]) -> SongLabel:
        """难度选择页右边封面下的曲名和 EXPERT 等级：转盘刚停时可能还在动画中，连续两次读数一致才采用。"""
        prev = None
        for _ in range(8):
            title, level = arena_song(items)
            if title and (title, level) == prev:
                break
            prev = (title, level)
            self._sleep(0.3)
            screen, items = self.look()
            if screen is not Screen.ARENA_DIFFICULTY:
                break
        else:
            logger.debug("难度选择页读数不稳定，采用最后一次")
        title, level = prev
        logger.debug("激奏舞台：曲名 %r，EXPERT 等级 %s", title, level)
        return SongLabel(title, "expert", level)

    def arena_ready(self, timeout_s: float = 150.0) -> None:
        """难度选择页：EXPERT → 准备完毕，等其他人都准备好、演奏画面出现（左上角 SCORE）后立即返回。

        为了尽快看到演奏画面，这里只识别左上角一小块，不判断别的画面；``timeout_s`` 内没出现时报错。
        """
        self.tap(BTN_ARENA_EXPERT, "EXPERT")
        self._sleep(0.6)
        self.tap(BTN_ARENA_READY, "准备完毕")
        ready_at = time.monotonic()
        while time.monotonic() - ready_at < timeout_s:
            frame, _ = self.source.grab()
            if find(self.ocr.read(frame, PLAY_SCORE_ROI), "SCORE"):
                logger.debug("点准备完毕 %.1fs 后进入演奏画面", time.monotonic() - ready_at)
                return
            self._sleep(0.1)
        self.look()
        raise self._fail(f"点准备完毕后 {timeout_s:.0f}s 没有进入演奏画面")

    def read_arena_result(self, expected_total: int | None = None, timeout_s: float = 360.0) -> ArenaOutcome:
        """演奏后一路点到结算页，记下名次、段位分增减和加减后的段位分（结果弹窗上的；名次没读到时看排名页），
        在结算页读判定数后返回，停在结算页（之后 :meth:`leave_result` 回到激奏舞台首页）。

        同步失败时歌还在放，超时较长。错过了结算页（已经到了后面几页）时判定数为 None。
        """
        place = gain = total = None
        deadline = time.monotonic() + timeout_s
        guess_at: float | None = None  # 开始看到像段位提升、但认不出的画面的时刻
        guessed = False

        def parse(its: list[OcrItem]):
            p, g = arena_result(its)
            if p is None:
                return None
            t = arena_ap_total(its)
            if t is None:  # 一位数的段位分（刚开始打的乐队）检测不到，直接识别那一块
                t = arena_ap_text(self.ocr.read_text(self._frame, ARENA_AP_ROI))
            return p, g, t

        while time.monotonic() < deadline:
            screen, items = self.look(still_ok=True)
            if screen is Screen.RESULT:
                counts = self._read_result_page(items, expected_total)
                return ArenaOutcome(place, gain, total, counts, self._arena_lb)
            if screen in (Screen.RESULT_REWARD, Screen.RESULT_EXP_NEXT, Screen.RESULT_EXP, Screen.ARENA_TOP):
                logger.warning("错过了激奏舞台的结算页，没有判定数")
                return ArenaOutcome(place, gain, total, None, self._arena_lb)
            if screen in RELOGIN_SCREENS:
                raise self._fail("游戏回到了标题画面，本局没有结算")
            if screen is Screen.ARENA_RESULT:
                got = self._stable_read(parse, items, tries=8) or parse(items)
                if got is not None:
                    place, gain, total = got
                    logger.info(
                        "激奏舞台：第 %d 名，段位分 %s%s",
                        place,
                        "?" if gain is None else f"{gain:+d}",
                        "" if total is None else f"（现在 {total}）",
                    )
                else:
                    logger.warning("激奏舞台结果弹窗没读出名次（截图 %s）", self.save_debug("arena_result"))
                screen, items = self.last_screen, self._items  # 上面又看过几次
            elif screen is Screen.ARENA_RANKING and place is None:
                place = arena_placement(items)
            if screen not in ARENA_SCREENS and rank_up_like(items):
                guess_at = guess_at or time.monotonic()
                if time.monotonic() - guess_at >= ARENA_RANK_UP_GUESS_S:
                    if not guessed:
                        guessed = True
                        logger.warning(
                            "画面上有 RANK UP、没有 OK（%s），%.0fs 没过去，当成段位提升点一下继续（截图 %s）",
                            screen,
                            time.monotonic() - guess_at,
                            self.save_debug("arena_rank_up_guess"),
                        )
                    screen = Screen.ARENA_RANK_UP
            else:
                guess_at = None
            if screen in ARENA_SCREENS or screen in ARENA_RESULT_POPUPS:
                self._common_step(screen, items)
                self._sleep(self.settle_s)
            else:
                self._sleep(1.0)
        raise self._fail(f"{timeout_s:.0f}s 内没有出现激奏舞台的结算页")
