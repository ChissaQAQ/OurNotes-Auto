"""界面导航：截图 → OCR 判断画面 → 点击，实现 :class:`~ournotes_auto.runner.Navigator`。

一局的画面顺序（国际服，自由演出）::

    乐曲选择 -[确定]-> 乐队确认 -[LIVE START]-> 演出前选项设置 -[演出]-> 加载 → 演奏
    → LIVE CLEAR/FINISH -[点击]-> 结算(判定数) -[下一步]-> 结算(奖励)
    (+ RANK UP 弹窗 -[OK]->) -[下一步]-> 结算(羁绊) -[再次演出]-> 乐曲选择（同一首歌）

结算页出现后可能陆续弹出达成奖励列表、最高分评级等，都点「关闭」。

开了 ``game.lb_refill`` 时，LB 不够每局消耗就在乐队确认页用道具补充::

    乐队确认 -[消耗LB]-> LIVE BOOST消耗设置 -[恢复]-> 恢复LIVE BOOST（「道具」页）-[饮料 + … OK]->
    将恢复n点LIVE BOOST -[OK]-> 已恢复LIVE BOOST -[OK]-> 消耗设置 -[OK]-> 乐队确认

活动期间羁绊页只有「下一步」，后面还有（第一次）活动故事解锁 -[关闭]-> 获得活动pt达成奖励 -[OK]->
活动结算页（活动pt），它和平时的羁绊页一样有「再次演出」。以后的活动可能还会多出别的页面：离开结算页时
看到「下一步」就点；认不出、画面又一直不动时直接停下（见 :data:`FROZEN_S`），不再反复重试。

每天游戏日期变更时弹窗要求回到标题画面::

    日期变更 -[前往标题画面]-> 标题 -[TAP TO START]-> 加载 → 登录奖励（×n，每页领取后弹「获得奖励」-[OK]->）
    → 公告 -[关闭]-> 主界面 -[演出]-> 演出首页 -[自由演出]-> 乐曲选择

游戏更新后登录时先弹「数据下载」，点 OK 下载追加数据；要求更新安装包（「检测到新版本」）时任务停下，等玩家自己更新。

游戏闪退（一直认不出画面、进程也不在了）或标题画面点击无反应时，经 adb 重启游戏，同样从标题画面重新登录。

挑战演出（``loop.challenge``，部分活动期间开放）从演出首页的「挑战演出」进，乐曲选择、乐队确认页换成挑战演出的，
消耗 CP 而不是 LB（见 :mod:`.challenge`）；之后的演奏、结算和自由演出一样。
"""

from __future__ import annotations

import logging
import math
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
from ..player.guard import load_template, pause_visible
from ..result_reader import OcrItem, ResultCounts, merge, parse_int, parse_totals
from ..runner import GameUpdateRequired, LbExhausted, NavigationError, ScreenFrozen, ServerMaintenance, SongLabel
from .jacket import JacketMatcher, crop_jacket
from .ocr import MaaOcr
from .screens import (
    CLOSE_ROI,
    CP_RADIO,
    LB_ALL_CHECK,
    LB_PLUS_X,
    LB_RADIO,
    LB_TAB_ITEMS,
    LB_TAB_OTHERS,
    LEVEL_ROI,
    NEXT_ROI,
    RELOGIN_SCREENS,
    RESULT_COMBO_ROI,
    RESULT_SCORE_ROI,
    RESULT_TOTAL_VARIANTS,
    UNLOCK_CLOSE_ROI,
    LbDrink,
    Rect,
    Screen,
    band_confirm_song,
    center,
    classify,
    find,
    lb_bar_held,
    lb_bar_timer,
    lb_drinks,
    lb_held,
    lb_preview,
    lb_recover_amount,
    maintenance_period,
    note_speed,
    parse_level,
    result_cells,
    title_startable,
)
from .challenge import BTN_CP_CANCEL, CHALLENGE_SCREENS, CP_CANCEL_ROI, ChallengeMixin
from .daily import DailyMixin
from .song_select import (
    BTN_BAND_BACK,
    BTN_DIFFICULTY,
    BTN_RANDOM,
    ListPositions,
    SongSelectMixin,
    filter_open,
    song_locked,
)
from .story import StoryMixin

logger = logging.getLogger(__name__)

DESIGN_W, DESIGN_H = 1280, 720

