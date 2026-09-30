"""界面导航：截图 → OCR 判断画面 → 点击，实现 :class:`~ournotes_auto.runner.Navigator`。

一局的画面顺序（国际服，自由演出）::

    乐曲选择 -[确定]-> 乐队确认 -[LIVE START]-> 演出前选项设置 -[演出]-> 加载 → 演奏
    → LIVE CLEAR/FINISH -[点击]-> 结算(判定数) -[下一步]-> 结算(奖励)
    (+ RANK UP 弹窗 -[OK]->) -[下一步]-> 结算(羁绊) -[再次演出]-> 乐曲选择（同一首歌）

结算页出现后可能陆续弹出达成奖励列表、最高分评级等，都点「关闭」。

活动期间羁绊页只有「下一步」，后面还有（第一次）活动故事解锁 -[关闭]-> 获得活动pt达成奖励 -[OK]->
活动结算页（活动pt），它和平时的羁绊页一样有「再次演出」。以后的活动可能还会多出别的页面：离开结算页时
看到「下一步」就点；认不出、画面又一直不动时直接停下（见 :data:`FROZEN_S`），不再反复重试。

每天游戏日期变更时弹窗要求回到标题画面::

    日期变更 -[前往标题画面]-> 标题 -[TAP TO START]-> 加载 → 登录奖励（×n，每页领取后弹「获得奖励」-[OK]->）
    → 公告 -[关闭]-> 主界面 -[演出]-> 演出首页 -[自由演出]-> 乐曲选择

游戏闪退（一直认不出画面、进程也不在了）或标题画面点击无反应时，经 adb 重启游戏，同样从标题画面重新登录。
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path

import cv2
import numpy as np

from ..config import Config
from ..device.base import FrameSource
from ..result_reader import OcrItem, ResultCounts, merge, parse_int, parse_totals
from ..runner import LbExhausted, NavigationError, ScreenFrozen, SongLabel
from .jacket import JacketMatcher, crop_jacket
from .ocr import MaaOcr
from .screens import (
    CLOSE_ROI,
    LB_ALL_CHECK,
    LB_RADIO,
    LEVEL_ROI,
    NEXT_ROI,
    RELOGIN_SCREENS,
    RESULT_COMBO_ROI,
    RESULT_SCORE_ROI,
    RESULT_TOTAL_VARIANTS,
    UNLOCK_CLOSE_ROI,
    Rect,
    Screen,
    band_confirm_song,
    center,
    classify,
    find,
    lb_bar_held,
    lb_bar_timer,
    lb_held,
    note_speed,
    parse_level,
    result_cells,
    title_startable,
)
from .daily import DailyMixin
from .song_select import BTN_RANDOM, ListPositions, SongSelectMixin, filter_open, song_locked
from .story import StoryMixin

logger = logging.getLogger(__name__)

DESIGN_W, DESIGN_H = 1280, 720

# 找不到按钮文字时使用的固定坐标（1280x720）
BTN_DIFFICULTY = {"easy": (807, 555), "normal": (937, 555), "hard": (1068, 555), "expert": (1198, 555)}
BTN_SONG_OK = (1163, 660)  # 乐曲选择：确定
BTN_BACK = (164, 40)  # 左上角主页按钮（设置页点它回到主界面）
BTN_LIVE_START = (1140, 648)
BTN_OPTIONS_START = (782, 612)  # 演出前选项设置：演出
BTN_OPTIONS_CANCEL = (499, 612)
BTN_NEXT = (1088, 660)  # 结算：下一步
BTN_AGAIN = (1104, 660)  # 结算：再次演出
BTN_CLOSE = (640, 652)  # 达成奖励列表：关闭
BTN_TOGGLE_TIMING = (1212, 396)  # 结算：⇄ 切换 FAST/SLOW
BTN_LB_OK = (640, 658)
BTN_LB_COST = (920, 665)  # 乐队确认页：消耗LB（打开 LIVE BOOST消耗设置）
# 恢复LIVE BOOST 弹窗只点左边的取消（右边 OK 会用道具/星钻恢复），按钮文字也只在左半边找
BTN_LB_RECOVER_CANCEL = (498, 663)
LB_RECOVER_CANCEL_ROI: Rect = (400, 630, 200, 70)
BTN_RANK_UP_OK = (640, 550)
BTN_PAUSE_END = (356, 579)  # 暂停：终止
# 终止确认：右边的「终止」（弹窗标题也是「终止」，只在按钮行找）
BTN_ABORT_CONFIRM = (781, 572)
ABORT_CONFIRM_ROI: Rect = (640, 540, 300, 70)
BTN_HOME_LIVE = (1081, 650)  # 主界面：演出
BTN_FREE_LIVE = (872, 350)  # 演出首页：自由演出
TAP_LIVE_END = (640, 650)
BTN_TO_TITLE = (640, 572)  # 日期变更：前往标题画面
# 标题画面：TAP TO START（不要点右上角的菜单按钮）
BTN_TAP_TO_START = (950, 585)
TAP_LOGIN_BONUS = (640, 650)  # 登录奖励演出：点下方空白处继续
BTN_REWARD_OK = (640, 659)  # 获得奖励 / GRADE UP 弹窗底部的 OK
BTN_UNLOCK_CLOSE = (640, 570)  # 乐曲解锁 / 故事解锁：关闭
TITLE_TAP_GAP_S = 3.0
# 标题画面出现 TAP TO START 后点了这么久还没反应就重启游戏；回到主界面之前最多重启几次
TITLE_STUCK_S = 60.0
MAX_RESTARTS = 2
# 连续这么久认不出画面就检查一次游戏是否还在运行（闪退后停在桌面上）
APP_CHECK_S = 30.0
# 认不出（或以为还在播动画）的画面这么久一点没变，就是停在了没见过的页面或弹窗上，干等不会变（比 APP_CHECK_S 长，
# 闪退后的桌面先由 _check_app 处理）。模拟器截图没有噪声，停住的画面逐像素相同；动画、转圈的缩略图会变几十上百
FROZEN_S = 60.0
FROZEN_THUMB = (160, 90)
FROZEN_DIFF = 4
# LB 用完改为消耗 0 后，这么久之内不再尝试按配置消耗（LB 随时间恢复；玩家升级时回满，看到升级画面就重新尝试）
LB_EMPTY_RETRY_S = 30 * 60
# 结算页出现约 1s 后可能叠上来的弹窗（首次达成奖励、评级提升及其奖励等），读数前先关掉
RESULT_POPUPS = (Screen.ACHIEVEMENT, Screen.POPUP, Screen.GRADE_UP, Screen.REWARD)
# 已经进入游戏、可以开始任务的画面（启动游戏时等到这些之一）
IN_GAME_SCREENS = frozenset((Screen.HOME, Screen.LIVE_TOP, Screen.SONG_SELECT, Screen.BAND_CONFIRM))
# 单选按钮/复选框选中时中心是白色（约 255），未选中是暗红（约 50）
LIT_THRESHOLD = 150


class GameNavigator(SongSelectMixin, DailyMixin, StoryMixin):
    def __init__(
        self,
        config: Config,
        source: FrameSource,
        touch,
        ocr: MaaOcr,
        stop: threading.Event | None = None,
        settle_s: float = 1.0,
        jackets: JacketMatcher | Future | None = None,
        restart_app: Callable[[], None] | None = None,
        app_running: Callable[[], bool] | None = None,
    ):
        self.cfg = config
        self.source = source
        self.touch = touch
        self.ocr = ocr
        self.stop = stop or threading.Event()
        self.settle_s = settle_s
        self._jackets = jackets
        self._rows_matcher: JacketMatcher | None = None  # 认列表缩略图用（第一次按曲目选歌时建）
        self.list_positions = ListPositions()
        self._filter_status: str | None = None  # 本次运行设好的「游玩状况」筛选（STATUS_OPTIONS 的键），不确定时为 None
        self.restart_app = restart_app
        self.app_running = app_running
        self.last_screen = Screen.UNKNOWN
        self._frame = None
        self._items: list[OcrItem] = []
        lb = config.game.lb_cost
        if lb is not None and lb not in LB_RADIO:
            raise ValueError(f"game.lb_cost 应为 0~3 或 null，而不是 {lb!r}")
        self._lb_cost: int | None = None  # 本次运行已在游戏里设置并核对过的 LB 消耗
        self._lb_empty_at: float | None = None  # 上次因 LB 用完改为消耗 0 的时刻
        self._title_since: float | None = None  # 标题画面第一次可以点击的时刻
        self._title_tap_at = -TITLE_TAP_GAP_S
        self._restarts = 0
        self._unknown_since: float | None = None  # 连续认不出画面的起点（每检查一次游戏进程重新计）
        self._still = None  # (起点, 缩略图)：认不出的画面从什么时候起没变过

    # ------------------------------------------------------------ 基础操作

    def _sleep(self, seconds: float) -> None:
        if self.stop.wait(seconds):
            raise NavigationError("已停止")

    @property
    def jackets(self) -> JacketMatcher | None:
        """封面匹配器；还在后台加载（见 :func:`~ournotes_auto.context.load_jackets_async`）时等它加载完。"""
        if isinstance(self._jackets, Future):
            if not self._jackets.done():
                logger.info("等曲目封面下载完…")
                while not self._jackets.done():
                    self._sleep(0.2)
            self._jackets = self._jackets.result()
        return self._jackets

    def look(self, still_ok: bool = False) -> tuple[Screen, list[OcrItem]]:
        """截图并判断画面。``still_ok``：画面长时间不动也正常（等结算时歌可能还在放），不按 FROZEN_S 判断卡住。"""
        frame, _ = self.source.grab()
        self._frame = frame
        items = self._items = self.ocr.read(frame)
        screen = classify(items)
        if screen != self.last_screen:
            logger.debug("画面：%s", screen)
        self.last_screen = screen
        if screen not in (Screen.TITLE, Screen.UNKNOWN):
            self._title_since = None
        if screen in (Screen.TITLE, Screen.DATE_CHANGE):
            self._filter_status = None  # 重新登录后游戏里的筛选不一定还是原来的
        if screen is Screen.HOME:
            self._restarts = 0
        if screen is Screen.UNKNOWN:
            self._check_app()
        else:
            self._unknown_since = None
        self._check_frozen(screen, still_ok)
        return screen, items

    def _check_app(self) -> None:
        """一直认不出画面时确认游戏还在运行，闪退了就重新启动（之后从标题画面重新登录）。"""
        if self.app_running is None:
            return
        t = time.monotonic()
        if self._unknown_since is None:
            self._unknown_since = t
        if t - self._unknown_since < APP_CHECK_S:
            return
        self._unknown_since = t
        try:
            running = self.app_running()
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("检查游戏进程失败：%s", e)
            return
        if running:
            return
        if self.restart_app is None or self._restarts >= MAX_RESTARTS:
            raise self._fail("游戏没有在运行（可能闪退了）")
        logger.warning("游戏没有在运行（可能闪退了），重新启动")
        self._restarts += 1
        self.restart_app()

    def _check_frozen(self, screen: Screen, still_ok: bool) -> None:
        """认不出（或以为还在播动画）的画面 FROZEN_S 内一点没变：抛 :class:`ScreenFrozen` 让任务停下，不再反复重试。
        要能确认游戏还在运行才判断。"""
        if still_ok or screen not in (Screen.UNKNOWN, Screen.RESULT_OTHER) or self.app_running is None:
            self._still = None
            return
        if not isinstance(self._frame, np.ndarray):  # 测试里用画面名代替截图
            return
        gray = cv2.cvtColor(self._frame, cv2.COLOR_BGR2GRAY)
        thumb = cv2.resize(gray, FROZEN_THUMB, interpolation=cv2.INTER_AREA)
        now = time.monotonic()
        if self._still is None or int(cv2.absdiff(self._still[1], thumb).max()) > FROZEN_DIFF:
            self._still = (now, thumb)
            return
        since = self._still[0]
        if now - since < FROZEN_S:
            return
        try:
            running = self.app_running()
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("检查游戏进程失败：%s", e)
            running = False
        if not running:
            self._still = (now, thumb)  # 过一阵再查
            return
        raise self._fail(f"画面 {now - since:.0f}s 没有变化，也认不出是什么页面（可能是没见过的页面或弹窗）", ScreenFrozen)

    def tap(self, point: tuple[int, int], what: str = "") -> None:
        w, h = self.source.size
        x, y = round(point[0] * w / DESIGN_W), round(point[1] * h / DESIGN_H)
        logger.debug("点击 %s (%d,%d)", what, x, y)
        self.touch.tap(x, y)

    def _button(
        self, items: list[OcrItem], text: str, default: tuple[int, int], roi: Rect | None = None
    ) -> tuple[int, int]:
        # 必须完全相等：「演出」按钮所在弹窗的标题「演出前选项设置」也包含「演出」
        it = find(items, text, roi, exact=True)
        return center(it) if it is not None else default

    def _lit(self, point: tuple[int, int]) -> bool:
        """最近一帧上 ``point``（设计坐标）附近是否是亮白色（选中的单选按钮/复选框）。"""
        h, w = self._frame.shape[:2]
        x, y = round(point[0] * w / DESIGN_W), round(point[1] * h / DESIGN_H)
        r = max(1, round(3 * w / DESIGN_W))
        return float(self._frame[y - r : y + r + 1, x - r : x + r + 1].mean()) > LIT_THRESHOLD

    def save_debug(self, name: str, frame=None) -> Path | None:
        frame = self._frame if frame is None else frame
        if frame is None:
            return None
        path = Path("debug/nav") / f"{time.strftime('%Y%m%d_%H%M%S')}_{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        # cv2.imwrite 不支持非 ASCII 路径（项目目录可能含中文）
        ok, buf = cv2.imencode(".png", frame)
        if not ok:
            return None
        path.write_bytes(buf.tobytes())
        return path

    def _fail(self, message: str, error: type[NavigationError] = NavigationError) -> NavigationError:
        path = self.save_debug(self.last_screen.name.lower())
        return error(f"{message}（画面：{self.last_screen}{f'，截图 {path}' if path else ''}）")

    def _common_step(self, screen: Screen, items: list[OcrItem]) -> bool:
        """与目标无关的通用处理（关弹窗、翻页），做了操作返回 True。"""
        if screen is Screen.LIVE_END:
            self.tap(TAP_LIVE_END, "LIVE CLEAR")
        elif screen in (Screen.ACHIEVEMENT, Screen.POPUP):
            self.tap(self._button(items, "关闭", BTN_CLOSE), "关闭")
        elif screen is Screen.UNLOCK:
            what = find(items, "现在可以选择") or find(items, "已解锁")
            logger.info("解锁：%s", what.text.strip() if what else "（没认出内容）")
            self.tap(self._button(items, "关闭", BTN_UNLOCK_CLOSE, UNLOCK_CLOSE_ROI), "关闭")
        elif screen in (Screen.RESULT, Screen.RESULT_REWARD, Screen.RESULT_EXP_NEXT):
            self.tap(self._button(items, "下一步", BTN_NEXT), "下一步")
        elif screen is Screen.RESULT_OTHER and (it := find(items, "下一步", NEXT_ROI, exact=True)):
            # 没见过的结算页（活动可能多出几页）。读判定数时不经过这里，所以看到「下一步」就点
            self.tap(center(it), "下一步")
        elif screen is Screen.RESULT_EXP:
            self.tap(self._button(items, "再次演出", BTN_AGAIN), "再次演出")
        elif screen is Screen.RANK_UP:
            self._lb_empty_at = None
            self.tap(self._button(items, "OK", BTN_RANK_UP_OK), "OK")
        elif screen is Screen.LB_SETTING:
            self.tap(BTN_LB_OK, "OK")  # 保持当前 LB 设置
        elif screen is Screen.LB_RECOVER:
            self._cancel_lb_recover(items)
        elif screen is Screen.LIVE_OPTIONS:
            self.tap(self._button(items, "取消", BTN_OPTIONS_CANCEL), "取消")
        elif screen is Screen.SETTINGS:
            self.tap(BTN_BACK, "返回")
        elif screen is Screen.PAUSE:
            logger.warning("演出处于暂停状态，终止本次演出")
            self.tap(self._button(items, "终止", BTN_PAUSE_END), "终止")
        elif screen is Screen.ABORT_CONFIRM:
            self.tap(self._button(items, "终止", BTN_ABORT_CONFIRM, ABORT_CONFIRM_ROI), "终止")
        elif screen is Screen.HOME:
            self.tap(BTN_HOME_LIVE, "演出")
        elif screen is Screen.LIVE_TOP:
            self.tap(self._button(items, "自由演出", BTN_FREE_LIVE), "自由演出")
        elif screen is Screen.DATE_CHANGE:
            logger.warning("游戏日期变更，回到标题画面重新登录")
            self.tap(self._button(items, "前往标题画面", BTN_TO_TITLE), "前往标题画面")
        elif screen is Screen.TITLE:
            self._on_title(items)
        elif screen is Screen.LOGIN_BONUS:
            self.tap(TAP_LOGIN_BONUS, "登录奖励")
        elif screen in (Screen.REWARD, Screen.GRADE_UP, Screen.BOND_UP):
            self.tap(self._button(items, "OK", BTN_REWARD_OK, CLOSE_ROI), "OK")
        else:
            return False
        return True

    def _on_title(self, items: list[OcrItem]) -> None:
        """标题画面：出现「TAP TO START」后点击登录。日期变更回到标题后曾出现怎么点都没反应的情况
        （登录 SDK 卡住），只能重启游戏。"""
        if find(items, "确定") or find(items, "取消") or find(items, "OK", exact=True):
            raise self._fail("标题画面上有未知弹窗")
        if not title_startable(items):
            return  # 刚启动，还在加载
        now = time.monotonic()
        if self._title_since is None:
            self._title_since = now
        if now - self._title_since > TITLE_STUCK_S:
            if self.restart_app is None or self._restarts >= MAX_RESTARTS:
                raise self._fail(f"标题画面 {TITLE_STUCK_S:.0f}s 点击无反应")
            logger.warning("标题画面 %.0fs 点击无反应，重启游戏", now - self._title_since)
            self._restarts += 1
            self._title_since = None
            self.restart_app()
        elif now - self._title_tap_at >= TITLE_TAP_GAP_S:
            self.tap(BTN_TAP_TO_START, "TAP TO START")
            self._title_tap_at = now

    # ------------------------------------------------------------ Navigator 接口

    def ensure_in_game(self, timeout_s: float = 180.0) -> Screen:
        """等到进入游戏（主界面或演出相关页面）：沿途点开标题画面、领登录奖励、关公告。"""
        deadline = time.monotonic() + timeout_s
        while True:
            screen, items = self.look()
            if screen in IN_GAME_SCREENS:
                return screen
            if screen in RELOGIN_SCREENS:
                deadline = time.monotonic() + timeout_s
            if time.monotonic() > deadline:
                raise self._fail(f"{timeout_s:.0f}s 内未能进入游戏")
            acted = self._common_step(screen, items)
            self._sleep(self.settle_s if acted else 1.0)

    def ensure_band_confirm(self, difficulty: str | None = None, timeout_s: float = 90.0) -> None:
        deadline = time.monotonic() + timeout_s
        diff = difficulty or self.cfg.game.difficulty
        while True:
            screen, items = self.look()
            if screen is Screen.BAND_CONFIRM:
                return
            if screen in RELOGIN_SCREENS:
                deadline = time.monotonic() + timeout_s
            if time.monotonic() > deadline:
                raise self._fail(f"{timeout_s:.0f}s 内未能进入乐队确认页")
            if screen is Screen.SONG_SELECT:
                if filter_open(items):  # 面板盖住了难度按钮和「确定」
                    self._close_filter()
                    continue
                if song_locked(items):
                    raise self._fail("选中的歌还没解锁")
                self.tap(BTN_DIFFICULTY[diff], diff.upper())
                self._sleep(0.6)
                self.tap(self._button(items, "确定", BTN_SONG_OK), "确定")
                acted = True
            else:
                acted = self._common_step(screen, items)
            self._sleep(self.settle_s if acted else 0.5)

    def selected_song(self) -> SongLabel:
        """刚进入乐队确认页时曲名还可能在动画中（曾读成单个「编」字），连续两次读数一致才采用。"""
        prev = None
        for _ in range(6):
            screen, items = self.look()
            if screen is not Screen.BAND_CONFIRM:
                raise self._fail("不在乐队确认页，无法读取曲名")
            title, diff = band_confirm_song(items)
            label = SongLabel(title, diff, parse_level(self.ocr.read_text(self._frame, LEVEL_ROI)))
            if label == prev:
                break
            prev = label
            self._sleep(0.3)
        else:
            logger.debug("乐队确认页读数不稳定，采用最后一次")
        if self.jackets is not None:
            label = label._replace(jacket=self.jackets.identify(crop_jacket(self._frame)))
        logger.debug(
            "乐队确认页：曲名 %r，难度 %s，等级 %s，封面 %s", label.title, label.difficulty, label.level, label.jacket
        )
        return label

    def choose_next_song(self, mode: str, timeout_s: float = 60.0) -> None:
        if mode == "current":
            return
        if mode != "random":
            raise ValueError(f"未知的选曲模式：{mode}")
        items = self.ensure_song_select(timeout_s)
        self.tap(self._button(items, "随机选曲", BTN_RANDOM), "随机选曲")
        self._sleep(self.settle_s)

    def _lb_want(self) -> int | None:
        want = self.cfg.game.lb_cost
        if want and self._lb_empty_at is not None and time.monotonic() - self._lb_empty_at < LB_EMPTY_RETRY_S:
            return 0
        return want

    def start_live(self, lb_short: str = "zero") -> None:
        """（按配置设好 LB 消耗后）点击 LIVE START，处理演出前选项设置弹窗后立即返回（弹窗关闭后才开始加载）。

        LB 不足时游戏照样开始演出，只消耗持有的全部（持有 0 就不消耗），有时则弹出恢复 LIVE BOOST：只点取消。
        ``lb_short`` 为 ``zero`` 时本局改为消耗 0；为 ``stop`` 时点 LIVE START 前先核对持有数，
        用完了（或弹出了恢复窗口）就留在乐队确认页并抛出 :class:`LbExhausted`。
        """
        if lb_short not in ("zero", "stop"):
            raise ValueError(f"未知的 LB 不足处理方式：{lb_short}")
        want = self._lb_want()
        if lb_short == "stop" and want and self._lb_bar_empty():
            # 顶栏偶尔漏读，打开消耗设置弹窗再核对一次（顺便确认消耗设置）
            if self.set_lb_cost(want) == 0:
                raise LbExhausted("LB 已用完")
        if want is not None and self._lb_cost != want:
            self.set_lb_cost(want)
        self.tap(BTN_LIVE_START, "LIVE START")
        started = time.monotonic()
        retapped = False
        unknown_since = None
        while time.monotonic() - started < 10:
            self._sleep(0.3)
            screen, items = self.look()
            if screen is Screen.LIVE_OPTIONS:
                self._check_speed(items)
                self.tap(self._button(items, "演出", BTN_OPTIONS_START), "演出")
                return
            if screen is Screen.LB_RECOVER:
                # LB 用完时要求先恢复：取消，本局改为消耗 0（一段时间后或玩家升级后再按配置设置）
                if self._lb_cost == 0:
                    raise self._fail("LB 消耗为 0 仍弹出恢复 LIVE BOOST")
                self._cancel_lb_recover(items)
                self._wait_for(Screen.BAND_CONFIRM)
                if lb_short == "stop":
                    raise LbExhausted("LB 已用完")
                self._lb_empty_at = time.monotonic()
                self.set_lb_cost(0)
                self.tap(BTN_LIVE_START, "LIVE START")
                started = time.monotonic()
                retapped = False
                unknown_since = None
            elif screen is Screen.BAND_CONFIRM:
                unknown_since = None
                if not retapped and time.monotonic() - started > 3:
                    self.tap(BTN_LIVE_START, "LIVE START")
                    retapped = True
            elif screen is Screen.UNKNOWN:
                # 弹窗淡入时也可能识别不出；持续一段时间才认为已直接进入加载（勾选过「下次不再显示」）
                unknown_since = unknown_since or time.monotonic()
                if time.monotonic() - unknown_since > 2.0:
                    return
            else:
                raise self._fail("点击 LIVE START 后出现意外画面")
        raise self._fail("点击 LIVE START 后没有进入演出")

    def set_lb_cost(self, cost: int, timeout_s: float = 15.0) -> int | None:
        """乐队确认页 → 消耗LB → 选中 ``cost``、按像素核对 → OK，回到乐队确认页（游戏会记住设置）。
        返回弹窗上读到的 LB 持有数。弹窗里的「恢复」按钮会打开用道具/星钻恢复 LB 的弹窗，绝不点。"""
        self.tap(BTN_LB_COST, "消耗LB")
        opened = time.monotonic()
        deadline = opened + timeout_s
        radio_taps = 0
        ok_at = None
        held = None
        while time.monotonic() < deadline:
            self._sleep(0.5)
            screen, items = self.look()
            if screen is Screen.BAND_CONFIRM:
                if ok_at is not None:
                    self._lb_cost = cost
                    return held
                if time.monotonic() - opened > 3:  # 点击没生效
                    self.tap(BTN_LB_COST, "消耗LB")
                    opened = time.monotonic()
            elif screen is Screen.LB_SETTING:
                if ok_at is not None and time.monotonic() - ok_at < 2:
                    continue  # 弹窗正在关闭
                selected = self._lb_selected()
                if selected != cost:
                    if radio_taps >= 3:
                        raise self._fail(f"LB 消耗选不中 {cost}（当前 {selected}）")
                    self.tap(LB_RADIO[cost], f"消耗 {cost}")
                    radio_taps += 1
                    continue
                if self._lit(LB_ALL_CHECK):
                    logger.warning("LB 消耗设置勾选了「全部消耗」，每局将消耗持有的全部 LB")
                held = lb_held(items)
                logger.debug("LB 消耗设置为 %d（持有 %s）", cost, "?" if held is None else held)
                self.tap(self._button(items, "OK", BTN_LB_OK), "OK")
                ok_at = time.monotonic()
            elif screen is not Screen.UNKNOWN:  # UNKNOWN：弹窗淡入淡出
                raise self._fail("设置 LB 消耗时出现意外画面")
        raise self._fail("未能设置 LB 消耗")

    def _lb_bar_empty(self) -> bool:
        """乐队确认页右上角的 LB 持有数是否读到 0（读不到算没用完，由恢复窗口兜底）。"""
        screen, items = self.look()
        if screen is not Screen.BAND_CONFIRM:
            return False
        held = lb_bar_held(items)
        logger.debug("顶栏 LB 持有数：%s", held)
        return held == 0

    def lb_status(self, check: bool = False) -> tuple[int | None, int | None]:
        """乐队确认页右上角的 LB 持有数和下一个恢复的倒计时（秒），读不到的项为 None。
        ``check`` 时再打开消耗设置弹窗核对持有数（顶栏的字很小，偶尔读错；顺便按配置设好消耗），
        顶栏漏读持有数时也这样读。"""
        screen, items = self.look()
        if screen is not Screen.BAND_CONFIRM:
            raise self._fail("不在乐队确认页，无法读取 LB")
        held, left = lb_bar_held(items), lb_bar_timer(items)
        want = self._lb_want()
        if (check or held is None) and want:
            held = self.set_lb_cost(want)
        logger.debug("LB 持有数：%s，恢复倒计时：%s", held, left)
        return held, left

    def _lb_selected(self) -> int | None:
        lit = [c for c, p in LB_RADIO.items() if self._lit(p)]
        return lit[0] if len(lit) == 1 else None

    def _cancel_lb_recover(self, items: list[OcrItem]) -> None:
        logger.warning("弹出了恢复 LIVE BOOST（LB 不足），点取消")
        self.tap(self._button(items, "取消", BTN_LB_RECOVER_CANCEL, LB_RECOVER_CANCEL_ROI), "取消")

    def _wait_for(self, target: Screen, timeout_s: float = 5.0) -> list[OcrItem]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._sleep(0.5)
            screen, items = self.look()
            if screen is target:
                return items
        raise self._fail(f"{timeout_s:.0f}s 内没有回到{target}")

    def _check_speed(self, items: list[OcrItem]) -> None:
        speed = note_speed(items)
        want = self.cfg.game.note_speed
        if speed is None:
            logger.debug("未读到节奏图示速度")
        elif abs(speed - want) > 1e-6:
            logger.warning(
                "游戏内节奏图示速度为 %.2f，配置为 %.2f：同步参数 play.sync.tau_s 与流速对应，请改回或重新校准",
                speed,
                want,
            )

    def read_result(self, expected_total: int | None = None, timeout_s: float = 300.0) -> ResultCounts:
        """等待结算页并读取判定数；同步失败时歌曲仍在播放，所以超时较长。

        ``expected_total``：谱面的判定音符数，用来核对、纠正判定总数的读数。
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            screen, items = self.look(still_ok=True)
            if screen is Screen.RESULT:
                return self._read_result_page(items, expected_total)
            if screen in RELOGIN_SCREENS:
                raise self._fail("游戏回到了标题画面，本局没有结算")
            if screen in (Screen.LIVE_END, *RESULT_POPUPS, Screen.PAUSE, Screen.ABORT_CONFIRM):
                self._common_step(screen, items)
                self._sleep(self.settle_s)
            elif screen in (
                Screen.RESULT_REWARD,
                Screen.RANK_UP,
                Screen.RESULT_EXP_NEXT,
                Screen.RESULT_EXP,
                Screen.SONG_SELECT,
                Screen.BAND_CONFIRM,
            ):
                raise self._fail("错过了结算页")
            else:
                self._sleep(1.0)
        raise self._fail(f"{timeout_s:.0f}s 内没有出现结算页")

    def _stable_read(self, parse, items: list[OcrItem], tries: int = 15):
        """结算页数字有滚动动画：连续两次读数一致才采用。``parse`` 读取 ``self._frame``。"""
        prev = None
        for _ in range(tries):
            value = parse(items)
            if value is not None and value == prev:
                return value
            prev = value
            self._sleep(0.4)
            items = self._result_items()
        return None

    def _result_items(self) -> list[OcrItem]:
        """重新识别结算页。首次达成连击/评级奖励、最高分评级提升等会在结算页出现约 1s 后弹窗，先关掉。"""
        for _ in range(5):
            screen, items = self.look()
            if screen not in RESULT_POPUPS:
                return items
            self._common_step(screen, items)
            self._sleep(self.settle_s)
        raise self._fail("关不掉结算页上的弹窗")

    def _read_cells(self, items: list[OcrItem], timing: bool, variant=(0, 0, 0, 0)) -> dict[str, list[int]] | None:
        rows = {}
        for j, rois in result_cells(items, timing, variant).items():
            nums = [parse_int(self.ocr.read_text(self._frame, roi)) for roi in rois]
            if None in nums:
                return None
            rows[j] = nums
        return rows

    def _read_totals(self, items: list[OcrItem], expected_total: int | None) -> dict[str, list[int]] | None:
        """判定总数：依次换几种框法，取第一个五项之和等于谱面音符数的；都对不上时用第一个读出来的。"""
        fallback = None
        for variant in RESULT_TOTAL_VARIANTS:
            rows = self._read_cells(items, False, variant)
            if rows is None:
                continue
            if expected_total is None or sum(v[0] for v in rows.values()) == expected_total:
                return rows
            fallback = fallback or rows
        return fallback

    def _read_result_page(self, items: list[OcrItem], expected_total: int | None = None) -> ResultCounts:
        totals = self._stable_read(lambda its: self._read_totals(its, expected_total), items)
        if totals is None:
            raise self._fail("结算页判定数读取失败")
        totals_frame = self._frame
        total = sum(v[0] for v in totals.values())
        if expected_total is not None and total != expected_total:
            logger.warning("判定数之和 %d 与谱面音符数 %d 不符：%s", total, expected_total, totals)
            self.save_debug("result_totals", totals_frame)
        page_combo = parse_totals(self._items, DESIGN_W).combo  # 整页识别的连击，单独识别失败时用
        # 数字动画结束前点 ⇄ 无效，所以先读总数再切换；切换后以表头 FAST 判断是否生效，避免连点又切回去
        fast: dict[str, int] = {}
        slow: dict[str, int] = {}
        for _ in range(4):
            self.tap(BTN_TOGGLE_TIMING, "⇄")
            self._sleep(1.2)
            items = self._result_items()
            if find(items, "FAST") is None:
                continue
            got = self._stable_read(lambda its: self._read_cells(its, timing=True), items, tries=5)
            if got is not None:
                fast = {j: v[0] for j, v in got.items()}
                slow = {j: v[1] for j, v in got.items()}
            break
        else:
            logger.warning("未能切换到 FAST/SLOW 显示")
        # 分数、连击只做记录。切换后再读（两种显示都有），这时数字动画肯定结束了
        score = parse_int(self.ocr.read_text(self._frame, RESULT_SCORE_ROI))
        combo = parse_int(self.ocr.read_text(self._frame, RESULT_COMBO_ROI))
        if combo is None:
            combo = page_combo
        counts = ResultCounts({j: v[0] for j, v in totals.items()}, {}, {}, score, combo)
        rc = merge(counts, fast, slow)
        if len(rc.fast) < len(fast):
            logger.warning("FAST/SLOW 读数与总数矛盾，已丢弃：总数 %s，FAST %s，SLOW %s", counts.counts, fast, slow)
            self.save_debug("result_timing")
            self.save_debug("result_totals", totals_frame)
        return rc

    def leave_result(self, timeout_s: float = 60.0) -> None:
        """结算页 → 下一步 ×2（活动期间更多）→ 再次演出，停在乐曲选择页（同一首歌）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            screen, items = self.look()
            if screen in (Screen.SONG_SELECT, Screen.BAND_CONFIRM):
                return
            if screen in RELOGIN_SCREENS:
                deadline = time.monotonic() + timeout_s
            if self._common_step(screen, items):
                self._sleep(self.settle_s)
            else:
                self._sleep(0.5)
        raise self._fail("未能离开结算页")
