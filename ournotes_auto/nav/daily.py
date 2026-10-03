"""日常：领取录音室练习、任务、通行证、限定任务、新手任务、T.G.W CARD、礼物盒的奖励（坐标均为 1280x720 设计尺寸）。

- 入口都在主界面。右侧一排从上到下是：礼物盒、任务、PASS（任务通行证）、交换所、录音室练习、消息。
  左侧一列是：新手任务、好友邀请、限定任务。底部是招募、商店。左侧的入口随活动增减，位置可能变化，所以打开后都要核对标题。
- 任务、礼物盒、通行证任务是弹窗，左下角有「关闭」。其余是整页，左上角有主页按钮（房子图标）。
- 任务通行证可能同时有几个（左侧列表），通行证任务也是每个通行证各一份，逐个选中来领。
- 「一键领取」没有可领的奖励时是灰的，亮着才点。
  - 录音室练习的一键领取总是亮的，没有奖励时提示「没有可领取的奖励。」。
- 领取后依次弹出的「获得奖励」只点 OK（T.G.W CARD 积分的只有「关闭」）；练习等级提升点空白处继续；其他弹窗只点「关闭」「取消」。
  - 认不出的确认框一律不点，报错停下。
- T.G.W CARD 在商店里：T.G.W CARD 页领每日积分和每日奖励，专享商品目录里只领价格是「免费」的商品。
  - 免费商品点「购买」直接买下（没有确认框），弹出「购买完成」只点 OK。
  - 领完后面的商品会补到同一格（可能要星钻），所以不按位置点：每次都重新认「免费」，领完核对星钻数没变。
- 不碰招募、礼包、交换所、通行证高级档和 pt 旁的「+」，不用星钻。
- 看故事（跳过没看过的乐队故事、视角故事、羁绊故事）在 story.py。
"""

from __future__ import annotations

import logging
import math
import re
import time

from ..result_reader import OcrItem
from ..runner import NavigationError, ServerMaintenance
from .screens import RELOGIN_SCREENS, TITLE_ROI, Rect, Screen, center, find, in_roi
from .song_select import filter_open

logger = logging.getLogger(__name__)

# 项目 → 页面标题，按这个顺序做：任务奖励可能超过持有上限被送进礼物盒，所以礼物盒最后；
# 看故事（见 story.py）在领任务之前，看完可能完成任务
DAILY_JOBS = {
    "studio": "录音室练习",
    "story": "看故事",
    "missions": "任务",
    "pass": "任务通行证",
    "limited": "限定任务",
    "beginner": "新手任务",
    "tgw": "T.G.W CARD",
    "gifts": "礼物盒",
}
# 不指定项目时不做的（要下载数据、比较慢，界面上默认也不勾）
OPT_IN_JOBS = frozenset(("story",))

# 主界面入口
HOME_ENTRIES = {
    "录音室练习": (1232, 515),
    "任务": (1232, 243),
    "任务通行证": (1232, 340),
    "限定任务": (40, 325),
    "新手任务": (40, 152),
    "礼物盒": (1232, 150),
    "商店": (690, 620),
}
# 页面标题（左上角）必须完全相等：「任务」「通行证任务」「任务通行证」互相包含，「录音室练习奖励」是领取后的弹窗
POPUP_PAGES = ("任务", "礼物盒", "通行证任务")  # 左下角「关闭」
FULL_PAGES = ("录音室练习", "任务通行证", "限定任务", "新手任务", "好友邀请", "交换所", "商店", "T.G.WCARD")  # 左上角主页按钮
BTN_PAGE_CLOSE = (497, 655)
PAGE_CLOSE_ROI: Rect = (400, 620, 200, 70)
BTN_HOME = (164, 40)  # 左上角主页按钮
BTN_BACK = (60, 40)  # 主页按钮左边的返回

# 各页「一键领取」的位置。按钮底色亮着约 180，灰的约 90，在文字左侧取样
CLAIM_BUTTONS = {
    "任务": (790, 654),
    "通行证任务": (790, 657),
    "礼物盒": (790, 650),
    "任务通行证": (1132, 656),
    "录音室练习": (1119, 658),
    "限定任务": (1066, 642),
    "新手任务": (1066, 642),
}
CLAIM_SEARCH = 60  # OCR 找到的按钮文字离默认位置不超过这么远才采用
CLAIM_LIT_DX = -75
# 领取后这么久没有新的弹窗就算领完了
CLAIM_QUIET_S = 3.0