# 找不到按钮文字时使用的固定坐标（1280x720）
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
# 消耗设置弹窗里持有数旁边的「恢复」（打开恢复LIVE BOOST），只在开了 game.lb_refill、LB 不够时点
BTN_LB_RECOVER = (940, 575)
LB_RECOVER_ROI: Rect = (860, 545, 160, 60)
# 恢复LIVE BOOST 弹窗：左取消右 OK。OK 只在开了 game.lb_refill、核对过选中的是「道具」页、数量和预览之后点
# （星钻、广告分页绝不点）；其余时候只点取消。按钮文字分别只在左右半边找
BTN_LB_RECOVER_CANCEL = (498, 663)
LB_RECOVER_CANCEL_ROI: Rect = (400, 630, 200, 70)
BTN_LB_RECOVER_OK = (782, 663)
LB_RECOVER_OK_ROI: Rect = (680, 630, 200, 70)
# 「将恢复n点LIVE BOOST。确定要恢复吗？」左取消右 OK；「已恢复LIVE BOOST。」只有 OK
BTN_LB_CONFIRM_CANCEL = (497, 572)
BTN_LB_CONFIRM_OK = (782, 572)
LB_CONFIRM_CANCEL_ROI: Rect = (340, 540, 300, 70)
LB_CONFIRM_OK_ROI: Rect = (640, 540, 300, 70)
BTN_LB_RECOVERED_OK = (640, 570)
LB_RECOVERED_OK_ROI: Rect = (540, 540, 200, 60)
BTN_RANK_UP_OK = (640, 550)
BTN_PAUSE_END = (356, 579)  # 暂停：终止
BTN_PAUSE_RETRY = (640, 579)  # 暂停：重试（这首歌从头开始）
BTN_PAUSE_CONTINUE = (923, 579)  # 暂停：继续
BTN_PLAY_PAUSE = (1240, 38)  # 演奏画面右上角的暂停按钮（模板 play_pause.png 的中心）
# 终止确认：右边的「终止」（弹窗标题也是「终止」，只在按钮行找）
BTN_ABORT_CONFIRM = (781, 572)
ABORT_CONFIRM_ROI: Rect = (640, 540, 300, 70)
# 重试确认：和终止确认一样的布局，左边「取消」右边「重试」（弹窗标题也是「重试」）
BTN_RETRY_CANCEL = (497, 572)
BTN_RETRY_CONFIRM = (781, 572)
RETRY_CANCEL_ROI: Rect = (340, 540, 300, 70)
RETRY_CONFIRM_ROI: Rect = (640, 540, 300, 70)
BTN_HOME_LIVE = (1081, 650)  # 主界面：演出
BTN_FREE_LIVE = (872, 350)  # 演出首页：自由演出
TAP_LIVE_END = (640, 650)
BTN_TO_TITLE = (640, 572)  # 日期变更：前往标题画面；连接失败：返回标题画面
BTN_MAINTENANCE_TO_TITLE = (640, 652)  # 服务器维护中：返回标题画面
# 数据下载：左取消右 OK，OK 只在右半边找
BTN_DOWNLOAD_OK = (782, 655)
DOWNLOAD_OK_ROI: Rect = (660, 620, 250, 70)
# 标题画面：TAP TO START（不要点右上角的菜单按钮）
BTN_TAP_TO_START = (950, 585)
BTN_NOTIFY_CLOSE = (822, 171)  # 「开启消息通知」弹窗右上角的 ⓧ
TAP_LOGIN_BONUS = (640, 650)  # 登录奖励演出：点下方空白处继续
BTN_REWARD_OK = (640, 659)  # 获得奖励 / GRADE UP 弹窗底部的 OK
BTN_UNLOCK_CLOSE = (640, 570)  # 乐曲解锁 / 故事解锁：关闭
TAP_NEW_SONG = (640, 627)  # 追加乐曲演出：TAP TO NEXT
TITLE_TAP_GAP_S = 3.0
# 标题画面出现 TAP TO START 后点了这么久还没反应就重启游戏；回到主界面之前最多重启几次
TITLE_STUCK_S = 60.0
# 标题画面这么久还没出现 TAP TO START（平时 7 秒左右）：多半是被没见过的弹窗挡住了，报错停下，不再干等
TITLE_LOADING_S = 120.0
# 「连接失败」：回到标题画面后等一会儿再登录；进到游戏之前连续这么多次都连不上（断网、维护）就报错
CONNECT_RETRY_WAIT_S = 10.0
MAX_CONNECT_RETRIES = 3
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
IN_GAME_SCREENS = frozenset((Screen.HOME, Screen.LIVE_TOP, Screen.SONG_SELECT, Screen.BAND_CONFIRM, *CHALLENGE_SCREENS))
# 乐曲选择、乐队确认页（自由演出和挑战演出的）
SONG_SCREENS = (Screen.SONG_SELECT, Screen.BAND_CONFIRM, *CHALLENGE_SCREENS)
# 单选按钮/复选框选中时中心是白色（约 255），未选中是暗红（约 50）
LIT_THRESHOLD = 150


