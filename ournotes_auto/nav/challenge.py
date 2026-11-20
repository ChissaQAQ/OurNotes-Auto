"""挑战演出（部分活动期间开放）：消耗挑战pt（CP）而不是 LB，打活动指定的几首歌。

画面顺序（坐标均为 1280x720 设计尺寸）::

    演出首页 -[挑战演出]-> 挑战演出乐曲选择（左边只有活动指定的几首歌）-[难度 → 确定]->
    挑战演出乐队确认（顶栏是 CP 持有数，底部「CP 设置」）-[LIVE START]-> 和自由演出一样

    乐队确认 -[CP 设置]-> 挑战pt消耗设置（每局 200/400/800/1600，奖励 ×1/×2/×4/×8）-[选中 → OK]-> 乐队确认

- 每局消耗多少由 ``game.challenge_cost`` 决定（默认最少的 200，能多打几局），只在核对过选中的单选按钮后点 OK；
  不改设置时只点「取消」。``game.challenge_auto`` 时每局按持有数选够用的最大一档（:func:`~.screens.cp_auto_cost`）。
- CP 不够每局消耗时停下（:class:`~ournotes_auto.runner.CpExhausted`）。持有数不够所选的消耗时游戏不让点 OK
  （提示「挑战pt不足。」），这时点「取消」再停下。
- 乐队确认页的「跳过 还剩n次」（扫荡券）绝不点。
- 选中的歌在所选难度有没有 AP 看乐曲选择页右侧面板的 ALL PERFECT 标记（:meth:`ChallengeMixin.challenge_song_ap`）。
"""

from __future__ import annotations

import logging
import time

from ..result_reader import OcrItem
from ..runner import CpExhausted
from .screens import (
    CHALLENGE_ROW_TAP_X,
    CP_RADIO,
    Rect,
    Screen,
    all_perfect_mark,
    center,
    challenge_rows,
    challenge_selected,
    cp_auto_cost,
    cp_bar_held,
    cp_held,
    find,
    same_title,
    select_panel_title,
)
from .song_select import BTN_BAND_BACK, BTN_DIFFICULTY

logger = logging.getLogger(__name__)

BTN_CHALLENGE_LIVE = (870, 638)  # 演出首页：挑战演出
BTN_CP_COST = (920, 665)  # 挑战演出乐队确认页：CP 设置（和自由演出的「消耗LB」同一个位置）
# 挑战pt消耗设置：左取消右 OK，按钮文字分别只在左右半边找
BTN_CP_CANCEL = (498, 572)
CP_CANCEL_ROI: Rect = (340, 540, 300, 70)
BTN_CP_OK = (782, 572)
CP_OK_ROI: Rect = (640, 540, 300, 70)
CP_SHORT_ROI: Rect = (440, 300, 400, 120)  # 持有数不够所选消耗时点 OK 弹出的「挑战pt不足。」
# 演出首页连续这么多次没认出「挑战演出」就认为没在开放（刚切到演出首页时可能还没读到）
CHALLENGE_MISSING_LOOKS = 3
CHALLENGE_MAX_TAPS = 3
CHALLENGE_SCREENS = frozenset((Screen.CHALLENGE_SONG_SELECT, Screen.CHALLENGE_BAND_CONFIRM))