# 左侧分页（任务、通行证任务）；限定任务、新手任务右侧是「1天」「2天」……
# 新手任务第 7 天的分页在 y≈635，没解锁时只有锁图标、认不出文字，就不会去点
MISSION_TABS = (("每日", (146, 138)), ("常规", (133, 206)), ("乐曲解锁", (133, 274)), ("主页解锁", (133, 342)))
PASS_MISSION_TABS = (("每日", (147, 118)), ("常规", (148, 192)))
TAB_ROI: Rect = (30, 90, 240, 300)
DAY_TAB_ROI: Rect = (1165, 100, 115, 570)
_DAY_TAB = re.compile(r"\d+天")

# 任务通行证页右上角的「通行证任务」（左边 pt 旁的「+」是购买，不要点）
BTN_PASS_MISSIONS = (1165, 40)
PASS_MISSIONS_ROI: Rect = (1080, 10, 190, 60)
# 任务通行证页左侧的通行证列表（活动通行证和赛季通行证可能同时有），每个横幅下面写着截止时间。
# 选中的横幅放大、右移，截止时间的中心 x≈178，没选中的 x≈138；横幅图片在截止时间上方约 55px
PASS_LIST_ROI: Rect = (0, 80, 300, 640)
PASS_SELECTED_X = 158
PASS_BANNER_DY = -55
_PASS_DATE = re.compile(r"\d{4}/\d{1,2}/\d{1,2}")
# 商店左下角的 T.G.W CARD 入口；左侧分页从上到下是星钻、礼包、T.G.W CARD 专享商品目录、交织的乐章通行证
BTN_TGW = (113, 555)
TGW_ROI: Rect = (0, 480, 240, 140)
BTN_CATALOG_TAB = (113, 268)
SHOP_TAB_ROI: Rect = (0, 90, 260, 300)
# 选中的分页是青色（G 约 170、R 约 90），没选中的是蓝紫色（G 约 90），在分页左侧取样
SHOP_TAB_SAMPLE_X = 40
# T.G.W CARD 页的领取按钮：(名称, 按钮文字, 默认位置, 在文字左边多远取亮度)
TGW_CLAIMS = (("积分", "领取积分", (136, 663), -50), ("每日奖励", "领取", (1016, 657), -75))
# 商店的商品格：「购买」正下方是价格（「免费」或星钻数）
SHOP_GRID_ROI: Rect = (250, 90, 1000, 630)
FREE_BUY_DX = 25
FREE_BUY_DY = 50
SHOP_FREE_MAX = 5  # 一次最多领这么多件，领完还有就停下
GEM_ROI: Rect = (1040, 5, 140, 55)  # 右上角的星钻数
_GEMS = re.compile(r"\d{1,3}(,\d{3})*")
PURCHASE_TITLE_ROI: Rect = (400, 0, 480, 70)  # 「购买完成」在画面顶部中央
PURCHASE_WAIT_S = 8.0  # 点了「购买」这么久还停在商店就算没反应
PURCHASE_RETAP_S = 5.0  # 点了 OK 这么久「购买完成」还在才再点
TAP_LEVEL_UP = (640, 660)  # 练习等级 LEVEL UP：点下方空白处继续
BOTTOM_ROI: Rect = (0, 600, 1280, 120)
# 只有 OK 的提示弹窗（领取奖励、通行证 pt 到账）的标题和 OK 所在范围
REWARD_TITLES = ("获得奖励", "领取奖励", "获得通行证")
REWARD_OK_ROI: Rect = (0, 450, 1280, 270)
TITLE_BAR_ROI: Rect = (400, 80, 480, 80)  # 居中弹窗的标题栏
BTN_GIFT_CANCEL = (496, 570)  # 「是否一键领取礼物？」的取消
# 左上角有主页按钮、可以直接点它回主界面的画面
HOME_BUTTON_SCREENS = frozenset(
    (
        Screen.LIVE_TOP,
        Screen.SONG_SELECT,
        Screen.BAND_CONFIRM,
        Screen.CHALLENGE_SONG_SELECT,
        Screen.CHALLENGE_BAND_CONFIRM,
        Screen.SETTINGS,
    )
)
# 领取后可能弹出、只需要关掉的弹窗（底部中央是「关闭」或「OK」，或者右上角有「跳过」的演出）
KNOWN_POPUPS = frozenset(
    (
        Screen.REWARD,
        Screen.GRADE_UP,
        Screen.BOND_UP,
        Screen.POPUP,
        Screen.ACHIEVEMENT,
        Screen.UNLOCK,
        Screen.NEW_SONG,
        Screen.OK_POPUP,
        Screen.SKIPPABLE,
    )
)


