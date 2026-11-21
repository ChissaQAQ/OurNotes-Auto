"""看广告领每日奖励：观看 CM 招募（每天 3 次）、看广告恢复 LIVE BOOST（每天 2 次，每次 10 点）、
录音室练习的 SKIP（每天 1 次，立刻拿 360 分钟的练习奖励）。坐标均为 1280x720 设计尺寸。

- 要游戏里放得出广告（Google Play 版；网络、模拟器得让 Google 肯给广告）。
- 没要到广告：游戏点了「观看」才去要广告，Google 这次不给（AdMob 回「没有可填充的广告」，实测时有时无）就在约 1 秒后弹出
  「广告获取失败。请稍后再试。」（标题「错误」，只有 OK），次数不会用掉。这不是程序能避免的，只能过一阵再要：
  - 认出这个弹窗就点掉 OK、关掉还开着的弹窗，这一项先放下（:class:`AdUnavailable`），别的项目照常做。
    点了之后 AD_START_S 一直没放出来的也一样算没要到。
  - 别的项目做完后隔一阵把没要到的再做一遍（AD_RETRY_WAITS_S）。还是没要到的记为出错，并记进存档
    （``state.json`` 的 ``ads_pending``，只在当天有效）：之后的挂机隔一阵再试（AD_LATER_GAPS_S，见 :meth:`AdsMixin.retry_ads`）。
- 游戏里开通了跳过广告的权益（通行证等，#146）：点了「观看」不放广告、奖励直接到手，前台一直是游戏。等广告出现的同时
  也看画面：连续几帧都是奖励到手的画面（``lb_ad_got`` 等）就当成广告已经关掉，照常往下走，不干等 AD_START_S 再报错。
  只有恢复 LB 的在玩家的日志里见过，另外两项是按「广告关掉之后本来就等的画面」推的。
- 广告是另一个界面（AdMob 的 AdActivity），画面认不出，只看两样：经 adb 读到的获得焦点的窗口，和画面顶部提示条的文字
  （文字随模拟器的系统语言，只实测过简体中文）。
  - 提示条：「N 秒后可得奖励」（连播时前面还有「第 2 个广告，共 3 个 ·」）→「奖励已发放」；连播时倒计时走完可能只剩一个 ✕。
  - 有的广告没有提示条，只在最上面有一条黄色的进度条（连播时几个广告共用一条），放完进度条就没了，过一会儿右上角才出现 ✕。
    实测进度条没了按返回键就关得掉、奖励也到手。两样都没有的广告放得够久（AD_BLIND_S）之后隔一阵按一次返回键试试。
  - 广告里的东西一律不点（点了就是点击广告）：奖励到手后按返回键关掉。
  - 广告会自己弹出商店页（前台变成别的应用），开着时广告不往下走：按返回键关掉。
  - 奖励没到手时按返回键会问「关闭广告？您将会失去奖励」：点「继续」接着放（这是广告 SDK 自己的确认框，不是广告内容）。
  - 一直不结束（AD_TIMEOUT_S）时放弃这次的奖励：按返回键、点「关闭」，还回不到游戏就重启游戏。
    广告留在画面上的话，后面的导航会把它当成认不出的画面去点。
- 次数按游戏里写的「还剩N次」：写着 0 次、或者按钮是灰的（今天的次数用完了，文字照旧）就不点；每项最多做 AD_JOBS_MAX 次。
- 观看 CM 招募：招募页左侧选中「观看CM招募」，只点写着「观看广告免费」的按钮；「确认招募」上写着「观看广告」「不会消耗星钻」
  才点 OK。广告关掉后和免费招募一样：结算存一张截图（data/recruit/）、只点「招募TOP」，前后核对星钻数没变。
- 恢复 LIVE BOOST：主界面 LB 旁的「+」→ 恢复LIVE BOOST 的「观看广告」分页，写着「确定要观看广告并恢复…」才点「观看」，
  「已恢复LIVE BOOST。」点 OK；「道具」「星钻」分页不碰。恢复的 LB 可以超过上限。
- 录音室练习的 SKIP：录音室练习是一张比画面宽的地图，每个乐队一个标志（艺术字，读不出来），下面的面板写着练习了多久。
  点标志进这个乐队的详情页：左上角的标志按模板认是哪个乐队，右上角是练习Lv，下面「SKIP」「领取奖励」。
  - 广告次数各乐队共用：SKIP 上面的角标「广告剩余N次」没有了就是用完了。
  - 用在哪个乐队：指定的乐队，或者练习Lv 最高的（把地图拖到最左、最右，每个乐队的详情页都看一遍；一样高时取先看到的）。
  - SKIP 弹窗（「获得相当于360分钟的练习奖励」）有「关闭」「道具」「广告」三个按钮：只点写着「观看1次广告」、
    角标还有次数、亮着的「广告」，「道具」（用 SKIP 券）不碰。广告关掉后「录音室练习奖励」点 OK。
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..logfile import log_image
from ..player.guard import load_template
from ..records import CHORE_KINDS, game_day
from ..result_reader import OcrItem
from ..runner import NavigationError
from .daily import (
    BTN_BACK,
    RECRUIT_DIALOG_BAD_FRAMES,
    RECRUIT_DIALOG_FRAMES,
    RECRUIT_SHOT_CM,
    _gems_text,
    cm_recruit_cancel,
    cm_recruit_dialog,
    cm_recruit_ok,
    page_title,
    practice_level_up,
    recruit_gift_ok,
    recruit_top,
    reward_ok,
    studio_skip_close,
    studio_skip_dialog,
)
from .jacket import crop_jacket
from .screens import ARENA_BANDS, LB_TABS_ROI, OK_POPUP_ROI, Rect, Screen, center, classify, find, in_roi, skip_button

logger = logging.getLogger(__name__)

# 项目 → 名称，按这个顺序做
AD_JOBS = {"recruit": "观看CM招募", "lb": "看广告恢复LB", "studio": "录音室练习SKIP"}
AD_JOBS_MAX = {"recruit": CHORE_KINDS["ad_recruit"][1], "lb": CHORE_KINDS["ad_lb"][1]}  # 每天的次数（3、2）；读不到「还剩N次」时最多做这么多次（录音室练习的 SKIP 每天 1 次）

# ---- 广告播放画面
AD_POLL_S = 2.0
AD_START_S = 25.0  # 点了「观看」这么久广告还没出现，就算没放出来
# 游戏里开通了跳过广告的权益（通行证等）时，点了「观看」不放广告、奖励直接到手，前台一直是游戏（#146）：
# 连续这么多帧都是奖励到手的画面（各项目的 xxx_got）、每帧截完前台都还是游戏，才算奖励到手了
AD_SKIPPED_FRAMES = 2
# 游戏的提示「广告获取失败。请稍后再试。」（标题「错误」，只有 OK）：这次没要到广告，点掉 OK 后原来的弹窗还在
AD_FAILED_TEXT = "广告获取失败"
BTN_AD_FAILED_OK = (640, 570)
AD_RETRY_WAITS_S = (60.0, 120.0)  # 有项目没要到广告时，别的项目做完后隔这么久把它们再做一遍（一个数一遍）
# 还是没要到的留给挂机：第 n 次再试之前隔这么久（再往后都按最后一个）；只在当天（游戏日）有效
AD_LATER_GAPS_S = (1800.0, 3600.0, 7200.0, 14400.0)
AD_TIMEOUT_S = 240.0  # 广告出现后这么久还没关掉就放弃（连播 3 个约 1 分钟）
AD_BAR_ROI: Rect = (0, 0, 1280, 70)  # 顶部的提示条（文字在 y≈34，✕ 在左上角或右上角）
_AD_COUNTDOWN = re.compile(r"(\d+)秒后可得奖励")
AD_GRANTED = ("奖励已发放", "已发放奖励")
# 没有提示条的广告最上面的黄色进度条：约 6px 高，从左边开始，颜色（BGR）很均匀
AD_PROGRESS_BGR = (70, 206, 247)
AD_PROGRESS_TOL = 35
AD_PROGRESS_ROI: Rect = (4, 1, 60, 4)
AD_GONE_FRAMES = 2  # 见过倒计时 / 进度条之后连续这么多帧都没有了（也没有「奖励已发放」），就按返回键试试
AD_BLIND_S = 90.0  # 广告放了这么久之后，只要没有倒计时就隔一阵按一次返回键试试（连播 3 个约 1 分钟）
AD_BLIND_GAP_S = 20.0
AD_RETRY_GAP_S = 10.0  # 按了返回键被问「关闭广告？」、点了「继续」之后，隔这么久再试
AD_BACK_GAP_S = 3.0  # 两次返回键之间至少隔这么久（等界面切换完）
# 「关闭广告？/ 您将会失去奖励」：左「关闭」右「继续」
AD_CLOSE_ROI: Rect = (380, 260, 520, 230)
AD_LEAVE_TRIES = 5
# 广告 SDK 的界面（com.google.android.gms.ads.AdActivity 等）：和游戏同一个包名，但不是游戏自己的界面
_AD_WINDOW = re.compile(r"\.ads?\.|AdActivity|adview|adunit", re.I)

# ---- 恢复 LIVE BOOST 的「观看广告」分页
BTN_HOME_LB_ADD = (517, 44)  # 主界面顶栏 LB 数（「10/10」）右边的「+」
HOME_LB_ROI: Rect = (380, 20, 120, 50)
_HOME_LB = re.compile(r"(\d+)/\d+")
BTN_LB_AD_TAB = (133, 290)
LB_AD_LEFT_ROI: Rect = (60, 240, 170, 45)  # 分页上面的角标「还剩N次」
LB_AD_TEXT = "观看广告并恢复"  # 「确定要观看广告并恢复10点LIVE BOOST吗?」
LB_AD_WATCH_ROI: Rect = (680, 630, 200, 70)  # 右下「观看」（「道具」页这里是 OK）
BTN_LB_AD_CANCEL = (497, 662)
LB_AD_CANCEL_ROI: Rect = (400, 630, 200, 70)
# 按钮底色亮着约 180，次数用完时是灰的（约 90），在文字左边取样
AD_BUTTON_LIT_DX = -75
LB_OPEN_S = 10.0
LB_RETAP_S = 4.0
LB_DONE_S = 30.0  # 广告关掉后这么久没看到「已恢复LIVE BOOST。」就报错
LB_BACK_S = 8.0  # 广告关掉后回到了恢复LIVE BOOST、过了这么久还没有「已恢复」：奖励没到手

# ---- 招募页的「观看CM招募」
# 左侧的招募横幅列表：选中的横幅放大、滚到中间。CM 招募横幅上的艺术字只读得出「观看」「招募」（比说明文字大）
RECRUIT_LIST_ROI: Rect = (0, 80, 300, 640)
CM_BANNER_MIN_H = 35
CM_BANNER_DX = 45  # 「观看」在横幅左半边，往右这么多是横幅中间
CM_LIST_DRAG = ((150, 540), (150, 300))  # 列表里没看到时往上拖着找
CM_FIND_TRIES = 5
# 右下角的按钮：「招募1次」「观看广告免费」，下面「还剩N次」
AD_RECRUIT_ROI: Rect = (1040, 530, 240, 115)
AD_RECRUIT_TEXT = "观看广告免费"
AD_RECRUIT_TAP_DY = -15  # 点按钮中间（文字在按钮下半边），也在这一行取样看按钮亮不亮
CM_STAY_S = 20.0  # 广告关掉后这么久一直停在招募页（没有演出、结算）：没有招募
_LEFT = re.compile(r"还剩(\d+)次")

# ---- 录音室练习的 SKIP
STUDIO_BANDS = ARENA_BANDS
STUDIO_HIGHEST = "highest"  # 用在练习Lv 最高的乐队上
STUDIO_BAND_MODES = (STUDIO_HIGHEST, *STUDIO_BANDS)
STUDIO_PAGE = "录音室练习"
# 地图上每个乐队标志下面的面板：练习了多久（时:分:秒）。标志在它上面，点标志进详情页
STUDIO_MAP_ROI: Rect = (0, 90, 1280, 500)
_STUDIO_TIMER = re.compile(r"\d{1,2}:\d{2}:\d{2}")
STUDIO_LOGO_OFFSET = (10, -70)
STUDIO_TAP_X = (60, 1220)  # 贴着画面两边的标志只露出一半，不点（地图拖到另一头时看得全）
BTN_STUDIO_CLAIM_ALL = (1124, 658)  # 地图右下角的「一键领取」（详情页没有）
STUDIO_CLAIM_ALL_ROI: Rect = (980, 620, 280, 80)
# 地图比画面宽（左右能拖约 720px）：按住往一边拖，拖到乐队标志的位置不再变（到头了）为止，最多 STUDIO_DRAGS 次
STUDIO_MAP_SIDES = (((300, 520), (900, 520)), ((900, 520), (300, 520)))
STUDIO_DRAGS = 5
STUDIO_SAME_PX = 12  # 两帧里同一个标志的位置差在这以内算没动（文字框有几个像素的抖动）
# 详情页
STUDIO_LV_LABEL_ROI: Rect = (800, 5, 130, 50)  # 「练习Lv」
STUDIO_LV_ROI: Rect = (930, 8, 126, 38)  # 练习Lv 的数字（右对齐）；到上限时前面有一把锁
STUDIO_LV_GAP = 10  # 锁和数字之间空着约 15px，数字之间只有 3～4px
STUDIO_LV_WHITE = 190
STUDIO_BAND_ROI: Rect = (656, 2, 164, 90)  # 左上角的乐队标志（模板 studio_band_<乐队>.png 在里面找）
STUDIO_BAND_MIN = 0.7  # 实测同一个乐队 1.00，别的乐队 0.42 以下
BTN_STUDIO_SKIP = (819, 660)
STUDIO_SKIP_ROI: Rect = (700, 630, 240, 60)
STUDIO_BADGE_ROI: Rect = (700, 600, 240, 45)  # SKIP 上面的角标「广告剩余N次」（次数用完时没有）
_STUDIO_LEFT = re.compile(r"广告剩余(\d+)次")
# SKIP 弹窗右边的「广告」按钮：角标「广告剩余N次」、「广告」、「观看1次广告」
STUDIO_AD_ROI: Rect = (800, 525, 260, 115)
STUDIO_AD_TEXT = "观看1次广告"
BTN_STUDIO_SKIP_CLOSE = (355, 588)
STUDIO_OPEN_S = 10.0  # 点了标志 / SKIP / 返回之后等画面出来最多多久
STUDIO_RETAP_S = 4.0
STUDIO_DONE_S = 30.0  # 广告关掉后这么久没看到「录音室练习奖励」就报错
STUDIO_BACK_S = 8.0  # 广告关掉后回到了 SKIP 弹窗 / 详情页、过了这么久还没有奖励：奖励没到手


class AdUnavailable(NavigationError):
    """这次没要到广告（游戏说「广告获取失败」，或者点了之后一直没放出来）：次数没用掉，过一阵可以再试。"""


@dataclass
class _AdWatch:
    """正在放的一次广告。"""

    game: str  # 看之前的游戏界面
    since: float  # 广告出现的时刻
    counted: bool = False  # 见过倒计时或进度条
    gone: int = 0  # 见过之后，连续几帧倒计时、进度条、「奖励已发放」都没有
    back_at: float = -math.inf  # 上次按返回键的时刻
    resumed_at: float = -math.inf  # 上次被问「关闭广告？」（奖励还没到手）的时刻
    bar: str | None = None  # 上一帧顶部提示条的文字（变了才记日志）
    back_shot: bool = False  # 调试日志开着时：刚按了返回键，下一帧存一张截图


def ad_countdown(items: list[OcrItem]) -> int | None:
    """广告顶部提示条上「N 秒后可得奖励」的 N。"""
    for it in items:
        if in_roi(it, AD_BAR_ROI) and (m := _AD_COUNTDOWN.search(re.sub(r"\s+", "", it.text))):
            return int(m.group(1))
    return None


def ad_progress(frame) -> bool:
    """广告画面最上面有黄色的进度条（没有文字提示条的广告，放完就没了）。"""
    if not isinstance(frame, np.ndarray):
        return False
    h, w = frame.shape[:2]
    x, y, rw, rh = AD_PROGRESS_ROI
    top, left = round(y * h / 720), round(x * w / 1280)
    strip = frame[top : top + max(1, round(rh * h / 720)), left : left + max(1, round(rw * w / 1280))]
    close = np.abs(strip.reshape(-1, 3).astype(int) - AD_PROGRESS_BGR).max(axis=1) <= AD_PROGRESS_TOL
    return bool(close.mean() >= 0.9)


def ad_granted(items: list[OcrItem]) -> bool:
    """广告顶部提示条写着「奖励已发放」。"""
    return any(find(items, text, AD_BAR_ROI) for text in AD_GRANTED)


def ad_closing(items: list[OcrItem]) -> tuple[OcrItem | None, OcrItem | None] | None:
    """奖励没到手时按返回键弹出的「关闭广告？」上的（关闭, 继续），不是这个确认框时为 None。"""
    if find(items, "关闭广告", AD_CLOSE_ROI) is None:
        return None
    return find(items, "关闭", AD_CLOSE_ROI, exact=True), find(items, "继续", AD_CLOSE_ROI, exact=True)


def ads_left(items: list[OcrItem], roi: Rect) -> int | None:
    """``roi`` 里「还剩N次」的 N。"""
    for it in items:
        if in_roi(it, roi) and (m := _LEFT.search(re.sub(r"\s+", "", it.text))):
            return int(m.group(1))
    return None


def home_lb(items: list[OcrItem]) -> int | None:
    """主界面顶栏的 LB 持有数。"""
    for it in items:
        if in_roi(it, HOME_LB_ROI) and (m := _HOME_LB.fullmatch(re.sub(r"\s+", "", it.text))):
            return int(m.group(1))
    return None


def lb_ad_watch(items: list[OcrItem]) -> OcrItem | None:
    """恢复LIVE BOOST「观看广告」分页（写着「确定要观看广告并恢复10点LIVE BOOST吗?」）右下角的「观看」。"""
    if find(items, LB_AD_TEXT) is None:
        return None
    return find(items, "观看", LB_AD_WATCH_ROI, exact=True)


# 奖励到手的画面：广告关掉之后各项目本来就等这些；有跳过广告的权益时点了「观看」不放广告、直接到这里（见 AD_SKIPPED_FRAMES）
def lb_ad_got(items: list[OcrItem]) -> bool:
    """「已恢复LIVE BOOST。」弹出来了（看广告恢复 LB 的奖励到手了）。"""
    return classify(items) is Screen.LB_RECOVERED


def studio_ad_got(items: list[OcrItem]) -> bool:
    """「录音室练习奖励」（或者练习Lv 提升的 LEVEL UP）弹出来了（录音室练习 SKIP 的奖励到手了）。"""
    return reward_ok(items) is not None or practice_level_up(items)


def cm_recruit_got(items: list[OcrItem]) -> bool:
    """观看 CM 招募已经在招募了：「确认招募」关掉了，画面上是招募演出（右上角 SKIP）、招募结算或「获得招募赠品」。"""
    if cm_recruit_dialog(items):
        return False
    return skip_button(items) is not None or recruit_top(items) is not None or recruit_gift_ok(items) is not None


def cm_banner(items: list[OcrItem]) -> tuple[int, int] | None:
    """招募页左侧列表里「观看CM招募」横幅的点击位置（没选中、或者选中了都算）。"""
    for it in items:
        if in_roi(it, RECRUIT_LIST_ROI) and re.sub(r"\s+", "", it.text) == "观看" and it.h >= CM_BANNER_MIN_H:
            x, y = center(it)
            return x + CM_BANNER_DX, y
    return None


def studio_panels(items: list[OcrItem]) -> list[tuple[int, int]]:
    """录音室练习地图上各乐队标志的点击位置（从左到右）：按标志下面面板上的时间找。"""
    points = []
    for it in items:
        if in_roi(it, STUDIO_MAP_ROI) and _STUDIO_TIMER.search(re.sub(r"\s+", "", it.text)):
            x, y = center(it)
            x, y = x + STUDIO_LOGO_OFFSET[0], y + STUDIO_LOGO_OFFSET[1]
            if STUDIO_TAP_X[0] <= x <= STUDIO_TAP_X[1]:
                points.append((x, y))
    return sorted(points)


def same_panels(a: list[tuple[int, int]] | None, b: list[tuple[int, int]] | None) -> bool:
    """两次看到的乐队标志位置一样（地图没有动）。"""
    return bool(a) and bool(b) and len(a) == len(b) and all(math.dist(p, q) <= STUDIO_SAME_PX for p, q in zip(a, b))


def studio_map(items: list[OcrItem]) -> bool:
    """录音室练习的地图（右下角有「一键领取」；乐队的详情页标题也是「录音室练习」）。"""
    return page_title(items) == STUDIO_PAGE and find(items, "一键领取", STUDIO_CLAIM_ALL_ROI, exact=True) is not None


def studio_detail(items: list[OcrItem]) -> bool:
    """录音室练习里某个乐队的详情页（右上角「练习Lv」，下面「SKIP」「领取奖励」）。"""
    return find(items, "练习Lv", STUDIO_LV_LABEL_ROI) is not None and find(items, "SKIP", STUDIO_SKIP_ROI, exact=True) is not None


def studio_ads_left(items: list[OcrItem], roi: Rect = STUDIO_BADGE_ROI) -> int | None:
    """``roi`` 里角标「广告剩余N次」的 N（详情页 SKIP 上面的，或者 SKIP 弹窗「广告」按钮上面的）；没有角标为 None。"""
    for it in items:
        if in_roi(it, roi) and (m := _STUDIO_LEFT.search(re.sub(r"\s+", "", it.text))):
            return int(m.group(1))
    return None


def studio_level_roi(frame) -> Rect:
    """详情页右上角练习Lv 的数字在哪。等级到上限时数字前面有一把锁（整页识别时读成「日」「8」，和数字连在一起），
    锁和数字之间空着一段：从右往左找到这段空隙，只留它右边的。没有锁时是整个 STUDIO_LV_ROI。"""
    if not isinstance(frame, np.ndarray):
        return STUDIO_LV_ROI
    x, y, w, h = STUDIO_LV_ROI
    fh, fw = frame.shape[:2]
    sx, sy = fw / 1280, fh / 720
    strip = frame[round(y * sy) : round((y + h) * sy), round(x * sx) : round((x + w) * sx)]
    cols = np.flatnonzero((strip.min(axis=2) > STUDIO_LV_WHITE).any(axis=0))  # 白字所在的列
    if cols.size == 0:
        return STUDIO_LV_ROI
    left = cols[-1]
    for c in cols[::-1]:
        if left - c > STUDIO_LV_GAP * sx:
            break
        left = c
    cut = max(0, int(left / sx) - 6)
    return x + cut, y, w - cut, h


def studio_level_text(items: list[OcrItem]) -> int | None:
    """整页识别结果里的练习Lv：取最后一串数字（前面的锁读成「日」时是对的，读成数字又连在一起时不对，所以只在没有截图时用）。"""
    for it in items:
        if in_roi(it, STUDIO_LV_ROI) and (digits := re.findall(r"\d+", it.text)):
            return int(digits[-1])
    return None


def studio_band(frame, templates: dict[str, np.ndarray]) -> tuple[str | None, float]:
    """详情页左上角的标志是哪个乐队（``templates``：乐队 → 灰度模板）和匹配度；都不像时乐队为 None。"""
    if not isinstance(frame, np.ndarray) or not templates:
        return None, 0.0
    region = crop_jacket(frame, STUDIO_BAND_ROI)
    size = STUDIO_BAND_ROI[2:]
    if region.shape[1::-1] != size:
        region = cv2.resize(region, size, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    scores = {b: float(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED).max()) for b, t in templates.items()}
    best = max(scores, key=scores.get)
    return (best if scores[best] >= STUDIO_BAND_MIN else None), scores[best]


def studio_ad_button(items: list[OcrItem]) -> OcrItem | None:
    """SKIP 弹窗右边写着「观看1次广告」的按钮上的「广告」两个字（点它）；不是 SKIP 弹窗、角标上没有次数了时为 None。"""
    if not studio_skip_dialog(items) or find(items, STUDIO_AD_TEXT, STUDIO_AD_ROI) is None:
        return None
    if not studio_ads_left(items, STUDIO_AD_ROI):
        return None
    return find(items, "广告", STUDIO_AD_ROI, exact=True)


def ad_recruit_button(items: list[OcrItem]) -> OcrItem | None:
    """选中「观看CM招募」时右下角按钮上的「观看广告免费」（上面是「招募1次」）。"""
    if find(items, "招募1次", AD_RECRUIT_ROI, exact=True) is None:
        return None
    return find(items, AD_RECRUIT_TEXT, AD_RECRUIT_ROI, exact=True)


class AdsMixin:
    """:class:`~ournotes_auto.nav.navigator.GameNavigator` 的看广告。

    用到它的 ``look`` ``tap`` ``_button`` ``_drag`` ``_sleep`` ``_fail`` ``_open_page`` ``_recruit_page``
    ``_settle_recruit`` ``_dismiss`` ``_lit`` ``_run_jobs`` 和 ``front_window`` ``press_back`` ``restart_app``。
    """

    _ad_studio_band = STUDIO_HIGHEST
    _studio_templates: dict[str, np.ndarray] | None = None  # 详情页左上角的乐队标志（第一次用时加载）
    ads_unavailable: tuple[str, ...] = ()  # 上一次 run_ads 里到最后都没要到广告的项目（AD_JOBS 的键）
    wall_clock = staticmethod(time.time)  # 现在的时刻（Unix 秒）：算哪个游戏日、下次什么时候再试

    def run_ads(self, jobs, studio_band: str = STUDIO_HIGHEST, waits=AD_RETRY_WAITS_S, now: float | None = None) -> list[str]:
        """按 :data:`AD_JOBS` 的顺序做 ``jobs`` 里的项目，返回出错的项目名，最后停在主界面。
        ``studio_band``：录音室练习的 SKIP 用在哪个乐队（:data:`STUDIO_BAND_MODES` 之一）。

        没要到广告的项目（:class:`AdUnavailable`，次数没用掉）等别的项目做完后隔 ``waits`` 里的秒数再做一遍（一个数一遍）；
        还是没要到的也算出错，键留在 ``ads_unavailable`` 里并记进存档，之后的挂机隔一阵再试（:meth:`retry_ads`）。
        ``now``：现在的时刻（Unix 秒），算下次再试的时间用。
        """
        if studio_band not in STUDIO_BAND_MODES:
            raise ValueError(f"未知的乐队：{studio_band}")
        self._ad_studio_band = studio_band
        self.ads_unavailable = ()
        later: list[str] = []

        def unavailable(job: str, error: NavigationError) -> bool:
            if not isinstance(error, AdUnavailable):
                return False
            logger.warning("%s", error)
            later.append(job)
            return True

        failed = self._run_jobs(AD_JOBS, jobs, "_ad_", "看广告", defer=unavailable)
        for wait in waits:
            if not later:
                break
            again, later[:] = list(later), []
            logger.warning("%s：没要到广告，%.0f 秒后再试", "、".join(AD_JOBS[j] for j in again), wait)
            self._sleep(wait)
            self.leave_ad()  # 刚才没放出来的广告这时才弹出来的话先关掉
            failed += self._run_jobs(AD_JOBS, again, "_ad_", "看广告", defer=unavailable)
        self.ads_unavailable = tuple(j for j in AD_JOBS if j in later)
        failed += [AD_JOBS[j] for j in self.ads_unavailable]
        self._save_ads_later(jobs, studio_band, self.wall_clock() if now is None else now)
        return failed

    # ------------------------------------------------------------ 没要到广告的留到以后再试

    def _save_ads_later(self, jobs, studio_band: str, now: float) -> None:
        """把今天还没要到广告的项目记进存档（挂机到时间再试）；之前记着、这次没做的照旧留着，都没有了就清掉。
        记不了只警告，不影响正在做的事。"""
        store = self._chore_store
        if store is None:
            return
        package, day = self.cfg.device.package, game_day(now)
        try:
            old = store.ads_pending(package, day)
            was = old["jobs"] if old is not None else ()
            keep = [j for j in AD_JOBS if j in self.ads_unavailable or (j in was and j not in jobs)]
            if keep:
                first = now + AD_LATER_GAPS_S[0]
                tries, due = (old["tries"], max(old["next"], first)) if old is not None else (0, first)
                store.set_ads_pending(package, day, {"jobs": keep, "studio_band": studio_band, "tries": tries, "next": due})
            elif old is not None:
                store.set_ads_pending(package, day, None)
        except OSError as e:
            logger.warning("没要到广告的项目没记下来（%s），之后不会自己再试", e)

    def _ads_later(self, now: float) -> dict | None:
        """今天还等着再试的项目（:meth:`RecordStore.ads_pending`，``jobs`` 只留认得的）；没有、看不了广告时为 None。"""
        store = self._chore_store
        if store is None or self.front_window is None or self.press_back is None:
            return None
        entry = store.ads_pending(self.cfg.device.package, game_day(now))
        if entry is None:
            return None
        entry["jobs"] = [j for j in AD_JOBS if j in entry["jobs"]]
        return entry if entry["jobs"] else None

    def ads_next(self, now: float) -> float | None:
        """今天没要到广告的项目下次再试的时刻（Unix 秒）；没有要再试的为 None。"""
        entry = self._ads_later(now)
        return entry["next"] if entry is not None else None

    def ads_due(self, now: float) -> list[str]:
        """今天没要到广告、到了再试时间的项目（AD_JOBS 的键）；没有为空。``now`` 是现在的时刻（Unix 秒）。"""
        entry = self._ads_later(now)
        return entry["jobs"] if entry is not None and now >= entry["next"] else []

    def retry_ads(self, now: float) -> list[str]:
        """挂机用：把今天「看广告」里没要到广告、到了再试时间（:meth:`ads_due`）的项目再做一遍，返回出错的项目名。
        先把下次再试的时间往后推（AD_LATER_GAPS_S），中途出错、被停掉也不会马上又试；这里不在原地等着重试。"""
        entry = self._ads_later(now)
        if entry is None or now < entry["next"]:
            return []
        jobs, tries = entry["jobs"], entry["tries"] + 1
        gap = AD_LATER_GAPS_S[min(tries, len(AD_LATER_GAPS_S) - 1)]
        band = entry["studio_band"] if entry["studio_band"] in STUDIO_BAND_MODES else STUDIO_HIGHEST
        names = "、".join(AD_JOBS[j] for j in jobs)
        try:
            self._chore_store.set_ads_pending(
                self.cfg.device.package, game_day(now), {**entry, "tries": tries, "next": now + gap}
            )
        except OSError as e:  # 记不了就不试：不然每次都会马上又试
            logger.warning("看广告：再试的时间没记下来（%s），这次不试", e)
            return []
        logger.info("看广告：之前没要到广告的项目再试一遍（%s）", names)
        self.leave_ad()
        failed = self.run_ads(jobs, studio_band=band, waits=(), now=now)
        if self.ads_unavailable:
            left = "、".join(AD_JOBS[j] for j in self.ads_unavailable)
            logger.warning("看广告：%s 还是没要到广告，%.0f 分钟后再试", left, gap / 60)
        elif not failed:
            logger.info("看广告：之前没要到广告的项目都做完了")
        return failed

    # ------------------------------------------------------------ 窗口、返回键

    def _front(self) -> str | None:
        """获得焦点的窗口（``包名/界面``）；读不到（adb 出错）为 None。"""
        try:
            return self.front_window()
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug("读前台窗口失败：%s", e)
            return None

    def _is_game(self, window: str | None) -> bool:
        """``window`` 是游戏自己的界面（包名对得上，也不是广告 SDK 的界面）。"""
        if window is None or "/" not in window:
            return False
        package, activity = window.split("/", 1)
        return package == self.cfg.device.package and not _AD_WINDOW.search(activity)

    def _game_window(self) -> str:
        """现在获得焦点的游戏界面，看广告之前记下来（之后前台不是它就是在放广告）。读不到时报错：这时什么都还没点。"""
        if self.front_window is None or self.press_back is None:
            raise self._fail("看广告要经 adb 读前台窗口、按返回键（这里没有接上）")
        window = self._front()
        if not self._is_game(window):
            raise self._fail(f"读不到游戏的窗口（前台是 {window or '读不到'}），不看广告")
        return window

    def _in_ad(self, window: str | None, game: str | None) -> bool:
        """前台是广告（或者广告弹出的页面）。知道看之前的游戏界面 ``game`` 时不是它就算；不知道时只认广告 SDK 的界面。"""
        if window is None:
            return False
        if game is not None:
            return window != game
        package, _, activity = window.partition("/")
        return package == self.cfg.device.package and bool(_AD_WINDOW.search(activity))

    def _back(self, game: str | None, what: str) -> bool:
        """前台确实是广告时按一下返回键（游戏在前台时按了会被游戏当成返回）。按了返回 True。"""
        window = self._front()
        if not self._in_ad(window, game):
            return False
        logger.debug("按返回键：%s（前台 %s）", what, window)
        try:
            self.press_back()
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("按返回键失败：%s", e)
            return False
        return True

    def _ad_items(self) -> list[OcrItem]:
        """截一帧广告画面做 OCR。不经过 ``look``：广告画面不是游戏的页面，不判断画面、不查卡住。"""
        frame, _ = self.source.grab()
        self._frame = frame
        self._items = self.ocr.read(frame)
        return self._items

    # ------------------------------------------------------------ 看广告

    def _ad_failed(self, game: str, items: list[OcrItem]) -> bool:
        """点了「观看」之后还在游戏里：这一帧（``items``）上弹出了「广告获取失败。请稍后再试。」就点掉 OK、返回 True。"""
        if find(items, AD_FAILED_TEXT) is None:
            return False
        if self._front() != game:  # 截图之后广告正好弹出来了：广告里的东西不点
            return False
        self.tap(self._button(items, "OK", BTN_AD_FAILED_OK, OK_POPUP_ROI), "OK（广告获取失败）")
        self._sleep(1.0)
        return True

    def _watch_or_fail(self, game: str, name: str, tapped: str, close, got) -> None:
        """点了 ``tapped`` 之后看广告（:meth:`_watch_ad`，``got`` 见那里）。没要到广告时先 ``close()`` 关掉还开着的弹窗，
        再抛 :class:`AdUnavailable`。"""
        try:
            if self._watch_ad(game, name, got):
                return
            error = self._fail(f"{name}：点了{tapped}{AD_START_S:.0f}s 广告没有放出来", AdUnavailable)
        except AdUnavailable as e:
            error = e
        close()
        raise error

    def _watch_ad(self, game: str, what: str, got=None) -> bool:
        """点了「观看」/ OK 之后：等广告出现、放到奖励到手，按返回键关掉，回到游戏（``game`` 是看之前的游戏界面）。

        广告 AD_START_S 内没出现返回 False（什么都没按）；游戏说「广告获取失败」时点掉 OK、抛 :class:`AdUnavailable`。
        AD_TIMEOUT_S 还没关掉时放弃这次奖励、想办法回到游戏再报错。

        ``got(items)``：这一帧是奖励到手的画面。游戏里开通了跳过广告的权益时不放广告、奖励直接到手（#146）：
        前台一直是游戏、连续 AD_SKIPPED_FRAMES 帧都是这个画面就返回 True，之后和广告关掉了一样处理。
        """
        package = game.split("/")[0]
        start = time.monotonic()
        ad: _AdWatch | None = None
        got_frames = 0
        while True:
            now = time.monotonic()
            window = self._front()
            if window == game:
                if ad is not None:
                    logger.info("%s：广告关掉了（放了 %.0f 秒）", what, now - ad.since)
                    return True
                items = self._ad_items()
                if self._ad_failed(game, items):
                    raise AdUnavailable(f"{what}：广告获取失败（游戏说请稍后再试，次数没用掉）")
                # 截图之后广告正好弹出来的话，这一帧是广告的画面（广告右上角也常有「跳过」）：前台还是游戏才算
                got_frames = got_frames + 1 if got is not None and got(items) and self._front() == game else 0
                if got_frames >= AD_SKIPPED_FRAMES:
                    logger.info("%s：没有放广告，奖励直接到手了（游戏里开通了跳过广告？）", what)
                    return True
                if now - start > AD_START_S and not got_frames:
                    return False
            elif window is None:  # 读不到前台窗口：接着等，一直读不到就放弃
                got_frames = 0
                if now - start > AD_START_S + AD_TIMEOUT_S:
                    break
            else:
                got_frames = 0
                if ad is None:
                    ad = _AdWatch(game, now)
                    logger.info("%s：广告开始播放（等它放完，不点广告里的东西）", what)
                if now - ad.since > AD_TIMEOUT_S:
                    break
                if window.startswith(package + "/"):
                    self._ad_step(ad, now)
                elif now - ad.back_at >= AD_BACK_GAP_S and self._back(game, "关掉广告弹出的页面"):
                    ad.back_at = now  # 广告自己弹出的商店页等：开着时广告不往下走
            self._sleep(AD_POLL_S)
        logger.warning("%s：广告 %.0f 秒还没有结束，放弃这次的奖励", what, AD_TIMEOUT_S)
        self.leave_ad(game)
        raise self._fail(f"{what}：广告 {AD_TIMEOUT_S:.0f}s 没有结束（放弃了这次的奖励）")

    def _ad_step(self, ad: _AdWatch, now: float) -> None:
        """广告界面上的一步：认顶部的提示条，奖励到手（或者多半到手了）时按返回键；被问「关闭广告？」就点「继续」。"""
        items = self._ad_items()
        bar = " | ".join(it.text.strip() for it in items if in_roi(it, AD_BAR_ROI))
        if bar != ad.bar:
            ad.bar = bar
            logger.debug("广告顶部：%s", bar or "（没有文字）")
        if ad.back_shot:  # 调试：按了返回键之后的第一帧
            ad.back_shot = False
            logger.debug("按返回键之后的画面：%s", self.save_debug("ad_after_back"))
        closing = ad_closing(items)
        if closing is not None:
            # 奖励还没到手：点「继续」接着放，过一阵再试
            if closing[1] is not None:
                self.tap(center(closing[1]), "继续（广告）")
            ad.gone = 0
            ad.resumed_at = now
            return
        if ad_countdown(items) is not None:  # 倒计时还在走：奖励没到手，不按
            ad.counted, ad.gone = True, 0
            return
        why = "奖励已发放"
        if not ad_granted(items):
            if ad_progress(self._frame):
                ad.counted, ad.gone = True, 0
            else:
                ad.gone += ad.counted
            played = now - ad.since
            if ad.counted and ad.gone >= AD_GONE_FRAMES:
                why = "倒计时 / 进度条没有了"
            elif played >= AD_BLIND_S and now - ad.back_at >= AD_BLIND_GAP_S:
                why = f"放了 {played:.0f} 秒"
            else:
                return
            if now - ad.resumed_at < AD_RETRY_GAP_S:
                return
        if now - ad.back_at < AD_BACK_GAP_S:
            return
        debug = logger.isEnabledFor(logging.DEBUG)
        if debug:  # 调试：按返回键之前的画面（看得出当时提示条是什么样）
            logger.debug("按返回键之前的画面：%s", self.save_debug("ad_before_back"))
        if self._back(ad.game, why):
            ad.back_at, ad.back_shot = now, debug

    def leave_ad(self, game: str | None = None) -> None:
        """广告还开着（放了太久，或者上次看到一半停了）时关掉、回到游戏：按返回键，问「关闭广告？」就点「关闭」；
        还是关不掉就重启游戏。``game`` 是看之前的游戏界面，不知道（None）时按窗口的名字认。没有广告时什么都不做。"""
        if self.front_window is None or self.press_back is None:
            return
        for attempt in range(AD_LEAVE_TRIES):
            window = self._front()
            if not self._in_ad(window, game):
                return
            if not attempt:
                logger.warning("广告还开着（前台 %s），关掉它", window)
            if window.split("/")[0] == self.cfg.device.package:
                closing = ad_closing(self._ad_items())
                if closing is not None and closing[0] is not None:
                    self.tap(center(closing[0]), "关闭（广告）")
                    self._sleep(AD_POLL_S)
                    continue
            self._back(game, "关掉广告")
            self._sleep(AD_POLL_S)
        window = self._front()
        if not self._in_ad(window, game):
            return
        if self.restart_app is None:
            raise self._fail(f"广告关不掉（前台 {window}）")
        logger.warning("广告关不掉（前台 %s），重启游戏", window)
        self.restart_app()
        self._sleep(3.0)

    # ------------------------------------------------------------ 恢复 LIVE BOOST

    def _ad_lb(self) -> None:
        """主界面 LB 旁的「+」→「观看广告」分页 → 观看 → 广告 → 「已恢复LIVE BOOST。」OK，做到今天的次数用完。"""
        name = AD_JOBS["lb"]
        game = self._game_window()
        for _ in range(AD_JOBS_MAX["lb"]):
            held = home_lb(self._items)
            items = self._open_lb_recover()
            left = ads_left(items, LB_AD_LEFT_ROI)
            watch = self._lb_ad_tab(items) if left != 0 else None
            if watch is None:
                self.tap(self._button(self._items, "取消", BTN_LB_AD_CANCEL, LB_AD_CANCEL_ROI), "取消")
                self._sleep(1.0)
                logger.info("%s：今天的次数用完了", name)
                return
            logger.info("%s：%s", name, f"还剩 {left} 次" if left is not None else "开始")
            self.tap(center(watch), "观看")
            self._watch_or_fail(game, name, "「观看」", self._lb_cancel, lb_ad_got)
            self._lb_ad_done(name)
            self._chore("ad_lb")
            self.go_home()
            after = home_lb(self._items)
            logger.info("%s：已恢复%s", name, f"（LB {held} → {after}）" if None not in (held, after) else "")

    def _lb_cancel(self) -> None:
        """恢复LIVE BOOST 弹窗还开着的话点「取消」。"""
        screen, items = self.look()
        if screen is Screen.LB_RECOVER:
            self.tap(self._button(items, "取消", BTN_LB_AD_CANCEL, LB_AD_CANCEL_ROI), "取消")
            self._sleep(1.0)

    def _open_lb_recover(self) -> list[OcrItem]:
        """在主界面点 LB 旁的「+」，等恢复LIVE BOOST 弹窗稳定（连续两帧），返回它的识别结果。"""
        self.tap(BTN_HOME_LB_ADD, "LB +")
        start = tapped = time.monotonic()
        seen = False
        while time.monotonic() - start < LB_OPEN_S:
            self._sleep(0.8)
            screen, items = self.look()
            if screen is Screen.LB_RECOVER:
                if seen:
                    return items
                seen = True
                continue
            seen = False
            if screen is Screen.HOME and time.monotonic() - tapped > LB_RETAP_S:
                self.tap(BTN_HOME_LB_ADD, "LB +")
                tapped = time.monotonic()
        raise self._fail("没能打开恢复LIVE BOOST")

    def _lb_ad_tab(self, items: list[OcrItem], timeout_s: float = 6.0) -> OcrItem | None:
        """切到「观看广告」分页，连续两帧写着「确定要观看广告并恢复…」、「观看」亮着时返回它；
        次数用完（角标写着还剩 0 次，或者「观看」是灰的）、没有这个分页时返回 None。"""
        self.tap(self._button(items, "观看广告", BTN_LB_AD_TAB, LB_TABS_ROI), "观看广告")
        start = time.monotonic()
        good = 0
        while time.monotonic() - start < timeout_s:
            self._sleep(0.6)
            screen, items = self.look()
            watch = lb_ad_watch(items) if screen is Screen.LB_RECOVER else None
            good = good + 1 if watch is not None else 0
            if good >= 2:
                x, y = center(watch)
                lit = ads_left(items, LB_AD_LEFT_ROI) != 0 and self._lit((x + AD_BUTTON_LIT_DX, y))
                return watch if lit else None
        return None

    def _lb_ad_done(self, name: str) -> None:
        """广告关掉之后：等「已恢复LIVE BOOST。」点 OK。回到了恢复LIVE BOOST 弹窗、一直没有「已恢复」就是奖励没到手：点取消、报错。"""
        start = time.monotonic()
        ok_at = back_since = None
        while time.monotonic() - start < LB_DONE_S:
            self._sleep(0.8)
            screen, items = self.look()
            now = time.monotonic()
            if screen is Screen.LB_RECOVERED:
                if ok_at is None or now - ok_at > 2:
                    self._common_step(screen, items)
                    ok_at = now
            elif ok_at is not None:
                return
            elif screen is Screen.LB_RECOVER:
                back_since = back_since or now
                if now - back_since > LB_BACK_S:
                    self.tap(self._button(items, "取消", BTN_LB_AD_CANCEL, LB_AD_CANCEL_ROI), "取消")
                    self._sleep(1.0)
                    raise self._fail(f"{name}：广告关掉后没有恢复 LB（奖励没到手）")
            else:
                back_since = None
        raise self._fail(f"{name}：广告关掉后 {LB_DONE_S:.0f}s 没看到「已恢复LIVE BOOST。」")

    # ------------------------------------------------------------ 录音室练习的 SKIP

    def _ad_studio(self) -> None:
        """录音室练习 → 选好的乐队的详情页 → SKIP →「广告」→ 广告 →「录音室练习奖励」OK。每天 1 次，各乐队共用。"""
        name = AD_JOBS["studio"]
        game = self._game_window()
        if not self._open_page(STUDIO_PAGE):
            return
        if not self._studio_pick(name, self._ad_studio_band):
            logger.info("%s：今天的次数用完了", name)
            return
        self.tap(self._button(self._items, "SKIP", BTN_STUDIO_SKIP, STUDIO_SKIP_ROI), "SKIP")
        button = self._studio_skip(name)
        if button is None:
            logger.info("%s：今天的次数用完了", name)
            return
        self.tap(center(button), "广告（SKIP）")
        self._watch_or_fail(game, name, "「广告」", self._studio_close_skip, studio_ad_got)
        self._studio_done(name)
        self._chore("ad_studio")
        logger.info("%s：已领取练习奖励", name)

    def _studio_pick(self, name: str, want: str) -> bool:
        """在录音室练习的地图上找到要用 SKIP 的乐队（``want``：某个乐队，或者练习Lv 最高的），停在它的详情页，返回 True。
        详情页上没有「广告剩余N次」的角标（次数各乐队共用，今天的用完了）时返回 False。

        地图拖到最左时从左往右看，拖到最右时从右往左看、看到已经看过的乐队就停（中间的乐队两头都看得到）。"""
        seen: list[tuple[int, int, tuple[int, int], str | None]] = []  # (练习Lv, 地图的哪一头, 点击位置, 乐队)
        bands: set[str] = set()
        for side in range(len(STUDIO_MAP_SIDES)):
            panels = self._studio_side(side)
            for point in reversed(panels) if side else panels:
                band, level, left = self._studio_open(point)
                logger.debug("录音室练习：%s 练习Lv %s，广告剩余 %s 次", band or "认不出的乐队", level, left)
                if not left:
                    return False
                if band == want:
                    return True
                again = band in bands
                if not again and level is not None:
                    seen.append((level, side, point, band))
                if band is not None:
                    bands.add(band)
                self._studio_back()
                if again:
                    break
        if want != STUDIO_HIGHEST:
            raise self._fail(f"{name}：录音室练习里没有找到 {want}（这个乐队的练习还没有开放，或者标志没认出来）")
        if not seen:
            raise self._fail(f"{name}：读不到各乐队的练习Lv")
        level, side, point, band = max(seen, key=lambda v: v[0])  # 一样高时是先看到的那个
        logger.info("%s：练习Lv 最高的是 %s（Lv%d）", name, band or "没认出来的乐队", level)
        near = min(self._studio_side(side), key=lambda q: math.dist(q, point))
        if math.dist(near, point) > STUDIO_SAME_PX:
            raise self._fail(f"{name}：回到地图上找不到刚才看过的乐队了（{point} → {near}）")
        again, _, left = self._studio_open(near)
        if again != band:
            raise self._fail(f"{name}：再点进去的不是刚才那个乐队（{band} → {again}）")
        return bool(left)

    def _studio_side(self, side: int) -> list[tuple[int, int]]:
        """把地图拖到最左（0）/ 最右（1）：拖一次看一次，乐队标志的位置不再变就是到头了。返回这时各标志的点击位置。"""
        last = None
        for _ in range(STUDIO_DRAGS):
            self._drag(*STUDIO_MAP_SIDES[side])
            panels = self._studio_panels()
            if same_panels(panels, last):
                return panels
            last = panels
        raise self._fail("录音室练习的地图一直在动，拖不到头")

    def _studio_panels(self) -> list[tuple[int, int]]:
        """地图上各乐队标志的点击位置（连续两帧一样才算：切换页面、拖动之后画面要停下来）。"""
        start = time.monotonic()
        last = None
        while time.monotonic() - start < STUDIO_OPEN_S:
            self._sleep(0.6)
            _, items = self.look()
            panels = studio_panels(items) if studio_map(items) else None
            if same_panels(panels, last):
                return panels
            last = panels
        raise self._fail("录音室练习的地图上没有找到乐队")

    def _studio_open(self, point: tuple[int, int]) -> tuple[str | None, int | None, int | None]:
        """点地图上的乐队标志，等详情页稳定（连续两帧），返回（乐队, 练习Lv, 广告剩余的次数）；认不出、读不到的是 None。"""
        self.tap(point, "乐队（录音室练习）")
        start = tapped = time.monotonic()
        good = 0
        left = None
        while time.monotonic() - start < STUDIO_OPEN_S:
            self._sleep(0.6)
            screen, items = self.look()
            if studio_detail(items):
                good += 1
                left = studio_ads_left(items) or left  # 角标偶尔漏读：两帧里有一帧读到就算
                if good >= 2:
                    return self._studio_band(), self._studio_level(items), left
                continue
            good = 0
            if studio_map(items):
                if time.monotonic() - tapped > STUDIO_RETAP_S:
                    self.tap(point, "乐队（录音室练习）")
                    tapped = time.monotonic()
            else:
                self._dismiss(screen, items)  # 第一次打开时的说明等
        raise self._fail("点了录音室练习的乐队标志，没有进到详情页")

    def _studio_back(self) -> None:
        """从乐队的详情页回到地图（地图停在原来的位置）。"""
        self.tap(BTN_BACK, "返回")
        start = tapped = time.monotonic()
        on_map = 0
        while time.monotonic() - start < STUDIO_OPEN_S:
            self._sleep(0.6)
            _, items = self.look()
            on_map = on_map + 1 if studio_map(items) else 0
            if on_map >= 2:  # 刚切回地图的那一下点了、拖了都没反应
                return
            if studio_detail(items) and time.monotonic() - tapped > STUDIO_RETAP_S:
                self.tap(BTN_BACK, "返回")
                tapped = time.monotonic()
        raise self._fail("从录音室练习的乐队详情页回不到地图")

    def _studio_band(self) -> str | None:
        """最近一帧（详情页）左上角的标志是哪个乐队，认不出为 None。"""
        if self._studio_templates is None:
            self._studio_templates = {}
            for band in STUDIO_BANDS:
                tpl = load_template(Path(f"resource/image/studio_band_{band}.png"))
                if tpl is not None:
                    self._studio_templates[band] = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
            if not self._studio_templates:
                logger.warning("没有录音室练习的乐队模板（resource/image/studio_band_*.png），认不出是哪个乐队")
        band, score = studio_band(self._frame, self._studio_templates)
        logger.debug("录音室练习的乐队标志：%s（匹配度 %.2f）", band, score)
        return band

    def _studio_level(self, items: list[OcrItem]) -> int | None:
        """最近一帧（详情页）右上角的练习Lv：只识别数字那一块（不带前面的锁），读不出来时退回整页识别的结果。"""
        if isinstance(self._frame, np.ndarray):
            text = self.ocr.read_text(self._frame, studio_level_roi(self._frame)).strip()
            if re.fullmatch(r"\d{1,3}", text):
                return int(text)
        return studio_level_text(items)

    def _studio_skip(self, name: str) -> OcrItem | None:
        """点了 SKIP 之后：等 SKIP 弹窗稳定（连续两帧），返回写着「观看1次广告」的「广告」按钮。
        角标上没有次数了、按钮是灰的时点「关闭」、返回 None。"""
        start = time.monotonic()
        good = 0
        while time.monotonic() - start < STUDIO_OPEN_S:
            self._sleep(0.6)
            _, items = self.look()
            if not studio_skip_dialog(items):
                good = 0
                continue
            good += 1
            if good < 2:
                continue
            button = studio_ad_button(items)
            if button is not None:
                x, y = center(button)
                if self._lit((x + AD_BUTTON_LIT_DX, y)):
                    return button
            self._studio_close_skip()
            return None
        raise self._fail(f"{name}：点了 SKIP 没有弹出 SKIP 弹窗")

    def _studio_close_skip(self) -> None:
        """SKIP 弹窗还开着的话点「关闭」。"""
        _, items = self.look()
        close = studio_skip_close(items)
        if close is not None:
            self.tap(center(close), "关闭（SKIP）")
            self._sleep(1.0)

    def _studio_done(self, name: str) -> None:
        """广告关掉之后：等「录音室练习奖励」点 OK（练习Lv 提升的 LEVEL UP 也点掉），回到详情页。
        回到了 SKIP 弹窗 / 详情页、一直没有奖励就是奖励没到手：关掉弹窗、报错。"""
        start = time.monotonic()
        got = False
        back_since = None
        while time.monotonic() - start < STUDIO_DONE_S:
            self._sleep(0.8)
            screen, items = self.look()
            now = time.monotonic()
            if reward_ok(items) is not None or practice_level_up(items):
                got = got or reward_ok(items) is not None
                self._dismiss(screen, items)
                back_since = None
            elif studio_skip_dialog(items) or studio_detail(items):
                if got:
                    return
                back_since = back_since or now
                if now - back_since > STUDIO_BACK_S:
                    self._studio_close_skip()
                    raise self._fail(f"{name}：广告关掉后没有拿到练习奖励（奖励没到手）")
            else:
                back_since = None
        if got:
            return
        raise self._fail(f"{name}：广告关掉后 {STUDIO_DONE_S:.0f}s 没看到「录音室练习奖励」")

    # ------------------------------------------------------------ 观看 CM 招募

    def _ad_recruit(self) -> None:
        """招募页选中「观看CM招募」→ 观看广告免费 → 确认招募 OK → 广告 → 招募结算（截图）→ 招募TOP，做到今天的次数用完。"""
        name = AD_JOBS["recruit"]
        game = self._game_window()
        if not self._open_page("招募"):
            return
        for _ in range(AD_JOBS_MAX["recruit"]):
            button, gems = self._cm_page()
            if button is None:
                logger.info("%s：招募页上没有找到「观看CM招募」，跳过", name)
                return
            left = ads_left(self._items, AD_RECRUIT_ROI)
            x, y = center(button)
            y += AD_RECRUIT_TAP_DY
            if left == 0 or not self._lit((x + AD_BUTTON_LIT_DX, y)):
                logger.info("%s：今天的次数用完了", name)
                return
            logger.info("%s：%s", name, f"还剩 {left} 次" if left is not None else "开始")
            self.tap((x, y), AD_RECRUIT_TEXT)
            ok = self._confirm_cm_recruit(name)
            if ok is None:
                raise self._fail(f"{name}：点了「{AD_RECRUIT_TEXT}」没有弹出确认招募")
            self.tap(center(ok), "OK（观看CM招募）")
            self._watch_or_fail(game, name, " OK ", self._cm_cancel, cm_recruit_got)
            shot = self._settle_recruit(what=name, stay_s=CM_STAY_S, tag=RECRUIT_SHOT_CM)
            self._chore("ad_recruit", shot)
            if shot is not None:
                logger.info("%s：结果截图 %s", name, shot.as_posix())
                log_image(shot)
            _, after = self._recruit_page()
            paid_changed = None not in (gems[1], after[1]) and after[1] != gems[1]
            if after[0] != gems[0] or paid_changed:
                raise self._fail(f"{name}后星钻数变了（{_gems_text(gems)} → {_gems_text(after)}）")
            logger.info("%s：已招募", name)

    def _cm_cancel(self) -> None:
        """「确认招募」还开着的话点「取消」（没有招募）。"""
        _, items = self.look()
        cancel = cm_recruit_cancel(items) if cm_recruit_dialog(items) else None
        if cancel is not None:
            self.tap(center(cancel), "取消")
            self._sleep(1.0)

    def _cm_page(self) -> tuple[OcrItem | None, tuple[int, int | None]]:
        """在招募页选中左侧的「观看CM招募」（没看到时往上拖列表找），返回「观看广告免费」按钮（找不到为 None）和（星钻, 付费星钻）。"""
        _, gems = self._recruit_page()
        for attempt in range(CM_FIND_TRIES):
            button = ad_recruit_button(self._items)
            if button is not None:
                return button, gems
            banner = cm_banner(self._items)
            if banner is not None:
                self.tap(banner, "观看CM招募")
            elif attempt < CM_FIND_TRIES - 1:
                self._drag(*CM_LIST_DRAG)
            self._sleep(1.0)
            _, gems = self._recruit_page()
        return ad_recruit_button(self._items), gems

    def _confirm_cm_recruit(self, name: str, timeout_s: float = 12.0) -> OcrItem | None:
        """点了「观看广告免费」后的「确认招募」：连续几帧都写着看广告、不会消耗星钻时返回 OK；写的不是这些就点取消、报错。
        一直没弹出来返回 None。"""
        start = time.monotonic()
        good = bad = 0
        while time.monotonic() - start < timeout_s:
            self._sleep(0.8)
            _, items = self.look()
            if not cm_recruit_dialog(items):
                good = bad = 0
                continue
            ok = cm_recruit_ok(items)
            if ok is not None:
                good, bad = good + 1, 0
                if good >= RECRUIT_DIALOG_FRAMES:
                    return ok
                continue
            good, bad = 0, bad + 1
            if bad >= RECRUIT_DIALOG_BAD_FRAMES:
                cancel = cm_recruit_cancel(items)
                if cancel is not None:
                    self.tap(center(cancel), "取消")
                    self._sleep(1.0)
                done = "点了取消" if cancel is not None else "没有点"
                raise self._fail(f"{name}：确认招募弹窗上没写着看广告、不会消耗星钻（{done}，没有招募）")
        return None