class ChallengeMixin:
    """:class:`~ournotes_auto.nav.navigator.GameNavigator` 的挑战演出操作。

    用到它的 ``look`` ``tap`` ``_sleep`` ``_lit`` ``_fail`` ``_button`` ``_common_step`` 等。
    """

    challenge: bool
    _cp_cost: int | None
    _cp_top: int | None
    _challenge_missing: int
    _challenge_taps: int

    def _enter_challenge(self, items: list[OcrItem]) -> bool:
        """演出首页：点「挑战演出」。连续几次都没有这个按钮（不在活动期间）、点了几次都进不去时报错停下。"""
        it = find(items, "挑战演出", exact=True)
        if it is None:
            self._challenge_missing += 1
            if self._challenge_missing >= CHALLENGE_MISSING_LOOKS:
                raise self._fail("演出首页没有「挑战演出」（只在部分活动期间开放）")
            return False
        self._challenge_missing = 0
        if self._challenge_taps >= CHALLENGE_MAX_TAPS:
            raise self._fail(f"点了 {CHALLENGE_MAX_TAPS} 次「挑战演出」都没有进去")
        self._challenge_taps += 1
        self.tap(center(it), "挑战演出")
        return True

    def _cp_selected(self) -> int | None:
        lit = [c for c, p in CP_RADIO.items() if self._lit(p)]
        return lit[0] if len(lit) == 1 else None

    def set_cp_cost(self, cost: int | None, timeout_s: float = 15.0, auto: bool = False) -> int | None:
        """挑战演出乐队确认页 → CP 设置 → 选中 ``cost``、按像素核对 → OK，回到乐队确认页（游戏会记住设置）。
        ``cost`` 为 None 时不改，只读出当前选中的消耗后点「取消」。返回弹窗上读到的 CP 持有数。
        持有数不够 ``cost`` 时点「取消」，回到乐队确认页后抛 :class:`CpExhausted`。

        ``auto``（``game.challenge_auto``）时不看 ``cost``，按弹窗上的持有数选够用的最大一档：某一档点了几次都选不中
        （没解锁？）就退一档，这次运行不再选它；持有数读不到就不改设置；最少的一档也不够时同样取消后抛 :class:`CpExhausted`。"""
        self.tap(BTN_CP_COST, "CP 设置")
        opened = time.monotonic()
        deadline = opened + timeout_s
        radio_taps = 0
        closed_at = None
        held = selected = None
        short = False
        while time.monotonic() < deadline:
            self._sleep(0.5)
            screen, items = self.look()
            if screen is Screen.CHALLENGE_BAND_CONFIRM:
                if closed_at is not None:
                    if short:
                        self._cp_cost = None  # 取消了，游戏里还是原来的设置
                        need = f"每局{'最少' if auto and cost == min(CP_RADIO) else ''}消耗 {cost}"
                        raise CpExhausted(f"挑战pt 不够了（持有 {'?' if held is None else held}，{need}）")
                    self._cp_cost = selected
                    return held
                if time.monotonic() - opened > 3:  # 点击没生效
                    self.tap(BTN_CP_COST, "CP 设置")
                    opened = time.monotonic()
            elif screen is Screen.CP_SETTING:
                if closed_at is not None and time.monotonic() - closed_at < 2:
                    continue  # 弹窗正在关闭
                selected = self._cp_selected()
                held = cp_held(items)
                if auto:
                    cost = None if held is None else cp_auto_cost(held, self._cp_top)
                    if held is not None and cost is None:  # 最少的一档也不够
                        cost, short = min(CP_RADIO), True
                if cost is not None and not short and selected != cost:
                    if radio_taps >= 3:
                        lower = max((c for c in CP_RADIO if c < cost), default=None)
                        if not auto or lower is None:
                            raise self._fail(f"挑战pt消耗选不中 {cost}（当前 {selected}）")
                        logger.warning("挑战pt消耗选不中 %d（没解锁？），这次运行最多选 %d", cost, lower)
                        self._cp_top, radio_taps = lower, 0
                        continue
                    self.tap(CP_RADIO[cost], f"每局 {cost} CP")
                    radio_taps += 1
                    continue
                logger.debug("挑战pt消耗为 %s（持有 %s）", selected or "?", "?" if held is None else held)
                if cost is not None and (
                    (held is not None and held < cost) or find(items, "挑战pt不足", CP_SHORT_ROI)
                ):
                    short = True  # OK 点不动（提示「挑战pt不足。」），取消后停下
                if cost is None or short:
                    self.tap(self._button(items, "取消", BTN_CP_CANCEL, CP_CANCEL_ROI), "取消")
                else:
                    self.tap(self._button(items, "OK", BTN_CP_OK, CP_OK_ROI), "OK")
                closed_at = time.monotonic()
            elif screen is not Screen.UNKNOWN:  # UNKNOWN：弹窗淡入淡出
                raise self._fail("设置挑战pt消耗时出现意外画面")
        raise self._fail("未能设置挑战pt消耗")

    def _check_cp(self) -> None:
        """挑战演出乐队确认页：按配置设好每局消耗，CP 不够一局时抛 :class:`CpExhausted`（留在乐队确认页）。
        顶栏的数字够就不打开弹窗；不够或没读到时用弹窗里的持有数核对。"""
        if self.cfg.game.challenge_auto:
            return self._check_cp_auto()
        want = self.cfg.game.challenge_cost
        screen, items = self.look()
        held = cp_bar_held(items) if screen is Screen.CHALLENGE_BAND_CONFIRM else None
        need = want or self._cp_cost
        if (want is not None and self._cp_cost != want) or need is None or held is None or held < need:
            held = self.set_cp_cost(want)
            need = want or self._cp_cost
        logger.debug("CP 持有 %s，每局消耗 %s", held, need)
        if held is not None and need is not None and held < need:
            raise CpExhausted(f"挑战pt 不够了（持有 {held}，每局消耗 {need}）")

    def _check_cp_auto(self) -> None:
        """``game.challenge_auto``：每局按持有的挑战pt 选够用的最大一档（先 1600，不够了依次 800、400、200），
        最少的一档也不够时抛 :class:`CpExhausted`。顶栏读到的持有数算出来和现在的设置一样就不打开弹窗；不一样、
        没读到或还没核对过设置时打开弹窗，按弹窗上的持有数选（见 :meth:`set_cp_cost`）。"""
        screen, items = self.look()
        held = cp_bar_held(items) if screen is Screen.CHALLENGE_BAND_CONFIRM else None
        if held is None or self._cp_cost is None or cp_auto_cost(held, self._cp_top) != self._cp_cost:
            held = self.set_cp_cost(None, auto=True)
        cost = self._cp_cost
        if cost is None:
            logger.warning("没读到挑战pt 的持有数和消耗设置，照游戏里现在的设置打")
            return
        logger.info(
            "挑战pt 持有 %s：每局消耗 %d（奖励 ×%d）", "?" if held is None else held, cost, cost // min(CP_RADIO)
        )

    def ensure_challenge_song_select(self, timeout_s: float = 60.0) -> list[OcrItem]:
        """回到挑战演出的乐曲选择页，返回这一帧的识别结果。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            screen, items = self.look()
            if screen is Screen.CHALLENGE_SONG_SELECT:
                return items
            if screen is Screen.CHALLENGE_BAND_CONFIRM:
                self.tap(BTN_BAND_BACK, "返回")
            elif not self._common_step(screen, items):
                self._sleep(0.5)
                continue
            self._sleep(self.settle_s)
        raise self._fail("未能回到挑战演出的乐曲选择页")

    def _challenge_rows(self, timeout_s: float) -> tuple[list[tuple[str, float]], int]:
        """挑战演出乐曲选择页上的各行和选中的那一行（列表还在滚动时多看几次）。"""
        for _ in range(5):
            rows = challenge_rows(self.ensure_challenge_song_select(timeout_s))
            i = challenge_selected(rows)
            if i is not None:
                return rows, i
            self._sleep(0.5)
        raise self._fail("挑战演出乐曲选择页没认出选中的是哪首歌")

    def next_challenge_song(self, timeout_s: float = 60.0) -> None:
        """挑战演出乐曲选择页：选中列表里的下一首（选中的是最后一首时回到第一首），轮流打每首歌。"""
        rows, i = self._challenge_rows(timeout_s)
        before = rows[i][0]
        if i + 1 < len(rows):
            self._tap_row(rows[i + 1])
        else:
            # 最后一首：反复点最上面的一行，直到上面没有别的歌（选中第一首）
            for _ in range(20):
                if i == 0:
                    break
                self._tap_row(rows[0])
                rows, i = self._challenge_rows(timeout_s)
            else:
                raise self._fail("挑战演出乐曲选择页回不到第一首")
            if rows[i][0] == before:
                logger.info("挑战演出只有一首歌：%s", before)
                return
        rows, i = self._challenge_rows(timeout_s)
        if rows[i][0] == before:
            raise self._fail("挑战演出乐曲选择页没换成下一首")
        logger.info("挑战演出换歌：%s → %s", before, rows[i][0])

    def challenge_song_ap(self, difficulty: str, timeout_s: float = 60.0) -> tuple[str, bool]:
        """挑战演出乐曲选择页：选上 ``difficulty``，返回选中的歌（右侧面板上的完整曲名）和它在这个难度是否已经 AP。

        看右侧面板的 ALL PERFECT 标记；面板上的曲名和选中的行对得上、连续两次读数一致才采用（刚换歌、换难度时面板可能还没变）。
        """
        self._challenge_rows(timeout_s)  # 先回到挑战演出乐曲选择页
        self.tap(BTN_DIFFICULTY[difficulty], difficulty.upper())
        prev = None
        for _ in range(8):
            self._sleep(0.4)
            items = self.ensure_challenge_song_select(timeout_s)
            rows = challenge_rows(items)
            i = challenge_selected(rows)
            if i is None or not same_title(rows[i][0], select_panel_title(items)):
                prev = None
                continue
            reading = (select_panel_title(items) or rows[i][0], all_perfect_mark(items))
            if prev is not None and same_title(prev[0], reading[0]) and prev[1] == reading[1]:
                logger.debug("挑战演出：%s %s%s", reading[0], difficulty.upper(), " 已 AP" if reading[1] else " 未 AP")
                return reading
            prev = reading
        raise self._fail("挑战演出乐曲选择页认不出选中的歌有没有 AP")

    def _tap_row(self, row: tuple[str, float]) -> None:
        self.tap((CHALLENGE_ROW_TAP_X, round(row[1])), row[0])
        self._sleep(self.settle_s)