def plan_refill(drinks: list[LbDrink], need: int, budget: int | None) -> list[int]:
    """用道具补充 ``need`` 个 LB 时每种饮料各用几瓶（和 ``drinks`` 一一对应）：先用恢复得少的凑，凑不够再用大的
    （多补的 LB 留着下局用），总共不超过 ``budget`` 个（None 为不限）。"""
    plan = [0] * len(drinks)
    gain = 0
    for i in sorted(range(len(drinks)), key=lambda i: drinks[i].lb):
        d = drinks[i]
        while gain < need and plan[i] < d.owned - d.chosen and (budget is None or gain + d.lb <= budget):
            plan[i] += 1
            gain += d.lb
    return plan


class GameNavigator(SongSelectMixin, DailyMixin, StoryMixin, ChallengeMixin):
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
        self.prev_screen = Screen.UNKNOWN  # 上一次 look 认出的画面
        self._frame = None
        self._items: list[OcrItem] = []
        lb = config.game.lb_cost
        if lb is not None and lb not in LB_RADIO:
            raise ValueError(f"game.lb_cost 应为 0~3 或 null，而不是 {lb!r}")
        self._lb_cost: int | None = None  # 本次运行已在游戏里设置并核对过的 LB 消耗
        # 挑战演出：要去的乐曲选择、乐队确认页换成挑战演出的，另一种（自由演出的）就退回演出首页
        self.challenge = config.loop.challenge
        self.song_screen = Screen.CHALLENGE_SONG_SELECT if self.challenge else Screen.SONG_SELECT
        self.band_screen = Screen.CHALLENGE_BAND_CONFIRM if self.challenge else Screen.BAND_CONFIRM
        self._foreign_screens = (
            frozenset((Screen.SONG_SELECT, Screen.BAND_CONFIRM)) if self.challenge else CHALLENGE_SCREENS
        )
        cp = config.game.challenge_cost
        if cp is not None and cp not in CP_RADIO:
            raise ValueError(f"game.challenge_cost 应为 {'/'.join(map(str, CP_RADIO))} 或 null，而不是 {cp!r}")
        self._cp_cost: int | None = None  # 本次运行在游戏里核对过的挑战pt消耗
        self._challenge_missing = 0  # 连续几次在演出首页没认出「挑战演出」
        self._challenge_taps = 0  # 点了几次「挑战演出」还没进去
        self._lb_empty_at: float | None = None  # 上次因 LB 用完改为消耗 0 的时刻
        # 用道具补充 LB（game.lb_refill）：还能补多少（None 为不限）、已经补了多少、是否不再补充（道具用完、额度用完）
        self._refill_left: int | None = config.game.lb_refill_limit or None
        self._refilled = 0
        self._refill_out = False
        self._title_since: float | None = None  # 标题画面第一次可以点击的时刻
        self._title_wait_since: float | None = None  # 标题画面开始等 TAP TO START 出现的时刻
        self._title_tap_at = -TITLE_TAP_GAP_S
        self._restarts = 0
        self._connect_errors = 0  # 进到游戏之前连续遇到「连接失败」的次数
        self._unknown_since: float | None = None  # 连续认不出画面的起点（每检查一次游戏进程重新计）
        self._still = None  # (起点, 缩略图)：认不出的画面从什么时候起没变过
        self._pause_template = load_template()  # 认演奏画面右上角的暂停按钮（重试前确认还在演奏）

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

    def look(self, still_ok: bool = False, blocking_ok: bool = False) -> tuple[Screen, list[OcrItem]]:
        """截图并判断画面。``still_ok``：画面长时间不动也正常（等结算时歌可能还在放），不按 FROZEN_S 判断卡住。

        看到服务器维护页时抛 :class:`ServerMaintenance`（开服之前什么都做不了），要求更新游戏时抛
        :class:`GameUpdateRequired`（要玩家自己去更新），``blocking_ok`` 时照常返回。
        """
        frame, _ = self.source.grab()
        self._frame = frame
        items = self._items = self.ocr.read(frame)
        screen = classify(items)
        if screen != self.last_screen:
            logger.debug("画面：%s", screen)
        self.prev_screen, self.last_screen = self.last_screen, screen
        if screen in CHALLENGE_SCREENS:
            self._challenge_taps = 0
        if screen is Screen.MAINTENANCE and not blocking_ok:
            period = maintenance_period(items)
            raise ServerMaintenance(f"服务器维护中{f'（维护时间 {period}）' if period else ''}")
        if screen is Screen.UPDATE_REQUIRED and not blocking_ok:
            raise self._fail("游戏有新版本，请先手动更新游戏（到应用商店下载最新版本）", GameUpdateRequired)
        if screen not in (Screen.TITLE, Screen.UNKNOWN):
            self._title_since = self._title_wait_since = None
        if screen in (Screen.TITLE, Screen.DATE_CHANGE):
            self._filter_status = None  # 重新登录后游戏里的筛选不一定还是原来的
        if screen is Screen.HOME:
            self._restarts = self._connect_errors = 0
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
        elif screen is Screen.NEW_SONG:
            self.tap(TAP_NEW_SONG, "TAP TO NEXT")
        elif screen in (Screen.RESULT,Screen.RESULT_REWARD, Screen.RESULT_EXP_NEXT):
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
        elif screen is Screen.CP_SETTING:
            self.tap(self._button(items, "取消", BTN_CP_CANCEL, CP_CANCEL_ROI), "取消")
        elif screen is Screen.LB_RECOVER:
            self._cancel_lb_recover(items)
        elif screen is Screen.LB_RECOVER_CONFIRM:  # 没在补充 LB 时不确认
            self.tap(self._button(items, "取消", BTN_LB_CONFIRM_CANCEL, LB_CONFIRM_CANCEL_ROI), "取消")
        elif screen is Screen.LB_RECOVERED:
            self.tap(self._button(items, "OK", BTN_LB_RECOVERED_OK, LB_RECOVERED_OK_ROI), "OK")
        elif screen is Screen.LIVE_OPTIONS:
            self.tap(self._button(items, "取消", BTN_OPTIONS_CANCEL), "取消")
        elif screen is Screen.SETTINGS:
            self.tap(BTN_BACK, "返回")
        elif screen is Screen.PAUSE:
            logger.warning("演出处于暂停状态，终止本次演出")
            self.tap(self._button(items, "终止", BTN_PAUSE_END), "终止")
        elif screen is Screen.ABORT_CONFIRM:
            self.tap(self._button(items, "终止", BTN_ABORT_CONFIRM, ABORT_CONFIRM_ROI), "终止")
        elif screen is Screen.RETRY_CONFIRM:
            # 重试只在 retry_live 里确认；别处看到说明演奏已经不管了，回到暂停菜单按上面终止
            self.tap(self._button(items, "取消", BTN_RETRY_CANCEL, RETRY_CANCEL_ROI), "取消")
        elif screen is Screen.HOME:
            self.tap(BTN_HOME_LIVE, "演出")
        elif screen is Screen.LIVE_TOP:
            if self.challenge:
                return self._enter_challenge(items)
            self.tap(self._button(items, "自由演出", BTN_FREE_LIVE), "自由演出")
        elif screen in self._foreign_screens:
            # 挑战演出和自由演出的页面互相走错了：退回演出首页再进。连续两次认出才退（页面淡入时可能认错）
            if self.prev_screen is not screen:
                return False
            self.tap(BTN_BAND_BACK, "返回")
        elif screen is Screen.DATE_CHANGE:
            logger.warning("游戏日期变更，回到标题画面重新登录")
            self.tap(self._button(items, "前往标题画面", BTN_TO_TITLE), "前往标题画面")
        elif screen is Screen.TITLE:
            self._on_title(items)
        elif screen is Screen.NOTIFY:
            self.tap(BTN_NOTIFY_CLOSE, "关闭「开启消息通知」")
        elif screen is Screen.CONNECT_ERROR:
            self._connect_errors += 1
            if self._connect_errors > MAX_CONNECT_RETRIES:
                raise self._fail(f"连续 {MAX_CONNECT_RETRIES} 次重新登录都连接失败（网络或服务器问题）")
            logger.warning("连接失败（发生网络连接错误），%.0f 秒后回到标题画面重新登录", CONNECT_RETRY_WAIT_S)
            self._sleep(CONNECT_RETRY_WAIT_S)
            self.tap(self._button(items, "返回标题画面", BTN_TO_TITLE), "返回标题画面")
        elif screen is Screen.DATA_DOWNLOAD:
            logger.info("下载追加的游戏数据")
            self.tap(self._button(items, "OK", BTN_DOWNLOAD_OK, DOWNLOAD_OK_ROI), "OK（数据下载）")
        elif screen is Screen.LOGIN_BONUS:
            self.tap(TAP_LOGIN_BONUS, "登录奖励")
        elif screen in (Screen.REWARD, Screen.GRADE_UP, Screen.BOND_UP):
            self.tap(self._button(items, "OK", BTN_REWARD_OK, CLOSE_ROI), "OK")
        else:
            return False
        return True

    def _on_title(self, items: list[OcrItem]) -> None:
        """标题画面：出现「TAP TO START」后点击登录。日期变更回到标题后曾出现怎么点都没反应的情况
        （登录 SDK 卡住），只能重启游戏。一直不出现「TAP TO START」时报错停下：被没见过的弹窗挡住时
        （之前遇到过「开启消息通知」），标题画面会让导航的超时一直重新计，不会自己结束。"""
        if find(items, "确定") or find(items, "取消") or find(items, "OK", exact=True):
            raise self._fail("标题画面上有未知弹窗")
        now = time.monotonic()
        if not title_startable(items):
            if self._title_wait_since is None:  # 刚启动，还在加载
                self._title_wait_since = now
            elif now - self._title_wait_since > TITLE_LOADING_S:
                raise self._fail(f"标题画面 {TITLE_LOADING_S:.0f}s 没出现 TAP TO START（可能被没见过的弹窗挡住了）")
            return
        self._title_wait_since = None
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

    def relogin(self, timeout_s: float = 180.0) -> Screen:
        """停在服务器维护页时回到标题画面重新登录，等到进入游戏；还在维护时抛 :class:`ServerMaintenance`。"""
        screen, items = self.look(blocking_ok=True)
        if screen is Screen.MAINTENANCE:
            self.tap(self._button(items, "返回标题画面", BTN_MAINTENANCE_TO_TITLE, CLOSE_ROI), "返回标题画面")
            self._sleep(self.settle_s)
        return self.ensure_in_game(timeout_s)

    def ensure_band_confirm(self, difficulty: str | None = None, timeout_s: float = 90.0) -> None:
        deadline = time.monotonic() + timeout_s
        diff = difficulty or self.cfg.game.difficulty
        while True:
            screen, items = self.look()
            if screen is self.band_screen:
                return
            if screen in RELOGIN_SCREENS:
                deadline = time.monotonic() + timeout_s
            if time.monotonic() > deadline:
                raise self._fail(f"{timeout_s:.0f}s 内未能进入{self.band_screen}页")
            if screen is self.song_screen:
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
            if screen not in (Screen.BAND_CONFIRM, Screen.CHALLENGE_BAND_CONFIRM):
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

        开了 ``game.lb_refill`` 时先看持有数，少于每局消耗就用道具补充（:meth:`_refill`）。
        LB 不足时游戏照样开始演出，只消耗持有的全部（持有 0 就不消耗），有时则弹出恢复 LIVE BOOST：点取消
        （能用道具补充的话再从消耗设置进去补）。``lb_short`` 为 ``zero`` 时本局改为消耗 0；为 ``stop`` 时
        点 LIVE START 前先核对持有数，用完了（或弹出了恢复窗口）就留在乐队确认页并抛出 :class:`LbExhausted`。
        """
        if lb_short not in ("zero", "stop"):
            raise ValueError(f"未知的 LB 不足处理方式：{lb_short}")
        if self.challenge:
            self._check_cp()
            want = None
        else:
            want = self._lb_want()
            held = self._refill_if_short(want) if want and self._can_refill() else None
            if lb_short == "stop" and want:
                if held is None and self._lb_bar_empty():
                    # 顶栏偶尔漏读，打开消耗设置弹窗再核对一次（顺便确认消耗设置）
                    held = self.set_lb_cost(want)
                if held == 0:
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
            if screen is Screen.LB_RECOVER and not self.challenge:
                # LB 用完时要求先恢复：取消，能用道具补充就补，否则本局改为消耗 0（一段时间后或玩家升级后再按配置设置）
                if self._lb_cost == 0:
                    raise self._fail("LB 消耗为 0 仍弹出恢复 LIVE BOOST")
                self._cancel_lb_recover(items)
                self._wait_for(Screen.BAND_CONFIRM)
                if not (want and self._can_refill() and self.set_lb_cost(want, refill=True)):
                    if lb_short == "stop":
                        raise LbExhausted("LB 已用完")
                    self._lb_empty_at = time.monotonic()
                    self.set_lb_cost(0)
                self.tap(BTN_LIVE_START, "LIVE START")
                started = time.monotonic()
                retapped = False
                unknown_since = None
            elif screen is self.band_screen:
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

    def _in_play(self, frame) -> bool:
        return self._pause_template is not None and pause_visible(frame, self._pause_template)

    def _wait_play(self, timeout_s: float = 2.5, polls: int = 60) -> bool:
        """不做 OCR、只认暂停按钮，快速等演奏画面出现（确认重试后约 0.36s，歌曲已经开始了）。"""
        if self._pause_template is None:
            return False
        deadline = time.monotonic() + timeout_s
        for _ in range(polls):
            frame, _ = self.source.grab()
            if self._in_play(frame):
                self._frame = frame
                return True
            if time.monotonic() > deadline:
                break
            self._sleep(0.03)
        return False

    def retry_live(self, timeout_s: float = 10.0) -> None:
        """演奏中暂停 → 重试 → 确认重试，这首歌从头开始（同步失败、演奏明显对不上时，不用干等歌曲放完）；
        点完立即返回，首音符同步要尽早开始看画面。

        先确认还在演奏画面（右上角的暂停按钮），不在就什么都不点。重试没生效时点「继续」接着放完这首歌
        （「终止」拿不到演出奖励），抛出 NavigationError。
        """
        self.look()
        if not self._in_play(self._frame):
            raise self._fail("没有在演奏画面上（看不到右上角的暂停按钮），不重试")
        self.tap(BTN_PLAY_PAUSE, "暂停")
        started = paused_at = time.monotonic()
        retry_at = confirm_at = unknown_at = None
        screen = Screen.UNKNOWN
        items: list[OcrItem] = []
        while time.monotonic() - started < timeout_s:
            self._sleep(0.3)
            screen, items = self.look()
            t = time.monotonic()
            unknown_at = (unknown_at or t) if screen is Screen.UNKNOWN else None
            if screen is Screen.PAUSE:
                if retry_at is None or t - retry_at > 2:  # 第一次，或者点了没反应
                    self.tap(self._button(items, "重试", BTN_PAUSE_RETRY), "重试")
                    retry_at = t
            elif screen is Screen.RETRY_CONFIRM:
                if confirm_at is None or t - confirm_at > 2:
                    self.tap(self._button(items, "重试", BTN_RETRY_CONFIRM, RETRY_CONFIRM_ROI), "确认重试")
                    confirm_at = t
                    if self._wait_play():
                        return  # 演奏画面回来了，歌曲已经开始
            elif screen is Screen.LIVE_OPTIONS:  # 重试后如果又弹出演出前选项设置，和开始时一样点「演出」
                self._check_speed(items)
                self.tap(self._button(items, "演出", BTN_OPTIONS_START), "演出")
                return
            elif screen is Screen.UNKNOWN:
                if confirm_at is not None:
                    return  # 确认弹窗关掉了，正在重新开始
                if retry_at is not None:
                    if t - unknown_at >= 3:
                        return  # 没有弹出确认就重新开始了
                elif t - paused_at > 2 and self._in_play(self._frame):  # 点暂停没生效
                    self.tap(BTN_PLAY_PAUSE, "暂停")
                    paused_at = t
            else:
                raise self._fail("重试时出现意外画面")
        if screen is Screen.RETRY_CONFIRM:
            self.tap(self._button(items, "取消", BTN_RETRY_CANCEL, RETRY_CANCEL_ROI), "取消")
            self._sleep(1.0)
            screen, items = self.look()
        if screen is Screen.PAUSE:
            self.tap(self._button(items, "继续", BTN_PAUSE_CONTINUE), "继续")
        raise self._fail("暂停后重试没有生效")

    def set_lb_cost(self, cost: int, timeout_s: float = 15.0, refill: bool = False) -> int | None:
        """乐队确认页 → 消耗LB → 选中 ``cost``、按像素核对 → OK，回到乐队确认页（游戏会记住设置）。
        返回弹窗上读到的 LB 持有数。``refill`` 时持有数少于 ``cost`` 就点弹窗里的「恢复」用道具补充一次
        （:meth:`_refill`），否则绝不点「恢复」。"""
        self.tap(BTN_LB_COST, "消耗LB")
        opened = time.monotonic()
        deadline = opened + timeout_s
        radio_taps = 0
        ok_at = recover_at = None
        refilled = False
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
                if recover_at is not None and not refilled:
                    if time.monotonic() - recover_at < 3:
                        continue  # 恢复LIVE BOOST 正在打开
                    logger.warning("点「恢复」没有打开恢复LIVE BOOST，这次不补充")
                    refilled = True
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
                if refill and recover_at is None and held is not None and held < cost and self._can_refill():
                    logger.info("LB 持有 %d 个，少于每局消耗 %d 个，用道具补充", held, cost)
                    self.tap(self._button(items, "恢复", BTN_LB_RECOVER, LB_RECOVER_ROI), "恢复")
                    recover_at = time.monotonic()
                    continue
                logger.debug("LB 消耗设置为 %d（持有 %s）", cost, "?" if held is None else held)
                self.tap(self._button(items, "OK", BTN_LB_OK), "OK")
                ok_at = time.monotonic()
            elif screen is Screen.LB_RECOVER and recover_at is not None and not refilled:
                self._refill(items, held, cost - held)
                refilled = True
                deadline = time.monotonic() + timeout_s
            elif screen is not Screen.UNKNOWN:  # UNKNOWN：弹窗淡入淡出
                raise self._fail("设置 LB 消耗时出现意外画面")
        raise self._fail("未能设置 LB 消耗")

    def _can_refill(self) -> bool:
        return bool(self.cfg.game.lb_refill and self.cfg.game.lb_cost) and not self._refill_out

    def _refill_if_short(self, want: int) -> int | None:
        """乐队确认页顶栏的 LB 持有数少于 ``want``（或没读到）时打开消耗设置弹窗核对，确实不够就用道具补充。
        返回持有数（读不到为 None）。"""
        screen, items = self.look()
        held = lb_bar_held(items) if screen is Screen.BAND_CONFIRM else None
        if held is not None and held >= want:
            return held
        return self.set_lb_cost(want, refill=True)

    def _tab_selected(self, point: tuple[int, int]) -> bool:
        """恢复LIVE BOOST 左边的分页 ``point`` 是否选中：选中的是青绿色（BGR 约 176,162,84），没选中的是深蓝（约 140,84,70）。"""
        h, w = self._frame.shape[:2]
        x, y = round(point[0] * w / DESIGN_W), round(point[1] * h / DESIGN_H)
        r = max(1, round(5 * w / DESIGN_W))
        _, g, red = self._frame[y - r : y + r + 1, x - r : x + r + 1].reshape(-1, 3).mean(axis=0)
        return g > 130 and g - red > 50

    def _items_tab(self) -> bool:
        """最近一帧的恢复LIVE BOOST 选中的是「道具」页（星钻、观看广告都没选中）。"""
        return self._tab_selected(LB_TAB_ITEMS) and not any(self._tab_selected(p) for p in LB_TAB_OTHERS)

    def _refill(self, items: list[OcrItem], held: int, need: int, timeout_s: float = 15.0) -> int:
        """恢复LIVE BOOST 弹窗：在「道具」页按 :func:`plan_refill` 选好饮料，核对数量和预览后 OK → 确认 OK →
        已恢复 OK，回到消耗设置弹窗，返回补充的 LB 数。``held`` 是消耗设置弹窗上读到的持有数（预览左边的数字
        常常漏读，就用它）。选中的不是「道具」页、没有能用的饮料（或剩余额度不够）、
        数量或预览对不上时点取消、返回 0，本次运行不再补充。只点饮料行的「+」和这几个弹窗的 OK / 取消，
        「星钻」「观看广告」分页绝不点。"""
        drinks = lb_drinks(items)
        problem = None
        if not self._items_tab():
            problem = "恢复LIVE BOOST 选中的不是「道具」页"
        else:
            plan = plan_refill(drinks, need, self._refill_left)
            gain = sum(n * d.lb for n, d in zip(plan, drinks))
            if not gain:
                if not any(d.owned > d.chosen for d in drinks):
                    logger.info("道具里没有 LIVE BOOST饮料了，不再用道具补充 LB")
                else:
                    logger.info("剩余补充额度（%s 个 LB）不够用一瓶饮料，不再用道具补充 LB", self._refill_left)
                return self._refill_cancel()
            for n, d in zip(plan, drinks):
                for _ in range(n):
                    self.tap((LB_PLUS_X, round(d.y)), f"{d.name} +")
                    self._sleep(0.3)
            for _ in range(3):  # 预览的数字很小，偶尔漏读，多看几帧
                self._sleep(0.5)
                screen, items = self.look()
                chosen = {d.lb: d.chosen for d in lb_drinks(items)}  # 按种类（+1 / +10）对，名字每次 OCR 可能略有不同
                before, after = lb_preview(items) or (None, None)
                if screen is not Screen.LB_RECOVER or not self._items_tab():
                    problem = "选饮料时画面变了"
                elif any(chosen.get(d.lb) != d.chosen + n for n, d in zip(plan, drinks) if n):
                    problem = f"选好的饮料数量对不上（{chosen}）"
                elif after is None or after - (held if before is None else before) != gain:
                    problem = f"恢复预览 {before} ▶ {after} 和要补充的 {gain} 个对不上（持有 {held}）"
                else:
                    problem = None
                    break
        if problem:
            logger.error("%s，点取消，不再用道具补充 LB", problem)
            return self._refill_cancel()
        used = "、".join(f"{d.name}×{n}" for n, d in zip(plan, drinks) if n)
        self.tap(self._button(items, "OK", BTN_LB_RECOVER_OK, LB_RECOVER_OK_ROI), "OK")
        ok_at = time.monotonic()
        confirmed = aborted = False
        tapped: dict[Screen, float] = {}  # 每个弹窗上次点击的时刻（点了 2s 内弹窗还在，多半是正在关闭）
        deadline = ok_at + timeout_s
        while time.monotonic() < deadline:
            self._sleep(0.5)
            screen, items = self.look()
            t = time.monotonic()
            if t - tapped.get(screen, -math.inf) < 2:
                continue
            if screen is Screen.LB_RECOVER_CONFIRM:
                amount = lb_recover_amount(items)
                if amount == gain and not aborted:
                    self.tap(self._button(items, "OK", BTN_LB_CONFIRM_OK, LB_CONFIRM_OK_ROI), "确认恢复")
                    confirmed = True
                else:
                    if not aborted:
                        logger.error("确认弹窗上要恢复 %s 个，和选好的 %d 个对不上，点取消，不再用道具补充 LB", amount, gain)
                    self.tap(self._button(items, "取消", BTN_LB_CONFIRM_CANCEL, LB_CONFIRM_CANCEL_ROI), "取消")
                    aborted = True
                tapped[screen] = t
            elif screen is Screen.LB_RECOVERED:
                self.tap(self._button(items, "OK", BTN_LB_RECOVERED_OK, LB_RECOVERED_OK_ROI), "OK")
                tapped[screen] = t
            elif screen is Screen.LB_RECOVER:
                if aborted:
                    self.tap(self._button(items, "取消", BTN_LB_RECOVER_CANCEL, LB_RECOVER_CANCEL_ROI), "取消")
                    tapped[screen] = t
                elif not confirmed and t - ok_at > 3:  # 点 OK 没生效
                    self.tap(self._button(items, "OK", BTN_LB_RECOVER_OK, LB_RECOVER_OK_ROI), "OK")
                    ok_at = t
            elif screen is Screen.LB_SETTING:
                if not confirmed:
                    self._refill_out = True
                    return 0
                return self._refilled_ok(gain, used)
            elif screen is not Screen.UNKNOWN:
                raise self._fail("用道具恢复 LB 时出现意外画面")
        raise self._fail("用道具恢复 LB 没有完成")

    def _refill_cancel(self) -> int:
        self._refill_out = True
        self.tap(self._button(self._items, "取消", BTN_LB_RECOVER_CANCEL, LB_RECOVER_CANCEL_ROI), "取消")
        return 0

    def _refilled_ok(self, gain: int, used: str) -> int:
        self._refilled += gain
        self._lb_empty_at = None
        if self._refill_left is not None:
            self._refill_left -= gain
        left = "" if self._refill_left is None else f"，还能补 {self._refill_left} 个"
        logger.info("用道具补充了 %d 个 LB（%s），本次共补充 %d 个%s", gain, used, self._refilled, left)
        if self._refill_left is not None and self._refill_left <= 0:
            logger.info("已补充 %d 个 LB，达到设置的上限，不再用道具补充", self._refilled)
            self._refill_out = True
        return gain

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
            if screen in (Screen.LIVE_END, *RESULT_POPUPS, Screen.PAUSE, Screen.ABORT_CONFIRM, Screen.RETRY_CONFIRM):
                self._common_step(screen, items)
                self._sleep(self.settle_s)
            elif screen in (
                Screen.RESULT_REWARD,
                Screen.RANK_UP,
                Screen.RESULT_EXP_NEXT,
                Screen.RESULT_EXP,
                *SONG_SCREENS,
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
            if screen in SONG_SCREENS:
                return
            if screen in RELOGIN_SCREENS:
                deadline = time.monotonic() + timeout_s
            if self._common_step(screen, items):
                self._sleep(self.settle_s)
            else:
                self._sleep(0.5)
        raise self._fail("未能离开结算页")
