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

激奏舞台（``loop.arena``）从演出首页的「激奏舞台」进，激奏舞台首页代替乐队确认页（选乐队、设 LB 消耗、LIVE START），
匹配后转盘定歌、只打 EXPERT（见 :mod:`.arena`）；结算页和自由演出一样，「再次演出」回到激奏舞台首页。

开了 ``loop.use_skip`` 时，自由演出先用乐队确认页的「跳过」（每天有次数）消耗 LB::

    乐队确认 -[跳过]-> 确认跳过（和消耗设置一样选消耗）-[跳过]-> 跳过结果 -[下一步]-> 跳过结果（活动pt）-[OK]-> 乐队确认
"""

from __future__ import annotations

import logging
import math
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from concurrent.futures import Future
from pathlib import Path

import cv2
import numpy as np

from ..config import Config
from ..device.base import FrameSource
from ..player.guard import load_template, pause_visible
from ..records import RecordStore, game_day
from ..result_reader import OcrItem, ResultCounts, merge, parse_int, parse_totals
from ..runner import GameUpdateRequired, LbExhausted, NavigationError, ScreenFrozen, ServerMaintenance, SongLabel
from .jacket import JacketMatcher, crop_jacket
from .ocr import MaaOcr
from .screens import (
    BOTTOM_LEFT_ROI,
    CLOSE_ROI,
    CP_RADIO,
    LB_ALL_CHECK,
    LB_LIST_DRAG,
    LB_MAX_COST,
    LB_PLUS_X,
    LB_TAB_ITEMS,
    LB_TAB_OTHERS,
    LEVEL_ROI,
    NEXT_ROI,
    NOTIFY_LOGO_Y,
    OK_POPUP_BLOCK,
    OK_POPUP_ROI,
    RELOGIN_SCREENS,
    RESULT_COMBO_ROI,
    RESULT_SCORE_ROI,
    RESULT_TOTAL_VARIANTS,
    SKIP_ALL_CHECK,
    SKIP_BTN_ROI,
    UNLOCK_CLOSE_ROI,
    LbDrink,
    LbRows,
    Rect,
    Screen,
    arena_guess,
    band_confirm_song,
    center,
    classify,
    find,
    lb_all_left,
    lb_all_used,
    lb_bar_held,
    lb_bar_timer,
    lb_drinks,
    lb_held,
    lb_preview,
    lb_recover_amount,
    lb_rows,
    loading,
    login_expanded,
    login_rows,
    maintenance_period,
    next_button,
    note_speed,
    parse_level,
    result_cells,
    sdk_point,
    sdk_scale,
    skip_button,
    skip_left,
    title_startable,
)
from .account import BTN_MENU_CLOSE, AccountMixin
from .arena import ARENA_SCREENS, ArenaMixin
from .challenge import BTN_CP_CANCEL, CHALLENGE_SCREENS, CP_CANCEL_ROI, ChallengeMixin
from .daily import DailyMixin
from .song_select import (
    BTN_BAND_BACK,
    BTN_DIFFICULTY,
    ListPositions,
    SongSelectMixin,
    filter_open,
    song_locked,
)
from .story import BTN_PLAYER_MENU, BTN_SKIP, BTN_SKIP_CONFIRM, PLAYER_MENU_GAP_S, SKIP_CONFIRM_ROI, StoryMixin

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
# 「开启消息通知」弹窗（B 站 SDK）右上角的 ⓧ，1280x720 下量的，点之前按 sdk_point 换算
BTN_NOTIFY_CLOSE = (822, 171)
TAP_LOGIN_BONUS = (640, 650)  # 登录奖励演出：点下方空白处继续
BTN_REWARD_OK = (640, 659)  # 获得奖励 / GRADE UP 弹窗底部的 OK
BTN_UNLOCK_CLOSE = (640, 570)  # 乐曲解锁 / 故事解锁：关闭
TAP_NEW_SONG = (640, 627)  # 追加乐曲演出：TAP TO NEXT
# 主界面角色对话：点下面的对话框关掉（关掉后这里是主界面底部「招募」「商店」之间的空隙）
TAP_HOME_TALK = (640, 600)
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
# 闪退后的桌面先由 _check_app 处理）。模拟器截图没有噪声，停住的画面逐像素相同；动画、转圈的缩略图会变几十上百。
# 认出了的画面点了 FROZEN_TAPS 次以上、这么久一点没变（点的位置不对，或者游戏不响应）也算（标题画面另有 TITLE_STUCK_S）
FROZEN_S = 60.0
FROZEN_THUMB = (160, 90)
FROZEN_DIFF = 4
FROZEN_TAPS = 3
# 认不出的画面停住了先试着点一下恢复：每停住 RECOVER_S 点一次，最多 RECOVER_TAPS 次，还不行到 FROZEN_S 照常重启游戏。
# 先找 RECOVER_BUTTONS 里的按钮（文字完全相等）；没有时点「下一步」、再没有点下方空白处 TAP_RECOVER，这两种只在画面上
# 没有 RECOVER_BLOCK 里的字时点（可能点到购买、用星钻、消耗道具上）。要能确认游戏在前台才点（不点到模拟器桌面上）
RECOVER_S = 8.0
RECOVER_TAPS = 3
RECOVER_BUTTONS = ("关闭", "取消")
RECOVER_BLOCK = (*OK_POPUP_BLOCK, "兑换", "消耗", "跳过", "解锁", "升级")
TAP_RECOVER = (640, 600)
# 保底：认不出的画面一直在动（循环播放的演出等，不算上面的停住）这么久，也当成卡住了。卡住时重启游戏
# （回到主界面之前最多 MAX_RESTARTS 次，重启次数用完才报错停下）
UNKNOWN_STUCK_S = 150.0
# 按上面两条判断卡住的画面（故事播放画面是认出来了，但一直跳不过去也算卡住；主界面角色对话点了关不掉也是）
STUCK_SCREENS = (Screen.UNKNOWN, Screen.RESULT_OTHER, Screen.STORY_PLAYER, Screen.HOME_TALK)
# LB 用完改为消耗 0 后，这么久之内不再尝试按配置消耗（LB 随时间恢复；玩家升级时回满，看到升级画面就重新尝试）
LB_EMPTY_RETRY_S = 30 * 60
# start_live("auto")（挂机不等 LB）：持有 LB 时每局消耗这么多，没有时消耗 0
LB_AUTO_COST = 1
# 不用解锁就能选的最大消耗（T.G.W CARD 10 级解锁 4、14 级解锁 5）
LB_BASE_COST = 3
# 「全部消耗」（每天能用的次数看月卡，一般 1 次）一次最多消耗这么多；持有这么多以上，或者比一局局消耗能省这么多局以上时用（game.lb_auto）
LB_ALL_MAX = 10
LB_ALL_SAVE = 2
# 乐队确认页「跳过」：确认跳过弹窗左「关闭」右「跳过」；跳过结果页右下「下一步」，活动pt页右下「OK」
BTN_SKIP_LIVE = (780, 664)
BTN_SKIP_CLOSE = (497, 657)
BTN_SKIP_LIVE_CONFIRM = (781, 657)
BTN_SKIP_RESULT_OK = (1126, 659)
# 结算页出现约 1s 后可能叠上来的弹窗（首次达成奖励、评级提升及其奖励等），读数前先关掉
RESULT_POPUPS = (Screen.ACHIEVEMENT, Screen.POPUP, Screen.GRADE_UP, Screen.REWARD, Screen.OK_POPUP)
# 已经进入游戏、可以开始任务的画面（启动游戏时等到这些之一）
IN_GAME_SCREENS = frozenset(
    (Screen.HOME, Screen.LIVE_TOP, Screen.SONG_SELECT, Screen.BAND_CONFIRM, *CHALLENGE_SCREENS, Screen.ARENA_TOP)
)
# 乐曲选择、乐队确认页（自由演出和挑战演出的）
SONG_SCREENS = (Screen.SONG_SELECT, Screen.BAND_CONFIRM, *CHALLENGE_SCREENS)
# 随机选曲后选中的还是没解锁的歌时最多点几次
RANDOM_LOCKED_TRIES = 5
# 要重试时还在开场（曲名卡片还没消失，看不到暂停按钮；首音符同步被开场的灯光骗了）：最多等演奏画面出来这么久
INTRO_WAIT_S = 15.0
INTRO_POLL_S = 0.15
# 还在加载（「NOW LOADING」，网络慢时进歌要很久，#98）：每次等 INTRO_WAIT_S，加载完后再等一次开场，最多等这么多次
LOADING_WAIT_ROUNDS = 4
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


def lb_auto_cost(held: int, top: int, all_ok: bool) -> tuple[int, bool]:
    """自动选消耗（game.lb_auto）：持有 ``held`` 个、最大能选 ``top`` 时这一局消耗几个、要不要勾「全部消耗」。

    平时选 ``min(held, top)``（持有不够就全部消耗掉）。「全部消耗」今天还能用（``all_ok``）时，持有 :data:`LB_ALL_MAX`
    个以上（它最多就消耗这么多），或者一局局消耗完要多打 :data:`LB_ALL_SAVE` 局以上，就勾上。
    """
    cost = min(held, top)
    use_all = all_ok and (held >= LB_ALL_MAX or math.ceil(held / max(top, 1)) - 1 >= LB_ALL_SAVE)
    return cost, use_all


@dataclass
class _LbPick:
    """在一个 LB 消耗弹窗里选消耗时的尝试次数。"""

    taps: int = 0  # 点单选按钮、拖列表
    all_taps: int = 0  # 点「全部消耗」
    dragged: bool = False  # 找最大消耗时拖过列表

class GameNavigator(SongSelectMixin, DailyMixin, StoryMixin, ChallengeMixin, ArenaMixin, AccountMixin):
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
        store: RecordStore | None = None,
        app_in_front: Callable[[], bool] | None = None,
    ):
        self.cfg = config
        self.source = source
        self.touch = touch
        self.ocr = ocr
        self.stop = stop or threading.Event()
        self.settle_s = settle_s
        self._jackets = jackets
        self.store = store  # 记「全部消耗」今天用过没有
        self._rows_matcher: JacketMatcher | None = None  # 认列表缩略图用（第一次按曲目选歌时建）
        self.list_positions = ListPositions()
        self._filter_status: str | None = None  # 本次运行设好的「游玩状况」筛选（STATUS_OPTIONS 的键），不确定时为 None
        self.restart_app = restart_app
        self.app_running = app_running
        self.app_in_front = app_in_front  # 游戏的界面在前台（认不出的画面上试着点一下恢复之前查）
        self.last_screen = Screen.UNKNOWN
        self.prev_screen = Screen.UNKNOWN  # 上一次 look 认出的画面
        self._frame = None
        self._items: list[OcrItem] = []
        self._ok_popup_seen: str | None = None  # 上一个点掉的没见过的弹窗的文字（同一个只存一张截图）
        self._recover_seen: str | None = None  # 上一个试着点一下恢复的认不出的画面的文字（同上）
        lb = config.game.lb_cost
        if lb is not None and lb not in range(LB_MAX_COST + 1):
            raise ValueError(f"game.lb_cost 应为 0~{LB_MAX_COST} 或 null，而不是 {lb!r}")
        self._lb_cost: int | None = None  # 本次运行已在游戏里设置并核对过的 LB 消耗
        self._lb_all = False  # 上面设置时勾了「全部消耗」（开始演出后记下今天用过了）
        self._lb_all_out = False  # 本次运行「全部消耗」勾不上（今天的次数用完了？）
        self._lb_all_jam = False  # 本次运行点了也勾不上过（弹窗上写着还剩次数也不再试）
        self._lb_top: int | None = None  # 消耗设置里最大能选几个（看过之后）
        self._lb_capped = False  # 已经提示过 game.lb_cost 超过了能选的最大值
        # 挑战演出：要去的乐曲选择、乐队确认页换成挑战演出的，另一种（自由演出的）就退回演出首页。
        # 激奏舞台：要去的是激奏舞台首页（选乐队、设 LB 消耗、LIVE START 都在这页），走到自由演出、挑战演出的页面就退回
        self.challenge = config.loop.challenge
        self.arena = config.loop.arena
        if self.arena:
            self.song_screen = self.band_screen = Screen.ARENA_TOP
            self._foreign_screens = frozenset((Screen.SONG_SELECT, Screen.BAND_CONFIRM, *CHALLENGE_SCREENS))
        else:
            self.song_screen = Screen.CHALLENGE_SONG_SELECT if self.challenge else Screen.SONG_SELECT
            self.band_screen = Screen.CHALLENGE_BAND_CONFIRM if self.challenge else Screen.BAND_CONFIRM
            self._foreign_screens = (
                frozenset((Screen.SONG_SELECT, Screen.BAND_CONFIRM)) if self.challenge else CHALLENGE_SCREENS
            ) | {Screen.ARENA_TOP}
        # 设置 LB 消耗、看持有数的页面（顶栏有 LB、下面有「消耗LB」）
        self._lb_screen = Screen.ARENA_TOP if self.arena else Screen.BAND_CONFIRM
        self._arena_lb: int | None = None  # 激奏舞台这一局开始时的 LB 消耗设置
        self._rank_up_taps = 0  # 激奏舞台段位提升画面点了几次（轮流点两处）
        self._arena_missed = 0  # 激奏舞台连续几局对局开始了却没认出难度选择页
        self._band_templates: dict[str, np.ndarray] | None = None  # 激奏舞台首页的乐队标志（第一次用时加载）
        self._side_templates: dict[str, np.ndarray | None] = {}  # 主界面左侧入口的图标（第一次用时加载）
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
        self._still = None  # (起点, 缩略图)：画面从什么时候起没变过
        self._still_taps = 0  # 那之后点了几次
        self._stuck_since: float | None = None  # 从什么时候起一直认不出画面（不论在不在动）
        self._story_menu_at = -PLAYER_MENU_GAP_S  # 上次点故事播放画面右上角菜单的时刻
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

    def _jackets_or_none(self) -> JacketMatcher | None:
        """同 :attr:`jackets`，但加载出错时不抛出：记一条警告、之后不再认封面（只认曲名），返回 None。"""
        try:
            return self.jackets
        except NavigationError:
            raise
        except Exception as e:
            logger.warning("曲目封面加载失败，只按曲名认歌：%s", e)
            self._jackets = None
            return None

    def look(self, still_ok: bool = False, blocking_ok: bool = False) -> tuple[Screen, list[OcrItem]]:
        """截图并判断画面。``still_ok``：画面长时间不动也正常（等结算时歌可能还在放），不按 FROZEN_S 判断卡住。

        看到服务器维护页时抛 :class:`ServerMaintenance`（开服之前什么都做不了），要求更新游戏时抛
        :class:`GameUpdateRequired`（要玩家自己去更新），``blocking_ok`` 时照常返回。
        """
        frame, _ = self.source.grab()
        self._frame = frame
        items = self._items = self.ocr.read(frame)
        screen = classify(items)
        # 故事播放画面也是名牌加台词，可能认成主界面角色对话：先按右上角的菜单按钮认
        if screen in (Screen.UNKNOWN, Screen.HOME_TALK) and isinstance(frame, np.ndarray) and self._player_menu_button():
            screen = Screen.STORY_PLAYER
        # 激奏舞台任务里认不出的画面：还没适配的语言（#105）按不分语言的特征猜是哪一页（简中等照常按文字认，不走这里）
        guessed = False
        if screen is Screen.UNKNOWN and self.arena and (guess := arena_guess(items)) is not None:
            screen, guessed = guess, True
        if screen != self.last_screen:
            logger.debug("画面：%s%s", screen, "（认不出文字，按位置猜的）" if guessed else "")
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
        self._check_stuck(screen, items, still_ok)
        return screen, items

    def _heal_display_id(self) -> None:
        """游戏在运行却一直认不出画面时，按包名重新解析一次 display_id（#58）。

        自动重启后如果 display_id 被解析成了模拟器桌面之类的非游戏画面（见 ``wait_for_game_display``），
        对着那个画面 OCR 永远认不出。这里查一次按包名的值，变了就换过来，让下一帧截到游戏的画面。
        """
        ipc = getattr(self.source, "ipc", None)
        if ipc is None or not hasattr(ipc, "game_display_id"):
            return
        try:
            d = ipc.game_display_id()
        except Exception as e:  # DLL 偶发异常不该让导航崩掉
            logger.debug("重新解析 display_id 失败：%s", e)
            return
        if d < 0 or d == ipc.display_id:
            return
        logger.warning("认不出画面，游戏的 display_id 变成了 %s（当前 %s），改用新的", d, ipc.display_id)
        ipc.display_id = d

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
            self._heal_display_id()
            return
        if self.restart_app is None or self._restarts >= MAX_RESTARTS:
            raise self._fail("游戏没有在运行（可能闪退了）")
        logger.warning("游戏没有在运行（可能闪退了），重新启动")
        self._restarts += 1
        self.restart_app()

    def _check_frozen(self, screen: Screen, still_ok: bool) -> None:
        """画面 FROZEN_S 内一点没变，而且认不出（或以为还在播动画）、或者认出了但点了 FROZEN_TAPS 次都没反应：重启游戏；
        不能重启（重启次数用完）时抛 :class:`ScreenFrozen` 让任务停下，不再反复重试。要能确认游戏还在运行才判断。"""
        if still_ok or screen is Screen.TITLE or self.app_running is None:
            self._still = None
            return
        if not isinstance(self._frame, np.ndarray):  # 测试里用画面名代替截图
            return
        gray = cv2.cvtColor(self._frame, cv2.COLOR_BGR2GRAY)
        thumb = cv2.resize(gray, FROZEN_THUMB, interpolation=cv2.INTER_AREA)
        now = time.monotonic()
        if self._still is None or int(cv2.absdiff(self._still[1], thumb).max()) > FROZEN_DIFF:
            self._still = (now, thumb)
            self._still_taps = 0
            return
        since = self._still[0]
        unknown = screen in STUCK_SCREENS
        if now - since < FROZEN_S or not (unknown or self._still_taps >= FROZEN_TAPS):
            return
        try:
            running = self.app_running()
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("检查游戏进程失败：%s", e)
            running = False
        if not running:
            self._still = (now, thumb)  # 过一阵再查
            return
        if unknown:
            why = f"画面 {now - since:.0f}s 没有变化，也认不出是什么页面（可能是没见过的页面或弹窗）"
        else:
            why = f"{screen}点了 {self._still_taps} 次，画面 {now - since:.0f}s 都没有变化（点的位置不对或者游戏没反应）"
        self._restart_stuck(why, ScreenFrozen)

    def _check_stuck(self, screen: Screen, items: list[OcrItem], still_ok: bool) -> None:
        """保底：一直认不出画面（画面在动，_check_frozen 不管）UNKNOWN_STUCK_S，就重启游戏。加载、下载中不算；
        不能重启游戏（没有 ``restart_app``）时不管，等导航自己超时。"""
        if still_ok or screen not in STUCK_SCREENS or loading(items) or self.restart_app is None:
            self._stuck_since = None
            return
        now = time.monotonic()
        if self._stuck_since is None:
            self._stuck_since = now
        elif now - self._stuck_since >= UNKNOWN_STUCK_S:
            self._restart_stuck(f"{now - self._stuck_since:.0f}s 都认不出是什么页面（可能是没见过的页面或演出）")

    def _restart_stuck(self, why: str, error: type[NavigationError] = NavigationError) -> None:
        """卡在认不出的画面上：重启游戏（之后从标题画面重新登录）；不能重启或回到主界面之前已经重启了
        MAX_RESTARTS 次时抛 ``error``。"""
        self._still = self._stuck_since = None
        if self.restart_app is None or self._restarts >= MAX_RESTARTS:
            raise self._fail(why, error)
        logger.warning("%s，重启游戏（截图 %s）", why, self.save_debug(self.last_screen.name.lower()))
        self._restarts += 1
        self.restart_app()

    def tap(self, point: tuple[int, int], what: str = "") -> None:
        w, h = self.source.size
        x, y = round(point[0] * w / DESIGN_W), round(point[1] * h / DESIGN_H)
        logger.debug("点击 %s (%d,%d)", what, x, y)
        self.touch.tap(x, y)
        self._still_taps += 1

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
            if find(items, "前往帮助"):  # 说明有好几页时「关闭」到最后一页才亮
                self._help_step(items)
            else:
                self.tap(self._button(items, "关闭", BTN_CLOSE), "关闭")
        elif screen is Screen.UNLOCK:
            what = find(items, "现在可以选择") or find(items, "已解锁")
            logger.info("解锁：%s", what.text.strip() if what else "（没认出内容）")
            self.tap(self._button(items, "关闭", BTN_UNLOCK_CLOSE, UNLOCK_CLOSE_ROI), "关闭")
        elif screen is Screen.NEW_SONG:
            self.tap(TAP_NEW_SONG, "TAP TO NEXT")
        elif screen is Screen.HOME_TALK:
            self.tap(TAP_HOME_TALK, "关闭角色对话")
        elif screen in (Screen.RESULT,Screen.RESULT_REWARD, Screen.RESULT_EXP_NEXT):
            self.tap(self._button(items, "下一步", BTN_NEXT), "下一步")
        elif screen is Screen.RESULT_OTHER and (it := next_button(items)):
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
            if self.arena:
                return self._enter_arena(items)
            self.tap(self._button(items, "自由演出", BTN_FREE_LIVE), "自由演出")
        elif screen in ARENA_SCREENS:
            return self._arena_step(screen, items)
        elif screen in self._foreign_screens:
            # 挑战演出、激奏舞台和自由演出的页面互相走错了：退回演出首页再进。连续两次认出才退（页面淡入时可能认错）
            if self.prev_screen is not screen:
                return False
            self.tap(BTN_BAND_BACK, "返回")
        elif screen is Screen.DATE_CHANGE:
            logger.warning("游戏日期变更，回到标题画面重新登录")
            self.tap(self._button(items, "前往标题画面", BTN_TO_TITLE), "前往标题画面")
        elif screen is Screen.TITLE:
            self._on_title(items)
        elif screen is Screen.NOTIFY:
            self.tap(sdk_point(BTN_NOTIFY_CLOSE, sdk_scale(items, NOTIFY_LOGO_Y)), "关闭「开启消息通知」")
        elif screen is Screen.NOTIFY_PERMISSION:
            # 系统弹窗也按 dp 排版，位置随分辨率 / DPI 变，而且紧挨着上面的「允许」：只按认出的文字点
            if not (it := find(items, "不允许", exact=True)):
                return False
            self.tap(center(it), "不允许发送通知")
        elif screen is Screen.TITLE_MENU:
            self.tap(BTN_MENU_CLOSE, "关闭")
        elif screen is Screen.USER_CENTER:
            self._user_center_back(items)
        elif screen is Screen.LOGIN_HISTORY:
            # 账号退出了登录（不是切换账号时）：登录上次登录的账号；列表展开着时选第一行（最近登录的）
            if login_expanded(items):
                if not (rows := login_rows(items)):
                    return False
                self.tap(center(rows[0]), "选择最近登录的账号")
            else:
                logger.warning("账号未登录，登录上次登录的账号")
                self._login(items)
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
        elif screen is Screen.SKIPPABLE:
            logger.info("跳过演出：%s", " / ".join(it.text.strip() for it in items))
            self.tap(center(skip_button(items)), "跳过")
        elif screen is Screen.STORY_PLAYER:
            # 菜单展开要一点时间，点了没反应隔一会儿再点（展开到一半再点会收起来）
            now = time.monotonic()
            if now - self._story_menu_at < PLAYER_MENU_GAP_S:
                return False
            self._story_menu_at = now
            self.tap(BTN_PLAYER_MENU, "故事菜单")
        elif screen is Screen.STORY_MENU:
            self.tap(BTN_SKIP, "SKIP")
        elif screen is Screen.STORY_SKIP:
            logger.info("跳过故事")
            self.tap(self._button(items, "跳过", BTN_SKIP_CONFIRM, SKIP_CONFIRM_ROI), "跳过")
        elif screen is Screen.OK_POPUP:
            # 没见过的提示：记下文字，同一个弹窗只存一张截图，以后加进识别
            texts = " / ".join(it.text.strip() for it in items)
            if texts != self._ok_popup_seen:
                self._ok_popup_seen = texts
                logger.warning("没见过的弹窗，只有 OK，点掉：%s（截图 %s）", texts, self.save_debug("ok_popup"))
            self.tap(self._button(items, "OK", BTN_REWARD_OK, OK_POPUP_ROI), "OK")
        elif screen in (Screen.UNKNOWN, Screen.RESULT_OTHER):
            return self._recover(items)
        else:
            return False
        return True

    def _recover(self, items: list[OcrItem]) -> bool:
        """认不出的画面停住了：点一下试试能不能恢复（见 RECOVER_S），点了返回 True。"""
        if self._still is None or self.app_in_front is None or self._still_taps >= RECOVER_TAPS or loading(items):
            return False
        still = time.monotonic() - self._still[0]
        if still < RECOVER_S * (self._still_taps + 1):
            return False
        button = next((it for text in RECOVER_BUTTONS if (it := find(items, text, exact=True))), None)
        safe = not any(find(items, text) for text in RECOVER_BLOCK)
        if button is None and safe:
            button = find(items, "下一步", exact=True)
        if button is None and not safe:
            return False
        try:
            front = self.app_in_front()
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug("检查游戏是否在前台失败：%s", e)
            return False
        if not front:
            return False
        what = f"「{button.text.strip()}」" if button else "下方空白处"
        texts = " / ".join(it.text.strip() for it in items) or "（没有文字）"
        shot = ""
        if texts != self._recover_seen:  # 同一个画面只存一张截图
            self._recover_seen = texts
            shot = f"（截图 {self.save_debug('recover')}）"
        logger.warning("画面 %.0fs 没有变化，也认不出是什么页面，点%s试试能不能恢复：%s%s", still, what, texts, shot)
        self.tap(center(button) if button else TAP_RECOVER, what)
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

    def _display_id(self) -> str:
        """当前截图用的 MuMu display_id（只用于日志；测试里的假截图源没有它）。"""
        ipc = getattr(self.source, "ipc", None)
        return str(getattr(ipc, "display_id", "?"))

    def ensure_in_game(self, timeout_s: float = 180.0) -> Screen:
        """等到进入游戏（主界面或演出相关页面）：沿途点开标题画面、领登录奖励、关公告。"""
        deadline = time.monotonic() + timeout_s
        last_log = time.monotonic()
        while True:
            screen, items = self.look()
            if screen in IN_GAME_SCREENS:
                return screen
            if screen in RELOGIN_SCREENS:
                deadline = time.monotonic() + timeout_s
            if time.monotonic() > deadline:
                raise self._fail(f"{timeout_s:.0f}s 内未能进入游戏")
            acted = self._common_step(screen, items)
            now = time.monotonic()
            if not acted and now - last_log >= 10.0:
                # 诊断（#58）：卡在认不出的画面上时 look() 只在画面变化时记日志，日志里可能只剩
                # 一行「画面：未知」，看不出等了多久、display_id 是多少。这里定期补一条。
                last_log = now
                logger.debug(
                    "还在等进入游戏：画面 %s，display_id %s，距超时还有 %.0fs",
                    screen,
                    self._display_id(),
                    deadline - now,
                )
            self._sleep(self.settle_s if acted else 1.0)

    def relogin(self, timeout_s: float = 180.0) -> Screen:
        """停在服务器维护页时回到标题画面重新登录，等到进入游戏；还在维护时抛 :class:`ServerMaintenance`。"""
        screen, items = self.look(blocking_ok=True)
        if screen is Screen.MAINTENANCE:
            self.tap(self._button(items, "返回标题画面", BTN_MAINTENANCE_TO_TITLE, CLOSE_ROI), "返回标题画面")
            self._sleep(self.settle_s)
        return self.ensure_in_game(timeout_s)

    def restart_game(self, timeout_s: float = 180.0) -> bool:
        """重启游戏，等到重新进入游戏（从标题画面登录）；没法重启（没有 ``restart_app``）时返回 False。"""
        if self.restart_app is None:
            return False
        self.restart_app()
        self._sleep(3.0)  # 别把重启前的画面当成已经在游戏里
        self.ensure_in_game(timeout_s)
        return True

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
                # 游戏卡的时候过一阵才切到乐队确认页：等它切过去再看，免得再点一遍难度（点在乐队确认页上会打开综合能力详情）
                self._wait_leave(screen)
                acted = True
            else:
                acted = self._common_step(screen, items)
            self._sleep(self.settle_s if acted else 0.5)

    def _wait_leave(self, screen: Screen, timeout_s: float = 4.0) -> None:
        """点了按钮之后等画面离开 ``screen``（最多 ``timeout_s``）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._sleep(0.5)
            if self.look()[0] is not screen:
                return

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

    def choose_next_song(self, mode: str) -> None:
        """``random``：点「随机选曲」，等抽到的歌停下来（列表还在滚时去点确定，看到的还是原来那首）。
        选中的还是没解锁的歌（游戏卡、没点上）就再点，点 RANDOM_LOCKED_TRIES 次还是这样就算了（交给 ensure_band_confirm 报错）。"""
        if mode == "current":
            return
        if mode != "random":
            raise ValueError(f"未知的选曲模式：{mode}")
        for i in range(RANDOM_LOCKED_TRIES):
            pick = self.random_song()
            if not pick.locked or pick.only:
                return
            logger.info("随机选曲后选中的还是没解锁的歌（第 %d 次）", i + 1)

    def selected_locked(self) -> bool:
        """选中的歌还没解锁：在乐队确认页时不会（进得去的都解锁了），否则进入乐曲选择页看。"""
        if self.look()[0] is self.band_screen:
            return False
        return song_locked(self.ensure_song_select())

    def _lb_want(self) -> int | None:
        want = self.cfg.game.lb_cost
        if want and self._lb_empty_at is not None and time.monotonic() - self._lb_empty_at < LB_EMPTY_RETRY_S:
            return 0
        return want

    def _lb_auto(self) -> int:
        """``auto``：持有 LB 时每局消耗 :data:`LB_AUTO_COST` 个，没有时消耗 0，返回这一局的消耗。

        按乐队确认页顶栏的持有数判断，和现在的消耗设置对不上时才打开消耗设置弹窗，以弹窗上的持有数为准
        （顶栏的字很小，偶尔读错）。顶栏没读出来时：还没设过就打开弹窗；已经按 0 打了 ``LB_EMPTY_RETRY_S``
        （该恢复出一个了）也打开看一次。
        """
        screen, items = self.look()
        bar = lb_bar_held(items) if screen is self._lb_screen else None
        now = time.monotonic()
        if bar is not None:
            check = self._lb_cost != (LB_AUTO_COST if bar else 0)
        elif self._lb_cost == 0:
            check = self._lb_empty_at is None or now - self._lb_empty_at >= LB_EMPTY_RETRY_S
        else:
            check = self._lb_cost is None
        if check:
            before = self._lb_cost
            held = self.set_lb_cost(LB_AUTO_COST, zero_if_empty=True)
            if self._lb_cost == 0:
                self._lb_empty_at = now
            if self._lb_cost != before:
                if self._lb_cost:
                    logger.info("LB 持有 %s 个：每局消耗 %d 个", "?" if held is None else held, self._lb_cost)
                else:
                    logger.info("LB 用完了：消耗 0 接着打，恢复后再消耗")
        return self._lb_cost

    def _lb_auto_pick(self, lb_short: str) -> int:
        """``game.lb_auto``：按持有数自动选这一局的消耗（:func:`lb_auto_cost`），返回消耗。持有 0 个时 ``lb_short``
        为 ``stop`` 就抛 :class:`LbExhausted`，否则消耗 0。

        顶栏读到的持有数算出来的选择和现在的设置一样时不打开消耗设置弹窗；顶栏没读出来、读到 0（顶栏的字很小，
        用完前用弹窗核对）、还不知道最大能选几个、要用道具补充时打开弹窗，在弹窗上按持有数选。
        """
        screen, items = self.look()
        bar = lb_bar_held(items) if screen is self._lb_screen else None
        top = self._lb_top
        if (
            not bar
            or top is None
            or self._lb_cost is None
            or (self._can_refill() and bar < top)
            or lb_auto_cost(bar, top, self._lb_all_ok()) != (self._lb_cost, self._lb_all)
        ):
            before = (self._lb_cost, self._lb_all)
            held = self.set_lb_cost(None, refill=self._can_refill())
            if (self._lb_cost, self._lb_all) != before:
                if self._lb_all:
                    logger.info("LB 持有 %s 个：这局用「全部消耗」", "?" if held is None else held)
                elif self._lb_cost:
                    logger.info("LB 持有 %s 个：每局消耗 %d 个", "?" if held is None else held, self._lb_cost)
                elif lb_short != "stop":
                    logger.info("LB 用完了：消耗 0 接着打，恢复后再消耗")
        if self._lb_cost == 0 and lb_short == "stop":
            raise LbExhausted("LB 已用完")
        return self._lb_cost

    def _lb_all_ok(self) -> bool:
        """「全部消耗」今天还能用：本次运行没发现勾不上，也没记下今天的次数用完了。"""
        if self._lb_all_out:
            return False
        return self.store is None or self.store.lb_all_used(self.cfg.device.package) != game_day()

    def _lb_all_used(self) -> None:
        """记下今天的「全部消耗」次数用完了（之后不再尝试，到游戏日期变更为止）。"""
        self._lb_all_out = True
        if self.store is not None and self.store.lb_all_used(self.cfg.device.package) != game_day():
            self.store.set_lb_all_used(self.cfg.device.package, game_day())

    def _lb_all_back(self) -> None:
        """弹窗上写着「全部消耗」今日还剩次数：之前记下的今天用完了不算数（游戏改了换日时间、旧版本按别的时间记的，
        或者勾不上时猜错了），照样去勾。本次运行点了也勾不上过的不管，免得每局都去点。"""
        if self._lb_all_jam:
            return
        package = self.cfg.device.package
        stale = self.store is not None and self.store.lb_all_used(package) == game_day()
        if stale or self._lb_all_out:
            logger.info("弹窗上写着「全部消耗」今日还剩次数，之前记的今天已用完不算数")
            self._lb_all_out = False
            if stale:
                self.store.set_lb_all_used(package, None)

    def _lb_all_spent(self) -> None:
        """用了一次「全部消耗」：记下今天用了几次。每天能用几次看月卡（一般 1 次，最高档的 5 次），这里不判断用完没有，
        之后的弹窗上写着「今日次数已用完」、或者勾不上时才记下用完了。"""
        if self.store is None:
            logger.info("用了一次「全部消耗」")
            return
        n = self.store.add_lb_all_use(self.cfg.device.package, game_day())
        logger.info("用了一次「全部消耗」（今天第 %d 次）", n)

    def _lb_started(self) -> None:
        """演出开始了：勾了「全部消耗」的话记下用了一次，下一局重新核对消耗设置。"""
        if self._lb_all:
            self._lb_all = False
            self._lb_cost = None
            self._lb_all_spent()

    def skip_live(self, lb_short: str = "zero", timeout_s: float = 30.0) -> bool:
        """自由演出乐队确认页的「跳过」（``loop.use_skip``）跳过一次，回到乐队确认页，返回是否跳过了。

        还有跳过次数、这局会消耗 LB 时才跳过（消耗按 ``game.lb_cost`` / ``game.lb_auto`` 选，``lb_short`` 为 ``auto``
        时消耗 :data:`LB_AUTO_COST` 个；持有 0 个时点「关闭」不跳过，跳过也要用掉次数）。开了 ``game.lb_refill`` 时
        先在消耗设置里补充。弹出恢复LIVE BOOST 时点取消，不跳过。挑战演出的「跳过」不点。
        """
        if self.challenge:
            return False
        screen, items = self.look()
        left = skip_left(items) if screen is Screen.BAND_CONFIRM else None
        btn = skip_button(items, SKIP_BTN_ROI) if left else None
        if btn is None or (lb_bar_held(items) == 0 and not self._can_refill()):  # 顶栏读到持有 0 就不打开确认弹窗了
            return False
        if self.cfg.game.lb_auto and lb_short != "auto":
            want = None
        else:
            want = LB_AUTO_COST if lb_short == "auto" else self._lb_want()
            if not want:
                return False
        if self._can_refill():
            self._refill_if_short(want if want is not None else self._lb_top or LB_MAX_COST)
        self.tap(center(btn), "跳过")
        tapped_at = time.monotonic()
        deadline = tapped_at + timeout_s
        pick = _LbPick()
        chosen: tuple[int, bool] | None = None
        confirmed_at = closed_at = None
        tapped: dict[Screen, float] = {}
        while time.monotonic() < deadline:
            self._sleep(0.5)
            screen, items = self.look()
            t = time.monotonic()
            if screen is Screen.BAND_CONFIRM:
                if closed_at is not None:
                    return False
                if confirmed_at is not None:
                    if t - confirmed_at < 3:
                        continue  # 弹窗正在关闭
                    self._skipped(chosen, left)
                    return True
                if t - tapped_at > 3:  # 点击没生效
                    self.tap(self._button_at(skip_button(items, SKIP_BTN_ROI), BTN_SKIP_LIVE), "跳过")
                    tapped_at = t
            elif screen is Screen.SKIP_CONFIRM:
                if t - (confirmed_at or closed_at or -math.inf) < 3:
                    continue  # 弹窗正在关闭
                held = lb_held(items)
                if held == 0 or (want is None and held is None and pick.taps >= 3):
                    logger.info("LB 用完了，不跳过")
                    self.tap(self._button(items, "关闭", BTN_SKIP_CLOSE, BOTTOM_LEFT_ROI), "关闭")
                    closed_at = t
                    continue
                if not self._lb_find_top(items, want, pick):
                    continue
                picked = self._lb_pick_step(items, want, held, SKIP_ALL_CHECK, pick)
                if picked is None:
                    continue
                chosen = picked
                logger.debug("跳过：消耗 %d%s（持有 %s）", picked[0], "（全部消耗）" if picked[1] else "", held)
                self.tap(self._button_at(skip_button(items, SKIP_BTN_ROI), BTN_SKIP_LIVE_CONFIRM), "确认跳过")
                confirmed_at = t
            elif screen is Screen.SKIP_RESULT:
                if t - tapped.get(screen, -math.inf) < 2:
                    continue
                it = next_button(items) or find(items, "OK", NEXT_ROI, exact=True)
                self.tap(self._button_at(it, BTN_SKIP_RESULT_OK), it.text if it else "OK")
                tapped[screen] = t
            elif screen is Screen.LB_RECOVER:
                self._cancel_lb_recover(items)
                closed_at = t
            elif screen is Screen.UNKNOWN:
                continue  # 弹窗淡入淡出、结果页的动画
            elif not self._common_step(screen, items):  # 跳过后的升级等弹窗
                raise self._fail("跳过时出现意外画面")
        raise self._fail("跳过没有完成")

    def _button_at(self, it: OcrItem | None, default: tuple[int, int]) -> tuple[int, int]:
        return center(it) if it is not None else default

    def _skipped(self, chosen: tuple[int, bool] | None, left: int) -> None:
        cost, use_all = chosen or (None, False)
        logger.info(
            "跳过了一次（消耗 %s，还剩 %d 次）", "全部" if use_all else "?" if cost is None else f"{cost} 个", left - 1
        )
        self._lb_cost = None  # 跳过时选的消耗可能改了游戏里的设置，下一局演奏前重新核对
        if use_all:
            self._lb_all_spent()

    def _prepare_lb(self, lb_short: str) -> tuple[int | None, bool]:
        """点 LIVE START 前按配置设好 LB 消耗（说明见 :meth:`start_live`），返回 (这局要消耗的个数, 是否按持有数自动选)。"""
        if lb_short not in ("zero", "stop", "auto"):
            raise ValueError(f"未知的 LB 不足处理方式：{lb_short}")
        auto_pick = self.cfg.game.lb_auto and lb_short != "auto"
        if self.challenge:
            self._check_cp()
            want = None
        elif lb_short == "auto":
            want = self._lb_auto()
        elif auto_pick:
            want = self._lb_auto_pick(lb_short)
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
        return want, auto_pick

    def _on_lb_recover(
        self, items: list[OcrItem], want: int | None, auto_pick: bool, lb_short: str, recovers: int
    ) -> int:
        """点 LIVE START 后弹出了恢复LIVE BOOST（LB 用完时要求先恢复）：取消，能用道具补充就补，否则本局改为消耗 0
        （一段时间后或玩家升级后再按配置设置；``lb_short`` 为 ``stop`` 时抛 :class:`LbExhausted`）。
        回到设 LB 的页面后返回弹出的次数（``recovers`` + 1），由调用方再点 LIVE START。"""
        if self._lb_cost == 0:
            raise self._fail("LB 消耗为 0 仍弹出恢复 LIVE BOOST")
        self._cancel_lb_recover(items)
        self._wait_for(self._lb_screen)
        recovers += 1
        refilled = want and self._can_refill() and self.set_lb_cost(None if auto_pick else want, refill=True)
        if not refilled or recovers > 1:
            if lb_short == "stop":
                raise LbExhausted("LB 已用完")
            self._lb_empty_at = time.monotonic()
            self.set_lb_cost(0)
        return recovers

    def start_live(self, lb_short: str = "zero") -> None:
        """（按配置设好 LB 消耗后）点击 LIVE START，处理演出前选项设置弹窗后立即返回（弹窗关闭后才开始加载）。

        开了 ``game.lb_refill`` 时先看持有数，少于每局消耗就用道具补充（:meth:`_refill`）。
        LB 不足时游戏照样开始演出，只消耗持有的全部（持有 0 就不消耗），有时则弹出恢复 LIVE BOOST：点取消
        （能用道具补充的话再从消耗设置进去补）。``lb_short`` 为 ``zero`` 时本局改为消耗 0；为 ``stop`` 时
        点 LIVE START 前先核对持有数，用完了（或弹出了恢复窗口）就留在乐队确认页并抛出 :class:`LbExhausted`；
        为 ``auto`` 时不看 ``game.lb_cost``，每局前按持有数选消耗（:meth:`_lb_auto`），弹出恢复窗口同 ``zero``。
        开了 ``game.lb_auto`` 时（``auto`` 除外）每局前按持有数自动选消耗（:meth:`_lb_auto_pick`）。
        """
        want, auto_pick = self._prepare_lb(lb_short)
        self.tap(BTN_LIVE_START, "LIVE START")
        started = time.monotonic()
        retapped = False
        unknown_since = None
        recovers = 0  # 弹出过几次恢复LIVE BOOST
        while time.monotonic() - started < 10:
            self._sleep(0.3)
            screen, items = self.look()
            if screen is Screen.LIVE_OPTIONS:
                self._check_speed(items)
                self.tap(self._button(items, "演出", BTN_OPTIONS_START), "演出")
                self._lb_started()
                return
            if screen is Screen.LB_RECOVER and not self.challenge:
                recovers = self._on_lb_recover(items, want, auto_pick, lb_short, recovers)
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
                    self._lb_started()
                    return
            else:
                raise self._fail("点击 LIVE START 后出现意外画面")
        raise self._fail("点击 LIVE START 后没有进入演出")

    def _in_play(self, frame) -> bool:
        return self._pause_template is not None and pause_visible(frame, self._pause_template)

    def _wait_play(self, timeout_s: float = 2.5, polls: int = 60, interval: float = 0.03) -> bool:
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
            self._sleep(interval)
        return False

    def _wait_intro(self, items: list[OcrItem]) -> None:
        """要重试时看不到暂停按钮：等开场过去、演奏画面出来，最多 INTRO_WAIT_S。
        还在加载时（items 是刚才看到的画面）每等完一次再看一眼，还在加载、或者刚加载完（接着是开场）就再等一次。"""
        logger.info("还在开场（看不到暂停按钮），等演奏画面出来再重试")
        was_loading = loading(items)
        for _ in range(LOADING_WAIT_ROUNDS):
            if self._wait_play(INTRO_WAIT_S, round(INTRO_WAIT_S / INTRO_POLL_S), INTRO_POLL_S):
                return
            now_loading = loading(self.look()[1])
            if not (now_loading or was_loading):
                return
            if now_loading:
                logger.info("还在加载（网络慢时进歌要很久），接着等演奏画面")
            was_loading = now_loading

    def retry_live(self, timeout_s: float = 10.0) -> None:
        """演奏中暂停 → 重试 → 确认重试，这首歌从头开始（同步失败、演奏明显对不上时，不用干等歌曲放完）；
        点完立即返回，首音符同步要尽早开始看画面。

        先确认还在演奏画面（右上角的暂停按钮）或暂停菜单上（演奏中被暂停了），都不在就什么都不点。
        还在开场（认不出的画面）时最多等 INTRO_WAIT_S 让演奏画面出来，还在加载时等得更久（见 _wait_intro）。
        重试没生效时点「继续」接着放完这首歌（「终止」拿不到演出奖励），抛出 NavigationError。
        """
        screen, items = self.look()
        if screen is not Screen.PAUSE:
            if screen is Screen.UNKNOWN and not self._in_play(self._frame):
                self._wait_intro(items)
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

    def set_lb_cost(
        self, cost: int | None, timeout_s: float = 15.0, refill: bool = False, zero_if_empty: bool = False
    ) -> int | None:
        """乐队确认页 → 消耗LB → 选中 ``cost``（None 为按持有数自动选，见 :func:`lb_auto_cost`）、按像素核对 → OK，
        回到乐队确认页（游戏会记住设置）。返回弹窗上读到的 LB 持有数。``refill`` 时持有数少于 ``cost``（自动选时为
        最大能选的消耗）就点弹窗里的「恢复」用道具补充一次（:meth:`_refill`），否则绝不点「恢复」。
        ``zero_if_empty`` 时弹窗上读到持有 0 就改选 0。"""
        self.tap(BTN_LB_COST, "消耗LB")
        opened = time.monotonic()
        deadline = opened + timeout_s
        pick = _LbPick()
        chosen = (cost, False)
        ok_at = recover_at = None
        refilled = False
        held = None
        while time.monotonic() < deadline:
            self._sleep(0.5)
            screen, items = self.look()
            if screen is self._lb_screen:
                if ok_at is not None:
                    self._lb_cost, self._lb_all = chosen
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
                held = lb_held(items)
                want = 0 if zero_if_empty and held == 0 else cost
                if not self._lb_find_top(items, want, pick):
                    continue
                need = self._lb_top if want is None else want
                if refill and recover_at is None and held is not None and need and held < need and self._can_refill():
                    logger.info("LB 持有 %d 个，少于每局消耗 %d 个，用道具补充", held, need)
                    self.tap(self._button(items, "恢复", BTN_LB_RECOVER, LB_RECOVER_ROI), "恢复")
                    recover_at = time.monotonic()
                    continue
                picked = self._lb_pick_step(items, want, held, LB_ALL_CHECK, pick)
                if picked is None:
                    continue
                chosen = picked
                logger.debug(
                    "LB 消耗设置为 %d%s（持有 %s）", picked[0], "（全部消耗）" if picked[1] else "", "?" if held is None else held
                )
                self.tap(self._button(items, "OK", BTN_LB_OK), "OK")
                ok_at = time.monotonic()
            elif screen is Screen.LB_RECOVER and recover_at is not None and not refilled:
                self._refill(items, held, need - held)
                refilled = True
                deadline = time.monotonic() + timeout_s
            elif screen is not Screen.UNKNOWN:  # UNKNOWN：弹窗淡入淡出
                raise self._fail("设置 LB 消耗时出现意外画面")
        raise self._fail("未能设置 LB 消耗")

    def _lb_find_top(self, items: list[OcrItem], want: int | None, pick: _LbPick) -> bool:
        """消耗设置 / 确认跳过弹窗：自动选、或者要选的超过 :data:`LB_BASE_COST` 时先看最大能选几个（列表只放得下 4 行，
        没看到 :data:`LB_MAX_COST` 就往下拖一次，露出上面更大的），记在 ``_lb_top``。做了操作（或没读出来）返回 False。"""
        if self._lb_top is not None or (want is not None and want <= LB_BASE_COST):
            return True
        rows = lb_rows(items)
        if rows is None:
            self._lb_pick_retry(pick, "没读到 LB 消耗的选项")
            return False
        if max(rows.y) < LB_MAX_COST and not pick.dragged:
            self._drag(*LB_LIST_DRAG)
            pick.dragged = True
            return False
        self._lb_top = max(rows.y)
        logger.info("LB 消耗最大能选 %d 个", self._lb_top)
        return True

    def _lb_pick_step(
        self, items: list[OcrItem], want: int | None, held: int | None, check: tuple[int, int], pick: _LbPick
    ) -> tuple[int, bool] | None:
        """消耗设置 / 确认跳过弹窗（「全部消耗」复选框在 ``check``）：选中 ``want``（None 为按持有数 ``held`` 自动选）、
        勾上或取消「全部消耗」。还要操作时做一步、返回 None，都选好了返回 (消耗, 是否全部消耗)。"""
        if lb_all_used(items):
            self._lb_all_used()
        elif lb_all_left(items):
            self._lb_all_back()
        top = self._lb_top
        if want is None:
            if held is None:
                self._lb_pick_retry(pick, "没读到 LB 持有数")
                return None
            cost, use_all = lb_auto_cost(held, top, self._lb_all_ok())
        else:
            cost, use_all = want, False
            if top is not None and want > top:
                if not self._lb_capped:
                    logger.warning("LB 消耗最大只能选 %d 个（设置的是 %d 个），按 %d 个消耗", top, want, top)
                    self._lb_capped = True
                cost = top
        if self._lit(check):
            if use_all:
                return cost, True
            if pick.all_taps >= 3:
                raise self._fail("取消不了「全部消耗」")
            self.tap(check, "取消全部消耗")
            pick.all_taps += 1
            return None
        rows = lb_rows(items)
        selected = None if rows is None else self._lb_selected(rows)
        if selected != cost:
            if pick.taps >= 6:
                if cost > LB_BASE_COST and want is None:
                    # 选不中：多半是看到的更大的消耗还没解锁，按小一个的选
                    logger.warning("LB 消耗选不中 %d（当前 %s），最大按 %d 个算", cost, selected, cost - 1)
                    self._lb_top = cost - 1
                    pick.taps = 0
                    return None
                raise self._fail(f"LB 消耗选不中 {cost}（当前 {selected}）")
            pick.taps += 1
            if rows is None:
                return None  # 没读到选项，下一帧再看
            if cost in rows.y:
                self.tap((rows.x, rows.y[cost]), f"消耗 {cost}")
            elif cost > max(rows.y):
                self._drag(*LB_LIST_DRAG)  # 往下拖，露出上面更大的
            else:
                self._drag(LB_LIST_DRAG[1], LB_LIST_DRAG[0])
            return None
        if use_all:
            if pick.all_taps >= 2:
                self._lb_all_jam = True
                if lb_all_left(items):
                    # 写着还剩次数却勾不上：不是次数用完了，不记下来，这次运行不再试
                    logger.warning("「全部消耗」勾不上（弹窗上写着今日还剩次数），这局消耗 %d 个", cost)
                    self._lb_all_out = True
                elif self.store is not None and self.store.lb_all_uses(self.cfg.device.package, game_day()):
                    # 今天用过了：多半是次数用完了（没认出「今日次数已用完」），记下来，今天不再试
                    logger.info("「全部消耗」勾不上（今天的次数用完了？），这局消耗 %d 个", cost)
                    self._lb_all_used()
                else:
                    logger.info("「全部消耗」勾不上（今天的次数在别处用完了？），这局消耗 %d 个", cost)
                    self._lb_all_out = True
                return cost, False
            self.tap(check, "全部消耗")
            pick.all_taps += 1
            return None
        return cost, False

    def _lb_pick_retry(self, pick: _LbPick, why: str) -> None:
        pick.taps += 1
        if pick.taps > 6:
            raise self._fail(why)

    def _can_refill(self) -> bool:
        return bool(self.cfg.game.lb_refill and self.cfg.game.lb_spends) and not self._refill_out

    def _refill_if_short(self, want: int) -> int | None:
        """乐队确认页顶栏的 LB 持有数少于 ``want``（或没读到）时打开消耗设置弹窗核对，确实不够就用道具补充
        （``game.lb_auto`` 时顺便按持有数选好消耗）。返回持有数（读不到为 None）。"""
        screen, items = self.look()
        held = lb_bar_held(items) if screen is self._lb_screen else None
        if held is not None and held >= want:
            return held
        return self.set_lb_cost(None if self.cfg.game.lb_auto else want, refill=True)

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
        if screen is not self._lb_screen:
            return False
        held = lb_bar_held(items)
        logger.debug("顶栏 LB 持有数：%s", held)
        return held == 0

    def lb_status(self, check: bool = False) -> tuple[int | None, int | None]:
        """乐队确认页右上角的 LB 持有数和下一个恢复的倒计时（秒），读不到的项为 None。
        ``check`` 时再打开消耗设置弹窗核对持有数（顶栏的字很小，偶尔读错；顺便按配置设好消耗），
        顶栏漏读持有数时也这样读。"""
        screen, items = self.look()
        if screen is not self._lb_screen:
            raise self._fail(f"不在{self._lb_screen}页，无法读取 LB")
        held, left = lb_bar_held(items), lb_bar_timer(items)
        if check or held is None:
            if self.cfg.game.lb_auto:
                held = self.set_lb_cost(None)
            elif want := self._lb_want():
                held = self.set_lb_cost(want)
        logger.debug("LB 持有数：%s，恢复倒计时：%s", held, left)
        return held, left

    def _lb_selected(self, rows: LbRows) -> int | None:
        """看得到的几行里选中的消耗（选中的那行不在画面上、或者读不准时为 None）。"""
        lit = [c for c, y in rows.y.items() if self._lit((rows.x, y))]
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
        """结算页 → 下一步 ×2（活动期间更多）→ 再次演出，停在乐曲选择页（同一首歌；激奏舞台回到激奏舞台首页）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            screen, items = self.look()
            if screen in SONG_SCREENS or screen is self.band_screen:
                return
            if screen in RELOGIN_SCREENS:
                deadline = time.monotonic() + timeout_s
            if self._common_step(screen, items):
                self._sleep(self.settle_s)
            else:
                self._sleep(0.5)
        raise self._fail("未能离开结算页")
