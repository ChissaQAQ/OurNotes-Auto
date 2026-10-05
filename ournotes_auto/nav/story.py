"""看故事：把没看过的乐队故事、视角故事、羁绊故事逐话跳过（解锁乐曲，领取看完的奖励：星钻、乐曲交换券、成员道具等）。

画面顺序（坐标均为 1280x720 设计尺寸）::

    主界面 -[故事]-> 故事菜单（叠在主界面上：乐队故事 / 羁绊故事）-[乐队故事]-> 乐队故事章节选择
    -[左侧乐队分页 → 确定]-> 乐队故事话数选择（自动选中第一话没看过的）-[观看故事]->
    要下载语音数据，并观看故事吗？ -[无语音]-> NOW LOADING → 播放 -[右上角菜单 → SKIP]->
    要跳过故事吗？ -[跳过]-> 领取奖励 -[OK]->（有的话之后是「乐曲解锁」-[关闭]->）话数选择（下一话解锁并选中）

    话数选择右上角切到「视角故事」分页：底部是各成员的「视角Ver.」卡片，选中后同样点观看故事

    故事菜单 -[羁绊故事]-> 羁绊故事（左侧乐队分页，右边五个成员）-[成员]-> 羁绊故事选择（和其他成员的组合）
    -[组合]-> 羁绊故事弹窗（各话一行，锁着的有锁）-[那一话]-> 要下载语音数据… → 播放 → … → 领取奖励 -[OK]-> 弹窗

- 没看过的故事在菜单按钮、分页、章节 / 成员 / 组合、话的右上角有红点，只进有红点的。锁着的（视角故事要达成
  解锁条件，羁绊故事按羁绊等级解锁）没有红点。
- 每一话第一次看要下载数据（无语音约 30MB），网络慢时要等一会儿。
- 只点上面这些按钮；认不出的确认框不点、报错停下。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Callable

import numpy as np

from ..result_reader import OcrItem
from .daily import BOTTOM_ROI, has_confirm
from .screens import CLOSE_ROI, PLAYER_MENU_ROI, TITLE_ROI, Rect, Screen, center, find, in_roi

logger = logging.getLogger(__name__)

BTN_HOME_STORY = (805, 625)  # 主界面：故事（打开菜单后再点一次收起）
BTN_BAND_STORY = (700, 440)  # 故事菜单：乐队故事
BAND_STORY_BADGE = (769, 395)
BTN_BOND_STORY = (900, 440)  # 故事菜单：羁绊故事
BOND_STORY_BADGE = (967, 395)
# 菜单按钮下方的文字「乐队故事」「羁绊故事」。有「特别故事」时是三个按钮，整排左移，所以按文字的位置点按钮、看红点
STORY_MENU_ROI: Rect = (450, 480, 620, 50)
STORY_MENU_BUTTON_OFFSET = (-5, -66)  # 按钮中心相对文字中心
STORY_MENU_BADGE_OFFSET = (64, -111)  # 红点相对文字中心
CHAPTERS_TITLE = "乐队故事章节选择"
EPISODES_TITLE = "乐队故事话数选择"
BOND_MEMBERS_TITLE = "羁绊故事"
BOND_PAIRS_TITLE = "羁绊故事选择"
BTN_STORY_BACK = (58, 38)  # 左上角返回上一页（右边是主页按钮）
BTN_HELP_CLOSE = (497, 650)  # 第一次打开时的说明：关闭 / 下一步

# 章节选择：左侧 ALL 下面的乐队分页。点了只列出这个乐队的章节，选中的在中间
BAND_TABS = (
    ("MyGO!!!!!", (100, 212)),
    ("Ave Mujica", (100, 293)),
    ("梦限大MewType", (100, 372)),
    ("millsage", (100, 452)),
    ("一家Dumb Rock!", (100, 532)),
)
BAND_TAB_BADGE_OFFSET = (76, -33)
CHAPTER_BADGE = (469, 309)  # 中间（选中）章节的红点
BTN_CHAPTER_OK = (1143, 667)
BTN_EPISODE_TAB = (765, 40)  # 话数选择：「乐队故事」分页（右边是视角故事）
BTN_POV_TAB = (1027, 40)
POV_TAB_BADGE = (1141, 16)

# 话数选择：左侧简介里是选中那话的「第N话」，底部列表每张卡片左下角也有「第N话」，红点在卡片右上角。
# 红点离文字中心横向 185~210：OCR 的框有时偏宽（选中的卡片四角有高亮框），所以在一段范围里找。
# 左右相邻卡片的红点离得更远（约 -70、+460），不会认错
EPISODE_NAME_ROI: Rect = (50, 300, 250, 50)
EPISODE_STRIP_ROI: Rect = (20, 630, 950, 45)
EPISODE_BADGE_DX = range(150, 244, 4)
EPISODE_BADGE_DY = -67
EPISODE_CARD_OFFSET = (90, -30)  # 从文字点到卡片缩略图上
_EPISODE = re.compile(r"第\d+话")
BTN_WATCH = (1127, 652)
BTN_NO_VOICE = (642, 663)  # 下载确认：取消 / 无语音 / 有语音
DIALOG_BUTTONS_ROI: Rect = (200, 630, 880, 70)
SKIP_CONFIRM_ROI: Rect = (640, 500, 260, 70)  # 要跳过故事吗？ 取消 / 跳过
BTN_SKIP_CONFIRM = (756, 534)

# 视角故事分页：卡片上是成员名（居中），下面一行「视角Ver.」（「视」常读成「见」「现」或漏掉），
# 红点在名字右上方约 (+80, -23)。左侧简介里是「<成员>视角Ver.」。一个章节 5 张卡片放不下，后面的要往左拖
POV_NAME_ROI: Rect = (0, 596, 980, 22)
POV_VER_ROI: Rect = (0, 616, 980, 24)
POV_BADGE_DX = range(64, 100, 4)
POV_BADGE_DY = -23
POV_CARD_DY = 12  # 点名字下面一点（卡片中间）
POV_DRAG = ((850, 640), (250, 640))
_POV = re.compile(r"(.*?)[视见现]?角Ver\.?")

# 羁绊故事：左侧乐队分页，右边成员卡片（高低错开），卡片底部是中文名，红点在名字上方约 (+90, -482)
BOND_TABS = (
    ("MyGO!!!!!", (100, 129)),
    ("Ave Mujica", (100, 213)),
    ("梦限大MewType", (100, 294)),
    ("millsage", (100, 373)),
    ("一家Dumb Rock!", (100, 452)),
)
BOND_TAB_BADGE_OFFSET = (79, -28)
MEMBER_NAME_ROI: Rect = (200, 565, 1080, 60)
MEMBER_BADGE_DX = range(80, 101, 4)
MEMBER_BADGE_DY = -482
MEMBER_CARD_Y = 400
_NAME = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]{2,}")  # 假名、汉字
# 羁绊故事选择：右边一行一个组合「【灯&爱音】」，红点在行的右上角
PAIR_ROI: Rect = (820, 140, 360, 460)
PAIR_BADGE_X = range(1172, 1189, 4)
PAIR_BADGE_DY = -43
# 组合的弹窗：标题在上方中间，各话一行（左边「第N话」），红点在行的右上角，底部「关闭」
BOND_POPUP_TITLE_ROI: Rect = (500, 15, 280, 50)
BOND_EPISODE_ROI: Rect = (380, 100, 120, 480)
BOND_EPISODE_BADGE_X = range(901, 918, 4)
BOND_EPISODE_BADGE_DY = -23
BOND_EPISODE_TAP_OFFSET = (137, 22)  # 从「第N话」点到这一行中间
BTN_BOND_CLOSE = (638, 662)

# 播放中右上角的菜单按钮：蓝色圆底上三道白线（整页几乎没有字，只能看颜色）。点开后是一列按钮，SKIP 在最上面
BTN_PLAYER_MENU = (1201, 101)
PLAYER_MENU_RING = ((1182, 101), (1220, 101), (1201, 122))
BTN_SKIP = (1201, 168)
PLAYER_MENU_GAP_S = 3.0  # 菜单展开要一点时间，点了没反应隔这么久再点

EPISODE_TIMEOUT_S = 180.0  # 一话从点观看到回到话数选择（含下载）
MAX_EPISODES = 60  # 一个章节 / 组合最多看这么多话（防止认错了一直循环）
BADGE_RADIUS = 10


def story_title(items: list[OcrItem]) -> str | None:
    for title in (CHAPTERS_TITLE, EPISODES_TITLE, BOND_MEMBERS_TITLE, BOND_PAIRS_TITLE):
        if find(items, title, TITLE_ROI, exact=True):
            return title
    return None


def story_menu_open(items: list[OcrItem]) -> bool:
    """主界面上展开了故事菜单（乐队故事 / 羁绊故事）。"""
    return find(items, "乐队故事", STORY_MENU_ROI) is not None and find(items, "羁绊故事", STORY_MENU_ROI) is not None


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def selected_episode(items: list[OcrItem]) -> str | None:
    """话数选择页上选中的是第几话（左侧简介里的「第N话」）。"""
    for it in items:
        if in_roi(it, EPISODE_NAME_ROI) and _EPISODE.fullmatch(_compact(it.text)):
            return _compact(it.text)
    return None


def episode_cards(items: list[OcrItem]) -> list[tuple[str, tuple[int, int]]]:
    """底部话数列表里认得出的卡片（「第N话」和它的位置），从左到右。"""
    cards = [
        (_compact(it.text), center(it))
        for it in items
        if in_roi(it, EPISODE_STRIP_ROI) and _EPISODE.fullmatch(_compact(it.text))
    ]
    return sorted(cards, key=lambda c: c[1][0])


def selected_pov(items: list[OcrItem]) -> str | None:
    """视角故事分页上选中的是谁的视角（左侧简介里的「<成员>视角Ver.」）。"""
    for it in items:
        m = _POV.fullmatch(_compact(it.text))
        if in_roi(it, EPISODE_NAME_ROI) and m and m.group(1):
            return m.group(1)
    return None


def pov_cards(items: list[OcrItem]) -> list[tuple[str, tuple[int, int]]]:
    """视角故事列表里认得出的卡片（成员名和名字的位置，下面一行要有「…Ver.」），从左到右。"""
    vers = [center(it) for it in items if in_roi(it, POV_VER_ROI) and "Ver" in it.text]
    cards = []
    for it in items:
        if not in_roi(it, POV_NAME_ROI):
            continue
        x, y = center(it)
        if any(abs(vx - x) <= 25 and 10 <= vy - y <= 30 for vx, vy in vers):
            cards.append((_compact(it.text), (x, y)))
    return sorted(cards, key=lambda c: c[1][0])


def _same_name(a: str | None, b: str) -> bool:
    return bool(a) and (a in b or b in a)


def member_names(items: list[OcrItem]) -> list[tuple[str, tuple[int, int]]]:
    """羁绊故事页上成员卡片底部的中文名和位置，从左到右（上面一行的罗马字不算）。"""
    names = [(_compact(it.text), center(it)) for it in items if in_roi(it, MEMBER_NAME_ROI) and _NAME.search(it.text)]
    return sorted(names, key=lambda n: n[1][0])


def bond_pairs(items: list[OcrItem]) -> list[tuple[str, tuple[int, int]]]:
    """羁绊故事选择页上的组合（「灯&爱音」和位置），从上到下。"""
    pairs = [
        (_compact(it.text).strip("【】[]"), center(it))
        for it in items
        if in_roi(it, PAIR_ROI) and "&" in it.text and len(_compact(it.text)) >= 3
    ]
    return sorted(pairs, key=lambda p: p[1][1])


def bond_popup(items: list[OcrItem]) -> bool:
    """组合的羁绊故事弹窗（标题在上方中间，底部「关闭」）。和其他弹窗一样会被认成 Screen.POPUP，不能顺手关掉。"""
    return (
        find(items, BOND_MEMBERS_TITLE, BOND_POPUP_TITLE_ROI, exact=True) is not None
        and find(items, "关闭", CLOSE_ROI, exact=True) is not None
    )


def bond_episode_rows(items: list[OcrItem]) -> list[tuple[str, tuple[int, int]]]:
    """羁绊故事弹窗里的各话（「第N话」和位置），从上到下。"""
    rows = [
        (_compact(it.text), center(it))
        for it in items
        if in_roi(it, BOND_EPISODE_ROI) and _EPISODE.fullmatch(_compact(it.text))
    ]
    return sorted(rows, key=lambda r: r[1][1])


def _offset(point: tuple[int, int], offset: tuple[int, int]) -> tuple[int, int]:
    return point[0] + offset[0], point[1] + offset[1]


def _episodes_page(items: list[OcrItem]) -> bool:
    return story_title(items) == EPISODES_TITLE and not find(items, "要下载")


def _bond_popup_back(items: list[OcrItem]) -> bool:
    return bond_popup(items) and not find(items, "要下载")


def _band_key(items: list[OcrItem]):
    return selected_episode(items), episode_cards(items)


def _pov_key(items: list[OcrItem]):
    return selected_pov(items), pov_cards(items)


class StoryMixin:
    """:class:`~ournotes_auto.nav.navigator.GameNavigator` 的看故事（领取日常的 ``story`` 项）。

    用到它的 ``look`` ``tap`` ``_drag`` ``_button`` ``_sleep`` ``_fail`` ``_dismiss`` ``go_home`` 等。
    """

    def _daily_story(self) -> None:
        if not self._open_story_menu():
            return
        band = self._badge(self._menu_point("乐队故事", BAND_STORY_BADGE, STORY_MENU_BADGE_OFFSET))
        bond = self._badge(self._menu_point("羁绊故事", BOND_STORY_BADGE, STORY_MENU_BADGE_OFFSET))
        if not (band or bond):
            logger.info("故事：没有没看过的")
            self._close_story_menu()
            return
        if band:
            self._band_stories()
        else:
            logger.info("乐队故事：没有没看过的")
        if not bond:
            logger.info("羁绊故事：没有没看过的")
            return
        if band:
            self.go_home()
            if not self._open_story_menu():
                return
        self._bond_stories()

    def _band_stories(self) -> None:
        """从故事菜单进乐队故事，把有红点的乐队分页里选中章节的乐队故事、视角故事看完，停在章节选择页。"""
        self._enter_story(BTN_BAND_STORY, "乐队故事", CHAPTERS_TITLE)
        watched = 0
        for band, tab in BAND_TABS:
            badge = _offset(tab, BAND_TAB_BADGE_OFFSET)
            if not self._badge(badge):
                continue
            self.tap(tab, band)
            self._sleep(1.2)
            self.look()
            if story_title(self._items) != CHAPTERS_TITLE:
                raise self._fail(f"切换到「{band}」后不在章节选择页")
            if not self._badge(CHAPTER_BADGE):
                logger.info("乐队故事·%s：分页有红点，但选中的章节没有", band)
                continue
            self.tap(self._button(self._items, "确定", BTN_CHAPTER_OK), "确定")
            if not self._wait_story(EPISODES_TITLE):
                raise self._fail(f"没能打开「{band}」的章节")
            watched += self._story_episodes(band)
            if self._badge(POV_TAB_BADGE):
                watched += self._pov_episodes(band)
            self.tap(BTN_STORY_BACK, "返回")
            if not self._wait_story(CHAPTERS_TITLE):
                raise self._fail("没能回到章节选择")
            if self._badge(badge):
                logger.info("乐队故事·%s：别的章节还有没看的（只看选中的章节）", band)
        logger.info("乐队故事：看了 %d 话", watched)

    def _bond_stories(self) -> None:
        """从故事菜单进羁绊故事，把有红点的乐队分页 → 成员 → 组合里没看过的话看完。"""
        self._enter_story(BTN_BOND_STORY, "羁绊故事", BOND_MEMBERS_TITLE)
        watched = 0
        for band, tab in BOND_TABS:
            if not self._badge(_offset(tab, BOND_TAB_BADGE_OFFSET)):
                continue
            self.tap(tab, band)
            if not self._wait_story(BOND_MEMBERS_TITLE):
                raise self._fail(f"切换到「{band}」后不在羁绊故事页")
            watched += self._bond_members(band)
        logger.info("羁绊故事：看了 %d 话", watched)

    # ------------------------------------------------------------ 页面

    def _open_story_menu(self) -> bool:
        """在主界面点「故事」展开菜单。刚回到主界面时（还在加载）点了可能没反应，隔一会儿再点一次。"""
        self.tap(BTN_HOME_STORY, "故事")
        tapped = time.monotonic()
        retapped = False
        deadline = tapped + 15.0
        while time.monotonic() < deadline:
            self._sleep(0.8)
            screen, items = self.look()
            if story_menu_open(items):
                self._sleep(0.8)
                self.look()
                return True
            if screen is Screen.HOME and time.monotonic() - tapped > 4 and not retapped:
                self.tap(BTN_HOME_STORY, "故事")
                tapped = time.monotonic()
                retapped = True
        logger.warning("没能打开故事菜单，跳过")
        return False

    def _close_story_menu(self) -> None:
        self.tap(BTN_HOME_STORY, "收起故事菜单")
        self._sleep(1.0)

    def _menu_point(self, name: str, default: tuple[int, int], offset: tuple[int, int]) -> tuple[int, int]:
        """故事菜单上 ``name`` 的按钮 / 红点（按最近一帧上文字的位置，没读到时用 ``default``）。"""
        it = find(self._items, name, STORY_MENU_ROI)
        return default if it is None else _offset(center(it), offset)

    def _enter_story(self, button: tuple[int, int], name: str, title: str) -> None:
        self.tap(self._menu_point(name, button, STORY_MENU_BUTTON_OFFSET), name)
        if not self._wait_story(title):
            error = self._fail(f"没能打开{name}")
            if story_menu_open(self._items):
                self._close_story_menu()  # 菜单叠在主界面上，回主界面时认不出来
            raise error

    def _wait_story(self, title: str, timeout_s: float = 20.0) -> bool:
        """等到 ``title`` 页出现，再等一会儿让列表和红点刷新。顺手关掉第一次进来时的说明和已知弹窗。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._sleep(0.8)
            screen, items = self.look()
            if find(items, "前往帮助"):
                # 第一次打开时的说明（来观看故事 / 观看特定的乐队故事就能解锁乐曲！），只点「关闭」
                self.tap(self._button(items, "关闭", BTN_HELP_CLOSE, BOTTOM_ROI), "关闭说明")
            elif self._dismiss(screen, items):
                continue
            elif story_title(items) == title:
                self._sleep(1.0)
                self.look()
                return True
        return False

    def _badge(self, point: tuple[int, int]) -> bool:
        """最近一帧上 ``point``（设计坐标）附近有没有红点（没看过的标记）。"""
        patch = self._patch(point, BADGE_RADIUS).astype(int)
        b, g, r = patch[..., 0], patch[..., 1], patch[..., 2]
        return float(((r > 200) & (g < 120) & (b < 120)).mean()) > 0.3

    def _badge_near(self, xs, y: int) -> bool:
        return any(self._badge((x, y)) for x in xs)

    def _episode_unread(self, label: tuple[int, int]) -> bool:
        """话数列表里文字在 ``label`` 的那张卡片右上角有红点。"""
        x, y = label
        return self._badge_near((x + dx for dx in EPISODE_BADGE_DX), y + EPISODE_BADGE_DY)

    def _patch(self, point: tuple[int, int], radius: int) -> np.ndarray:
        h, w = self._frame.shape[:2]
        x, y = round(point[0] * w / 1280), round(point[1] * h / 720)
        r = max(1, round(radius * w / 1280))
        return self._frame[max(0, y - r) : y + r + 1, max(0, x - r) : x + r + 1]

    def _player_menu_button(self) -> bool:
        """最近一帧是故事播放画面（右上角有菜单按钮）。"""
        for point in PLAYER_MENU_RING:
            b, g, r = (float(v) for v in self._patch(point, 2).reshape(-1, 3).mean(axis=0))
            if not (b > 120 and r < 110 and b - r > 50):
                return False
        return float(self._patch(BTN_PLAYER_MENU, 1).mean()) > 220

    # ------------------------------------------------------------ 乐队故事 / 视角故事

    def _story_episodes(self, band: str) -> int:
        """在话数选择页把列表里有红点的话从左到右逐话看完，返回看了几话。"""
        self.tap(BTN_EPISODE_TAB, "乐队故事分页")
        self._settle_episodes()
        watched: list[str] = []
        for _ in range(MAX_EPISODES):
            unread = [c for c in episode_cards(self._items) if self._episode_unread(c[1])]
            if not unread:
                break
            name, pos = unread[0]
            if name in watched:
                raise self._fail(f"{band}·{name}跳过后还是没看过")
            if selected_episode(self._items) != name:
                self.tap(_offset(pos, EPISODE_CARD_OFFSET), name)
                self._settle_episodes()
                if selected_episode(self._items) != name:
                    raise self._fail(f"没能选中{band}·{name}")
            start = self._button(self._items, "观看故事", BTN_WATCH)
            popups = self._watch_episode(f"{band}·{name}", start, _episodes_page)
            logger.info("乐队故事·%s·%s：已跳过%s", band, name, f"，关掉 {popups} 个奖励 / 解锁弹窗" if popups else "")
            watched.append(name)
            self._settle_episodes()
        else:
            logger.warning("乐队故事·%s：看了 %d 话还没看完，下次再看", band, len(watched))
        return len(watched)

    def _pov_episodes(self, band: str) -> int:
        """切到视角故事分页，把有红点的卡片逐个看完，返回看了几话。看得到的都没红点、分页上还有时往左拖一次。"""
        self.tap(BTN_POV_TAB, "视角故事分页")
        self._settle_episodes(_pov_key)
        watched: list[str] = []
        dragged = False
        for _ in range(MAX_EPISODES):
            unread = [c for c in pov_cards(self._items) if self._pov_unread(c[1])]
            if not unread:
                if dragged or not self._badge(POV_TAB_BADGE):
                    break
                self._drag(*POV_DRAG)
                dragged = True
                self._settle_episodes(_pov_key)
                continue
            name, pos = unread[0]
            if name in watched:
                raise self._fail(f"{band}·{name}视角跳过后还是没看过")
            if not _same_name(selected_pov(self._items), name):
                self.tap((pos[0], pos[1] + POV_CARD_DY), f"{name}视角")
                self._settle_episodes(_pov_key)
                if not _same_name(selected_pov(self._items), name):
                    raise self._fail(f"没能选中{band}·{name}视角")
            watch = find(self._items, "观看故事", BOTTOM_ROI, exact=True)
            if watch is None:
                raise self._fail(f"{band}·{name}视角有红点，但没有「观看故事」按钮")
            popups = self._watch_episode(f"{band}·{name}视角", center(watch), _episodes_page)
            logger.info("视角故事·%s·%s：已跳过%s", band, name, f"，关掉 {popups} 个奖励 / 解锁弹窗" if popups else "")
            watched.append(name)
            self.tap(BTN_POV_TAB, "视角故事分页")  # 看完回来可能回到乐队故事分页
            self._settle_episodes(_pov_key)
            dragged = False
        else:
            logger.warning("视角故事·%s：看了 %d 话还没看完，下次再看", band, len(watched))
        if self._badge(POV_TAB_BADGE):
            logger.info("视角故事·%s：分页上还有红点，但列表里找不到没看过的", band)
        return len(watched)

    def _pov_unread(self, name_pos: tuple[int, int]) -> bool:
        x, y = name_pos
        return self._badge_near((x + dx for dx in POV_BADGE_DX), y + POV_BADGE_DY)

    def _settle_episodes(self, key: Callable[[list[OcrItem]], object] = _band_key) -> None:
        """等话数列表滚动停下：连续两帧 ``key``（选中的和卡片位置）一样。"""
        prev = None
        for _ in range(8):
            self._sleep(0.8)
            self.look()
            if story_title(self._items) != EPISODES_TITLE:
                prev = None
                continue
            now = key(self._items)
            if now == prev:
                return
            prev = now
        if story_title(self._items) != EPISODES_TITLE:
            raise self._fail("不在话数选择页")

    # ------------------------------------------------------------ 羁绊故事

    def _bond_members(self, band: str) -> int:
        """羁绊故事页上逐个点有红点的成员，看完他们的组合，返回看了几话。"""
        done: list[int] = []  # 进过的成员卡片的 x（名字 OCR 可能每次不一样）
        watched = 0
        for _ in range(MAX_EPISODES):
            todo = [
                (name, pos)
                for name, pos in member_names(self._items)
                if not any(abs(pos[0] - x) < 40 for x in done) and self._member_unread(pos)
            ]
            if not todo:
                break
            name, pos = todo[0]
            done.append(pos[0])
            self.tap((pos[0], MEMBER_CARD_Y), name)
            if not self._wait_story(BOND_PAIRS_TITLE):
                raise self._fail(f"没能打开{band}·{name}的羁绊故事")
            watched += self._bond_pairs()
            self.tap(BTN_STORY_BACK, "返回")
            if not self._wait_story(BOND_MEMBERS_TITLE):
                raise self._fail("没能回到羁绊故事页")
        return watched

    def _member_unread(self, name_pos: tuple[int, int]) -> bool:
        x, y = name_pos
        return self._badge_near((x + dx for dx in MEMBER_BADGE_DX), y + MEMBER_BADGE_DY)

    def _bond_pairs(self) -> int:
        """羁绊故事选择页上逐个打开有红点的组合，看完里面没看过的话，返回看了几话。"""
        done: list[int] = []  # 打开过的行的 y
        watched = 0
        for _ in range(MAX_EPISODES):
            todo = [
                (pair, pos)
                for pair, pos in bond_pairs(self._items)
                if not any(abs(pos[1] - y) < 30 for y in done)
                and self._badge_near(PAIR_BADGE_X, pos[1] + PAIR_BADGE_DY)
            ]
            if not todo:
                break
            pair, pos = todo[0]
            done.append(pos[1])
            self.tap(pos, pair)
            if not self._wait_bond_popup():
                raise self._fail(f"没能打开「{pair}」的羁绊故事")
            watched += self._bond_episodes(pair)
            self._close_bond_popup()
        return watched

    def _bond_episodes(self, pair: str) -> int:
        """羁绊故事弹窗里把有红点的话从上到下逐话看完，返回看了几话。"""
        watched: list[str] = []
        for _ in range(MAX_EPISODES):
            unread = [
                r
                for r in bond_episode_rows(self._items)
                if self._badge_near(BOND_EPISODE_BADGE_X, r[1][1] + BOND_EPISODE_BADGE_DY)
            ]
            if not unread:
                break
            name, pos = unread[0]
            if name in watched:
                raise self._fail(f"{pair}·{name}跳过后还是没看过")
            popups = self._watch_episode(f"{pair}·{name}", _offset(pos, BOND_EPISODE_TAP_OFFSET), _bond_popup_back)
            logger.info("羁绊故事·%s·%s：已跳过%s", pair, name, f"，关掉 {popups} 个奖励 / 解锁弹窗" if popups else "")
            watched.append(name)
            self._sleep(1.0)  # 等红点刷新
            self.look()
        return len(watched)

    def _wait_bond_popup(self, timeout_s: float = 20.0) -> bool:
        """等组合的羁绊故事弹窗出现（它会被认成普通弹窗，不能用 _wait_story：会被当成已知弹窗关掉）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._sleep(0.8)
            self.look()
            if bond_popup(self._items):
                self._sleep(1.0)
                self.look()
                return True
        return False

    def _close_bond_popup(self) -> None:
        """关掉组合的弹窗，等它淡出（淡出时再点「关闭」会点到下面的列表）再等羁绊故事选择页。"""
        self.tap(self._button(self._items, "关闭", BTN_BOND_CLOSE, CLOSE_ROI), "关闭")
        for _ in range(10):
            self._sleep(0.5)
            self.look()
            if not bond_popup(self._items):
                break
        if not self._wait_story(BOND_PAIRS_TITLE):
            raise self._fail("没能回到羁绊故事选择")

    # ------------------------------------------------------------ 看

    def _watch_episode(self, label: str, start: tuple[int, int], done: Callable[[list[OcrItem]], bool]) -> int:
        """点 ``start``（「观看故事」或羁绊故事那一话）、无语音、打开菜单 SKIP、确认跳过、关掉领取奖励（和乐曲解锁）弹窗，
        直到回到 ``done`` 认得出的页面。返回关掉了几个弹窗。"""
        self.tap(start, f"观看·{label}")
        deadline = time.monotonic() + EPISODE_TIMEOUT_S
        skipped = False
        popups = 0
        menu_at = -PLAYER_MENU_GAP_S
        while time.monotonic() < deadline:
            self._sleep(1.0)
            screen, items = self.look()
            if done(items):
                if skipped:
                    return popups
                continue
            if find(items, "要下载语音数据"):
                self.tap(self._button(items, "无语音", BTN_NO_VOICE, DIALOG_BUTTONS_ROI), "无语音")
            elif find(items, "要跳过故事吗"):
                self.tap(self._button(items, "跳过", BTN_SKIP_CONFIRM, SKIP_CONFIRM_ROI), "跳过")
                skipped = True
            elif find(items, "SKIP", PLAYER_MENU_ROI, exact=True):
                self.tap(BTN_SKIP, "SKIP")
            elif self._dismiss(screen, items):
                popups += 1
                skipped = True
            elif find(items, "NOW LOADING"):
                pass
            elif self._player_menu_button():
                now = time.monotonic()
                if now - menu_at >= PLAYER_MENU_GAP_S:
                    self.tap(BTN_PLAYER_MENU, "故事菜单")
                    menu_at = now
            elif has_confirm(items):
                raise self._fail(f"看{label}时出现认不出的弹窗（没有点）")
        raise self._fail(f"{label} {EPISODE_TIMEOUT_S:.0f}s 内没有看完")