def page_title(items: list[OcrItem]) -> str | None:
    for title in (*POPUP_PAGES, *FULL_PAGES):
        if find(items, title, TITLE_ROI, exact=True):
            return title
    return None


def has_confirm(items: list[OcrItem]) -> bool:
    """画面上有「OK」「确定」这类确认按钮（认不出的弹窗上绝不点）。"""
    return any(find(items, text, exact=True) for text in ("OK", "确定"))


def reward_ok(items: list[OcrItem]) -> OcrItem | None:
    """领到东西后只有 OK 的提示弹窗上的 OK：「获得奖励」（录音室练习奖励的 OK 在右下，其他在中下）、
    「获得通行证pt」（OK 在中间偏下）。"""
    if not any(find(items, text) for text in REWARD_TITLES):
        return None
    if any(find(items, text, exact=True) for text in ("取消", "确定")) or find(items, "购买"):
        return None
    return find(items, "OK", REWARD_OK_ROI, exact=True)


def reward_close(items: list[OcrItem]) -> OcrItem | None:
    """只有「关闭」的获得奖励弹窗（T.G.W CARD 领积分后「已获得T.G.W CARD pt。」，关闭在中间偏下）上的关闭。"""
    if find(items, "获得奖励", TITLE_BAR_ROI, exact=True) is None:
        return None
    if any(find(items, text, exact=True) for text in ("取消", "确定", "OK", "购买")):
        return None
    return find(items, "关闭", REWARD_OK_ROI, exact=True)


def gift_confirm(items: list[OcrItem]) -> OcrItem | None:
    """礼物盒「一键领取」后的确认弹窗「领取礼物：是否一键领取礼物？※最多可领取100件。」上的 OK。"""
    if find(items, "领取礼物", TITLE_BAR_ROI, exact=True) is None or find(items, "一键领取礼物") is None:
        return None
    return find(items, "OK", REWARD_OK_ROI, exact=True)


def purchase_ok(items: list[OcrItem]) -> OcrItem | None:
    """免费商品「购买」后的「购买完成」弹窗上的 OK（弹窗上没有「取消」「确定」「购买」按钮才算）。"""
    if find(items, "购买完成", PURCHASE_TITLE_ROI, exact=True) is None:
        return None
    if any(find(items, text, exact=True) for text in ("取消", "确定", "购买")):
        return None
    return find(items, "OK", REWARD_OK_ROI, exact=True)


def free_buys(items: list[OcrItem]) -> list[tuple[int, int]]:
    """商店商品格里价格是「免费」的商品的「购买」按钮（从上到下、从左到右）。"""
    buys = [center(it) for it in items if in_roi(it, SHOP_GRID_ROI) and _compact(it.text) == "购买"]
    found = []
    for it in items:
        if not in_roi(it, SHOP_GRID_ROI) or _compact(it.text) != "免费":
            continue
        fx, fy = center(it)
        found += [(bx, by) for bx, by in buys if abs(bx - fx) <= FREE_BUY_DX and 0 < fy - by <= FREE_BUY_DY][:1]
    return sorted(found, key=lambda p: (p[1], p[0]))


def gem_balance(items: list[OcrItem]) -> int | None:
    """商店右上角的星钻数。"""
    for it in items:
        text = _compact(it.text)
        if in_roi(it, GEM_ROI) and _GEMS.fullmatch(text):
            return int(text.replace(",", ""))
    return None


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def practice_level_up(items: list[OcrItem]) -> bool:
    """录音室练习领取后的「LEVEL UP」（练习Lv 提升），没有按钮。"""
    return find(items, "LEVELUP") is not None and find(items, "练习Lv") is not None


def home_icon(items: list[OcrItem]) -> bool:
    """左上角的主页按钮。房子图标常被识别成「合」「へ」「^」之类的单个字符。"""
    return any(len(it.text.strip()) == 1 and math.dist(center(it), BTN_HOME) <= 12 for it in items)


