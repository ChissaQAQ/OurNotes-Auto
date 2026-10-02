"""乐曲选择页：分类切换、筛选面板、随机选曲后读取选中的歌（坐标均为 1280x720 设计尺寸）。

- 左上角分类按钮每点一次按 原创 → 翻唱 → 全部 → 原创 循环。
- 筛选面板（漏斗）每次打开都在顶部，从上到下依次是：收藏、难度、乐曲等级、游玩状况、乐曲标签、演出效果。
  - 单选项文字左边 33px 处是圆点，选中时是白色。
  - 滑动面板要按住拖动，停顿后再抬起：这样每次滚动的距离固定（拖 300px 滚约 294px）。
    直接甩的话惯性滚动的距离每次不同，停下后再点会点空。
  - 「重置」不弹确认框，也不改难度。
- 「随机选曲」只在筛选后的列表里抽。实测不会抽到未解锁的歌，但这还没法证明。
  抽到未解锁的歌时，右侧面板有「解锁条件」，「确定」是灰的。
- 右侧面板的大封面和乐队确认页的封面一样，可以用封面匹配认出是哪首歌。未解锁的歌大封面左上角有锁，认不出。
- 列表：选中的歌总在中间（高一些的一行，缩略图 100x100），上下各行的缩略图 66x66、行距 102px。
  - 点别的行就选中它并滚到中间；滚动列表也会换选中的歌（滚到哪首在中间就是哪首）。
  - 「默认」排序并不按 musicId，所以找歌要边滚边认：按住拖动（停顿后抬起，没有惯性）每次滚 4 行，
    一屏能认出 5~6 首，相邻两屏有重叠。
  - 最上、最下两行是渐隐的，缩略图用逐行归一化的封面匹配（见 :class:`~ournotes_auto.nav.jacket.JacketMatcher`）。
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from typing import NamedTuple

import cv2
import numpy as np

from ..result_reader import OcrItem
from .jacket import DESIGN_H, DESIGN_W, JacketMatcher, crop_jacket
from .screens import RELOGIN_SCREENS, Rect, Screen, center, find, in_roi

logger = logging.getLogger(__name__)

BTN_CATEGORY = (90, 144)
CATEGORY_ROI: Rect = (0, 110, 190, 70)
CATEGORIES = ("原创", "翻唱", "全部")
BTN_FILTER = (918, 40)
# 漏斗上的一点：有「游玩状况」「收藏」筛选时变成青色（约 H95 V210），平时约 H114 V140。只选难度不变色
FUNNEL_POINT = (903, 27)
FILTER_ROI: Rect = (740, 60, 540, 580)  # 筛选面板内容区（被面板挡住的右侧面板文字不会混进来）
FILTER_BUTTONS_ROI: Rect = (760, 630, 520, 60)  # 面板底部：重置 / 随机选曲 / 关闭
BTN_FILTER_RESET = (818, 660)
BTN_FILTER_CLOSE = (1142, 660)
# 在左右两列选项之间的空隙里上下拖，不会碰到选项
FILTER_DRAG = ((980, 450), (980, 150))
RADIO_DX = -33  # 单选圆点相对选项文字左边缘的位置
EMPTY_ROI: Rect = (380, 300, 520, 90)  # 列表为空时的「没有符合筛选条件的乐曲」
LOCK_ROI: Rect = (740, 510, 540, 80)  # 选中未解锁的歌时右侧面板的「解锁条件」
SELECT_JACKET_ROI = (737, 92, 300, 300)  # 右侧面板的封面
# 选曲页的大封面有的和缩略图差得多一些（unravel 只有 0.90，第二名 0.51），阈值放宽、要求和第二名拉开更多。
# 认错的代价只是跳过判断用错了曲目：演奏前在乐队确认页还会按曲名 + 等级再认一次
SELECT_MIN_SCORE = 0.85
SELECT_MIN_MARGIN = 0.25
# 几乎一模一样时第二名也可能挺像（青春コンプレックス 0.999，同是蓝色调的 100020 有 0.82），只要求拉开 0.1
SELECT_SURE_SCORE = 0.97
SELECT_SURE_MARGIN = 0.1
BTN_RANDOM = (967, 660)
BTN_DIFFICULTY = {"easy": (807, 555), "normal": (937, 555), "hard": (1068, 555), "expert": (1198, 555)}
# 随机选曲没得选（比如筛选后能打的只剩当前选中的这首）时的提示条，横跨屏幕中间，挡住封面下半部分
NO_RANDOM_TEXT = "没有可以随机选择"
BTN_BAND_BACK = (56, 38)  # 乐队确认页左上角「<」：直接回到乐曲选择页
FILTER_WAIT_S = 3.0  # 点开 / 关闭筛选面板后多久没反应就再点一次

# 列表（缩略图左边缘 x、边长；上面各行缩略图上边缘在 ROW_ABOVE_Y - 102k，下面各行在 ROW_BELOW_Y + 102k）
LIST_THUMB_X = 213
LIST_THUMB = 66
LIST_Y_RANGE = (80, 654)  # 在这个范围里逐像素滑动找缩略图的上边缘（再往上是标题栏）
ROW_PITCH = 102
ROW_ABOVE_Y = 301
ROW_BELOW_Y = 339
ROW_INSET = 4 / 66  # 缩略图四周有描边，裁掉再比
# 实测：真正的行 0.91~0.99，且比第二名高 0.33 以上；素色封面在别处偶尔也有 0.9，但和第二名只差 0.06
ROW_MIN_SCORE = 0.85
ROW_MIN_MARGIN = 0.2
ROW_PEAK_PX = 15  # 同一行的匹配峰只取一个
LIST_TAP_X = 450  # 点行的位置（曲名那一块）
LIST_DRAG_DOWN = ((450, 620), (450, 620 - 4 * ROW_PITCH))  # 往列表后面滚 4 行
LIST_DRAG_UP = ((450, 180), (450, 180 + 4 * ROW_PITCH))
LIST_SETTLE_S = 0.6  # 拖完等列表对齐

DIFFICULTY_OPTIONS = {"easy": "EASY", "normal": "NORMAL", "hard": "HARD", "expert": "EXPERT"}
# 「游玩状况」的选项；OCR 会把 ALL PERFECT、FULL COMBO 中间的空格去掉
STATUS_OPTIONS = {
    "any": "不指定",
    "not_clear": "未完成",
    "not_ss": "未达成SS",
    "not_fc": "未FULLCOMBO",
    "not_ap": "未ALLPERFECT",
    "clear": "已完成",
    "ss": "已达成SS",
    "fc": "FULLCOMBO",
    "ap": "ALLPERFECT",
}


class SongPick(NamedTuple):
    """乐曲选择页右侧当前选中的歌。"""

    music_id: int | None  # 封面认出的曲目，认不出为 None
    locked: bool = False
    empty: bool = False  # 筛选后列表为空，没有选中的歌
    only: bool = False  # 随机选曲提示没得选：选中的还是原来那首，列表里没有别的可抽


def _option_key(text: str) -> str:
    """选项文字比较用：去掉空白和 OCR 混进来的符号，转大写。LL 当作 L，因为 FULL COMBO 常被读成 FUL_COMBO。"""
    return re.sub(r"[\s_\[\]|.,:;'\"`]", "", text).upper().replace("LL", "L")


def filter_option(items: list[OcrItem], text: str) -> OcrItem | None:
    """筛选面板里文字是 ``text`` 的选项，有多个时取最上面的（「演出效果」也有「不指定」）。

    末尾少读一个字母也算（NORMAL 常被读成 NORMA[）。
    """
    want = _option_key(text)
    loose = None
    for it in sorted(items, key=lambda it: it.cy):
        if not in_roi(it, FILTER_ROI):
            continue
        got = _option_key(it.text)
        if got == want:
            return it
        if loose is None and len(want) >= 5 and got == want[:-1]:
            loose = it
    return loose


def song_category(items: list[OcrItem]) -> str | None:
    for it in items:
        if in_roi(it, CATEGORY_ROI):
            for name in CATEGORIES:
                if name in it.text:
                    return name
    return None


def filter_open(items: list[OcrItem]) -> bool:
    return find(items, "重置", FILTER_BUTTONS_ROI) is not None and find(items, "关闭", FILTER_BUTTONS_ROI) is not None


def list_empty(items: list[OcrItem]) -> bool:
    return find(items, "没有符合", EMPTY_ROI) is not None


def song_locked(items: list[OcrItem]) -> bool:
    return find(items, "解锁条件", LOCK_ROI) is not None


def row_offset(y: int) -> int | None:
    """缩略图上边缘 y → 相对中间选中的歌第几行（上负下正）；落在中间那行里的返回 None。"""
    if y < (ROW_ABOVE_Y + ROW_BELOW_Y) / 2:
        k = round((y - ROW_ABOVE_Y) / ROW_PITCH)
        return k if k < 0 else None
    k = round((y - ROW_BELOW_Y) / ROW_PITCH)
    return k if k > 0 else None


def row_matcher(jackets: JacketMatcher) -> JacketMatcher:
    """认列表缩略图用的封面匹配。"""
    return jackets.variant(inset=ROW_INSET, row_norm=True, min_score=ROW_MIN_SCORE, min_margin=ROW_MIN_MARGIN)


def list_rows(frame: np.ndarray, matcher: JacketMatcher) -> list[tuple[int, int]]:
    """列表里认得出的行（不含中间选中的那行）：从上到下的 (缩略图上边缘 y, musicId)，坐标为 1280x720。

    ``matcher`` 用 :func:`row_matcher` 建。在 :data:`LIST_Y_RANGE` 里逐像素滑动，
    取足够像、比第二名明显像、且是附近最高的位置。
    """
    if len(matcher.ids) < 2:
        return []
    if frame.shape[1] != DESIGN_W or frame.shape[0] != DESIGN_H:
        frame = cv2.resize(frame, (DESIGN_W, DESIGN_H), interpolation=cv2.INTER_AREA)
    x, n = LIST_THUMB_X, LIST_THUMB
    ys = range(LIST_Y_RANGE[0], LIST_Y_RANGE[1] + 1)
    scores = matcher.scores([frame[y : y + n, x : x + n] for y in ys])
    top2 = np.partition(scores, -2, axis=1)[:, -2:]
    best, second = top2[:, 1], top2[:, 0]
    ok = (best >= matcher.min_score) & (best - second >= matcher.min_margin)
    out: list[tuple[int, int]] = []
    for i in np.flatnonzero(ok):
        lo, hi = max(0, i - ROW_PEAK_PX), i + ROW_PEAK_PX + 1
        if best[i] < best[lo:hi].max() or (out and ys[i] - out[-1][0] <= ROW_PEAK_PX):
            continue
        out.append((ys[i], matcher.ids[int(scores[i].argmax())]))
    return out


class ListPositions:
    """列表里认出过的歌的相对位置（单位：行，以最早看到的那屏中间为 0）。

    选中的歌总在中间，每屏认出的行都能换算成「中间往上 / 往下第几行」；和之前见过的歌对上号，
    就能把这屏放进同一个坐标里。之后再找见过的歌，往哪个方向滚一查便知。
    分类、筛选一变顺序就不同，要 :meth:`clear`。
    """

    def __init__(self):
        self.pos: dict[int, int] = {}
        self._at: dict[int, int] = {}

    def clear(self) -> None:
        self.pos.clear()
        self._at.clear()

    def at(self, position: int) -> int | None:
        return self._at.get(position)

    def observe(self, center: int | None, rows: list[tuple[int, int]]) -> int | None:
        """记下一屏：``center`` 为中间的歌，``rows`` 为 (相对中间第几行, musicId)。返回这屏中间的位置，
        什么都没认出时为 None。和见过的歌对不上（没有重叠，或各首推出的位置不一致）时从这屏重新记。"""
        seen = ([(0, center)] if center is not None else []) + rows
        if not seen:
            return None
        bases = Counter(self.pos[mid] - off for off, mid in seen if mid in self.pos)
        if len(bases) == 1:
            base = next(iter(bases))
        else:
            if self.pos:
                logger.debug("列表位置对不上（%s），重新记录", dict(bases) or "没有见过的歌")
            self.clear()
            base = 0
        for off, mid in seen:
            p = base + off
            old = self._at.get(p)
            if old is not None and old != mid:
                del self.pos[old]
            if mid in self.pos:
                self._at.pop(self.pos[mid], None)
            self.pos[mid] = p
            self._at[p] = mid
        return base


def _patch(frame: np.ndarray, point: tuple[int, int], r: int = 3) -> np.ndarray:
    h, w = frame.shape[:2]
    x, y = round(point[0] * w / 1280), round(point[1] * h / 720)
    r = max(1, round(r * w / 1280))
    return frame[y - r : y + r + 1, x - r : x + r + 1]


def funnel_active(frame: np.ndarray) -> bool:
    """漏斗是否是青色（有「游玩状况」或「收藏」筛选）。"""
    hsv = cv2.cvtColor(_patch(frame, FUNNEL_POINT), cv2.COLOR_BGR2HSV).reshape(-1, 3).mean(axis=0)
    return hsv[2] > 185 and 85 <= hsv[0] <= 105


class SongSelectMixin:
    """:class:`~ournotes_auto.nav.navigator.GameNavigator` 的乐曲选择页操作。

    用到它的 ``look`` ``tap`` ``_sleep`` ``_lit`` ``_fail`` ``_common_step`` ``touch`` ``jackets`` 等。
    """

    list_positions: ListPositions
    _rows_matcher: JacketMatcher | None
    _filter_status: str | None

    def ensure_song_select(self, timeout_s: float = 60.0) -> list[OcrItem]:
        """回到乐曲选择页（筛选面板是关着的），返回这一帧的识别结果。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            screen, items = self.look()
            if screen is Screen.SONG_SELECT:
                if not filter_open(items):
                    return items
                self._close_filter()
                continue
            if screen in RELOGIN_SCREENS:
                deadline = time.monotonic() + timeout_s
            if screen is Screen.BAND_CONFIRM:
                self.tap(BTN_BAND_BACK, "返回")
            elif not self._common_step(screen, items):
                self._sleep(0.5)
                continue
            self._sleep(self.settle_s)
        raise self._fail("未能回到乐曲选择页")

    def _drag(self, start: tuple[int, int], end: tuple[int, int], move_s: float = 0.4, hold_s: float = 0.3) -> None:
        """按住从 ``start`` 拖到 ``end``，停顿 ``hold_s`` 再抬起（抬起时速度为 0，没有惯性滚动）。"""
        w, h = self.source.size
        sx, sy = w / 1280, h / 720
        steps = 20
        self.touch.down(9, round(start[0] * sx), round(start[1] * sy))
        self.touch.flush()
        for i in range(1, steps + 1):
            self._sleep(move_s / steps)
            x = start[0] + (end[0] - start[0]) * i / steps
            y = start[1] + (end[1] - start[1]) * i / steps
            self.touch.move(9, round(x * sx), round(y * sy))
            self.touch.flush()
        time.sleep(hold_s)  # 不能被停止信号打断：手指要抬起来
        self.touch.up(9)
        self.touch.flush()

    def set_song_category(self, name: str) -> str | None:
        """把分类切到 ``name``，返回切换前的分类（没认出来时为 None）。"""
        items = self.ensure_song_select()
        before = current = song_category(items)
        for _ in range(len(CATEGORIES) + 1):
            if current == name:
                if before != name:
                    logger.debug("分类：%s → %s", before or "?", name)
                return before
            self.tap(BTN_CATEGORY, "分类")
            self.list_positions.clear()
            self._sleep(1.0)
            _, items = self.look()
            current = song_category(items)
        raise self._fail(f"切换不到分类「{name}」")

    def _open_filter(self) -> None:
        tapped_at = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            screen, items = self.look()
            if screen is Screen.SONG_SELECT and filter_open(items):
                self._sleep(0.5)  # 等面板滑入动画结束
                return
            if screen is Screen.SONG_SELECT and (tapped_at is None or time.monotonic() - tapped_at > FILTER_WAIT_S):
                self.tap(BTN_FILTER, "筛选")
                tapped_at = time.monotonic()
            self._sleep(0.5)
        raise self._fail("打不开筛选面板")

    def _close_filter(self) -> None:
        tapped_at = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            screen, items = self.look()
            if screen is Screen.SONG_SELECT and not filter_open(items):
                return
            # 只在看到面板时点「关闭」：面板关掉后同一位置附近是「确定」
            if filter_open(items) and (tapped_at is None or time.monotonic() - tapped_at > FILTER_WAIT_S):
                self.tap(BTN_FILTER_CLOSE, "关闭筛选")
                tapped_at = time.monotonic()
            self._sleep(0.5)
        raise self._fail("关不掉筛选面板")

    def _select_filter_option(self, text: str) -> bool:
        """在打开的筛选面板里选中单选项 ``text``（看不到就往下拖），按圆点核对；原来没选中时返回 True。"""
        for drags in range(4):
            _, items = self.look()
            it = filter_option(items, text)
            if it is not None:
                break
            if drags == 3:
                raise self._fail(f"筛选面板里找不到「{text}」")
            self._drag(*FILTER_DRAG)
            self._sleep(0.5)
        changed = False
        for _ in range(3):
            if self._lit((round(it.x) + RADIO_DX, round(it.cy))):
                return changed
            self.tap(center(it), text)
            changed = True
            self._sleep(0.8)
            _, items = self.look()
            it = filter_option(items, text) or it
        if self._lit((round(it.x) + RADIO_DX, round(it.cy))):
            return changed
        raise self._fail(f"筛选「{text}」选不中")

    def _known_status(self) -> str | None:
        """本次运行设好的游玩状况筛选；漏斗颜色对不上（游戏里被改过）时作废。要在乐曲选择页的一帧上调用。"""
        if self._filter_status is not None and funnel_active(self._frame) != (self._filter_status != "any"):
            self._filter_status = None
        return self._filter_status

    def set_song_filter(self, difficulty: str | None = None, status: str | None = None, reset: bool = False) -> None:
        """打开筛选面板，（``reset`` 时先重置）选好难度和游玩状况，再关上。

        游玩状况已经是本次运行上次设好的（且漏斗颜色对得上）时不再去选：选项在面板下方，要拖动才看得到。
        """
        self.ensure_song_select()
        if status is not None and not reset and self._known_status() == status:
            logger.debug("游玩状况已是「%s」", STATUS_OPTIONS[status])
            status = None
            if difficulty is None:
                return
        self._open_filter()
        self.list_positions.clear()
        if reset:
            self._filter_status = None  # 重置后是「不指定」，但不确定点上了没有
            self.tap(BTN_FILTER_RESET, "重置")
            self._sleep(1.0)
        if difficulty is not None:
            self._select_filter_option(DIFFICULTY_OPTIONS[difficulty])
        if status is not None:
            self._filter_status = None
            self._select_filter_option(STATUS_OPTIONS[status])
            self._filter_status = status
        self._close_filter()
        done = ["重置"] if reset else []
        if difficulty:
            done.append(f"难度 {DIFFICULTY_OPTIONS[difficulty]}")
        if status:
            done.append(f"游玩状况「{STATUS_OPTIONS[status]}」")
        logger.debug("筛选：%s", "，".join(done))

    def clear_status_filter(self) -> None:
        """「游玩状况」筛选不是「不指定」时改回来（随机选曲只在筛选后的列表里抽）。漏斗不是青色就不用管。"""
        self.ensure_song_select()
        if not funnel_active(self._frame):
            self._filter_status = "any"
            return
        self._open_filter()
        self._filter_status = None
        if self._select_filter_option(STATUS_OPTIONS["any"]):
            self.list_positions.clear()
            logger.info("游玩状况筛选已改回「不指定」")
        self._filter_status = "any"
        self._close_filter()

    def read_song_pick(self) -> SongPick:
        """当前选中的歌；随机选曲后列表还在滚动，封面连续两次认成同一首才采用。

        「没有可以随机选择的乐曲。」的提示条挡着封面时认不准，等它消失再认；但选中的是未解锁的歌时
        （「解锁条件」在提示条下面，读得到）不用等：抽不到别的歌，这首又打不了。
        """
        prev = pick = None
        only = False
        for _ in range(12):
            screen, items = self.look()
            if screen is not Screen.SONG_SELECT:
                raise self._fail("不在乐曲选择页")
            if list_empty(items):
                return SongPick(None, empty=True)
            if find(items, NO_RANDOM_TEXT):
                if song_locked(items):
                    return SongPick(None, locked=True, only=True)
                only, prev = True, None
            else:
                pick = SongPick(self._center_song(), song_locked(items), only=only)
                if pick == prev:
                    return pick
                prev = pick
            self._sleep(0.4)
        return pick or SongPick(None, only=only)

    def random_song(self) -> SongPick:
        """点「随机选曲」并返回抽到的歌；筛选后列表为空时不点。"""
        items = self.ensure_song_select()
        if list_empty(items):
            return SongPick(None, empty=True)
        self.tap(self._button(items, "随机选曲", BTN_RANDOM), "随机选曲")
        self._sleep(self.settle_s)
        return self.read_song_pick()

    def _center_song(self) -> int | None:
        """最近一帧右侧大封面认出的歌。"""
        if self.jackets is None:
            return None
        crop = crop_jacket(self._frame, SELECT_JACKET_ROI)
        hit = self.jackets.identify(crop, SELECT_MIN_SCORE, SELECT_MIN_MARGIN) or self.jackets.identify(
            crop, SELECT_SURE_SCORE, SELECT_SURE_MARGIN
        )
        return hit[0] if hit else None

    def select_song(self, music_id: int, max_drags: int = 80) -> SongPick | None:
        """在当前分类、筛选下的列表里选中 ``music_id``：看得到就点那一行，看不到就拖动列表找
        （见过它就往它那边滚，否则先往下，到底再往上）。两头都找过还没有返回 None。"""
        if self.jackets is None:
            raise ValueError("没有封面数据，无法按曲目选歌")
        if self._rows_matcher is None:
            self._rows_matcher = row_matcher(self.jackets)
        positions = self.list_positions
        direction, ends, drags, taps = 1, 0, 0, 0
        last = None  # 上次拖动前看到的（中间的歌, 各行的歌）：拖了没变就是到头了
        while True:
            items = self.ensure_song_select()
            if list_empty(items):
                return SongPick(None, empty=True)
            rows = list_rows(self._frame, self._rows_matcher)
            center = self._center_song()
            base = positions.observe(center, [(off, mid) for y, mid in rows if (off := row_offset(y)) is not None])
            if center is None and base is not None:
                center = positions.at(base)  # 未解锁的歌大封面上有锁认不出，按位置推
            if center == music_id:
                pick = SongPick(music_id, song_locked(items))
                logger.debug("选中 %d%s", music_id, "（未解锁）" if pick.locked else "")
                return pick
            y = next((y for y, mid in rows if mid == music_id), None)
            if y is not None:
                taps += 1
                if taps > 3:
                    raise self._fail(f"点不中列表里的 {music_id}")
                self.tap((LIST_TAP_X, y + LIST_THUMB // 2), str(music_id))
                self._sleep(self.settle_s)
                last = None
                continue
            view = (center, tuple(mid for _, mid in rows))
            if view == last:
                ends += 1
                if ends >= 2:
                    logger.debug("列表里没有 %d", music_id)
                    return None
                direction = -direction
            elif ends == 0 and base is not None and music_id in positions.pos:
                direction = 1 if positions.pos[music_id] > base else -1
            if drags >= max_drags:
                raise self._fail(f"拖了 {drags} 次还没找到 {music_id}")
            last = view
            self._drag(*(LIST_DRAG_DOWN if direction > 0 else LIST_DRAG_UP))
            drags += 1
            self._sleep(LIST_SETTLE_S)