def day_tabs(items: list[OcrItem]) -> list[tuple[str, tuple[int, int]]]:
    """限定任务、新手任务右侧的天数分页（从上到下）。"""
    tabs = [
        (it.text.strip(), center(it))
        for it in items
        if in_roi(it, DAY_TAB_ROI) and _DAY_TAB.fullmatch(re.sub(r"\s+", "", it.text))
    ]
    return sorted(tabs, key=lambda t: t[1][1])


def pass_banners(items: list[OcrItem]) -> list[tuple[str, tuple[int, int], bool]]:
    """任务通行证页左侧的通行证（从上到下）：(截止日期, 横幅的点击位置, 是否选中)。"""
    banners = []
    for it in items:
        m = _PASS_DATE.match(_compact(it.text))
        if m and in_roi(it, PASS_LIST_ROI):
            x, y = center(it)
            banners.append((m.group(), (x, y + PASS_BANNER_DY), x >= PASS_SELECTED_X))
    return sorted(banners, key=lambda b: b[1][1])


class DailyMixin:
    """:class:`~ournotes_auto.nav.navigator.GameNavigator` 的日常领取。

    用到它的 ``look`` ``tap`` ``_button`` ``_lit`` ``_patch`` ``_sleep`` ``_fail`` ``_common_step`` ``stop`` 等。
    """

    def run_daily(self, jobs) -> list[str]:
        """按 :data:`DAILY_JOBS` 的顺序做 ``jobs`` 里的项目，返回出错的项目名，最后停在主界面。

        每项开始前都回到主界面；某一项出错时记下来，接着做下一项。回不到主界面、服务器维护则整体报错。
        """
        unknown = set(jobs) - set(DAILY_JOBS)
        if unknown:
            raise ValueError(f"未知的日常项目：{', '.join(sorted(unknown))}")
        failed = []
        for job, name in DAILY_JOBS.items():
            if job not in jobs:
                continue
            self.go_home()
            try:
                getattr(self, f"_daily_{job}")()
            except ServerMaintenance:
                raise
            except NavigationError as e:
                if self.stop.is_set():
                    raise
                logger.error("%s：%s", name, e)
                failed.append(name)
        self.go_home()
        return failed

    # ------------------------------------------------------------ 各项目

    def _daily_studio(self) -> None:
        if self._open_page("录音室练习"):
            self._claim("录音室练习", always_lit=True)

    def _daily_missions(self) -> None:
        if self._open_page("任务"):
            self._claim_tabs("任务", MISSION_TABS)

    def _daily_pass(self) -> None:
        """左侧列表里的通行证逐个选中来领（通行证任务每个通行证各一份）：
        通行证任务（弹窗）领完 pt 再领任务通行证的奖励，pt 可能刚好让通行证升级。"""
        if not self._open_page("任务通行证"):
            return
        count = len(pass_banners(self._items))
        for i in range(max(count, 1)):
            label = "任务通行证" if count <= 1 else f"任务通行证（{i + 1}/{count}）"
            if count > 1:
                self._select_pass(i, count, label)
            self.tap(self._button(self._items, "通行证任务", BTN_PASS_MISSIONS, PASS_MISSIONS_ROI), "通行证任务")
            if not self._wait_page("通行证任务"):
                raise self._fail(f"{label}：没能打开通行证任务")
            self._claim_tabs("通行证任务", PASS_MISSION_TABS, label.replace("任务通行证", "通行证任务"))
            self._leave_page("通行证任务")
            if not self._wait_page("任务通行证"):
                raise self._fail(f"{label}：关闭通行证任务后没有回到任务通行证")
            self._claim("任务通行证", label)

    def _select_pass(self, index: int, count: int, label: str) -> None:
        """在任务通行证页左侧选中第 ``index`` 个通行证（已经选中就不点），等到它放大、按钮状态刷新。"""
        banners = pass_banners(self._items)
        if len(banners) != count:
            raise self._fail(f"任务通行证左侧的通行证从 {count} 个变成了 {len(banners)} 个")
        date, point, selected = banners[index]
        if not selected:
            self.tap(point, label)
            for _ in range(10):
                self._sleep(0.5)
                _, items = self.look()
                if page_title(items) != "任务通行证":
                    raise self._fail(f"点{label}后不在任务通行证页")
                banners = pass_banners(items)
                if len(banners) == count and banners[index][2]:
                    break
            else:
                raise self._fail(f"没能选中{label}")
            self._sleep(1.0)
            self.look()
        logger.info("%s：%s 截止", label, date)

    def _daily_limited(self) -> None:
        if self._open_page("限定任务"):
            self._claim_days("限定任务")

    def _daily_beginner(self) -> None:
        if self._open_page("新手任务"):
            self._claim_days("新手任务")

    def _daily_tgw(self) -> None:
        """商店 → T.G.W CARD 页领每日积分、每日奖励 → 返回商店，在专享商品目录领免费商品。"""
        if not self._open_page("商店"):
            return
        self.tap(self._button(self._items, "T.G.W", BTN_TGW, TGW_ROI), "T.G.W")
        if not self._wait_page("T.G.WCARD"):
            raise self._fail("没能打开 T.G.W CARD")
        for name, text, default, lit_dx in TGW_CLAIMS:
            self._claim_button("T.G.WCARD", text, default, f"T.G.W CARD·{name}", lit_dx=lit_dx)
        self.tap(BTN_BACK, "返回")
        if not self._wait_page("商店"):
            raise self._fail("从 T.G.W CARD 返回后不在商店")
        self.tap(self._button(self._items, "专享商品目录", BTN_CATALOG_TAB, SHOP_TAB_ROI), "专享商品目录")
        bought = self._claim_free()
        logger.info("T.G.W CARD·专享商品目录：%s", f"领了 {bought} 件免费商品" if bought else "没有免费商品")

    def _daily_gifts(self) -> None:
        if self._open_page("礼物盒"):
            self._claim("礼物盒")

    # ------------------------------------------------------------ 页面

    def _open_page(self, title: str) -> bool:
        """在主界面点入口打开 ``title``。没有这个入口（活动结束等）或打开的是别的页面时返回 False。"""
        point = HOME_ENTRIES[title]
        self.tap(point, title)
        return self._wait_page(title, retap=point)

    def _wait_page(self, title: str, timeout_s: float = 15.0, retap: tuple[int, int] | None = None) -> bool:
        """等到 ``title`` 页出现（顺手关掉已知的弹窗），再等一会儿让列表和按钮状态刷新。

        ``retap``：从主界面打开时的入口，点了还停在主界面就再点一次，打开了别的页面就放弃。
        """
        start = tapped = time.monotonic()
        retapped = False
        while time.monotonic() - start < timeout_s:
            self._sleep(0.8)
            screen, items = self.look()
            got = page_title(items)
            if got == title:
                self._sleep(1.0)
                self.look()
                return True
            if retap is None:
                self._dismiss(screen, items)
                continue
            if got is not None:
                logger.warning("点「%s」的入口打开的是「%s」，跳过", title, got)
                return False
            if self._dismiss(screen, items):
                continue
            if screen is Screen.HOME and time.monotonic() - tapped > 4:
                if retapped:
                    break
                self.tap(retap, title)
                tapped = time.monotonic()
                retapped = True
        if retap is not None:
            logger.warning("没能打开「%s」（主界面上可能没有这个入口），跳过", title)
        return False

    def _leave_page(self, title: str) -> None:
        """关掉弹窗式页面 / 点主页按钮，等到标题变了（不连点：淡出时再点一次会点到下面的东西）。"""
        if title in POPUP_PAGES:
            self.tap(self._button(self._items, "关闭", BTN_PAGE_CLOSE, PAGE_CLOSE_ROI), "关闭")
        else:
            self.tap(BTN_HOME, "主页")
        for _ in range(10):
            self._sleep(0.5)
            _, items = self.look()
            if page_title(items) != title:
                return

    def go_home(self, timeout_s: float = 60.0) -> None:
        """回到主界面：关掉已知弹窗、日常页面，其他页面点左上角主页按钮，结算等画面按通用流程往下走。"""
        deadline = time.monotonic() + timeout_s
        while True:
            screen, items = self.look()
            if screen is Screen.HOME:
                return
            if screen in RELOGIN_SCREENS:
                deadline = time.monotonic() + timeout_s
            if time.monotonic() > deadline:
                raise self._fail(f"{timeout_s:.0f}s 内没能回到主界面")
            if self._dismiss(screen, items):
                continue
            if gift_confirm(items) is not None:
                # 上次停在了一键领取礼物的确认框（比如中途停止）：取消，领取礼物时会重新点
                self.tap(self._button(items, "取消", BTN_GIFT_CANCEL), "取消")
                self._sleep(1.0)
                continue
            title = page_title(items)
            if title is not None:
                self._leave_page(title)
            elif screen is Screen.SONG_SELECT and filter_open(items):
                self._close_filter()
            elif screen in HOME_BUTTON_SCREENS or (screen is Screen.UNKNOWN and home_icon(items)):
                self.tap(BTN_HOME, "主页")
                self._sleep(1.5)
            elif screen is not Screen.UNKNOWN and self._common_step(screen, items):
                self._sleep(self.settle_s)
            else:
                self._sleep(1.0)

    def _dismiss(self, screen: Screen, items: list[OcrItem]) -> bool:
        """关掉领取后（或打开页面时）弹出的已知弹窗，做了操作返回 True。"""
        ok = reward_ok(items) or reward_close(items)
        if ok is not None:
            self.tap(center(ok), ok.text.strip())
        elif practice_level_up(items):
            self.tap(TAP_LEVEL_UP, "LEVEL UP")
        elif screen in KNOWN_POPUPS:
            self._common_step(screen, items)
        else:
            return False
        self._sleep(1.0)
        return True

    # ------------------------------------------------------------ 领取

    def _claim_tabs(self, title: str, tabs, label: str | None = None) -> None:
        label = label or title
        for tab, default in tabs:
            self.tap(self._button(self._items, tab, default, TAB_ROI), f"{label}·{tab}")
            self._sleep(1.2)
            _, items = self.look()
            if page_title(items) != title:
                raise self._fail(f"切换到「{tab}」分页后不在{title}页")
            self._claim(title, f"{label}·{tab}")

    def _claim_days(self, title: str) -> None:
        tabs = day_tabs(self._items)
        if not tabs:
            self._claim(title)
            return
        for tab, point in tabs:
            self.tap(point, f"{title}·{tab}")
            if not self._wait_page(title, timeout_s=5.0):
                raise self._fail(f"点「{tab}」后不在{title}页")
            self._claim(title, f"{title}·{tab}")

    def _claim(self, title: str, label: str | None = None, always_lit: bool = False) -> bool:
        """一键领取亮着就点，处理完领取后的弹窗、回到 ``title`` 页。领了返回 True。"""
        return self._claim_button(title, "一键领取", CLAIM_BUTTONS[title], label or title, always_lit)

    def _claim_button(
        self,
        title: str,
        text: str,
        default: tuple[int, int],
        label: str,
        always_lit: bool = False,
        lit_dx: int = CLAIM_LIT_DX,
    ) -> bool:
        """同 :meth:`_claim`，按钮文字是 ``text``、默认在 ``default``，在文字左边 ``-lit_dx`` 处看亮不亮。"""
        x, y = default
        s = CLAIM_SEARCH
        it = find(self._items, text, (x - s, y - s, 2 * s, 2 * s), exact=True)
        point = center(it) if it is not None else (x, y)
        if not always_lit and not self._lit((point[0] + lit_dx, point[1])):
            logger.info("%s：没有可领取的奖励", label)
            return False
        self.tap(point, text)
        popups = self._settle_claim(title)
        if popups is None:
            logger.info("%s：没有可领取的奖励", label)
            return False
        logger.info("%s：已领取%s", label, f"（{popups} 个弹窗）" if popups else "")
        return True

    def _settle_claim(self, title: str, timeout_s: float = 60.0) -> int | None:
        """点了一键领取之后：逐个关掉获得奖励等弹窗，直到回到 ``title`` 页、``CLAIM_QUIET_S`` 内没有新弹窗。

        返回关掉的弹窗数；提示「没有可领取的奖励」时返回 None。
        """
        deadline = time.monotonic() + timeout_s
        quiet_since = None
        popups = 0
        while time.monotonic() < deadline:
            self._sleep(0.8)
            screen, items = self.look()
            confirm = gift_confirm(items) if title == "礼物盒" else None
            if confirm is not None:
                self.tap(center(confirm), "OK（一键领取礼物）")
                self._sleep(1.0)
                quiet_since = None
                continue
            if self._dismiss(screen, items):
                popups += 1
                quiet_since = None
                continue
            if find(items, "没有可领取"):
                return None if not popups else popups
            if page_title(items) == title and not has_confirm(items):
                now = time.monotonic()
                if quiet_since is None:
                    quiet_since = now
                elif now - quiet_since >= CLAIM_QUIET_S:
                    return popups
                continue
            quiet_since = None
            if screen is Screen.HOME or screen in RELOGIN_SCREENS:
                raise self._fail(f"领取奖励时离开了{title}页")
            closer = find(items, "关闭", BOTTOM_ROI, exact=True) or find(items, "取消", BOTTOM_ROI, exact=True)
            if closer is not None:
                self.tap(center(closer), closer.text.strip())
                self._sleep(1.0)
                popups += 1
            elif has_confirm(items):
                raise self._fail("领取奖励后出现认不出的弹窗（没有点）")
        raise self._fail(f"领取奖励后 {timeout_s:.0f}s 没有回到{title}页")

    # ------------------------------------------------------------ 商店的免费商品

    def _claim_free(self) -> int:
        """在商店的 T.G.W CARD 专享商品目录逐件领价格是「免费」的商品，返回领了几件。"""
        bought = 0
        buy, gems = self._next_free()
        while buy is not None:
            if bought >= SHOP_FREE_MAX:
                raise self._fail(f"领了 {bought} 件免费商品后还有，停下")
            self.tap(buy, "购买（免费）")
            self._settle_purchase()
            bought += 1
            buy, after = self._next_free()
            if after != gems:
                raise self._fail(f"领免费商品后星钻数变了（{gems} → {after}）")
        return bought

    def _next_free(self, timeout_s: float = 15.0) -> tuple[tuple[int, int] | None, int]:
        """等商店停在专享商品目录、连续两帧认出的免费商品和星钻数都一样，
        返回第一件免费商品的「购买」按钮（没有为 None）和星钻数。"""
        start = time.monotonic()
        prev = None
        while time.monotonic() - start < timeout_s:
            self._sleep(1.0)
            items = self._catalog_items()
            cur = None if items is None else (free_buys(items), gem_balance(items))
            if cur is not None and cur[1] is not None and prev is not None and cur[1] == prev[1]:
                buys, last = cur[0], prev[0]
                if len(buys) == len(last) and all(math.dist(a, b) <= 8 for a, b in zip(buys, last)):
                    return (buys[0] if buys else None), cur[1]
            prev = cur
        raise self._fail("没能停在商店的 T.G.W CARD 专享商品目录")

    def _catalog_items(self) -> list[OcrItem] | None:
        """截一帧，停在商店的专享商品目录分页、没有弹窗时返回识别结果，否则返回 None。"""
        _, items = self.look()
        tab = find(items, "专享商品目录", SHOP_TAB_ROI, exact=True)
        if page_title(items) != "商店" or has_confirm(items) or tab is None:
            return None
        _, g, r = (float(v) for v in self._patch((SHOP_TAB_SAMPLE_X, center(tab)[1]), 3).reshape(-1, 3).mean(axis=0))
        return items if g > 130 and g - r > 50 else None

    def _settle_purchase(self, timeout_s: float = 20.0) -> None:
        """点了免费商品的「购买」之后：「购买完成」上点一次 OK，等回到商店。

        OK 正下方是第二排商品的「购买」，弹窗淡出时不能再点 OK。认不出的弹窗有「取消」就点取消，然后报错。
        """
        start = time.monotonic()
        tapped_at = None
        while time.monotonic() - start < timeout_s:
            self._sleep(0.8)
            _, items = self.look()
            ok = purchase_ok(items)
            if ok is not None:
                if tapped_at is None or time.monotonic() - tapped_at > PURCHASE_RETAP_S:
                    self.tap(center(ok), "OK（购买完成）")
                    tapped_at = time.monotonic()
                continue
            if page_title(items) == "商店" and not has_confirm(items):
                if tapped_at is not None:
                    return
                if time.monotonic() - start > PURCHASE_WAIT_S:
                    raise self._fail("点了免费商品的「购买」没有反应")
                continue
            cancel = find(items, "取消", exact=True)
            if cancel is not None:
                self.tap(center(cancel), "取消")
                self._sleep(1.0)
                raise self._fail("点「购买」后弹出了认不出的确认框（点了取消，没有买）")
            if has_confirm(items):
                raise self._fail("点「购买」后弹出了认不出的弹窗（没有点）")
        raise self._fail(f"点「购买」后 {timeout_s:.0f}s 没有回到商店")
